"""Customer API: anonymous per-browser wallet, top-up flow, polling."""
from __future__ import annotations

import re
import uuid

from flask import Blueprint, current_app, jsonify, request, session
from sqlalchemy import select

from ..extensions import db
from ..models import Customer, LedgerEntry, PaymentEvent, Topup, WalletAccount
from ..money import (
    AmountError,
    exponent,
    minor_to_decimal,
    parse_provider_amount,
    parse_user_amount,
)
from ..pawapay.client import PawaPayTransportError, get_client
from ..serializers import serialize_event, serialize_ledger, serialize_topup
from ..services.catalog import (
    SCENARIO_IDS,
    SCENARIOS,
    find_provider,
    get_catalog,
    is_sandbox,
)
from ..services.topups import (
    TopupError,
    TopupRequest,
    create_topup,
    initiate,
    request_fingerprint,
)
from .common import (
    api_error,
    json_body,
    parse_uuid,
    require_configured,
    session_customer_id,
)

bp = Blueprint("customer", __name__, url_prefix="/api")

DEFAULT_CURRENCY = "ZMW"
MSISDN_RE = re.compile(r"^[1-9][0-9]{7,14}$")


def _current_customer() -> Customer | None:
    cid = session_customer_id()
    if cid is None:
        return None
    return db.session.get(Customer, cid)


def _no_session():
    return api_error("no_session", "Start a session first: POST /api/session.", 401)


@bp.post("/session")
def create_session():
    customer = _current_customer()
    if customer is not None:
        return jsonify({"customer_id": str(customer.id), "display_name": customer.display_name}), 200
    customer = Customer(id=uuid.uuid4(), display_name="Your wallet")
    db.session.add(customer)
    db.session.commit()
    session.clear()
    session["customer_id"] = str(customer.id)
    session.permanent = True
    return jsonify({"customer_id": str(customer.id), "display_name": customer.display_name}), 201


@bp.get("/config")
def get_config():
    catalog = get_catalog()
    return jsonify(
        {
            "countries": catalog["countries"],
            "sandbox": is_sandbox(),
            "source": catalog["source"],
            "fallback_reason": catalog["reason"],
            "pawapay_configured": bool(current_app.config.get("PAWAPAY_API_TOKEN")),
            "reconcile_after_seconds": current_app.config["RECONCILE_AFTER_SECONDS"],
        }
    )


@bp.post("/predict-provider")
def predict_provider():
    blocked = require_configured()
    if blocked:
        return blocked
    phone = str(json_body().get("phone") or "").strip()
    if not phone or len(phone) > 32:
        return api_error("invalid_phone", "Enter a phone number with its country code.", 422, "phone")
    try:
        res = get_client().predict_provider(phone)
    except PawaPayTransportError:
        return api_error("pawapay_unavailable", "Could not reach pawaPay to predict the network.", 502)
    body = res.body if isinstance(res.body, dict) else {}
    if res.http_status == 200 and body.get("provider"):
        return jsonify(
            {
                "country": body.get("country"),
                "provider": body.get("provider"),
                "phone_number": body.get("phoneNumber"),
            }
        )
    reason = body.get("failureReason") if isinstance(body.get("failureReason"), dict) else {}
    return api_error(
        "invalid_phone",
        reason.get("failureMessage") or "pawaPay could not recognise this phone number.",
        422,
        "phone",
        pawapay_code=reason.get("failureCode"),
    )


@bp.get("/scenarios")
def scenarios():
    return jsonify({"scenarios": SCENARIOS})


@bp.get("/wallet")
def wallet():
    customer = _current_customer()
    if customer is None:
        return _no_session()
    accounts = db.session.execute(
        select(WalletAccount).where(WalletAccount.customer_id == customer.id).order_by(WalletAccount.currency)
    ).scalars().all()
    balances = [
        {"currency": a.currency, "balance_minor": a.balance_minor, "exponent": exponent(a.currency)}
        for a in accounts
    ]
    if not balances:
        balances = [{"currency": DEFAULT_CURRENCY, "balance_minor": 0, "exponent": exponent(DEFAULT_CURRENCY)}]
    topups = db.session.execute(
        select(Topup).where(Topup.customer_id == customer.id).order_by(Topup.created_at.desc()).limit(20)
    ).scalars().all()
    return jsonify({"balances": balances, "topups": [serialize_topup(t) for t in topups]})


