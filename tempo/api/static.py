"""Serving the built frontend, and the two files that must not be cached.

One container, one origin: the API and the app come from the same place, so
the session cookie works without a CORS story and the service worker's scope
covers everything it needs to.

``sw.js`` and ``manifest.webmanifest`` are sent with ``no-store``. A cached
service worker cannot be replaced by a newer one — the browser keeps
serving the old app forever — and a cached manifest freezes the install
prompt on whatever it said the first time. The build already keeps both out
of the precache; this is the other half, for the ordinary HTTP cache.
"""

from __future__ import annotations

import logging
import os
from os import PathLike
from pathlib import Path
from typing import Final

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

log = logging.getLogger(__name__)

# Where the frontend build lands. Beside the package in the container, and
# two levels up in a checkout.
CANDIDATES: Final = (
    Path(__file__).resolve().parent.parent / "web",
    Path(__file__).resolve().parents[2] / "web" / "dist",
)

NEVER_CACHED: Final = frozenset({"sw.js", "manifest.webmanifest"})

# The hashed assets are immutable: their name changes when they do.
IMMUTABLE_PREFIX: Final = "assets/"


def find_frontend() -> Path | None:
    for candidate in CANDIDATES:
        if (candidate / "index.html").is_file():
            return candidate
    return None


class FrontendFiles(StaticFiles):
    """Static files with Tempo's caching rules, and SPA routing."""

    def file_response(
        self,
        full_path: PathLike[str] | str,
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        path = str(full_path)
        if Path(path).name in NEVER_CACHED:
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        elif IMMUTABLE_PREFIX in path.replace("\\", "/"):
            # Hashed file names: the name changes when the content does.
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            # index.html above all: revalidated every time, so a deploy is
            # visible on the next load rather than on the next cache expiry.
            response.headers["Cache-Control"] = "no-cache"
        return response

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as missing:
            if missing.status_code != 404 or path.startswith(("api", "health")):
                # An unknown endpoint stays a 404. Answering it with the
                # HTML shell would turn a typo in a path into a parse error
                # in the client, which is far harder to read.
                raise
            # Client-side routing: /trends is a route in the app, not a file.
            return await super().get_response("index.html", scope)


def mount_frontend(app: FastAPI) -> bool:
    """Serve the built app at ``/``, if there is one. Returns whether there was.

    A deployment without a build is not broken — the API and /health work
    exactly as before — so this reports rather than raises.
    """
    root = find_frontend()
    if root is None:
        log.info("no frontend build found; serving the API only")
        return False

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest() -> FileResponse:
        return FileResponse(
            root / "manifest.webmanifest",
            media_type="application/manifest+json",
            headers={"Cache-Control": "no-store, must-revalidate"},
        )

    @app.get("/sw.js", include_in_schema=False)
    def service_worker() -> FileResponse:
        return FileResponse(
            root / "sw.js",
            media_type="text/javascript",
            headers={
                "Cache-Control": "no-store, must-revalidate",
                # The worker controls the whole origin, not just /assets.
                "Service-Worker-Allowed": "/",
            },
        )

    app.mount("/", FrontendFiles(directory=root, html=True), name="frontend")
    log.info("serving the frontend", extra={"root": str(root)})
    return True
