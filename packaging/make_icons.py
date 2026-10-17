"""Render the app icon. Writes src/sleeve/resources/icon.png and packaging/icons/sleeve.{ico,icns}."""

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen

ROOT = Path(__file__).resolve().parent.parent


def render(size: int = 1024) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 1024, size / 1024)

    tile = QPainterPath()
    tile.addRoundedRect(QRectF(100, 100, 824, 824), 184, 184)
    p.fillPath(tile, QColor("#ECE8E1"))
    p.setClipPath(tile)

    center = QPointF(640, 512)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#1D1D1F"))
    p.drawEllipse(center, 270, 270)
    p.setBrush(Qt.BrushStyle.NoBrush)
    for radius, alpha in ((236, 60), (204, 45), (172, 60), (140, 40)):
        p.setPen(QPen(QColor(255, 255, 255, alpha), 4))
        p.drawEllipse(center, radius, radius)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#D2643C"))
    p.drawEllipse(center, 88, 88)
    p.setBrush(QColor("#ECE8E1"))
    p.drawEllipse(center, 12, 12)

    sleeve = QRectF(170, 272, 480, 480)
    p.setBrush(QColor(0, 0, 0, 40))
    p.drawRect(sleeve.translated(10, 0))
    p.setBrush(QColor("#FBFAF7"))
    p.setPen(QPen(QColor("#CFC9BF"), 3))
    p.drawRect(sleeve)
    p.setPen(QPen(QColor("#1D1D1F"), 14, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(226, 652), QPointF(420, 652))
    p.setPen(QPen(QColor("#9C958B"), 14, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(226, 690), QPointF(352, 690))
    p.end()
    return image


def main() -> int:
    app = QGuiApplication(sys.argv)
    png = ROOT / "src" / "sleeve" / "resources" / "icon.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    render(512).save(str(png))
    out = ROOT / "packaging" / "icons"
    out.mkdir(parents=True, exist_ok=True)
    big = out / "icon-1024.png"
    render(1024).save(str(big))
    try:
        from PIL import Image
    except ImportError:
        print("Pillow not installed; skipped .ico/.icns")
        return 0
    with Image.open(big) as img:
        img.save(out / "sleeve.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
        img.save(out / "sleeve.icns")
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
