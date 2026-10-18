from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from sleeve.core.names import sanitize_component
from sleeve.settings import Settings


class BottomBar(QFrame):
    add_clicked = Signal()
    prepare_clicked = Signal()
    cancel_clicked = Signal()
    spotify_clicked = Signal()

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.running = False
        self.connected = False

        self.add_button = QPushButton("Add files…")
        self.add_button.clicked.connect(self.add_clicked)

        self.playlist = QComboBox()
        self.playlist.setEditable(True)
        self.playlist.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.playlist.setMinimumContentsLength(18)
        self.playlist.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        line = self.playlist.lineEdit()
        assert line is not None
        line.setPlaceholderText("Playlist name")
        line.returnPressed.connect(self._on_primary)
        playlist_label = QLabel("Playlist:")
        playlist_label.setBuddy(self.playlist)

        self.format = QComboBox()
        self.format.setToolTip("Used for songs Sleeve saves as files for Local Files.")
        format_label = QLabel("Format:")
        format_label.setBuddy(self.format)

        self.spotify = QLabel()
        self.spotify.setTextFormat(Qt.TextFormat.RichText)
        self.spotify.linkActivated.connect(lambda _link: self.spotify_clicked.emit())

        self.primary = QPushButton("Prepare for Spotify")
        self.primary.setDefault(True)
        self.primary.setAutoDefault(True)
        self.primary.clicked.connect(self._on_primary)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 10)
        layout.setSpacing(8)
        layout.addWidget(self.add_button)
        layout.addWidget(self.spotify)
        layout.addStretch(1)
        layout.addWidget(playlist_label)
        layout.addWidget(self.playlist)
        layout.addSpacing(8)
        layout.addWidget(format_label)
        layout.addWidget(self.format)
        layout.addSpacing(8)
        layout.addWidget(self.primary)
        self.apply_settings(settings)

    def apply_settings(self, settings: Settings) -> None:
        current = self.playlist_name()
        self.playlist.blockSignals(True)
        self.playlist.clear()
        self.playlist.addItems(settings.recent_playlists)
        self.playlist.setEditText(current or (settings.recent_playlists or [""])[0])
        self.playlist.blockSignals(False)
        self.format.clear()
        self.format.addItem(f"MP3 {settings.mp3_bitrate} kbps", "mp3")
        self.format.addItem(f"M4A (AAC) {settings.aac_bitrate} kbps", "m4a")
        self.format.setCurrentIndex(0 if settings.format == "mp3" else 1)

    def playlist_name(self) -> str:
        return self.playlist.currentText().strip()

    def folder_name(self) -> str:
        return sanitize_component(self.playlist_name()) if self.playlist_name() else ""

    def chosen_format(self) -> str:
        return str(self.format.currentData() or "mp3")

    def primary_text(self) -> str:
        return "Add to Spotify" if self.connected else "Prepare for Spotify"

    def set_connected(self, connected: bool) -> None:
        self.connected = connected
        self.spotify.setText(
            "Connected to Spotify" if connected else "<a href='connect'>Connect Spotify\u2026</a>"
        )
        self.spotify.setToolTip(
            "Songs already on Spotify are added straight to your playlist."
            if connected
            else "Connect to add songs that are already on Spotify directly, "
            "instead of as local files."
        )
        if not self.running:
            self.primary.setText(self.primary_text())

    def set_running(self, running: bool) -> None:
        self.running = running
        self.primary.setText("Cancel" if running else self.primary_text())
        self.primary.setDefault(not running)
        for widget in (self.add_button, self.playlist, self.format, self.spotify):
            widget.setEnabled(not running)

    def set_can_prepare(self, enabled: bool) -> None:
        if not self.running:
            self.primary.setEnabled(enabled)

    def _on_primary(self) -> None:
        if self.running:
            self.cancel_clicked.emit()
        elif self.primary.isEnabled():
            self.prepare_clicked.emit()

    def focus_playlist(self) -> None:
        self.playlist.setFocus(Qt.FocusReason.OtherFocusReason)
        line = self.playlist.lineEdit()
        if line is not None:
            line.selectAll()
