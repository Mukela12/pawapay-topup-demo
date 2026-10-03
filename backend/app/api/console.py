"""Operations console API. No auth in the demo: read access plus a few demo actions."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric import ec
from flask import Blueprint, jsonify, request
from sqlalchemy import String, and_, cast, func, or_, select

from ..extensions import db
from ..models import (
    OPEN_STATES,
    LedgerEntry,
    Outbox,
    PaymentEvent,
    ReconcileRun,
    Topup,
    WalletAccount,
)
from ..money import to_pawapay_amount
from ..pawapay.client import PawaPayTransportError, get_client
from ..pawapay.signatures import RequestView, content_digest_header, sign_request
from ..serializers import (
    serialize_event,
    serialize_ledger,
    serialize_outbox,
    serialize_topup,
)
from ..services.callbacks import process_callback
from ..services.events import EventInfo, record_event
from ..services.reconcile import reconcile_once, serialize_run
from ..services.topups import check_and_apply
from .common import api_error, int_arg, parse_uuid, require_configured
from .webhooks import WEBHOOK_PATH, request_authority

bp = Blueprint("console", __name__, url_prefix="/api/console")

FORGED_KEY_ID = "HTTP_EC_P256_KEY:1"  # pawaPay's documented key id; the signer is NOT pawaPay


def _load_topup(topup_id: str) -> Topup | None:
    tid = parse_uuid(topup_id)
    if tid is None:
        return None
    return db.session.execute(
        select(Topup).where(or_(Topup.id == tid, Topup.deposit_id == tid))
    ).scalar_one_or_none()


def _fresh(topup_id: uuid.UUID) -> Topup:
    t = db.session.execute(
        select(Topup).where(Topup.id == topup_id).execution_options(populate_existing=True)
    ).scalar_one()
    return t


def _balance(customer_id: uuid.UUID, currency: str) -> int:
    value = db.session.execute(
        select(WalletAccount.balance_minor).where(
            WalletAccount.customer_id == customer_id, WalletAccount.currency == currency
        )
    ).scalar_one_or_none()
    return int(value or 0)


@bp.get("/stats")
def stats():
    today = func.date_trunc("day", func.now())
    week_ago = func.now() - timedelta(days=7)
    row = db.session.execute(
        select(
            func.count().filter(and_(Topup.status == "COMPLETED", Topup.finalized_at >= today)),
            func.count().filter(
                and_(Topup.status.in_(("FAILED", "REJECTED")), Topup.finalized_at >= today)
            ),
            func.count().filter(Topup.status.in_(OPEN_STATES)),
            func.count().filter(Topup.status == "NEEDS_ATTENTION"),
            func.count().filter(and_(Topup.status == "COMPLETED", Topup.created_at >= week_ago)),
            func.count().filter(
                and_(
                    Topup.status.in_(("COMPLETED", "FAILED", "REJECTED")),
                    Topup.created_at >= week_ago,
                )
            ),
        )
    ).one()
    median = db.session.execute(
        select(
            func.percentile_cont(0.5).within_group(
                func.extract("epoch", Topup.finalized_at - Topup.created_at)
            )
        ).where(
            Topup.status.in_(("COMPLETED", "FAILED")),
            Topup.finalized_at.is_not(None),
            Topup.created_at >= week_ago,
        )
    ).scalar_one_or_none()
    credited = db.session.execute(
        select(WalletAccount.currency, func.sum(LedgerEntry.amount_minor))
        .join(WalletAccount, WalletAccount.id == LedgerEntry.account_id)
        .group_by(WalletAccount.currency)
        .order_by(WalletAccount.currency)
    ).all()
    completed_7d, final_7d = row[4], row[5]
    return jsonify(
        {
            "completed_today": row[0],
            "failed_today": row[1],
            "open_now": row[2],
            "needs_attention": row[3],
            "success_rate_7d": round(completed_7d / final_7d, 4) if final_7d else None,
            "median_seconds_to_final": round(float(median), 1) if median is not None else None,
            "total_credited": [
                {"currency": currency, "amount_minor": int(total or 0)} for currency, total in credited
            ],
        }
    )


@bp.get("/topups")
def list_topups():
    limit = int_arg("limit", 50, 1, 200)
    status = (request.args.get("status") or "").strip().upper()
    q = (request.args.get("q") or "").strip()
    stmt = select(Topup)
    if status == "OPEN":
        stmt = stmt.where(Topup.status.in_(OPEN_STATES))
    elif status:
        stmt = stmt.where(Topup.status.in_([s.strip() for s in status.split(",") if s.strip()]))
    if q:
        digits = "".join(ch for ch in q if ch.isdigit())
        conditions = [
            cast(Topup.deposit_id, String).ilike(f"{q.lower()}%"),
            cast(Topup.id, String).ilike(f"{q.lower()}%"),
            Topup.provider.ilike(f"%{q}%"),
        ]
        if digits:
            conditions.append(Topup.msisdn.like(f"%{digits}"))
        stmt = stmt.where(or_(*conditions))
    rows = db.session.execute(stmt.order_by(Topup.created_at.desc()).limit(limit)).scalars().all()
    return jsonify({"topups": [serialize_topup(t, audience="console") for t in rows]})


@bp.get("/topups/<topup_id>")
def topup_detail(topup_id: str):
    topup = _load_topup(topup_id)
    if topup is None:
        return api_error("not_found", "Top-up not found.", 404)
    events = db.session.execute(
        select(PaymentEvent).where(PaymentEvent.topup_id == topup.id).order_by(PaymentEvent.created_at)
    ).scalars().all()
    ledger = db.session.execute(
        select(LedgerEntry).where(LedgerEntry.topup_id == topup.id)
    ).scalar_one_or_none()
    outbox = db.session.execute(
        select(Outbox)
        .where(Outbox.dedupe_key.in_([f"receipt.issued:{topup.deposit_id}", f"voip_credit.granted:{topup.deposit_id}"]))
        .order_by(Outbox.created_at, Outbox.topic)
    ).scalars().all()
    return jsonify(
        {
            "topup": serialize_topup(topup, audience="console"),
            "events": [serialize_event(e, include_payload=True) for e in events],
            "ledger_entry": serialize_ledger(ledger),
            "outbox": [serialize_outbox(o) for o in outbox],
            "balance_minor": _balance(topup.customer_id, topup.currency),
        }
    )


@bp.post("/topups/<topup_id>/recheck")
def recheck(topup_id: str):
    topup = _load_topup(topup_id)
    if topup is None:
        return api_error("not_found", "Top-up not found.", 404)
    blocked = require_configured()
    if blocked:
        return blocked
    tid, dep = topup.id, topup.deposit_id
    age = (datetime.now(timezone.utc) - topup.created_at).total_seconds()
    db.session.rollback()
    # NOT_FOUND only fails a top-up once the initiate call has certainly finished.
    result = check_and_apply(dep, "status_check", not_found="fail" if age > 60 else "ignore")
    return jsonify({"result": result.as_dict(), "topup": serialize_topup(_fresh(tid), audience="console")})


@bp.post("/topups/<topup_id>/resend-callback")
def resend_callback(topup_id: str):
    topup = _load_topup(topup_id)
    if topup is None:
        return api_error("not_found", "Top-up not found.", 404)
    blocked = require_configured()
    if blocked:
        return blocked
    # NEEDS_ATTENTION is included: it is often a COMPLETED deposit held over an amount mismatch.
    # pawaPay answers INVALID_STATE itself if the deposit is not final on its side.
    if topup.status not in ("COMPLETED", "FAILED", "NEEDS_ATTENTION"):
        return api_error(
            "not_final",
            "pawaPay only resends callbacks for deposits in a final state.",
            409,
        )
    tid, dep = topup.id, topup.deposit_id
    db.session.rollback()
    try:
        res = get_client().resend_callback(str(dep))
    except PawaPayTransportError as exc:
        event_id = record_event(
            EventInfo(source="resend_request"),
            outcome="error",
            topup_id=tid,
            deposit_id=dep,
            detail=f"Could not reach pawaPay: {exc}",
        )
        return jsonify({"result": {"outcome": "error", "event_id": str(event_id), "detail": str(exc)}}), 502
    status = res.status
    if res.http_status == 200 and status == "ACCEPTED":
        outcome, detail = "no_change", "pawaPay accepted the resend request. The callback will arrive shortly."
    else:
        reason = res.failure_reason
        outcome = "rejected"
        detail = (
            f"pawaPay rejected the resend: {reason.get('failureCode') or 'HTTP ' + str(res.http_status)}"
            + (f" ({reason.get('failureMessage')})" if reason.get("failureMessage") else "")
        )
    event_id = record_event(
        EventInfo(source="resend_request", http_status=res.http_status, payload=res.body),
        outcome=outcome,
        topup_id=tid,
        deposit_id=dep,
        detail=detail,
    )
    return jsonify(
        {
            "result": {
                "outcome": outcome,
                "http_status": res.http_status,
                "pawapay_status": status,
                "detail": detail,
                "event_id": str(event_id),
            }
        }
    )


@bp.post("/topups/<topup_id>/replay-callback")
def replay_callback(topup_id: str):
    topup = _load_topup(topup_id)
    if topup is None:
        return api_error("not_found", "Top-up not found.", 404)
    last = db.session.execute(
        select(PaymentEvent)
        .where(PaymentEvent.topup_id == topup.id, PaymentEvent.source == "callback")
        .order_by(PaymentEvent.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if last is None:
        return api_error(
            "no_callback",
            "No callback has been received for this top-up yet, so there is nothing to replay.",
            404,
        )
    raw = (last.raw_body or json.dumps(last.payload or {}, separators=(",", ":"))).encode("utf-8")
    stored = last.headers or {}
    headers = {"Content-Type": stored.get("content-type") or "application/json"}
    if stored.get("content-digest"):
        headers["Content-Digest"] = stored["content-digest"]
    if stored.get("signature-input"):
        headers["Signature-Input"] = stored["signature-input"]
    tid, cid, currency = topup.id, topup.customer_id, topup.currency
    status_before = topup.status
    balance_before = _balance(cid, currency)
    db.session.rollback()

    # The stored callback has no Signature header (only presence flags are kept) and its
    # signature would be past expiry anyway, so replay=True runs it in hint mode: confirmed with
    # GET /v2/deposits and applied through apply_result, even when signatures are required.
    result = process_callback(
        raw,
        headers,
        method="POST",
        authority=request_authority(),
        path=WEBHOOK_PATH,
        scheme=request.scheme,
        source="replay",
        replay=True,
    )
    fresh = _fresh(tid)
    return jsonify(
        {
            "result": result.as_dict(),
            "replayed_event_id": str(last.id),
            "status_before": status_before,
            "status_after": fresh.status,
            "balance_before": balance_before,
            "balance_after": _balance(cid, currency),
            "topup": serialize_topup(fresh, audience="console"),
        }
    )


@bp.post("/topups/<topup_id>/forged-callback")
def forged_callback(topup_id: str):
    topup = _load_topup(topup_id)
    if topup is None:
        return api_error("not_found", "Top-up not found.", 404)
    tid, cid, currency, dep = topup.id, topup.customer_id, topup.currency, topup.deposit_id
    status_before = topup.status
    balance_before = _balance(cid, currency)
    body = {
        "depositId": str(dep),
        "status": "COMPLETED",
        "amount": to_pawapay_amount(topup.amount_minor, currency, 2),
        "currency": currency,
        "country": topup.country,
        "payer": {
            "type": "MMO",
            "accountDetails": {"phoneNumber": topup.msisdn, "provider": topup.provider},
        },
        "customerMessage": "Ringwise topup",
        "created": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "providerTransactionId": "FORGED-TEST",
        "metadata": {"customerId": str(cid), "source": "ringwise-demo"},
    }
    db.session.rollback()
    raw = json.dumps(body, separators=(",", ":")).encode()
    now = int(time.time())
    authority = request_authority()
    headers = {
        "Content-Type": "application/json; charset=UTF-8",
        "Content-Digest": content_digest_header(raw, "sha-512"),
        "Signature-Date": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    # A throwaway key that is not pawaPay's: correct format, correct digest, wrong signer.
    attacker_key = ec.generate_private_key(ec.SECP256R1())
    view = RequestView("POST", authority, WEBHOOK_PATH, "", request.scheme, headers)
    sig_input, signature = sign_request(
        view, attacker_key, keyid=FORGED_KEY_ID, created=now, expires=now + 60
    )
    headers["Signature-Input"] = sig_input
    headers["Signature"] = signature

    result = process_callback(
        raw,
        headers,
        method="POST",
        authority=authority,
        path=WEBHOOK_PATH,
        scheme=request.scheme,
        source="forged_test",
        force_require_signature=True,
    )
    fresh = _fresh(tid)
    return jsonify(
        {
            "result": result.as_dict(),
            "status_before": status_before,
            "status_after": fresh.status,
            "balance_before": balance_before,
            "balance_after": _balance(cid, currency),
            "topup": serialize_topup(fresh, audience="console"),
        }
    )


@bp.get("/reconcile-runs")
def reconcile_runs():
    limit = int_arg("limit", 20, 1, 100)
    rows = db.session.execute(
        select(ReconcileRun).order_by(ReconcileRun.started_at.desc()).limit(limit)
    ).scalars().all()
    return jsonify({"runs": [serialize_run(r) for r in rows]})


@bp.post("/reconcile")
def reconcile_now():
    blocked = require_configured()
    if blocked:
        return blocked
    db.session.rollback()
    return jsonify({"run": reconcile_once("manual")})


@bp.get("/events")
def events():
    limit = int_arg("limit", 100, 1, 500)
    rows = db.session.execute(
        select(PaymentEvent).order_by(PaymentEvent.created_at.desc()).limit(limit)
    ).scalars().all()
    return jsonify({"events": [serialize_event(e, include_payload=True) for e in rows]})
