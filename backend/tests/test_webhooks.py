"""POST /webhooks/pawapay plus the console replay and forged-callback actions."""
import httpx
from conftest import (
    KEY_ID,
    callback_bytes,
    deposit_data,
    found,
    make_topup,
    new_key,
    signed_headers,
)
from sqlalchemy import func, select

from app.extensions import db
from app.models import LedgerEntry, PaymentEvent, Topup, WalletAccount


def post_callback(client, body: bytes, headers: dict | None = None, **kw):
    headers = dict(headers or {"Content-Type": "application/json"})
    return client.post("/webhooks/pawapay", data=body, headers=headers, **kw)


def fresh(t):
    row = db.session.get(Topup, t.id, populate_existing=True)
    db.session.rollback()
    return row


def ledger_count():
    n = db.session.execute(select(func.count()).select_from(LedgerEntry)).scalar_one()
    db.session.rollback()
    return n


def balance(customer_id):
    v = db.session.execute(
        select(WalletAccount.balance_minor).where(WalletAccount.customer_id == customer_id)
    ).scalar_one_or_none()
    db.session.rollback()
    return v or 0


def last_event(t):
    e = db.session.execute(
        select(PaymentEvent).where(PaymentEvent.topup_id == t.id).order_by(PaymentEvent.created_at.desc()).limit(1)
    ).scalar_one()
    db.session.rollback()
    return e


def mock_keys(pawapay, pem):
    return pawapay.get("/v2/public-key/http").mock(
        return_value=httpx.Response(200, json=[{"id": KEY_ID, "key": pem}])
    )


# --------------------------------------------------------------------------- signed callbacks
def test_valid_signed_callback_is_applied_without_status_check(app, ctx, client, pawapay):
    app.config["PAWAPAY_REQUIRE_SIGNATURE"] = True
    key, pem = new_key()
    mock_keys(pawapay, pem)
    status_route = pawapay.get(url__regex=r"/v2/deposits/.*")
    t = make_topup(amount_minor=1500)
    body = callback_bytes(deposit_data(t, amount="15.00"))
    resp = post_callback(client, body, signed_headers(body, key))
    assert resp.status_code == 200, resp.json
    assert resp.json == {"outcome": "applied", "signature": "verified"}
    assert not status_route.called
    assert fresh(t).status == "COMPLETED"
    assert balance(t.customer_id) == 1500
    ev = last_event(t)
    assert ev.signature == "verified" and ev.source == "callback"
    assert ev.headers["has_signature"] is True
    assert "signature" not in ev.headers  # presence flag only, never the value
    assert "authorization" not in {k.lower() for k in ev.headers}


def test_tampered_body_fails_content_digest(app, ctx, client, pawapay):
    app.config["PAWAPAY_REQUIRE_SIGNATURE"] = True
    key, pem = new_key()
    mock_keys(pawapay, pem)
    t = make_topup(amount_minor=1500)
    original = callback_bytes(deposit_data(t, amount="15.00"))
    tampered = callback_bytes(deposit_data(t, amount="15000.00"))
    resp = post_callback(client, tampered, signed_headers(original, key))
    assert resp.status_code == 401
    assert resp.json["outcome"] == "rejected"
    assert fresh(t).status == "ACCEPTED"
    assert ledger_count() == 0
    ev = last_event(t)
    assert ev.outcome == "rejected" and ev.signature == "invalid" and "digest_mismatch" in ev.detail


def test_wrong_key_fails(app, ctx, client, pawapay):
    app.config["PAWAPAY_REQUIRE_SIGNATURE"] = True
    key, _ = new_key()
    _, other_pem = new_key()
    mock_keys(pawapay, other_pem)
    t = make_topup()
    body = callback_bytes(deposit_data(t))
    resp = post_callback(client, body, signed_headers(body, key))
    assert resp.status_code == 401
    assert "bad_signature" in last_event(t).detail
    assert fresh(t).status == "ACCEPTED"


def test_expired_signature_fails(app, ctx, client, pawapay):
    import time

    app.config["PAWAPAY_REQUIRE_SIGNATURE"] = True
    key, pem = new_key()
    mock_keys(pawapay, pem)
    t = make_topup()
    body = callback_bytes(deposit_data(t))
    old = int(time.time()) - 3600
    resp = post_callback(client, body, signed_headers(body, key, created=old, expires=old + 60))
    assert resp.status_code == 401
    assert "expired" in last_event(t).detail
    assert fresh(t).status == "ACCEPTED"


def test_authority_comes_from_x_forwarded_host(app, ctx, client, pawapay):
    app.config["PAWAPAY_REQUIRE_SIGNATURE"] = True
    key, pem = new_key()
    mock_keys(pawapay, pem)
    t = make_topup()
    body = callback_bytes(deposit_data(t))
    headers = signed_headers(body, key, authority="web-production-b64c0.up.railway.app")
    headers["X-Forwarded-Host"] = "web-production-b64c0.up.railway.app"
    resp = post_callback(client, body, headers)
    assert resp.status_code == 200 and resp.json["signature"] == "verified"


