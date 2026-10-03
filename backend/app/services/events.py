"""payment_events: the audit trail. Every callback, status check and initiate response lands here."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..extensions import db
from ..models import PaymentEvent

STORED_HEADER_NAMES = ("content-type", "content-digest", "signature-input")
NUL_REPLACEMENT = "\ufffd"


def scrub_nul(value: Any) -> Any:
    """Replace U+0000 in strings, dict keys and nested lists with U+FFFD.

    PostgreSQL text and JSONB cannot store U+0000, and a valid JSON body can carry it as the
    escape \\u0000. Replacing (not deleting) keeps evidence that something was there, and makes
    any decision taken on scrubbed data fail safe: an amount or status containing U+FFFD never
    matches, so it is held for review. Callers bound the nesting depth before calling this.
    """
    if isinstance(value, str):
        return value.replace("\x00", NUL_REPLACEMENT) if "\x00" in value else value
    if isinstance(value, dict):
        return {scrub_nul(k): scrub_nul(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_nul(v) for v in value]
    return value


@dataclass
class EventInfo:
    source: str
    http_status: int | None = None
    signature: str = "not_checked"
    payload: Any = None
    headers: dict | None = None
    raw_body: str | None = None
    detail: str | None = None
    extra_detail: list[str] = field(default_factory=list)

    def full_detail(self, *more: str | None) -> str | None:
        parts = [p for p in (self.detail, *self.extra_detail, *more) if p]
        return " ".join(parts) if parts else None


def pick_headers(headers: Mapping[str, str]) -> dict:
    """Keep only what is useful for audit and replay. Never auth headers, never the signature."""
    lowered = {k.lower(): v for k, v in headers.items()}
    stored: dict[str, Any] = {name: lowered[name] for name in STORED_HEADER_NAMES if name in lowered}
    stored["has_signature"] = "signature" in lowered
    stored["has_signature_input"] = "signature-input" in lowered
    stored["has_content_digest"] = "content-digest" in lowered
    stored["has_signature_date"] = "signature-date" in lowered
    return stored


def new_event(
    info: EventInfo,
    *,
    outcome: str,
    topup_id: uuid.UUID | None,
    deposit_id: uuid.UUID | None,
    detail: str | None = None,
) -> PaymentEvent:
    event = PaymentEvent(
        id=uuid.uuid4(),
        topup_id=topup_id,
        deposit_id=deposit_id,
        source=info.source,
        http_status=info.http_status,
        signature=info.signature,
        outcome=outcome,
        detail=scrub_nul(info.full_detail(detail)),
        payload=scrub_nul(info.payload),
        headers=scrub_nul(info.headers),
        raw_body=scrub_nul(info.raw_body),
    )
    db.session.add(event)
    return event


def record_event(
    info: EventInfo,
    *,
    outcome: str,
    topup_id: uuid.UUID | None = None,
    deposit_id: uuid.UUID | None = None,
    detail: str | None = None,
) -> uuid.UUID:
    """Insert one event in its own short transaction and return its id."""
    try:
        event = new_event(info, outcome=outcome, topup_id=topup_id, deposit_id=deposit_id, detail=detail)
        db.session.commit()
        return event.id
    except Exception:
        db.session.rollback()
        raise
