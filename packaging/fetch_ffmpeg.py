"""Download a pinned ffmpeg build into packaging/vendor/ffmpeg/ for PyInstaller to bundle.

The binaries come from the imageio-ffmpeg wheels on PyPI, checked against known SHA-256 sums.
Usage: python packaging/fetch_ffmpeg.py [macos-arm64|macos-x86_64|windows-x86_64]
"""

import hashlib
import io
import platform
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

VERSION = "0.6.0"
WHEELS = {
    "macos-arm64": (
        "macosx_11_0_arm64",
        "b1ae3173414b5fc5f538a726c4e48ea97edc0d2cdc11f103afee655c463fa742",
    ),
    "macos-x86_64": (
        "macosx_10_9_x86_64",
        "9d2baaf867088508d4a3458e61eeb30e945c4ad8016025545f66c4b5aaef0a61",
    ),
    "windows-x86_64": (
        "win_amd64",
        "02fa47c83703c37df6bfe4896aab339013f62bf02c5ebf2dce6da56af04ffc0a",
    ),
}
VENDOR = Path(__file__).resolve().parent / "vendor" / "ffmpeg"


def current_target() -> str:
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        return "macos-arm64" if machine in ("arm64", "aarch64") else "macos-x86_64"
    if sys.platform == "win32":
        return "windows-x86_64"
    raise SystemExit("Build on macOS or Windows, or pass a target explicitly.")


def fetch(target: str) -> Path:
    tag, expected = WHEELS[target]
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [sys.executable, "-m", "pip", "download", f"imageio-ffmpeg=={VERSION}", "--no-deps",
             "--only-binary=:all:", "--platform", tag, "-d", tmp, "-q"],
            check=True,
        )  # fmt: skip
        wheel = next(Path(tmp).glob("*.whl"))
        data = wheel.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise SystemExit(f"Checksum mismatch for {wheel.name}: {actual}")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        member = next(n for n in zf.namelist() if "/binaries/ffmpeg-" in n)
        binary = zf.read(member)
    VENDOR.mkdir(parents=True, exist_ok=True)
    out = VENDOR / ("ffmpeg.exe" if target.startswith("windows") else "ffmpeg")
    out.write_bytes(binary)
    out.chmod(0o755)
    (VENDOR / "SOURCE.txt").write_text(
        f"ffmpeg from imageio-ffmpeg {VERSION} ({wheel.name}, {member})\n"
        "Build details and source: https://github.com/imageio/imageio-binaries and "
        "https://ffmpeg.org/download.html\n"
    )
    return out


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else current_target()
    if target not in WHEELS:
        raise SystemExit(f"Unknown target {target!r}; choose from {', '.join(WHEELS)}")
    print(f"Saved {fetch(target)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
