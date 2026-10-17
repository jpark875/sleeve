import json

from sleeve.settings import Settings


def test_defaults_and_round_trip() -> None:
    s = Settings.load()
    assert s.format == "mp3" and s.mp3_bitrate == 320
    assert s.output_root.endswith("Sleeve")
    s.format = "m4a"
    s.remember_playlist("Demos")
    s.save()
    again = Settings.load()
    assert again.format == "m4a"
    assert again.recent_playlists == ["Demos"]


def test_recents_are_unique_and_capped() -> None:
    s = Settings()
    for i in range(12):
        s.remember_playlist(f"List {i}")
    s.remember_playlist("list 3")
    assert s.recent_playlists[0] == "list 3"
    assert len(s.recent_playlists) == 8
    assert sum(n.casefold() == "list 3" for n in s.recent_playlists) == 1


def test_bad_values_fall_back() -> None:
    s = Settings()
    s.path.parent.mkdir(parents=True, exist_ok=True)
    s.path.write_text(json.dumps({"format": "flac", "mp3_bitrate": "loud", "pattern": "title"}))
    loaded = Settings.load()
    assert loaded.format == "mp3"
    assert loaded.mp3_bitrate == 320
    assert loaded.pattern == "title"


def test_corrupt_file_uses_defaults() -> None:
    s = Settings()
    s.path.parent.mkdir(parents=True, exist_ok=True)
    s.path.write_text("{")
    assert Settings.load() == Settings()
