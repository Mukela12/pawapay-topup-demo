"""Console read endpoints and the recheck / resend actions."""
import httpx
from conftest import deposit_data, found, make_customer, make_topup

from app.extensions import db
from app.models import Topup
from app.services.topups import apply_result


def test_stats_and_list(ctx, client):
    cid = make_customer()
    done = make_topup(cid, amount_minor=1000, msisdn="260763456789")
    apply_result(done.deposit_id, "COMPLETED", deposit_data(done), "callback")
    failed = make_topup(cid, msisdn="260763456039")
    apply_result(failed.deposit_id, "FAILED", {"failureReason": {"failureCode": "PAYMENT_NOT_APPROVED"}}, "callback")
    make_topup(cid, msisdn="260763456129")  # open
    held = make_topup(cid, msisdn="260973456789")
    apply_result(held.deposit_id, "WEIRD", {}, "status_check")

    stats = client.get("/api/console/stats").json
    assert stats["completed_today"] == 1
    assert stats["failed_today"] == 1
    assert stats["open_now"] == 1
    assert stats["needs_attention"] == 1
    assert stats["success_rate_7d"] == 0.5
    assert stats["median_seconds_to_final"] is not None
    assert stats["total_credited"] == [{"currency": "ZMW", "amount_minor": 1000}]

    listing = client.get("/api/console/topups").json["topups"]
    assert len(listing) == 4
    assert all("*" in t["phone"] for t in listing)  # masked
    assert listing[0]["phone"].startswith("2609")
    only_open = client.get("/api/console/topups?status=open").json["topups"]
    assert [t["status"] for t in only_open] == ["ACCEPTED"]
    by_phone = client.get("/api/console/topups?q=6039").json["topups"]
    assert len(by_phone) == 1 and by_phone[0]["status"] == "FAILED"
    by_dep = client.get(f"/api/console/topups?q={str(done.deposit_id)[:8]}").json["topups"]
    assert by_dep[0]["deposit_id"] == str(done.deposit_id)


def test_detail_includes_events_ledger_and_outbox(ctx, client):
    t = make_topup(amount_minor=1000)
    apply_result(t.deposit_id, "COMPLETED", deposit_data(t), "callback")
    data = client.get(f"/api/console/topups/{t.id}").json
    assert data["topup"]["status"] == "COMPLETED"
    assert data["ledger_entry"]["amount_minor"] == 1000
    assert {o["label"] for o in data["outbox"]} == {"Receipt issued", "Calling credit granted"}
    assert data["events"][0]["payload"]["depositId"] == str(t.deposit_id)
    assert data["balance_minor"] == 1000
    # lookup by depositId works too
    assert client.get(f"/api/console/topups/{t.deposit_id}").json["topup"]["id"] == str(t.id)
    events = client.get("/api/console/events").json["events"]
    assert events[0]["source"] == "callback"


def test_recheck_applies_status(ctx, client, pawapay):
    t = make_topup(amount_minor=1000, age_seconds=120)
    pawapay.get(f"/v2/deposits/{t.deposit_id}").mock(
        return_value=httpx.Response(200, json=found(deposit_data(t)))
    )
    data = client.post(f"/api/console/topups/{t.id}/recheck").json
    assert data["result"]["outcome"] == "applied"
    assert data["topup"]["status"] == "COMPLETED"
    assert data["topup"]["check_count"] == 1


def test_recheck_not_found_on_young_topup_is_left_alone(ctx, client, pawapay):
    t = make_topup()
    pawapay.get(f"/v2/deposits/{t.deposit_id}").mock(return_value=httpx.Response(200, json={"status": "NOT_FOUND"}))
    data = client.post(f"/api/console/topups/{t.id}/recheck").json
    assert data["topup"]["status"] == "ACCEPTED"


def test_resend_callback_requires_final(ctx, client, pawapay):
    t = make_topup()
    assert client.post(f"/api/console/topups/{t.id}/resend-callback").status_code == 409
    apply_result(t.deposit_id, "COMPLETED", deposit_data(t), "callback")
    route = pawapay.post(f"/v2/deposits/resend-callback/{t.deposit_id}").mock(
        return_value=httpx.Response(200, json={"depositId": str(t.deposit_id), "status": "ACCEPTED"})
    )
    data = client.post(f"/api/console/topups/{t.id}/resend-callback").json
    assert route.called
    assert data["result"]["pawapay_status"] == "ACCEPTED"
    assert data["result"]["outcome"] == "no_change"


def test_console_actions_need_token(app, ctx, client):
    app.config["PAWAPAY_API_TOKEN"] = None
    t = make_topup()
    assert client.post(f"/api/console/topups/{t.id}/recheck").status_code == 503
    assert client.post("/api/console/reconcile").status_code == 503
    assert client.get("/api/console/topups").status_code == 200  # read-only still works


def test_manual_reconcile_endpoint(ctx, client, pawapay):
    t = make_topup(age_seconds=120)
    pawapay.get(f"/v2/deposits/{t.deposit_id}").mock(return_value=httpx.Response(200, json={"status": "NOT_FOUND"}))
    run = client.post("/api/console/reconcile").json["run"]
    assert run["trigger"] == "manual" and run["failed_not_found"] == 1
    runs = client.get("/api/console/reconcile-runs").json["runs"]
    assert runs[0]["id"] == run["id"]
    assert db.session.get(Topup, t.id, populate_existing=True).status == "FAILED"
