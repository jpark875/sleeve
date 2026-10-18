import plistlib
from pathlib import Path

import pytest

from sleeve.core.library import LibraryError, path_from_url, read_itunes, read_m3u, read_playlists


def test_path_from_url() -> None:
    assert path_from_url("file:///Users/me/Music/A%20Song.mp3") == Path(
        "/Users/me/Music/A Song.mp3"
    )
    assert path_from_url("file://localhost/C:/Music/x.mp3") == Path("C:/Music/x.mp3")
    assert path_from_url("https://example.com/x.mp3") is None


def test_m3u_with_extinf_and_relative_paths(tmp_path: Path) -> None:
    (tmp_path / "songs").mkdir()
    (tmp_path / "songs" / "a.mp3").write_bytes(b"")
    playlist = tmp_path / "Road Trip.m3u8"
    playlist.write_text(
        "#EXTM3U\n#EXTINF:215,Phoebe Bridgers - Motion Sickness\nsongs/a.mp3\n"
        "\n#EXTINF:-1,Untitled stream\nhttp://radio.example/stream\n"
        "songs\\02 - Night Drive.flac\n",
        "utf-8",
    )
    result = read_m3u(playlist)
    assert result.name == "Road Trip"
    first, second = result.entries
    assert (first.artist, first.title, first.duration) == (
        "Phoebe Bridgers",
        "Motion Sickness",
        215,
    )
    assert first.path == tmp_path / "songs" / "a.mp3"
    assert (
        second.title == "Night Drive"
        and second.path == tmp_path / "songs" / "02 - Night Drive.flac"
    )


def test_empty_m3u(tmp_path: Path) -> None:
    path = tmp_path / "x.m3u"
    path.write_text("#EXTM3U\n")
    with pytest.raises(LibraryError):
        read_m3u(path)


def itunes_library(tmp_path: Path) -> Path:
    data = {
        "Tracks": {
            "1": {
                "Track ID": 1,
                "Name": "Hey Jude",
                "Artist": "The Beatles",
                "Album": "1",
                "Total Time": 431000,
                "Track Number": 21,
                "Year": 1968,
                "Location": "file:///Users/me/Music/Hey%20Jude.m4a",
                "Kind": "AAC audio file",
            },
            "2": {"Track ID": 2, "Name": "Episode 4", "Podcast": True, "Kind": "MPEG audio file"},
            "3": {
                "Track ID": 3,
                "Name": "Motion Sickness",
                "Artist": "Phoebe Bridgers",
                "Total Time": 230000,
                "Apple Music": True,
                "Kind": "Apple Music AAC audio file",
            },
            "4": {"Track ID": 4, "Name": "Clip", "Has Video": True, "Kind": "MPEG-4 video file"},
        },
        "Playlists": [
            {"Name": "Library", "Master": True, "Playlist Items": [{"Track ID": 1}]},
            {"Name": "Music", "Distinguished Kind": 4, "Playlist Items": [{"Track ID": 1}]},
            {
                "Name": "Road Trip",
                "Playlist Items": [{"Track ID": 3}, {"Track ID": 2}, {"Track ID": 1}],
            },
            {"Name": "Empty", "Playlist Items": []},
            {"Name": "Folder", "Folder": True},
        ],
    }
    path = tmp_path / "Library.xml"
    with path.open("wb") as fh:
        plistlib.dump(data, fh)
    return path


def test_itunes_library(tmp_path: Path) -> None:
    playlists = read_itunes(itunes_library(tmp_path))
    assert [p.name for p in playlists] == ["All music", "Road Trip"]
    everything, road_trip = playlists
    assert [e.title for e in everything.entries] == ["Motion Sickness", "Hey Jude"]
    assert [e.title for e in road_trip.entries] == ["Motion Sickness", "Hey Jude"]
    jude = road_trip.entries[1]
    assert (jude.duration, jude.track, jude.year) == (431.0, 21, "1968")
    assert jude.path == Path("/Users/me/Music/Hey Jude.m4a")
    assert road_trip.entries[0].path is None


def test_not_a_library(tmp_path: Path) -> None:
    path = tmp_path / "Library.xml"
    path.write_text("<html></html>")
    with pytest.raises(LibraryError) as info:
        read_playlists(path)
    assert "Export Library" in info.value.message
    with pytest.raises(LibraryError):
        read_playlists(tmp_path / "notes.txt")
