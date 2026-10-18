"""All parsers available to scrape configurations.

Adding a parser for a new website: write a module in app/scraping/sites/ with a `parse(ctx)`
function and a VERSION, then register it below. Other parsers and the global logic stay untouched
(see docs/SCRAPER_GUIDE.md).
"""
from typing import Any

from app.scraping.parsers import selectors, structured
from app.scraping.parsers.base import ParserSpec
from app.scraping.sites import alter_cz

_PARSERS = [
    ParserSpec("auto", "Automaticky", "Skúsi JSON-LD, microdata a Open Graph v tomto poradí.", structured.VERSION,
               structured.parse_auto),
    ParserSpec("jsonld", "JSON-LD", "Štruktúrované dáta schema.org v značke <script type=\"application/ld+json\">.",
               structured.VERSION, structured.parse_jsonld),
    ParserSpec("microdata", "Microdata", "Atribúty itemprop podľa schema.org.", structured.VERSION,
               structured.parse_microdata),
    ParserSpec("opengraph", "Open Graph", "Meta značky product:price:amount.", structured.VERSION,
               structured.parse_opengraph),
    ParserSpec("css", "CSS selektory", "Vlastné CSS selektory pre stránky bez štruktúrovaných dát.",
               selectors.VERSION, selectors.parse_css, selectors.CssSelectors, "css"),
    ParserSpec("xpath", "XPath", "Vlastné XPath výrazy pre stránky bez štruktúrovaných dát.",
               selectors.VERSION, selectors.parse_xpath, selectors.XPathExpressions, "xpath"),
    ParserSpec("alter_cz", "alter.cz", "Parser pre www.alter.cz: názov, cena podľa variantu (predvolene tištěná), "
               "dostupnosť a EAN.", alter_cz.VERSION, alter_cz.parse),
]
PARSERS: dict[str, ParserSpec] = {p.name: p for p in _PARSERS}


def get_parser(name: str) -> ParserSpec:
    try:
        return PARSERS[name]
    except KeyError:
        raise ValueError(f"Neznámy typ parsera: {name}") from None


def validate_parser_config(name: str, config: dict[str, Any]) -> dict[str, Any]:
    spec = get_parser(name)
    if spec.config_model is None:
        if config:
            raise ValueError(f"Parser '{name}' nemá žiadne nastavenia.")
        return {}
    return spec.config_model.model_validate(config).model_dump(exclude_none=True)
