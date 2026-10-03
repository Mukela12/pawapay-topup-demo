"""Money helpers: integer minor units in storage, Decimal at the pawaPay boundary.

pawaPay's amount schema is ^([0]|([1-9][0-9]{0,17}))([.][0-9]{0,3}[1-9])?$ which rejects
trailing zeros: "100.50" and "100.00" are invalid, "100.5" and "100" are valid. Responses
and callbacks may still come back as "100.00", so provider amounts are always compared as
Decimal, never as strings.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

PAWAPAY_AMOUNT_RE = re.compile(r"^([0]|([1-9][0-9]{0,17}))([.][0-9]{0,3}[1-9])?$")
USER_AMOUNT_RE = re.compile(r"^[0-9]{1,18}(\.[0-9]{1,4})?$")

# ISO 4217 minor-unit exponents for the currencies pawaPay serves (plus a few common ones).
# TZS is 2 in ISO 4217; Tanzanian providers accept no decimals, which is a per-provider
# rule (decimalsInAmount NONE) and is enforced separately from the currency exponent.
CURRENCY_EXPONENTS: dict[str, int] = {
    "ZMW": 2,
    "KES": 2,
    "GHS": 2,
    "ETB": 2,
    "TZS": 2,
    "MWK": 2,
    "CDF": 2,
    "USD": 2,
    "NGN": 2,
    "MZN": 2,
    "SLE": 2,
    "LSL": 2,
    "CAD": 2,
    "EUR": 2,
    "UGX": 0,
    "XOF": 0,
    "XAF": 0,
    "RWF": 0,
    "GNF": 0,
}


class AmountError(ValueError):
    """Raised for an amount that cannot be represented or is not allowed."""


def exponent(currency: str) -> int:
    try:
        return CURRENCY_EXPONENTS[currency.upper()]
    except KeyError as exc:
        raise AmountError(f"Unsupported currency {currency!r}") from exc


def decimals_from_conf(decimals_in_amount: str | int | None) -> int:
    """Map active-conf decimalsInAmount (TWO_PLACES | NONE) to a number of places."""
    if isinstance(decimals_in_amount, int):
        return decimals_in_amount
    if decimals_in_amount and str(decimals_in_amount).upper() == "NONE":
        return 0
    return 2


def minor_to_decimal(amount_minor: int, currency: str) -> Decimal:
    return Decimal(int(amount_minor)).scaleb(-exponent(currency))


def to_pawapay_amount(
    amount_minor: int, currency: str, decimals_in_amount: str | int | None = "TWO_PLACES"
) -> str:
    """Format minor units as a pawaPay amount string with no trailing zeros.

    10050 ZMW -> "100.5", 10000 ZMW -> "100", 1525 ZMW -> "15.25", 5000 UGX -> "5000".
    """
    if int(amount_minor) <= 0:
        raise AmountError("Amount must be positive")
    value = minor_to_decimal(amount_minor, currency)
    places = decimals_from_conf(decimals_in_amount)
    if value != value.quantize(Decimal(1).scaleb(-places)):
        raise AmountError(
            f"{currency} amount {value} has more decimal places than this provider allows ({places})"
        )
    normalized = value.normalize()
    text = format(normalized, "f")
    if not PAWAPAY_AMOUNT_RE.match(text):  # defensive: never send something pawaPay rejects
        raise AmountError(f"Formatted amount {text!r} does not match pawaPay's amount format")
    return text


def parse_user_amount(raw: str, currency: str, decimals_in_amount: str | int | None) -> int:
    """Parse a customer-entered amount string ("10", "10.5", "10.50") into minor units."""
    if not isinstance(raw, str):
        raise AmountError("Amount must be a string, for example \"25\" or \"25.50\"")
    raw = raw.strip()
    if not USER_AMOUNT_RE.match(raw):
        raise AmountError("Enter an amount like 25 or 25.50")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise AmountError("Enter an amount like 25 or 25.50") from exc
    if value <= 0:
        raise AmountError("Amount must be more than zero")
    places = min(decimals_from_conf(decimals_in_amount), exponent(currency))
    if value != value.quantize(Decimal(1).scaleb(-places)):
        if places == 0:
            raise AmountError("This network only accepts whole amounts")
        raise AmountError(f"Use at most {places} decimal places")
    return int(value.scaleb(exponent(currency)))


def parse_provider_amount(raw: object) -> Decimal | None:
    """Leniently parse an amount coming back from pawaPay ("15", "15.00", 15)."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite():
        return None
    return value


def provider_amount_matches(raw: object, amount_minor: int, currency: str) -> bool:
    value = parse_provider_amount(raw)
    if value is None:
        return False
    return value == minor_to_decimal(amount_minor, currency)


def format_display(amount_minor: int, currency: str) -> str:
    """Fixed decimal display string, for example "100.50"."""
    exp = exponent(currency)
    value = minor_to_decimal(amount_minor, currency)
    return format(value.quantize(Decimal(1).scaleb(-exp)), "f")
