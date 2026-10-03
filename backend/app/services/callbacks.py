"""The real callback handler. Used by POST /webhooks/pawapay and by the console's replay and
forged-callback actions, so the demo exercises exactly the code path pawaPay hits.

Decision table:
- Signature, Signature-Input and Content-Digest present and valid: apply the body directly.
- Signature missing or invalid:
    PAWAPAY_REQUIRE_SIGNATURE true (or forced): 401, event outcome "rejected".
    otherwise: the callback is only a HINT. We confirm with GET /v2/deposits/{id} and apply
    what pawaPay's API says (including FAILED), never what the unsigned body says.
- Console replay (replay=True): stored callbacks keep no Signature header, so a replay can
  never re-verify. It always runs in hint mode, even when signatures are required, and its
  event records signature "not_checked".
- Always store a payment_event. 200 for applied, duplicate, held and no_change.
- Bodies are untrusted until verified: nesting deeper than MAX_JSON_DEPTH counts as malformed,
  and U+0000 (which PostgreSQL cannot store) is replaced before anything is stored or applied.
  Signature and digest checks always use the original bytes.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Mapping

from flask import current_app
from sqlalchemy import select

from ..extensions import db
from ..models import Topup
from ..pawapay.client import (
    PawaPayError,
    PawaPayTransportError,
    get_client,
    is_configured,
)
from ..pawapay.signatures import RequestView, verify_request
from .events import EventInfo, pick_headers, record_event, scrub_nul
from .topups import apply_result

log = logging.getLogger(__name__)

# pawaPay callbacks nest three levels deep. Anything far deeper is not a pawaPay payload, and
# bounding it keeps every later recursive step (scrubbing, JSON encoding for JSONB) safe.
MAX_JSON_DEPTH = 32


@dataclass
class CallbackResult:
    http_status: int
    outcome: str
    signature: str
    detail: str | None = None
    topup_status: str | None = None
    event_id: uuid.UUID | None = None
    signature_reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "http_status": self.http_status,
            "outcome": self.outcome,
            "signature": self.signature,
            "signature_reason": self.signature_reason,
            "detail": self.detail,
            "topup_status": self.topup_status,
            "event_id": str(self.event_id) if self.event_id else None,
        }


def _key_resolver(keyid: str) -> str | None:
    return get_client().public_key_for(keyid)


def _too_deep(value: object, limit: int = MAX_JSON_DEPTH) -> bool:
    """Iterative depth check, so a hostile body cannot exhaust the Python stack here."""
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, (dict, list)):
            if depth > limit:
                return True
            children = item.values() if isinstance(item, dict) else item
            stack.extend((child, depth + 1) for child in children)
    return False


def _parse_body(raw: bytes) -> object | None:
    """Lenient parse used only to link the event to a top-up. Never raises."""
    if not raw:
        return None
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):  # RecursionError: absurdly nested arrays or objects
        return None
    if _too_deep(body):
        return None
    return scrub_nul(body)


def _lenient_deposit_id(body: object) -> uuid.UUID | None:
    if isinstance(body, dict):
        try:
            return uuid.UUID(str(body.get("depositId")))
        except (ValueError, TypeError):
            return None
    return None


def _topup_for(dep: uuid.UUID | None):
    if dep is None:
        return None
    row = db.session.execute(
        select(Topup.id, Topup.status).where(Topup.deposit_id == dep)
    ).first()
    db.session.rollback()  # end the read transaction before any HTTP call
    return row


def process_callback(
    raw: bytes,
    headers: Mapping[str, str],
    *,
    method: str,
    authority: str,
    path: str,
    query: str = "",
    scheme: str = "https",
    source: str = "callback",
    force_require_signature: bool = False,
    replay: bool = False,
) -> CallbackResult:
    cfg = current_app.config
    require = force_require_signature or bool(cfg.get("PAWAPAY_REQUIRE_SIGNATURE"))
    stored_headers = pick_headers(headers)
    raw_text = scrub_nul(raw.decode("utf-8", errors="replace"))

    # Parse leniently up front only to link the event to a top-up; trust nothing yet.
    body = _parse_body(raw)
    dep = _lenient_deposit_id(body)
    topup = _topup_for(dep)

    lowered = {k.lower() for k, _ in headers.items()}
    sig_reason = None
    if replay:
        signature = "not_checked"
    elif "signature" not in lowered:
        signature = "missing"
    else:
        view = RequestView(
            method=method,
            authority=authority,
            path=path,
            query=query,
            scheme=scheme,
            headers=headers,
        )
        result = verify_request(
            view,
            raw,
            _key_resolver,
            clock_skew=cfg.get("SIGNATURE_CLOCK_SKEW_SECONDS", 30),
            max_age=cfg.get("SIGNATURE_MAX_AGE_SECONDS", 300),
        )
        signature = "verified" if result.ok else "invalid"
        sig_reason = result.reason if not result.ok else None
        if not result.ok:
            log.warning("callback signature invalid: %s (%s)", result.reason, result.message)

    info = EventInfo(
        source=source,
        http_status=None,
        signature=signature,
        payload=body if body is not None else None,
        headers=stored_headers,
        raw_body=raw_text[:65536],
    )

    if signature != "verified" and require and not replay:
        detail = (
            f"Rejected: signature {signature}"
            + (f" ({sig_reason})" if sig_reason else "")
            + ". Signed callbacks are required, so nothing was applied."
        )
        info.http_status = 401
        event_id = record_event(
            info, outcome="rejected", topup_id=topup.id if topup else None, deposit_id=dep, detail=detail
        )
        return CallbackResult(
            401, "rejected", signature, detail, topup.status if topup else None, event_id, sig_reason
        )

    if not isinstance(body, dict) or dep is None:
        detail = "Body is not JSON with a valid depositId."
        info.http_status = 400
        event_id = record_event(info, outcome="error", deposit_id=dep, detail=detail)
        return CallbackResult(400, "error", signature, detail, None, event_id, sig_reason)

    if topup is None:
        detail = "No top-up has this depositId. Acknowledged so pawaPay stops retrying."
        info.http_status = 200
        event_id = record_event(info, outcome="no_change", deposit_id=dep, detail=detail)
        return CallbackResult(200, "no_change", signature, detail, None, event_id, sig_reason)

    if signature == "verified":
        info.http_status = 200
        info.detail = "Signature verified, applied from the callback body."
        applied = apply_result(dep, body.get("status"), body, source, event=info)
        return CallbackResult(
            200, applied.outcome, signature, applied.detail, applied.status, applied.event_id, sig_reason
        )

    # Hint mode: confirm with pawaPay's API before touching state.
    if replay:
        hint_note = (
            "Replay of the stored callback. Stored callbacks keep no signature, so the replay is "
            "checked against GET /v2/deposits instead of being applied from its body."
        )
    elif signature == "missing":
        hint_note = "Unsigned callback, treated as a hint."
    else:
        hint_note = f"Signature invalid ({sig_reason}), treated as a hint."
    if not is_configured():
        detail = f"{hint_note} Cannot confirm because pawaPay is not configured. Nothing applied."
        info.http_status = 503
        event_id = record_event(info, outcome="error", topup_id=topup.id, deposit_id=dep, detail=detail)
        return CallbackResult(503, "error", signature, detail, topup.status, event_id, sig_reason)
    try:
        res = get_client().check_deposit(str(dep))
    except (PawaPayTransportError, PawaPayError) as exc:
        detail = f"{hint_note} Status check failed ({exc}); pawaPay will retry the callback."
        info.http_status = 503
        event_id = record_event(info, outcome="error", topup_id=topup.id, deposit_id=dep, detail=detail)
        return CallbackResult(503, "error", signature, detail, topup.status, event_id, sig_reason)

    status_body = res.body if isinstance(res.body, dict) else {}
    if res.status == "FOUND" and isinstance(status_body.get("data"), dict):
        data = status_body["data"]
        confirmed = str(data.get("status") or "").upper()
        info.http_status = 200
        info.detail = (
            f"{hint_note} Confirmed with GET /v2/deposits: {confirmed or '(no status)'}"
            + (
                f" (callback body said {str(body.get('status')).upper()})."
                if str(body.get("status") or "").upper() != confirmed
                else "."
            )
        )
        applied = apply_result(dep, confirmed, data, source, event=info)
        return CallbackResult(
            200, applied.outcome, signature, applied.detail, applied.status, applied.event_id, sig_reason
        )

    if res.status == "NOT_FOUND":
        detail = f"{hint_note} pawaPay has no record of this deposit, so the callback was ignored."
        info.http_status = 200
        event_id = record_event(info, outcome="rejected", topup_id=topup.id, deposit_id=dep, detail=detail)
        return CallbackResult(200, "rejected", signature, detail, topup.status, event_id, sig_reason)

    detail = f"{hint_note} Status check returned HTTP {res.http_status}; pawaPay will retry the callback."
    info.http_status = 503
    event_id = record_event(info, outcome="error", topup_id=topup.id, deposit_id=dep, detail=detail)
    return CallbackResult(503, "error", signature, detail, topup.status, event_id, sig_reason)
