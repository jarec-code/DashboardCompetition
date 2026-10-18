import pytest

from app.services.identifiers import gtin_keys, normalize_ean, normalize_isbn, normalize_sku
from app.services.matching import name_similarity


@pytest.mark.parametrize("raw, expected", [
    ("978-0-306-40615-7", "9780306406157"),
    ("0-306-40615-2", "9780306406157"),      # ISBN-10 converted to ISBN-13
    ("ISBN 978 80 7358 185 1", None),         # wrong check digit
    ("0-306-40615-3", None),
    ("8594165541796", None),                  # an EAN, not an ISBN (no 978/979 prefix)
    ("", None), (None, None), ("abc", None),
])
def test_normalize_isbn(raw, expected):
    assert normalize_isbn(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("8594165541796", "8594165541796"),       # EAN from alter.cz
    ("4006381333931", "4006381333931"),
    ("4006381333932", None),
    ("036000291452", "0036000291452"),        # UPC-A padded to 13 digits
    ("9780306406157", "9780306406157"),       # ISBN-13 is a valid EAN-13
    ("12345", None), (None, None),
])
def test_normalize_ean(raw, expected):
    assert normalize_ean(raw) == expected


def test_gtin_keys_treat_isbn_and_ean_alike():
    assert gtin_keys("0-306-40615-2", None) == {"9780306406157"}
    assert gtin_keys(None, "978-0306406157") == {"9780306406157"}
    assert normalize_sku(" ab 12-x ") == "AB12-X"


def test_name_similarity_same_title_with_different_punctuation():
    a = "Hravá matematika 2 – pracovní učebnice – 1. díl – revidované RVP ZV"
    b = "HRAVÁ MATEMATIKA 2 - pracovní učebnice, 1. díl (revidované RVP ZV)"
    assert name_similarity(a, b) >= 0.9


def test_name_similarity_numbers_must_agree():
    assert name_similarity("Matematika 2 pro SOŠ – pracovní sešit", "Matematika 3 pro SOŠ – pracovní sešit") < 0.6
    assert name_similarity("Hravá písanka 1 - 2. díl", "Hravá písanka 1 - 5. díl") < 0.6


def test_name_similarity_unrelated():
    assert name_similarity("Pohostinství & gastronomie 4", "Detektiv Písmenko 5") < 0.3
    assert name_similarity("", "cokoliv") == 0.0
