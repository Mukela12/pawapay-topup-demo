"""Verification of pawaPay signed callbacks: RFC 9421 HTTP Message Signatures + Content-Digest.

A signed callback carries:
    Content-Digest:  sha-512=:<base64>:            (or sha-256)
    Signature-Input: sig-pp=("@method" "@authority" "@path" "signature-date"
                     "content-digest" "content-type");alg="ecdsa-p256-sha256";
                     keyid="HTTP_EC_P256_KEY:1";created=1714657551;expires=1714657611
    Signature:       sig-pp=:<base64>:
    Signature-Date:  2024-05-02T16:45:51.131905Z   (custom header, covered like any other)

Verification steps:
1. Hash the raw request bytes with the Content-Digest algorithm and compare (constant time).
2. Rebuild the RFC 9421 signature base from the covered components listed in Signature-Input,
   ending with the "@signature-params" line, which is the Signature-Input member value exactly
   as received (so parameter order is preserved).
3. Verify the signature with the pawaPay public key whose id matches keyid.

DER note: RFC 9421 says ECDSA signatures are the raw 64-byte r||s concatenation, but pawaPay's
samples decode to 70-72 byte ASN.1 DER sequences (Node's crypto.sign default). Strict RFC 9421
libraries reject those. We verify DER first and fall back to raw r||s by converting it with
encode_dss_signature, so either encoding is accepted.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

PREFERRED_LABEL = "sig-pp"
DIGEST_ALGORITHMS = {"sha-256": hashlib.sha256, "sha-512": hashlib.sha512}
SUPPORTED_ALGS = {"ecdsa-p256-sha256", "ecdsa-p384-sha384", "rsa-pss-sha512", "rsa-v1_5-sha256"}


class SignatureError(Exception):
    def __init__(self, reason: str, message: str | None = None):
        super().__init__(message or reason)
        self.reason = reason
        self.message = message or reason


@dataclass
class SignatureInput:
    label: str
    components: list[str]
    params: dict[str, object]
    raw_value: str  # the member value as received, used verbatim for "@signature-params"


@dataclass
class VerificationResult:
    ok: bool
    reason: str = "ok"
    message: str = ""
    label: str | None = None
    keyid: str | None = None
    alg: str | None = None
    digest_alg: str | None = None
    covered: list[str] = field(default_factory=list)


@dataclass
class RequestView:
    """The parts of an HTTP request a signature base can cover."""

    method: str
    authority: str
    path: str
    query: str
    scheme: str
    headers: Mapping[str, str]  # case-insensitive lookups are done by header()

    def header(self, name: str) -> str | None:
        lname = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lname:
                return value
        return None


# --------------------------------------------------------------------------- parsing
def _split_top_level(value: str, sep: str = ",") -> list[str]:
    """Split a structured-field string on sep, ignoring separators inside quotes or ()."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    in_quotes = False
    escaped = False
    for ch in value:
        if in_quotes:
            buf.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_quotes = False
            continue
        if ch == '"':
            in_quotes = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    if buf and "".join(buf).strip():
        parts.append("".join(buf).strip())
    return parts


def _parse_param_value(raw: str) -> object:
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        return raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if re.fullmatch(r"-?[0-9]+", raw):
        return int(raw)
    return raw


def parse_signature_input(header: str) -> dict[str, SignatureInput]:
    result: dict[str, SignatureInput] = {}
    for member in _split_top_level(header):
        if "=" not in member:
            raise SignatureError("malformed_signature_input", "Signature-Input member has no '='")
        label, value = member.split("=", 1)
        label = label.strip()
        value = value.strip()
        match = re.match(r"^\((?P<items>[^)]*)\)(?P<params>.*)$", value)
        if not match:
            raise SignatureError("malformed_signature_input", "Signature-Input is not an inner list")
        items = match.group("items").strip()
        components: list[str] = []
        for token in re.findall(r'"((?:[^"\\]|\\.)*)"(;[^\s"]*)?', items):
            name, comp_params = token
            if comp_params:
                raise SignatureError(
                    "unsupported_component",
                    f"Component parameters are not supported: {name}{comp_params}",
                )
            components.append(name.lower())
        params: dict[str, object] = {}
        for p in _split_top_level(match.group("params"), sep=";"):
            if not p:
                continue
            if "=" in p:
                k, v = p.split("=", 1)
                params[k.strip()] = _parse_param_value(v)
            else:
                params[p.strip()] = True
        result[label] = SignatureInput(label, components, params, value)
    return result


