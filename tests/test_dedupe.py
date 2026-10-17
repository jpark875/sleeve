import shutil
from pathlib import Path

from sleeve.core.dedupe import (
    MANIFEST_NAME,
    Entry,
    Manifest,
    SizeIndex,
    file_digest,
)


def test_file_digest_is_content_based(tmp_path: Path, data_dir: Path) -> None:
    a = tmp_path / "a.mp3"
    b = tmp_path / "renamed.bin"
    shutil.copy(data_dir / "tone.mp3", a)
    shutil.copy(data_dir / "tone.mp3", b)
    assert file_digest(a) == file_digest(b)
    assert file_digest(a) != file_digest(data_dir / "tone.m4a")


def test_manifest_round_trip_and_duplicate(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "Song.mp3").write_bytes(b"prepared")
    manifest = Manifest(out)
    manifest.record("src1", Entry("Song.mp3", file_digest(out / "Song.mp3"), "tags1"))
    manifest.flush()

    reloaded = Manifest(out)
    assert reloaded.is_duplicate("src1", "tags1")
    assert not reloaded.is_duplicate("src1", "tags2")
    assert not reloaded.is_duplicate("other", "tags1")
    assert (out / MANIFEST_NAME).is_file()


def test_edited_or_missing_output_is_not_duplicate(tmp_path: Path) -> None:
    (tmp_path / "Song.mp3").write_bytes(b"prepared")
    manifest = Manifest(tmp_path)
    manifest.record("src", Entry("Song.mp3", file_digest(tmp_path / "Song.mp3"), "t"))
    (tmp_path / "Song.mp3").write_bytes(b"changed by user")
    assert not manifest.is_duplicate("src", "t")
    (tmp_path / "Song.mp3").unlink()
    assert manifest.lookup("src") is None


def test_corrupt_manifest_is_ignored(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_NAME).write_text("{not json")
    assert Manifest(tmp_path).lookup("x") is None


def test_size_index_matches_find_identical(tmp_path: Path, data_dir: Path) -> None:
    shutil.copy(data_dir / "tone.mp3", tmp_path / "Copy.mp3")
    index = SizeIndex(tmp_path)
    source = data_dir / "tone.mp3"
    assert index.find_identical(source, file_digest(source)) == tmp_path / "Copy.mp3"
    other = data_dir / "tone.m4a"
    assert index.find_identical(other, file_digest(other)) is None
    shutil.copy(other, tmp_path / "Later.m4a")
    index.add(tmp_path / "Later.m4a")
    assert index.find_identical(other, file_digest(other)) == tmp_path / "Later.m4a"