def test_missing_signature_rejected_when_required(app, ctx, client, pawapay):
    app.config["PAWAPAY_REQUIRE_SIGNATURE"] = True
    t = make_topup()
    resp = post_callback(client, callback_bytes(deposit_data(t)))
    assert resp.status_code == 401
    ev = last_event(t)
    assert ev.outcome == "rejected" and ev.signature == "missing"
    assert fresh(t).status == "ACCEPTED"


# --------------------------------------------------------------------------- hint mode
def test_unsigned_callback_is_a_hint_confirmed_by_status_check(ctx, client, pawapay):
    t = make_topup(amount_minor=1000)
    route = pawapay.get(f"/v2/deposits/{t.deposit_id}").mock(
        return_value=httpx.Response(200, json=found(deposit_data(t, amount="10.00")))
    )
    resp = post_callback(client, callback_bytes(deposit_data(t)))
    assert resp.status_code == 200
    assert resp.json == {"outcome": "applied", "signature": "missing"}
    assert route.called
    assert fresh(t).status == "COMPLETED"
    assert balance(t.customer_id) == 1000
    assert "hint" in last_event(t).detail


def test_hint_mode_trusts_the_api_not_the_body(ctx, client, pawapay):
    t = make_topup()
    failed = deposit_data(
        t, status="FAILED", failureReason={"failureCode": "INSUFFICIENT_BALANCE", "failureMessage": "low"}
    )
    pawapay.get(f"/v2/deposits/{t.deposit_id}").mock(return_value=httpx.Response(200, json=found(failed)))
    resp = post_callback(client, callback_bytes(deposit_data(t, status="COMPLETED")))
    assert resp.status_code == 200
    row = fresh(t)
    assert row.status == "FAILED" and row.failure_code == "INSUFFICIENT_BALANCE"
    assert ledger_count() == 0


def test_forged_unsigned_failed_callback_cannot_deny_a_pending_credit(ctx, client, pawapay):
    t = make_topup()
    pawapay.get(f"/v2/deposits/{t.deposit_id}").mock(
        return_value=httpx.Response(200, json=found(deposit_data(t, status="PROCESSING")))
    )
    resp = post_callback(client, callback_bytes(deposit_data(t, status="FAILED")))
    assert resp.status_code == 200
    assert fresh(t).status == "PROCESSING"  # still open, the real COMPLETED can land later


def test_invalid_signature_falls_back_to_hint_when_not_required(ctx, client, pawapay):
    key, _ = new_key()
    _, other_pem = new_key()
    mock_keys(pawapay, other_pem)
    t = make_topup()
    pawapay.get(f"/v2/deposits/{t.deposit_id}").mock(
        return_value=httpx.Response(200, json=found(deposit_data(t)))
    )
    body = callback_bytes(deposit_data(t))
    resp = post_callback(client, body, signed_headers(body, key))
    assert resp.status_code == 200
    assert resp.json["signature"] == "invalid"
    assert fresh(t).status == "COMPLETED"


def test_duplicate_callback_returns_200_and_credits_once(ctx, client, pawapay):
    t = make_topup(amount_minor=1000)
    pawapay.get(f"/v2/deposits/{t.deposit_id}").mock(
        return_value=httpx.Response(200, json=found(deposit_data(t)))
    )
    body = callback_bytes(deposit_data(t))
    first = post_callback(client, body)
    second = post_callback(client, body)
    assert first.status_code == 200 and first.json["outcome"] == "applied"
    assert second.status_code == 200 and second.json["outcome"] == "duplicate_ignored"
    assert ledger_count() == 1
    assert balance(t.customer_id) == 1000


def test_unknown_deposit_is_acknowledged(ctx, client, pawapay):
    import uuid

    dep = uuid.uuid4()
    resp = post_callback(client, callback_bytes({"depositId": str(dep), "status": "COMPLETED"}))
    assert resp.status_code == 200 and resp.json["outcome"] == "no_change"


def test_status_check_failure_asks_pawapay_to_retry(ctx, client, pawapay):
    t = make_topup()
    pawapay.get(f"/v2/deposits/{t.deposit_id}").mock(side_effect=httpx.ConnectTimeout("boom"))
    resp = post_callback(client, callback_bytes(deposit_data(t)))
    assert resp.status_code == 503
    assert fresh(t).status == "ACCEPTED"


def test_malformed_body_is_400(ctx, client, pawapay):
    resp = post_callback(client, b"not json")
    assert resp.status_code == 400


