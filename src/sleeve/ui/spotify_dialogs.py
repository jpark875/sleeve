import threading
import webbrowser

from PySide6.QtCore import QThreadPool, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sleeve import APP_NAME
from sleeve.core.matching import Match, MatchState
from sleeve.settings import Settings
from sleeve.spotify.auth import AuthError, Token, TokenStore, redirect_uri, sign_in
from sleeve.ui.dialogs import heading
from sleeve.ui.track_model import Track, format_time
from sleeve.ui.workers import TaskWorker

DASHBOARD = "https://developer.spotify.com/dashboard"


class ConnectDialog(QDialog):
    """Walks through creating a Spotify developer app and signing in with PKCE."""

    connected = Signal()

    def __init__(
        self, settings: Settings, store: TokenStore, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.store = store
        self._cancel: threading.Event | None = None
        self.setWindowTitle("Connect Spotify")
        layout = QVBoxLayout(self)
        layout.addWidget(heading("Connect Spotify"))
        why = QLabel(
            f"Once connected, {APP_NAME} finds songs that are already on Spotify and adds them "
            "straight to your playlist. Only songs Spotify doesn't have become local files."
        )
        why.setWordWrap(True)
        layout.addWidget(why)

        need = QLabel(
            "Spotify only lets personal apps connect this way, so you create one once "
            "(about two minutes). The app owner needs Spotify Premium."
        )
        need.setWordWrap(True)
        layout.addWidget(need)

        def step(number: int, text: str, *widgets: QWidget) -> None:
            row = QHBoxLayout()
            label = QLabel(f"<b>{number}.</b> {text}")
            label.setWordWrap(True)
            row.addWidget(label, 1)
            for widget in widgets:
                row.addWidget(widget)
            layout.addLayout(row)

        dashboard = QPushButton("Open dashboard")
        dashboard.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(DASHBOARD)))
        step(1, "In the Spotify Developer Dashboard, click <b>Create app</b>. Any name works; "
                "tick <b>Web API</b>.", dashboard)  # fmt: skip

        self.redirect = QLineEdit(redirect_uri(settings.spotify_redirect_port))
        self.redirect.setReadOnly(True)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.redirect.text()))
        step(2, "Under <b>Redirect URIs</b>, add exactly:", self.redirect, copy)

        step(3, "Open <b>User Management</b> and add the name and email of the Spotify account "
                "you'll connect with.")  # fmt: skip

        self.client_id = QLineEdit(settings.spotify_client_id)
        self.client_id.setPlaceholderText("Client ID")
        self.client_id.setMinimumWidth(260)
        step(4, "Copy the app's <b>Client ID</b> here:", self.client_id)

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.connect_button = self.buttons.addButton(
            "Connect", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.connect_button.setDefault(True)
        self.connect_button.clicked.connect(self._connect)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.client_id.textChanged.connect(self._update)
        self._update()
        self.setMinimumWidth(600)

    def _update(self) -> None:
        if self._cancel is None:
            self.connect_button.setEnabled(bool(self.client_id.text().strip()))

    def _connect(self) -> None:
        client_id = self.client_id.text().strip()
        if not client_id or self._cancel is not None:
            return
        cancel = threading.Event()
        self._cancel = cancel
        port = self.settings.spotify_redirect_port
        self.connect_button.setEnabled(False)
        self.client_id.setEnabled(False)
        self.status.setText("Finish signing in in your browser…")

        def describe(exc: Exception) -> str:
            return exc.message if isinstance(exc, AuthError) else "Sign-in failed. Try again."

        worker = TaskWorker(lambda: sign_in(client_id, port, webbrowser.open, cancel=cancel),
                            describe)  # fmt: skip
        worker.signals.done.connect(self._done)
        worker.signals.failed.connect(self._failed)
        QThreadPool.globalInstance().start(worker)

    def _done(self, token: object) -> None:
        self._cancel = None
        if isinstance(token, Token):
            self.store.save(token)
            self.settings.spotify_client_id = self.client_id.text().strip()
            self.settings.save()
            self.connected.emit()
            self.accept()

    def _failed(self, message: str) -> None:
        self._cancel = None
        self.client_id.setEnabled(True)
        self.status.setText(message)
        self._update()

    def done(self, result: int) -> None:
        if self._cancel is not None:
            self._cancel.set()
        super().done(result)


_USE_FILE = "Not on Spotify — use my file"
_SKIP = "Not on Spotify — leave it out"


class ReviewDialog(QDialog):
    """Lets the user confirm uncertain matches, or pick their own file instead."""

    def __init__(self, tracks: list[Track], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        count = len(tracks)
        self.setWindowTitle("Check Spotify matches")
        self.tracks = tracks
        self.combos: dict[int, QComboBox] = {}
        layout = QVBoxLayout(self)
        layout.addWidget(heading(f"Check {count} match{'es' if count != 1 else ''}"))
        intro = QLabel(
            "Spotify found songs that look close but not certain. Pick the right version, "
            "or keep your own file."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        tree = QTreeWidget()
        tree.setRootIsDecorated(False)
        tree.setHeaderLabels(["Your song", "On Spotify"])
        tree.setUniformRowHeights(True)
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        tree.header().setStretchLastSection(True)
        tree.setColumnWidth(0, 300)
        for track in tracks:
            mine = f"{track.tags.title} — {track.tags.artist or 'Unknown artist'}"
            if track.detected.duration:
                mine += f" · {format_time(track.detected.duration)}"
            item = QTreeWidgetItem(tree, [mine, ""])
            item.setToolTip(0, mine)
            combo = QComboBox()
            for candidate in track.match.candidates:
                combo.addItem(candidate.describe(), candidate.uri)
            combo.addItem(_USE_FILE if track.has_file else _SKIP, "")
            chosen = track.match.chosen
            if chosen is not None and track.match.state in (MatchState.FOUND, MatchState.REVIEW):
                combo.setCurrentIndex(max(0, combo.findData(chosen.uri)))
            elif track.match.state in (MatchState.KEEP_LOCAL, MatchState.NOT_FOUND):
                combo.setCurrentIndex(combo.count() - 1)
            tree.setItemWidget(item, 1, combo)
            self.combos[track.id] = combo
        layout.addWidget(tree, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(820, min(560, 180 + 34 * count))

    def decisions(self) -> dict[int, Match]:
        out = {}
        for track in self.tracks:
            uri = self.combos[track.id].currentData()
            chosen = next((c for c in track.match.candidates if c.uri == uri), None)
            if chosen is not None:
                out[track.id] = Match(MatchState.FOUND, track.match.candidates, chosen)
            else:
                out[track.id] = Match(MatchState.KEEP_LOCAL, track.match.candidates)
        return out
