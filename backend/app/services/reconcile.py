"""Reconciliation: settle top-ups whose callback never arrived.

1. Claim open top-ups older than RECONCILE_AFTER_SECONDS in a short transaction
   (FOR UPDATE SKIP LOCKED, stamp last_checked_at), then commit.
2. Call GET /v2/deposits/{id} for each one OUTSIDE any transaction.
3. Apply each answer through apply_result in its own transaction.
NOT_FOUND means pawaPay never received the deposit: FAILED with NOT_FOUND_AT_PROVIDER.
PROCESSING and IN_RECONCILIATION stay open for the next run.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from flask import current_app
from sqlalchemy import func, select, update

from ..extensions import db
from ..models import OPEN_STATES, ReconcileRun, Topup
from .topups import FINAL_STATES, check_and_apply

log = logging.getLogger(__name__)

RECHECK_GAP_SECONDS = 15  # do not re-check the same top-up twice within this window


def claim_due(limit: int) -> list:
    after = current_app.config["RECONCILE_AFTER_SECONDS"]
    try:
        rows = db.session.execute(
            select(Topup.deposit_id)
            .where(
                Topup.status.in_(OPEN_STATES),
                Topup.created_at < func.now() - timedelta(seconds=after),
                (Topup.last_checked_at.is_(None))
                | (Topup.last_checked_at < func.now() - timedelta(seconds=RECHECK_GAP_SECONDS)),
            )
            .order_by(Topup.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).scalars().all()
        if rows:
            db.session.execute(
                update(Topup)
                .where(Topup.deposit_id.in_(rows))
                .values(last_checked_at=func.now(), check_count=Topup.check_count + 1)
                .execution_options(synchronize_session=False)
            )
        db.session.commit()
        return list(rows)
    except Exception:
        db.session.rollback()
        raise


def reconcile_once(trigger: str = "manual") -> dict:
    run = ReconcileRun(trigger=trigger)
    db.session.add(run)
    db.session.commit()

    counts = {"checked": 0, "settled": 0, "still_open": 0, "failed_not_found": 0, "errors": 0}
    try:
        due = claim_due(current_app.config.get("RECONCILE_BATCH_LIMIT", 50))
    except Exception:
        log.exception("reconcile claim failed")
        due = []
        counts["errors"] += 1

    for dep in due:  # each HTTP call happens with no transaction open
        counts["checked"] += 1
        try:
            result = check_and_apply(dep, "reconcile", not_found="fail", mark=False)
        except Exception:
            db.session.rollback()
            log.exception("reconcile failed for %s", dep)
            counts["errors"] += 1
            continue
        if result.kind == "error":
            counts["errors"] += 1
        elif result.kind == "not_found":
            counts["failed_not_found"] += 1
        else:
            status_after = result.apply.status if result.apply else None
            if status_after in FINAL_STATES:
                counts["settled"] += 1
            else:
                counts["still_open"] += 1

    db.session.execute(
        update(ReconcileRun)
        .where(ReconcileRun.id == run.id)
        .values(finished_at=func.now(), **counts)
        .execution_options(synchronize_session=False)
    )
    db.session.commit()
    finished = db.session.execute(
        select(ReconcileRun).where(ReconcileRun.id == run.id).execution_options(populate_existing=True)
    ).scalar_one()
    db.session.commit()
    if counts["checked"]:
        log.info("reconcile run %s trigger=%s %s", run.id, trigger, counts)
    return serialize_run(finished)


def serialize_run(run: ReconcileRun) -> dict:
    return {
        "id": str(run.id),
        "trigger": run.trigger,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "checked": run.checked,
        "settled": run.settled,
        "still_open": run.still_open,
        "failed_not_found": run.failed_not_found,
        "errors": run.errors,
    }
