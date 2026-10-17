import logging
import os
import shutil
import threading
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from sleeve.core import errors
from sleeve.core.dedupe import Entry, Manifest, SizeIndex, file_digest
from sleeve.core.errors import SleeveError
from sleeve.core.ffmpeg import Cancelled, Encoding, convert
from sleeve.core.formats import Action, Detected, Kind
from sleeve.core.names import (
    existing_names,
    fit_path,
    guess_from_filename,
    render_stem,
    unique_path,
)
from sleeve.core.tags import Tags, write_tags

log = logging.getLogger(__name__)


class Outcome(StrEnum):
    READY = "ready"
    UPDATED = "updated"
    DUPLICATE = "duplicate"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Job:
    key: int
    source: Path
    tags: Tags
    detected: Detected


@dataclass(frozen=True)
class Options:
    folder: Path
    encoding: Encoding = field(default_factory=Encoding.mp3)
    pattern: str = "artist_title"
    workers: int = 3


@dataclass(frozen=True)
class Result:
    key: int
    outcome: Outcome
    message: str = ""
    path: Path | None = None


@dataclass
class Summary:
    results: list[Result]
    fatal: str | None = None

    def count(self, *outcomes: Outcome) -> int:
        return sum(r.outcome in outcomes for r in self.results)

    @property
    def ready(self) -> int:
        return self.count(Outcome.READY, Outcome.UPDATED)

    def problems(self) -> str:
        parts = []
        for outcomes, word in (
            ((Outcome.SKIPPED, Outcome.DUPLICATE), "skipped"),
            ((Outcome.FAILED,), "failed"),
            ((Outcome.CANCELLED,), "cancelled"),
        ):
            if n := self.count(*outcomes):
                parts.append(f"{n} {word}")
        return ", ".join(parts)

    def text(self) -> str:
        if self.fatal:
            return self.fatal
        ready = self.ready
        text = f"{ready} track{'s' if ready != 1 else ''} ready."
        if problems := self.problems():
            text += f" {problems.capitalize()} \u2014 see details."
        return text


ProgressFn = Callable[[int, float], None]
ResultFn = Callable[[Result], None]


def ensure_writable(folder: Path) -> None:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f".sleeve-write-test-{uuid.uuid4().hex}"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as exc:
        raise errors.from_os_error(exc, folder) from exc