def normalize_phone(raw: object, prefix: str) -> str:
    digits = re.sub(r"[\s\-().]", "", str(raw or ""))
    if digits.startswith("+"):
        digits = digits[1:]
    elif digits.startswith("00"):
        digits = digits[2:]
    if not digits.isdigit():
        raise ValueError("Use digits only, for example 260763456789.")
    if prefix and not digits.startswith(prefix):
        if digits.startswith("0"):
            digits = prefix + digits[1:]
        elif len(digits) <= 10:
            digits = prefix + digits
    if not MSISDN_RE.match(digits):
        raise ValueError("Enter the full number with country code, for example 260763456789.")
    if prefix and not digits.startswith(prefix):
        raise ValueError(f"This number does not start with the country code {prefix}.")
    return digits


@bp.post("/topups")
def create():
    customer_id = session_customer_id()
    if customer_id is None:
        return _no_session()
    blocked = require_configured()
    if blocked:
        return blocked

    body = json_body()
    country = str(body.get("country") or "").strip().upper()
    provider = str(body.get("provider") or "").strip().upper()
    scenario = body.get("scenario") or None

    catalog = get_catalog()
    country_conf, provider_conf = find_provider(catalog, country, provider)
    if country_conf is None:
        return api_error("invalid_country", "Choose a supported country.", 422, "country")
    if provider_conf is None:
        return api_error("invalid_provider", "Choose a network available in this country.", 422, "provider")
    try:
        msisdn = normalize_phone(body.get("phone"), country_conf.get("prefix") or "")
    except ValueError as exc:
        return api_error("invalid_phone", str(exc), 422, "phone")

    currency = provider_conf.get("currency") or country_conf.get("currency")
    decimals_in_amount = provider_conf.get("decimals_in_amount") or "TWO_PLACES"
    try:
        amount_minor = parse_user_amount(body.get("amount"), currency, decimals_in_amount)
    except AmountError as exc:
        return api_error("invalid_amount", str(exc), 422, "amount")
    value = minor_to_decimal(amount_minor, currency)
    min_amount = parse_provider_amount(provider_conf.get("min_amount"))
    max_amount = parse_provider_amount(provider_conf.get("max_amount"))
    if min_amount is not None and value < min_amount:
        return api_error(
            "amount_too_small", f"The minimum for this network is {min_amount} {currency}.", 422, "amount"
        )
    if max_amount is not None and value > max_amount:
        return api_error(
            "amount_too_large", f"The maximum for this network is {max_amount} {currency}.", 422, "amount"
        )
    if scenario is not None and scenario not in SCENARIO_IDS:
        return api_error("invalid_scenario", "Unknown scenario.", 422, "scenario")

    idem_key = (request.headers.get("Idempotency-Key") or "").strip() or None
    if idem_key and len(idem_key) > 255:
        return api_error("invalid_idempotency_key", "Idempotency-Key must be at most 255 characters.", 422)

    fingerprint = request_fingerprint(
        {
            "country": country,
            "provider": provider,
            "msisdn": msisdn,
            "amount_minor": amount_minor,
            "currency": currency,
            "scenario": scenario,
        }
    )
    req = TopupRequest(
        customer_id=customer_id,
        country=country,
        provider=provider,
        msisdn=msisdn,
        amount_minor=amount_minor,
        currency=currency,
        decimals_in_amount=decimals_in_amount,
        scenario=scenario,
        idempotency_key=idem_key,
        fingerprint=fingerprint,
    )
    try:
        topup, replayed = create_topup(req)
    except TopupError as exc:
        extra = {}
        if exc.extra.get("topup_obj") is not None:
            extra["topup"] = serialize_topup(exc.extra["topup_obj"])
        return api_error(exc.code, exc.message, exc.status, exc.field, **extra)

    if replayed:
        response = jsonify({"topup": serialize_topup(topup), "idempotent_replay": True})
        response.headers["Idempotent-Replayed"] = "true"
        return response, 200

    topup = initiate(topup.id, decimals_in_amount)
    return jsonify({"topup": serialize_topup(topup)}), 201


@bp.get("/topups/<topup_id>")
def get_topup(topup_id: str):
    customer_id = session_customer_id()
    if customer_id is None:
        return _no_session()
    tid = parse_uuid(topup_id)
    topup = (
        db.session.execute(
            select(Topup).where(Topup.id == tid, Topup.customer_id == customer_id)
        ).scalar_one_or_none()
        if tid
        else None
    )
    if topup is None:
        return api_error("not_found", "Top-up not found.", 404)
    events = db.session.execute(
        select(PaymentEvent).where(PaymentEvent.topup_id == topup.id).order_by(PaymentEvent.created_at)
    ).scalars().all()
    ledger = db.session.execute(
        select(LedgerEntry).where(LedgerEntry.topup_id == topup.id)
    ).scalar_one_or_none()
    return jsonify(
        {
            "topup": serialize_topup(topup),
            "events": [serialize_event(e) for e in events],
            "ledger_entry": serialize_ledger(ledger),
        }
    )
