from pathlib import Path

from sleeve.core.matching import (
    Candidate,
    MatchState,
    Wanted,
    classify,
    normalize,
    queries,
    rank,
    score,
    similarity,
)


def cand(
    name: str, *artists: str, duration: float = 200.0, isrc: str = "", uri: str = ""
) -> Candidate:
    return Candidate(uri or f"spotify:track:{name}", name, artists, "Album", duration, isrc)


def test_normalize_drops_noise() -> None:
    assert normalize("Hey Jude - Remastered 2015") == "hey jude"
    assert normalize("Señorita (feat. Camila Cabello)") == "senorita"
    assert normalize("Don't Stop Me Now (2011 Remaster)") == "don t stop me now"
    assert normalize("Live Forever (Live)") == "live forever live"


def test_similarity_is_forgiving_about_punctuation() -> None:
    assert similarity("Dont Stop Me Now", "Don't Stop Me Now") > 0.9
    assert similarity("Hello", "Goodbye") < 0.5


def test_isrc_wins() -> None:
    wanted = Wanted("Something else", "Nobody", isrc="GBUM71029604")
    assert score(wanted, cand("Hey Jude", "The Beatles", isrc="GBUM71029604")) == 1.0


def test_exact_song_is_found() -> None:
    wanted = Wanted("Motion Sickness", "Phoebe Bridgers", duration=230)
    ranked = rank(
        wanted,
        [
            cand("Motion Sickness", "Phoebe Bridgers", duration=229.6),
            cand("Motion Sickness - Live", "Phoebe Bridgers", duration=260),
        ],
    )
    match = classify(ranked)
    assert match.state is MatchState.FOUND
    assert match.chosen is not None and match.chosen.name == "Motion Sickness"
    assert match.uri == "spotify:track:Motion Sickness"


def test_featured_artist_and_remaster_still_match() -> None:
    wanted = Wanted("Señorita", "Shawn Mendes & Camila Cabello", duration=191)
    found = classify(
        rank(wanted, [cand("Señorita", "Shawn Mendes", "Camila Cabello", duration=190.8)])
    )
    assert found.state is MatchState.FOUND


def test_wrong_artist_needs_review_or_fails() -> None:
    wanted = Wanted("Yesterday", "My Garage Band", duration=125)
    match = classify(rank(wanted, [cand("Yesterday", "The Beatles", duration=125)]))
    assert match.state in (MatchState.REVIEW, MatchState.NOT_FOUND)
    assert match.uri is None


def test_far_off_duration_lowers_confidence() -> None:
    wanted = Wanted("Intro", "The xx", duration=128)
    good = score(wanted, cand("Intro", "The xx", duration=127))
    bad = score(wanted, cand("Intro", "The xx", duration=300))
    assert good > bad


def test_missing_artist_never_auto_matches() -> None:
    match = classify(rank(Wanted("Intro"), [cand("Intro", "The xx")]))
    assert match.state is not MatchState.FOUND


def test_unrelated_results_are_not_found() -> None:
    match = classify(rank(Wanted("My Demo 3", "Me"), [cand("Demons", "Imagine Dragons")]))
    assert match.state is MatchState.NOT_FOUND
    assert classify([]).state is MatchState.NOT_FOUND


def test_queries() -> None:
    assert queries(Wanted("Hey Jude", "The Beatles", isrc="GBUM71029604")) == [
        "isrc:GBUM71029604",
        'track:"hey jude" artist:"the beatles"',
        "hey jude the beatles",
    ]
    assert queries(Wanted("Intro")) == ["intro"]


class FakeSearch:
    def __init__(self, results: dict[str, list[Candidate]]) -> None:
        self.results = results
        self.calls: list[str] = []

    def search_tracks(self, query: str) -> list[Candidate]:
        self.calls.append(query)
        return self.results.get(query, [])


def test_matcher_stops_at_first_confident_query(tmp_path: Path) -> None:
    from sleeve.spotify.matcher import Matcher, SearchCache

    hit = cand("Hey Jude", "The Beatles", duration=431)
    search = FakeSearch({'track:"hey jude" artist:"the beatles"': [hit]})
    matcher = Matcher(search, SearchCache(tmp_path / "cache.json"))
    match = matcher.match(Wanted("Hey Jude", "The Beatles", duration=431))
    assert match.state is MatchState.FOUND
    assert search.calls == ['track:"hey jude" artist:"the beatles"']


def test_matcher_uses_cache(tmp_path: Path) -> None:
    from sleeve.spotify.matcher import Matcher, SearchCache

    search = FakeSearch({"intro": [cand("Intro", "The xx")]})
    cache = SearchCache(tmp_path / "cache.json")
    Matcher(search, cache).match(Wanted("Intro"))
    cache.save()
    again = FakeSearch({})
    match = Matcher(again, SearchCache(tmp_path / "cache.json")).match(Wanted("Intro"))
    assert again.calls == []
    assert match.candidates and match.candidates[0].name == "Intro"


def test_matcher_merges_queries_and_keeps_best(tmp_path: Path) -> None:
    from sleeve.spotify.matcher import Matcher, SearchCache

    search = FakeSearch(
        {
            'track:"my demo" artist:"me"': [],
            "my demo me": [cand("Demons", "Imagine Dragons")],
        }
    )
    match = Matcher(search, SearchCache(tmp_path / "c.json")).match(Wanted("My Demo", "Me"))
    assert match.state is MatchState.NOT_FOUND
    assert len(search.calls) == 2