# --------------------------------------------------------------------------- console actions
def test_forged_callback_action_is_rejected_with_no_state_change(ctx, client, pawapay):
    _, pem = new_key()  # stands in for pawaPay's real public key
    mock_keys(pawapay, pem)
    status_route = pawapay.get(url__regex=r"/v2/deposits/.*")
    t = make_topup(amount_minor=1000)
    resp = client.post(f"/api/console/topups/{t.id}/forged-callback")
    assert resp.status_code == 200
    data = resp.json
    assert data["result"]["http_status"] == 401
    assert data["result"]["outcome"] == "rejected"
    assert data["result"]["signature"] == "invalid"
    assert data["result"]["signature_reason"] == "bad_signature"
    assert data["status_before"] == data["status_after"] == "ACCEPTED"
    assert data["balance_before"] == data["balance_after"] == 0
    assert not status_route.called
    ev = last_event(t)
    assert ev.source == "forged_test" and ev.outcome == "rejected"
    assert ledger_count() == 0


def test_forged_callback_rejected_even_without_token(app, ctx, client):
    app.config["PAWAPAY_API_TOKEN"] = None
    t = make_topup()
    resp = client.post(f"/api/console/topups/{t.id}/forged-callback")
    assert resp.json["result"]["http_status"] == 401
    assert resp.json["result"]["signature_reason"] == "key_unavailable"
    assert fresh(t).status == "ACCEPTED"


def test_replay_last_callback_is_a_duplicate(ctx, client, pawapay):
    t = make_topup(amount_minor=1000)
    pawapay.get(f"/v2/deposits/{t.deposit_id}").mock(
        return_value=httpx.Response(200, json=found(deposit_data(t)))
    )
    assert post_callback(client, callback_bytes(deposit_data(t))).json["outcome"] == "applied"
    resp = client.post(f"/api/console/topups/{t.id}/replay-callback")
    assert resp.status_code == 200
    data = resp.json
    assert data["result"]["outcome"] == "duplicate_ignored"
    assert data["balance_before"] == data["balance_after"] == 1000
    assert data["status_after"] == "COMPLETED"
    assert last_event(t).source == "replay"
    assert ledger_count() == 1


def test_replay_without_callback_is_404(ctx, client):
    t = make_topup()
    resp = client.post(f"/api/console/topups/{t.id}/replay-callback")
    assert resp.status_code == 404
    assert resp.json["error"]["code"] == "no_callback"


def test_replay_works_when_signatures_are_required(app, ctx, client, pawapay):
    """Stored callbacks keep no Signature header, so a replay can never re-verify. It is checked
    against GET /v2/deposits instead and must still show duplicate_ignored, not a 401."""
    app.config["PAWAPAY_REQUIRE_SIGNATURE"] = True
    key, pem = new_key()
    mock_keys(pawapay, pem)
    t = make_topup(amount_minor=1000)
    status_route = pawapay.get(f"/v2/deposits/{t.deposit_id}").mock(
        return_value=httpx.Response(200, json=found(deposit_data(t)))
    )
    body = callback_bytes(deposit_data(t))
    assert post_callback(client, body, signed_headers(body, key)).json["outcome"] == "applied"
    assert not status_route.called

    resp = client.post(f"/api/console/topups/{t.id}/replay-callback")
    assert resp.status_code == 200
    data = resp.json
    assert data["result"]["http_status"] == 200
    assert data["result"]["outcome"] == "duplicate_ignored"
    assert data["result"]["signature"] == "not_checked"
    assert data["balance_before"] == data["balance_after"] == 1000
    assert data["status_before"] == data["status_after"] == "COMPLETED"
    assert status_route.called  # confirmed with the API, never applied from the stored body
    ev = last_event(t)
    assert ev.source == "replay" and ev.outcome == "duplicate_ignored"
    assert "keep no signature" in ev.detail
    assert ledger_count() == 1
    # A real unsigned callback from outside is still rejected.
    assert post_callback(client, body).status_code == 401


# --------------------------------------------------------------------------- hostile bodies
def test_nul_escape_in_unsigned_callback_is_stored_and_applied(ctx, client, pawapay):
    """Postgres JSONB and text reject U+0000. A payload carrying it must still apply, not 500."""
    t = make_topup(amount_minor=1000)
    pawapay.get(f"/v2/deposits/{t.deposit_id}").mock(
        return_value=httpx.Response(200, json=found(deposit_data(t, customerMessage="Ringwise\u0000topup")))
    )
    body = callback_bytes(deposit_data(t, customerMessage="Ringwise\u0000topup", note={"k\u0000": "v"}))
    assert b"\\u0000" in body
    resp = post_callback(client, body)
    assert resp.status_code == 200, resp.json
    assert resp.json["outcome"] == "applied"
    assert fresh(t).status == "COMPLETED"
    assert balance(t.customer_id) == 1000
    ev = last_event(t)
    assert ev.payload["customerMessage"] == "Ringwise�topup"
    assert ev.payload["note"] == {"k�": "v"}


