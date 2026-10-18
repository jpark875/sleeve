import json
import logging
import threading
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from sleeve.core.matching import Candidate
from sleeve.spotify.auth import AuthError, Token, refresh
from sleeve.spotify.http import NetworkError, Response, Transport, urllib_transport

log = logging.getLogger(__name__)

API = "https://api.spotify.com/v1"
SEARCH_LIMIT = 10
ADD_CHUNK = 100
MAX_WAIT = 120.0


class Store(Protocol):
    def load(self) -> Token | None: ...
    def save(self, token: Token) -> None: ...
    def clear(self) -> None: ...


class SpotifyError(Exception):
    def __init__(self, message: str, *, reconnect: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.reconnect = reconnect


@dataclass(frozen=True)
class Playlist:
    id: str
    name: str
    uri: str


def _retry_after(resp: Response) -> float:
    raw = resp.headers.get("Retry-After") or resp.headers.get("retry-after") or ""
    try:
        return max(1.0, min(float(raw), 60.0))
    except ValueError:
        return 5.0


def _candidate(item: dict[str, Any]) -> Candidate | None:
    try:
        return Candidate(
            uri=str(item["uri"]),
            name=str(item.get("name", "")),
            artists=tuple(str(a.get("name", "")) for a in item.get("artists") or []),
            album=str((item.get("album") or {}).get("name", "")),
            duration=float(item.get("duration_ms") or 0) / 1000,
            isrc=str((item.get("external_ids") or {}).get("isrc", "")).upper(),
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def explain(resp: Response) -> SpotifyError:
    payload = resp.json()
    error = payload.get("error") if isinstance(payload, dict) else None
    detail = error.get("message", "") if isinstance(error, dict) else str(error or "")
    reason = error.get("reason", "") if isinstance(error, dict) else ""
    log.warning("spotify %s: %s %s", resp.status, detail, reason)
    if resp.status == 401:
        return SpotifyError(
            "Your Spotify sign-in expired. Connect again in Settings.", reconnect=True
        )
    if resp.status == 429:
        wait = resp.headers.get("Retry-After") or resp.headers.get("retry-after") or ""
        when = f" in {wait} seconds" if wait.isdigit() else " in a few minutes"
        return SpotifyError(f"Spotify is limiting requests right now. Try again{when}.")
    if resp.status == 403:
        if "QUOTA" in reason.upper() or "quota" in detail.lower():
            return SpotifyError(
                "Spotify has paused this app's access (quota exceeded). "
                "Your files are ready; create the playlist in Spotify yourself."
            )
        if "premium" in detail.lower():
            return SpotifyError(
                "Spotify requires the developer app's owner to have Premium. "
                "Your files are ready; create the playlist in Spotify yourself."
            )
        return SpotifyError(
            "Spotify didn't allow this request. In Development Mode, your account must be on "
            "the app's user list in the Spotify Developer Dashboard. "
            "Your files are ready; create the playlist in Spotify yourself."
        )
    if resp.status == 404:
        return SpotifyError("Spotify couldn't find that playlist. It may have been deleted.")
    if resp.status >= 500:
        return SpotifyError("Spotify is having trouble right now. Try again later.")
    return SpotifyError(f"Spotify returned an error ({resp.status}). Try again later.")


class SpotifyClient:
    def __init__(
        self, client_id: str, store: Store, transport: Transport = urllib_transport
    ) -> None:
        self.client_id = client_id
        self.store = store
        self.transport = transport
        self.patient = True
        self.cancel = threading.Event()
        self.on_wait: Callable[[float], None] | None = None

    def _token(self, force_refresh: bool = False) -> Token:
        token = self.store.load()
        if token is None:
            raise SpotifyError("Connect Spotify in Settings first.", reconnect=True)
        if force_refresh or token.expired:
            try:
                token = refresh(self.client_id, token, self.transport)
            except AuthError as exc:
                self.store.clear()
                raise SpotifyError(exc.message, reconnect=True) from exc
            self.store.save(token)
        return token

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        url = path if path.startswith("http") else f"{API}{path}"
        refreshed = False
        waited = 0.0
        while True:
            token = self._token(force_refresh=refreshed)
            headers = {"Authorization": f"Bearer {token.access_token}"}
            if data is not None:
                headers["Content-Type"] = "application/json"
            try:
                resp = self.transport(method, url, headers, data)
            except NetworkError as exc:
                raise SpotifyError(
                    "Couldn't reach Spotify. Check your internet connection."
                ) from exc
            if resp.status == 401 and not refreshed:
                refreshed = True
                continue
            if resp.status == 429 and self.patient:
                wait = _retry_after(resp)
                if waited + wait <= MAX_WAIT:
                    log.info("rate limited, waiting %.0fs", wait)
                    if self.on_wait:
                        self.on_wait(wait)
                    if self.cancel.wait(wait):
                        raise SpotifyError("Cancelled.")
                    waited += wait
                    continue
            if resp.status >= 400:
                raise explain(resp)
            return resp.json()

    def me(self) -> dict[str, Any]:
        return dict(self._request("GET", "/me"))

    def find_playlist(self, name: str, owner_id: str) -> Playlist | None:
        url: str | None = f"/me/playlists?{urllib.parse.urlencode({'limit': 50})}"
        pages = 0
        while url and pages < 6:
            page = self._request("GET", url)
            for item in page.get("items") or []:
                if not item:
                    continue
                owner = (item.get("owner") or {}).get("id")
                if owner == owner_id and str(item.get("name", "")).casefold() == name.casefold():
                    return Playlist(item["id"], item["name"], item["uri"])
            url = page.get("next")
            pages += 1
        return None

    def create_playlist(self, name: str) -> Playlist:
        item = self._request(
            "POST",
            "/me/playlists",
            {"name": name, "public": False, "description": "Made with Sleeve."},
        )
        try:
            return Playlist(item["id"], item["name"], item["uri"])
        except (KeyError, TypeError) as exc:
            raise SpotifyError("Spotify sent an unexpected reply. Try again later.") from exc

    def ensure_playlist(self, name: str) -> Playlist:
        user = self.me()
        existing = self.find_playlist(name, str(user.get("id", "")))
        return existing or self.create_playlist(name)

    def search_tracks(self, query: str) -> list[Candidate]:
        params = urllib.parse.urlencode(
            {"q": query, "type": "track", "limit": SEARCH_LIMIT, "market": "from_token"}
        )
        page = self._request("GET", f"/search?{params}")
        items = ((page or {}).get("tracks") or {}).get("items") or []
        return [c for c in (_candidate(i) for i in items if i) if c is not None]

    def playlist_uris(self, playlist_id: str) -> set[str]:
        uris: set[str] = set()
        url: str | None = f"/playlists/{playlist_id}/items?limit=50"
        while url:
            page = self._request("GET", url)
            for entry in page.get("items") or []:
                item = (entry or {}).get("item") or (entry or {}).get("track") or {}
                if item.get("uri"):
                    uris.add(str(item["uri"]))
            url = page.get("next")
        return uris

    def add_tracks(self, playlist_id: str, uris: list[str]) -> list[str]:
        """Append uris in order, skipping ones already there; returns the ones added."""
        present = self.playlist_uris(playlist_id)
        fresh = []
        for uri in uris:
            if uri not in present:
                present.add(uri)
                fresh.append(uri)
        for start in range(0, len(fresh), ADD_CHUNK):
            self._request(
                "POST",
                f"/playlists/{playlist_id}/items",
                {"uris": fresh[start : start + ADD_CHUNK]},
            )
        return fresh
