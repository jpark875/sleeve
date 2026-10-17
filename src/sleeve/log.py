import logging
import logging.handlers
from pathlib import Path

from sleeve.paths import log_dir


def log_file() -> Path:
    return log_dir() / "sleeve.log"


def setup_logging() -> Path:
    path = log_file()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
    except OSError:
        handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    return path
