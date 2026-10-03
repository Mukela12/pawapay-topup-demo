"""JSON shapes for the API (snake_case keys)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import OPEN_STATES, LedgerEntry, Outbox, PaymentEvent, Topup
from .money import exponent, format_display, to_pawapay_amount
from .services.outbox import TOPIC_LABELS

# Customer-facing copy per failure code. failure_message from pawaPay is shown to ops only.
HUMAN_REASONS = {
    "PAYER_NOT_FOUND": "This number does not have an active mobile money wallet on the selected network.",
    "PAYMENT_NOT_APPROVED": "The payment was not approved on the phone. The PIN prompt may have timed out.",
    "PAYER_LIMIT_REACHED": "The mobile money wallet has reached its transaction limit.",
    "PAYMENT_IN_PROGRESS": "Another payment is still pending on this phone. Wait a few minutes, then try again.",
    "INSUFFICIENT_BALANCE": "The mobile money wallet does not have enough balance for this amount.",
    "WALLET_LIMIT_REACHED": "The mobile money wallet has reached its limit.",
    "UNSPECIFIED_FAILURE": "The network declined the payment without giving a reason.",
    "UNKNOWN_ERROR": "The network could not process the payment.",
    "NOT_FOUND_AT_PROVIDER": "pawaPay has no record of this payment, so no money was taken.",
    "AMOUNT_MISMATCH": "The amount confirmed by the network did not match. It is being reviewed and nothing has been credited yet.",
    "CURRENCY_MISMATCH": "The currency confirmed by the network did not match. It is being reviewed and nothing has been credited yet.",
    "UNKNOWN_STATUS": "The payment is being reviewed by our team before anything is credited.",
    "LATE_COMPLETED_AFTER_FAILURE": "pawaPay confirmed this payment after it was marked failed. It is being reviewed and nothing has been credited yet.",
    "INVALID_PHONE_NUMBER": "That phone number is not valid for the selected network.",
    "INVALID_AMOUNT": "That amount is not accepted by the selected network.",
    "AMOUNT_OUT_OF_BOUNDS": "That amount is outside the limits of the selected network.",
    "INVALID_CURRENCY": "That currency is not supported for this network.",
    "INVALID_PROVIDER": "That network is not available.",
    "PROVIDER_TEMPORARILY_UNAVAILABLE": "The selected network is temporarily unavailable. Try again shortly.",
    "DEPOSITS_NOT_ALLOWED": "Top-ups are not enabled for this network right now.",
}
GENERIC_FAILURE = "The payment could not be completed. No money was credited."


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def mask_msisdn(msisdn: str | None) -> str | None:
    if not msisdn:
        return msisdn
    if len(msisdn) <= 8:
        return msisdn[:2] + "*" * max(len(msisdn) - 4, 0) + msisdn[-2:]
    return msisdn[:4] + "*" * (len(msisdn) - 8) + msisdn[-4:]


def human_reason(topup: Topup) -> str | None:
    if topup.status in ("FAILED", "REJECTED", "NEEDS_ATTENTION"):
        return HUMAN_REASONS.get(topup.failure_code or "", GENERIC_FAILURE)
    return None


def serialize_topup(topup: Topup, *, audience: str = "customer") -> dict:
    exp = exponent(topup.currency)
    try:
        pawapay_amount = to_pawapay_amount(topup.amount_minor, topup.currency, exp)
    except Exception:
        pawapay_amount = None
    out = {
        "id": str(topup.id),
        "deposit_id": str(topup.deposit_id),
        "status": topup.status,
        "is_open": topup.status in OPEN_STATES,
        "amount": format_display(topup.amount_minor, topup.currency),
        "amount_minor": topup.amount_minor,
        "pawapay_amount": pawapay_amount,
        "currency": topup.currency,
        "exponent": exp,
        "country": topup.country,
        "provider": topup.provider,
        "phone": topup.msisdn if audience == "customer" else mask_msisdn(topup.msisdn),
        "failure_code": topup.failure_code,
        "reason": human_reason(topup),
        "provider_txn_id": topup.provider_txn_id,
        "scenario": topup.scenario,
        "created_at": iso(topup.created_at),
        "accepted_at": iso(topup.accepted_at),
        "finalized_at": iso(topup.finalized_at),
        "last_checked_at": iso(topup.last_checked_at),
        "check_count": topup.check_count,
    }
    if audience == "console":
        out["failure_message"] = topup.failure_message
        out["customer_id"] = str(topup.customer_id)
    return out


def _pawapay_status_of(payload: Any) -> str | None:
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), dict) and payload["data"].get("status"):
            return str(payload["data"]["status"]).upper()
        if payload.get("status"):
            return str(payload["status"]).upper()
    return None


def serialize_event(event: PaymentEvent, *, include_payload: bool = False) -> dict:
    out = {
        "id": str(event.id),
        "topup_id": str(event.topup_id) if event.topup_id else None,
        "deposit_id": str(event.deposit_id) if event.deposit_id else None,
        "source": event.source,
        "http_status": event.http_status,
        "signature": event.signature,
        "outcome": event.outcome,
        "pawapay_status": _pawapay_status_of(event.payload),
        "detail": event.detail,
        "created_at": iso(event.created_at),
    }
    if include_payload:
        out["payload"] = event.payload
        out["headers"] = event.headers
    return out


def serialize_ledger(entry: LedgerEntry | None) -> dict | None:
    if entry is None:
        return None
    return {
        "id": str(entry.id),
        "account_id": str(entry.account_id),
        "topup_id": str(entry.topup_id),
        "kind": entry.kind,
        "amount_minor": entry.amount_minor,
        "created_at": iso(entry.created_at),
    }


def serialize_outbox(row: Outbox) -> dict:
    return {
        "id": str(row.id),
        "topic": row.topic,
        "label": TOPIC_LABELS.get(row.topic, row.topic),
        "dedupe_key": row.dedupe_key,
        "attempts": row.attempts,
        "payload": row.payload,
        "created_at": iso(row.created_at),
        "delivered_at": iso(row.delivered_at),
    }
