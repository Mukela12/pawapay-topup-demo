"""In-process background jobs (APScheduler), safe with several gunicorn workers and instances.

Every worker process starts its own BackgroundScheduler, but each job first takes a Postgres
session-level advisory lock with pg_try_advisory_lock on a dedicated AUTOCOMMIT connection.
Only the process holding the lock runs the job; the others skip that tick. Session-level locks
are released by pg_advisory_unlock or, if the process dies, when its connection closes.

On Elastic Beanstalk with a worker tier, set SCHEDULER_ENABLED=false on the web tier and let
cron.yaml POST /internal/reconcile instead.
"""
from __future__ import annotations

import atexit
import logging
import os
import threading

from sqlalchemy import text

from .extensions import db

log = logging.getLogger(__name__)

LOCK_MIGRATE = 727_300
LOCK_RECONCILE = 727_301
LOCK_OUTBOX = 727_302

_started_pid: int | None = None
_start_lock = threading.Lock()


def run_locked(app, lock_key: int, job, name: str) -> bool:
    """Run job() only if this process wins pg_try_advisory_lock(lock_key). Returns True if run."""
    with app.app_context():
        try:
            with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_key}).scalar()
                if not got:
                    return False
                try:
                    job()
                    return True
                finally:
                    conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key})
        except Exception:
            log.exception("scheduled job %s failed", name)
            db.session.rollback()
            return False
        finally:
            db.session.remove()


RECONCILE_INTERVAL_SECONDS = 60


def _ran_recently(min_gap_seconds: int) -> bool:
    """True if another worker already ran a scheduled reconcile within min_gap_seconds.

    Each gunicorn worker has its own scheduler, so without this check the job would run once per
    worker per interval. The check runs while the advisory lock is held, so it cannot race.
    """
    last = db.session.execute(
        text(
            "SELECT EXTRACT(EPOCH FROM (now() - max(started_at))) FROM reconcile_runs "
            "WHERE trigger = 'schedule'"
        )
    ).scalar()
    return last is not None and float(last) < min_gap_seconds


def _reconcile_job() -> None:
    from .pawapay.client import is_configured
    from .services.reconcile import reconcile_once

    if not is_configured():
        return
    if _ran_recently(RECONCILE_INTERVAL_SECONDS - 10):
        return
    reconcile_once("schedule")


def _outbox_job() -> None:
    from .services.outbox import relay_once

    relay_once()


def start_scheduler(app) -> bool:
    """Start the scheduler once per process. Returns True if it was started by this call."""
    global _started_pid
    if not app.config.get("SCHEDULER_ENABLED", True):
        return False
    with _start_lock:
        if _started_pid == os.getpid():
            return False
        from apscheduler.schedulers.background import BackgroundScheduler

        scheduler = BackgroundScheduler(
            daemon=True, job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 30}
        )
        scheduler.add_job(
            run_locked,
            "interval",
            seconds=RECONCILE_INTERVAL_SECONDS,
            args=[app, LOCK_RECONCILE, _reconcile_job, "reconcile"],
            id="reconcile",
        )
        scheduler.add_job(
            run_locked,
            "interval",
            seconds=15,
            args=[app, LOCK_OUTBOX, _outbox_job, "outbox"],
            id="outbox",
        )
        scheduler.start()
        _started_pid = os.getpid()
        atexit.register(lambda: scheduler.shutdown(wait=False))
        log.info("scheduler started in pid %s (reconcile 60s, outbox 15s)", os.getpid())
        return True
