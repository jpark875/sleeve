import base64
import hashlib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import mutagen
from mutagen.asf import ASF
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4Cover

FIELDS = ("title", "artist", "album", "album_artist", "track", "year", "genre")


@dataclass(frozen=True)
class Artwork:
    data: bytes
    mime: str

    @classmethod
    def from_bytes(cls, data: bytes) -> "Artwork | None":
        if data[:3] == b"\xff\xd8\xff":
            return cls(data, "image/jpeg")
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return cls(data, "image/png")
        return None

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


@dataclass
class Tags:
    title: str = ""
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    track: int | None = None
    year: str = ""
    genre: str = ""
    artwork: Artwork | None = field(default=None, repr=False)

    def copy(self, **changes: Any) -> "Tags":
        return replace(self, **changes)

    def fingerprint(self) -> str:
        h = hashlib.sha256()
        for name in FIELDS:
            h.update(f"{name}={getattr(self, name) or ''}\x00".encode())
        h.update((self.artwork.digest if self.artwork else "").encode())
        return h.hexdigest()


def _first(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list | tuple):
        value = value[0] if value else ""
    if hasattr(value, "text"):
        return _first(value.text)
    return str(value).strip()


def _track_number(value: Any) -> int | None:
    if isinstance(value, list) and value and isinstance(value[0], tuple):
        number = value[0][0]
        return int(number) if number else None
    text = _first(value).split("/")[0].strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


def _picture_artwork(pictures: list[Picture]) -> Artwork | None:
    if not pictures:
        return None
    front = next((p for p in pictures if p.type == 3), pictures[0])
    return Artwork.from_bytes(bytes(front.data))


def _from_id3(tags: ID3) -> Tags:
    art = None
    frames = tags.getall("APIC")
    if frames:
        front = next((f for f in frames if f.type == 3), frames[0])
        art = Artwork.from_bytes(bytes(front.data))
    return Tags(
        title=_first(tags.get("TIT2")),
        artist=_first(tags.get("TPE1")),
        album=_first(tags.get("TALB")),
        album_artist=_first(tags.get("TPE2")),
        track=_track_number(tags.get("TRCK")),
        year=_first(tags.get("TDRC"))[:4],
        genre=_first(tags.get("TCON")),
        artwork=art,
    )


def _from_mp4(audio: MP4) -> Tags:
    tags: Any = audio.tags or {}
    covers = tags.get("covr") or []
    return Tags(
        title=_first(tags.get("\xa9nam")),
        artist=_first(tags.get("\xa9ART")),
        album=_first(tags.get("\xa9alb")),
        album_artist=_first(tags.get("aART")),
        track=_track_number(tags.get("trkn")),
        year=_first(tags.get("\xa9day"))[:4],
        genre=_first(tags.get("\xa9gen")),
        artwork=Artwork.from_bytes(bytes(covers[0])) if covers else None,
    )


def _from_vorbis(audio: Any) -> Tags:
    tags: Any = audio.tags or {}
    art = None
    if isinstance(audio, FLAC):
        art = _picture_artwork(audio.pictures)
    else:
        blocks = tags.get("metadata_block_picture") or []
        pictures = []
        for block in blocks:
            try:
                pictures.append(Picture(base64.b64decode(block)))
            except Exception:
                continue
        art = _picture_artwork(pictures)
    return Tags(
        title=_first(tags.get("title")),
        artist=_first(tags.get("artist")),
        album=_first(tags.get("album")),
        album_artist=_first(tags.get("albumartist")),
        track=_track_number(tags.get("tracknumber")),
        year=_first(tags.get("date"))[:4],
        genre=_first(tags.get("genre")),
        artwork=art,
    )


def _from_asf(audio: ASF) -> Tags:
    tags: Any = audio.tags or {}
    return Tags(
        title=_first(tags.get("Title")),
        artist=_first(tags.get("Author")),
        album=_first(tags.get("WM/AlbumTitle")),
        album_artist=_first(tags.get("WM/AlbumArtist")),
        track=_track_number(tags.get("WM/TrackNumber")),
        year=_first(tags.get("WM/Year"))[:4],
        genre=_first(tags.get("WM/Genre")),
    )


def read_tags(path: Path) -> Tags:
    """Read whatever tags a file has; missing or unreadable tags give empty fields."""
    try:
        audio = mutagen.File(path)
    except Exception:
        return Tags()
    if audio is None:
        return Tags()
    if isinstance(audio, MP4):
        return _from_mp4(audio)
    if isinstance(audio, ASF):
        return _from_asf(audio)
    if isinstance(audio.tags, ID3):
        return _from_id3(audio.tags)
    if audio.tags is not None and hasattr(audio.tags, "keys"):
        return _from_vorbis(audio)
    return Tags()


def _write_id3(path: Path, tags: Tags) -> None:
    try:
        id3 = ID3(path)
    except ID3NoHeaderError:
        id3 = ID3()
    for frame_id, cls, value in (
        ("TIT2", TIT2, tags.title),
        ("TPE1", TPE1, tags.artist),
        ("TALB", TALB, tags.album),
        ("TPE2", TPE2, tags.album_artist),
        ("TRCK", TRCK, str(tags.track) if tags.track else ""),
        ("TDRC", TDRC, tags.year),
        ("TCON", TCON, tags.genre),
    ):
        id3.delall(frame_id)
        if value:
            id3.add(cls(encoding=1, text=[value]))
    id3.delall("APIC")
    if tags.artwork:
        id3.add(APIC(encoding=0, mime=tags.artwork.mime, type=3, desc="", data=tags.artwork.data))
    id3.save(path, v2_version=3)


def _write_mp4(path: Path, tags: Tags) -> None:
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    assert audio.tags is not None
    text = {
        "\xa9nam": tags.title,
        "\xa9ART": tags.artist,
        "\xa9alb": tags.album,
        "aART": tags.album_artist,
        "\xa9day": tags.year,
        "\xa9gen": tags.genre,
    }
    for key, value in text.items():
        if value:
            audio.tags[key] = [value]
        else:
            audio.tags.pop(key, None)
    if tags.track:
        audio.tags["trkn"] = [(tags.track, 0)]
    else:
        audio.tags.pop("trkn", None)
    if tags.artwork:
        kind = MP4Cover.FORMAT_PNG if tags.artwork.mime == "image/png" else MP4Cover.FORMAT_JPEG
        audio.tags["covr"] = [MP4Cover(tags.artwork.data, imageformat=kind)]
    else:
        audio.tags.pop("covr", None)
    audio.save()


def write_tags(path: Path, tags: Tags, suffix: str | None = None) -> None:
    """Write tags to an MP3 or M4A file, replacing the fields Sleeve manages."""
    suffix = (suffix or path.suffix).lower()
    if suffix == ".mp3":
        _write_id3(path, tags)
    elif suffix in (".m4a", ".mp4"):
        _write_mp4(path, tags)
    else:
        raise ValueError(f"Can't write tags to {suffix} files")
