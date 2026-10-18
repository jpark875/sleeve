from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from sleeve.core.names import PATTERNS, render_stem
from sleeve.settings import AAC_BITRATES, MP3_BITRATES, Settings
from sleeve.spotify.auth import TokenStore

_PATTERN_LABELS = {
    "artist_title": "Artist - Title",
    "title": "Title",
    "track_title": "Track Title",
    "artist_album_title": "Artist - Album - Title",
}


class SettingsDialog(QDialog):
    def __init__(
        self, settings: Settings, store: TokenStore, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.settings = settings
        self.store = store

        files = QGroupBox("Files")
        form = QFormLayout(files)
        self.folder = QLineEdit(settings.output_root)
        self.folder.setReadOnly(True)
        choose = QPushButton("Choose…")
        choose.clicked.connect(self._choose_folder)
        row = QHBoxLayout()
        row.addWidget(self.folder, 1)
        row.addWidget(choose)
        form.addRow("Save to:", row)

        self.mp3 = QComboBox()
        for kbps in MP3_BITRATES:
            self.mp3.addItem(f"{kbps} kbps (constant)", kbps)
        self.mp3.setCurrentIndex(MP3_BITRATES.index(settings.mp3_bitrate))
        form.addRow("MP3 quality:", self.mp3)

        self.aac = QComboBox()
        for kbps in AAC_BITRATES:
            self.aac.addItem(f"{kbps} kbps", kbps)
        self.aac.setCurrentIndex(AAC_BITRATES.index(settings.aac_bitrate))
        form.addRow("AAC quality:", self.aac)

        self.pattern = QComboBox()
        for key in PATTERNS:
            example = render_stem(key, title="Song", artist="Artist", album="Album", track=1)
            self.pattern.addItem(f"{_PATTERN_LABELS[key]}  ({example}.mp3)", key)
        self.pattern.setCurrentIndex(
            max(0, list(PATTERNS).index(settings.pattern) if settings.pattern in PATTERNS else 0)
        )
        form.addRow("File names:", self.pattern)
        note = QLabel("MP3 and M4A files are copied as they are. Other formats are converted.")
        note.setWordWrap(True)
        form.addRow("", note)

        spotify = QGroupBox("Spotify")
        sform = QFormLayout(spotify)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.connect_button = QPushButton()
        self.connect_button.clicked.connect(self._toggle_connection)
        status_row = QHBoxLayout()
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.connect_button)
        sform.addRow("Account:", status_row)
        self.client_id = QLineEdit(settings.spotify_client_id)
        self.client_id.setPlaceholderText("From your app in the Spotify Developer Dashboard")
        sform.addRow("Client ID:", self.client_id)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(settings.spotify_redirect_port)
        self.port.setToolTip("Change only if another app uses this port, then update the "
                             "redirect URI in the Spotify Developer Dashboard.")  # fmt: skip
        sform.addRow("Sign-in port:", self.port)
        self._refresh_status()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(files)
        layout.addWidget(spotify)
        layout.addWidget(buttons)
        self.setMinimumWidth(560)

    def _refresh_status(self) -> None:
        connected = self.store.load() is not None
        if connected:
            where = "" if self.store.persistent else " (until you quit; no keychain found)"
            self.status.setText(f"Connected{where}")
        else:
            self.status.setText("Not connected")
        self.connect_button.setText("Disconnect" if connected else "Connect\u2026")

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Save prepared tracks to", self.folder.text()
        )
        if folder:
            self.folder.setText(str(Path(folder)))

    def _toggle_connection(self) -> None:
        if self.store.load() is not None:
            self.store.clear()
            self._refresh_status()
            return
        from sleeve.ui.spotify_dialogs import ConnectDialog

        self.settings.spotify_client_id = self.client_id.text().strip()
        self.settings.spotify_redirect_port = self.port.value()
        dialog = ConnectDialog(self.settings, self.store, self)
        dialog.exec()
        self.client_id.setText(self.settings.spotify_client_id)
        self._refresh_status()

    def apply(self) -> None:
        s = self.settings
        s.output_root = self.folder.text()
        s.mp3_bitrate = int(self.mp3.currentData())
        s.aac_bitrate = int(self.aac.currentData())
        s.pattern = str(self.pattern.currentData())
        s.spotify_client_id = self.client_id.text().strip()
        s.spotify_redirect_port = self.port.value()
        s.save()
