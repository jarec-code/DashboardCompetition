import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matcher import assign_matches, similarity  # noqa: E402
from scraper import extract_products, parse_price  # noqa: E402


class ParsePriceTest(unittest.TestCase):
    def test_formats(self):
        cases = {
            "1 299,90 €": 1299.90, "$1,299.99": 1299.99, "1.299,90 Kč": 1299.90, "12,50": 12.50,
            "1,299": 1299.0, "1.299": 1299.0, "19.99": 19.99, "1 299,- Kč": 1299.0, 42: 42.0,
            "€ 7.5": 7.5, "abc": None, None: None, "1\xa0234,00\xa0€": 1234.0,
        }
        for raw, expected in cases.items():
            self.assertEqual(parse_price(raw), expected, raw)


JSONLD_PAGE = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"ItemList","itemListElement":[
 {"@type":"ListItem","position":1,"item":{"@type":"Product","name":"Acme Phone X 128GB","url":"/p/1",
   "offers":{"@type":"Offer","price":"499.00","priceCurrency":"EUR"}}},
 {"@type":"ListItem","position":2,"item":{"@type":"Product","name":"Acme Phone Y",
   "offers":[{"@type":"Offer","price":299,"priceCurrency":"EUR"}]}}]}
</script></head><body></body></html>"""

MICRODATA_PAGE = """<div itemscope itemtype="https://schema.org/Product">
  <a itemprop="url" href="/x"><span itemprop="name">Widget Pro</span></a>
  <div itemprop="offers" itemscope itemtype="https://schema.org/Offer">
    <meta itemprop="priceCurrency" content="EUR"><span itemprop="price" content="19.90">19,90 €</span>
  </div></div>"""

CSS_PAGE = """<ul><li class="card"><a class="t" href="/a">Gadget One</a><span class="p">1 049,00 €</span></li>
<li class="card"><a class="t" href="/b">Gadget Two</a><span class="p">99,90 €</span></li></ul>"""


class ExtractTest(unittest.TestCase):
    def test_jsonld(self):
        products, method = extract_products(JSONLD_PAGE, "https://shop.example/cat")
        self.assertEqual(method, "json-ld")
        self.assertEqual([(p["name"], p["price"]) for p in products],
                         [("Acme Phone X 128GB", 499.0), ("Acme Phone Y", 299.0)])
        self.assertEqual(products[0]["url"], "https://shop.example/p/1")

    def test_microdata(self):
        products, method = extract_products(MICRODATA_PAGE, "https://shop.example/")
        self.assertEqual(method, "microdata")
        self.assertEqual(products[0]["name"], "Widget Pro")
        self.assertEqual(products[0]["price"], 19.90)
        self.assertEqual(products[0]["currency"], "EUR")

    def test_css_selectors(self):
        products, method = extract_products(CSS_PAGE, "https://shop.example/", ".card", ".t", ".p")
        self.assertEqual(method, "css")
        self.assertEqual([(p["name"], p["price"], p["currency"]) for p in products],
                         [("Gadget One", 1049.0, "EUR"), ("Gadget Two", 99.9, "EUR")])
        self.assertEqual(products[1]["url"], "https://shop.example/b")

    def test_nothing_found(self):
        self.assertEqual(extract_products("<p>hello</p>", "https://x/"), ([], None))


class MatcherTest(unittest.TestCase):
    def test_similarity(self):
        self.assertGreater(similarity("Acme Phone X 128GB", "ACME phone X (128 GB)"), 0.6)
        self.assertLess(similarity("Acme Phone X", "Garden Hose 20m"), 0.4)

    def test_assign_with_manual_override(self):
        ours = [{"id": 1, "name": "Acme Phone X 128GB"}, {"id": 2, "name": "Widget Pro"}]
        cps = [{"id": 10, "competitor_id": 5, "name": "Acme Phone X 128 GB"},
               {"id": 11, "competitor_id": 5, "name": "Widget Pro"},
               {"id": 12, "competitor_id": 5, "name": "Unrelated thing"}]
        self.assertEqual({k: v[0] for k, v in assign_matches(ours, cps, {}).items()}, {10: 1, 11: 2})
        result = assign_matches(ours, cps, {11: None, 12: 2})
        self.assertNotIn(11, result)
        self.assertEqual(result[12], (2, 1.0, True))


if __name__ == "__main__":
    unittest.main()
