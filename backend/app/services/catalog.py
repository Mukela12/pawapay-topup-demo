"""Country/provider catalogue for the top-up form, built from GET /v2/active-conf.

Only PROVIDER_AUTH providers whose DEPOSIT status is not CLOSED are offered. When the token is
unset or active-conf is unavailable, a small static Zambia catalogue is served instead so the
UI still renders.
"""
from __future__ import annotations

import logging
from typing import Any

from flask import current_app

from ..money import decimals_from_conf
from ..pawapay.client import PawaPayError, get_client, is_configured

log = logging.getLogger(__name__)

FALLBACK_ZAMBIA = {
    "country": "ZMB",
    "display_name": "Zambia",
    "prefix": "260",
    "currency": "ZMW",
    "providers": [
        {
            "provider": "MTN_MOMO_ZMB",
            "display_name": "MTN",
            "logo": None,
            "currency": "ZMW",
            "name_displayed_to_customer": "Ringwise",
            "min_amount": "1",
            "max_amount": "10000",
            "decimals": 2,
            "decimals_in_amount": "TWO_PLACES",
            "status": "OPERATIONAL",
            "pin_prompt": "AUTOMATIC",
            "pin_prompt_revivable": False,
            "pin_prompt_instructions": [],
        },
        {
            "provider": "AIRTEL_OAPI_ZMB",
            "display_name": "Airtel",
            "logo": None,
            "currency": "ZMW",
            "name_displayed_to_customer": "Ringwise",
            "min_amount": "1",
            "max_amount": "10000",
            "decimals": 2,
            "decimals_in_amount": "TWO_PLACES",
            "status": "OPERATIONAL",
            "pin_prompt": "AUTOMATIC",
            "pin_prompt_revivable": False,
            "pin_prompt_instructions": [],
        },
        {
            "provider": "ZAMTEL_ZMB",
            "display_name": "Zamtel",
            "logo": None,
            "currency": "ZMW",
            "name_displayed_to_customer": "Ringwise",
            "min_amount": "1",
            "max_amount": "10000",
            "decimals": 2,
            "decimals_in_amount": "TWO_PLACES",
            "status": "OPERATIONAL",
            "pin_prompt": "AUTOMATIC",
            "pin_prompt_revivable": False,
            "pin_prompt_instructions": [],
        },
    ],
}

# Sandbox test numbers, from https://docs.pawapay.io/v2/docs/test_numbers (checked 2026-10-03).
SCENARIOS = [
    {
        "id": "mtn-completed",
        "label": "MTN, approved",
        "description": "The sandbox approves the payment and sends a COMPLETED callback.",
        "country": "ZMB",
        "provider": "MTN_MOMO_ZMB",
        "phone": "260763456789",
        "expected": "COMPLETED",
    },
    {
        "id": "mtn-not-approved",
        "label": "MTN, PIN not approved",
        "description": "The customer does not approve the PIN prompt in time.",
        "country": "ZMB",
        "provider": "MTN_MOMO_ZMB",
        "phone": "260763456039",
        "expected": "FAILED: PAYMENT_NOT_APPROVED",
    },
    {
        "id": "mtn-payer-not-found",
        "label": "MTN, no wallet",
        "description": "The number has no MTN mobile money account.",
        "country": "ZMB",
        "provider": "MTN_MOMO_ZMB",
        "phone": "260763456029",
        "expected": "FAILED: PAYER_NOT_FOUND",
    },
    {
        "id": "mtn-unspecified",
        "label": "MTN, declined",
        "description": "The network declines without a specific reason.",
        "country": "ZMB",
        "provider": "MTN_MOMO_ZMB",
        "phone": "260763456069",
        "expected": "FAILED: UNSPECIFIED_FAILURE",
    },
    {
        "id": "mtn-submitted",
        "label": "MTN, never settles",
        "description": (
            "The payment stays pending with no final callback, so reconciliation has to settle it."
        ),
        "country": "ZMB",
        "provider": "MTN_MOMO_ZMB",
        "phone": "260763456129",
        "expected": "SUBMITTED (stays open)",
    },
    {
        "id": "airtel-completed",
        "label": "Airtel, approved",
        "description": "The sandbox approves the payment and sends a COMPLETED callback.",
        "country": "ZMB",
        "provider": "AIRTEL_OAPI_ZMB",
        "phone": "260973456789",
        "expected": "COMPLETED",
    },
    {
        "id": "airtel-insufficient",
        "label": "Airtel, low balance",
        "description": "The wallet does not have enough money for the payment.",
        "country": "ZMB",
        "provider": "AIRTEL_OAPI_ZMB",
        "phone": "260973456049",
        "expected": "FAILED: INSUFFICIENT_BALANCE",
    },
]
SCENARIO_IDS = {s["id"] for s in SCENARIOS}


