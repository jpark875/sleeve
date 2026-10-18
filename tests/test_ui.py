import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QApplication, QLabel, QLineEdit
from pytestqt.qtbot import QtBot

from sleeve.core.formats import detect
from sleeve.core.tags import Tags
from sleeve.settings import Settings
from sleeve.ui.dialogs import FinishDialog
from sleeve.ui.main_window import MainWindow
from sleeve.ui.track_model import Column, Track, TrackModel
from sleeve.ui.workers import load_track

from .conftest import png_bytes
from .fake_spotify import FakeSpotify, MemoryStore, track


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
    assert len(model.add([make_track(data_dir)])) == 1
    assert len(model.add([make_track(data_dir)])) == 0


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
    assert "2 files ready for Local Files. 1 skipped." in window.message.text()
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


def test_setup_guide_creates_the_folder(qtbot: QtBot, tmp_path: Path) -> None:
    from sleeve.ui.dialogs import SetupGuide

    root = tmp_path / "Music" / "Sleeve"
    guide = SetupGuide(root)
    qtbot.addWidget(guide)
    assert root.is_dir()
    texts = " ".join(label.text() for label in guide.findChildren(QLabel))
    assert "created for you" in texts
    assert "<b>Sleeve</b>" in texts


def test_setup_guide_explains_when_folder_cannot_be_created(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sleeve.ui import dialogs

    monkeypatch.setattr(dialogs, "create_folder", lambda path: False)
    root = tmp_path / "Music" / "Sleeve"
    guide = dialogs.SetupGuide(root)
    qtbot.addWidget(guide)
    texts = " ".join(label.text() for label in guide.findChildren(QLabel))
    assert "Create a folder named <b>Sleeve</b>" in texts
    assert str(root.parent) in texts


def test_first_run_creates_output_root(window: MainWindow) -> None:
    root = Path(window.settings.output_root)
    assert not root.exists()
    window.first_run()
    assert root.is_dir()


CATALOG = [
    track("Hey Jude", "The Beatles", "spotify:track:jude"),
    track("Motion Sickness", "Phoebe Bridgers", "spotify:track:motion"),
]


@pytest.fixture
def connected(window: MainWindow) -> MainWindow:
    window.store = MemoryStore()  # type: ignore[assignment]
    window.settings.spotify_client_id = "cid"
    window.transport = FakeSpotify(CATALOG)
    window.refresh_connection()
    return window


def add_mixed(window: MainWindow, tmp_path: Path, data_dir: Path) -> None:
    from sleeve.core.library import Entry
    from sleeve.ui.workers import track_from_entry

    jude = tmp_path / "jude.mp3"
    demo = tmp_path / "demo.wav"
    shutil.copy(data_dir / "tone.mp3", jude)
    shutil.copy(data_dir / "tone.wav", demo)
    tracks = [
        track_from_entry(Entry("Hey Jude", "The Beatles", duration=1.0, path=jude)),
        track_from_entry(Entry("My Demo", "Me", duration=1.0, path=demo)),
        track_from_entry(Entry("Motion Sickness", "Phoebe Bridgers", duration=1.0)),
        track_from_entry(Entry("Lost Song", "Nobody", duration=1.0)),
    ]
    window._found(tracks)


def test_connected_flow_adds_catalog_songs_and_prepares_the_rest(
    connected: MainWindow, qtbot: QtBot, tmp_path: Path, data_dir: Path
) -> None:
    from sleeve.core.matching import MatchState

    window = connected
    assert window.bottom.primary.text() == "Add to Spotify"
    add_mixed(window, tmp_path, data_dir)
    qtbot.waitUntil(lambda: not window.matching and not window.match_queue, timeout=5000)
    states = [t.match.state for t in window.model.tracks]
    assert states == [
        MatchState.FOUND,
        MatchState.NOT_FOUND,
        MatchState.FOUND,
        MatchState.NOT_FOUND,
    ]
    statuses = [window.model.data(window.model.index(r, Column.STATUS)) for r in range(4)]
    assert statuses == ["On Spotify", "Will convert", "On Spotify", "No file"]

    window.bottom.playlist.setEditText("Road Trip")
    window.prepare()
    qtbot.waitUntil(lambda: window.prepare_cancel is None, timeout=15000)
    spotify = window.transport
    assert isinstance(spotify, FakeSpotify)
    assert spotify.created == ["Road Trip"]
    assert spotify.playlist == ["spotify:track:jude", "spotify:track:motion"]
    report = window.report
    assert report is not None
    assert (report.added, report.local_ready, report.missing) == (2, 1, 1)
    files = [
        p.name
        for p in (tmp_path / "Music" / "Sleeve" / "Road Trip").iterdir()
        if not p.name.startswith(".")
    ]
    assert files == ["Me - My Demo.mp3"]
    statuses = [window.model.data(window.model.index(r, Column.STATUS)) for r in range(4)]
    assert statuses == ["Added", "Ready", "Added", "Not found"]
    assert isinstance(window._dialog, FinishDialog)

    window._dialog.close()
    window.prepare()
    qtbot.waitUntil(lambda: window.prepare_cancel is None, timeout=15000)
    assert spotify.playlist == ["spotify:track:jude", "spotify:track:motion"]
    assert window.report is not None and window.report.already == 2


def test_prepare_waits_for_search_to_finish(
    connected: MainWindow, qtbot: QtBot, tmp_path: Path, data_dir: Path
) -> None:
    window = connected
    add_mixed(window, tmp_path, data_dir)
    window.bottom.playlist.setEditText("Road Trip")
    window.prepare()
    assert window.prepare_after_match
    qtbot.waitUntil(
        lambda: window.report is not None and window.prepare_cancel is None, timeout=15000
    )
    assert window.report is not None and window.report.added == 2


def test_spotify_refusal_is_reported(
    connected: MainWindow, qtbot: QtBot, tmp_path: Path, data_dir: Path
) -> None:
    window = connected
    window.transport.fail_add = 403  # type: ignore[attr-defined]
    add_mixed(window, tmp_path, data_dir)
    qtbot.waitUntil(lambda: not window.matching and not window.match_queue, timeout=5000)
    window.bottom.playlist.setEditText("Road Trip")
    window.prepare()
    qtbot.waitUntil(lambda: window.prepare_cancel is None, timeout=15000)
    report = window.report
    assert report is not None and "user list" in report.spotify_error
    assert report.local_ready == 1
    status = window.model.data(window.model.index(0, Column.STATUS))
    assert status == "Not added"


def test_editing_a_title_searches_again(
    connected: MainWindow, qtbot: QtBot, tmp_path: Path, data_dir: Path
) -> None:
    from sleeve.core.matching import MatchState

    window = connected
    add_mixed(window, tmp_path, data_dir)
    qtbot.waitUntil(lambda: not window.matching and not window.match_queue, timeout=5000)
    row = 3
    window.model.setData(window.model.index(row, Column.TITLE), "Hey Jude")
    window.model.setData(window.model.index(row, Column.ARTIST), "The Beatles")
    qtbot.waitUntil(lambda: window.model.tracks[row].match.state is MatchState.FOUND, timeout=5000)


def test_review_dialog_decisions(qtbot: QtBot, data_dir: Path) -> None:
    from sleeve.core.matching import Candidate, Match, MatchState
    from sleeve.ui.spotify_dialogs import ReviewDialog

    cands = (Candidate("spotify:track:a", "Yesterday", ("The Beatles",), "Help!", 125.0, "", 0.7),)
    one = make_track(data_dir, title="Yesterday", artist="My Band")
    one.match = Match(MatchState.REVIEW, cands, cands[0])
    two = make_track(data_dir, "tone.wav", title="Yesterday", artist="Other")
    two.match = Match(MatchState.REVIEW, cands, cands[0])
    dialog = ReviewDialog([one, two])
    qtbot.addWidget(dialog)
    dialog.combos[two.id].setCurrentIndex(dialog.combos[two.id].count() - 1)
    decisions = dialog.decisions()
    assert decisions[one.id].state is MatchState.FOUND
    assert decisions[two.id].state is MatchState.KEEP_LOCAL


def test_import_m3u_sets_playlist_name(
    window: MainWindow, qtbot: QtBot, tmp_path: Path, data_dir: Path
) -> None:
    shutil.copy(data_dir / "tone.mp3", tmp_path / "a.mp3")
    playlist = tmp_path / "Summer 2019.m3u8"
    playlist.write_text(
        "#EXTM3U\n#EXTINF:1,Band - Song\na.mp3\n#EXTINF:200,Gone - Missing\nb.mp3\n"
    )
    window.bottom.playlist.setEditText("")
    window.add_paths([playlist])
    qtbot.waitUntil(lambda: len(window.model.tracks) == 2 and window.scanning == 0, timeout=5000)
    assert window.bottom.playlist_name() == "Summer 2019"
    assert [t.has_file for t in window.model.tracks] == [True, False]


def test_dropping_a_folder_names_the_playlist(
    window: MainWindow, qtbot: QtBot, tmp_path: Path, data_dir: Path
) -> None:
    folder = tmp_path / "Garage Demos"
    folder.mkdir()
    shutil.copy(data_dir / "tone.mp3", folder / "a.mp3")
    window.bottom.playlist.setEditText("")
    window.add_paths([folder])
    qtbot.waitUntil(lambda: window.scanning == 0 and len(window.model.tracks) == 1, timeout=5000)
    assert window.bottom.playlist_name() == "Garage Demos"
