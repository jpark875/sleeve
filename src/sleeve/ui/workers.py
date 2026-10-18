import logging
import os
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal

from sleeve.core.errors import SleeveError
from sleeve.core.formats import Detected, Kind, detect, is_candidate
from sleeve.core.library import Entry
from sleeve.core.matching import Match, MatchState, Wanted
from sleeve.core.names import guess_from_filename
from sleeve.core.pipeline import Job, Options, Summary, prepare
from sleeve.core.tags import Tags, read_tags
from sleeve.spotify.api import SpotifyError
from sleeve.spotify.matcher import Matcher
from sleeve.ui.track_model import Track

log = logging.getLogger(__name__)


def walk(paths: Iterable[Path], cancel: threading.Event) -> Iterator[Path]:
    for path in paths:
        if cancel.is_set():
            return
        if path.is_dir():
            for root, dirs, files in os.walk(path):
                if cancel.is_set():
                    return
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for name in sorted(files, key=str.casefold):
                    candidate = Path(root) / name
                    if is_candidate(candidate):
                        yield candidate
        elif path.is_file():
            yield path


def load_track(path: Path) -> Track | None:
    try:
        detected = detect(path)
    except OSError:
        log.warning("could not open %s", path, exc_info=True)
        return None
    if detected.kind is Kind.UNKNOWN:
        return None
    tags = read_tags(path)
    if not tags.title:
        artist, title = guess_from_filename(path.name)
        tags.title = title
        tags.artist = tags.artist or artist or ""
    return Track(path, detected, tags)


class _ScanSignals(QObject):
    found = Signal(list)
    finished = Signal(int, int)


class ScanWorker(QRunnable):
    """Walks folders and reads tags off the UI thread, reporting tracks in batches."""

    def __init__(self, paths: list[Path], cancel: threading.Event) -> None:
        super().__init__()
        self.paths = paths
        self.cancel = cancel
        self.signals = _ScanSignals()

    def run(self) -> None:
        batch: list[Track] = []
        added = rejected = 0
        last = time.monotonic()
        for path in walk(self.paths, self.cancel):
            track = load_track(path)
            if track is None:
                rejected += 1
                continue
            batch.append(track)
            added += 1
            if len(batch) >= 50 or time.monotonic() - last > 0.25:
                self.signals.found.emit(batch)
                batch, last = [], time.monotonic()
        if batch:
            self.signals.found.emit(batch)
        self.signals.finished.emit(added, rejected)


class _PrepareSignals(QObject):
    progress = Signal(int, float)
    result = Signal(object)
    finished = Signal(object)
    failed = Signal(str)


class PrepareWorker(QRunnable):
    def __init__(
        self, jobs: list[Job], options: Options, ffmpeg: Path | None, cancel: threading.Event
    ) -> None:
        super().__init__()
        self.jobs = jobs
        self.options = options
        self.ffmpeg = ffmpeg
        self.cancel = cancel
        self.signals = _PrepareSignals()
        self._last: dict[int, float] = {}
        self._lock = threading.Lock()

    def _progress(self, key: int, fraction: float) -> None:
        with self._lock:
            if fraction < 1.0 and fraction - self._last.get(key, -1.0) < 0.02:
                return
            self._last[key] = fraction
        self.signals.progress.emit(key, fraction)

    def run(self) -> None:
        try:
            summary: Summary = prepare(
                self.jobs,
                self.options,
                ffmpeg=self.ffmpeg,
                on_progress=self._progress,
                on_result=lambda r: self.signals.result.emit(r),
                cancel=self.cancel,
            )
        except SleeveError as exc:
            self.signals.failed.emit(exc.message)
            return
        except Exception:
            log.exception("prepare crashed")
            self.signals.failed.emit("Something went wrong. Details are in Help > Show log.")
            return
        self.signals.finished.emit(summary)


class _TaskSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class TaskWorker(QRunnable):
    """Runs a blocking call off the UI thread; errors carry a user-facing message."""

    def __init__(self, fn: Callable[[], Any], describe: Callable[[Exception], str]) -> None:
        super().__init__()
        self.fn = fn
        self.describe = describe
        self.signals = _TaskSignals()

    def run(self) -> None:
        try:
            value = self.fn()
        except Exception as exc:
            log.warning("background task failed", exc_info=True)
            self.signals.failed.emit(self.describe(exc))
            return
        self.signals.done.emit(value)


def track_from_entry(entry: Entry) -> Track:
    """A track for a playlist entry: the real file when it exists, otherwise just its details."""
    if entry.path is not None and entry.path.is_file():
        try:
            detected = detect(entry.path)
        except OSError:
            detected = Detected(Kind.UNKNOWN)
        if detected.kind is not Kind.UNKNOWN:
            tags = read_tags(entry.path)
            for name in ("title", "artist", "album", "album_artist", "year", "genre"):
                if not getattr(tags, name):
                    setattr(tags, name, getattr(entry, name))
            tags.track = tags.track or entry.track
            if not tags.title:
                tags.title = guess_from_filename(entry.path.name)[1]
            return Track(entry.path, detected, tags)
    tags = Tags(
        title=entry.title, artist=entry.artist, album=entry.album,
        album_artist=entry.album_artist, track=entry.track, year=entry.year, genre=entry.genre,
    )  # fmt: skip
    source = entry.path or Path(entry.title or "Untitled")
    return Track(source, Detected(Kind.UNKNOWN, duration=entry.duration), tags, has_file=False)


class ImportWorker(QRunnable):
    def __init__(self, entries: list[Entry], cancel: threading.Event) -> None:
        super().__init__()
        self.entries = entries
        self.cancel = cancel
        self.signals = _ScanSignals()

    def run(self) -> None:
        batch: list[Track] = []
        for entry in self.entries:
            if self.cancel.is_set():
                break
            batch.append(track_from_entry(entry))
            if len(batch) >= 50:
                self.signals.found.emit(batch)
                batch = []
        if batch:
            self.signals.found.emit(batch)
        self.signals.finished.emit(len(self.entries), 0)


class _MatchSignals(QObject):
    matched = Signal(int, object)
    waiting = Signal(float)
    failed = Signal(str)
    finished = Signal()


class MatchWorker(QRunnable):
    """Looks up each song on Spotify. A Spotify-wide problem stops the run with one message."""

    def __init__(
        self, items: list[tuple[int, Wanted]], matcher: Matcher, cancel: threading.Event
    ) -> None:
        super().__init__()
        self.items = items
        self.matcher = matcher
        self.cancel = cancel
        self.signals = _MatchSignals()

    def _one(self, item: tuple[int, Wanted]) -> None:
        track_id, wanted = item
        if self.cancel.is_set():
            return
        try:
            match = self.matcher.match(wanted)
        except SpotifyError as exc:
            if not self.cancel.is_set():
                self.cancel.set()
                self.signals.failed.emit(exc.message)
            return
        except Exception:
            log.exception("matching %s failed", wanted)
            match = Match(MatchState.ERROR, message="Couldn't search Spotify for this song.")
        self.signals.matched.emit(track_id, match)

    def run(self) -> None:
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(self._one, self.items))
        finally:
            self.matcher.cache.save()
            self.signals.finished.emit()
