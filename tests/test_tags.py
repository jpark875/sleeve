import base64
import shutil
from pathlib import Path

import pytest
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE

from sleeve.core.tags import Artwork, Tags, read_tags, write_tags

from .conftest import png_bytes


@pytest.fixture
def art() -> Artwork:
    artwork = Artwork.from_bytes(png_bytes())
    assert artwork is not None
    return artwork


@pytest.mark.parametrize("name", ["tone.mp3", "tone.m4a"])
def test_round_trip(tmp_path: Path, data_dir: Path, art: Artwork, name: str) -> None:
    path = tmp_path / name
    shutil.copy(data_dir / name, path)
    tags = Tags(
        title="夜に駆ける",
        artist="Ýoasobi & Friends",
        album="The Book",
        album_artist="YOASOBI",
        track=7,
        year="2020",
        genre="J-Pop",
        artwork=art,
    )
    write_tags(path, tags)
    assert read_tags(path) == tags


@pytest.mark.parametrize("name", ["tone.mp3", "tone.m4a"])
def test_clearing_fields_removes_them(
    tmp_path: Path, data_dir: Path, art: Artwork, name: str
) -> None:
    path = tmp_path / name
    shutil.copy(data_dir / name, path)
    write_tags(path, Tags(title="A", artist="B", track=2, artwork=art))
    write_tags(path, Tags(title="A"))
    assert read_tags(path) == Tags(title="A")


def test_mp3_uses_id3v23(tmp_path: Path, data_dir: Path) -> None:
    path = tmp_path / "tone.mp3"
    shutil.copy(data_dir / "tone.mp3", path)
    write_tags(path, Tags(title="x"))
    assert ID3(path).version[:2] == (2, 3)


def test_untagged_file_reads_empty(data_dir: Path) -> None:
    tags = read_tags(data_dir / "tone.wav")
    assert tags.title == "" and tags.artwork is None


def test_garbage_reads_empty(tmp_path: Path) -> None:
    path = tmp_path / "x.mp3"
    path.write_bytes(b"\x00" * 10)
    assert read_tags(path) == Tags()


def test_reads_flac_with_picture(tmp_path: Path, data_dir: Path) -> None:
    path = tmp_path / "tone.flac"
    shutil.copy(data_dir / "tone.flac", path)
    audio = FLAC(path)
    audio["title"] = "Flac Song"
    audio["artist"] = "Someone"
    audio["tracknumber"] = "3/12"
    audio["date"] = "2019-05-01"
    picture = Picture()
    picture.type = 3
    picture.mime = "image/png"
    picture.data = png_bytes()
    audio.add_picture(picture)
    audio.save()
    tags = read_tags(path)
    assert (tags.title, tags.artist, tags.track, tags.year) == ("Flac Song", "Someone", 3, "2019")
    assert tags.artwork is not None and tags.artwork.mime == "image/png"


def test_reads_ogg_picture_block(tmp_path: Path, data_dir: Path) -> None:
    path = tmp_path / "tone.ogg"
    shutil.copy(data_dir / "tone.ogg", path)
    audio = OggVorbis(path)
    picture = Picture()
    picture.type = 3
    picture.mime = "image/png"
    picture.data = png_bytes()
    audio["metadata_block_picture"] = [base64.b64encode(picture.write()).decode()]
    audio["title"] = "Ogg Song"
    audio.save()
    tags = read_tags(path)
    assert tags.title == "Ogg Song"
    assert tags.artwork is not None


def test_reads_wav_id3_chunk(tmp_path: Path, data_dir: Path) -> None:
    path = tmp_path / "tone.wav"
    shutil.copy(data_dir / "tone.wav", path)
    audio = WAVE(path)
    audio.add_tags()
    from mutagen.id3 import TIT2, TPE1

    audio.tags.add(TIT2(encoding=3, text=["Wave Song"]))
    audio.tags.add(TPE1(encoding=3, text=["Artist"]))
    audio.save()
    tags = read_tags(path)
    assert (tags.title, tags.artist) == ("Wave Song", "Artist")


def test_fingerprint_tracks_changes(art: Artwork) -> None:
    base = Tags(title="a")
    assert base.fingerprint() == Tags(title="a").fingerprint()
    assert base.fingerprint() != base.copy(artist="b").fingerprint()
    assert base.fingerprint() != base.copy(artwork=art).fingerprint()


def test_artwork_rejects_unknown_bytes() -> None:
    assert Artwork.from_bytes(b"GIF89a") is None


def test_write_rejects_other_formats(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write_tags(tmp_path / "x.wav", Tags())
