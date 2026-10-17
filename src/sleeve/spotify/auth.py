import base64
import hashlib
import json
import logging
import secrets
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from sleeve.spotify.http import NetworkError, Transport, form, urllib_transport

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SCOPES = "playlist-read-private playlist-modify-private"
KEYRING_SERVICE = "Sleeve"
KEYRING_USER = "spotify-token"

_DONE_PAGE = (
    "<!doctype html><meta charset=utf-8><title>Sleeve</title>"
    "<body style='font:15px system-ui;margin:3em'>{message}</body>"
)


class AuthError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class Token:
    access_token: str
    refresh_token: str
    expires_at: float

    @property
    def expired(self) -> bool:
        return time.time() > self.expires_at - 60


def redirect_uri(port: int) -> str:
    return f"http://127.0.0.1:{port}/callback"


def make_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(client_id: str, port: int, challenge: str, state: str) -> str:
    query = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri(port),
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "scope": SCOPES,
        "state": state,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(query)}"


def _token_from(payload: dict[str, Any], previous_refresh: str = "") -> Token:
    try:
        return Token(
            access_token=str(payload["access_token"]),
            refresh_token=str(payload.get("refresh_token") or previous_refresh),
            expires_at=time.time() + int(payload.get("expires_in", 3600)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError("Spotify sent an unexpected reply. Try connecting again.") from exc


def _post_token(data: dict[str, str], transport: Transport) -> dict[str, Any]:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    try:
        resp = transport("POST", TOKEN_URL, headers, form(data))
    except NetworkError as exc:
        raise AuthError("Couldn't reach Spotify. Check your internet connection.") from exc
    payload = resp.json()
    if resp.status != 200:
        log.warning("token request failed: %s %s", resp.status, payload)
        if payload.get("error") == "invalid_client":
            raise AuthError("Spotify didn't recognize the Client ID. Check it in Settings.")
        raise AuthError("Spotify sign-in expired. Connect again in Settings.")
    return dict(payload)


def exchange_code(
    client_id: str, code: str, port: int, verifier: str, transport: Transport = urllib_transport
) -> Token:
    payload = _post_token(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri(port),
            "client_id": client_id,
            "code_verifier": verifier,
        },
        transport,
    )
    return _token_from(payload)


def refresh(client_id: str, token: Token, transport: Transport = urllib_transport) -> Token:
    payload = _post_token(
        {
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
            "client_id": client_id,
        },
        transport,
    )
    return _token_from(payload, token.refresh_token)


class _CallbackHandler(BaseHTTPRequestHandler):
    server: "_CallbackServer"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_error(404)
            return
        params = dict(urllib.parse.parse_qsl(parsed.query))
        self.server.params = params
        ok = "code" in params
        message = (
            "Sleeve is connected to Spotify. You can close this tab."
            if ok
            else "Spotify sign-in was cancelled. You can close this tab."
        )
        body = _DONE_PAGE.format(message=message).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.server.done.set()

    def log_message(self, format: str, *args: Any) -> None:
        log.debug("callback: " + format, *args)


class _CallbackServer(HTTPServer):
    def __init__(self, port: int) -> None:
        super().__init__(("127.0.0.1", port), _CallbackHandler)
        self.params: dict[str, str] = {}
        self.done = threading.Event()
        self.timeout = 0.5


def sign_in(
    client_id: str,
    port: int,
    open_browser: Callable[[str], object],
    *,
    cancel: threading.Event | None = None,
    timeout: float = 300,
    transport: Transport = urllib_transport,
) -> Token:
    """Run the PKCE flow: open the browser, wait for the loopback redirect, swap the code."""
    if not client_id.strip():
        raise AuthError("Add your Spotify Client ID in Settings first.")
    cancel = cancel or threading.Event()
    verifier, challenge = make_pkce()
    state = secrets.token_urlsafe(16)
    try:
        server = _CallbackServer(port)
    except OSError as exc:
        raise AuthError(
            f"Port {port} is in use. Close the app using it, or pick another port in Settings "
            "and add the new redirect URI in the Spotify Developer Dashboard."
        ) from exc
    with server:
        open_browser(authorize_url(client_id.strip(), port, challenge, state))
        deadline = time.monotonic() + timeout
        while not server.done.is_set():
            if cancel.is_set():
                raise AuthError("Sign-in cancelled.")
            if time.monotonic() > deadline:
                raise AuthError("Sign-in timed out. Try connecting again.")
            server.handle_request()
    params = server.params
    if params.get("state") != state:
        raise AuthError("Sign-in failed a security check. Try connecting again.")
    if "code" not in params:
        if params.get("error") == "access_denied":
            raise AuthError("Spotify access wasn't allowed.")
        raise AuthError("Spotify sign-in didn't finish. Try connecting again.")
    return exchange_code(client_id.strip(), params["code"], port, verifier, transport)


class TokenStore:
    """Keeps the token in the OS keychain; falls back to memory if no keychain exists."""

    def __init__(self) -> None:
        self._memory: Token | None = None
        self.persistent = True

    def load(self) -> Token | None:
        try:
            import keyring

            raw = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        except Exception:
            log.warning("keychain unavailable", exc_info=True)
            self.persistent = False
            return self._memory
        if not raw:
            return self._memory
        try:
            return Token(**json.loads(raw))
        except (ValueError, TypeError):
            return None

    def save(self, token: Token) -> None:
        self._memory = token
        try:
            import keyring

            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, json.dumps(asdict(token)))
            self.persistent = True
        except Exception:
            log.warning("keychain unavailable, keeping token in memory", exc_info=True)
            self.persistent = False

    def clear(self) -> None:
        self._memory = None
        try:
            import keyring

            keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
        except Exception:
            log.info("no stored token to delete")
