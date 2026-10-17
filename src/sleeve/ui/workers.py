import logging
import os
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal

from sleeve.core.errors import SleeveError
from sleeve.core.formats import Kind, detect, is_candidate
from sleeve.core.names import guess_from_filename
from sleeve.core.pipeline import Job, Options, Summary, prepare
from sleeve.core.tags import read_tags
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
