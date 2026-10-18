import plistlib
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sleeve.core.names import guess_from_filename


@dataclass(frozen=True)
class Entry:
    title: str
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    duration: float = 0.0
    track: int | None = None
    year: str = ""
    genre: str = ""
    path: Path | None = None


@dataclass(frozen=True)
class Playlist:
    name: str
    entries: tuple[Entry, ...]


class LibraryError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


_DRIVE = re.compile(r"^/[A-Za-z]:")
_EXTINF = re.compile(r"#EXTINF:\s*(-?\d+(?:\.\d+)?)[^,]*,(.*)")
_SKIP_KINDS = ("podcast", "audiobook", "video", "movie", "pdf", "book")


def path_from_url(url: str) -> Path | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "file":
        return None
    path = urllib.parse.unquote(parsed.path)
    if _DRIVE.match(path):
        path = path[1:]
    if parsed.netloc and parsed.netloc != "localhost":
        path = f"//{parsed.netloc}{path}"
    return Path(path)


def _is_song(track: dict[str, Any]) -> bool:
    if any(track.get(k) for k in ("Podcast", "Movie", "TV Show", "Has Video", "Music Video")):
        return False
    kind = str(track.get("Kind", "")).casefold()
    return not any(word in kind for word in _SKIP_KINDS)


def _entry(track: dict[str, Any]) -> Entry:
    location = track.get("Location")
    number = track.get("Track Number")
    year = track.get("Year")
    return Entry(
        title=str(track.get("Name", "")).strip(),
        artist=str(track.get("Artist", "")).strip(),
        album=str(track.get("Album", "")).strip(),
        album_artist=str(track.get("Album Artist", "")).strip(),
        duration=float(track.get("Total Time", 0) or 0) / 1000,
        track=int(number) if isinstance(number, int) and number > 0 else None,
        year=str(year) if year else "",
        genre=str(track.get("Genre", "")).strip(),
        path=path_from_url(location) if isinstance(location, str) else None,
    )


def read_itunes(path: Path) -> list[Playlist]:
    """Read an iTunes or Music app library export: the whole music library, then each playlist."""
    try:
        with path.open("rb") as fh:
            data = plistlib.load(fh)
    except (OSError, plistlib.InvalidFileException, ValueError) as exc:
        raise LibraryError(
            f"Couldn't read '{path.name}'. Export it again from Music with File › Library "
            "› Export Library."
        ) from exc
    tracks = data.get("Tracks") if isinstance(data, dict) else None
    if not isinstance(tracks, dict):
        raise LibraryError(f"'{path.name}' isn't an iTunes or Music library export.")
    songs = {str(k): _entry(t) for k, t in tracks.items() if isinstance(t, dict) and _is_song(t)}
    out = [Playlist("All music", tuple(sorted(
        songs.values(), key=lambda e: (e.artist.casefold(), e.album.casefold(), e.track or 0)
    )))]  # fmt: skip
    for raw in data.get("Playlists") or []:
        if not isinstance(raw, dict):
            continue
        if raw.get("Master") or raw.get("Folder") or raw.get("Distinguished Kind"):
            continue
        if raw.get("Visible") is False:
            continue
        items = [songs.get(str(i.get("Track ID"))) for i in raw.get("Playlist Items") or []]
        entries = tuple(e for e in items if e is not None)
        if entries:
            out.append(Playlist(str(raw.get("Name", "Playlist")), entries))
    return out


def read_m3u(path: Path) -> Playlist:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise LibraryError(f"Couldn't open '{path.name}'.") from exc
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    entries = []
    pending: tuple[float, str] | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            info = _EXTINF.match(line)
            if info:
                pending = (float(info.group(1)), info.group(2).strip())
            continue
        if re.match(r"^[a-z][a-z0-9+.-]*://", line, re.I):
            target = path_from_url(line)
            if target is None:
                pending = None
                continue
        else:
            target = Path(line if Path(line).exists() else line.replace("\\", "/"))
            if not target.is_absolute():
                target = path.parent / target
        artist, title = guess_from_filename(target.name)
        duration = 0.0
        if pending:
            duration = max(pending[0], 0.0)
            label = pending[1]
            if " - " in label:
                artist, title = (part.strip() for part in label.split(" - ", 1))
            elif label:
                title = label
        entries.append(Entry(title=title, artist=artist or "", duration=duration, path=target))
        pending = None
    if not entries:
        raise LibraryError(f"'{path.name}' doesn't list any songs.")
    return Playlist(path.stem, tuple(entries))


def read_playlists(path: Path) -> list[Playlist]:
    suffix = path.suffix.lower()
    if suffix in (".m3u", ".m3u8"):
        return [read_m3u(path)]
    if suffix == ".xml":
        return read_itunes(path)
    raise LibraryError(f"'{path.name}' isn't a playlist Sleeve can import (M3U or Library.xml).")


PLAYLIST_EXTENSIONS = {".m3u", ".m3u8", ".xml"}