def _parse_byte_sequence(raw: str) -> bytes:
    raw = raw.strip()
    if not (raw.startswith(":") and raw.endswith(":")):
        raise SignatureError("malformed_signature", "Expected a :base64: byte sequence")
    try:
        return base64.b64decode(raw[1:-1], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SignatureError("malformed_signature", "Invalid base64 in byte sequence") from exc


def parse_signature(header: str) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for member in _split_top_level(header):
        if "=" not in member:
            raise SignatureError("malformed_signature", "Signature member has no '='")
        label, value = member.split("=", 1)
        result[label.strip()] = _parse_byte_sequence(value)
    return result


def parse_content_digest(header: str) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for member in _split_top_level(header):
        if "=" not in member:
            raise SignatureError("malformed_digest", "Content-Digest member has no '='")
        alg, value = member.split("=", 1)
        try:
            result[alg.strip().lower()] = _parse_byte_sequence(value)
        except SignatureError as exc:
            raise SignatureError("malformed_digest", exc.message) from exc
    return result


# --------------------------------------------------------------------------- digest
def content_digest_header(body: bytes, alg: str = "sha-512") -> str:
    digest = DIGEST_ALGORITHMS[alg](body).digest()
    return f"{alg}=:{base64.b64encode(digest).decode()}:"


def verify_content_digest(header: str, body: bytes) -> str:
    """Check Content-Digest against the raw body. Returns the algorithm used."""
    digests = parse_content_digest(header)
    usable = [alg for alg in ("sha-512", "sha-256") if alg in digests]
    if not usable:
        raise SignatureError("unsupported_digest", "Content-Digest has no sha-256 or sha-512 value")
    for alg in usable:
        expected = DIGEST_ALGORITHMS[alg](body).digest()
        if not hmac.compare_digest(expected, digests[alg]):
            raise SignatureError("digest_mismatch", "Body does not match Content-Digest")
    return usable[0]


# --------------------------------------------------------------------------- base
def _component_value(name: str, req: RequestView) -> str:
    if name == "@method":
        return req.method.upper()
    if name == "@authority":
        return req.authority.lower()
    if name == "@path":
        return req.path or "/"
    if name == "@query":
        return "?" + (req.query or "")
    if name == "@scheme":
        return req.scheme.lower()
    if name == "@request-target":
        return (req.path or "/") + (("?" + req.query) if req.query else "")
    if name == "@target-uri":
        query = ("?" + req.query) if req.query else ""
        return f"{req.scheme.lower()}://{req.authority.lower()}{req.path or '/'}{query}"
    if name.startswith("@"):
        raise SignatureError("unsupported_component", f"Derived component {name} is not supported")
    value = req.header(name)
    if value is None:
        raise SignatureError("missing_component", f"Covered header {name} is missing")
    # RFC 9421 2.1: strip leading/trailing whitespace, keep the value otherwise as sent.
    return value.strip()


def build_signature_base(sig_input: SignatureInput, req: RequestView) -> bytes:
    lines = []
    for name in sig_input.components:
        lines.append(f'"{name}": {_component_value(name, req)}')
    lines.append(f'"@signature-params": {sig_input.raw_value}')
    return "\n".join(lines).encode("utf-8")


# --------------------------------------------------------------------------- crypto
def _load_public_key(pem: str):
    data = pem.strip().encode()
    if b"BEGIN" not in data:  # bare base64 SPKI
        data = b"-----BEGIN PUBLIC KEY-----\n" + data + b"\n-----END PUBLIC KEY-----\n"
    return serialization.load_pem_public_key(data)


def _ecdsa_verify(key, signature: bytes, base: bytes, hash_alg, raw_len: int) -> None:
    try:
        key.verify(signature, base, ec.ECDSA(hash_alg))
        return
    except (InvalidSignature, ValueError):
        if len(signature) != raw_len:
            raise InvalidSignature() from None
    # Raw r||s fallback (the strict RFC 9421 encoding).
    half = raw_len // 2
    r = int.from_bytes(signature[:half], "big")
    s = int.from_bytes(signature[half:], "big")
    key.verify(encode_dss_signature(r, s), base, ec.ECDSA(hash_alg))


def verify_with_key(pem: str, alg: str | None, signature: bytes, base: bytes) -> str:
    """Verify signature over base with the PEM public key. Returns the algorithm used."""
    key = _load_public_key(pem)
    if alg is None:  # infer from the key when the signer did not send alg
        if isinstance(key, ec.EllipticCurvePublicKey):
            alg = "ecdsa-p384-sha384" if key.curve.name == "secp384r1" else "ecdsa-p256-sha256"
        elif isinstance(key, rsa.RSAPublicKey):
            alg = "rsa-pss-sha512"
    if alg not in SUPPORTED_ALGS:
        raise SignatureError("unsupported_alg", f"Unsupported signature algorithm {alg!r}")
    try:
        if alg == "ecdsa-p256-sha256":
            if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
                raise SignatureError("key_alg_mismatch", "Key is not EC P-256")
            _ecdsa_verify(key, signature, base, hashes.SHA256(), 64)
        elif alg == "ecdsa-p384-sha384":
            if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp384r1":
                raise SignatureError("key_alg_mismatch", "Key is not EC P-384")
            _ecdsa_verify(key, signature, base, hashes.SHA384(), 96)
        elif alg == "rsa-pss-sha512":
            if not isinstance(key, rsa.RSAPublicKey):
                raise SignatureError("key_alg_mismatch", "Key is not RSA")
            key.verify(
                signature,
                base,
                padding.PSS(mgf=padding.MGF1(hashes.SHA512()), salt_length=64),
                hashes.SHA512(),
            )
        elif alg == "rsa-v1_5-sha256":
            if not isinstance(key, rsa.RSAPublicKey):
                raise SignatureError("key_alg_mismatch", "Key is not RSA")
            key.verify(signature, base, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise SignatureError("bad_signature", "Signature does not verify with the pawaPay key") from exc
    return alg


# --------------------------------------------------------------------------- entry point
def verify_request(
    req: RequestView,
    body: bytes,
    key_resolver: Callable[[str], str | None],
    *,
    now: int | None = None,
    clock_skew: int = 30,
    max_age: int = 300,
) -> VerificationResult:
    """Full verification of a signed callback. Never raises; returns ok=False with a reason."""
    now = int(time.time()) if now is None else now
    result = VerificationResult(ok=False)
    try:
        sig_header = req.header("Signature")
        input_header = req.header("Signature-Input")
        digest_header = req.header("Content-Digest")
        if not sig_header or not input_header or not digest_header:
            raise SignatureError(
                "incomplete_headers",
                "Signature, Signature-Input and Content-Digest are all required",
            )

        result.digest_alg = verify_content_digest(digest_header, body)

        inputs = parse_signature_input(input_header)
        signatures = parse_signature(sig_header)
        label = PREFERRED_LABEL if PREFERRED_LABEL in inputs else next(iter(inputs), None)
        if label is None or label not in signatures:
            raise SignatureError("label_mismatch", "No matching label in Signature and Signature-Input")
        sig_input = inputs[label]
        result.label = label
        result.covered = list(sig_input.components)

        # The digest only protects the body if the signature covers the digest header.
        if "content-digest" not in sig_input.components:
            raise SignatureError("digest_not_covered", "Signature does not cover content-digest")

        created = sig_input.params.get("created")
        expires = sig_input.params.get("expires")
        if isinstance(expires, int) and now > expires + clock_skew:
            raise SignatureError("expired", f"Signature expired at {expires}, now {now}")
        if isinstance(created, int):
            if created > now + clock_skew:
                raise SignatureError("created_in_future", "Signature created in the future")
            if not isinstance(expires, int) and now - created > max_age:
                raise SignatureError("expired", f"Signature older than {max_age}s")
        elif not isinstance(expires, int):
            raise SignatureError("no_timestamps", "Signature has neither created nor expires")

        keyid = sig_input.params.get("keyid")
        if not isinstance(keyid, str) or not keyid:
            raise SignatureError("missing_keyid", "Signature-Input has no keyid")
        result.keyid = keyid
        try:
            pem = key_resolver(keyid)
        except Exception as exc:  # network or config failure while fetching keys
            raise SignatureError("key_unavailable", f"Could not load pawaPay public keys: {exc}") from exc
        if not pem:
            raise SignatureError("unknown_key", f"No pawaPay public key with id {keyid}")

        alg = sig_input.params.get("alg")
        base = build_signature_base(sig_input, req)
        result.alg = verify_with_key(pem, alg if isinstance(alg, str) else None, signatures[label], base)
        result.ok = True
        result.reason = "ok"
        result.message = "Signature verified"
        return result
    except SignatureError as exc:
        result.ok = False
        result.reason = exc.reason
        result.message = exc.message
        return result
    except Exception as exc:  # malformed key material or anything unexpected
        result.ok = False
        result.reason = "verification_error"
        result.message = f"{type(exc).__name__}: {exc}"
        return result


# --------------------------------------------------------------------------- signing (tests, forged demo)
def sign_request(
    req: RequestView,
    private_key,
    *,
    keyid: str,
    created: int,
    expires: int,
    components: tuple[str, ...] = (
        "@method",
        "@authority",
        "@path",
        "signature-date",
        "content-digest",
        "content-type",
    ),
    label: str = PREFERRED_LABEL,
    alg: str = "ecdsa-p256-sha256",
) -> tuple[str, str]:
    """Produce (Signature-Input, Signature) header values, DER-encoded like pawaPay."""
    comp = " ".join(f'"{c}"' for c in components)
    raw_value = f'({comp});alg="{alg}";keyid="{keyid}";created={created};expires={expires}'
    sig_input = SignatureInput(label, list(components), {}, raw_value)
    base = build_signature_base(sig_input, req)
    if alg == "ecdsa-p256-sha256":
        signature = private_key.sign(base, ec.ECDSA(hashes.SHA256()))  # DER by default
    else:
        raise ValueError("sign_request only implements ecdsa-p256-sha256")
    return (
        f"{label}={raw_value}",
        f"{label}=:{base64.b64encode(signature).decode()}:",
    )
