"""Ringwise demo backend: Flask app factory."""
from __future__ import annotations

import logging
import os
import secrets
import threading
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import build_config
from .extensions import db, migrate

BACKEND_DIR = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = BACKEND_DIR / "migrations"

log = logging.getLogger(__name__)


def _configure_logging() -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=os.environ.get("LOG_LEVEL", "INFO"),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    # httpx logs full request lines at INFO; our client logs its own concise line instead.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def create_app(overrides: dict | None = None) -> Flask:
    load_dotenv(BACKEND_DIR / ".env", override=False)
    _configure_logging()

    app = Flask(__name__, static_folder=None)
    app.json.sort_keys = False  # keep response keys in the order we build them
    app.config.update(build_config())
    if overrides:
        app.config.update(overrides)

    if not app.config.get("SECRET_KEY"):
        # Sessions still work, but cookies will not survive a restart or be shared across
        # workers. Always set SECRET_KEY outside local development.
        app.config["SECRET_KEY"] = secrets.token_hex(32)
        log.warning("SECRET_KEY is not set; using a random per-process key")

    hops = int(app.config.get("TRUST_PROXY_HOPS") or 0)
    if hops > 0:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops)  # type: ignore[method-assign]

    db.init_app(app)
    migrate.init_app(app, db, directory=str(MIGRATIONS_DIR), compare_type=True)

    from . import (
        models,  # noqa: F401  (register tables with the metadata)
        spa,
    )
    from .api import console, customer, internal, webhooks

    app.register_blueprint(customer.bp)
    app.register_blueprint(console.bp)
    app.register_blueprint(webhooks.bp)
    app.register_blueprint(internal.bp)
    app.register_blueprint(spa.bp)
    spa.register_error_handlers(app)

    if app.config.get("SCHEDULER_ENABLED"):
        _start_scheduler_on_first_request(app)

    if not app.config.get("PAWAPAY_API_TOKEN"):
        log.warning("PAWAPAY_API_TOKEN is not set: customer top-ups return 503, console is read-only")
    if not app.config.get("SESSION_COOKIE_SECURE") and os.environ.get("PORT"):
        # PORT is set by Railway and similar platforms: probably a deployed instance.
        log.warning(
            "SESSION_COOKIE_SECURE is off while PORT is set: the session cookie is sent without "
            "Secure. Set PUBLIC_BASE_URL to the https URL, or SESSION_COOKIE_SECURE=true."
        )
    return app


def _start_scheduler_on_first_request(app: Flask) -> None:
    """Start APScheduler lazily in a serving process, never in CLI commands like flask db."""
    started = threading.Event()

    @app.before_request
    def _ensure_scheduler() -> None:
        if started.is_set():
            return
        started.set()
        from .scheduler import start_scheduler

        start_scheduler(app)
