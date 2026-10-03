"""Top-up lifecycle.

create_topup()  commits the row (with our uuid4 depositId) BEFORE calling pawaPay.
initiate()      calls POST /v2/deposits outside any DB transaction and records the answer.
apply_result()  is the ONE guarded transition every source goes through: initiate response,
                callback, status check, reconciliation, replay.
check_and_apply() calls GET /v2/deposits/{id} outside any transaction, then apply_result().

Never call pawaPay while a DB transaction is open: every function here commits or rolls back
before it makes an HTTP call.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from flask import current_app
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import (
    OPEN_STATES,
    OUTBOX_TOPICS,
    Customer,
    LedgerEntry,
    Outbox,
    Topup,
    WalletAccount,
)
from ..money import format_display, provider_amount_matches, to_pawapay_amount
from ..pawapay.client import PawaPayTransportError, get_client
from .events import EventInfo, new_event, record_event, scrub_nul

log = logging.getLogger(__name__)

OPEN_RANK = {"CREATED": 0, "ACCEPTED": 1, "PROCESSING": 2, "IN_RECONCILIATION": 3}
FINAL_STATES = ("COMPLETED", "FAILED", "REJECTED", "NEEDS_ATTENTION")
CUSTOMER_MESSAGE = "Ringwise topup"  # 4-22 chars, letters, digits and spaces only
NOT_FOUND_CODE = "NOT_FOUND_AT_PROVIDER"
LATE_COMPLETED_CODE = "LATE_COMPLETED_AFTER_FAILURE"


def as_uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


@dataclass
class ApplyResult:
    outcome: str
    status: str | None
    previous_status: str | None
    event_id: uuid.UUID | None
    credited: bool = False
    detail: str | None = None

    def as_dict(self) -> dict:
        return {
            "outcome": self.outcome,
            "status": self.status,
            "previous_status": self.previous_status,
            "event_id": str(self.event_id) if self.event_id else None,
            "credited": self.credited,
            "detail": self.detail,
        }


def _failure_reason(data: dict) -> tuple[str | None, str | None]:
    reason = data.get("failureReason") if isinstance(data, dict) else None
    if isinstance(reason, dict):
        code = reason.get("failureCode")
        message = reason.get("failureMessage")
        return (str(code)[:64] if code else None, str(message) if message else None)
    return None, None


# =========================================================================== the guarded transition
def apply_result(
    deposit_id: Any,
    pawapay_status: str | None,
    data: dict | None,
    source: str,
    *,
    event: EventInfo | None = None,
) -> ApplyResult:
    """Apply a pawaPay status to a top-up in ONE transaction, exactly once.

    1. UPDATE topups SET status=:new ... WHERE deposit_id=:id AND status IN (allowed) RETURNING.
    2. No row: the top-up is already final or held -> duplicate_ignored, balance untouched.
       Exception: COMPLETED for a FAILED or REJECTED top-up is a conflict, not a duplicate (the
       customer may have paid). It moves to NEEDS_ATTENTION (held_for_review), never credited.
    3. COMPLETED: amount (Decimal) and currency must match the stored values, otherwise
       NEEDS_ATTENTION (held_for_review). On a match: ledger entry (UNIQUE(topup_id, kind)),
       balance += amount, two outbox rows, all in the same transaction.
    4. FAILED / REJECTED: store failure code and message.
    5. ACCEPTED / PROCESSING / IN_RECONCILIATION: status only, stays open, never moves back.
    6. Anything else: NEEDS_ATTENTION.
    An IntegrityError on the ledger key means it was already credited: duplicate_ignored.
    """
    dep = as_uuid(deposit_id)
    info = event or EventInfo(source=source, payload=data)
    info.source = source
    status = str(pawapay_status or "").strip().upper()
    data = scrub_nul(data) if isinstance(data, dict) else {}
    try:
        return _apply_in_tx(dep, status, data, info)
    except IntegrityError:
        db.session.rollback()
        log.warning("apply_result: ledger/outbox unique key hit for %s, treating as duplicate", dep)
        current = db.session.execute(
            select(Topup.id, Topup.status).where(Topup.deposit_id == dep)
        ).first()
        db.session.rollback()
        detail = "The ledger already holds a credit for this top-up (unique key), nothing changed."
        event_id = record_event(
            info,
            outcome="duplicate_ignored",
            topup_id=current.id if current else None,
            deposit_id=dep,
            detail=detail,
        )
        return ApplyResult(
            "duplicate_ignored",
            current.status if current else None,
            current.status if current else None,
            event_id,
            detail=detail,
        )
    except Exception:
        db.session.rollback()
        raise


def _apply_in_tx(dep: uuid.UUID, status: str, data: dict, info: EventInfo) -> ApplyResult:
    row = db.session.execute(
        select(
            Topup.id,
            Topup.customer_id,
            Topup.amount_minor,
            Topup.currency,
            Topup.country,
            Topup.provider,
            Topup.status,
        ).where(Topup.deposit_id == dep)
    ).first()
    if row is None:
        detail = "No top-up has this depositId, nothing to apply."
        ev = new_event(info, outcome="no_change", topup_id=None, deposit_id=dep, detail=detail)
        db.session.commit()
        return ApplyResult("no_change", None, None, ev.id, detail=detail)

    previous = row.status
    values: dict[str, Any] = {}
    txn_id = data.get("providerTransactionId")
    if txn_id:
        values["provider_txn_id"] = str(txn_id)[:128]
    allowed_from: tuple[str, ...] = OPEN_STATES
    credit = False
    applied_outcome = "applied"
    expected = f"{format_display(row.amount_minor, row.currency)} {row.currency}"

    if status == "COMPLETED":
        amount_ok = provider_amount_matches(data.get("amount"), row.amount_minor, row.currency)
        currency_ok = str(data.get("currency") or "").strip().upper() == row.currency
        if amount_ok and currency_ok:
            values.update(
                status="COMPLETED", finalized_at=func.now(), failure_code=None, failure_message=None
            )
            credit = True
            detail = f"COMPLETED, {expected} credited to the wallet."
        else:
            code = "CURRENCY_MISMATCH" if amount_ok else "AMOUNT_MISMATCH"
            message = (
                f"pawaPay reported {data.get('amount')!r} {data.get('currency')!r} but the top-up "
                f"was for {expected}. Held for review, nothing credited."
            )
            values.update(status="NEEDS_ATTENTION", failure_code=code, failure_message=message)
            applied_outcome = "held_for_review"
            detail = message
    elif status == "FAILED":
        code, message = _failure_reason(data)
        values.update(
            status="FAILED",
            failure_code=code or "UNSPECIFIED_FAILURE",
            failure_message=message,
            finalized_at=func.now(),
        )
        detail = f"FAILED with {code or 'no failure code'}."
    elif status == "REJECTED":
        # Initiation rejection: only legal straight from CREATED.
        allowed_from = ("CREATED",)
        code, message = _failure_reason(data)
        values.update(
            status="REJECTED",
            failure_code=code or "REJECTED",
            failure_message=message,
            finalized_at=func.now(),
        )
        detail = f"pawaPay rejected the deposit: {code or 'no failure code'}."
    elif status in OPEN_RANK and status != "CREATED":
        rank = OPEN_RANK[status]
        allowed_from = tuple(s for s, r in OPEN_RANK.items() if r < rank)
        values.update(status=status, accepted_at=func.coalesce(Topup.accepted_at, func.now()))
        detail = f"pawaPay status {status}, still open."
    else:
        message = f"pawaPay returned an unexpected status {status or '(empty)'!r}. Held for review."
        values.update(status="NEEDS_ATTENTION", failure_code="UNKNOWN_STATUS", failure_message=message)
        applied_outcome = "held_for_review"
        detail = message

    updated = db.session.execute(
        update(Topup)
        .where(Topup.deposit_id == dep, Topup.status.in_(allowed_from))
        .values(**values)
        .returning(Topup.status)
        .execution_options(synchronize_session=False)
    ).first()

    if updated is None:
        current = db.session.execute(
            select(Topup.status).where(Topup.deposit_id == dep)
        ).scalar_one()
        if status == "COMPLETED" and current in ("FAILED", "REJECTED"):
            held = _hold_conflicting_completed(dep, data, current)
            if held is not None:
                ev = new_event(info, outcome="held_for_review", topup_id=row.id, deposit_id=dep, detail=held)
                db.session.commit()
                log.warning(
                    "topup.conflict deposit_id=%s was=%s got=COMPLETED source=%s: held for review",
                    dep,
                    current,
                    info.source,
                )
                return ApplyResult("held_for_review", "NEEDS_ATTENTION", current, ev.id, detail=held)
            current = db.session.execute(
                select(Topup.status).where(Topup.deposit_id == dep)
            ).scalar_one()
        if current in OPEN_STATES:
            outcome = "no_change"
            detail = f"{status or 'Empty status'} does not move the top-up forward from {current}."
        else:
            outcome = "duplicate_ignored"
            detail = f"Top-up is already {current}, so {status or 'this result'} was ignored."
        ev = new_event(info, outcome=outcome, topup_id=row.id, deposit_id=dep, detail=detail)
        db.session.commit()
        return ApplyResult(outcome, current, previous, ev.id, detail=detail)

    if credit:
        _credit_wallet(row, dep)

    ev = new_event(info, outcome=applied_outcome, topup_id=row.id, deposit_id=dep, detail=detail)
    db.session.commit()
    log.info(
        "topup.transition deposit_id=%s from=%s to=%s source=%s outcome=%s",
        dep,
        previous,
        updated.status,
        info.source,
        applied_outcome,
    )
    return ApplyResult(applied_outcome, updated.status, previous, ev.id, credited=credit, detail=detail)


def _hold_conflicting_completed(dep: uuid.UUID, data: dict, current: str) -> str | None:
    """COMPLETED arrived for a top-up we already marked FAILED or REJECTED.

    The usual cause is a NOT_FOUND status check that raced an in-flight deposit. pawaPay now says
    the money moved, so dropping this as a duplicate would lose the customer's payment silently.
    Move it to NEEDS_ATTENTION for a person to confirm and credit; never credit it automatically.
    Returns the detail message, or None if another writer changed the status first.
    """
    previous_code = db.session.execute(
        select(Topup.failure_code).where(Topup.deposit_id == dep)
    ).scalar_one()
    message = (
        f"pawaPay reported COMPLETED ({data.get('amount')!r} {data.get('currency')!r}) after this "
        f"top-up was marked {current} ({previous_code or 'no failure code'}). The customer may "
        "have paid. Held for review, nothing credited."
    )
    values: dict[str, Any] = {
        "status": "NEEDS_ATTENTION",
        "failure_code": LATE_COMPLETED_CODE,
        "failure_message": message,
    }
    txn_id = data.get("providerTransactionId")
    if txn_id:
        values["provider_txn_id"] = str(txn_id)[:128]
    moved = db.session.execute(
        update(Topup)
        .where(Topup.deposit_id == dep, Topup.status == current)
        .values(**values)
        .returning(Topup.status)
        .execution_options(synchronize_session=False)
    ).first()
    return message if moved is not None else None


def _credit_wallet(row: Any, dep: uuid.UUID) -> None:
    """Ledger entry + balance + outbox, inside the caller's transaction."""
    db.session.execute(
        pg_insert(WalletAccount)
        .values(id=uuid.uuid4(), customer_id=row.customer_id, currency=row.currency, balance_minor=0)
        .on_conflict_do_nothing(index_elements=["customer_id", "currency"])
    )
    account_id = db.session.execute(
        select(WalletAccount.id).where(
            WalletAccount.customer_id == row.customer_id, WalletAccount.currency == row.currency
        )
    ).scalar_one()
    # UNIQUE(topup_id, kind) makes a second credit impossible; a violation raises IntegrityError.
    db.session.execute(
        insert(LedgerEntry).values(
            id=uuid.uuid4(),
            account_id=account_id,
            topup_id=row.id,
            kind="topup_credit",
            amount_minor=row.amount_minor,
        )
    )
    db.session.execute(
        update(WalletAccount)
        .where(WalletAccount.id == account_id)
        .values(balance_minor=WalletAccount.balance_minor + row.amount_minor)
        .execution_options(synchronize_session=False)
    )
    payload = {
        "deposit_id": str(dep),
        "topup_id": str(row.id),
        "customer_id": str(row.customer_id),
        "amount_minor": row.amount_minor,
        "currency": row.currency,
        "provider": row.provider,
    }
    for topic in OUTBOX_TOPICS:
        db.session.execute(
            pg_insert(Outbox)
            .values(
                id=uuid.uuid4(),
                topic=topic,
                payload={**payload, "topic": topic},
                dedupe_key=f"{topic}:{dep}",
            )
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
        )


