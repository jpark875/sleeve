import itertools
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QPersistentModelIndex, Qt
from PySide6.QtGui import QImage, QPixmap, QUndoCommand, QUndoStack

from sleeve.core.formats import Action, Detected, Kind
from sleeve.core.pipeline import Outcome, Result
from sleeve.core.tags import Artwork, Tags

_ids = itertools.count(1)
Index = QModelIndex | QPersistentModelIndex
_ROOT = QModelIndex()


class Column(IntEnum):
    ART = 0
    TITLE = 1
    ARTIST = 2
    ALBUM = 3
    TIME = 4
    FORMAT = 5
    STATUS = 6


HEADERS = {
    Column.ART: "",
    Column.TITLE: "Title",
    Column.ARTIST: "Artist",
    Column.ALBUM: "Album",
    Column.TIME: "Time",
    Column.FORMAT: "Format",
    Column.STATUS: "Status",
}
EDITABLE = {Column.TITLE: "title", Column.ARTIST: "artist", Column.ALBUM: "album"}


class State(StrEnum):
    IDLE = "idle"
    WORKING = "working"
    DONE = "done"
    PROBLEM = "problem"


@dataclass
class Track:
    source: Path
    detected: Detected
    tags: Tags
    id: int = field(default_factory=lambda: next(_ids))
    state: State = State.IDLE
    progress: float = 0.0
    message: str = ""
    output: Path | None = None

    def plan_text(self) -> str:
        if self.detected.protected:
            return "Copy-protected"
        action = self.detected.action
        if action is Action.SKIP:
            return "Can't read"
        return "Will copy" if action is Action.COPY else "Will convert"


_OUTCOME_TEXT = {
    Outcome.READY: "Ready",
    Outcome.UPDATED: "Updated",
    Outcome.DUPLICATE: "Already there",
    Outcome.SKIPPED: "Skipped",
    Outcome.FAILED: "Failed",
    Outcome.CANCELLED: "Cancelled",
}


def format_time(seconds: float) -> str:
    if seconds <= 0:
        return ""
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


