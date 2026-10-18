import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QItemSelection, QModelIndex, QSize, Qt, QThreadPool, QTimer
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from sleeve import APP_NAME
from sleeve.core.ffmpeg import Encoding, find_ffmpeg
from sleeve.core.formats import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS
from sleeve.core.library import PLAYLIST_EXTENSIONS, LibraryError, Playlist, read_playlists
from sleeve.core.matching import Match, MatchState
from sleeve.core.pipeline import Job, Options, Summary
from sleeve.log import log_file
from sleeve.paths import config_dir, create_folder
from sleeve.settings import Settings
from sleeve.spotify.api import SpotifyClient, SpotifyError
from sleeve.spotify.auth import TokenStore
from sleeve.spotify.http import Transport, urllib_transport
from sleeve.spotify.matcher import Matcher, SearchCache
from sleeve.ui.artwork import is_image, load_artwork
from sleeve.ui.bottom_bar import BottomBar
from sleeve.ui.dialogs import (
    BulkEditDialog,
    DetailsDialog,
    FinishDialog,
    PhoneHelp,
    PlaylistPicker,
    RunReport,
    SetupGuide,
    about,
)
from sleeve.ui.drop_area import DropArea
from sleeve.ui.platform import FILE_MANAGER, reveal
from sleeve.ui.settings_dialog import SettingsDialog
from sleeve.ui.spotify_dialogs import ConnectDialog, ReviewDialog
from sleeve.ui.track_model import Column, Track, TrackModel, format_time
from sleeve.ui.track_table import TrackTable
from sleeve.ui.workers import ImportWorker, MatchWorker, PrepareWorker, ScanWorker, TaskWorker

log = logging.getLogger(__name__)

_MEDIA = sorted(AUDIO_EXTENSIONS | VIDEO_EXTENSIONS)
_AUDIO_FILTER = "Audio and video files ({})".format(" ".join(f"*{e}" for e in _MEDIA))
_PLAYLIST_FILTER = "Playlists (*.m3u *.m3u8 *.xml)"
_PENDING = (MatchState.UNCHECKED, MatchState.SEARCHING)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