# =========================================================================== status checks
@dataclass
class CheckResult:
    kind: str  # found | not_found | error
    http_status: int | None = None
    pawapay_status: str | None = None
    apply: ApplyResult | None = None
    event_id: uuid.UUID | None = None
    detail: str | None = None

    def as_dict(self) -> dict:
        out = {
            "kind": self.kind,
            "http_status": self.http_status,
            "pawapay_status": self.pawapay_status,
            "detail": self.detail,
        }
        if self.apply:
            out.update(self.apply.as_dict())
            out["detail"] = self.detail or self.apply.detail
        else:
            out["event_id"] = str(self.event_id) if self.event_id else None
        return out


def _topup_id_for(dep: uuid.UUID) -> uuid.UUID | None:
    found = db.session.execute(select(Topup.id).where(Topup.deposit_id == dep)).scalar_one_or_none()
    db.session.rollback()  # end the read transaction before any HTTP call
    return found


def mark_checked(dep: uuid.UUID) -> None:
    db.session.execute(
        update(Topup)
        .where(Topup.deposit_id == dep)
        .values(last_checked_at=func.now(), check_count=Topup.check_count + 1)
        .execution_options(synchronize_session=False)
    )
    db.session.commit()


def check_and_apply(
    deposit_id: Any,
    source: str,
    *,
    not_found: str = "fail",
    mark: bool = True,
    detail_prefix: str | None = None,
) -> CheckResult:
    """GET /v2/deposits/{id} (outside any transaction), then apply_result.

    not_found="fail": NOT_FOUND means pawaPay never got the deposit, mark it FAILED.
    not_found="ignore": record the answer and change nothing. Used while the deposit request may
    still be in flight (a read timeout on initiate, a very young top-up); reconciliation applies
    the NOT_FOUND rule later, once RECONCILE_AFTER_SECONDS have passed.
    """
    dep = as_uuid(deposit_id)
    if mark:
        mark_checked(dep)
    else:
        db.session.rollback()
    client = get_client()
    try:
        res = client.check_deposit(str(dep))
    except PawaPayTransportError as exc:
        detail = f"Status check failed: {exc}. Left unchanged, it will be retried."
        event_id = record_event(
            EventInfo(source=source, detail=detail_prefix),
            outcome="error",
            topup_id=_topup_id_for(dep),
            deposit_id=dep,
            detail=detail,
        )
        return CheckResult("error", None, None, None, event_id, detail)

    body = res.body if isinstance(res.body, dict) else {}
    if res.status == "FOUND":
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        pstatus = str(data.get("status") or "").upper() or None
        result = apply_result(
            dep,
            pstatus,
            data,
            source,
            event=EventInfo(
                source=source,
                http_status=res.http_status,
                payload=res.body,
                detail=detail_prefix or f"GET /v2/deposits returned FOUND {pstatus}.",
            ),
        )
        return CheckResult("found", res.http_status, pstatus, result)

    if res.status == "NOT_FOUND":
        if not_found == "fail":
            result = apply_result(
                dep,
                "FAILED",
                {
                    "failureReason": {
                        "failureCode": NOT_FOUND_CODE,
                        "failureMessage": "pawaPay has no record of this deposit, so no money moved.",
                    }
                },
                source,
                event=EventInfo(
                    source=source,
                    http_status=res.http_status,
                    payload=res.body,
                    detail=detail_prefix or "GET /v2/deposits returned NOT_FOUND.",
                ),
            )
            return CheckResult("not_found", res.http_status, "NOT_FOUND", result)
        detail = (
            "GET /v2/deposits returned NOT_FOUND. Left open because the deposit request may still "
            "be in flight; reconciliation fails it if pawaPay still has no record after "
            f"{current_app.config.get('RECONCILE_AFTER_SECONDS', 900)}s."
        )
        event_id = record_event(
            EventInfo(source=source, http_status=res.http_status, payload=res.body, detail=detail_prefix),
            outcome="no_change",
            topup_id=_topup_id_for(dep),
            deposit_id=dep,
            detail=detail,
        )
        return CheckResult("not_found", res.http_status, "NOT_FOUND", None, event_id, detail)

    detail = f"Status check returned HTTP {res.http_status} without a usable status. Left unchanged."
    event_id = record_event(
        EventInfo(source=source, http_status=res.http_status, payload=res.body, detail=detail_prefix),
        outcome="error",
        topup_id=_topup_id_for(dep),
        deposit_id=dep,
        detail=detail,
    )
    return CheckResult("error", res.http_status, res.status, None, event_id, detail)


