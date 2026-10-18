# Parser pre nový web

Návod pre vývojára: ako naučiť aplikáciu čítať ceny z nového e-shopu bez zásahu do ostatných
parserov a do globálnej logiky.

## 1. Najprv skúste konfiguráciu bez kódu

V detaile konkurenta → **Nastavenia scrapingu** → **Testovacia kontrola** vyskúšajte v tomto poradí:

1. **Automaticky** (`auto`): JSON-LD → microdata → Open Graph. Väčšina e-shopov publikuje štruktúrované dáta pre vyhľadávače.
2. **CSS selektory** (`css`) alebo **XPath** (`xpath`): ak web štruktúrované dáta nemá. Povinná je iba cena; ďalej názov, pôvodná cena, mena, dostupnosť a EAN/ISBN/SKU. Selektory sú dáta, nie kód, a pri uložení sa kontrolujú.

Vlastný parser píšte iba vtedy, keď to nestačí: napríklad web má viac variantov s rôznou cenou alebo EAN je len v texte popisu (ako alter.cz).

Nikdy neobchádzajte prihlásenie, CAPTCHA ani ochranu proti botom. Ak ich web vyžaduje, označte ho ako nepodporovaný.

## 2. Uložte vzorky stránok

Stiahnite 2–3 reprezentatívne produktové stránky raz, s identifikovateľným User-Agentom a s rozostupom:

```bash
mkdir -p backend/tests/fixtures/html/novy_web
curl -sL -A "CompetitionChecker/1.0 (price monitoring)" -o backend/tests/fixtures/html/novy_web/produkt_1.html https://www.novy-web.cz/produkt/1
```

Testy pracujú iba s týmito súbormi a reálny web nezaťažujú.

## 3. Napíšte modul parsera

`backend/app/scraping/sites/novy_web.py` (vzor: `alter_cz.py`):

```python
from bs4 import BeautifulSoup

from app.scraping.normalize import detect_currency, find_identifiers, normalize_availability, parse_price
from app.scraping.parsers.base import Extracted, PageContext, ParseError

VERSION = 1  # zvýšte pri každej zmene logiky parsera


def parse(ctx: PageContext) -> Extracted:
    soup = BeautifulSoup(ctx.html, "html.parser")
    price_el = soup.select_one(".product-price")
    if price_el is None:
        raise ParseError("Cena sa nenašla (.product-price). Web mohol zmeniť štruktúru.")
    price_text = price_el.get_text(" ", strip=True)
    return Extracted(
        price=parse_price(price_text),
        currency=detect_currency(price_text),
        availability=normalize_availability(soup.select_one(".stock") and soup.select_one(".stock").get_text()),
        title=soup.select_one("h1").get_text(" ", strip=True),
        identifiers=find_identifiers(soup.get_text(" ")),
        source="novy_web",
    )
```

Pravidlá:
- **Neznámu hodnotu nevymýšľajte.** Dostupnosť `unknown`, DPH `unknown` a chýbajúci identifikátor sú v poriadku.
- **Keď chýba cena, vyhoďte `ParseError`.** Kontrola sa zaznamená ako chyba a posledná platná cena zostane.
- `ctx.variant` je variant ponuky (prázdny = predvolený); `ctx.config` sú nastavenia parsera (ak nejaké má).
- Parser iba číta HTML; sťahovanie, robots.txt, limity a ukladanie rieši spoločný kód.

## 4. Zaregistrujte parser

V `backend/app/scraping/parsers/registry.py` pridajte jeden riadok do `_PARSERS`:

```python
ParserSpec("novy_web", "novy-web.cz", "Parser pre www.novy-web.cz: …", novy_web.VERSION, novy_web.parse),
```

Parser sa potom automaticky zobrazí vo výbere v UI (`GET /api/parsers`). Ak potrebuje nastavenia, doplňte Pydantic model ako `config_model`; nastavenia sa pri uložení validujú ním.

## 5. Otestujte

`backend/tests/unit/test_scraping.py` (vzor: testy `test_alter_*`):

```python
def test_novy_web():
    data = parse("novy_web", "novy_web/produkt_1.html")
    assert (data.price, data.currency) == (Decimal("129.00"), "CZK")
```

Otestujte aj prípad, keď sa štruktúra zmení: `parse("novy_web", "generic/no_data.html")` musí vyhodiť `ParseError`.

```bash
cd backend && .venv/bin/pytest tests/unit/test_scraping.py
```

Potom v UI nastavte konkurentovi nový parser a spustite **Testovacia kontrola** na skutočnej stránke.

## 6. Keď web zmení štruktúru

- Kontroly začnú zlyhávať s chybou parsera. Ceny zostanú posledné platné a po čase sa označia ako neaktuálne.
- Uložte novú vzorku stránky, upravte parser, zvýšte `VERSION` a doplňte test na novú vzorku.
- Výsledok každej kontroly obsahuje verziu parsera (`alter_cz@1`), takže sa dá dohľadať, ktorá verzia ktorú cenu vyčítala.
