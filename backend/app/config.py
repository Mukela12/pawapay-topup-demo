"""Configuration, read from environment variables only.

Nothing secret has a default. When PAWAPAY_API_TOKEN is unset the app still boots:
the customer API answers 503 and the console works read-only.
"""
from __future__ import annotations

import os
from datetime import timedelta
from urllib.parse import urlsplit

DEFAULT_DATABASE_URL = "postgresql:///ringwise_dev"
SANDBOX_BASE_URL = "https://api.sandbox.pawapay.io"


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def env_str(name: str, default: str | None = None) -> str | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def normalize_database_url(url: str) -> str:
    """Point SQLAlchemy at psycopg 3 whatever scheme the platform hands us.

    Railway and Heroku style URLs start with postgres:// or postgresql://, which
    SQLAlchemy would map to psycopg2. We only ship psycopg 3.
    """
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def default_cookie_secure(public_base_url: str | None) -> bool:
    """Secure session cookies unless this is clearly local development over plain http.

    Local means PUBLIC_BASE_URL is an http:// loopback address (backend/.env.example sets
    http://127.0.0.1:5001), or PUBLIC_BASE_URL is unset and FLASK_DEBUG is on. Everything else,
    including a production deploy that forgot PUBLIC_BASE_URL, gets Secure. SESSION_COOKIE_SECURE
    overrides this either way.
    """
    if public_base_url:
        parts = urlsplit(public_base_url)
        host = (parts.hostname or "").lower()
        is_loopback = host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost")
        return not (parts.scheme == "http" and is_loopback)
    return not env_bool("FLASK_DEBUG", False)


def build_config() -> dict:
    public_base_url = env_str("PUBLIC_BASE_URL")
    commit = env_str("GIT_COMMIT") or env_str("RAILWAY_GIT_COMMIT_SHA") or "dev"
    return {
        "SECRET_KEY": env_str("SECRET_KEY"),
        "SQLALCHEMY_DATABASE_URI": normalize_database_url(
            env_str("DATABASE_URL", DEFAULT_DATABASE_URL)
        ),
        "SQLALCHEMY_ENGINE_OPTIONS": {
            "pool_pre_ping": True,
            "pool_size": 5,
            "max_overflow": 5,
            "pool_recycle": 1800,
        },
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        # pawaPay
        "PAWAPAY_API_TOKEN": env_str("PAWAPAY_API_TOKEN"),
        "PAWAPAY_BASE_URL": (env_str("PAWAPAY_BASE_URL", SANDBOX_BASE_URL) or "").rstrip("/"),
        "PAWAPAY_REQUIRE_SIGNATURE": env_bool("PAWAPAY_REQUIRE_SIGNATURE", False),
        "PAWAPAY_CONNECT_TIMEOUT": 5.0,
        "PAWAPAY_READ_TIMEOUT": 15.0,
        # Reconciliation and limits
        "RECONCILE_AFTER_SECONDS": env_int("RECONCILE_AFTER_SECONDS", 900),
        "RECONCILE_BATCH_LIMIT": env_int("RECONCILE_BATCH_LIMIT", 50),
        "OPEN_TOPUP_LOCK_SECONDS": env_int("OPEN_TOPUP_LOCK_SECONDS", 600),
        "PHONE_HOURLY_CAP": env_int("PHONE_HOURLY_CAP", 5),
        "CUSTOMER_DAILY_CAP": env_int("CUSTOMER_DAILY_CAP", 20),
        "SIGNATURE_CLOCK_SKEW_SECONDS": 30,
        "SIGNATURE_MAX_AGE_SECONDS": 300,
        # Ops
        "INTERNAL_TOKEN": env_str("INTERNAL_TOKEN"),
        "EB_WORKER": env_bool("EB_WORKER", False),
        "PUBLIC_BASE_URL": public_base_url,
        "GIT_COMMIT": commit[:12],
        "SCHEDULER_ENABLED": env_bool("SCHEDULER_ENABLED", True),
        "TRUST_PROXY_HOPS": env_int("TRUST_PROXY_HOPS", 1),
        "FRONTEND_DIST": env_str("FRONTEND_DIST"),
        # Cookies: signed Flask session, HttpOnly, SameSite=Lax, Secure unless local http.
        "SESSION_COOKIE_NAME": "ringwise_session",
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": env_bool("SESSION_COOKIE_SECURE", default_cookie_secure(public_base_url)),
        "PERMANENT_SESSION_LIFETIME": timedelta(days=30),
        "MAX_CONTENT_LENGTH": 1024 * 1024,
    }
