"""Extract product names and prices from competitor web pages.

Strategies, in order:
  1. Custom CSS selectors configured for the competitor (most reliable when set).
  2. schema.org JSON-LD (Product, ItemList, @graph) - used by most e-shops for SEO.
  3. schema.org microdata (itemtype=".../Product").
  4. Open Graph product meta tags (single product pages).
"""
import json
import re
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = "CompetitionChecker/1.0 (price monitoring)"
TIMEOUT = 20


class ScrapeError(Exception):
    pass


def parse_price(value):
    """Turn '1 299,90 €', '$1,299.99', '1.299,-' or 12.5 into a float (or None)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("\xa0", " ").strip()
    match = re.search(r"\d[\d\s.,']*", text)
    if not match:
        return None
    num = re.sub(r"[\s']", "", match.group(0)).rstrip(".,")
    if "," in num and "." in num:
        # Whichever separator comes last is the decimal separator.
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        whole, _, frac = num.rpartition(",")
        num = whole.replace(",", "") + ("." + frac if len(frac) != 3 else frac)
    elif num.count(".") > 1 or (num.count(".") == 1 and len(num.rpartition(".")[2]) == 3 and len(num) > 4):
        num = num.replace(".", "")
    try:
        return float(num)
    except ValueError:
        return None


def detect_currency(text):
    if not text:
        return None
    text = str(text)
    for symbol, code in (("€", "EUR"), ("$", "USD"), ("£", "GBP"), ("Kč", "CZK"), ("zł", "PLN"), ("Ft", "HUF")):
        if symbol in text:
            return code
    match = re.search(r"\b(EUR|USD|GBP|CZK|PLN|HUF|CHF)\b", text, re.I)
    return match.group(1).upper() if match else None


def allowed_by_robots(url):
    parts = urlparse(url)
    rp = robotparser.RobotFileParser()
    try:
        resp = requests.get(f"{parts.scheme}://{parts.netloc}/robots.txt",
                            headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        if resp.status_code >= 400:
            return True
        rp.parse(resp.text.splitlines())
        return rp.can_fetch(USER_AGENT, url)
    except requests.RequestException:
        return True


def fetch(url):
    if not allowed_by_robots(url):
        raise ScrapeError(f"robots.txt disallows {url}")
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en,sk;q=0.8"},
                            timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise ScrapeError(f"{url}: {exc}") from exc
    return resp.text


# ---------- JSON-LD ----------

def _is_type(obj, name):
    t = obj.get("@type")
    return t == name or (isinstance(t, list) and name in t)


def _offer_price(offers):
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    if not isinstance(offers, dict):
        return None, None
    price = offers.get("price") or offers.get("lowPrice")
    if price is None and isinstance(offers.get("priceSpecification"), dict):
        price = offers["priceSpecification"].get("price")
    return parse_price(price), offers.get("priceCurrency")


def _walk_jsonld(obj, found, base_url):
    if isinstance(obj, list):
        for item in obj:
            _walk_jsonld(item, found, base_url)
        return
    if not isinstance(obj, dict):
        return
    if _is_type(obj, "Product") and obj.get("name"):
        price, currency = _offer_price(obj.get("offers"))
        found.append({"name": str(obj["name"]).strip(), "price": price, "currency": currency,
                      "url": urljoin(base_url, obj.get("url") or "") or base_url})
        if not isinstance(obj.get("hasVariant"), list):
            return
    for key in ("@graph", "itemListElement", "item", "mainEntity", "hasVariant"):
        if key in obj:
            _walk_jsonld(obj[key], found, base_url)


def extract_jsonld(soup, base_url):
    found = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (ValueError, TypeError):
            continue
        _walk_jsonld(data, found, base_url)
    return found


# ---------- Microdata ----------

def extract_microdata(soup, base_url):
    found = []
    for item in soup.select('[itemtype*="schema.org/Product"]'):
        name_el = item.select_one('[itemprop="name"]')
        price_el = item.select_one('[itemprop="price"], [itemprop="lowPrice"]')
        if not name_el:
            continue
        cur_el = item.select_one('[itemprop="priceCurrency"]')
        link_el = item.select_one('[itemprop="url"], a[href]')
        price_raw = price_el.get("content") or price_el.get_text() if price_el else None
        found.append({
            "name": (name_el.get("content") or name_el.get_text()).strip(),
            "price": parse_price(price_raw),
            "currency": (cur_el.get("content") or cur_el.get_text()).strip() if cur_el else detect_currency(price_raw),
            "url": urljoin(base_url, (link_el.get("href") or link_el.get("content") or "")) if link_el else base_url,
        })
    return found


# ---------- Open Graph ----------

def extract_opengraph(soup, base_url):
    def meta(prop):
        el = soup.find("meta", attrs={"property": prop})
        return el.get("content") if el else None

    price = meta("product:price:amount") or meta("og:price:amount")
    title = meta("og:title")
    if title and price:
        return [{"name": title.strip(), "price": parse_price(price),
                 "currency": meta("product:price:currency") or meta("og:price:currency"), "url": base_url}]
    return []


# ---------- CSS selectors ----------

def extract_selectors(soup, base_url, item_sel, name_sel, price_sel):
    found = []
    for card in soup.select(item_sel):
        name_el = card.select_one(name_sel) if name_sel else None
        price_el = card.select_one(price_sel) if price_sel else None
        if not name_el:
            continue
        link = next((el for el in (name_el, card) if el.name == "a"), None) or name_el.find("a") or card.find("a")
        price_text = price_el.get_text(" ", strip=True) if price_el else None
        found.append({
            "name": name_el.get_text(" ", strip=True),
            "price": parse_price(price_text),
            "currency": detect_currency(price_text),
            "url": urljoin(base_url, link.get("href")) if link and link.get("href") else base_url,
        })
    return found


def extract_products(html, base_url, item_sel=None, name_sel=None, price_sel=None):
    soup = BeautifulSoup(html, "html.parser")
    if item_sel and name_sel:
        products = extract_selectors(soup, base_url, item_sel, name_sel, price_sel)
        if products:
            return products, "css"
    for method, func in (("json-ld", extract_jsonld), ("microdata", extract_microdata),
                         ("opengraph", extract_opengraph)):
        products = func(soup, base_url)
        if products:
            return _dedupe(products), method
    return [], None


def _dedupe(products):
    seen, result = set(), []
    for p in products:
        key = (p["name"].lower(), p["price"])
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result


def scrape_competitor(urls, item_sel=None, name_sel=None, price_sel=None):
    """Return (products, status_message)."""
    all_products, notes = [], []
    for url in urls:
        try:
            html = fetch(url)
        except ScrapeError as exc:
            notes.append(f"error: {exc}")
            continue
        products, method = extract_products(html, url, item_sel, name_sel, price_sel)
        if products:
            notes.append(f"{len(products)} via {method} from {url}")
        else:
            notes.append(f"nothing found on {url} (try setting CSS selectors)")
        all_products.extend(products)
    return all_products, "; ".join(notes)
