"""Transactional outbox relay.

Rows are written in the same transaction as the ledger credit, so a receipt or calling-credit
grant exists if and only if the wallet was credited. The relay claims pending rows with
FOR UPDATE SKIP LOCKED (safe with several workers), "delivers" them (a log line in this demo;
in production an HTTP call to the billing system, which dedupes on dedupe_key), and marks them
delivered.
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select

from ..extensions import db
from ..models import Outbox

log = logging.getLogger(__name__)

TOPIC_LABELS = {"receipt.issued": "Receipt issued", "voip_credit.granted": "Calling credit granted"}


def relay_once(limit: int = 50) -> int:
    try:
        rows = db.session.execute(
            select(Outbox)
            .where(Outbox.delivered_at.is_(None))
            .order_by(Outbox.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).scalars().all()
        for row in rows:
            row.attempts = (row.attempts or 0) + 1
            log.info(
                "outbox.deliver topic=%s dedupe_key=%s amount_minor=%s currency=%s",
                row.topic,
                row.dedupe_key,
                (row.payload or {}).get("amount_minor"),
                (row.payload or {}).get("currency"),
            )
            row.delivered_at = func.now()
        db.session.commit()
        return len(rows)
    except Exception:
        db.session.rollback()
        raise
