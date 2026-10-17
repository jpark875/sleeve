from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter, QPaintEvent, QPalette, QPen
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class DropArea(QWidget):
    """The empty state: one line of text inside a quiet drop target."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.highlighted = False
        self.label = QLabel("Drop audio files, videos or a folder here")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        layout = QVBoxLayout(self)
        layout.addWidget(self.label)

    def set_highlighted(self, on: bool) -> None:
        self.highlighted = on
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        role = QPalette.ColorRole.Highlight if self.highlighted else QPalette.ColorRole.Mid
        pen = QPen(palette.color(role), 1.5 if self.highlighted else 1.0)
        pen.setStyle(Qt.PenStyle.SolidLine if self.highlighted else Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(12.5, 12.5, -12.5, -12.5), 6, 6)
