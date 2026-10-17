import logging
import os
import shutil
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sleeve.core.errors import SleeveError, disk_full, unreadable

log = logging.getLogger(__name__)

_EXE = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Cancelled(Exception):
    pass


@dataclass(frozen=True)
class Encoding:
    codec: str
    bitrate_kbps: int
    extension: str

    @classmethod
    def mp3(cls, kbps: int = 320) -> "Encoding":
        return cls("libmp3lame", kbps, ".mp3")

    @classmethod
    def aac(cls, kbps: int = 256) -> "Encoding":
        return cls("aac", kbps, ".m4a")


def _bundled_candidates() -> list[Path]:
    roots = []
    if base := getattr(sys, "_MEIPASS", None):
        roots.append(Path(base))
    roots.append(Path(sys.executable).resolve().parent)
    if sys.platform == "darwin":
        roots.append(Path(sys.executable).resolve().parent.parent / "Resources")
    return [root / "ffmpeg" / _EXE for root in roots]


def find_ffmpeg() -> Path | None:
    if env := os.environ.get("SLEEVE_FFMPEG"):
        path = Path(env)
        return path if path.is_file() else None
    for candidate in _bundled_candidates():
        if candidate.is_file():
            return candidate
    if found := shutil.which("ffmpeg"):
        return Path(found)
    try:
        import imageio_ffmpeg

        return Path(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        return None


def build_command(ffmpeg: Path, src: Path, dst: Path, enc: Encoding, channels: int) -> list[str]:
    cmd = [
        str(ffmpeg), "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error",
        "-progress", "pipe:1", "-y", "-i", str(src),
        "-map", "0:a:0", "-vn", "-sn", "-map_metadata", "-1",
        "-c:a", enc.codec, "-b:a", f"{enc.bitrate_kbps}k",
    ]  # fmt: skip
    if channels > 2:
        cmd += ["-ac", "2"]
    if enc.codec == "aac":
        cmd += ["-movflags", "+faststart", "-f", "ipod"]
    else:
        cmd += ["-write_xing", "1", "-id3v2_version", "3", "-f", "mp3"]
    cmd.append(str(dst))
    return cmd


def convert(
    ffmpeg: Path,
    src: Path,
    dst: Path,
    enc: Encoding,
    *,
    duration: float = 0.0,
    channels: int = 0,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """Encode the first audio stream of src into dst. dst is only touched on success."""
    cmd = build_command(ffmpeg, src, dst, enc, channels)
    log.info("convert %s -> %s", src, dst)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stderr: deque[str] = deque(maxlen=40)
    reader = threading.Thread(target=lambda: stderr.extend(proc.stderr or []), daemon=True)
    reader.start()
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                proc.kill()
                raise Cancelled
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and value.isdigit() and duration > 0 and on_progress:
                on_progress(min(1.0, int(value) / 1_000_000 / duration))
        code = proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        reader.join(timeout=2)
    if cancel is not None and cancel.is_set():
        raise Cancelled
    if code != 0:
        detail = "".join(stderr)
        log.error("ffmpeg failed (%s) for %s:\n%s", code, src, detail)
        if "No space left" in detail:
            raise disk_full(dst.parent)
        raise unreadable(src.name)
    if on_progress:
        on_progress(1.0)


def check(ffmpeg: Path) -> SleeveError | None:
    try:
        out = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("ffmpeg check failed: %s", exc)
        return SleeveError("The audio converter (ffmpeg) won't start. Reinstall Sleeve.")
    if "libmp3lame" not in out.stdout:
        return SleeveError("The audio converter (ffmpeg) can't make MP3 files. Reinstall Sleeve.")
    return None
