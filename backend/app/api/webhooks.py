"""POST /webhooks/pawapay: pawaPay deposit callbacks. Outside app auth and CSRF by design;
protected by signature verification (or confirmation via the status API in hint mode)."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..services.callbacks import process_callback

bp = Blueprint("webhooks", __name__)

WEBHOOK_PATH = "/webhooks/pawapay"


def request_authority() -> str:
    """The host pawaPay called, for "@authority". Behind Railway or an ALB the original Host
    arrives in X-Forwarded-Host; otherwise use Host as received."""
    forwarded = request.headers.get("X-Forwarded-Host")
    host = forwarded.split(",")[0].strip() if forwarded else (request.headers.get("Host") or request.host)
    return host.lower()


def raw_path() -> str:
    """The path exactly as sent on the request line (still percent-encoded), without query."""
    raw = request.environ.get("RAW_URI") or request.environ.get("REQUEST_URI")
    if raw:
        if "://" in raw:  # absolute-form request target
            raw = "/" + raw.split("://", 1)[1].split("/", 1)[-1]
        return raw.split("?", 1)[0] or "/"
    return request.path


@bp.post(WEBHOOK_PATH)
def pawapay_callback():
    raw = request.get_data(cache=True)  # raw bytes first: Content-Digest is over these exact bytes
    headers = {key: value for key, value in request.headers.items()}
    result = process_callback(
        raw,
        headers,
        method=request.method,
        authority=request_authority(),
        path=raw_path(),
        query=request.query_string.decode("latin-1"),
        scheme=request.headers.get("X-Forwarded-Proto", request.scheme).split(",")[0].strip(),
        source="callback",
    )
    body = {"outcome": result.outcome, "signature": result.signature}
    if result.http_status >= 400:
        body["error"] = {"code": result.outcome, "message": result.detail}
    return jsonify(body), result.http_status
