from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
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

from sleeve import APP_NAME, __version__
from sleeve.core.pipeline import Outcome, Summary
from sleeve.core.tags import Artwork
from sleeve.ui.artwork import load_artwork
from sleeve.ui.platform import FILE_MANAGER, open_spotify, reveal
from sleeve.ui.track_model import Track

_KEEP = "Mixed — leave unchanged"


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    font = label.font()
    font.setPointSizeF(font.pointSizeF() * 1.15)
    font.setBold(True)
    label.setFont(font)
    return label


def _steps(lines: list[str]) -> QLabel:
    items = "".join(f"<li style='margin-bottom:6px'>{line}</li>" for line in lines)
    label = QLabel(f"<ol style='margin-left:-20px'>{items}</ol>")
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.RichText)
    return label


def _path_row(path: Path, parent: QWidget) -> QWidget:
    row = QWidget(parent)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    field = QLineEdit(str(path))
    field.setReadOnly(True)
    field.setCursorPosition(0)
    copy = QPushButton("Copy folder path")

    def do_copy() -> None:
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(str(path))
        copy.setText("Copied")

    copy.clicked.connect(do_copy)
    layout.addWidget(field, 1)
    layout.addWidget(copy)
    return row


def _spotify_button(parent: QWidget, uri: str = "spotify:") -> QPushButton:
    button = QPushButton("Open Spotify", parent)

    def go() -> None:
        if not open_spotify(uri):
            button.setToolTip("Couldn't open Spotify. Is the desktop app installed?")
            button.setText("Spotify not found")
            button.setEnabled(False)

    button.clicked.connect(go)
    return button


