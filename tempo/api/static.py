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


# Where to look for the built frontend, in order.
#
# Explicit rather than derived from the package's own location: whether
# ``tempo`` is installed as a copy or as a link decides where
# ``__file__`` points, and hanging the app's availability on that is how a
# green build ends up serving a 404. The working directory is the same in
# the container and in a checkout — /app and the repository root — so
# ``web/dist`` beneath it is one path that means one thing in both.
def candidates(override: Path | None = None) -> tuple[Path, ...]:
    """Where a build could be, most explicit first."""
    found: list[Path] = []
    if override is not None:
        found.append(override)
    found.append(Path.cwd() / "web" / "dist")
    # Beside the package, for an installation that carries the build with it.
    found.append(Path(__file__).resolve().parent.parent / "web")
    return tuple(found)


NEVER_CACHED: Final = frozenset({"sw.js", "manifest.webmanifest"})

# Paths that belong to the server, not to the app. An unknown path under
# one of these stays a 404: answering /api/gibt-es-nicht with the HTML
# shell turns a typo in a path into a parse error in the client, which is
# far harder to read than the 404 it actually is.
SERVER_PREFIXES: Final = ("api", "health", "docs", "openapi.json")

# The hashed assets are immutable: their name changes when they do.
IMMUTABLE_PREFIX: Final = "assets/"


def find_frontend(override: Path | None = None) -> Path | None:
    """The first candidate that actually holds a build."""
    for candidate in candidates(override):
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
            if missing.status_code != 404 or path.startswith(SERVER_PREFIXES):
                raise
            # Client-side routing: /trends is a route in the app, not a file.
            return await super().get_response("index.html", scope)


def mount_frontend(app: FastAPI, override: Path | None = None) -> bool:
    """Serve the built app at ``/``, if there is one. Returns whether there was.

    A deployment without a build is not broken — the API and /health work
    exactly as before — so this reports rather than raises.
    """
    root = find_frontend(override)
    if root is None:
        # Named, not just counted: when the app answers 404 at "/", this
        # line is the first thing worth reading.
        log.warning(
            "no frontend build found; serving the API only",
            extra={"looked_in": ", ".join(str(path) for path in candidates(override))},
        )
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
