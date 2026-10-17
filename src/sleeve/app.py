import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from sleeve import APP_NAME, __version__
from sleeve.log import setup_logging
from sleeve.settings import Settings


def main() -> int:
    log_path = setup_logging()
    logging.getLogger(__name__).info("%s %s starting, log at %s", APP_NAME, __version__, log_path)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setApplicationVersion(__version__)
    if sys.platform == "win32" and "windows11" in [s.lower() for s in _styles()]:
        app.setStyle("windows11")
    icon = _icon()
    if icon is not None:
        app.setWindowIcon(icon)

    from sleeve.ui.main_window import MainWindow

    window = MainWindow(Settings.load())
    window.show()
    window.first_run()
    return app.exec()


def _styles() -> list[str]:
    from PySide6.QtWidgets import QStyleFactory

    return list(QStyleFactory.keys())


def _icon() -> QIcon | None:
    from pathlib import Path

    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    for candidate in (base / "resources" / "icon.png", base / "sleeve" / "resources" / "icon.png"):
        if candidate.is_file():
            return QIcon(str(candidate))
    return None
