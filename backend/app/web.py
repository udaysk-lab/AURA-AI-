"""Serve the web app from the API's own address.

The desktop app is one process: this backend, plus the frontend exported as
static files (`npm run build:desktop`). Serving both from one origin means no
second server, no CORS, and no NEXT_PUBLIC_API_URL to get wrong — the frontend
already talks to its own origin when that variable is unset.

Only installed when AURA_WEB_DIR points at an exported build. The hosted site
never sets it: Vercel serves the frontend itself.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

log = logging.getLogger("aura.web")

# Spelled out rather than left to `mimetypes`: on Windows that module reads the
# registry, where .js is sometimes registered as text/plain — and with
# X-Content-Type-Options: nosniff the browser then refuses to run the app's own
# scripts, leaving a blank window.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".webmanifest": "application/manifest+json",
}

# Paths that belong to the API. A miss under these is the API's 404, never the
# web app's 404 page.
RESERVED = ("api", "health", "docs", "redoc", "openapi.json")


def is_reserved(path: str) -> bool:
    first = path.strip("/").split("/", 1)[0]
    return first in RESERVED


def content_type(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _inside(root: Path, relative: str) -> Path | None:
    """The file `relative` names under `root`, or None.

    Resolving first collapses `..` and follows links, so no spelling of a path
    — encoded, doubled, backslashed — gets out of the web folder.
    """
    if "\x00" in relative:
        return None
    try:
        candidate = (root / relative).resolve()
        candidate.relative_to(root)
    except (ValueError, OSError):
        return None
    return candidate if candidate.is_file() else None


def find(root: Path, path: str) -> tuple[Path | None, int]:
    """Map a URL path onto the export: /chat → chat.html, / → index.html."""
    relative = path.strip("/")
    if not relative:
        hit = _inside(root, "index.html")
        return hit, 200 if hit else 404
    for option in (relative, f"{relative}.html", f"{relative}/index.html"):
        hit = _inside(root, option)
        if hit:
            return hit, 200
    return _inside(root, "404.html"), 404


def install(app: FastAPI, web_dir: str) -> bool:
    """Add the routes that serve the exported web app. Call after every API route."""
    root = Path(web_dir).expanduser().resolve()
    if not (root / "index.html").is_file():
        log.warning("AURA_WEB_DIR=%s has no index.html; not serving the web app.", web_dir)
        return False

    def serve(path: str):
        if is_reserved(path):
            raise HTTPException(status_code=404, detail="Not found")
        file, status = find(root, path)
        if file is None:
            raise HTTPException(status_code=404, detail="Not found")
        # Next names everything under _next/static by its content hash, so it
        # can be cached for good. Pages must be re-checked, or an update to
        # the app would keep showing the old screens.
        cache = (
            "public, max-age=31536000, immutable"
            if path.startswith("_next/static/")
            else "no-cache"
        )
        return FileResponse(
            file,
            status_code=status,
            media_type=content_type(file),
            headers={"Cache-Control": cache},
        )

    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    def web_index():
        return serve("")

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def web_path(path: str):
        return serve(path)

    log.info("Serving the web app from %s", root)
    return True
