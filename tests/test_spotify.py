import json
import socket
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Mapping

import pytest

from sleeve.spotify import auth
from sleeve.spotify.api import Playlist, SpotifyClient, SpotifyError, explain
from sleeve.spotify.auth import AuthError, Token
from sleeve.spotify.http import NetworkError, Response


class MemoryStore:
    def __init__(self, token: Token | None) -> None:
        self.token = token

    def load(self) -> Token | None:
        return self.token

    def save(self, token: Token) -> None:
        self.token = token

    def clear(self) -> None:
        self.token = None


class FakeTransport:
    def __init__(self, *responses: Response) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> Response:
        self.calls.append((method, url, dict(headers), body))
        return self.responses.pop(0)


def ok(payload: object, status: int = 200) -> Response:
    return Response(status, json.dumps(payload).encode())


def fresh() -> Token:
    return Token("access", "refresh", time.time() + 3600)


def test_pkce_challenge_is_s256() -> None:
    verifier, challenge = auth.make_pkce()
    assert 43 <= len(verifier) <= 128
    assert "=" not in challenge and len(challenge) == 43


def test_authorize_url_has_no_secret() -> None:
    url = auth.authorize_url("cid", 43817, "chal", "st")
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    assert query["redirect_uri"] == "http://127.0.0.1:43817/callback"
    assert query["code_challenge_method"] == "S256"
    assert "client_secret" not in query


def test_create_playlist_uses_me_endpoint() -> None:
    transport = FakeTransport(ok({"id": "p1", "name": "Demos", "uri": "spotify:playlist:p1"}, 201))
    client = SpotifyClient("cid", MemoryStore(fresh()), transport)
    playlist = client.create_playlist("Demos")
    assert playlist == Playlist("p1", "Demos", "spotify:playlist:p1")
    method, url, headers, body = transport.calls[0]
    assert (method, url) == ("POST", "https://api.spotify.com/v1/me/playlists")
    assert json.loads(body or b"{}")["public"] is False
    assert headers["Authorization"] == "Bearer access"


def test_ensure_playlist_reuses_existing() -> None:
    transport = FakeTransport(
        ok({"id": "me"}),
        ok(
            {
                "items": [
                    {"id": "x", "name": "Other", "uri": "u", "owner": {"id": "me"}},
                    {
                        "id": "p",
                        "name": "demos",
                        "uri": "spotify:playlist:p",
                        "owner": {"id": "me"},
                    },
                ],
                "next": None,
            }
        ),
    )
    client = SpotifyClient("cid", MemoryStore(fresh()), transport)
    assert client.ensure_playlist("Demos").id == "p"
    assert len(transport.calls) == 2


def test_401_refreshes_once_then_retries() -> None:
    transport = FakeTransport(
        Response(401, b"{}"),
        ok({"access_token": "new", "expires_in": 3600}),
        ok({"id": "me"}),
    )
    store = MemoryStore(fresh())
    client = SpotifyClient("cid", store, transport)
    assert client.me() == {"id": "me"}
    assert store.token is not None and store.token.access_token == "new"
    assert store.token.refresh_token == "refresh"


def test_failed_refresh_clears_token() -> None:
    expired = Token("a", "r", time.time() - 10)
    store = MemoryStore(expired)
    client = SpotifyClient("cid", store, FakeTransport(ok({"error": "invalid_grant"}, 400)))
    with pytest.raises(SpotifyError) as info:
        client.me()
    assert info.value.reconnect
    assert store.token is None


def test_not_connected() -> None:
    with pytest.raises(SpotifyError) as info:
        SpotifyClient("cid", MemoryStore(None), FakeTransport()).me()
    assert "Connect Spotify" in info.value.message


@pytest.mark.parametrize(
    ("resp", "fragment"),
    [
        (Response(429, b"{}", {"Retry-After": "30"}), "Try again in 30 seconds"),
        (Response(429, b"{}"), "in a few minutes"),
        (
            ok({"error": {"status": 403, "message": "Forbidden", "reason": "QUOTA_EXCEEDED"}}, 403),
            "quota exceeded",
        ),
        (
            ok({"error": {"status": 403, "message": "Active premium subscription required"}}, 403),
            "Premium",
        ),
        (ok({"error": {"status": 403, "message": "Forbidden"}}, 403), "user list"),
        (Response(503, b"oops"), "having trouble"),
    ],
)
def test_error_messages(resp: Response, fragment: str) -> None:
    assert fragment in explain(resp).message


