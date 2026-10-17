from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QImageReader

from sleeve.core.tags import Artwork

MAX_EDGE = 1200
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp", ".gif", ".tif", ".tiff"}


def load_artwork(path: Path) -> Artwork | None:
    """Load an image file as embedded artwork, re-encoding anything that isn't a small JPEG/PNG."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    direct = Artwork.from_bytes(raw)
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if direct and len(raw) <= 1_500_000 and max(size.width(), size.height()) <= MAX_EDGE:
        return direct
    image = reader.read()
    if image.isNull():
        return None
    if max(image.width(), image.height()) > MAX_EDGE:
        image = image.scaled(
            MAX_EDGE,
            MAX_EDGE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    image = image.convertToFormat(QImage.Format.Format_RGB32)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, b"JPEG", 90)
    return Artwork.from_bytes(bytes(buffer.data().data()))


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS
