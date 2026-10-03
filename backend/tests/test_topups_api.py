"""Customer API: session, initiation outcomes, abuse limits, idempotency, config fallback."""
import json
import uuid

import httpx
from conftest import found
from sqlalchemy import func, select, update

from app.extensions import db
from app.models import PaymentEvent, Topup

ZMB_TOPUP = {"country": "ZMB", "provider": "MTN_MOMO_ZMB", "phone": "260763456789", "amount": "10.50"}

ACTIVE_CONF = {
    "companyName": "Ringwise",
    "signatureConfiguration": {"signedRequestsOnly": False, "signedCallbacks": False},
    "countries": [
        {
            "country": "ZMB",
            "displayName": {"en": "Zambia", "fr": "Zambie"},
            "prefix": "260",
            "flag": "https://cdn.example/zmb.svg",
            "providers": [
                {
                    "provider": "MTN_MOMO_ZMB",
                    "displayName": "MTN",
                    "logo": "https://cdn.example/mtn.png",
                    "nameDisplayedToCustomer": "Ringwise",
                    "currencies": [
                        {
                            "currency": "ZMW",
                            "displayName": "K",
                            "operationTypes": {
                                "DEPOSIT": {
                                    "authType": "PROVIDER_AUTH",
                                    "pinPrompt": "AUTOMATIC",
                                    "pinPromptRevivable": True,
                                    "pinPromptInstructions": {
                                        "channels": [
                                            {
                                                "type": "USSD",
                                                "displayName": {"en": "Not getting the PIN prompt?"},
                                                "quickLink": "tel:*115%23",
                                                "variables": {"shortCode": "*115#"},
                                                "instructions": {"en": [{"text": "Dial *115# on your phone"}]},
                                            }
                                        ]
                                    },
                                    "minAmount": "1",
                                    "maxAmount": "5000",
                                    "decimalsInAmount": "TWO_PLACES",
                                    "status": "OPERATIONAL",
                                }
                            },
                        }
                    ],
                },
                {
                    # Docs-example shape: operationTypes as a list, old limit names.
                    "provider": "AIRTEL_OAPI_ZMB",
                    "displayName": "Airtel",
                    "logo": "https://cdn.example/airtel.png",
                    "nameDisplayedToCustomer": "Ringwise",
                    "currencies": [
                        {
                            "currency": "ZMW",
                            "operationTypes": [
                                {
                                    "DEPOSIT": {
                                        "authType": "PROVIDER_AUTH",
                                        "minTransactionLimit": "2",
                                        "maxTransactionLimit": "3000",
                                        "decimalsInAmount": "NONE",
                                        "status": "DELAYED",
                                    }
                                }
                            ],
                        }
                    ],
                },
                {
                    "provider": "ZAMTEL_ZMB",
                    "displayName": "Zamtel",
                    "currencies": [
                        {
                            "currency": "ZMW",
                            "operationTypes": {
                                "DEPOSIT": {"authType": "PROVIDER_AUTH", "status": "CLOSED"}
                            },
                        }
                    ],
                },
            ],
        },
        {
            "country": "SEN",
            "displayName": {"en": "Senegal"},
            "prefix": "221",
            "providers": [
                {
                    "provider": "WAVE_SEN",
                    "displayName": "Wave",
                    "currencies": [
                        {
                            "currency": "XOF",
                            "operationTypes": {
                                "DEPOSIT": {"authType": "REDIRECT_AUTH", "status": "OPERATIONAL"}
                            },
                        }
                    ],
                }
            ],
        },
    ],
}


def start_session(client):
    resp = client.post("/api/session")
    assert resp.status_code in (200, 201)
    return resp.json["customer_id"]


def mock_conf(pawapay):
    return pawapay.get("/v2/active-conf").mock(return_value=httpx.Response(200, json=ACTIVE_CONF))


def accepted(dep):
    return httpx.Response(200, json={"depositId": dep, "status": "ACCEPTED", "created": "2026-10-03T08:00:00Z"})