def test_nul_escape_in_signed_callback_verifies_and_applies(app, ctx, client, pawapay):
    app.config["PAWAPAY_REQUIRE_SIGNATURE"] = True
    key, pem = new_key()
    mock_keys(pawapay, pem)
    t = make_topup(amount_minor=1000)
    body = callback_bytes(deposit_data(t, customerMessage="Ringwise\u0000topup"))
    resp = post_callback(client, body, signed_headers(body, key))  # digest is over the original bytes
    assert resp.status_code == 200 and resp.json == {"outcome": "applied", "signature": "verified"}
    assert balance(t.customer_id) == 1000


def test_raw_nul_byte_body_is_a_400_not_a_500(ctx, client, pawapay):
    resp = post_callback(client, b'{"depositId": "x\x00", "status": "COMPLETED"}')
    assert resp.status_code == 400
    ev = db.session.execute(select(PaymentEvent).order_by(PaymentEvent.created_at.desc()).limit(1)).scalar_one()
    db.session.rollback()
    assert "\x00" not in ev.raw_body and "�" in ev.raw_body


def test_deeply_nested_body_is_a_400_not_a_500(ctx, client, pawapay):
    deep = b"[" * 100_000 + b"]" * 100_000  # RecursionError inside json.loads
    resp = post_callback(client, deep)
    assert resp.status_code == 400 and resp.json["outcome"] == "error"
    t = make_topup()
    nested = callback_bytes(deposit_data(t, metadata=_nest(200)))  # parses, but no real payload is this deep
    resp = post_callback(client, nested)
    assert resp.status_code == 400
    assert fresh(t).status == "ACCEPTED"


def _nest(depth: int):
    value: object = "x"
    for _ in range(depth):
        value = [value]
    return value


# --------------------------------------------------------------------------- public key fetches
def test_random_keyids_cannot_force_a_key_fetch_per_request(app, ctx, client, pawapay):
    """Unknown keyids may trigger at most one forced refresh per minute, so unauthenticated
    callers cannot make this server call GET /v2/public-key/http once per request."""
    app.config["PAWAPAY_REQUIRE_SIGNATURE"] = True
    key, pem = new_key()
    keys_route = mock_keys(pawapay, pem)
    t = make_topup()
    body = callback_bytes(deposit_data(t))
    for i in range(5):
        resp = post_callback(client, body, signed_headers(body, key, keyid=f"rnd-{i}"))
        assert resp.status_code == 401
        assert "unknown_key" in last_event(t).detail
    assert keys_route.call_count == 1  # the initial fetch only
    # The real key still verifies from the cache.
    resp = post_callback(client, body, signed_headers(body, key))
    assert resp.status_code == 200 and resp.json["signature"] == "verified"
    assert keys_route.call_count == 1


def test_key_rotation_still_refreshes_once_the_cache_is_a_minute_old(app, ctx, client, pawapay):
    from conftest import BASE_URL

    from app.pawapay import client as client_module

    app.config["PAWAPAY_REQUIRE_SIGNATURE"] = True
    old_key, old_pem = new_key()
    new_key_, new_pem = new_key()
    keys_route = pawapay.get("/v2/public-key/http").mock(
        side_effect=[
            httpx.Response(200, json=[{"id": KEY_ID, "key": old_pem}]),
            httpx.Response(200, json=[{"id": KEY_ID, "key": old_pem}, {"id": "HTTP_EC_P256_KEY:2", "key": new_pem}]),
            httpx.Response(200, json=[{"id": KEY_ID, "key": old_pem}, {"id": "HTTP_EC_P256_KEY:2", "key": new_pem}]),
        ]
    )
    t = make_topup(amount_minor=1000)
    first = callback_bytes(deposit_data(t, status="PROCESSING"))
    assert post_callback(client, first, signed_headers(first, old_key)).json["signature"] == "verified"

    with client_module._cache_lock:  # age the cached keys past the forced-refresh interval
        fetched_at, cached = client_module._public_keys_cache[BASE_URL]
        client_module._public_keys_cache[BASE_URL] = (fetched_at - 61, cached)
    body = callback_bytes(deposit_data(t))
    resp = post_callback(client, body, signed_headers(body, new_key_, keyid="HTTP_EC_P256_KEY:2"))
    assert resp.status_code == 200 and resp.json == {"outcome": "applied", "signature": "verified"}
    assert keys_route.call_count == 2
    # A second unknown keyid right after the refresh does not fetch again.
    resp = post_callback(client, body, signed_headers(body, new_key_, keyid="rnd"))
    assert resp.status_code == 401
    assert keys_route.call_count == 2
