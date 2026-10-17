from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import mutagen


class Kind(StrEnum):
    MP3 = "MP3"
    AAC = "AAC"
    ALAC = "ALAC"
    WAV = "WAV"
    AIFF = "AIFF"
    FLAC = "FLAC"
    VORBIS = "Ogg Vorbis"
    OPUS = "Opus"
    WMA = "WMA"
    ADTS = "AAC (raw)"
    MP4_OTHER = "MP4 audio"
    UNKNOWN = "Unknown"


class Action(StrEnum):
    COPY = "copy"
    CONVERT = "convert"
    SKIP = "skip"


AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".m4p", ".m4b", ".mp4", ".aac", ".wav", ".wave", ".aif", ".aiff",
    ".aifc", ".flac", ".ogg", ".oga", ".opus", ".wma", ".asf",
}  # fmt: skip

_ASF_HEADER = bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c")
_ASF_DRM = (
    bytes.fromhex("fbb31122 23bdd211 b4b700a0 c955fc6e"),
    bytes.fromhex("14e68a29 2226174c b935dae0 7ee9289c"),
)
_MP4_DRM_CODECS = {"drms", "enca", "drmi"}


@dataclass(frozen=True)
class Detected:
    kind: Kind
    protected: bool = False
    duration: float = 0.0
    channels: int = 0

    @property
    def action(self) -> Action:
        if self.protected or self.kind is Kind.UNKNOWN:
            return Action.SKIP
        if self.kind in (Kind.MP3, Kind.AAC):
            return Action.COPY
        return Action.CONVERT

    @property
    def extension(self) -> str:
        return ".m4a" if self.kind is Kind.AAC else ".mp3"


def _sniff(head: bytes) -> Kind:
    if head[:3] == b"ID3":
        return Kind.MP3
    if len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0:
        return Kind.ADTS if head[1] & 0x06 == 0 else Kind.MP3
    if head[4:8] == b"ftyp":
        return Kind.AAC
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return Kind.WAV
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return Kind.AIFF
    if head[:4] == b"fLaC":
        return Kind.FLAC
    if head[:4] == b"OggS":
        return Kind.OPUS if b"OpusHead" in head else Kind.VORBIS
    if head[:16] == _ASF_HEADER:
        return Kind.WMA
    return Kind.UNKNOWN


def detect(path: Path) -> Detected:
    """Identify a file by its contents, not its extension."""
    with path.open("rb") as fh:
        head = fh.read(64 * 1024)
    kind = _sniff(head)
    if kind is Kind.UNKNOWN:
        return Detected(kind)
    protected = False
    if kind is Kind.WMA:
        protected = any(guid in head for guid in _ASF_DRM)
    try:
        info = mutagen.File(path)
    except Exception:
        info = None
    duration = float(getattr(getattr(info, "info", None), "length", 0.0) or 0.0)
    channels = int(getattr(getattr(info, "info", None), "channels", 0) or 0)
    if kind is Kind.AAC:
        codec = str(getattr(getattr(info, "info", None), "codec", "") or "")
        if codec in _MP4_DRM_CODECS or head[8:12] == b"M4P ":
            protected = True
        elif info is None or not codec:
            kind = Kind.UNKNOWN
        elif codec == "alac":
            kind = Kind.ALAC
        elif not codec.startswith("mp4a.40"):
            kind = Kind.MP4_OTHER
    return Detected(kind, protected, duration, channels)


def is_candidate(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS and not path.name.startswith("._")