def deposit_id_from(request) -> str:
    return json.loads(request.content)["depositId"]


def events_for(topup_id):
    rows = db.session.execute(
        select(PaymentEvent).where(PaymentEvent.topup_id == uuid.UUID(topup_id)).order_by(PaymentEvent.created_at)
    ).scalars().all()
    db.session.rollback()
    return rows


# --------------------------------------------------------------------------- session + config
def test_session_is_idempotent(client, ctx):
    first = client.post("/api/session")
    second = client.post("/api/session")
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json["customer_id"] == second.json["customer_id"]
    cookie = first.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_config_fallback_when_token_unset(app, client, ctx):
    app.config["PAWAPAY_API_TOKEN"] = None
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json
    assert data["source"] == "fallback"
    assert data["sandbox"] is True
    assert data["reconcile_after_seconds"] == 60
    zmb = data["countries"][0]
    assert zmb["country"] == "ZMB" and zmb["currency"] == "ZMW" and zmb["prefix"] == "260"
    assert {p["provider"] for p in zmb["providers"]} == {"MTN_MOMO_ZMB", "AIRTEL_OAPI_ZMB", "ZAMTEL_ZMB"}


def test_config_falls_back_when_active_conf_fails(client, ctx, pawapay):
    pawapay.get("/v2/active-conf").mock(return_value=httpx.Response(500, json={}))
    assert client.get("/api/config").json["source"] == "fallback"


def test_config_from_active_conf_filters_and_maps(client, ctx, pawapay):
    route = mock_conf(pawapay)
    data = client.get("/api/config").json
    assert data["source"] == "pawapay"
    assert [c["country"] for c in data["countries"]] == ["ZMB"]  # Wave (REDIRECT_AUTH) dropped
    providers = {p["provider"]: p for p in data["countries"][0]["providers"]}
    assert set(providers) == {"MTN_MOMO_ZMB", "AIRTEL_OAPI_ZMB"}  # Zamtel CLOSED dropped
    mtn = providers["MTN_MOMO_ZMB"]
    assert mtn["decimals"] == 2 and mtn["min_amount"] == "1" and mtn["max_amount"] == "5000"
    assert mtn["pin_prompt_instructions"][0]["steps"] == ["Dial *115# on your phone"]
    airtel = providers["AIRTEL_OAPI_ZMB"]
    assert airtel["decimals"] == 0 and airtel["min_amount"] == "2" and airtel["status"] == "DELAYED"
    client.get("/api/config")
    assert route.call_count == 1  # cached for 5 minutes


def test_topup_returns_503_when_not_configured(app, client, ctx):
    app.config["PAWAPAY_API_TOKEN"] = None
    start_session(client)
    resp = client.post("/api/topups", json=ZMB_TOPUP)
    assert resp.status_code == 503
    assert resp.json["error"]["code"] == "pawapay_not_configured"


def test_scenarios(client):
    data = client.get("/api/scenarios").json["scenarios"]
    phones = {s["phone"]: s["expected"] for s in data}
    assert phones["260763456789"] == "COMPLETED"
    assert phones["260763456129"].startswith("SUBMITTED")
    assert phones["260973456049"] == "FAILED: INSUFFICIENT_BALANCE"
    assert all({"id", "label", "description", "country", "provider", "phone", "expected"} <= s.keys() for s in data)


