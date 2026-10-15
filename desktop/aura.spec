# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller recipe for the AURA AI desktop app.
#
# Don't run this directly — use desktop/build.py, which builds the web app
# first, runs this, and packages the result for the platform it's on.
#
# One folder, not one file: a one-file build unpacks itself to a temp folder on
# every launch (slow, and antivirus tools dislike it). The folder is what the
# installer copies, or what the portable zip contains.

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
BACKEND = ROOT / "backend"
ASSETS = ROOT / "desktop" / "assets"
WEB = Path(os.environ.get("AURA_WEB_BUILD") or (ROOT / "frontend" / "out")).resolve()

if not (WEB / "index.html").is_file():
    raise SystemExit(
        f"No built web app at {WEB}. Run `npm run build:desktop` in frontend/ "
        "(or set AURA_WEB_BUILD to an exported build)."
    )

sys.path.insert(0, str(BACKEND))
from app.version import __version__  # noqa: E402

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
NAME = "aura-ai" if sys.platform.startswith("linux") else "AURA AI"

hiddenimports = (
    collect_submodules("app")
    + collect_submodules("uvicorn")
    + ["worker", "tzdata"]
)

datas = [
    (str(WEB), "web"),
    (str(ASSETS / "aura-icon.png"), "assets"),
]
datas += collect_data_files("tzdata")
datas += collect_data_files("certifi")

# Not needed on a desktop install, and each adds weight: the desktop app keeps
# its data in SQLite and its rate limits in memory, and ships no tests.
excludes = [
    "psycopg",
    "psycopg_binary",
    "psycopg_c",
    "redis",
    "pytest",
    "_pytest",
    "IPython",
    "matplotlib",
    "numpy",
    "alembic",
    "mako",
]

a = Analysis(
    [str(BACKEND / "aura_desktop.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    debug=False,
    strip=False,
    upx=False,
    # The app's face is its small window; a console behind it would only
    # confuse. Logs go to ~/.aura/logs instead.
    console=False,
    disable_windowed_traceback=False,
    icon=str(ASSETS / ("aura.ico" if IS_WINDOWS else "aura.icns")),
    version=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=NAME,
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name="AURA AI.app",
        icon=str(ASSETS / "aura.icns"),
        bundle_identifier="ai.aura.desktop",
        version=__version__,
        info_plist={
            "CFBundleName": "AURA AI",
            "CFBundleDisplayName": "AURA AI",
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            # Asked when AURA closes an app for you ("quit app ..." via osascript).
            "NSAppleEventsUsageDescription": (
                "AURA asks other apps to quit when you tell it to close them."
            ),
        },
    )
