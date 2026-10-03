"""Thin pawaPay Merchant API v2 client (httpx, explicit timeouts, typed errors).

Design rules:
- Business results come back in the body: REJECTED arrives as HTTP 200, so callers branch on
  body["status"], not only on the HTTP code. Calls that move money return an ApiResult and
  never raise for 4xx/5xx.
- A timeout or connection failure raises PawaPayTransportError: the outcome is UNKNOWN and
  the caller must check status before deciding anything.
- Parsing is lenient: unknown fields are kept, non-JSON bodies are wrapped, not fatal.
- The bearer token is only ever placed in the Authorization header and never logged.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx
from flask import current_app

log = logging.getLogger(__name__)

ACTIVE_CONF_TTL_SECONDS = 300
PUBLIC_KEYS_TTL_SECONDS = 3600
# An unknown keyid may force a refresh (key rotation), but at most once per this many seconds
# per process. Callers are unauthenticated, so without this limit a stream of callbacks with
# random keyids would make us call GET /v2/public-key/http once per request.
PUBLIC_KEYS_MIN_REFRESH_SECONDS = 60


class PawaPayError(Exception):
    """Base error for pawaPay client failures."""


class PawaPayNotConfigured(PawaPayError):
    """PAWAPAY_API_TOKEN is not set."""


class PawaPayTransportError(PawaPayError):
    """Timeout or network failure.

    request_sent is False only when the connection was never established (connect error or
    timeout, pool timeout), so pawaPay certainly never saw the request. Otherwise the request
    may have reached pawaPay and may still be in flight there.
    """

    def __init__(self, message: str, *, request_sent: bool = True):
        super().__init__(message)
        self.request_sent = request_sent


class PawaPayHTTPError(PawaPayError):
    """Non-success HTTP status on a read-only endpoint."""

    def __init__(self, status_code: int, body: Any):
        super().__init__(f"pawaPay returned HTTP {status_code}")
        self.status_code = status_code
        self.body = body


@dataclass
class ApiResult:
    http_status: int
    body: Any

    @property
    def status(self) -> str | None:
        if isinstance(self.body, dict):
            value = self.body.get("status")
            return str(value).upper() if value is not None else None
        return None

    @property
    def failure_reason(self) -> dict:
        if isinstance(self.body, dict):
            reason = self.body.get("failureReason")
            if isinstance(reason, dict):
                return reason
        return {}


# In-process caches (per worker). Keys include the base URL so sandbox and production
# never share entries.
_cache_lock = threading.Lock()
_active_conf_cache: dict[tuple[str, str], tuple[float, Any]] = {}
_public_keys_cache: dict[str, tuple[float, list[dict]]] = {}
_public_keys_forced_at: dict[str, float] = {}
_http_clients: dict[tuple[str, float, float], httpx.Client] = {}


def clear_caches() -> None:
    with _cache_lock:
        _active_conf_cache.clear()
        _public_keys_cache.clear()
        _public_keys_forced_at.clear()


def _parse_body(response: httpx.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except (ValueError, RecursionError):
        return {"raw": response.text[:2000]}


class PawaPayClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        connect_timeout: float = 5.0,
        read_timeout: float = 15.0,
    ):
        if not token:
            raise PawaPayNotConfigured("PAWAPAY_API_TOKEN is not set")
        self.base_url = base_url.rstrip("/")
        self._token = token
        key = (self.base_url, connect_timeout, read_timeout)
        with _cache_lock:
            client = _http_clients.get(key)
            if client is None or client.is_closed:
                client = httpx.Client(
                    base_url=self.base_url,
                    timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
                    headers={"Accept": "application/json", "User-Agent": "ringwise-demo/1.0"},
                )
                _http_clients[key] = client
        self._http = client

    # ------------------------------------------------------------------ transport
    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict | None = None,
    ) -> ApiResult:
        headers = {"Authorization": f"Bearer {self._token}"}
        if json is not None:
            headers["Content-Type"] = "application/json"
        started = time.monotonic()
        try:
            response = self._http.request(method, path, json=json, params=params, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            log.warning("pawapay %s %s could not connect: %s", method, path, type(exc).__name__)
            raise PawaPayTransportError(
                f"Could not connect to pawaPay for {method} {path}", request_sent=False
            ) from exc
        except httpx.TimeoutException as exc:
            log.warning("pawapay %s %s timed out after %.1fs", method, path, time.monotonic() - started)
            raise PawaPayTransportError(f"Timeout calling {method} {path}") from exc
        except httpx.TransportError as exc:
            log.warning("pawapay %s %s transport error: %s", method, path, type(exc).__name__)
            raise PawaPayTransportError(f"Network error calling {method} {path}") from exc
        log.info(
            "pawapay %s %s -> %s in %.0fms",
            method,
            path,
            response.status_code,
            (time.monotonic() - started) * 1000,
        )
        return ApiResult(response.status_code, _parse_body(response))

    # ------------------------------------------------------------------ deposits
    def initiate_deposit(
        self,
        *,
        deposit_id: str,
        amount: str,
        currency: str,
        provider: str,
        phone_number: str,
        customer_message: str,
        metadata: list[dict],
    ) -> ApiResult:
        payload = {
            "depositId": deposit_id,
            "amount": amount,
            "currency": currency,
            "payer": {
                "type": "MMO",
                "accountDetails": {"phoneNumber": phone_number, "provider": provider},
            },
            "customerMessage": customer_message,
            "metadata": metadata,
        }
        return self._request("POST", "/v2/deposits", json=payload)

    def check_deposit(self, deposit_id: str) -> ApiResult:
        return self._request("GET", f"/v2/deposits/{deposit_id}")

    def resend_callback(self, deposit_id: str) -> ApiResult:
        return self._request("POST", f"/v2/deposits/resend-callback/{deposit_id}")

    # ------------------------------------------------------------------ toolkit
    def predict_provider(self, phone_number: str) -> ApiResult:
        return self._request("POST", "/v2/predict-provider", json={"phoneNumber": phone_number})

    def active_conf(self, country: str | None = None) -> Any:
        """GET /v2/active-conf?operationType=DEPOSIT, cached for 5 minutes per process."""
        key = (self.base_url, country or "")
        now = time.monotonic()
        with _cache_lock:
            hit = _active_conf_cache.get(key)
            if hit and now - hit[0] < ACTIVE_CONF_TTL_SECONDS:
                return hit[1]
        params = {"operationType": "DEPOSIT"}
        if country:
            params["country"] = country
        result = self._request("GET", "/v2/active-conf", params=params)
        if result.http_status != 200 or not isinstance(result.body, dict):
            raise PawaPayHTTPError(result.http_status, result.body)
        with _cache_lock:
            _active_conf_cache[key] = (now, result.body)
        return result.body

    def public_keys(self, force_refresh: bool = False) -> list[dict]:
        """GET /v2/public-key/http, cached for 1 hour per process. Returns [{id, key}]."""
        now = time.monotonic()
        with _cache_lock:
            hit = _public_keys_cache.get(self.base_url)
            if hit and not force_refresh and now - hit[0] < PUBLIC_KEYS_TTL_SECONDS:
                return hit[1]
        result = self._request("GET", "/v2/public-key/http")
        if result.http_status != 200:
            raise PawaPayHTTPError(result.http_status, result.body)
        body = result.body
        if isinstance(body, dict):  # tolerate a wrapped shape
            body = body.get("keys") or body.get("data") or []
        keys = [
            {"id": str(item.get("id")), "key": str(item.get("key"))}
            for item in (body or [])
            if isinstance(item, dict) and item.get("id") and item.get("key")
        ]
        with _cache_lock:
            _public_keys_cache[self.base_url] = (now, keys)
        return keys

    def _claim_forced_refresh(self) -> bool:
        """Allow a forced key refresh only if the keys were not fetched (or force-refreshed) in
        the last PUBLIC_KEYS_MIN_REFRESH_SECONDS. Claimed under the lock, so concurrent requests
        with unknown keyids produce one refresh between them, not one each."""
        now = time.monotonic()
        with _cache_lock:
            hit = _public_keys_cache.get(self.base_url)
            fetched_at = hit[0] if hit else float("-inf")
            last = max(fetched_at, _public_keys_forced_at.get(self.base_url, float("-inf")))
            if now - last < PUBLIC_KEYS_MIN_REFRESH_SECONDS:
                return False
            _public_keys_forced_at[self.base_url] = now
            return True

    def public_key_for(self, key_id: str) -> str | None:
        keys = self.public_keys()
        for item in keys:
            if item["id"] == key_id:
                return item["key"]
        # Key rotation: refresh before giving up on an unknown keyid, rate limited. A genuinely
        # new pawaPay key that lands inside the window fails once; pawaPay retries callbacks for
        # 15 minutes, so a retry after the window picks the new key up.
        if not self._claim_forced_refresh():
            return None
        keys = self.public_keys(force_refresh=True)
        for item in keys:
            if item["id"] == key_id:
                return item["key"]
        return None


def is_configured() -> bool:
    return bool(current_app.config.get("PAWAPAY_API_TOKEN"))


def get_client() -> PawaPayClient:
    """Client for the current app config. Raises PawaPayNotConfigured without a token."""
    cfg = current_app.config
    token = cfg.get("PAWAPAY_API_TOKEN")
    if not token:
        raise PawaPayNotConfigured("PAWAPAY_API_TOKEN is not set")
    return PawaPayClient(
        cfg["PAWAPAY_BASE_URL"],
        token,
        connect_timeout=cfg.get("PAWAPAY_CONNECT_TIMEOUT", 5.0),
        read_timeout=cfg.get("PAWAPAY_READ_TIMEOUT", 15.0),
    )
