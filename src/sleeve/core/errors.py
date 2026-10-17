import errno
from pathlib import Path


class SleeveError(Exception):
    """An error with a message that is safe to show in the UI."""

    def __init__(self, message: str, *, fatal: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.fatal = fatal


def disk_full(folder: Path) -> SleeveError:
    return SleeveError(
        f"The disk with '{folder}' is full. Free up some space and try again.", fatal=True
    )


def not_writable(folder: Path) -> SleeveError:
    return SleeveError(
        f"Can't save to '{folder}'. The folder is read-only. "
        "Choose another output folder in Settings.",
        fatal=True,
    )


def ffmpeg_missing(name: str) -> SleeveError:
    return SleeveError(
        f"Couldn't convert '{name}' because the audio converter (ffmpeg) is missing. "
        "Reinstall Sleeve to restore it."
    )


def unreadable(name: str) -> SleeveError:
    return SleeveError(
        f"Couldn't read '{name}'. The file may be damaged. "
        "Try exporting it again from your audio app."
    )


def protected(name: str) -> SleeveError:
    return SleeveError(
        f"'{name}' is copy-protected (DRM). Spotify can't play it, so it was skipped."
    )


def not_audio(name: str) -> SleeveError:
    return SleeveError(f"'{name}' isn't an audio file Sleeve can read, so it was skipped.")


def source_missing(name: str) -> SleeveError:
    return SleeveError(f"'{name}' was moved or deleted. Add it again to include it.")


def from_os_error(exc: OSError, folder: Path) -> SleeveError:
    if exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", -1)):
        return disk_full(folder)
    if exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
        return not_writable(folder)
    return SleeveError(f"Couldn't save to '{folder}' ({exc.strerror or 'unknown error'}).")
