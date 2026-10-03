from __future__ import annotations

import uuid
from typing import Any

from flask import jsonify, request, session

from ..pawapay.client import is_configured


def api_error(code: str, message: str, status: int, field: str | None = None, **extra: Any):
    err: dict[str, Any] = {"code": code, "message": message}
    if field:
        err["field"] = field
    body: dict[str, Any] = {"error": err}
    body.update(extra)
    return jsonify(body), status


def not_configured_error():
    return api_error(
        "pawapay_not_configured",
        "pawaPay is not configured on this server (PAWAPAY_API_TOKEN is unset), so top-ups are "
        "switched off. The console still works read-only.",
        503,
    )


def require_configured():
    return None if is_configured() else not_configured_error()


def json_body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def session_customer_id() -> uuid.UUID | None:
    raw = session.get("customer_id")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


def parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def int_arg(name: str, default: int, minimum: int = 1, maximum: int = 500) -> int:
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))
