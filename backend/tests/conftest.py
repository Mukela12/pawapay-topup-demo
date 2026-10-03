"""Test fixtures. Tests run against postgresql:///ringwise_test (override with TEST_DATABASE_URL).

The session fixture drops and recreates the public schema, then runs the real Alembic migration,
so the CHECK constraints, unique keys and the append-only trigger under test are exactly the
ones production gets. Every test ends with a TRUNCATE (which does not fire row-level triggers).
pawaPay is mocked with respx; no test touches the network.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import time
import uuid
from datetime import timedelta

import pytest
import respx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from flask_migrate import upgrade
from sqlalchemy import create_engine, func, text, update
from sqlalchemy.exc import OperationalError

from app import MIGRATIONS_DIR, create_app
from app.config import normalize_database_url
from app.extensions import db
from app.models import Customer, Topup
from app.pawapay.client import clear_caches

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql:///ringwise_test")
BASE_URL = "https://api.sandbox.pawapay.io"
KEY_ID = "HTTP_EC_P256_KEY:1"

TEST_CONFIG = {
    "TESTING": True,
    "SQLALCHEMY_DATABASE_URI": normalize_database_url(TEST_DB_URL),
    "SECRET_KEY": "test-secret",
    "PAWAPAY_API_TOKEN": "test-token",
    "PAWAPAY_BASE_URL": BASE_URL,
    "PAWAPAY_REQUIRE_SIGNATURE": False,
    "RECONCILE_AFTER_SECONDS": 60,
    "OPEN_TOPUP_LOCK_SECONDS": 600,
    "PHONE_HOURLY_CAP": 5,
    "CUSTOMER_DAILY_CAP": 20,
    "INTERNAL_TOKEN": "internal-test-token",
    "EB_WORKER": False,
    "SCHEDULER_ENABLED": False,
    "TRUST_PROXY_HOPS": 0,
    "FRONTEND_DIST": None,
    "SESSION_COOKIE_SECURE": False,
}


def _ensure_database() -> None:
    engine = create_engine(normalize_database_url(TEST_DB_URL))
    try:
        engine.connect().close()
    except OperationalError:
        name = TEST_DB_URL.rsplit("/", 1)[-1]
        subprocess.run(["createdb", name], check=True)
    finally:
        engine.dispose()


def _reset_schema() -> None:
    with db.engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


@pytest.fixture(scope="session")
def app():
    _ensure_database()
    application = create_app(dict(TEST_CONFIG))
    with application.app_context():
        _reset_schema()
        upgrade(directory=str(MIGRATIONS_DIR))
    yield application
    with application.app_context():
        db.session.remove()
        _reset_schema()
        db.engine.dispose()


@pytest.fixture(autouse=True)
def _isolate(app):
    saved = dict(app.config)
    clear_caches()
    yield
    app.config.clear()
    app.config.update(saved)
    clear_caches()
    with app.app_context():
        db.session.remove()
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    "TRUNCATE ledger_entries, outbox, payment_events, reconcile_runs, "
                    "wallet_accounts, topups, customers RESTART IDENTITY CASCADE"
                )
            )


@pytest.fixture
def ctx(app):
    with app.app_context():
        yield app
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def pawapay():
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


# --------------------------------------------------------------------------- data helpers
def make_customer() -> uuid.UUID:
    c = Customer(id=uuid.uuid4(), display_name="Your wallet")
    db.session.add(c)
    db.session.commit()
    db.session.expunge(c)
    return c.id


def make_topup(
    customer_id: uuid.UUID | None = None,
    *,
    status: str = "ACCEPTED",
    amount_minor: int = 1000,
    currency: str = "ZMW",
    msisdn: str = "260763456789",
    provider: str = "MTN_MOMO_ZMB",
    age_seconds: int = 0,
) -> Topup:
    customer_id = customer_id or make_customer()
    t = Topup(
        id=uuid.uuid4(),
        deposit_id=uuid.uuid4(),
        customer_id=customer_id,
        amount_minor=amount_minor,
        currency=currency,
        country="ZMB",
        provider=provider,
        msisdn=msisdn,
        status=status,
        check_count=0,
    )
    db.session.add(t)
    db.session.commit()
    if age_seconds:
        db.session.execute(
            update(Topup)
            .where(Topup.id == t.id)
            .values(created_at=func.now() - timedelta(seconds=age_seconds))
        )
        db.session.commit()
    # Detach: requests made with the test client inside this app context share the session,
    # and a cached instance would hide updates made through Core statements.
    db.session.expunge(t)
    return t


def deposit_data(topup: Topup, status: str = "COMPLETED", amount: str = "10.00", **extra) -> dict:
    data = {
        "depositId": str(topup.deposit_id),
        "status": status,
        "amount": amount,
        "currency": topup.currency,
        "country": "ZMB",
        "payer": {
            "type": "MMO",
            "accountDetails": {"phoneNumber": topup.msisdn, "provider": topup.provider},
        },
        "customerMessage": "Ringwise topup",
        "created": "2026-10-03T08:00:00Z",
        "providerTransactionId": "PP-TXN-1",
        "metadata": {"customerId": str(topup.customer_id), "source": "ringwise-demo"},
    }
    data.update(extra)
    return data


def found(data: dict) -> dict:
    return {"status": "FOUND", "data": data}


# --------------------------------------------------------------------------- signing helpers
def new_key():
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return key, pem


def signed_headers(
    body: bytes,
    key,
    *,
    authority: str = "localhost",
    path: str = "/webhooks/pawapay",
    created: int | None = None,
    expires: int | None = None,
    keyid: str = KEY_ID,
    digest_body: bytes | None = None,
) -> dict:
    """Sign a callback the way pawaPay does, building the RFC 9421 base by hand (independent
    of the code under test) and producing an ASN.1 DER ECDSA signature."""
    created = int(time.time()) if created is None else created
    expires = created + 60 if expires is None else expires
    digest = base64.b64encode(hashlib.sha512(digest_body if digest_body is not None else body).digest()).decode()
    content_digest = f"sha-512=:{digest}:"
    signature_date = "2026-10-03T08:00:00.000000Z"
    content_type = "application/json; charset=UTF-8"
    params = (
        '("@method" "@authority" "@path" "signature-date" "content-digest" "content-type")'
        f';alg="ecdsa-p256-sha256";keyid="{keyid}";created={created};expires={expires}'
    )
    base = (
        f'"@method": POST\n'
        f'"@authority": {authority}\n'
        f'"@path": {path}\n'
        f'"signature-date": {signature_date}\n'
        f'"content-digest": {content_digest}\n'
        f'"content-type": {content_type}\n'
        f'"@signature-params": {params}'
    ).encode()
    der = key.sign(base, ec.ECDSA(hashes.SHA256()))
    assert der[0] == 0x30  # ASN.1 SEQUENCE, i.e. DER like pawaPay
    return {
        "Content-Type": content_type,
        "Content-Digest": content_digest,
        "Signature-Date": signature_date,
        "Signature-Input": f"sig-pp={params}",
        "Signature": f"sig-pp=:{base64.b64encode(der).decode()}:",
    }


def callback_bytes(data: dict) -> bytes:
    return json.dumps(data, separators=(",", ":")).encode()