# =========================================================================== creation
class TopupError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int = 422,
        field: str | None = None,
        extra: dict | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.field = field
        self.extra = extra or {}


@dataclass
class TopupRequest:
    customer_id: uuid.UUID
    country: str
    provider: str
    msisdn: str
    amount_minor: int
    currency: str
    decimals_in_amount: str
    scenario: str | None = None
    idempotency_key: str | None = None
    fingerprint: str | None = None
    extra: dict = field(default_factory=dict)


def request_fingerprint(body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _reload(topup_id: uuid.UUID) -> Topup:
    t = db.session.execute(
        select(Topup).where(Topup.id == topup_id).execution_options(populate_existing=True)
    ).scalar_one()
    db.session.commit()
    return t


def create_topup(req: TopupRequest) -> tuple[Topup, bool]:
    """Phase A: validate limits under a customer row lock and commit the CREATED row.

    Returns (topup, replayed). The pawaPay call happens afterwards in initiate().
    """
    cfg = current_app.config
    try:
        locked = db.session.execute(
            select(Customer.id).where(Customer.id == req.customer_id).with_for_update()
        ).scalar_one_or_none()
        if locked is None:
            raise TopupError("no_session", "Start a session first (POST /api/session).", 401)

        if req.idempotency_key:
            existing = db.session.execute(
                select(Topup).where(
                    Topup.customer_id == req.customer_id,
                    Topup.idempotency_key == req.idempotency_key,
                )
            ).scalar_one_or_none()
            if existing is not None:
                db.session.commit()
                if existing.request_fingerprint != req.fingerprint:
                    raise TopupError(
                        "idempotency_key_reused",
                        "This Idempotency-Key was already used with a different request body.",
                        422,
                    )
                return existing, True

        open_cutoff = func.now() - timedelta(seconds=cfg["OPEN_TOPUP_LOCK_SECONDS"])
        open_topup = db.session.execute(
            select(Topup)
            .where(
                Topup.customer_id == req.customer_id,
                Topup.status.in_(OPEN_STATES),
                Topup.created_at > open_cutoff,
            )
            .order_by(Topup.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if open_topup is not None:
            raise TopupError(
                "topup_open",
                "You already have a top-up waiting for approval. Finish or wait for it before starting another.",
                409,
                extra={"topup_obj": open_topup},
            )

        # Serialise per phone number so the hourly cap holds across customers and workers.
        db.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"phone:{req.msisdn}"}
        )
        phone_count = db.session.execute(
            select(func.count())
            .select_from(Topup)
            .where(Topup.msisdn == req.msisdn, Topup.created_at > func.now() - timedelta(hours=1))
        ).scalar_one()
        if phone_count >= cfg["PHONE_HOURLY_CAP"]:
            raise TopupError(
                "phone_hourly_cap",
                f"This phone number has reached the demo limit of {cfg['PHONE_HOURLY_CAP']} top-ups per hour.",
                429,
                field="phone",
            )
        customer_count = db.session.execute(
            select(func.count())
            .select_from(Topup)
            .where(
                Topup.customer_id == req.customer_id,
                Topup.created_at > func.now() - timedelta(days=1),
            )
        ).scalar_one()
        if customer_count >= cfg["CUSTOMER_DAILY_CAP"]:
            raise TopupError(
                "customer_daily_cap",
                f"This wallet has reached the demo limit of {cfg['CUSTOMER_DAILY_CAP']} top-ups per day.",
                429,
            )

        topup = Topup(
            id=uuid.uuid4(),
            deposit_id=uuid.uuid4(),
            customer_id=req.customer_id,
            amount_minor=req.amount_minor,
            currency=req.currency,
            country=req.country,
            provider=req.provider,
            msisdn=req.msisdn,
            status="CREATED",
            scenario=req.scenario,
            idempotency_key=req.idempotency_key,
            request_fingerprint=req.fingerprint,
            check_count=0,
        )
        db.session.add(topup)
        db.session.commit()  # the depositId is durable before pawaPay ever hears of it
        return topup, False
    except TopupError:
        # Nothing was written before any TopupError is raised. Commit (not rollback) only to
        # release the row and advisory locks without expiring objects the caller serialises.
        db.session.commit()
        raise
    except Exception:
        db.session.rollback()
        raise


def initiate(topup_id: uuid.UUID, decimals_in_amount: str = "TWO_PLACES") -> Topup:
    """Phase B: POST /v2/deposits outside any transaction, then record the answer."""
    snap = db.session.execute(
        select(
            Topup.deposit_id,
            Topup.customer_id,
            Topup.amount_minor,
            Topup.currency,
            Topup.provider,
            Topup.msisdn,
        ).where(Topup.id == topup_id)
    ).one()
    db.session.rollback()  # no transaction open across the HTTP call
    dep = snap.deposit_id
    try:
        client = get_client()
        amount = to_pawapay_amount(snap.amount_minor, snap.currency, decimals_in_amount)
        try:
            res = client.initiate_deposit(
                deposit_id=str(dep),
                amount=amount,
                currency=snap.currency,
                provider=snap.provider,
                phone_number=snap.msisdn,
                customer_message=CUSTOMER_MESSAGE,
                metadata=[{"customerId": str(snap.customer_id)}, {"source": "ringwise-demo"}],
            )
        except PawaPayTransportError as exc:
            # If the request may have reached pawaPay (read timeout, dropped connection), it can
            # still be in flight, and an immediate NOT_FOUND would race it. Only a request that
            # never left (connect failure) can be failed on NOT_FOUND straight away.
            record_event(
                EventInfo(source="initiate_response"),
                outcome="no_change",
                topup_id=topup_id,
                deposit_id=dep,
                detail=f"No answer from pawaPay ({exc}). Outcome unknown, checking status now.",
            )
            check_and_apply(dep, "status_check", not_found="ignore" if exc.request_sent else "fail")
            return _reload(topup_id)

        status = res.status
        if res.http_status >= 500 or (status is None and res.http_status not in (400, 401, 403, 404)):
            record_event(
                EventInfo(source="initiate_response", http_status=res.http_status, payload=res.body),
                outcome="no_change",
                topup_id=topup_id,
                deposit_id=dep,
                detail=(
                    f"HTTP {res.http_status} from pawaPay means the outcome is unknown, not failed. "
                    "Checking status now; only NOT_FOUND marks it failed."
                ),
            )
            check_and_apply(dep, "status_check", not_found="fail")
            return _reload(topup_id)

        if status in ("ACCEPTED", "DUPLICATE_IGNORED"):
            detail = (
                "pawaPay accepted the deposit and will send a callback."
                if status == "ACCEPTED"
                else "pawaPay already had this depositId (DUPLICATE_IGNORED), treated as accepted."
            )
            apply_result(
                dep,
                "ACCEPTED",
                res.body if isinstance(res.body, dict) else {},
                "initiate_response",
                event=EventInfo(
                    source="initiate_response",
                    http_status=res.http_status,
                    payload=res.body,
                    detail=detail,
                ),
            )
        elif status == "REJECTED" or (status is None and 400 <= res.http_status < 500):
            reason = res.failure_reason or {"failureCode": f"HTTP_{res.http_status}"}
            apply_result(
                dep,
                "REJECTED",
                {"failureReason": reason},
                "initiate_response",
                event=EventInfo(
                    source="initiate_response", http_status=res.http_status, payload=res.body
                ),
            )
        else:
            apply_result(
                dep,
                status,
                res.body if isinstance(res.body, dict) else {},
                "initiate_response",
                event=EventInfo(
                    source="initiate_response",
                    http_status=res.http_status,
                    payload=res.body,
                    detail="Unexpected initiation status.",
                ),
            )
    except Exception as exc:  # never lose track of a committed top-up
        db.session.rollback()
        log.exception("initiate failed for %s", dep)
        record_event(
            EventInfo(source="initiate_response"),
            outcome="error",
            topup_id=topup_id,
            deposit_id=dep,
            detail=f"Unexpected error while initiating ({type(exc).__name__}). Reconciliation will settle it.",
        )
    return _reload(topup_id)
