import os
import struct
import zlib
from pathlib import Path

import pytest

DATA = Path(__file__).parent / "data"


def png_bytes(width: int = 2, height: int = 2) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    raw = b"".join(b"\x00" + b"\xcc\x33\x33" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("SLEEVE_HOME", str(home))
    return home


@pytest.fixture
def data_dir() -> Path:
    return DATA


@pytest.fixture(scope="session")
def ffmpeg_path() -> Path:
    from sleeve.core.ffmpeg import find_ffmpeg

    found = find_ffmpeg()
    if found is None:
        pytest.skip("ffmpeg not available")
    return found


def pytest_configure() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
