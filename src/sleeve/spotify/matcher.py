import json
import logging
import os
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol

from sleeve.core.matching import (
    Candidate,
    Match,
    MatchState,
    Wanted,
    classify,
    queries,
    rank,
)

log = logging.getLogger(__name__)

CACHE_DAYS = 30


class Searcher(Protocol):
    def search_tracks(self, query: str) -> list[Candidate]: ...


class SearchCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] = {}
        self._dirty = False
        try:
            raw = json.loads(path.read_text("utf-8"))
            if isinstance(raw, dict):
                self._data = raw
        except (OSError, ValueError):
            pass

    def get(self, query: str) -> list[Candidate] | None:
        with self._lock:
            entry = self._data.get(query.casefold())
        if not entry or time.time() - float(entry.get("at", 0)) > CACHE_DAYS * 86400:
            return None
        try:
            return [Candidate(**{**c, "artists": tuple(c["artists"])}) for c in entry["items"]]
        except (KeyError, TypeError):
            return None

    def put(self, query: str, items: list[Candidate]) -> None:
        with self._lock:
            self._data[query.casefold()] = {"at": time.time(), "items": [asdict(c) for c in items]}
            self._dirty = True

    def save(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self._data, ensure_ascii=False), "utf-8")
                os.replace(tmp, self.path)
                self._dirty = False
            except OSError:
                log.warning("could not save search cache", exc_info=True)


class Matcher:
    """Finds the Spotify catalog track for a local song, trying the most precise query first."""

    def __init__(self, searcher: Searcher, cache: SearchCache) -> None:
        self.searcher = searcher
        self.cache = cache

    def _search(self, query: str) -> list[Candidate]:
        cached = self.cache.get(query)
        if cached is not None:
            return cached
        results = self.searcher.search_tracks(query)
        self.cache.put(query, results)
        return results

    def match(self, wanted: Wanted) -> Match:
        if not wanted.title.strip():
            return Match(MatchState.NOT_FOUND)
        pool: dict[str, Candidate] = {}
        for query in queries(wanted):
            for candidate in self._search(query):
                pool.setdefault(candidate.uri, candidate)
            result = classify(rank(wanted, list(pool.values())))
            if result.state is MatchState.FOUND:
                return result
        return classify(rank(wanted, list(pool.values())))
