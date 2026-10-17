import os
import sys
from pathlib import Path

from sleeve import APP_NAME


def _override() -> Path | None:
    value = os.environ.get("SLEEVE_HOME")
    return Path(value) if value else None


def config_dir() -> Path:
    if root := _override():
        return root / "config"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    return Path(base) / APP_NAME.lower()


def log_dir() -> Path:
    if root := _override():
        return root / "logs"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / APP_NAME
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
        return Path(local) / APP_NAME / "Logs"
    base = os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")
    return Path(base) / APP_NAME.lower()


def default_output_root() -> Path:
    if root := _override():
        return root / "Music" / APP_NAME
    return Path.home() / "Music" / APP_NAME


def create_folder(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return path.is_dir()
