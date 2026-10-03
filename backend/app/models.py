"""Database models. Money is always stored as integer minor units (bigint)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CHAR, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .extensions import db

OPEN_STATES = ("CREATED", "ACCEPTED", "PROCESSING", "IN_RECONCILIATION")
TOPUP_STATES = OPEN_STATES + ("COMPLETED", "FAILED", "REJECTED", "NEEDS_ATTENTION")
EVENT_SOURCES = (
    "initiate_response",
    "callback",
    "status_check",
    "reconcile",
    "replay",
    "forged_test",
    "resend_request",
)
SIGNATURE_STATES = ("verified", "invalid", "missing", "not_checked")
EVENT_OUTCOMES = (
    "applied",
    "duplicate_ignored",
    "rejected",
    "held_for_review",
    "no_change",
    "error",
)
OUTBOX_TOPICS = ("receipt.issued", "voip_credit.granted")
RECONCILE_TRIGGERS = ("schedule", "manual", "eb_cron")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Customer(db.Model):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class WalletAccount(db.Model):
    __tablename__ = "wallet_accounts"
    __table_args__ = (
        UniqueConstraint("customer_id", "currency"),
        CheckConstraint("balance_minor >= 0", name="balance_non_negative"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False
    )
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    balance_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = _created_at()


class Topup(db.Model):
    __tablename__ = "topups"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in(TOPUP_STATES)})", name="status_valid"),
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        CheckConstraint("msisdn ~ '^[1-9][0-9]{7,14}$'", name="msisdn_format"),
        UniqueConstraint("customer_id", "idempotency_key"),
        Index("ix_topups_customer_created", "customer_id", "created_at"),
        Index("ix_topups_msisdn_created", "msisdn", "created_at"),
        Index(
            "ix_topups_open_created",
            "created_at",
            postgresql_where=text(f"status IN ({_in(OPEN_STATES)})"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    # Our pawaPay depositId: a uuid4 generated here and committed BEFORE calling pawaPay.
    deposit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    country: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    msisdn: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="CREATED", server_default=text("'CREATED'")
    )
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_message: Mapped[str | None] = mapped_column(Text)
    provider_txn_id: Mapped[str | None] = mapped_column(String(128))
    scenario: Mapped[str | None] = mapped_column(String(64))
    # Idempotency-Key header from the client plus a fingerprint of the request body.
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created_at()
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )


class LedgerEntry(db.Model):
    """Append-only. A PL/pgSQL trigger (see the migration) raises on UPDATE or DELETE."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        UniqueConstraint("topup_id", "kind"),
        CheckConstraint("kind IN ('topup_credit')", name="kind_valid"),
        CheckConstraint("amount_minor > 0", name="amount_positive"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wallet_accounts.id"), nullable=False, index=True
    )
    topup_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topups.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class PaymentEvent(db.Model):
    __tablename__ = "payment_events"
    __table_args__ = (
        CheckConstraint(f"source IN ({_in(EVENT_SOURCES)})", name="source_valid"),
        CheckConstraint(f"signature IN ({_in(SIGNATURE_STATES)})", name="signature_valid"),
        CheckConstraint(f"outcome IN ({_in(EVENT_OUTCOMES)})", name="outcome_valid"),
        Index("ix_payment_events_topup_created", "topup_id", "created_at"),
        Index("ix_payment_events_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    topup_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topups.id")
    )
    deposit_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    signature: Mapped[str] = mapped_column(
        String(16), nullable=False, default="not_checked", server_default=text("'not_checked'")
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[Any | None] = mapped_column(JSONB)
    # Only content-type, content-digest, signature-input and presence flags. Never auth.
    headers: Mapped[Any | None] = mapped_column(JSONB)
    # Exact callback bytes (UTF-8), kept so a callback can be replayed byte for byte.
    raw_body: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class Outbox(db.Model):
    __tablename__ = "outbox"
    __table_args__ = (
        CheckConstraint(f"topic IN ({_in(OUTBOX_TOPICS)})", name="topic_valid"),
        Index(
            "ix_outbox_pending",
            "created_at",
            postgresql_where=text("delivered_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    topic: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[Any] = mapped_column(JSONB, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = _created_at()
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReconcileRun(db.Model):
    __tablename__ = "reconcile_runs"
    __table_args__ = (
        CheckConstraint(f"trigger IN ({_in(RECONCILE_TRIGGERS)})", name="trigger_valid"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    settled: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    still_open: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    failed_not_found: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
