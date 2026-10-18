import pytest

from app.services.urls import InvalidUrl, domain_of, normalize_url


@pytest.mark.parametrize("raw, expected", [
    ("https://www.alter.cz", "https://www.alter.cz/"),
    ("  HTTPS://WWW.Alter.CZ/shop?x=1#top ", "https://www.alter.cz/shop?x=1"),
    ("alter.cz", "https://alter.cz/"),
    ("http://alter.cz:80/", "http://alter.cz/"),
    ("https://alter.cz:443/x", "https://alter.cz/x"),
    ("https://alter.cz:80/", "https://alter.cz:80/"),
    ("https://kníhkupectvo.sk", "https://xn--knhkupectvo-pcb.sk/"),
])
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "ftp://alter.cz", "https://user:pass@alter.cz", "https://alter.cz:8080", "http://localhost",
    "http://127.0.0.1", "http://[::1]/", "http://10.0.0.5", "https://intranet", "https://server.local",
    "https://" + "a" * 2050 + ".cz", "javascript:alert(1)", "https://alter..cz",
])
def test_invalid_urls(raw):
    with pytest.raises(InvalidUrl):
        normalize_url(raw)


def test_domain_strips_www_only():
    assert domain_of("https://www.alter.cz/") == "alter.cz"
    assert domain_of("https://eshop.alter.cz/") == "eshop.alter.cz"