@dataclass
class _SpotifyResult:
    uri: str
    added: list[str]


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, store: TokenStore | None = None) -> None:
        super().__init__()
        self.settings = settings
        self.store = store or TokenStore()
        self.pool = QThreadPool.globalInstance()
        self.ffmpeg = find_ffmpeg()
        log.info("ffmpeg: %s", self.ffmpeg or "not found")
        self.scan_cancel = threading.Event()
        self.prepare_cancel: threading.Event | None = None
        self.match_cancel = threading.Event()
        self.scanning = 0
        self.matching = False
        self.match_queue: set[int] = set()
        self.match_done = 0
        self.match_total = 0
        self.prepare_after_match = False
        self.connected = False
        self.last_summary: Summary | None = None
        self.last_folder: Path | None = None
        self.report: RunReport | None = None
        self._dialog: Any = None
        self._import_path = Path()
        self._run_uris: list[str] = []
        self.search_cache = SearchCache(config_dir() / "spotify-search.json")
        self.transport: Transport = urllib_transport

        self.setWindowTitle(APP_NAME)
        self.setAcceptDrops(True)
        self.setMinimumSize(QSize(720, 420))
        self.resize(1000, 620)

        self.model = TrackModel(self)
        self.table = TrackTable(self.model)
        self.table.doubleClicked.connect(self._double_clicked)
        self.drop_area = DropArea()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.drop_area)
        self.stack.addWidget(self.table)

        self.bottom = BottomBar(settings)
        self.bottom.add_clicked.connect(self.add_files)
        self.bottom.prepare_clicked.connect(self.prepare)
        self.bottom.cancel_clicked.connect(self.cancel)
        self.bottom.spotify_clicked.connect(self.spotify_button)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.stack, 1)
        layout.addWidget(self.bottom)
        self.setCentralWidget(central)

        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.RichText)
        self.message.linkActivated.connect(self._link)
        self.message.setContentsMargins(8, 0, 0, 2)
        self.totals = QLabel()
        self.totals.setContentsMargins(0, 0, 8, 2)
        self.statusBar().addWidget(self.message, 1)
        self.statusBar().addPermanentWidget(self.totals)
        self.statusBar().setSizeGripEnabled(False)

        self.match_timer = QTimer(self)
        self.match_timer.setSingleShot(True)
        self.match_timer.setInterval(400)
        self.match_timer.timeout.connect(self._start_matching)

        self._build_menus()
        self.model.rowsInserted.connect(self._update_state)
        self.model.rowsRemoved.connect(self._update_state)
        self.model.modelReset.connect(self._update_state)
        self.model.dataChanged.connect(self._update_totals)
        self.model.tags_edited.connect(self._tags_edited)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.refresh_connection()

    def _action(
        self, text: str, slot: Any, shortcut: Any = None, *, role: QAction.MenuRole | None = None
    ) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(slot)
        if shortcut is not None:
            if isinstance(shortcut, list):
                action.setShortcuts(shortcut)
            else:
                action.setShortcut(shortcut)
        if role is not None:
            action.setMenuRole(role)
        return action

    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        self.act_add = self._action("Add files…", self.add_files, QKeySequence.StandardKey.Open)
        self.act_add_folder = self._action(
            "Add folder…", self.add_folder, QKeySequence("Ctrl+Shift+O")
        )
        self.act_import = self._action(
            "Import playlist…", self.choose_playlist, QKeySequence("Ctrl+Shift+I")
        )
        self.act_prepare = self._action(
            "Prepare for Spotify", self.prepare, QKeySequence("Ctrl+Return")
        )
        self.act_show = self._action(
            f"Show output folder in {FILE_MANAGER}", self.show_output, QKeySequence("Ctrl+Shift+R")
        )
        self.act_connect = self._action("Connect Spotify…", self.spotify_button)
        self.act_settings = self._action(
            "Settings…",
            self.open_settings,
            QKeySequence.StandardKey.Preferences,
            role=QAction.MenuRole.PreferencesRole,
        )
        if self.act_settings.shortcut().isEmpty():
            self.act_settings.setShortcut(QKeySequence("Ctrl+,"))
        quit_action = self._action(
            "Quit", self.close, QKeySequence.StandardKey.Quit, role=QAction.MenuRole.QuitRole
        )
        for action in (self.act_add, self.act_add_folder, self.act_import):
            file_menu.addAction(action)
        file_menu.addSeparator()
        file_menu.addAction(self.act_prepare)
        file_menu.addAction(self.act_show)
        file_menu.addSeparator()
        file_menu.addAction(self.act_connect)
        file_menu.addAction(self.act_settings)
        file_menu.addSeparator()
        file_menu.addAction(quit_action)

        edit_menu = bar.addMenu("&Edit")
        stack = self.model.undo
        self.act_undo = self._action("Undo", stack.undo, QKeySequence.StandardKey.Undo)
        self.act_redo = self._action("Redo", stack.redo, QKeySequence.StandardKey.Redo)
        stack.indexChanged.connect(self._update_state)
        self.act_edit = self._action(
            "Edit selected tracks…", self.edit_selected, QKeySequence("Ctrl+I")
        )
        self.act_artwork = self._action("Set artwork…", self.choose_artwork)
        self.act_review = self._action(
            "Check Spotify matches…", self.review_matches, QKeySequence("Ctrl+Shift+M")
        )
        self.act_rematch = self._action("Search Spotify again", self.rematch_selected)
        self.act_select_all = self._action(
            "Select all", self.select_all, QKeySequence.StandardKey.SelectAll
        )
        self.act_remove = self._action(
            "Remove from list",
            self.remove_selected,
            [QKeySequence(Qt.Key.Key_Delete), QKeySequence(Qt.Key.Key_Backspace)],
        )
        self.act_remove.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.table.addAction(self.act_remove)
        self.act_clear = self._action("Clear list", self.clear_list)
        edit_menu.addAction(self.act_undo)
        edit_menu.addAction(self.act_redo)
        edit_menu.addSeparator()
        edit_menu.addAction(self.act_edit)
        edit_menu.addAction(self.act_artwork)
        edit_menu.addSeparator()
        edit_menu.addAction(self.act_review)
        edit_menu.addAction(self.act_rematch)
        edit_menu.addSeparator()
        edit_menu.addAction(self.act_select_all)
        edit_menu.addAction(self.act_remove)
        edit_menu.addAction(self.act_clear)

        help_menu = bar.addMenu("&Help")
        help_menu.addAction(self._action("Setup guide", self.show_setup_guide))
        help_menu.addAction(self._action("Listen on your phone", self.show_phone_help))
        help_menu.addSeparator()
        help_menu.addAction(self._action("Show log", lambda: reveal(log_file())))
        help_menu.addAction(
            self._action(f"About {APP_NAME}", lambda: about(self), role=QAction.MenuRole.AboutRole)
        )

    def _running(self) -> bool:
        return self.prepare_cancel is not None

    def _update_state(self, *args: object) -> None:
        count = len(self.model.tracks)
        running = self._running()
        self.stack.setCurrentWidget(self.table if count else self.drop_area)
        has_selection = bool(self.table.selected_rows())
        for action in (self.act_edit, self.act_artwork, self.act_remove):
            action.setEnabled(has_selection and not running)
        self.act_rematch.setEnabled(has_selection and self.connected and not running)
        self.act_clear.setEnabled(count > 0 and not running)
        self.act_select_all.setEnabled(count > 0)
        self.act_prepare.setEnabled(count > 0 or running)
        self.act_prepare.setText(self.bottom.primary_text())
        for action in (self.act_add, self.act_add_folder, self.act_import, self.act_settings,
                       self.act_connect):  # fmt: skip
            action.setEnabled(not running)
        stack = self.model.undo
        self.act_undo.setEnabled(stack.canUndo() and not running)
        self.act_redo.setEnabled(stack.canRedo() and not running)
        self.act_undo.setText(f"Undo {stack.undoText()}".strip())
        self.act_redo.setText(f"Redo {stack.redoText()}".strip())
        self.bottom.set_can_prepare(count > 0)
        self._update_totals()

    def _update_totals(self, *args: object) -> None:
        tracks = self.model.tracks
        count = len(tracks)
        self.act_review.setEnabled(
            not self._running() and any(t.match.state is MatchState.REVIEW for t in tracks)
        )
        if not count:
            self.totals.clear()
            return
        parts = [_plural(count, "song")]
        if self.connected:
            found = sum(t.on_spotify for t in tracks)
            if found:
                parts.append(f"{found} on Spotify")
        total = format_time(self.model.total_duration())
        if total:
            parts.append(total)
        self.totals.setText(" · ".join(parts))

    def _selection_changed(self, selected: QItemSelection, deselected: QItemSelection) -> None:
        self._update_state()

    def set_message(self, text: str, link: str = "", label: str = "") -> None:
        html = _escape(text)
        if link:
            html = f"{html} <a href='{link}'>{_escape(label)}</a>"
        self.message.setText(html)

    def _link(self, target: str) -> None:
        if target == "details":
            self.show_details()
        elif target == "review":
            self.review_matches()
        elif target == "connect":
            self.spotify_button()

    def refresh_connection(self) -> None:
        self.connected = bool(self.settings.spotify_client_id) and self.store.load() is not None
        self.bottom.set_connected(self.connected)
        self.act_connect.setText("Disconnect Spotify" if self.connected else "Connect Spotify…")
        self._update_state()

    def spotify_button(self) -> None:
        if self.connected:
            answer = QMessageBox.question(
                self,
                APP_NAME,
                "Disconnect Spotify? Sleeve will prepare every song as a local file until you "
                "connect again.",
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.store.clear()
                self.match_cancel.set()
                for track in self.model.tracks:
                    if track.match.state is not MatchState.UNCHECKED:
                        self.model.set_match(track.id, Match(MatchState.UNCHECKED))
                self.refresh_connection()
            return
        dialog = ConnectDialog(self.settings, self.store, self)
        dialog.exec()
        self.refresh_connection()
        if self.connected:
            self.set_message("Connected to Spotify. Looking up your songs…")
            self.queue_match([t.id for t in self.model.tracks])

    def _client(self) -> SpotifyClient:
        return SpotifyClient(self.settings.spotify_client_id, self.store, self.transport)

    def queue_match(self, ids: list[int], force: bool = False) -> None:
        if not self.connected or not ids:
            return
        for track_id in ids:
            track = self.model.track(track_id)
            if track is None:
                continue
            if track.match.state is MatchState.KEEP_LOCAL and not force:
                continue
            if track.match.state is not MatchState.SEARCHING:
                self.model.set_match(track_id, Match(MatchState.SEARCHING))
            self.match_queue.add(track_id)
        self.match_timer.start()

    def _tags_edited(self, ids: list[int]) -> None:
        self.queue_match(ids)

    def _start_matching(self) -> None:
        if self.matching or not self.match_queue or not self.connected:
            return
        items = []
        for track_id in sorted(self.match_queue, key=self.model.row_of):
            track = self.model.track(track_id)
            if track is not None and track.match.state in _PENDING:
                items.append((track_id, track.wanted()))
                self.model.set_match(track_id, Match(MatchState.SEARCHING))
        self.match_queue.clear()
        if not items:
            return
        self.matching = True
        self.match_done = 0
        self.match_total = len(items)
        self.match_cancel = threading.Event()
        client = self._client()
        client.cancel = self.match_cancel
        worker = MatchWorker(items, Matcher(client, self.search_cache), self.match_cancel)
        client.on_wait = worker.signals.waiting.emit
        worker.signals.matched.connect(self._matched)
        worker.signals.waiting.connect(self._match_waiting)
        worker.signals.failed.connect(self._match_failed)
        worker.signals.finished.connect(self._match_finished)
        self.set_message(f"Searching Spotify… 0 of {self.match_total}")
        self.pool.start(worker)

    def _matched(self, track_id: int, match: Match) -> None:
        self.match_done += 1
        self.model.set_match(track_id, match)
        if self.match_done % 5 == 0 or self.match_done == self.match_total:
            self.set_message(f"Searching Spotify… {self.match_done} of {self.match_total}")

    def _match_waiting(self, seconds: float) -> None:
        self.set_message(f"Spotify asked Sleeve to slow down. Continuing in {round(seconds)} s…")

    def _match_failed(self, message: str) -> None:
        for track in self.model.tracks:
            if track.match.state is MatchState.SEARCHING:
                self.model.set_match(track.id, Match(MatchState.UNCHECKED))
        self.refresh_connection()
        self.set_message(message)

    def _match_finished(self) -> None:
        self.matching = False
        for track in self.model.tracks:
            if track.match.state is MatchState.SEARCHING:
                self.model.set_match(track.id, Match(MatchState.UNCHECKED))
        if self.match_queue and not self.match_cancel.is_set():
            self._start_matching()
            return
        if self.match_cancel.is_set():
            if self.prepare_after_match:
                self.prepare_after_match = False
                self._end_run()
            return
        self._match_summary()
        if self.prepare_after_match:
            self.prepare_after_match = False
            self._end_run()
            self.prepare()

    def _match_summary(self) -> None:
        tracks = self.model.tracks
        found = sum(t.match.state is MatchState.FOUND for t in tracks)
        review = sum(t.match.state is MatchState.REVIEW for t in tracks)
        local = sum(
            not t.on_spotify and t.has_file and t.match.state is not MatchState.REVIEW
            for t in tracks
        )
        missing = sum(
            not t.on_spotify and not t.has_file and t.match.state is not MatchState.REVIEW
            for t in tracks
        )
        parts = [f"{found} on Spotify."]
        if local:
            parts.append(f"{_plural(local, 'song')} will be added as local files.")
        if missing:
            parts.append(f"{missing} not found.")
        text = " ".join(parts)
        if review:
            self.set_message(
                f"{text} {review} need{'s' if review == 1 else ''} a quick check —",
                "review",
                "check now",
            )
        else:
            self.set_message(text)

    def rematch_selected(self) -> None:
        self.queue_match(self.table.selected_ids(), force=True)

    def _double_clicked(self, index: QModelIndex) -> None:
        if index.column() != Column.STATUS:
            return
        track = self.model.tracks[index.row()]
        if track.match.candidates and not self._running():
            self.review_matches([track])

    def review_matches(self, tracks: list[Track] | None = None) -> bool:
        tracks = tracks or [t for t in self.model.tracks if t.match.state is MatchState.REVIEW]
        if not tracks:
            return True
        dialog = ReviewDialog(tracks, self)
        if dialog.exec() != ReviewDialog.DialogCode.Accepted:
            return False
        for track_id, match in dialog.decisions().items():
            self.model.set_match(track_id, match)
        self._match_summary()
        return True

    def add_paths(self, paths: list[Path]) -> None:
        if not paths or self._running():
            return
        playlists = [p for p in paths if p.is_file() and p.suffix.lower() in PLAYLIST_EXTENSIONS]
        if playlists:
            self.import_playlist(playlists[0])
            paths = [p for p in paths if p not in playlists]
            if not paths:
                return
        if not self.bottom.playlist_name() and len(paths) == 1 and paths[0].is_dir():
            self.bottom.playlist.setEditText(paths[0].name)
        self.scanning += 1
        self.set_message("Reading files…")
        worker = ScanWorker(paths, self.scan_cancel)
        worker.signals.found.connect(self._found)
        worker.signals.finished.connect(self._scan_finished)
        self.pool.start(worker)

    def _found(self, tracks: list[Track]) -> None:
        fresh = self.model.add(tracks)
        self.queue_match([t.id for t in fresh])

    def _scan_finished(self, added: int, rejected: int) -> None:
        self.scanning -= 1
        parts = []
        if added:
            parts.append(f"Added {_plural(added, 'song')}.")
        if rejected:
            parts.append(f"{_plural(rejected, 'file')} skipped (not audio or unreadable).")
        if not added and not rejected:
            parts.append("No audio files found there.")
        orphans = sum(not t.has_file for t in self.model.tracks)
        if orphans and not self.connected:
            self.set_message(
                f"{' '.join(parts)} {_plural(orphans, 'song')} {'has' if orphans == 1 else 'have'} "
                "no file on this computer \u2014",
                "connect",
                "connect Spotify to add them from there",
            )
        elif not self.matching and not self.match_queue:
            self.set_message(" ".join(parts))
        self._update_state()

    def add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add files", "", f"{_AUDIO_FILTER};;{_PLAYLIST_FILTER};;All files (*)"
        )
        self.add_paths([Path(f) for f in files])

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add folder")
        if folder:
            self.add_paths([Path(folder)])

    def choose_playlist(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import playlist",
            "",
            f"{_PLAYLIST_FILTER};;All files (*)",
        )
        if path:
            self.import_playlist(Path(path))

    def import_playlist(self, path: Path) -> None:
        self.set_message(f"Reading '{path.name}'…")

        def describe(exc: Exception) -> str:
            return exc.message if isinstance(exc, LibraryError) else f"Couldn't read '{path.name}'."

        worker = TaskWorker(lambda: read_playlists(path), describe)
        self._import_path = path
        worker.signals.done.connect(self._playlists_read)
        worker.signals.failed.connect(self.set_message)
        self.pool.start(worker)

    def _playlists_read(self, playlists: object) -> None:
        path = self._import_path
        if not isinstance(playlists, list) or not playlists:
            self.set_message(f"'{path.name}' doesn't list any songs.")
            return
        chosen: Playlist | None = playlists[0]
        if len(playlists) > 1:
            picker = PlaylistPicker(path.name, playlists, self)
            if picker.exec() != PlaylistPicker.DialogCode.Accepted:
                self.set_message("")
                return
            chosen = picker.chosen()
        if chosen is None:
            return
        if not self.bottom.playlist_name() or not self.model.tracks:
            self.bottom.playlist.setEditText(chosen.name)
        self.scanning += 1
        self.set_message(f"Adding '{chosen.name}'…")
        worker = ImportWorker(list(chosen.entries), self.scan_cancel)
        worker.signals.found.connect(self._found)
        worker.signals.finished.connect(self._scan_finished)
        self.pool.start(worker)

    def remove_selected(self) -> None:
        if self._running():
            return
        rows = self.table.selected_rows()
        if rows:
            self.model.remove_rows(rows)
            self.set_message(f"Removed {_plural(len(rows), 'song')}. Undo brings them back.")

    def clear_list(self) -> None:
        if not self._running():
            self.model.remove_rows(list(range(len(self.model.tracks))))

    def select_all(self) -> None:
        self.table.setFocus()
        self.table.selectAll()

    def _target_ids(self) -> list[int]:
        return self.table.selected_ids() or [t.id for t in self.model.tracks]

    def edit_selected(self) -> None:
        ids = self._target_ids()
        tracks = [t for t in self.model.tracks if t.id in ids]
        if not tracks or self._running():
            return
        dialog = BulkEditDialog(tracks, self)
        if dialog.exec() != BulkEditDialog.DialogCode.Accepted:
            return
        changes = dialog.changes()
        undo = self.model.undo
        undo.beginMacro(f"Edit {_plural(len(tracks), 'Track')}")
        if changes:
            self.model.set_fields(ids, changes, "Edit Tracks")
        if dialog.number.isChecked():
            for position, track in enumerate(tracks, start=1):
                self.model.set_fields([track.id], {"track": position}, "Number Tracks")
        undo.endMacro()

    def choose_artwork(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose artwork",
            "",
            "Images (*.jpg *.jpeg *.png *.webp *.heic *.bmp *.tif *.tiff)",
        )
        if path:
            self.apply_artwork(Path(path))

    def apply_artwork(self, path: Path) -> None:
        ids = self.table.selected_ids()
        if not ids:
            return
        art = load_artwork(path)
        if art is None:
            self.set_message(f"Couldn't read '{path.name}' as an image. Try a JPEG or PNG.")
            return
        self.model.set_fields(ids, {"artwork": art}, "Set Artwork")
        self.set_message(f"Set artwork on {_plural(len(ids), 'song')}.")

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime = event.mimeData()
        if not self._running() and any(url.isLocalFile() for url in mime.urls()):
            event.acceptProposedAction()
            self.drop_area.set_highlighted(True)
            return
        event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self.drop_area.set_highlighted(False)

    def dropEvent(self, event: QDropEvent) -> None:
        self.drop_area.set_highlighted(False)
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        if not paths:
            return
        event.acceptProposedAction()
        if all(p.is_file() and is_image(p) for p in paths) and self.table.selected_ids():
            self.apply_artwork(paths[0])
            return
        self.add_paths(paths)

    def _begin_run(self) -> threading.Event:
        self.prepare_cancel = threading.Event()
        self.model.locked = True
        self.bottom.set_running(True)
        self._update_state()
        return self.prepare_cancel

    def _end_run(self) -> None:
        self.prepare_cancel = None
        self.model.locked = False
        self.bottom.set_running(False)
        self.bottom.apply_settings(self.settings)
        self._update_state()

    def prepare(self) -> None:
        if self._running():
            self.cancel()
            return
        tracks = self.model.tracks
        if not tracks:
            return
        if self.scanning:
            self.set_message("Still reading files. Try again in a moment.")
            return
        playlist = self.bottom.playlist_name()
        if not playlist:
            self.set_message("Type a playlist name first.")
            self.bottom.focus_playlist()
            return
        if self.connected:
            pending = [t.id for t in tracks if t.match.state in _PENDING]
            if pending or self.matching:
                self.queue_match([i for i in pending if i not in self.match_queue])
                self.prepare_after_match = True
                self._begin_run()
                self.set_message("Finishing the Spotify search first…")
                return
            if (
                any(t.match.state is MatchState.REVIEW for t in tracks)
                and not self._resolve_reviews()
            ):
                return
        self._run(playlist)

    def _resolve_reviews(self) -> bool:
        review = [t for t in self.model.tracks if t.match.state is MatchState.REVIEW]
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(
            f"{_plural(len(review), 'song')} might be on Spotify, but the match isn't certain."
        )
        box.setInformativeText(
            "Check it now, or skip the Spotify version."
            if len(review) == 1
            else "Check them now, or skip the Spotify versions."
        )
        check = box.addButton("Check…", QMessageBox.ButtonRole.AcceptRole)
        own = box.addButton("Skip", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(check)
        box.exec()
        if box.clickedButton() is check:
            return self.review_matches(review)
        if box.clickedButton() is own:
            for track in review:
                self.model.set_match(track.id, Match(MatchState.KEEP_LOCAL, track.match.candidates))
            return True
        return False

    def _run(self, playlist: str) -> None:
        tracks = self.model.tracks
        folder = Path(self.settings.output_root) / self.bottom.folder_name()
        self.settings.format = self.bottom.chosen_format()
        self.settings.remember_playlist(playlist)
        self.settings.save()
        on_spotify = [t for t in tracks if self.connected and t.on_spotify]
        local = [t for t in tracks if t not in on_spotify and t.has_file]
        missing = [t for t in tracks if t not in on_spotify and not t.has_file]
        self.model.reset_status()
        for track in missing:
            self.model.set_status(
                track.id,
                "Not found",
                "Not on Spotify, and the file isn't on this computer.",
                problem=True,
            )
        self.report = RunReport(playlist, folder, missing=len(missing), connected=self.connected)
        self.last_folder = folder
        cancel = self._begin_run()
        self.match_cancel.set()
        if not local:
            self._run_uris = [t.match.uri for t in on_spotify if t.match.uri]
            self._local_done(None)
            return
        encoding = (
            Encoding.mp3(self.settings.mp3_bitrate)
            if self.settings.format == "mp3"
            else Encoding.aac(self.settings.aac_bitrate)
        )
        jobs = [Job(t.id, t.source, t.tags.copy(), t.detected) for t in local]
        workers = max(1, min(4, (os.cpu_count() or 2) // 2))
        options = Options(folder, encoding, self.settings.pattern, workers)
        self.set_message(f"Preparing {_plural(len(jobs), 'file')}…")
        worker = PrepareWorker(jobs, options, self.ffmpeg, cancel)
        uris = [t.match.uri for t in on_spotify if t.match.uri]
        worker.signals.progress.connect(self.model.mark_working)
        worker.signals.result.connect(self.model.mark_result)
        self._run_uris = uris
        worker.signals.finished.connect(self._local_done)
        worker.signals.failed.connect(self._prepare_failed)
        self.pool.start(worker)

    def cancel(self) -> None:
        if self.prepare_after_match:
            self.match_cancel.set()
        if self.prepare_cancel is not None:
            self.prepare_cancel.set()
            self.set_message("Stopping…")

    def _prepare_failed(self, message: str) -> None:
        self._end_run()
        self.set_message(message)
        QMessageBox.warning(self, APP_NAME, message)

    def _local_done(self, summary: Summary | None) -> None:
        uris = self._run_uris
        assert self.report is not None
        cancel = self.prepare_cancel
        self.report.summary = summary
        self.last_summary = summary
        if summary is not None and summary.fatal:
            self._end_run()
            self.set_message(summary.fatal)
            QMessageBox.warning(self, APP_NAME, summary.fatal)
            return
        if cancel is not None and cancel.is_set():
            self._end_run()
            self.set_message("Stopped.")
            return
        if not self.connected:
            self._finish()
            return
        self.set_message("Adding songs to your Spotify playlist…")
        client = self._client()
        if cancel is not None:
            client.cancel = cancel
        playlist = self.report.playlist

        def work() -> _SpotifyResult:
            target = client.ensure_playlist(playlist)
            added = client.add_tracks(target.id, uris) if uris else []
            return _SpotifyResult(target.uri, added)

        def describe(exc: Exception) -> str:
            return exc.message if isinstance(exc, SpotifyError) else "Couldn't reach Spotify."

        worker = TaskWorker(work, describe)
        worker.signals.done.connect(self._spotify_done)
        worker.signals.failed.connect(self._spotify_failed)
        self.pool.start(worker)

    def _spotify_done(self, result: object) -> None:
        uris = self._run_uris
        assert self.report is not None and isinstance(result, _SpotifyResult)
        added = set(result.added)
        self.report.playlist_uri = result.uri
        self.report.added = len(added)
        self.report.already = len(uris) - len(added)
        for track in self.model.tracks:
            uri = track.match.uri
            if uri and uri in uris:
                text = "Added" if uri in added else "Already in playlist"
                self.model.set_status(track.id, text)
        self._finish()

    def _spotify_failed(self, message: str) -> None:
        uris = self._run_uris
        assert self.report is not None
        self.report.spotify_error = message
        for track in self.model.tracks:
            if track.match.uri and track.match.uri in uris:
                self.model.set_status(track.id, "Not added", message, problem=True)
        self.refresh_connection()
        self._finish()

    def _finish(self) -> None:
        report = self.report
        assert report is not None
        self._end_run()
        parts = []
        if report.added:
            parts.append(f"Added {_plural(report.added, 'song')} on Spotify.")
        if report.local_ready:
            parts.append(f"{_plural(report.local_ready, 'file')} ready for Local Files.")
        if report.summary is not None:
            problems = report.summary.problems()
            if problems:
                parts.append(problems.capitalize() + ".")
        if report.missing:
            parts.append(f"{report.missing} not found.")
        if report.spotify_error:
            parts.append("Spotify didn't accept the changes.")
        text = " ".join(parts) or "Nothing new to add."
        has_details = bool(report.summary and report.summary.problems())
        self.set_message(text, "details" if has_details else "", "Details")
        dialog = FinishDialog(report, self)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.finished.connect(self._finish_closed)
        self._dialog = dialog
        dialog.open()

    def _finish_closed(self) -> None:
        dialog = self._dialog
        if isinstance(dialog, FinishDialog) and dialog.start_over:
            self.clear_list()
            self.bottom.playlist.setEditText("")
            self.set_message("Ready for the next playlist. Undo brings the last list back.")
            self.bottom.focus_playlist()

    def show_details(self, *args: object) -> None:
        if self.last_summary is None:
            return
        tracks = {t.id: t for t in self.model.tracks}
        DetailsDialog(self.last_summary, tracks, self).exec()

    def show_output(self) -> None:
        root = Path(self.settings.output_root)
        target = self.last_folder if self.last_folder and self.last_folder.is_dir() else root
        if not create_folder(target):
            self.set_message(f"Can't open '{target}'. Choose another output folder in Settings.")
            return
        reveal(target)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self.store, self)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted:
            dialog.apply()
            self.bottom.apply_settings(self.settings)
            if not create_folder(Path(self.settings.output_root)):
                self.set_message(
                    f"Couldn't create '{self.settings.output_root}'. Create that folder "
                    "yourself, or choose another one in Settings."
                )
        self.refresh_connection()

    def show_setup_guide(self) -> None:
        dialog = SetupGuide(Path(self.settings.output_root), self)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self._dialog = dialog
        dialog.open()

    def show_phone_help(self) -> None:
        PhoneHelp(self).exec()

    def first_run(self) -> None:
        create_folder(Path(self.settings.output_root))
        if not self.settings.setup_done:
            self.settings.setup_done = True
            self.settings.save()
            QTimer.singleShot(250, self.show_setup_guide)
        if self.ffmpeg is None:
            self.set_message(
                "The audio converter (ffmpeg) is missing. MP3 and M4A files can "
                "still be copied; reinstall Sleeve to convert other formats."
            )
        elif not self.connected:
            self.set_message(
                "Tip: connect Spotify so songs it already has go straight into your playlist.",
                "connect",
                "Connect…",
            )

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._running():
            answer = QMessageBox.question(
                self,
                APP_NAME,
                "Songs are still being prepared. Stop and quit?",
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.cancel()
        self.scan_cancel.set()
        self.match_cancel.set()
        self.pool.waitForDone(10_000)
        self.search_cache.save()
        event.accept()


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
