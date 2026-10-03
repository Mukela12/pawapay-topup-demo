"""Serve the built React SPA (frontend/dist) from the same origin as the API.

- /assets/* is served from dist/assets with long-lived caching and explicit MIME types.
- Files at the dist root (favicon, icons/*.json) are served as files.
- Any other GET outside /api, /webhooks, /internal and /healthz returns index.html so
  client-side routes like /wallet and /console/topups/<id> work on reload.
- Unknown /api paths always get a JSON 404, never the SPA.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path

from flask import Blueprint, Flask, current_app, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException, MethodNotAllowed, NotFound

for _type, _ext in (
    ("text/javascript", ".js"),
    ("text/javascript", ".mjs"),
    ("text/css", ".css"),
    ("application/json", ".json"),
    ("image/svg+xml", ".svg"),
    ("font/woff2", ".woff2"),
    ("font/woff", ".woff"),
    ("application/wasm", ".wasm"),
    ("image/webp", ".webp"),
    ("application/manifest+json", ".webmanifest"),
):
    mimetypes.add_type(_type, _ext)

API_PREFIXES = ("/api/", "/webhooks/", "/internal/")
API_EXACT = ("/api", "/webhooks", "/internal", "/healthz")

bp = Blueprint("spa", __name__)


def is_api_path(path: str) -> bool:
    return path in API_EXACT or path.startswith(API_PREFIXES)


def dist_dir() -> Path:
    configured = current_app.config.get("FRONTEND_DIST")
    if configured:
        return Path(configured).resolve()
    here = Path(__file__).resolve()
    candidates = (
        here.parents[2] / "frontend" / "dist",  # repo checkout or Docker: <root>/frontend/dist
        here.parents[1] / "frontend" / "dist",  # Elastic Beanstalk bundle: backend is the root
    )
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return candidates[0]


def _index():
    dist = dist_dir()
    index = dist / "index.html"
    if not index.is_file():
        return (
            jsonify(
                {
                    "service": "ringwise-api",
                    "message": "API is running. The frontend build (frontend/dist) is not present.",
                }
            ),
            200 if request.path == "/" else 404,
        )
    response = send_from_directory(dist, "index.html", max_age=0)
    response.headers["Cache-Control"] = "no-cache"
    return response


@bp.get("/assets/<path:filename>")
def assets(filename: str):
    assets_dir = dist_dir() / "assets"
    if not assets_dir.is_dir():
        raise NotFound()
    response = send_from_directory(assets_dir, filename, max_age=31536000)
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]


@bp.route("/", defaults={"path": ""}, methods=ALL_METHODS)
@bp.route("/<path:path>", methods=ALL_METHODS)
def spa(path: str):
    full = "/" + path
    if is_api_path(full):
        raise NotFound()  # unknown API path: JSON 404 from the error handler, never the SPA
    if request.method not in ("GET", "HEAD"):
        raise MethodNotAllowed(valid_methods=["GET", "HEAD"])
    dist = dist_dir()
    if path:
        candidate = (dist / path).resolve()
        try:
            candidate.relative_to(dist)
        except ValueError:
            raise NotFound() from None
        if candidate.is_file():
            return send_from_directory(dist, path, max_age=3600)
        if "." in path.rsplit("/", 1)[-1]:
            raise NotFound()  # a missing file, not a client route
    return _index()


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(HTTPException)
    def _http_error(exc: HTTPException):
        if is_api_path(request.path) or request.method != "GET" or request.accept_mimetypes.best == "application/json":
            code = (exc.name or "error").lower().replace(" ", "_")
            return jsonify({"error": {"code": code, "message": exc.description}}), exc.code
        return exc

    @app.errorhandler(Exception)
    def _unhandled(exc: Exception):
        if isinstance(exc, HTTPException):
            return _http_error(exc)
        current_app.logger.exception("unhandled error on %s %s", request.method, request.path)
        return (
            jsonify({"error": {"code": "internal_error", "message": "Something went wrong on the server."}}),
            500,
        )
