import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from sleeve.paths import config_dir, default_output_root

log = logging.getLogger(__name__)

FORMATS = ("mp3", "m4a")
MP3_BITRATES = (320, 256, 192)
AAC_BITRATES = (256, 192)
MAX_RECENTS = 8
DEFAULT_REDIRECT_PORT = 43817


@dataclass
class Settings:
    output_root: str = field(default_factory=lambda: str(default_output_root()))
    format: str = "mp3"
    mp3_bitrate: int = 320
    aac_bitrate: int = 256
    pattern: str = "artist_title"
    recent_playlists: list[str] = field(default_factory=list)
    setup_done: bool = False
    spotify_client_id: str = ""
    spotify_redirect_port: int = DEFAULT_REDIRECT_PORT

    @property
    def path(self) -> Path:
        return config_dir() / "settings.json"

    def remember_playlist(self, name: str) -> None:
        name = name.strip()
        if not name:
            return
        recents = [n for n in self.recent_playlists if n.casefold() != name.casefold()]
        self.recent_playlists = [name, *recents][:MAX_RECENTS]

    def save(self) -> None:
        path = self.path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), "utf-8")
            os.replace(tmp, path)
        except OSError:
            log.exception("could not save settings")

    @classmethod
    def load(cls) -> "Settings":
        settings = cls()
        try:
            raw: dict[str, Any] = json.loads(settings.path.read_text("utf-8"))
        except FileNotFoundError:
            return settings
        except (OSError, ValueError):
            log.exception("could not read settings, using defaults")
            return settings
        defaults = cls()
        for f in fields(cls):
            value = raw.get(f.name, getattr(defaults, f.name))
            if isinstance(value, type(getattr(defaults, f.name))):
                setattr(settings, f.name, value)
        if settings.format not in FORMATS:
            settings.format = "mp3"
        if settings.mp3_bitrate not in MP3_BITRATES:
            settings.mp3_bitrate = 320
        if settings.aac_bitrate not in AAC_BITRATES:
            settings.aac_bitrate = 256
        settings.recent_playlists = [str(n) for n in settings.recent_playlists][:MAX_RECENTS]
        return settings
