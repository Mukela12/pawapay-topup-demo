from decimal import Decimal

import pytest

from app.money import (
    PAWAPAY_AMOUNT_RE,
    AmountError,
    format_display,
    parse_user_amount,
    provider_amount_matches,
    to_pawapay_amount,
)


@pytest.mark.parametrize(
    "minor,currency,decimals,expected",
    [
        (10050, "ZMW", "TWO_PLACES", "100.5"),  # trailing zero dropped
        (10000, "ZMW", "TWO_PLACES", "100"),  # no ".00"
        (1525, "ZMW", "TWO_PLACES", "15.25"),
        (1, "ZMW", "TWO_PLACES", "0.01"),
        (5000, "UGX", "NONE", "5000"),  # zero-exponent currency
        (12000, "KES", "NONE", "120"),  # 2-exponent currency, provider without decimals
        (100000, "ZMW", "TWO_PLACES", "1000"),  # normalize() gives 1E+3, must still print 1000
    ],
)
def test_to_pawapay_amount_has_no_trailing_zeros(minor, currency, decimals, expected):
    out = to_pawapay_amount(minor, currency, decimals)
    assert out == expected
    assert PAWAPAY_AMOUNT_RE.match(out)


def test_pawapay_regex_rejects_trailing_zeros():
    assert not PAWAPAY_AMOUNT_RE.match("100.50")
    assert not PAWAPAY_AMOUNT_RE.match("100.00")
    assert PAWAPAY_AMOUNT_RE.match("100.5")


def test_none_decimals_rejects_fractional_amount():
    with pytest.raises(AmountError):
        to_pawapay_amount(10050, "KES", "NONE")


def test_parse_user_amount_respects_decimals():
    assert parse_user_amount("10.50", "ZMW", "TWO_PLACES") == 1050
    assert parse_user_amount("10", "ZMW", "TWO_PLACES") == 1000
    assert parse_user_amount("250", "UGX", "NONE") == 250
    assert parse_user_amount("120", "KES", "NONE") == 12000
    with pytest.raises(AmountError):
        parse_user_amount("10.5", "KES", "NONE")
    with pytest.raises(AmountError):
        parse_user_amount("10.555", "ZMW", "TWO_PLACES")
    with pytest.raises(AmountError):
        parse_user_amount("0", "ZMW", "TWO_PLACES")
    with pytest.raises(AmountError):
        parse_user_amount("1e3", "ZMW", "TWO_PLACES")
    with pytest.raises(AmountError):
        parse_user_amount(10, "ZMW", "TWO_PLACES")  # must be a string


def test_provider_amounts_compare_as_decimal_not_string():
    assert provider_amount_matches("100.00", 10000, "ZMW")
    assert provider_amount_matches("100", 10000, "ZMW")
    assert provider_amount_matches("100.50", 10050, "ZMW")
    assert provider_amount_matches("100.5", 10050, "ZMW")
    assert not provider_amount_matches("100.51", 10050, "ZMW")
    assert not provider_amount_matches(None, 10050, "ZMW")
    assert not provider_amount_matches("abc", 10050, "ZMW")


def test_format_display():
    assert format_display(10050, "ZMW") == "100.50"
    assert format_display(5000, "UGX") == "5000"
    assert Decimal(format_display(1, "ZMW")) == Decimal("0.01")
