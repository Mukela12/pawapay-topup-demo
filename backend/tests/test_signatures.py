"""Unit tests for RFC 9421 + Content-Digest verification (no Flask, no DB)."""
import base64
import time

from conftest import KEY_ID, new_key, signed_headers
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from app.pawapay.signatures import (
    RequestView,
    build_signature_base,
    content_digest_header,
    parse_signature_input,
    verify_content_digest,
    verify_request,
)

# The deposit callback example from https://docs.pawapay.io/v2/docs/signatures
DOC_HEADERS = {
    "Content-Type": "application/json; charset=UTF-8",
    "Content-Digest": "sha-512=:0ki7QBS/0MA424uwOq3k5HnJnL5SRkPjit12m0YMpd4JgWiMvm9+yNT3FunkpDaTSsKhTkliQwJlRw9bgsos9w==:",
    "Signature-Date": "2024-05-02T16:45:51.131905Z",
    "Signature": "sig-pp=:MEQCIHFvGCUgyxmmowMufO4Yk20pBs3JHRax81si2QZVi9ByAiBPpg1WBhQjZ6fmi3a/gKcWiQ73Qm9Ol35On3c4K/flew==:",
    "Signature-Input": 'sig-pp=("@method" "@authority" "@path" "signature-date" "content-digest" "content-type");alg="ecdsa-p256-sha256";keyid="CUSTOMER_TEST_KEY";created=1714657551;expires=1714657611',
}
DOC_BASE = (
    '"@method": POST\n'
    '"@authority": localhost:8080\n'
    '"@path": /callback\n'
    '"signature-date": 2024-05-02T16:45:51.131905Z\n'
    '"content-digest": sha-512=:0ki7QBS/0MA424uwOq3k5HnJnL5SRkPjit12m0YMpd4JgWiMvm9+yNT3FunkpDaTSsKhTkliQwJlRw9bgsos9w==:\n'
    '"content-type": application/json; charset=UTF-8\n'
    '"@signature-params": ("@method" "@authority" "@path" "signature-date" "content-digest" "content-type");alg="ecdsa-p256-sha256";keyid="CUSTOMER_TEST_KEY";created=1714657551;expires=1714657611'
)


def view(headers, authority="localhost", path="/webhooks/pawapay"):
    return RequestView("POST", authority, path, "", "https", headers)


def test_signature_base_matches_pawapay_doc_example():
    inputs = parse_signature_input(DOC_HEADERS["Signature-Input"])
    sig_input = inputs["sig-pp"]
    assert sig_input.params["keyid"] == "CUSTOMER_TEST_KEY"
    assert sig_input.params["created"] == 1714657551
    base = build_signature_base(sig_input, view(DOC_HEADERS, "localhost:8080", "/callback"))
    assert base.decode() == DOC_BASE


def test_doc_sample_signature_is_der_not_raw():
    sig = base64.b64decode(DOC_HEADERS["Signature"].split("=:", 1)[1].rstrip(":"))
    assert sig[0] == 0x30 and len(sig) in (70, 71, 72)


def test_valid_der_signature_verifies():
    key, pem = new_key()
    body = b'{"depositId":"x","status":"COMPLETED"}'
    headers = signed_headers(body, key)
    result = verify_request(view(headers), body, lambda kid: pem if kid == KEY_ID else None)
    assert result.ok, result.message
    assert result.alg == "ecdsa-p256-sha256"
    assert result.digest_alg == "sha-512"


def test_raw_r_s_signature_is_accepted_as_fallback():
    key, pem = new_key()
    body = b'{"a":1}'
    headers = signed_headers(body, key)
    der = base64.b64decode(headers["Signature"].split("=:", 1)[1].rstrip(":"))
    r, s = decode_dss_signature(der)
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    headers["Signature"] = f"sig-pp=:{base64.b64encode(raw).decode()}:"
    result = verify_request(view(headers), body, lambda kid: pem)
    assert result.ok, result.message


def test_tampered_body_fails_content_digest():
    key, pem = new_key()
    body = b'{"amount":"10"}'
    headers = signed_headers(body, key)
    result = verify_request(view(headers), b'{"amount":"99"}', lambda kid: pem)
    assert not result.ok and result.reason == "digest_mismatch"


def test_wrong_key_fails():
    key, _ = new_key()
    _, other_pem = new_key()
    body = b"{}"
    result = verify_request(view(signed_headers(body, key)), body, lambda kid: other_pem)
    assert not result.ok and result.reason == "bad_signature"


def test_expired_signature_fails():
    key, pem = new_key()
    body = b"{}"
    old = int(time.time()) - 600
    headers = signed_headers(body, key, created=old, expires=old + 60)
    result = verify_request(view(headers), body, lambda kid: pem)
    assert not result.ok and result.reason == "expired"


def test_wrong_authority_fails():
    key, pem = new_key()
    body = b"{}"
    headers = signed_headers(body, key, authority="ringwise.example")
    result = verify_request(view(headers, authority="evil.example"), body, lambda kid: pem)
    assert not result.ok and result.reason == "bad_signature"


def test_unknown_keyid_fails():
    key, _ = new_key()
    body = b"{}"
    result = verify_request(view(signed_headers(body, key)), body, lambda kid: None)
    assert not result.ok and result.reason == "unknown_key"


def test_signature_must_cover_content_digest():
    key, pem = new_key()
    body = b"{}"
    now = int(time.time())
    params = f'("@method" "@path");alg="ecdsa-p256-sha256";keyid="{KEY_ID}";created={now};expires={now + 60}'
    base = f'"@method": POST\n"@path": /webhooks/pawapay\n"@signature-params": {params}'.encode()
    sig = key.sign(base, ec.ECDSA(hashes.SHA256()))
    headers = {
        "Content-Digest": content_digest_header(body),
        "Signature-Input": f"sig-pp={params}",
        "Signature": f"sig-pp=:{base64.b64encode(sig).decode()}:",
    }
    result = verify_request(view(headers), body, lambda kid: pem)
    assert not result.ok and result.reason == "digest_not_covered"


def test_content_digest_sha256_and_sha512():
    body = b"hello"
    assert verify_content_digest(content_digest_header(body, "sha-256"), body) == "sha-256"
    assert verify_content_digest(content_digest_header(body, "sha-512"), body) == "sha-512"