class _Batch:
    def __init__(
        self,
        options: Options,
        ffmpeg: Path | None,
        on_progress: ProgressFn,
        cancel: threading.Event,
    ) -> None:
        self.options = options
        self.ffmpeg = ffmpeg
        self.on_progress = on_progress
        self.cancel = cancel
        self.manifest = Manifest(options.folder)
        self.sizes = SizeIndex(options.folder)
        self.names_lock = threading.Lock()
        self.taken = existing_names(options.folder)
        self.claimed: dict[str, int] = {}
        self.fatal: str | None = None

    def target_for(self, job: Job, extension: str, previous: Entry | None) -> Path:
        folder = self.options.folder
        guessed_artist, guessed_title = guess_from_filename(job.source.name)
        stem = render_stem(
            self.options.pattern,
            title=job.tags.title or guessed_title,
            artist=job.tags.artist or guessed_artist or "",
            album=job.tags.album,
            track=job.tags.track,
        )
        wanted = fit_path(folder, stem, extension)
        with self.names_lock:
            if previous and previous.file.casefold() == wanted.name.casefold():
                return folder / previous.file
            return unique_path(wanted, self.taken)

    def run(self, job: Job) -> Result:
        name = job.source.name
        if self.cancel.is_set():
            return Result(job.key, Outcome.CANCELLED)
        if job.detected.protected:
            return Result(job.key, Outcome.SKIPPED, errors.protected(name).message)
        if job.detected.kind is Kind.UNKNOWN:
            return Result(job.key, Outcome.SKIPPED, errors.not_audio(name).message)
        if job.detected.action is Action.CONVERT and self.ffmpeg is None:
            return Result(job.key, Outcome.FAILED, errors.ffmpeg_missing(name).message)
        try:
            digest = file_digest(job.source)
        except FileNotFoundError:
            return Result(job.key, Outcome.FAILED, errors.source_missing(name).message)
        except OSError:
            return Result(job.key, Outcome.FAILED, errors.unreadable(name).message)

        fingerprint = job.tags.fingerprint()
        with self.names_lock:
            first = self.claimed.setdefault(digest, job.key)
        if first != job.key:
            return Result(
                job.key, Outcome.DUPLICATE, f"'{name}' is the same as another file in this list."
            )
        if self.manifest.is_duplicate(digest, fingerprint):
            entry = self.manifest.lookup(digest)
            shown = entry.file if entry else name
            return Result(job.key, Outcome.DUPLICATE, f"'{name}' is already prepared as '{shown}'.")
        if job.detected.action is Action.COPY:
            same = self.sizes.find_identical(job.source, digest)
            if same is not None:
                return Result(
                    job.key,
                    Outcome.DUPLICATE,
                    f"'{name}' is already in the folder as '{same.name}'.",
                )

        previous = self.manifest.lookup(digest)
        extension = (
            job.detected.extension
            if job.detected.action is Action.COPY
            else self.options.encoding.extension
        )
        target = self.target_for(job, extension, previous)
        tmp = target.with_name(f".{uuid.uuid4().hex[:12]}.part")
        try:
            self.on_progress(job.key, 0.0)
            if job.detected.action is Action.COPY:
                shutil.copyfile(job.source, tmp)
            else:
                assert self.ffmpeg is not None
                convert(
                    self.ffmpeg,
                    job.source,
                    tmp,
                    self.options.encoding,
                    duration=job.detected.duration,
                    channels=job.detected.channels,
                    on_progress=lambda f: self.on_progress(job.key, f * 0.95),
                    cancel=self.cancel,
                )
            write_tags(tmp, job.tags, extension)
            os.replace(tmp, target)
            if previous and previous.file != target.name:
                (self.options.folder / previous.file).unlink(missing_ok=True)
            self.manifest.record(digest, Entry(target.name, file_digest(target), fingerprint))
            self.sizes.add(target)
            self.on_progress(job.key, 1.0)
        except Cancelled:
            return Result(job.key, Outcome.CANCELLED)
        except SleeveError as exc:
            return self._fail(job, exc)
        except OSError as exc:
            log.exception("writing %s failed", target)
            return self._fail(job, errors.from_os_error(exc, self.options.folder))
        except Exception:
            log.exception("preparing %s failed", job.source)
            return self._fail(job, errors.unreadable(name))
        finally:
            tmp.unlink(missing_ok=True)
        outcome = Outcome.UPDATED if previous else Outcome.READY
        return Result(job.key, outcome, path=target)

    def _fail(self, job: Job, exc: SleeveError) -> Result:
        if exc.fatal:
            self.fatal = self.fatal or exc.message
            self.cancel.set()
        return Result(job.key, Outcome.FAILED, exc.message)


def prepare(
    jobs: Sequence[Job],
    options: Options,
    *,
    ffmpeg: Path | None,
    on_progress: ProgressFn = lambda key, fraction: None,
    on_result: ResultFn = lambda result: None,
    cancel: threading.Event | None = None,
) -> Summary:
    """Copy or convert each job into options.folder. Originals are only ever read."""
    cancel = cancel or threading.Event()
    ensure_writable(options.folder)
    batch = _Batch(options, ffmpeg, on_progress, cancel)
    results: list[Result] = []
    lock = threading.Lock()

    def work(job: Job) -> None:
        result = batch.run(job)
        with lock:
            results.append(result)
        on_result(result)

    try:
        with ThreadPoolExecutor(max_workers=max(1, options.workers)) as pool:
            list(pool.map(work, jobs))
    finally:
        try:
            batch.manifest.flush()
        except OSError:
            log.exception("could not save manifest")
    order = {job.key: i for i, job in enumerate(jobs)}
    results.sort(key=lambda r: order[r.key])
    return Summary(results, batch.fatal)