# --------------------------------------------------------------------------- initiation
def test_accepted_topup_sends_correct_v2_request(client, ctx, pawapay):
    mock_conf(pawapay)
    route = pawapay.post("/v2/deposits").mock(side_effect=lambda req: accepted(deposit_id_from(req)))
    customer_id = start_session(client)
    resp = client.post("/api/topups", json=ZMB_TOPUP)
    assert resp.status_code == 201, resp.json
    topup = resp.json["topup"]
    assert topup["status"] == "ACCEPTED" and topup["amount_minor"] == 1050
    sent = json.loads(route.calls.last.request.content)
    assert route.calls.last.request.headers["Authorization"] == "Bearer test-token"
    assert sent["amount"] == "10.5"  # no trailing zero
    assert sent["currency"] == "ZMW"
    assert sent["depositId"] == topup["deposit_id"]
    assert sent["payer"] == {"type": "MMO", "accountDetails": {"phoneNumber": "260763456789", "provider": "MTN_MOMO_ZMB"}}
    assert sent["customerMessage"] == "Ringwise topup"
    assert sent["metadata"] == [{"customerId": customer_id}, {"source": "ringwise-demo"}]
    # depositId row existed (CREATED) before the call: the initiate event is recorded after it
    assert [e.source for e in events_for(topup["id"])] == ["initiate_response"]

    wallet = client.get("/api/wallet").json
    assert wallet["topups"][0]["id"] == topup["id"]
    assert wallet["balances"] == [{"currency": "ZMW", "balance_minor": 0, "exponent": 2}]

    detail = client.get(f"/api/topups/{topup['id']}").json
    assert detail["topup"]["status"] == "ACCEPTED"
    assert detail["events"][0]["outcome"] == "applied"


def test_initiate_rejected_path(client, ctx, pawapay):
    mock_conf(pawapay)
    pawapay.post("/v2/deposits").mock(
        side_effect=lambda req: httpx.Response(
            200,
            json={
                "depositId": deposit_id_from(req),
                "status": "REJECTED",
                "failureReason": {"failureCode": "INVALID_PHONE_NUMBER", "failureMessage": "bad number"},
            },
        )
    )
    start_session(client)
    resp = client.post("/api/topups", json=ZMB_TOPUP)
    assert resp.status_code == 201
    topup = resp.json["topup"]
    assert topup["status"] == "REJECTED"
    assert topup["failure_code"] == "INVALID_PHONE_NUMBER"
    assert topup["reason"].startswith("That phone number is not valid")
    # A rejected top-up is final, so the customer can start another one immediately.
    pawapay.post("/v2/deposits").mock(side_effect=lambda req: accepted(deposit_id_from(req)))
    assert client.post("/api/topups", json=ZMB_TOPUP).status_code == 201


def test_initiate_http_500_then_status_found(client, ctx, pawapay):
    mock_conf(pawapay)
    pawapay.post("/v2/deposits").mock(
        return_value=httpx.Response(500, json={"failureReason": {"failureCode": "UNKNOWN_ERROR"}})
    )
    status_route = pawapay.get(url__regex=r"/v2/deposits/[0-9a-f-]{36}$").mock(
        side_effect=lambda req: httpx.Response(
            200,
            json=found({"depositId": req.url.path.rsplit("/", 1)[-1], "status": "ACCEPTED", "amount": "10.5", "currency": "ZMW"}),
        )
    )
    start_session(client)
    resp = client.post("/api/topups", json=ZMB_TOPUP)
    assert resp.status_code == 201
    topup = resp.json["topup"]
    assert status_route.called
    assert topup["status"] == "ACCEPTED"  # unknown outcome resolved by the status check, not failed
    sources = [(e.source, e.outcome) for e in events_for(topup["id"])]
    assert sources == [("initiate_response", "no_change"), ("status_check", "applied")]


def test_initiate_http_500_then_status_not_found_fails(client, ctx, pawapay):
    mock_conf(pawapay)
    pawapay.post("/v2/deposits").mock(return_value=httpx.Response(500, json={}))
    pawapay.get(url__regex=r"/v2/deposits/[0-9a-f-]{36}$").mock(
        return_value=httpx.Response(200, json={"status": "NOT_FOUND"})
    )
    start_session(client)
    topup = client.post("/api/topups", json=ZMB_TOPUP).json["topup"]
    assert topup["status"] == "FAILED"
    assert topup["failure_code"] == "NOT_FOUND_AT_PROVIDER"


