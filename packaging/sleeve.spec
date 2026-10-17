# PyInstaller spec for Sleeve. Run: pyinstaller packaging/sleeve.spec --noconfirm
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"
VENDOR = ROOT / "packaging" / "vendor" / "ffmpeg"
ICONS = ROOT / "packaging" / "icons"
sys.path.insert(0, str(SRC))
from sleeve import APP_NAME, __version__  # noqa: E402

ffmpeg_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
ffmpeg = VENDOR / ffmpeg_name
if not ffmpeg.is_file():
    raise SystemExit("Run `python packaging/fetch_ffmpeg.py` first.")

hidden = collect_submodules("keyring.backends")
if sys.platform == "win32":
    hidden += ["win32ctypes.core"]

a = Analysis(
    [str(SRC / "sleeve" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=[(str(ffmpeg), "ffmpeg")],
    datas=[
        (str(SRC / "sleeve" / "resources" / "icon.png"), "resources"),
        (str(VENDOR / "SOURCE.txt"), "ffmpeg"),
        (str(ROOT / "THIRD_PARTY.md"), "."),
        (str(ROOT / "LICENSE"), "."),
    ],
    hiddenimports=hidden,
    excludes=["tkinter", "imageio_ffmpeg", "pytest", "PySide6.QtQml", "PySide6.QtQuick",
              "PySide6.QtWebEngineCore", "PySide6.QtMultimedia", "PySide6.Qt3DCore"],
    noarchive=False,
)
_UNUSED = ("Qt6Qml", "Qt6Quick", "VirtualKeyboard", "Qt6Pdf", "qpdf", "QtQml", "QtQuick")
a.binaries = [b for b in a.binaries if not any(part in b[0] for part in _UNUSED)]
a.datas = [d for d in a.datas if not any(part in d[0] for part in _UNUSED)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    icon=str(ICONS / ("sleeve.ico" if sys.platform == "win32" else "sleeve.icns")),
    codesign_identity=None,
    entitlements_file=str(ROOT / "packaging" / "macos" / "entitlements.plist"),
)

coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME, strip=False, upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=str(ICONS / "sleeve.icns"),
        bundle_identifier="com.jpark875.sleeve",
        version=__version__,
        info_plist={
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "LSMinimumSystemVersion": "11.0",
            "LSApplicationCategoryType": "public.app-category.music",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "NSHumanReadableCopyright": "Copyright 2026 Jason Park",
        },
    )
