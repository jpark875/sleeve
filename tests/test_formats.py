import shutil
from pathlib import Path

import pytest

from sleeve.core.formats import Action, Kind, detect, is_candidate

DATA = Path(__file__).parent / "data"


@pytest.mark.parametrize(
    ("name", "kind", "action"),
    [
        ("tone.mp3", Kind.MP3, Action.COPY),
        ("tone.m4a", Kind.AAC, Action.COPY),
        ("alac.m4a", Kind.ALAC, Action.CONVERT),
        ("tone.wav", Kind.WAV, Action.CONVERT),
        ("tone.aiff", Kind.AIFF, Action.CONVERT),
        ("tone.flac", Kind.FLAC, Action.CONVERT),
        ("tone.ogg", Kind.VORBIS, Action.CONVERT),
        ("tone.opus", Kind.OPUS, Action.CONVERT),
        ("tone.wma", Kind.WMA, Action.CONVERT),
    ],
)
def test_detect_by_content(name: str, kind: Kind, action: Action) -> None:
    detected = detect(DATA / name)
    assert detected.kind is kind
    assert detected.action is action
    assert not detected.protected
    assert detected.duration == pytest.approx(1.0, abs=0.1)


def test_extension_is_ignored(tmp_path: Path) -> None:
    misnamed = tmp_path / "actually-a-wav.mp3"
    shutil.copy(DATA / "tone.wav", misnamed)
    assert detect(misnamed).kind is Kind.WAV


def test_protected_m4p_is_skipped() -> None:
    detected = detect(DATA / "drm.m4p")
    assert detected.protected
    assert detected.action is Action.SKIP


def test_protected_wma_is_skipped(tmp_path: Path) -> None:
    data = bytearray((DATA / "tone.wma").read_bytes())
    data[200:200] = bytes.fromhex("fbb3112223bdd211b4b700a0c955fc6e")
    path = tmp_path / "locked.wma"
    path.write_bytes(bytes(data))
    assert detect(path).protected


def test_unknown_content(tmp_path: Path) -> None:
    path = tmp_path / "notes.mp3"
    path.write_text("not audio")
    detected = detect(path)
    assert detected.kind is Kind.UNKNOWN
    assert detected.action is Action.SKIP


def test_output_extension() -> None:
    assert detect(DATA / "tone.m4a").extension == ".m4a"
    assert detect(DATA / "tone.wav").extension == ".mp3"


def test_is_candidate() -> None:
    assert is_candidate(Path("a/Song.FLAC"))
    assert not is_candidate(Path("a/._Song.mp3"))
    assert not is_candidate(Path("a/cover.jpg"))
