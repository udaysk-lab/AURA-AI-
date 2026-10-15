"""Build the AURA AI desktop app for the platform this runs on.

    python desktop/build.py              web app + app + archive
    python desktop/build.py --skip-web   reuse frontend/out (CI builds it once)

Needs Node 18+ (for the web app) and a Python environment with the backend's
requirements, the device extras and PyInstaller:

    pip install -r backend/requirements.txt -r backend/requirements-device.txt pyinstaller

Output, in build/desktop/:

    Windows   AURA AI/                         the app folder (the installer wraps it)
              AURA-AI-Windows-x64-portable.zip
    macOS     AURA AI.app
              AURA-AI-macOS-AppleSilicon.zip    (or -Intel on an Intel Mac)
    Linux     aura-ai/
              AURA-AI-Linux-x64.tar.gz

The Windows installer is built from the app folder by desktop/windows/aura.iss
(Inno Setup); the release workflow does that step.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
OUT = ROOT / "build" / "desktop"


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, env=env, check=True, shell=(os.name == "nt" and cmd[0] == "npm"))


def build_web() -> Path:
    if not (FRONTEND / "node_modules").is_dir():
        run(["npm", "ci", "--no-audit", "--no-fund"], cwd=FRONTEND)
    run(["npm", "run", "build:desktop"], cwd=FRONTEND)
    web = FRONTEND / "out"
    if not (web / "index.html").is_file():
        raise SystemExit("The web build finished but frontend/out/index.html is missing.")
    return web


def asset_name() -> str:
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return "AURA-AI-Windows-x64-portable.zip"
    if sys.platform == "darwin":
        return "AURA-AI-macOS-AppleSilicon.zip" if machine in ("arm64", "aarch64") else "AURA-AI-macOS-Intel.zip"
    return "AURA-AI-Linux-x64.tar.gz" if machine in ("x86_64", "amd64") else f"AURA-AI-Linux-{machine}.tar.gz"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def package(dist: Path) -> Path:
    archive = OUT / asset_name()
    if archive.exists():
        archive.unlink()

    if sys.platform == "darwin":
        app = dist / "AURA AI.app"
        # ditto keeps the symlinks and extended attributes a .app relies on;
        # a plain zip of a bundle can come out unopenable.
        run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(archive)])
        shutil.rmtree(OUT / "AURA AI.app", ignore_errors=True)
        shutil.copytree(app, OUT / "AURA AI.app", symlinks=True)
    elif sys.platform == "win32":
        folder = dist / "AURA AI"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for path in sorted(folder.rglob("*")):
                zf.write(path, Path("AURA AI") / path.relative_to(folder))
        shutil.rmtree(OUT / "AURA AI", ignore_errors=True)
        shutil.copytree(folder, OUT / "AURA AI")
    else:
        folder = dist / "aura-ai"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(folder, arcname="aura-ai")
        shutil.rmtree(OUT / "aura-ai", ignore_errors=True)
        shutil.copytree(folder, OUT / "aura-ai", symlinks=True)
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-web", action="store_true", help="use the existing frontend/out")
    args = parser.parse_args()

    web = FRONTEND / "out" if args.skip_web else build_web()
    if not (web / "index.html").is_file():
        raise SystemExit("frontend/out is missing — run without --skip-web.")

    OUT.mkdir(parents=True, exist_ok=True)
    work = ROOT / "build" / "pyinstaller"
    dist = work / "dist"
    env = {**os.environ, "AURA_WEB_BUILD": str(web)}
    run(
        [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--distpath", str(dist), "--workpath", str(work / "work"),
            str(ROOT / "desktop" / "aura.spec"),
        ],
        cwd=ROOT,
        env=env,
    )

    archive = package(dist)
    size_mb = archive.stat().st_size / 1_000_000
    print(f"\nBuilt {archive}  ({size_mb:.1f} MB)\nsha256 {sha256(archive)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
