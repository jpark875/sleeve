import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

MANIFEST_NAME = ".sleeve.json"
_CHUNK = 1 << 20


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class Entry:
    file: str
    output_digest: str
    tags: str


class Manifest:
    """Remembers which sources were prepared into a folder, keyed by source content."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.path = folder / MANIFEST_NAME
        self._lock = threading.Lock()
        self._entries: dict[str, Entry] = {}
        self._dirty = 0
        self._flushed = time.monotonic()
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text("utf-8"))
            for key, value in raw.get("entries", {}).items():
                self._entries[key] = Entry(value["file"], value["output_digest"], value["tags"])
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("ignoring unreadable manifest %s: %s", self.path, exc)

    def lookup(self, source_digest: str) -> Entry | None:
        with self._lock:
            entry = self._entries.get(source_digest)
        if entry is None:
            return None
        target = self.folder / entry.file
        if not target.is_file():
            return None
        return entry

    def is_duplicate(self, source_digest: str, tags_fingerprint: str) -> bool:
        entry = self.lookup(source_digest)
        if entry is None or entry.tags != tags_fingerprint:
            return False
        try:
            return file_digest(self.folder / entry.file) == entry.output_digest
        except OSError:
            return False

    def record(self, source_digest: str, entry: Entry) -> None:
        with self._lock:
            self._entries[source_digest] = entry
            self._dirty += 1
            due = self._dirty >= 25 or time.monotonic() - self._flushed > 2.0
        if due:
            self.flush()

    def flush(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            payload = {
                "version": 1,
                "entries": {k: vars(v) for k, v in sorted(self._entries.items())},
            }
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False), "utf-8")
            os.replace(tmp, self.path)
            self._dirty = 0
            self._flushed = time.monotonic()


class SizeIndex:
    """Files in a folder grouped by size, so identical-file checks only hash likely matches."""

    def __init__(self, folder: Path) -> None:
        self._lock = threading.Lock()
        self._by_size: dict[int, list[Path]] = {}
        try:
            for entry in os.scandir(folder):
                if entry.is_file() and not entry.name.startswith("."):
                    self._by_size.setdefault(entry.stat().st_size, []).append(Path(entry.path))
        except OSError:
            pass

    def add(self, path: Path) -> None:
        try:
            size = path.stat().st_size
        except OSError:
            return
        with self._lock:
            self._by_size.setdefault(size, []).append(path)

    def find_identical(self, source: Path, source_digest: str) -> Path | None:
        try:
            size = source.stat().st_size
        except OSError:
            return None
        with self._lock:
            candidates = list(self._by_size.get(size, ()))
        for candidate in candidates:
            try:
                if file_digest(candidate) == source_digest:
                    return candidate
            except OSError:
                continue
        return None