def _deposit_conf(currency_entry: dict) -> dict | None:
    """operationTypes is an object in the schema but a list in the docs example. Accept both."""
    ops = currency_entry.get("operationTypes")
    if isinstance(ops, dict):
        dep = ops.get("DEPOSIT")
        return dep if isinstance(dep, dict) else None
    if isinstance(ops, list):
        for item in ops:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("DEPOSIT"), dict):
                return item["DEPOSIT"]
            if str(item.get("operationType", "")).upper() == "DEPOSIT":
                return item
    return None


def _instructions(dep: dict) -> list[dict]:
    block = dep.get("pinPromptInstructions")
    channels = block.get("channels") if isinstance(block, dict) else None
    out = []
    for ch in channels or []:
        if not isinstance(ch, dict):
            continue
        display = ch.get("displayName")
        steps = ch.get("instructions")
        en_steps = steps.get("en") if isinstance(steps, dict) else None
        out.append(
            {
                "type": ch.get("type"),
                "display_name": display.get("en") if isinstance(display, dict) else display,
                "quick_link": ch.get("quickLink"),
                "steps": [
                    s.get("text") for s in (en_steps or []) if isinstance(s, dict) and s.get("text")
                ],
            }
        )
    return out


def parse_active_conf(conf: Any) -> list[dict]:
    countries_out = []
    for c in (conf or {}).get("countries") or []:
        if not isinstance(c, dict):
            continue
        display = c.get("displayName")
        providers_out = []
        for p in c.get("providers") or []:
            if not isinstance(p, dict):
                continue
            for cur in p.get("currencies") or []:
                if not isinstance(cur, dict):
                    continue
                dep = _deposit_conf(cur)
                if not dep:
                    continue
                if str(dep.get("authType", "")).upper() != "PROVIDER_AUTH":
                    continue
                status = str(dep.get("status") or "OPERATIONAL").upper()
                if status == "CLOSED":
                    continue
                decimals_raw = dep.get("decimalsInAmount") or "TWO_PLACES"
                providers_out.append(
                    {
                        "provider": p.get("provider"),
                        "display_name": p.get("displayName") or p.get("provider"),
                        "logo": p.get("logo"),
                        "currency": cur.get("currency"),
                        "name_displayed_to_customer": p.get("nameDisplayedToCustomer"),
                        # v2 schema says minAmount/maxAmount; doc examples still show the old names.
                        "min_amount": str(dep.get("minAmount") or dep.get("minTransactionLimit") or "")
                        or None,
                        "max_amount": str(dep.get("maxAmount") or dep.get("maxTransactionLimit") or "")
                        or None,
                        "decimals": decimals_from_conf(decimals_raw),
                        "decimals_in_amount": str(decimals_raw).upper(),
                        "status": status,
                        "pin_prompt": dep.get("pinPrompt"),
                        "pin_prompt_revivable": bool(dep.get("pinPromptRevivable")),
                        "pin_prompt_instructions": _instructions(dep),
                    }
                )
        if not providers_out:
            continue
        currencies = [p["currency"] for p in providers_out if p.get("currency")]
        countries_out.append(
            {
                "country": c.get("country"),
                "display_name": (display.get("en") if isinstance(display, dict) else display)
                or c.get("country"),
                "prefix": str(c.get("prefix") or ""),
                "currency": max(set(currencies), key=currencies.count) if currencies else None,
                "providers": providers_out,
            }
        )
    return countries_out


def get_catalog() -> dict:
    """Returns {"countries": [...], "source": "pawapay" | "fallback", "reason": ...}."""
    if not is_configured():
        return {"countries": [FALLBACK_ZAMBIA], "source": "fallback", "reason": "pawapay_not_configured"}
    try:
        conf = get_client().active_conf()
        countries = parse_active_conf(conf)
        if countries:
            return {"countries": countries, "source": "pawapay", "reason": None}
        return {"countries": [FALLBACK_ZAMBIA], "source": "fallback", "reason": "active_conf_empty"}
    except PawaPayError as exc:
        log.warning("active-conf unavailable, serving fallback catalogue: %s", exc)
        return {"countries": [FALLBACK_ZAMBIA], "source": "fallback", "reason": "active_conf_unavailable"}


def find_provider(catalog: dict, country: str, provider: str) -> tuple[dict | None, dict | None]:
    for c in catalog["countries"]:
        if c["country"] == country:
            for p in c["providers"]:
                if p["provider"] == provider:
                    return c, p
            return c, None
    return None, None


def is_sandbox() -> bool:
    return "sandbox" in (current_app.config.get("PAWAPAY_BASE_URL") or "")