def test_initiate_timeout_then_status_check(client, ctx, pawapay):
    mock_conf(pawapay)
    pawapay.post("/v2/deposits").mock(side_effect=httpx.ReadTimeout("slow"))
    pawapay.get(url__regex=r"/v2/deposits/[0-9a-f-]{36}$").mock(
        side_effect=lambda req: httpx.Response(
            200,
            json=found({"depositId": req.url.path.rsplit("/", 1)[-1], "status": "PROCESSING"}),
        )
    )
    start_session(client)
    topup = client.post("/api/topups", json=ZMB_TOPUP).json["topup"]
    assert topup["status"] == "PROCESSING"


def _signed_completed(client, app, pawapay, topup: dict):
    """Deliver a validly signed COMPLETED callback for a top-up returned by the API."""
    from conftest import KEY_ID, callback_bytes, new_key, signed_headers

    key, pem = new_key()
    pawapay.get("/v2/public-key/http").mock(return_value=httpx.Response(200, json=[{"id": KEY_ID, "key": pem}]))
    body = callback_bytes(
        {
            "depositId": topup["deposit_id"],
            "status": "COMPLETED",
            "amount": "10.5",
            "currency": "ZMW",
            "country": "ZMB",
            "providerTransactionId": "PP-LATE-1",
        }
    )
    return client.post("/webhooks/pawapay", data=body, headers=signed_headers(body, key))


def test_initiate_read_timeout_then_not_found_stays_open_until_the_callback(app, client, ctx, pawapay):
    """A read timeout means the deposit request may still be in flight at pawaPay, so an immediate
    NOT_FOUND must not fail the top-up. The late COMPLETED callback then credits it normally."""
    mock_conf(pawapay)
    pawapay.post("/v2/deposits").mock(side_effect=httpx.ReadTimeout("slow"))
    pawapay.get(url__regex=r"/v2/deposits/[0-9a-f-]{36}$").mock(
        return_value=httpx.Response(200, json={"status": "NOT_FOUND"})
    )
    start_session(client)
    resp = client.post("/api/topups", json=ZMB_TOPUP)
    assert resp.status_code == 201
    topup = resp.json["topup"]
    assert topup["status"] == "CREATED"  # open: reconciliation settles it if nothing arrives
    outcomes = [(e.source, e.outcome) for e in events_for(topup["id"])]
    assert outcomes == [("initiate_response", "no_change"), ("status_check", "no_change")]
    assert "in flight" in events_for(topup["id"])[-1].detail

    app.config["PAWAPAY_REQUIRE_SIGNATURE"] = True
    cb = _signed_completed(client, app, pawapay, topup)
    assert cb.status_code == 200 and cb.json == {"outcome": "applied", "signature": "verified"}
    wallet = client.get("/api/wallet").json
    assert wallet["balances"] == [{"currency": "ZMW", "balance_minor": 1050, "exponent": 2}]
    assert wallet["topups"][0]["status"] == "COMPLETED"


def test_initiate_connect_error_then_not_found_fails_immediately(client, ctx, pawapay):
    """A connect failure means the request never left this server, so NOT_FOUND is final."""
    mock_conf(pawapay)
    pawapay.post("/v2/deposits").mock(side_effect=httpx.ConnectError("refused"))
    pawapay.get(url__regex=r"/v2/deposits/[0-9a-f-]{36}$").mock(
        return_value=httpx.Response(200, json={"status": "NOT_FOUND"})
    )
    start_session(client)
    topup = client.post("/api/topups", json=ZMB_TOPUP).json["topup"]
    assert topup["status"] == "FAILED"
    assert topup["failure_code"] == "NOT_FOUND_AT_PROVIDER"


