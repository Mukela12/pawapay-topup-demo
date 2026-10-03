"""Run `flask db upgrade` under a Postgres advisory lock.

Used by the Railway/Docker start command and the Elastic Beanstalk predeploy hook. Several
instances may run it at once; the session-level advisory lock serialises them and Alembic
makes the second run a no-op. The lock connection is AUTOCOMMIT so it never sits idle in a
transaction (which idle_in_transaction_session_timeout could kill, silently dropping the lock).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from flask_migrate import upgrade  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import MIGRATIONS_DIR, create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.scheduler import LOCK_MIGRATE  # noqa: E402


def main() -> int:
    app = create_app({"SCHEDULER_ENABLED": False})
    with app.app_context():
        started = time.monotonic()
        with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            print(f"migrate: waiting for advisory lock {LOCK_MIGRATE}", flush=True)
            conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": LOCK_MIGRATE})
            try:
                upgrade(directory=str(MIGRATIONS_DIR))
                print(f"migrate: database at head ({time.monotonic() - started:.1f}s)", flush=True)
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_MIGRATE})
    return 0


if __name__ == "__main__":
    sys.exit(main())
