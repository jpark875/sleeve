import logging
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

log = logging.getLogger(__name__)

FILE_MANAGER = (
    "Finder" if sys.platform == "darwin" else "Explorer" if sys.platform == "win32" else "Files"
)


def reveal(path: Path) -> bool:
    """Show a folder, or select a file inside its folder, in Finder or Explorer."""
    try:
        if path.is_file() and sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
            return True
        if path.is_file() and sys.platform == "win32":
            subprocess.Popen(["explorer", f"/select,{path}"])
            return True
    except OSError:
        log.warning("reveal failed for %s", path, exc_info=True)
    folder = path if path.is_dir() else path.parent
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def open_spotify(uri: str = "spotify:") -> bool:
    return QDesktopServices.openUrl(QUrl(uri))