def test_network_error_is_plain() -> None:
    def down(*args: object) -> Response:
        raise NetworkError("dns")

    with pytest.raises(SpotifyError) as info:
        SpotifyClient("cid", MemoryStore(fresh()), down).me()
    assert "internet connection" in info.value.message


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_sign_in_round_trip() -> None:
    port = free_port()
    transport = FakeTransport(ok({"access_token": "a", "refresh_token": "r", "expires_in": 60}))

    def browser(url: str) -> None:
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        target = f"{query['redirect_uri']}?code=xyz&state={query['state']}"
        threading.Thread(target=lambda: urllib.request.urlopen(target, timeout=5).read()).start()

    token = auth.sign_in("cid", port, browser, timeout=10, transport=transport)
    assert token.access_token == "a"
    body = dict(urllib.parse.parse_qsl((transport.calls[0][3] or b"").decode()))
    assert body["code"] == "xyz" and "code_verifier" in body and "client_secret" not in body


def test_sign_in_rejects_bad_state() -> None:
    port = free_port()

    def browser(url: str) -> None:
        target = f"http://127.0.0.1:{port}/callback?code=xyz&state=forged"
        threading.Thread(target=lambda: urllib.request.urlopen(target, timeout=5).read()).start()

    with pytest.raises(AuthError) as info:
        auth.sign_in("cid", port, browser, timeout=10, transport=FakeTransport())
    assert "security check" in info.value.message


def test_sign_in_needs_client_id() -> None:
    with pytest.raises(AuthError):
        auth.sign_in(" ", 1, lambda url: None)


def test_sign_in_cancel() -> None:
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(AuthError) as info:
        auth.sign_in("cid", free_port(), lambda url: None, cancel=cancel)
    assert "cancelled" in info.value.message


def track_json(name: str, uri: str, isrc: str = "") -> dict[str, object]:
    return {
        "uri": uri,
        "name": name,
        "artists": [{"name": "Artist"}],
        "album": {"name": "LP"},
        "duration_ms": 201000,
        "external_ids": {"isrc": isrc},
    }


def test_search_parses_candidates() -> None:
    transport = FakeTransport(
        ok({"tracks": {"items": [track_json("Song", "spotify:track:1", "usabc1234567"), None]}})
    )
    client = SpotifyClient("cid", MemoryStore(fresh()), transport)
    results = client.search_tracks('track:"song"')
    assert len(results) == 1
    assert results[0].artists == ("Artist",) and results[0].duration == 201.0
    assert results[0].isrc == "USABC1234567"
    url = transport.calls[0][1]
    assert "limit=10" in url and "market=from_token" in url and "type=track" in url


def test_add_tracks_skips_existing_and_chunks() -> None:
    existing = {
        "items": [{"item": {"uri": "spotify:track:0"}}, {"track": {"uri": "spotify:track:1"}}],
        "next": None,
    }
    uris = [f"spotify:track:{i}" for i in range(0, 152)]
    transport = FakeTransport(
        ok(existing), ok({"snapshot_id": "a"}, 201), ok({"snapshot_id": "b"}, 201)
    )
    client = SpotifyClient("cid", MemoryStore(fresh()), transport)
    assert len(client.add_tracks("pl", uris)) == 150
    posts = [c for c in transport.calls if c[0] == "POST"]
    assert [c[1] for c in posts] == ["https://api.spotify.com/v1/playlists/pl/items"] * 2
    first = json.loads(posts[0][3] or b"{}")["uris"]
    assert len(first) == 100 and first[0] == "spotify:track:2"


def test_rate_limit_waits_then_retries() -> None:
    transport = FakeTransport(Response(429, b"{}", {"Retry-After": "1"}), ok({"id": "me"}))
    client = SpotifyClient("cid", MemoryStore(fresh()), transport)
    waits: list[float] = []
    client.on_wait = waits.append
    assert client.me() == {"id": "me"}
    assert waits == [1.0]


def test_rate_limit_can_be_cancelled() -> None:
    transport = FakeTransport(Response(429, b"{}", {"Retry-After": "30"}))
    client = SpotifyClient("cid", MemoryStore(fresh()), transport)
    client.cancel.set()
    with pytest.raises(SpotifyError):
        client.me()
