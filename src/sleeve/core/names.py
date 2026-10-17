import re
import unicodedata
from pathlib import Path

_TRACK_PREFIX = re.compile(
    r"^\s*(?:\d{1,2}[-.])?(?:0\d{1,2}(?:\s*[-._)]\s*|\s+)|\d{1,3}\s*[-._)]\s*)(?=\S)"
)
_NOISE = re.compile(
    r"""(?ix)
    (?:^|(?<=[\s_\-.(\[]))
    (?:final(?:[\s_-]*final)*|v(?:er(?:sion)?)?[\s_.-]?\d+(?:\.\d+)*|bounced?|export(?:ed)?
       |mastered|mixdown|wip)
    (?=$|[\s_\-.)\]])
    """
)
_BRACKET_NOISE = re.compile(
    r"\s*[(\[][^()\[\]]*\b(?:official|lyrics?|visuali[sz]er|music video|video|audio|hd|hq"
    r"|4k|1080p|720p)\b[^()\[\]]*[)\]]",
    re.I,
)
_COPY_SUFFIX = re.compile(r"\s*[(\[]\s*(?:\d+|copy)\s*[)\]]\s*$", re.I)
_EMPTY_BRACKETS = re.compile(r"[(\[]\s*[)\]]")
_SPACES = re.compile(r"\s+")
_SMALL = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to"}

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}

MAX_COMPONENT_BYTES = 120
MAX_PATH_CHARS = 240


def _smart_case(text: str) -> str:
    if not (text.islower() or text.isupper()):
        return text
    words = text.lower().split(" ")
    out = []
    for i, word in enumerate(words):
        if 0 < i < len(words) - 1 and word in _SMALL:
            out.append(word)
        else:
            out.append(word[:1].upper() + word[1:])
    return " ".join(out)


def _tidy(text: str) -> str:
    text = text.replace("_", " ")
    text = _BRACKET_NOISE.sub("", text)
    text = _COPY_SUFFIX.sub("", text)
    previous = None
    while previous != text:
        previous = text
        text = _NOISE.sub(" ", text)
        text = _EMPTY_BRACKETS.sub(" ", text)
    text = _SPACES.sub(" ", text)
    return text.strip(" -.·")


def guess_from_filename(filename: str) -> tuple[str | None, str]:
    """Return (artist, title) guessed from a file name with no usable tags."""
    stem = unicodedata.normalize("NFC", Path(filename).stem)
    stem = _TRACK_PREFIX.sub("", stem)
    artist: str | None = None
    parts = [p for p in re.split(r"\s+[-\u2013\u2014]\s+", stem.replace("_-_", " - ")) if p.strip()]
    if len(parts) == 2:
        artist = _smart_case(_tidy(parts[0])) or None
        stem = parts[1]
    title = _smart_case(_tidy(stem))
    if not title:
        title = _tidy(Path(filename).stem) or "Untitled"
    return artist, title


def _truncate_bytes(text: str, limit: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="ignore").rstrip(" .")


def sanitize_component(text: str, limit: int = MAX_COMPONENT_BYTES) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.replace("/", "-").replace("\\", "-").replace(":", " -")
    text = _INVALID.sub("", text)
    text = _SPACES.sub(" ", text).strip(" .")
    text = _truncate_bytes(text, limit)
    if text.split(".")[0].upper() in _RESERVED:
        text = f"{text}_"
    return text or "Untitled"


PATTERNS = {
    "artist_title": "{artist} - {title}",
    "title": "{title}",
    "track_title": "{track} {title}",
    "artist_album_title": "{artist} - {album} - {title}",
}


def render_stem(
    pattern: str, *, title: str, artist: str = "", album: str = "", track: int | None = None
) -> str:
    values = {
        "title": title.strip(),
        "artist": artist.strip(),
        "album": album.strip(),
        "track": f"{track:02d}" if track else "",
    }
    template = PATTERNS.get(pattern, pattern)
    pieces = re.split(r"(\{\w+\})", template)
    out: list[str] = []
    for piece in pieces:
        match = re.fullmatch(r"\{(\w+)\}", piece)
        out.append(values.get(match.group(1), "") if match else piece)
    stem = "".join(out)
    stem = re.sub(r"(?:\s+-(?=\s))+\s+", " - ", stem)
    stem = re.sub(r"^\s*-\s+|\s+-\s*$", "", stem)
    return sanitize_component(stem or values["title"] or "Untitled")


def fit_path(folder: Path, stem: str, suffix: str) -> Path:
    room = MAX_PATH_CHARS - len(str(folder)) - len(suffix) - 6
    if room < 8:
        room = 8
    if len(stem) > room:
        stem = stem[:room].rstrip(" .-")
    return folder / f"{stem}{suffix}"


def existing_names(folder: Path) -> set[str]:
    try:
        return {p.name.casefold() for p in folder.iterdir()}
    except OSError:
        return set()


def unique_path(path: Path, taken: set[str]) -> Path:
    candidate = path
    n = 2
    while candidate.name.casefold() in taken or candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    taken.add(candidate.name.casefold())
    return candidate
