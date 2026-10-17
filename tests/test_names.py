from pathlib import Path

import pytest

from sleeve.core.names import (
    existing_names,
    fit_path,
    guess_from_filename,
    render_stem,
    sanitize_component,
    unique_path,
)


@pytest.mark.parametrize(
    ("filename", "artist", "title"),
    [
        ("01 - my_song_FINAL v2.wav", None, "My Song"),
        ("03. Night Drive.mp3", None, "Night Drive"),
        ("1-04 the end of the road.flac", None, "The End of the Road"),
        ("Phoebe Bridgers - Motion Sickness.m4a", "Phoebe Bridgers", "Motion Sickness"),
        ("99 Problems.mp3", None, "99 Problems"),
        ("Carbon Copy.mp3", None, "Carbon Copy"),
        ("beat_v3_bounced (1).wav", None, "Beat"),
        ("track_mixdown_final_final.aiff", None, "Track"),
        ("iPhone Memo.m4a", None, "iPhone Memo"),
        ("MY LOUD SONG.wav", None, "My Loud Song"),
        ("Café del Mar \u2013 Remix.wav", "Café del Mar", "Remix"),
        ("夜に駆ける.flac", None, "夜に駆ける"),
        ("01.wav", None, "01"),
        ("Night Drive (Official Music Video) [4K].mp4", None, "Night Drive"),
        ("Band - Song [Lyrics] (HD).webm", "Band", "Song"),
        ("Song (Radio Edit).mp3", None, "Song (Radio Edit)"),
    ],
)
def test_guess_from_filename(filename: str, artist: str | None, title: str) -> None:
    assert guess_from_filename(filename) == (artist, title)


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ('AC/DC: "Live"?', "AC-DC - Live"),
        ("  trailing dots...  ", "trailing dots"),
        ("CON", "CON_"),
        ("aux.mp3", "aux.mp3_"),
        ("tab\there", "tabhere"),
        ("", "Untitled"),
        ("Beyoncé", "Beyoncé"),
    ],
)
def test_sanitize_component(raw: str, clean: str) -> None:
    assert sanitize_component(raw) == clean


def test_sanitize_truncates_on_character_boundary() -> None:
    long = "é" * 200
    out = sanitize_component(long)
    assert len(out.encode()) <= 120
    assert out == "é" * 60


def test_render_stem_patterns() -> None:
    assert render_stem("artist_title", title="Song", artist="Band") == "Band - Song"
    assert render_stem("artist_title", title="Song") == "Song"
    assert render_stem("track_title", title="Song", track=3) == "03 Song"
    assert render_stem("track_title", title="Song") == "Song"
    assert render_stem("artist_album_title", title="T", artist="A") == "A - T"
    assert render_stem("artist_title", title="a/b", artist="c:d") == "c -d - a-b"


def test_fit_path_limits_length(tmp_path: Path) -> None:
    path = fit_path(tmp_path, "x" * 400, ".mp3")
    assert len(str(path)) <= 240
    assert path.suffix == ".mp3"


def test_unique_path_avoids_taken_and_existing(tmp_path: Path) -> None:
    (tmp_path / "Song.mp3").write_bytes(b"")
    taken = existing_names(tmp_path)
    first = unique_path(tmp_path / "Song.mp3", taken)
    second = unique_path(tmp_path / "song.mp3", taken)
    assert first.name == "Song (2).mp3"
    assert second.name == "song (3).mp3"
