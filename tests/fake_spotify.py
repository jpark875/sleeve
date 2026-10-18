import json
import time
import urllib.parse
from collections.abc import Mapping

from sleeve.spotify.auth import Token
from sleeve.spotify.http import Response


class MemoryStore:
    persistent = True

    def __init__(self, token: Token | None = None) -> None:
        self.token = token or Token("access", "refresh", time.time() + 3600)

    def load(self) -> Token | None:
        return self.token

    def save(self, token: Token) -> None:
        self.token = token

    def clear(self) -> None:
        self.token = None


def track(name: str, artist: str, uri: str, duration: float = 1.0) -> dict[str, object]:
    return {
        "uri": uri,
        "name": name,
        "artists": [{"name": artist}],
        "album": {"name": "Album"},
        "duration_ms": int(duration * 1000),
        "external_ids": {},
    }


class FakeSpotify:
    """Just enough of the Web API for the app's flow."""

    def __init__(self, catalog: list[dict[str, object]], existing: list[str] | None = None) -> None:
        self.catalog = catalog
        self.playlist: list[str] = list(existing or [])
        self.created: list[str] = []
        self.searches: list[str] = []
        self.fail_add: int | None = None

    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> Response:
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.removeprefix("/v1")
        query = dict(urllib.parse.parse_qsl(parsed.query))
        if path == "/search":
            q = query["q"].casefold()
            self.searches.append(q)
            words = [
                w
                for w in q.replace('"', " ").replace("track:", " ").replace("artist:", " ").split()
            ]
            hits = [
                t
                for t in self.catalog
                if all(
                    w in f"{t['name']} {t['artists'][0]['name']}".casefold()  # type: ignore[index]
                    for w in words
                )
            ]
            return self._ok({"tracks": {"items": hits[:10]}})
        if path == "/me":
            return self._ok({"id": "me"})
        if path == "/me/playlists" and method == "GET":
            items = [
                {"id": "pl1", "name": n, "uri": "spotify:playlist:pl1", "owner": {"id": "me"}}
                for n in self.created
            ]
            return self._ok({"items": items, "next": None})
        if path == "/me/playlists" and method == "POST":
            name = json.loads(body or b"{}")["name"]
            self.created.append(name)
            return self._ok({"id": "pl1", "name": name, "uri": "spotify:playlist:pl1"}, 201)
        if path == "/playlists/pl1/items" and method == "GET":
            return self._ok({"items": [{"item": {"uri": u}} for u in self.playlist], "next": None})
        if path == "/playlists/pl1/items" and method == "POST":
            if self.fail_add:
                return self._ok(
                    {"error": {"status": self.fail_add, "message": "Forbidden"}}, self.fail_add
                )
            self.playlist.extend(json.loads(body or b"{}")["uris"])
            return self._ok({"snapshot_id": "x"}, 201)
        return self._ok({"error": {"status": 404, "message": "not found"}}, 404)

    @staticmethod
    def _ok(payload: object, status: int = 200) -> Response:
        return Response(status, json.dumps(payload).encode())
