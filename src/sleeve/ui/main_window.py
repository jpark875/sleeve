import logging
import os
import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import QItemSelection, QSize, Qt, QThreadPool, QTimer
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
from sleeve.core.formats import AUDIO_EXTENSIONS
from sleeve.core.pipeline import Job, Options, Summary
from sleeve.log import log_file
from sleeve.paths import create_folder
from sleeve.settings import Settings
from sleeve.spotify.api import Playlist, SpotifyClient, SpotifyError
from sleeve.spotify.auth import TokenStore
from sleeve.ui.artwork import is_image, load_artwork
from sleeve.ui.bottom_bar import BottomBar
from sleeve.ui.dialogs import (
    BulkEditDialog,
    DetailsDialog,
    FinishDialog,
    PhoneHelp,
    SetupGuide,
    about,
)
from sleeve.ui.drop_area import DropArea
from sleeve.ui.platform import FILE_MANAGER, reveal
from sleeve.ui.settings_dialog import SettingsDialog
from sleeve.ui.track_model import TrackModel, format_time
from sleeve.ui.track_table import TrackTable
from sleeve.ui.workers import PrepareWorker, ScanWorker, TaskWorker

log = logging.getLogger(__name__)

_AUDIO_FILTER = "Audio files ({})".format(" ".join(f"*{e}" for e in sorted(AUDIO_EXTENSIONS)))


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


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
        self.scanning = 0
        self.last_summary: Summary | None = None
        self.last_folder: Path | None = None
        self._dialog: Any = None

        self.setWindowTitle(APP_NAME)
        self.setAcceptDrops(True)
        self.setMinimumSize(QSize(680, 400))
        self.resize(980, 600)

        self.model = TrackModel(self)
        self.table = TrackTable(self.model)
        self.drop_area = DropArea()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.drop_area)
        self.stack.addWidget(self.table)

        self.bottom = BottomBar(settings)
        self.bottom.add_clicked.connect(self.add_files)
        self.bottom.prepare_clicked.connect(self.prepare)
        self.bottom.cancel_clicked.connect(self.cancel)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.stack, 1)
        layout.addWidget(self.bottom)
        self.setCentralWidget(central)

        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.RichText)
        self.message.linkActivated.connect(self.show_details)
        self.message.setContentsMargins(8, 0, 0, 2)
        self.totals = QLabel()
        self.totals.setContentsMargins(0, 0, 8, 2)
        self.statusBar().addWidget(self.message, 1)
        self.statusBar().addPermanentWidget(self.totals)
        self.statusBar().setSizeGripEnabled(False)

        self._build_menus()
        self.model.rowsInserted.connect(self._update_state)
        self.model.rowsRemoved.connect(self._update_state)
        self.model.modelReset.connect(self._update_state)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self._update_state()

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
        self.act_prepare = self._action(
            "Prepare for Spotify", self.prepare, QKeySequence("Ctrl+Return")
        )
        self.act_show = self._action(
            f"Show output folder in {FILE_MANAGER}", self.show_output, QKeySequence("Ctrl+Shift+R")
        )
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
        for action in (self.act_add, self.act_add_folder):
            file_menu.addAction(action)
        file_menu.addSeparator()
        file_menu.addAction(self.act_prepare)
        file_menu.addAction(self.act_show)
        file_menu.addSeparator()
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
        self.act_clear.setEnabled(count > 0 and not running)
        self.act_select_all.setEnabled(count > 0)
        self.act_prepare.setEnabled(count > 0 or running)
        self.act_add.setEnabled(not running)
        self.act_add_folder.setEnabled(not running)
        self.act_settings.setEnabled(not running)
        stack = self.model.undo
        self.act_undo.setEnabled(stack.canUndo() and not running)
        self.act_redo.setEnabled(stack.canRedo() and not running)
        self.act_undo.setText(f"Undo {stack.undoText()}".strip())
        self.act_redo.setText(f"Redo {stack.redoText()}".strip())
        self.bottom.set_can_prepare(count > 0)
        if count:
            total = format_time(self.model.total_duration())
            self.totals.setText(
                f"{_plural(count, 'track')}, {total}" if total else _plural(count, "track")
            )
        else:
            self.totals.clear()

    def _selection_changed(self, selected: QItemSelection, deselected: QItemSelection) -> None:
        self._update_state()

    def set_message(self, text: str, details: bool = False) -> None:
        if details:
            plain = text.replace(" — see details.", "")
            text = f"{_escape(plain)} — <a href='details'>see details</a>."
        else:
            text = _escape(text)
        self.message.setText(text)

    def add_paths(self, paths: list[Path]) -> None:
        if not paths or self._running():
            return
        self.scanning += 1
        self.set_message("Reading files…")
        worker = ScanWorker(paths, self.scan_cancel)
        worker.signals.found.connect(self._found)
        worker.signals.finished.connect(self._scan_finished)
        self.pool.start(worker)

    def _found(self, tracks: list[Any]) -> None:
        self.model.add(tracks)

    def _scan_finished(self, added: int, rejected: int) -> None:
        self.scanning -= 1
        parts = []
        if added:
            parts.append(f"Added {_plural(added, 'track')}.")
        if rejected:
            parts.append(f"{_plural(rejected, 'file')} skipped (not audio or unreadable).")
        if not added and not rejected:
            parts.append("No audio files found there.")
        self.set_message(" ".join(parts))
        self._update_state()

    def add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add files", "", f"{_AUDIO_FILTER};;All files (*)"
        )
        self.add_paths([Path(f) for f in files])

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add folder")
        if folder:
            self.add_paths([Path(folder)])

    def remove_selected(self) -> None:
        if self._running():
            return
        rows = self.table.selected_rows()
        if rows:
            self.model.remove_rows(rows)
            self.set_message(f"Removed {_plural(len(rows), 'track')}. Undo brings them back.")

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
        self.set_message(f"Set artwork on {_plural(len(ids), 'track')}.")

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

    def prepare(self) -> None:
        if self._running():
            self.cancel()
            return
        if not self.model.tracks:
            return
        if self.scanning:
            self.set_message("Still reading files. Try again in a moment.")
            return
        playlist = self.bottom.playlist_name()
        if not playlist:
            self.set_message("Type a playlist name first.")
            self.bottom.focus_playlist()
            return
        folder = Path(self.settings.output_root) / self.bottom.folder_name()
        self.settings.format = self.bottom.chosen_format()
        self.settings.remember_playlist(playlist)
        self.settings.save()
        encoding = (
            Encoding.mp3(self.settings.mp3_bitrate)
            if self.settings.format == "mp3"
            else Encoding.aac(self.settings.aac_bitrate)
        )
        jobs = [Job(t.id, t.source, t.tags.copy(), t.detected) for t in self.model.tracks]
        workers = max(1, min(4, (os.cpu_count() or 2) // 2))
        options = Options(folder, encoding, self.settings.pattern, workers)
        self.prepare_cancel = threading.Event()
        self.last_folder = folder
        self.model.reset_status()
        self.model.locked = True
        self.bottom.set_running(True)
        self._update_state()
        self.set_message(f"Preparing {_plural(len(jobs), 'track')}…")
        worker = PrepareWorker(jobs, options, self.ffmpeg, self.prepare_cancel)
        worker.signals.progress.connect(self.model.mark_working)
        worker.signals.result.connect(self.model.mark_result)
        worker.signals.finished.connect(self._prepared)
        worker.signals.failed.connect(self._prepare_failed)
        self.pool.start(worker)

    def cancel(self) -> None:
        if self.prepare_cancel is not None:
            self.prepare_cancel.set()
            self.set_message("Stopping…")

    def _finish_run(self) -> None:
        self.prepare_cancel = None
        self.model.locked = False
        self.bottom.set_running(False)
        self.bottom.apply_settings(self.settings)
        self._update_state()

    def _prepare_failed(self, message: str) -> None:
        self._finish_run()
        self.set_message(message)
        QMessageBox.warning(self, APP_NAME, message)

    def _prepared(self, summary: Summary) -> None:
        cancelled = self.prepare_cancel is not None and self.prepare_cancel.is_set()
        self._finish_run()
        self.last_summary = summary
        needs_details = summary.ready != len(summary.results) or bool(summary.fatal)
        self.set_message(summary.text(), details=needs_details)
        if summary.fatal:
            QMessageBox.warning(self, APP_NAME, summary.fatal)
            return
        if cancelled or not summary.results:
            return
        playlist = self.bottom.playlist_name()
        if self.store.load() is not None and self.settings.spotify_client_id:
            self._create_playlist(summary, playlist)
        else:
            self._show_finish(summary, playlist, None, "")

    def _create_playlist(self, summary: Summary, playlist: str) -> None:
        client = SpotifyClient(self.settings.spotify_client_id, self.store)
        self.set_message("Creating the playlist in Spotify…")

        def describe(exc: Exception) -> str:
            return exc.message if isinstance(exc, SpotifyError) else "Couldn't reach Spotify."

        worker = TaskWorker(lambda: client.ensure_playlist(playlist), describe)

        def done(result: object) -> None:
            uri = result.uri if isinstance(result, Playlist) else None
            self.set_message(summary.text(), details=summary.ready != len(summary.results))
            self._show_finish(summary, playlist, uri, "")

        def failed(message: str) -> None:
            self.set_message(summary.text(), details=summary.ready != len(summary.results))
            self._show_finish(summary, playlist, None, f"Spotify: {message}")

        worker.signals.done.connect(done)
        worker.signals.failed.connect(failed)
        self.pool.start(worker)

    def _show_finish(self, summary: Summary, playlist: str, uri: str | None, note: str) -> None:
        assert self.last_folder is not None
        dialog = FinishDialog(summary, self.last_folder, playlist, uri, note, self)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self._dialog = dialog
        dialog.open()

    def show_details(self, *args: object) -> None:
        if self.last_summary is None:
            return
        tracks = {t.id: t for t in self.model.tracks}
        DetailsDialog(self.last_summary, tracks, self).exec()

    def show_output(self) -> None:
        root = Path(self.settings.output_root)
        target = self.last_folder if self.last_folder and self.last_folder.is_dir() else root
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
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

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._running():
            answer = QMessageBox.question(
                self,
                APP_NAME,
                "Tracks are still being prepared. Stop and quit?",
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.cancel()
        self.scan_cancel.set()
        self.pool.waitForDone(10_000)
        event.accept()


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