class TrackModel(QAbstractTableModel):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.tracks: list[Track] = []
        self.undo = QUndoStack(self)
        self.locked = False
        self._thumbs: dict[str, QPixmap] = {}
        self._status_text: dict[int, str] = {}

    def rowCount(self, parent: Index = _ROOT) -> int:
        return 0 if parent.isValid() else len(self.tracks)

    def columnCount(self, parent: Index = _ROOT) -> int:
        return 0 if parent.isValid() else len(Column)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[Column(section)]
        if role == Qt.ItemDataRole.TextAlignmentRole and section == Column.TIME:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def flags(self, index: Index) -> Qt.ItemFlag:
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if not self.locked and index.column() in EDITABLE:
            base |= Qt.ItemFlag.ItemIsEditable
        return base

    def thumbnail(self, art: Artwork | None, size: int, dpr: float) -> QPixmap | None:
        if art is None:
            return None
        key = f"{art.digest}:{size}:{dpr}"
        if key not in self._thumbs:
            image = QImage.fromData(art.data)
            if image.isNull():
                return None
            pixels = round(size * dpr)
            pixmap = QPixmap.fromImage(
                image.scaled(
                    pixels,
                    pixels,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            pixmap.setDevicePixelRatio(dpr)
            self._thumbs[key] = pixmap
        return self._thumbs[key]

    def status_text(self, track: Track) -> str:
        if track.state is State.WORKING:
            return "Converting" if track.detected.action is Action.CONVERT else "Copying"
        return self._status_text.get(track.id) or track.plan_text()

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        track = self.tracks[index.row()]
        col = Column(index.column())
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if col in EDITABLE:
                return getattr(track.tags, EDITABLE[col])
            if col is Column.TIME:
                return format_time(track.detected.duration)
            if col is Column.FORMAT:
                return "" if track.detected.kind is Kind.UNKNOWN else track.detected.kind.value
            if col is Column.STATUS:
                return self.status_text(track)
        if role == Qt.ItemDataRole.ToolTipRole:
            if col is Column.STATUS and track.message:
                return track.message
            if col in (Column.TITLE, Column.ART):
                return str(track.source)
        if role == Qt.ItemDataRole.TextAlignmentRole and col is Column.TIME:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.UserRole:
            return track
        return None

    def setData(self, index: Index, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole or index.column() not in EDITABLE or self.locked:
            return False
        text = str(value).strip()
        name = EDITABLE[Column(index.column())]
        track = self.tracks[index.row()]
        if getattr(track.tags, name) == text:
            return False
        self.set_fields([track.id], {name: text}, f"Edit {HEADERS[Column(index.column())]}")
        return True

    def row_of(self, track_id: int) -> int:
        for row, track in enumerate(self.tracks):
            if track.id == track_id:
                return row
        return -1

    def track(self, track_id: int) -> Track | None:
        row = self.row_of(track_id)
        return self.tracks[row] if row >= 0 else None

    def refresh_row(self, row: int) -> None:
        if row >= 0:
            self.dataChanged.emit(self.index(row, 0), self.index(row, len(Column) - 1))

    def set_fields(self, track_ids: list[int], values: dict[str, Any], text: str) -> None:
        changes = []
        for track_id in track_ids:
            track = self.track(track_id)
            if track is None:
                continue
            for name, new in values.items():
                old = getattr(track.tags, name)
                if old != new:
                    changes.append((track_id, name, old, new))
        if changes:
            self.undo.push(_SetFields(self, changes, text))

    def apply_field(self, track_id: int, name: str, value: Any) -> None:
        row = self.row_of(track_id)
        if row < 0:
            return
        setattr(self.tracks[row].tags, name, value)
        self._status_text.pop(track_id, None)
        self.tracks[row].state = State.IDLE
        self.refresh_row(row)

    def add(self, tracks: list[Track]) -> int:
        known = {t.source for t in self.tracks}
        fresh = []
        for t in tracks:
            if t.source not in known:
                known.add(t.source)
                fresh.append(t)
        if fresh:
            start = len(self.tracks)
            self.beginInsertRows(QModelIndex(), start, start + len(fresh) - 1)
            self.tracks.extend(fresh)
            self.endInsertRows()
        return len(fresh)

    def remove_rows(self, rows: list[int]) -> None:
        rows = sorted({r for r in rows if 0 <= r < len(self.tracks)})
        if rows and not self.locked:
            self.undo.push(_Remove(self, [(r, self.tracks[r]) for r in rows]))

    def _take(self, row: int) -> None:
        self.beginRemoveRows(QModelIndex(), row, row)
        del self.tracks[row]
        self.endRemoveRows()

    def _put(self, row: int, track: Track) -> None:
        row = min(row, len(self.tracks))
        self.beginInsertRows(QModelIndex(), row, row)
        self.tracks.insert(row, track)
        self.endInsertRows()

    def clear(self) -> None:
        self.beginResetModel()
        self.tracks.clear()
        self._status_text.clear()
        self.endResetModel()
        self.undo.clear()

    def mark_working(self, track_id: int, fraction: float) -> None:
        row = self.row_of(track_id)
        if row < 0:
            return
        track = self.tracks[row]
        track.state = State.WORKING
        track.progress = fraction
        self.dataChanged.emit(self.index(row, Column.STATUS), self.index(row, Column.STATUS))

    def mark_result(self, result: Result) -> None:
        row = self.row_of(result.key)
        if row < 0:
            return
        track = self.tracks[row]
        good = result.outcome in (Outcome.READY, Outcome.UPDATED)
        track.state = State.DONE if good else State.PROBLEM
        if result.outcome is Outcome.CANCELLED:
            track.state = State.IDLE
        track.progress = 1.0 if good else 0.0
        track.message = result.message
        track.output = result.path
        self._status_text[track.id] = _OUTCOME_TEXT[result.outcome]
        self.refresh_row(row)

    def reset_status(self) -> None:
        self._status_text.clear()
        for track in self.tracks:
            track.state = State.IDLE
            track.progress = 0.0
            track.message = ""
        if self.tracks:
            last = self.index(len(self.tracks) - 1, len(Column) - 1)
            self.dataChanged.emit(self.index(0, 0), last)

    def total_duration(self) -> float:
        return sum(t.detected.duration for t in self.tracks)


class _SetFields(QUndoCommand):
    def __init__(
        self, model: TrackModel, changes: list[tuple[int, str, Any, Any]], text: str
    ) -> None:
        super().__init__(text)
        self.model = model
        self.changes = changes

    def redo(self) -> None:
        for track_id, name, _old, new in self.changes:
            self.model.apply_field(track_id, name, new)

    def undo(self) -> None:
        for track_id, name, old, _new in reversed(self.changes):
            self.model.apply_field(track_id, name, old)


class _Remove(QUndoCommand):
    def __init__(self, model: TrackModel, removed: list[tuple[int, Track]]) -> None:
        super().__init__("Remove" if len(removed) == 1 else f"Remove {len(removed)} Tracks")
        self.model = model
        self.removed = removed

    def redo(self) -> None:
        for row, _track in reversed(self.removed):
            self.model._take(row)

    def undo(self) -> None:
        for row, track in self.removed:
            self.model._put(row, track)
