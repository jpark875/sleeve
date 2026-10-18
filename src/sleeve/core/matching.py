import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import StrEnum

FOUND_AT = 0.86
REVIEW_AT = 0.6

_FEAT = re.compile(r"\s*[(\[]?\b(?:feat|ft|featuring|with)\b\.?\s+[^)\]]*[)\]]?", re.I)
_EXTRA = re.compile(
    r"\s*(?:[(\[][^)\]]*\b(?:remaster(?:ed)?|mono|stereo|deluxe|bonus|single version"
    r"|album version|explicit|clean)\b[^)\]]*[)\]]"
    r"|\s-\s[^-]*\b(?:remaster(?:ed)?|mono|stereo|single version|album version)\b.*$)",
    re.I,
)
_PUNCT = re.compile(r"[^\w\s]")
_SPACE = re.compile(r"\s+")
_ARTIST_SPLIT = re.compile(r"\s*(?:,|&|\band\b|\bx\b|\bfeat\.?|\bft\.?|/|;)\s*", re.I)


class MatchState(StrEnum):
    UNCHECKED = "unchecked"
    SEARCHING = "searching"
    FOUND = "found"
    REVIEW = "review"
    NOT_FOUND = "not_found"
    KEEP_LOCAL = "keep_local"
    ERROR = "error"


@dataclass(frozen=True)
class Candidate:
    uri: str
    name: str
    artists: tuple[str, ...]
    album: str
    duration: float
    isrc: str = ""
    score: float = 0.0

    @property
    def artist_text(self) -> str:
        return ", ".join(self.artists)

    def describe(self) -> str:
        parts = [f"{self.name} \u2014 {self.artist_text}"]
        if self.album:
            parts.append(self.album)
        if self.duration:
            minutes, seconds = divmod(round(self.duration), 60)
            parts.append(f"{minutes}:{seconds:02d}")
        return " \u00b7 ".join(parts)


@dataclass(frozen=True)
class Match:
    state: MatchState
    candidates: tuple[Candidate, ...] = field(default=())
    chosen: Candidate | None = None
    message: str = ""

    @property
    def uri(self) -> str | None:
        if self.state is MatchState.FOUND and self.chosen is not None:
            return self.chosen.uri
        return None


@dataclass(frozen=True)
class Wanted:
    title: str
    artist: str = ""
    album: str = ""
    duration: float = 0.0
    isrc: str = ""


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _FEAT.sub(" ", text)
    text = _EXTRA.sub(" ", text)
    text = text.replace("&", " and ")
    text = _PUNCT.sub(" ", text.casefold())
    return _SPACE.sub(" ", text).strip()


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    overlap = len(ta & tb) / max(len(ta), len(tb))
    return max(ratio, overlap * 0.95)


def artist_similarity(local: str, remote: tuple[str, ...]) -> float:
    names = [n for n in _ARTIST_SPLIT.split(local) if n.strip()] or [local]
    best = 0.0
    for name in names:
        for other in remote:
            best = max(best, similarity(name, other))
    if remote:
        best = max(best, similarity(local, ", ".join(remote)))
    return best


def score(wanted: Wanted, candidate: Candidate) -> float:
    if wanted.isrc and candidate.isrc and wanted.isrc == candidate.isrc:
        return 1.0
    title = similarity(wanted.title, candidate.name)
    parts = [(title, 0.55)]
    if wanted.artist:
        parts.append((artist_similarity(wanted.artist, candidate.artists), 0.3))
    if wanted.duration > 0 and candidate.duration > 0:
        delta = abs(wanted.duration - candidate.duration)
        parts.append((max(0.0, 1.0 - max(0.0, delta - 2.0) / 13.0), 0.15))
    total = sum(weight for _, weight in parts)
    value = sum(s * weight for s, weight in parts) / total
    if title < 0.5:
        value = min(value, 0.5)
    if not wanted.artist:
        value = min(value, 0.84)
    return round(value, 4)


def rank(wanted: Wanted, candidates: list[Candidate]) -> list[Candidate]:
    scored = {c.uri: Candidate(c.uri, c.name, c.artists, c.album, c.duration, c.isrc,
                               score(wanted, c)) for c in candidates}  # fmt: skip
    return sorted(scored.values(), key=lambda c: c.score, reverse=True)


def classify(ranked: list[Candidate]) -> Match:
    if not ranked:
        return Match(MatchState.NOT_FOUND)
    best = ranked[0]
    top = tuple(ranked[:5])
    if best.score >= FOUND_AT:
        return Match(MatchState.FOUND, top, best)
    if best.score >= REVIEW_AT:
        return Match(MatchState.REVIEW, top, best)
    return Match(MatchState.NOT_FOUND, top)


def queries(wanted: Wanted) -> list[str]:
    out = []
    if wanted.isrc:
        out.append(f"isrc:{wanted.isrc}")
    title = normalize(wanted.title) or wanted.title
    artist = normalize(wanted.artist) if wanted.artist else ""
    if artist:
        out.append(f'track:"{title}" artist:"{artist}"')
        out.append(f"{title} {artist}")
    else:
        out.append(title)
    return out
