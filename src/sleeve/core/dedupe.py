import hashlib
import json
import logging
import os
import threading
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
            payload = {
                "version": 1,
                "entries": {k: vars(v) for k, v in sorted(self._entries.items())},
            }
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False), "utf-8")
            os.replace(tmp, self.path)


def find_identical(folder: Path, source: Path, source_digest: str) -> Path | None:
    """Return a file in folder that is byte-for-byte the same as source."""
    try:
        size = source.stat().st_size
        candidates = [p for p in folder.iterdir() if p.is_file() and p.stat().st_size == size]
    except OSError:
        return None
    for candidate in candidates:
        try:
            if file_digest(candidate) == source_digest:
                return candidate
        except OSError:
            continue
    return None