def test_late_signed_completed_after_not_found_failure_is_held_for_review(app, client, ctx, pawapay):
    """HTTP 500 then NOT_FOUND fails the top-up (pawaPay's documented rule). If pawaPay later
    reports COMPLETED anyway, the money may have moved: hold it, never drop it silently."""
    mock_conf(pawapay)
    pawapay.post("/v2/deposits").mock(return_value=httpx.Response(500, json={}))
    pawapay.get(url__regex=r"/v2/deposits/[0-9a-f-]{36}$").mock(
        return_value=httpx.Response(200, json={"status": "NOT_FOUND"})
    )
    start_session(client)
    topup = client.post("/api/topups", json=ZMB_TOPUP).json["topup"]
    assert topup["status"] == "FAILED" and topup["failure_code"] == "NOT_FOUND_AT_PROVIDER"

    app.config["PAWAPAY_REQUIRE_SIGNATURE"] = True
    cb = _signed_completed(client, app, pawapay, topup)
    assert cb.status_code == 200
    assert cb.json == {"outcome": "held_for_review", "signature": "verified"}
    detail = client.get(f"/api/console/topups/{topup['id']}").json
    assert detail["topup"]["status"] == "NEEDS_ATTENTION"
    assert detail["topup"]["failure_code"] == "LATE_COMPLETED_AFTER_FAILURE"
    assert detail["ledger_entry"] is None and detail["balance_minor"] == 0
    assert detail["events"][-1]["outcome"] == "held_for_review"
    assert client.get("/api/console/stats").json["needs_attention"] == 1


def test_initiate_and_status_both_down_leaves_created_for_reconciliation(client, ctx, pawapay):
    mock_conf(pawapay)
    pawapay.post("/v2/deposits").mock(side_effect=httpx.ConnectError("down"))
    pawapay.get(url__regex=r"/v2/deposits/.*").mock(side_effect=httpx.ConnectError("down"))
    start_session(client)
    topup = client.post("/api/topups", json=ZMB_TOPUP).json["topup"]
    assert topup["status"] == "CREATED"


# --------------------------------------------------------------------------- limits + idempotency
def test_one_open_topup_per_customer(client, ctx, pawapay):
    mock_conf(pawapay)
    pawapay.post("/v2/deposits").mock(side_effect=lambda req: accepted(deposit_id_from(req)))
    start_session(client)
    first = client.post("/api/topups", json=ZMB_TOPUP)
    second = client.post("/api/topups", json=ZMB_TOPUP)
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json["error"]["code"] == "topup_open"
    assert second.json["topup"]["id"] == first.json["topup"]["id"]


def test_open_rule_lapses_after_lock_window(client, ctx, pawapay):
    mock_conf(pawapay)
    pawapay.post("/v2/deposits").mock(side_effect=lambda req: accepted(deposit_id_from(req)))
    start_session(client)
    first = client.post("/api/topups", json=ZMB_TOPUP).json["topup"]
    db.session.execute(
        update(Topup)
        .where(Topup.id == uuid.UUID(first["id"]))
        .values(created_at=func.now() - func.make_interval(0, 0, 0, 0, 0, 11))
    )
    db.session.commit()
    assert client.post("/api/topups", json=ZMB_TOPUP).status_code == 201


def test_idempotency_key_replay_returns_same_topup(client, ctx, pawapay):
    mock_conf(pawapay)
    route = pawapay.post("/v2/deposits").mock(side_effect=lambda req: accepted(deposit_id_from(req)))
    start_session(client)
    headers = {"Idempotency-Key": "c1d4e8a0-key"}
    first = client.post("/api/topups", json=ZMB_TOPUP, headers=headers)
    replay = client.post("/api/topups", json=ZMB_TOPUP, headers=headers)
    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json["topup"]["id"] == first.json["topup"]["id"]
    assert route.call_count == 1  # pawaPay called once
    changed = client.post("/api/topups", json={**ZMB_TOPUP, "amount": "20"}, headers=headers)
    assert changed.status_code == 422
    assert changed.json["error"]["code"] == "idempotency_key_reused"


