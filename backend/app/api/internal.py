"""Ops endpoints: health check and the Elastic Beanstalk worker cron target."""
from __future__ import annotations

import hmac
import ipaddress
import logging

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import text

from ..extensions import db
from ..pawapay.client import is_configured
from ..services.reconcile import reconcile_once
from .common import api_error

log = logging.getLogger(__name__)

bp = Blueprint("internal", __name__)


@bp.get("/healthz")
def healthz():
    db_ok = True
    try:
        db.session.execute(text("SELECT 1"))
        db.session.rollback()
    except Exception:
        db.session.rollback()
        log.exception("healthz: database check failed")
        db_ok = False
    cfg = current_app.config
    body = {
        "ok": db_ok,
        "db": db_ok,
        "pawapay_configured": is_configured(),
        "signature_required": bool(cfg.get("PAWAPAY_REQUIRE_SIGNATURE")),
        "commit": cfg.get("GIT_COMMIT"),
    }
    return jsonify(body), 200 if db_ok else 503


def _is_loopback(addr: str | None) -> bool:
    try:
        ip = ipaddress.ip_address((addr or "").strip())
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def _caller_is_local() -> bool:
    """True only for a request that originated on this machine, whatever proxy sits in front.

    Three checks, all required:
    - the TCP peer (kept by ProxyFix in werkzeug.proxy_fix.orig) is loopback, so a caller that
      reaches gunicorn directly cannot pass by sending "X-Forwarded-For: 127.0.0.1";
    - request.remote_addr (the client ProxyFix resolved) is loopback;
    - every X-Forwarded-For entry is loopback. Behind EB's nginx the TCP peer is always nginx,
      and nginx appends the real peer, so an outside caller always leaves a non-loopback entry.
      This also holds if TRUST_PROXY_HOPS is set wrong for the worker.
    sqsd posts from localhost, so it passes directly or through nginx.
    """
    socket_peer = request.environ.get("werkzeug.proxy_fix.orig", {}).get("REMOTE_ADDR", request.remote_addr)
    forwarded = [part for part in request.headers.get("X-Forwarded-For", "").split(",") if part.strip()]
    return (
        _is_loopback(socket_peer)
        and _is_loopback(request.remote_addr)
        and all(_is_loopback(part) for part in forwarded)
    )


def _authorized() -> bool:
    expected = current_app.config.get("INTERNAL_TOKEN")
    supplied = request.headers.get("X-Internal-Token")
    if expected and supplied and hmac.compare_digest(expected.encode(), supplied.encode()):
        return True
    # EB worker tier: the SQS daemon (sqsd) posts cron.yaml jobs to localhost.
    if current_app.config.get("EB_WORKER") and _caller_is_local():
        return True
    return False


@bp.post("/internal/reconcile")
def internal_reconcile():
    if not _authorized():
        return api_error("forbidden", "Missing or wrong X-Internal-Token.", 403)
    if not is_configured():
        # 200 so the EB worker daemon does not retry a job that cannot run.
        return jsonify({"skipped": "pawapay_not_configured"}), 200
    trigger = "eb_cron" if request.headers.get("X-Aws-Sqsd-Taskname") or current_app.config.get("EB_WORKER") else "manual"
    return jsonify({"run": reconcile_once(trigger)})
