"""reconcile_once, the outbox relay, the scheduler lock and the ops endpoints."""
import httpx
from conftest import deposit_data, found, make_topup
from sqlalchemy import func, select

from app.extensions import db
from app.models import LedgerEntry, Outbox, ReconcileRun, Topup
from app.scheduler import LOCK_RECONCILE, run_locked
from app.services.outbox import relay_once
from app.services.reconcile import reconcile_once
from app.services.topups import apply_result


def fresh(t):
    row = db.session.get(Topup, t.id, populate_existing=True)
    db.session.rollback()
    return row


def test_reconcile_once_settles_missed_callbacks(ctx, pawapay):
    lost = make_topup(age_seconds=120)  # pawaPay never got it
    pending = make_topup(age_seconds=120, msisdn="260763456129")
    paid = make_topup(age_seconds=120, amount_minor=2000, msisdn="260973456789")
    young = make_topup(age_seconds=0)  # not yet due
    pawapay.get(f"/v2/deposits/{lost.deposit_id}").mock(
        return_value=httpx.Response(200, json={"status": "NOT_FOUND"})
    )
    pawapay.get(f"/v2/deposits/{pending.deposit_id}").mock(
        return_value=httpx.Response(200, json=found(deposit_data(pending, status="IN_RECONCILIATION")))
    )
    pawapay.get(f"/v2/deposits/{paid.deposit_id}").mock(
        return_value=httpx.Response(200, json=found(deposit_data(paid, amount="20.00")))
    )
    young_route = pawapay.get(f"/v2/deposits/{young.deposit_id}")

    run = reconcile_once("manual")

    assert run["checked"] == 3
    assert run["failed_not_found"] == 1
    assert run["still_open"] == 1
    assert run["settled"] == 1
    assert run["errors"] == 0
    assert run["finished_at"] is not None
    assert not young_route.called

    lost_row = fresh(lost)
    assert lost_row.status == "FAILED" and lost_row.failure_code == "NOT_FOUND_AT_PROVIDER"
    assert fresh(pending).status == "IN_RECONCILIATION"  # stays open
    assert fresh(paid).status == "COMPLETED"
    assert db.session.execute(select(func.count()).select_from(LedgerEntry)).scalar_one() == 1
    assert fresh(paid).check_count == 1
    assert db.session.execute(select(func.count()).select_from(ReconcileRun)).scalar_one() == 1


def test_reconcile_counts_errors_and_retries_later(ctx, pawapay):
    t = make_topup(age_seconds=120)
    pawapay.get(f"/v2/deposits/{t.deposit_id}").mock(side_effect=httpx.ConnectTimeout("x"))
    run = reconcile_once("schedule")
    assert run["errors"] == 1 and run["checked"] == 1
    assert fresh(t).status == "ACCEPTED"


def test_reconcile_credit_is_idempotent_with_callback(ctx, pawapay):
    t = make_topup(age_seconds=120, amount_minor=500)
    apply_result(t.deposit_id, "COMPLETED", deposit_data(t, amount="5"), "callback")
    pawapay.get(f"/v2/deposits/{t.deposit_id}").mock(
        return_value=httpx.Response(200, json=found(deposit_data(t, amount="5")))
    )
    run = reconcile_once("manual")
    assert run["checked"] == 0  # already final, never claimed
    assert db.session.execute(select(func.count()).select_from(LedgerEntry)).scalar_one() == 1


def test_outbox_relay_marks_rows_delivered(ctx):
    t = make_topup()
    apply_result(t.deposit_id, "COMPLETED", deposit_data(t), "callback")
    assert relay_once() == 2
    assert relay_once() == 0
    rows = db.session.execute(select(Outbox)).scalars().all()
    assert all(r.delivered_at is not None and r.attempts == 1 for r in rows)


def test_scheduler_job_runs_only_for_the_lock_holder(app):
    calls = []
    with app.app_context():
        with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as other:
            other.execute(db.text("SELECT pg_advisory_lock(:k)"), {"k": LOCK_RECONCILE})
            assert run_locked(app, LOCK_RECONCILE, lambda: calls.append(1), "test") is False
            other.execute(db.text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_RECONCILE})
        assert run_locked(app, LOCK_RECONCILE, lambda: calls.append(1), "test") is True
    assert calls == [1]


