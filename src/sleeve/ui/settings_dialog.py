import threading
import webbrowser
from pathlib import Path

from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QGuiApplication
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

from sleeve import APP_NAME
from sleeve.core.names import PATTERNS, render_stem
from sleeve.settings import AAC_BITRATES, MP3_BITRATES, Settings
from sleeve.spotify.auth import AuthError, TokenStore, redirect_uri, sign_in
from sleeve.ui.workers import TaskWorker

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
        self._cancel_sign_in: threading.Event | None = None

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

        self.format = QComboBox()
        self.format.addItem("MP3", "mp3")
        self.format.addItem("M4A (AAC)", "m4a")
        self.format.setCurrentIndex(0 if settings.format == "mp3" else 1)
        form.addRow("Convert to:", self.format)

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

        spotify = QGroupBox("Spotify (optional)")
        sform = QFormLayout(spotify)
        about = QLabel(
            f"Connecting lets {APP_NAME} create the playlist for you. Everything else works "
            "without it. You need your own app in the Spotify Developer Dashboard; paste its "
            "Client ID here and add the redirect URI below to it."
        )
        about.setWordWrap(True)
        sform.addRow(about)
        self.client_id = QLineEdit(settings.spotify_client_id)
        self.client_id.setPlaceholderText("Client ID from developer.spotify.com")
        sform.addRow("Client ID:", self.client_id)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(settings.spotify_redirect_port)
        self.redirect = QLineEdit()
        self.redirect.setReadOnly(True)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.redirect.text()))
        self.port.valueChanged.connect(self._update_redirect)
        redirect_row = QHBoxLayout()
        redirect_row.addWidget(self.redirect, 1)
        redirect_row.addWidget(copy)
        sform.addRow("Redirect URI:", redirect_row)
        sform.addRow("Port:", self.port)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.connect_button = QPushButton()
        self.connect_button.clicked.connect(self._toggle_connection)
        status_row = QHBoxLayout()
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.connect_button)
        sform.addRow(status_row)
        self._update_redirect()
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

    def _update_redirect(self) -> None:
        self.redirect.setText(redirect_uri(self.port.value()))

    def _refresh_status(self, message: str = "") -> None:
        connected = self.store.load() is not None
        if message:
            self.status.setText(message)
        elif connected:
            where = "" if self.store.persistent else " (until you quit; no keychain found)"
            self.status.setText(f"Connected{where}.")
        else:
            self.status.setText("Not connected.")
        self.connect_button.setText("Disconnect" if connected else "Connect…")
        self.connect_button.setEnabled(True)

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Save prepared tracks to", self.folder.text()
        )
        if folder:
            self.folder.setText(str(Path(folder)))

    def _toggle_connection(self) -> None:
        if self._cancel_sign_in is not None:
            self._cancel_sign_in.set()
            return
        if self.store.load() is not None:
            self.store.clear()
            self._refresh_status()
            return
        client_id = self.client_id.text().strip()
        if not client_id:
            self._refresh_status("Paste your Client ID first.")
            return
        cancel = threading.Event()
        self._cancel_sign_in = cancel
        port = self.port.value()
        self.status.setText("Finish signing in in your browser…")
        self.connect_button.setText("Cancel")

        def describe(exc: Exception) -> str:
            return exc.message if isinstance(exc, AuthError) else "Sign-in failed. Try again."

        worker = TaskWorker(
            lambda: sign_in(client_id, port, webbrowser.open, cancel=cancel), describe
        )
        worker.signals.done.connect(self._signed_in)
        worker.signals.failed.connect(self._sign_in_failed)
        QThreadPool.globalInstance().start(worker)

    def _signed_in(self, token: object) -> None:
        from sleeve.spotify.auth import Token

        self._cancel_sign_in = None
        if isinstance(token, Token):
            self.store.save(token)
            self.settings.spotify_client_id = self.client_id.text().strip()
            self.settings.spotify_redirect_port = self.port.value()
            self.settings.save()
        self._refresh_status()

    def _sign_in_failed(self, message: str) -> None:
        self._cancel_sign_in = None
        self._refresh_status(message)

    def done(self, result: int) -> None:
        if self._cancel_sign_in is not None:
            self._cancel_sign_in.set()
        super().done(result)

    def apply(self) -> None:
        s = self.settings
        s.output_root = self.folder.text()
        s.format = str(self.format.currentData())
        s.mp3_bitrate = int(self.mp3.currentData())
        s.aac_bitrate = int(self.aac.currentData())
        s.pattern = str(self.pattern.currentData())
        s.spotify_client_id = self.client_id.text().strip()
        s.spotify_redirect_port = self.port.value()
        s.save()
