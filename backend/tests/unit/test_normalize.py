from decimal import Decimal

import pytest

from app.scraping.normalize import detect_currency, parse_price


@pytest.mark.parametrize("raw, expected", [
    ("1 299,90 €", "1299.90"), ("$1,299.99", "1299.99"), ("1.299,90 Kč", "1299.90"), ("12,50", "12.50"),
    ("1,299", "1299.00"), ("1.299", "1299.00"), ("19.99", "19.99"), ("1 299,- Kč", "1299.00"), (42, "42.00"),
    ("€ 7.5", "7.50"), ("1\xa0234,00\xa0€", "1234.00"), ("44,00\xa0Kč", "44.00"), (Decimal("3.456"), "3.46"),
])
def test_parse_price(raw, expected):
    assert parse_price(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["abc", None, "", True, "-5", float("nan")])
def test_parse_price_invalid(raw):
    assert parse_price(raw) is None


@pytest.mark.parametrize("raw, expected", [
    ("44,00 Kč", "CZK"), ("12 €", "EUR"), ("EUR 5", "EUR"), ("price: 3 czk", "CZK"), ("12", None), (None, None),
])
def test_detect_currency(raw, expected):
    assert detect_currency(raw) == expected
