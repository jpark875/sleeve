from PySide6.QtCore import QAbstractItemModel, QModelIndex, QPersistentModelIndex, QRect, Qt
from PySide6.QtGui import QKeyEvent, QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QLineEdit,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionProgressBar,
    QStyleOptionViewItem,
    QTableView,
    QWidget,
)

from sleeve.core.matching import MatchState
from sleeve.ui.track_model import EDITABLE, Column, State, Track, TrackModel

ROW_HEIGHT = 30
ART_SIZE = 22
BULK_COLUMNS = {Column.ARTIST, Column.ALBUM}


class TrackDelegate(QStyledItemDelegate):
    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        track: Track = index.data(Qt.ItemDataRole.UserRole)
        col = index.column()
        if col == Column.ART:
            self._paint_art(painter, option, index, track)
            return
        if col == Column.STATUS and track.state is State.WORKING:
            self._paint_progress(painter, option, track)
            return
        if (
            col == Column.STATUS
            and track.state is State.IDLE
            and track.match.state is MatchState.REVIEW
        ):
            option.palette.setColor(
                QPalette.ColorRole.Text, option.palette.color(QPalette.ColorRole.Link)
            )
        if col == Column.STATUS and track.state is State.PROBLEM:
            option.palette.setColor(
                QPalette.ColorRole.Text, option.palette.color(QPalette.ColorRole.PlaceholderText)
            )
        super().paint(painter, option, index)

    def _paint_art(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
        track: Track,
    ) -> None:
        style = option.widget.style() if option.widget else QApplication.style()
        base = QStyleOptionViewItem(option)
        self.initStyleOption(base, index)
        style.drawPrimitive(
            QStyle.PrimitiveElement.PE_PanelItemViewItem, base, painter, option.widget
        )
        model = index.model()
        assert isinstance(model, TrackModel)
        dpr = painter.device().devicePixelRatioF()
        rect = QRect(0, 0, ART_SIZE, ART_SIZE)
        rect.moveCenter(option.rect.center())
        pixmap = model.thumbnail(track.tags.artwork, ART_SIZE, dpr)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if pixmap is not None:
            source = QRect(0, 0, round(ART_SIZE * dpr), round(ART_SIZE * dpr))
            source.moveCenter(pixmap.rect().center())
            painter.drawPixmap(rect, pixmap, source)
        painter.restore()

    def _paint_progress(
        self, painter: QPainter, option: QStyleOptionViewItem, track: Track
    ) -> None:
        bar = QStyleOptionProgressBar()
        bar.rect = option.rect.adjusted(6, 8, -8, -8)
        bar.minimum = 0
        bar.maximum = 1000
        bar.progress = round(track.progress * 1000)
        bar.textVisible = False
        bar.state = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Horizontal
        bar.palette = option.palette
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ProgressBar, bar, painter, option.widget)

    def createEditor(
        self,
        parent: QWidget,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> QWidget:
        editor = QLineEdit(parent)
        editor.setFrame(False)
        return editor

    def setModelData(
        self, editor: QWidget, model: QAbstractItemModel, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        assert isinstance(editor, QLineEdit) and isinstance(model, TrackModel)
        col = Column(index.column())
        view = self.parent()
        rows = view.selected_rows() if isinstance(view, TrackTable) else []
        if col in BULK_COLUMNS and len(rows) > 1 and index.row() in rows:
            ids = [model.tracks[r].id for r in rows]
            name = EDITABLE[col]
            model.set_fields(ids, {name: editor.text().strip()}, f"Edit {name.title()}")
            return
        model.setData(index, editor.text())


class TrackTable(QTableView):
    def __init__(self, model: TrackModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModel(model)
        self.setItemDelegate(TrackDelegate(self))
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setTabKeyNavigation(True)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setStretchLastSection(False)
        widths = {
            Column.ART: 34, Column.TITLE: 240, Column.ARTIST: 170, Column.ALBUM: 170,
            Column.TIME: 56, Column.FORMAT: 84, Column.STATUS: 120,
        }  # fmt: skip
        for col, width in widths.items():
            self.setColumnWidth(col, width)
        header.setSectionResizeMode(Column.ART, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(Column.TITLE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(Column.ARTIST, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(Column.ALBUM, QHeaderView.ResizeMode.Interactive)

    def track_model(self) -> TrackModel:
        model = self.model()
        assert isinstance(model, TrackModel)
        return model

    def selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.selectionModel().selectedRows()})

    def selected_ids(self) -> list[int]:
        tracks = self.track_model().tracks
        return [tracks[r].id for r in self.selected_rows()]

    def keyPressEvent(self, event: QKeyEvent) -> None:
        plain = event.modifiers() in (
            Qt.KeyboardModifier.NoModifier,
            Qt.KeyboardModifier.KeypadModifier,
        )
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and plain
            and self.state() != QAbstractItemView.State.EditingState
        ):
            index = self.currentIndex()
            if index.isValid():
                if index.column() not in EDITABLE:
                    index = index.siblingAtColumn(Column.TITLE)
                    self.setCurrentIndex(index)
                self.edit(index)
                return
        super().keyPressEvent(event)

    def moveCursor(
        self, action: QAbstractItemView.CursorAction, modifiers: Qt.KeyboardModifier
    ) -> QModelIndex:
        forward = action is QAbstractItemView.CursorAction.MoveNext
        if not forward and action is not QAbstractItemView.CursorAction.MovePrevious:
            return super().moveCursor(action, modifiers)
        model = self.track_model()
        rows = model.rowCount()
        current = self.currentIndex()
        if rows == 0:
            return current
        cols = sorted(EDITABLE)
        row = current.row() if current.isValid() else 0
        col = current.column() if current.isValid() else cols[0]
        later = [c for c in cols if (c > col if forward else c < col)]
        if later:
            return model.index(row, later[0] if forward else later[-1])
        row = (row + (1 if forward else -1)) % rows
        return model.index(row, cols[0] if forward else cols[-1])