def test_internal_reconcile_requires_token(app, client, ctx, pawapay):
    assert client.post("/internal/reconcile").status_code == 403
    assert client.post("/internal/reconcile", headers={"X-Internal-Token": "wrong"}).status_code == 403
    ok = client.post("/internal/reconcile", headers={"X-Internal-Token": "internal-test-token"})
    assert ok.status_code == 200 and ok.json["run"]["trigger"] == "manual"
    sqsd = client.post(
        "/internal/reconcile",
        headers={"X-Internal-Token": "internal-test-token", "X-Aws-Sqsd-Taskname": "reconcile"},
    )
    assert sqsd.json["run"]["trigger"] == "eb_cron"


def test_internal_reconcile_allows_localhost_on_eb_worker(app, client, ctx, pawapay):
    app.config["EB_WORKER"] = True
    resp = client.post("/internal/reconcile", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert resp.status_code == 200 and resp.json["run"]["trigger"] == "eb_cron"
    remote = client.post("/internal/reconcile", environ_base={"REMOTE_ADDR": "10.0.0.8"})
    assert remote.status_code == 403


def test_eb_worker_localhost_exemption_ignores_spoofed_forwarded_for(app, client, ctx, pawapay, monkeypatch):
    """With ProxyFix trusting one hop, a caller that reaches gunicorn directly must not get the
    worker exemption by sending X-Forwarded-For: 127.0.0.1. Behind nginx (which appends the real
    peer) only sqsd on localhost passes."""
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.config["EB_WORKER"] = True
    monkeypatch.setattr(app, "wsgi_app", ProxyFix(app.wsgi_app, x_for=1, x_proto=1))

    def call(peer, xff=None):
        headers = {"X-Forwarded-For": xff} if xff else {}
        return client.post("/internal/reconcile", headers=headers, environ_base={"REMOTE_ADDR": peer}).status_code

    assert call("203.0.113.9") == 403  # direct, no proxy
    assert call("203.0.113.9", "127.0.0.1") == 403  # direct with a spoofed header
    assert call("127.0.0.1", "127.0.0.1") == 200  # sqsd -> nginx -> gunicorn
    assert call("127.0.0.1", "203.0.113.9") == 403  # outside caller via nginx
    assert call("127.0.0.1", "127.0.0.1, 203.0.113.9") == 403  # spoof attempt via nginx
    assert call("127.0.0.1") == 200  # sqsd straight to gunicorn, no proxy header
    # Without ProxyFix (TRUST_PROXY_HOPS=0 by mistake) nginx traffic from outside still fails.
    monkeypatch.undo()
    app.config["EB_WORKER"] = True
    assert call("127.0.0.1", "203.0.113.9") == 403
    assert call("127.0.0.1", "127.0.0.1") == 200
    # The token still works from anywhere.
    assert client.post(
        "/internal/reconcile",
        headers={"X-Internal-Token": "internal-test-token", "X-Forwarded-For": "198.51.100.1"},
        environ_base={"REMOTE_ADDR": "203.0.113.9"},
    ).status_code == 200


def test_healthz(app, client):
    data = client.get("/healthz").json
    assert data["ok"] is True and data["db"] is True
    assert data["pawapay_configured"] is True
    assert data["signature_required"] is False
    app.config["PAWAPAY_API_TOKEN"] = None
    assert client.get("/healthz").json["pawapay_configured"] is False


def test_scheduled_reconcile_runs_once_per_interval_across_workers(ctx, pawapay, monkeypatch):
    """Two workers ticking in the same interval must produce one scheduled run, not two."""
    from app import scheduler

    monkeypatch.setattr("app.pawapay.client.is_configured", lambda: True)
    scheduler._reconcile_job()  # first worker's tick
    scheduler._reconcile_job()  # second worker's tick a few seconds later
    runs = db.session.scalar(
        select(func.count()).select_from(ReconcileRun).where(ReconcileRun.trigger == "schedule")
    )
    assert runs == 1