class SetupGuide(QDialog):
    def __init__(self, root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set up Spotify")
        layout = QVBoxLayout(self)
        layout.addWidget(_heading("Set up Spotify once"))
        intro = QLabel(f"{APP_NAME} saves your tracks in this folder:")
        layout.addWidget(intro)
        layout.addWidget(_path_row(root, self))
        layout.addWidget(
            _steps(
                [
                    "Open Spotify on this computer and go to <b>Settings</b>.",
                    "Turn on <b>Show Local Files</b>.",
                    "Under <b>Show songs from</b>, click <b>Add a source</b> and choose the "
                    "folder above.",
                ]
            )
        )
        note = QLabel(
            "You only need to do this once. Every playlist you prepare is saved inside this "
            "folder, and its tracks appear in <b>Your Library › Local Files</b>."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.addButton(_spotify_button(self), QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setMinimumWidth(520)


class PhoneHelp(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Listen on your phone")
        layout = QVBoxLayout(self)
        layout.addWidget(_heading("Listen on your phone"))
        need = QLabel(
            "Playing local files on a phone needs <b>Spotify Premium</b>, and the phone "
            "and computer must be on the <b>same Wi-Fi network</b>."
        )
        need.setWordWrap(True)
        layout.addWidget(need)
        layout.addWidget(
            _steps(
                [
                    "On the computer, add the tracks to a playlist and keep Spotify open.",
                    "On the phone, open Spotify Settings and turn on <b>Local audio files</b>. "
                    "On iPhone, allow <b>Local Network</b> access if asked.",
                    "Open the playlist on the phone and tap <b>Download</b>. "
                    "The tracks copy over Wi-Fi from your computer.",
                ]
            )
        )
        tip = QLabel(
            "If tracks stay grayed out, check that both devices use the same Spotify account "
            "and the same network (not a guest network), then restart Spotify on both."
        )
        tip.setWordWrap(True)
        layout.addWidget(tip)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setMinimumWidth(480)


class FinishDialog(QDialog):
    def __init__(
        self,
        summary: Summary,
        folder: Path,
        playlist: str,
        playlist_uri: str | None,
        spotify_note: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ready for Spotify")
        layout = QVBoxLayout(self)
        ready = summary.ready
        headline = (
            f"{ready} track{'s are' if ready != 1 else ' is'} ready"
            if ready
            else "No new tracks to add"
        )
        layout.addWidget(_heading(headline))
        if problems := summary.problems():
            detail = QLabel(f"{problems.capitalize()}. See details in the main window.")
            detail.setWordWrap(True)
            layout.addWidget(detail)
        layout.addWidget(_path_row(folder, self))
        drag = f"Select the new tracks and drag them into <b>{_escape(playlist)}</b>."
        if playlist_uri:
            drag = (
                f"Select the new tracks and drag them into <b>{_escape(playlist)}</b>, "
                "which is now in your Spotify library."
            )
        layout.addWidget(
            _steps(
                [
                    "First time only: in Spotify <b>Settings</b>, turn on <b>Show Local "
                    f"Files</b> and add the <b>{APP_NAME}</b> folder as a source "
                    "(Help › Setup guide).",
                    "In Spotify, open <b>Your Library › Local Files</b>.",
                    drag,
                ]
            )
        )
        if spotify_note:
            note = QLabel(spotify_note)
            note.setWordWrap(True)
            layout.addWidget(note)
        buttons = QDialogButtonBox()
        show = QPushButton("Show files")
        show.setToolTip(f"Show the folder in {FILE_MANAGER}")
        show.clicked.connect(lambda: reveal(folder))
        buttons.addButton(show, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(
            _spotify_button(self, playlist_uri or "spotify:"),
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        done = buttons.addButton("Done", QDialogButtonBox.ButtonRole.AcceptRole)
        done.setDefault(True)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        self.setMinimumWidth(540)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class DetailsDialog(QDialog):
    def __init__(
        self, summary: Summary, tracks: dict[int, Track], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Details")
        layout = QVBoxLayout(self)
        tree = QTreeWidget()
        tree.setRootIsDecorated(False)
        tree.setHeaderLabels(["File", "What happened"])
        tree.setWordWrap(True)
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        tree.header().setStretchLastSection(True)
        if summary.fatal:
            QTreeWidgetItem(tree, ["", summary.fatal])
        for result in summary.results:
            if result.outcome in (Outcome.READY, Outcome.UPDATED) and not result.message:
                continue
            track = tracks.get(result.key)
            name = track.source.name if track else ""
            QTreeWidgetItem(tree, [name, result.message or result.outcome.value.capitalize()])
        layout.addWidget(tree)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(640, 320)


class BulkEditDialog(QDialog):
    FIELDS = (("artist", "Artist"), ("album", "Album"), ("album_artist", "Album artist"),
              ("year", "Year"), ("genre", "Genre"))  # fmt: skip

    def __init__(self, tracks: list[Track], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        count = len(tracks)
        self.setWindowTitle(f"Edit {count} track{'s' if count != 1 else ''}")
        self.tracks = tracks
        self.edits: dict[str, QLineEdit] = {}
        self.artwork: Artwork | None = None
        self.artwork_changed = False
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for name, label in self.FIELDS:
            values = {getattr(t.tags, name) for t in tracks}
            edit = QLineEdit()
            if len(values) == 1:
                edit.setText(values.pop())
            else:
                edit.setPlaceholderText(_KEEP)
            self.edits[name] = edit
            form.addRow(f"{label}:", edit)

        self.preview = QLabel()
        self.preview.setFixedSize(72, 72)
        self.preview.setFrameShape(QLabel.Shape.StyledPanel)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arts = {t.tags.artwork.digest if t.tags.artwork else None for t in tracks}
        if len(arts) == 1 and tracks[0].tags.artwork:
            self._show(tracks[0].tags.artwork)
        elif len(arts) > 1:
            self.preview.setText("Mixed")
        choose = QPushButton("Choose…")
        choose.clicked.connect(self._choose)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove)
        art_row = QHBoxLayout()
        art_row.addWidget(self.preview)
        art_buttons = QVBoxLayout()
        art_buttons.addWidget(choose)
        art_buttons.addWidget(remove)
        art_buttons.addStretch(1)
        art_row.addLayout(art_buttons)
        art_row.addStretch(1)
        form.addRow("Artwork:", art_row)

        self.number = QCheckBox("Number tracks in list order")
        form.addRow("", self.number)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.setMinimumWidth(420)

    def _show(self, art: Artwork | None) -> None:
        if art is None:
            self.preview.clear()
            return
        pixmap = QPixmap()
        pixmap.loadFromData(art.data)
        self.preview.setPixmap(
            pixmap.scaled(
                70,
                70,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _choose(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose artwork",
            "",
            "Images (*.jpg *.jpeg *.png *.webp *.heic *.bmp *.tif *.tiff)",
        )
        if not path:
            return
        art = load_artwork(Path(path))
        if art is None:
            self.preview.setText("Can't read")
            return
        self.artwork, self.artwork_changed = art, True
        self._show(art)

    def _remove(self) -> None:
        self.artwork, self.artwork_changed = None, True
        self.preview.clear()

    def changes(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, edit in self.edits.items():
            if edit.text().strip() or not edit.placeholderText():
                out[name] = edit.text().strip()
        if self.artwork_changed:
            out["artwork"] = self.artwork
        return out


def about(parent: QWidget) -> None:
    from PySide6.QtWidgets import QMessageBox

    QMessageBox.about(
        parent,
        f"About {APP_NAME}",
        f"<b>{APP_NAME}</b> {__version__}<p>Gets your own audio files ready for Spotify "
        "Local Files.</p><p>Uses FFmpeg (ffmpeg.org), Qt for Python and Mutagen. "
        "See THIRD_PARTY.md for licenses.</p>",
    )