def test_phone_hourly_cap(app, client, ctx, pawapay):
    app.config["PHONE_HOURLY_CAP"] = 2
    mock_conf(pawapay)
    pawapay.post("/v2/deposits").mock(
        side_effect=lambda req: httpx.Response(
            200,
            json={"depositId": deposit_id_from(req), "status": "REJECTED", "failureReason": {"failureCode": "INVALID_AMOUNT"}},
        )
    )
    start_session(client)
    assert client.post("/api/topups", json=ZMB_TOPUP).status_code == 201
    assert client.post("/api/topups", json=ZMB_TOPUP).status_code == 201
    capped = client.post("/api/topups", json=ZMB_TOPUP)
    assert capped.status_code == 429
    assert capped.json["error"]["code"] == "phone_hourly_cap"


def test_validation_errors_name_the_field(client, ctx, pawapay):
    mock_conf(pawapay)
    start_session(client)
    cases = [
        ({**ZMB_TOPUP, "country": "XXX"}, "country"),
        ({**ZMB_TOPUP, "provider": "WAVE_SEN"}, "provider"),
        ({**ZMB_TOPUP, "phone": "abc"}, "phone"),
        ({**ZMB_TOPUP, "amount": "10.555"}, "amount"),
        ({**ZMB_TOPUP, "amount": "0.50"}, "amount"),  # below minAmount 1
        ({**ZMB_TOPUP, "amount": "6000"}, "amount"),  # above maxAmount 5000
        ({**ZMB_TOPUP, "provider": "AIRTEL_OAPI_ZMB", "phone": "260973456789", "amount": "10.5"}, "amount"),
        ({**ZMB_TOPUP, "scenario": "nope"}, "scenario"),
    ]
    for body, field in cases:
        resp = client.post("/api/topups", json=body)
        assert resp.status_code == 422, (body, resp.json)
        assert resp.json["error"]["field"] == field, (body, resp.json)


def test_local_number_is_normalised(client, ctx, pawapay):
    mock_conf(pawapay)
    route = pawapay.post("/v2/deposits").mock(side_effect=lambda req: accepted(deposit_id_from(req)))
    start_session(client)
    resp = client.post("/api/topups", json={**ZMB_TOPUP, "phone": "0763 456 789"})
    assert resp.status_code == 201
    assert json.loads(route.calls.last.request.content)["payer"]["accountDetails"]["phoneNumber"] == "260763456789"


def test_customer_cannot_read_another_customers_topup(app, client, ctx, pawapay):
    mock_conf(pawapay)
    pawapay.post("/v2/deposits").mock(side_effect=lambda req: accepted(deposit_id_from(req)))
    start_session(client)
    topup = client.post("/api/topups", json=ZMB_TOPUP).json["topup"]
    other = app.test_client()
    start_session(other)
    assert other.get(f"/api/topups/{topup['id']}").status_code == 404


def test_requests_without_session_are_401(client):
    assert client.get("/api/wallet").status_code == 401
    assert client.post("/api/topups", json=ZMB_TOPUP).status_code == 401


def test_predict_provider(client, ctx, pawapay):
    pawapay.post("/v2/predict-provider").mock(
        return_value=httpx.Response(200, json={"country": "ZMB", "provider": "MTN_MOMO_ZMB", "phoneNumber": "260763456789"})
    )
    resp = client.post("/api/predict-provider", json={"phone": "+260 76 345 6789"})
    assert resp.json == {"country": "ZMB", "provider": "MTN_MOMO_ZMB", "phone_number": "260763456789"}
    pawapay.post("/v2/predict-provider").mock(
        return_value=httpx.Response(400, json={"failureReason": {"failureCode": "INVALID_PHONE_NUMBER", "failureMessage": "nope"}})
    )
    bad = client.post("/api/predict-provider", json={"phone": "123"})
    assert bad.status_code == 422 and bad.json["error"]["field"] == "phone"
