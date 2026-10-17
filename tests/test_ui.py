import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QApplication, QLineEdit
from pytestqt.qtbot import QtBot

from sleeve.core.formats import detect
from sleeve.core.tags import Tags
from sleeve.settings import Settings
from sleeve.ui.dialogs import FinishDialog
from sleeve.ui.main_window import MainWindow
from sleeve.ui.track_model import Column, Track, TrackModel
from sleeve.ui.workers import load_track

from .conftest import png_bytes


def make_track(data_dir: Path, name: str = "tone.mp3", **tags: str) -> Track:
    path = data_dir / name
    return Track(path, detect(path), Tags(**tags))


class NoStore:
    persistent = True

    def load(self) -> None:
        return None

    def save(self, token: object) -> None:
        pass

    def clear(self) -> None:
        pass


@pytest.fixture
def window(
    qtbot: QtBot, tmp_path: Path, ffmpeg_path: Path, monkeypatch: pytest.MonkeyPatch
) -> MainWindow:
    monkeypatch.setenv("SLEEVE_FFMPEG", str(ffmpeg_path))
    settings = Settings(output_root=str(tmp_path / "Music" / "Sleeve"), setup_done=True)
    win = MainWindow(settings, NoStore())  # type: ignore[arg-type]
    qtbot.addWidget(win)
    win.show()
    return win


def test_edit_and_undo(qtbot: QtBot, data_dir: Path) -> None:
    model = TrackModel()
    model.add([make_track(data_dir, title="Old")])
    index = model.index(0, Column.TITLE)
    assert model.setData(index, "  New ")
    assert model.data(index) == "New"
    model.undo.undo()
    assert model.data(index) == "Old"
    model.undo.redo()
    assert model.data(index) == "New"


def test_bulk_set_is_one_undo_step(data_dir: Path) -> None:
    model = TrackModel()
    model.add([make_track(data_dir, "tone.mp3"), make_track(data_dir, "tone.wav")])
    ids = [t.id for t in model.tracks]
    model.set_fields(ids, {"artist": "Band", "album": "LP"}, "Edit")
    assert {t.tags.artist for t in model.tracks} == {"Band"}
    model.undo.undo()
    assert {t.tags.artist for t in model.tracks} == {""}


def test_remove_and_undo(data_dir: Path) -> None:
    model = TrackModel()
    model.add([make_track(data_dir, n) for n in ("tone.mp3", "tone.wav", "tone.flac")])
    model.remove_rows([0, 2])
    assert [t.source.name for t in model.tracks] == ["tone.wav"]
    model.undo.undo()
    assert [t.source.name for t in model.tracks] == ["tone.mp3", "tone.wav", "tone.flac"]


def test_add_ignores_same_file_twice(data_dir: Path) -> None:
    model = TrackModel()
    assert model.add([make_track(data_dir)]) == 1
    assert model.add([make_track(data_dir)]) == 0


def test_locked_model_is_read_only(data_dir: Path) -> None:
    model = TrackModel()
    model.add([make_track(data_dir, title="x")])
    model.locked = True
    assert not model.setData(model.index(0, Column.TITLE), "y")


def test_load_track_falls_back_to_filename(tmp_path: Path, data_dir: Path) -> None:
    path = tmp_path / "01 - my_song_FINAL v2.wav"
    shutil.copy(data_dir / "tone.wav", path)
    track = load_track(path)
    assert track is not None and track.tags.title == "My Song"


def test_empty_state_then_table(
    window: MainWindow, qtbot: QtBot, tmp_path: Path, data_dir: Path
) -> None:
    assert window.stack.currentWidget() is window.drop_area
    assert not window.bottom.primary.isEnabled()
    folder = tmp_path / "drop"
    (folder / "nested").mkdir(parents=True)
    shutil.copy(data_dir / "tone.mp3", folder / "nested" / "a.mp3")
    shutil.copy(data_dir / "tone.flac", folder / "b.flac")
    (folder / "notes.mp3").write_text("not audio")
    (folder / "cover.jpg").write_bytes(b"")
    window.add_paths([folder])
    qtbot.waitUntil(lambda: window.scanning == 0 and len(window.model.tracks) == 2, timeout=5000)
    assert window.stack.currentWidget() is window.table
    assert window.bottom.primary.isEnabled()
    assert "1 file skipped" in window.message.text()


def test_keyboard_delete_enter_and_tab(window: MainWindow, qtbot: QtBot, data_dir: Path) -> None:
    window.model.add([make_track(data_dir, n) for n in ("tone.mp3", "tone.wav")])
    table = window.table
    table.setFocus()
    table.setCurrentIndex(window.model.index(0, Column.TITLE))
    qtbot.keyClick(table, Qt.Key.Key_Return)
    assert table.state() == QAbstractItemView.State.EditingState
    editor = QApplication.focusWidget()
    assert isinstance(editor, QLineEdit)
    editor.setText("Typed")
    qtbot.keyClick(editor, Qt.Key.Key_Tab)
    assert window.model.tracks[0].tags.title == "Typed"
    assert table.currentIndex().column() == Column.ARTIST
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Escape)
    table.selectRow(1)
    qtbot.keyClick(table, Qt.Key.Key_Delete)
    assert len(window.model.tracks) == 1
    window.act_undo.trigger()
    assert len(window.model.tracks) == 2


def test_artwork_for_selection(window: MainWindow, tmp_path: Path, data_dir: Path) -> None:
    window.model.add([make_track(data_dir, n) for n in ("tone.mp3", "tone.wav")])
    image = tmp_path / "cover.png"
    image.write_bytes(png_bytes(4, 4))
    window.table.selectAll()
    window.apply_artwork(image)
    assert all(t.tags.artwork is not None for t in window.model.tracks)


def test_prepare_end_to_end(
    window: MainWindow, qtbot: QtBot, tmp_path: Path, data_dir: Path
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    for name in ("tone.wav", "tone.mp3", "drm.m4p"):
        shutil.copy(data_dir / name, src / name)
    window.add_paths([src])
    qtbot.waitUntil(lambda: window.scanning == 0 and len(window.model.tracks) == 3)
    window.bottom.playlist.setEditText("Late Night / Demos")
    window.prepare()
    assert window.model.locked
    qtbot.waitUntil(lambda: window.prepare_cancel is None, timeout=15000)
    out = tmp_path / "Music" / "Sleeve" / "Late Night - Demos"
    assert sorted(p.suffix for p in out.iterdir() if not p.name.startswith(".")) == [".mp3", ".mp3"]
    assert "2 tracks ready. 1 skipped" in window.message.text()
    assert isinstance(window._dialog, FinishDialog)
    assert window.settings.recent_playlists[0] == "Late Night / Demos"
    statuses = [window.model.data(window.model.index(r, Column.STATUS)) for r in range(3)]
    assert sorted(statuses) == ["Ready", "Ready", "Skipped"]


def test_prepare_needs_playlist_name(window: MainWindow, data_dir: Path) -> None:
    window.model.add([make_track(data_dir)])
    window.bottom.playlist.setEditText("")
    window.prepare()
    assert window.prepare_cancel is None
    assert "playlist name" in window.message.text()


def test_undo_blocked_while_running(window: MainWindow, data_dir: Path) -> None:
    window.model.add([make_track(data_dir, title="Old")])
    window.model.setData(window.model.index(0, Column.TITLE), "New")
    assert window.act_undo.isEnabled()
    assert window.act_undo.text() == "Undo Edit Title"
    window.prepare_cancel = __import__("threading").Event()
    window._update_state()
    assert not window.act_undo.isEnabled()
    window.prepare_cancel = None
