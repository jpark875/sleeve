import errno
import os
import shutil
import sys
import threading
from pathlib import Path

import pytest

from sleeve.core.dedupe import file_digest
from sleeve.core.errors import SleeveError
from sleeve.core.ffmpeg import Encoding
from sleeve.core.formats import detect
from sleeve.core.pipeline import Job, Options, Outcome, Summary, prepare
from sleeve.core.tags import Tags, read_tags


def job(key: int, path: Path, **tags: object) -> Job:
    return Job(key, path, Tags(**tags), detect(path))  # type: ignore[arg-type]


@pytest.fixture
def sources(tmp_path: Path, data_dir: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    for name in ("tone.wav", "tone.mp3", "tone.m4a", "tone.flac", "drm.m4p"):
        shutil.copy(data_dir / name, src / name)
    return src


def snapshot(folder: Path) -> dict[str, str]:
    return {p.name: file_digest(p) for p in folder.iterdir()}


def test_converts_copies_and_tags(tmp_path: Path, sources: Path, ffmpeg_path: Path) -> None:
    out = tmp_path / "out"
    before = snapshot(sources)
    jobs = [
        job(1, sources / "tone.wav", title="Wave", artist="Band"),
        job(2, sources / "tone.mp3", title="Mp3"),
        job(3, sources / "tone.m4a", title="Aac", artist="Band"),
        job(4, sources / "tone.flac", title="Ünïcode / Song", artist="Band"),
        job(5, sources / "drm.m4p", title="Locked"),
    ]
    seen: list[float] = []
    summary = prepare(
        jobs, Options(out), ffmpeg=ffmpeg_path, on_progress=lambda k, f: seen.append(f)
    )
    outcomes = [r.outcome for r in summary.results]
    assert outcomes == [Outcome.READY] * 4 + [Outcome.SKIPPED]
    assert "copy-protected" in summary.results[4].message
    assert (out / "Band - Wave.mp3").is_file()
    assert (out / "Mp3.mp3").is_file()
    assert (out / "Band - Aac.m4a").is_file()
    assert (out / "Band - Ünïcode - Song.mp3").is_file()
    assert detect(out / "Band - Wave.mp3").kind.value == "MP3"
    assert read_tags(out / "Band - Wave.mp3").title == "Wave"
    assert read_tags(out / "Band - Aac.m4a").artist == "Band"
    assert snapshot(sources) == before
    assert max(seen) == 1.0
    assert summary.text() == "4 tracks ready. 1 skipped — see details."


def test_skips_duplicates_on_second_run(tmp_path: Path, sources: Path, ffmpeg_path: Path) -> None:
    out = tmp_path / "out"
    jobs = [job(1, sources / "tone.wav", title="One"), job(2, sources / "tone.mp3", title="Two")]
    prepare(jobs, Options(out), ffmpeg=ffmpeg_path)
    again = prepare(jobs, Options(out), ffmpeg=ffmpeg_path)
    assert [r.outcome for r in again.results] == [Outcome.DUPLICATE, Outcome.DUPLICATE]
    assert "already prepared" in again.results[0].message


def test_duplicate_within_batch(tmp_path: Path, sources: Path, ffmpeg_path: Path) -> None:
    copy = sources / "copy of tone.mp3"
    shutil.copy(sources / "tone.mp3", copy)
    summary = prepare(
        [job(1, sources / "tone.mp3", title="X"), job(2, copy, title="Different title")],
        Options(tmp_path / "out", workers=1),
        ffmpeg=ffmpeg_path,
    )
    assert [r.outcome for r in summary.results] == [Outcome.READY, Outcome.DUPLICATE]


def test_changed_tags_update_in_place(tmp_path: Path, sources: Path, ffmpeg_path: Path) -> None:
    out = tmp_path / "out"
    prepare([job(1, sources / "tone.mp3", title="Old")], Options(out), ffmpeg=ffmpeg_path)
    summary = prepare([job(1, sources / "tone.mp3", title="New")], Options(out), ffmpeg=ffmpeg_path)
    assert summary.results[0].outcome is Outcome.UPDATED
    files = [p.name for p in out.iterdir() if not p.name.startswith(".")]
    assert files == ["New.mp3"]


def test_m4a_output_option(tmp_path: Path, sources: Path, ffmpeg_path: Path) -> None:
    out = tmp_path / "out"
    summary = prepare(
        [job(1, sources / "tone.flac", title="F")],
        Options(out, Encoding.aac(256)),
        ffmpeg=ffmpeg_path,
    )
    path = summary.results[0].path
    assert path is not None and path.suffix == ".m4a"
    assert detect(path).kind.value == "AAC"


def test_missing_ffmpeg_still_copies(tmp_path: Path, sources: Path) -> None:
    summary = prepare(
        [job(1, sources / "tone.wav", title="W"), job(2, sources / "tone.mp3", title="M")],
        Options(tmp_path / "out"),
        ffmpeg=None,
    )
    assert summary.results[0].outcome is Outcome.FAILED
    assert "ffmpeg" in summary.results[0].message
    assert summary.results[1].outcome is Outcome.READY


def test_damaged_file_message(tmp_path: Path, sources: Path, ffmpeg_path: Path) -> None:
    broken = sources / "track3.wav"
    broken.write_bytes(b"RIFF\x00\x10\x00\x00WAVE" + b"junk" * 64)
    result = prepare(
        [job(1, broken, title="x")], Options(tmp_path / "out"), ffmpeg=ffmpeg_path
    ).results[0]
    assert result.outcome is Outcome.FAILED
    assert result.message == (
        "Couldn't read 'track3.wav'. The file may be damaged. "
        "Try exporting it again from your audio app."
    )


def test_moved_source(tmp_path: Path, sources: Path, ffmpeg_path: Path) -> None:
    j = job(1, sources / "tone.mp3", title="x")
    (sources / "tone.mp3").unlink()
    result = prepare([j], Options(tmp_path / "out"), ffmpeg=ffmpeg_path).results[0]
    assert result.outcome is Outcome.FAILED
    assert "moved or deleted" in result.message


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX non-root")
def test_read_only_folder(tmp_path: Path, sources: Path, ffmpeg_path: Path) -> None:
    out = tmp_path / "locked"
    out.mkdir()
    out.chmod(0o500)
    try:
        with pytest.raises(SleeveError) as info:
            prepare([job(1, sources / "tone.mp3")], Options(out), ffmpeg=ffmpeg_path)
        assert "read-only" in info.value.message
    finally:
        out.chmod(0o700)


def test_read_only_folder_any_platform(
    tmp_path: Path, sources: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(self: Path, data: bytes) -> int:
        raise OSError(errno.EROFS, "Read-only file system")

    monkeypatch.setattr(Path, "write_bytes", refuse)
    with pytest.raises(SleeveError) as info:
        prepare([job(1, sources / "tone.mp3")], Options(tmp_path / "out"), ffmpeg=None)
    assert "read-only" in info.value.message
    assert info.value.fatal


def test_disk_full_stops_batch(
    tmp_path: Path, sources: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full(src: Path, dst: Path) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("sleeve.core.pipeline.shutil.copyfile", full)
    jobs = [job(1, sources / "tone.mp3", title="a"), job(2, sources / "tone.m4a", title="b")]
    summary = prepare(jobs, Options(tmp_path / "out", workers=1), ffmpeg=None)
    assert summary.results[0].outcome is Outcome.FAILED
    assert summary.results[1].outcome is Outcome.CANCELLED
    assert summary.fatal is not None and "is full" in summary.fatal
    assert summary.text() == summary.fatal


def test_cancel_before_start(tmp_path: Path, sources: Path, ffmpeg_path: Path) -> None:
    cancel = threading.Event()
    cancel.set()
    summary = prepare(
        [job(1, sources / "tone.wav")], Options(tmp_path / "out"), ffmpeg=ffmpeg_path, cancel=cancel
    )
    assert summary.results[0].outcome is Outcome.CANCELLED
    assert not [p for p in (tmp_path / "out").iterdir() if p.suffix == ".part"]


def test_summary_text() -> None:
    from sleeve.core.pipeline import Result

    s = Summary([Result(1, Outcome.READY)])
    assert s.text() == "1 track ready."
    s = Summary([Result(1, Outcome.READY), Result(2, Outcome.FAILED)])
    assert s.text() == "1 track ready. 1 failed — see details."
    assert Summary([], fatal="Disk full.").text() == "Disk full."


@pytest.mark.parametrize("name", ["clip.mp4", "clip.webm"])
def test_extracts_audio_from_video(
    tmp_path: Path, data_dir: Path, ffmpeg_path: Path, name: str
) -> None:
    summary = prepare(
        [job(1, data_dir / name, title="Upload")], Options(tmp_path / "out"), ffmpeg=ffmpeg_path
    )
    result = summary.results[0]
    assert result.outcome is Outcome.READY
    assert result.path is not None and result.path.suffix == ".mp3"
    out = detect(result.path)
    assert out.kind.value == "MP3"
    assert out.duration == pytest.approx(1.0, abs=0.15)
