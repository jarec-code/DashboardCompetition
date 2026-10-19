# Analýza a plán: robustné načítavanie produktov z webov konkurencie

Stav: **návrh na schválenie** (19. 10. 2026). Zdrojový kód sa v tejto fáze nemenil.

Dokument oddeľuje **potvrdené zistenia** (✔ overené na produkčných dátach, na webe alebo na kóde) od
**predpokladov** (❓ treba overiť v implementácii).

---

## 1. Súčasný stav

### 1.1 Relevantné moduly

| Oblasť | Súbor / funkcia | Čo robí |
|---|---|---|
| Evidencia konkurentov | `models/competitors.py` (`Competitor`, `ScrapeConfig`) | doména, základná URL, parser, rozostup, timeout; `ScrapeConfig.discovery` (JSON) = nastavenia a stav objavovania |
| Sťahovanie | `scraping/fetcher.py` (`Fetcher`) | GET bez JavaScriptu, SSRF ochrana, robots.txt (`can_fetch`, `Crawl-delay`, `Sitemap`), limit veľkosti, rozpoznanie Cloudflare výzvy, 429/Retry-After |
| Parsovanie detailu | `scraping/parsers/structured.py` (`parse_auto`), `selectors.py` (CSS/XPath), `sites/alter_cz.py` | `auto` = iba JSON-LD → microdata → Open Graph; bez nich chyba |
| Objavovanie zdroja | `services/discovery.py`, trieda `Runner`, metódy `_detect_url` / `_detect_with` | adaptér webu → sitemapy z robots.txt → `/sitemap.xml` → domovská stránka → max. 3 „katalógové“ odkazy |
| Čítanie zdroja | `scraping/catalog.py`: `read_sitemap`, `read_feed`, `read_listing`, `ADAPTERS` | zoznam ide **iba cez `rel="next"`**, kategórie sa neprechádzajú |
| Rozpoznanie produktových odkazov | `scraping/discover.py`: `discover_links`, `_score`, `PRODUCT_PATH`, `TRAILING_ID` | skóre **iba podľa cesty URL** (`/produkt/`, `/p/`, číslo na konci), query sa ignoruje |
| Načítanie detailov | `Runner._enrich` | parser konkurenta na každú novú položku, bez ceny = „nie je produkt“ |
| Párovanie | `services/comparable.py` | porovnateľnosť (ročník, predmet, typ, diel) alebo ISBN/EAN |
| Výsledky a chyby | `models/discovery.py` (`CatalogItem`, `DiscoveryRun`), `DiscoveryRun.stats` | položky katalógu, beh ako stavový automat (`detect` → `list` → `enrich` → `done`) |
| Poistky | `Runner._request_failed`, `_reconcile` | 403/429/výzva → pauza 7 dní; 3 chyby za sebou → pauza; prudký pokles počtu → stop |
| UI | `frontend/src/pages/competitors/DiscoveryTab.tsx`, `UpdatesPage.tsx` | nastavenia, posledné behy, katalóg, prehľad v Aktualizáciách |

**Miesto, kde treba riešiť výber a kombinovanie metód ✔:** `services/discovery.py`, metódy
`Runner._detect_url` / `Runner._detect_with` (pevná postupnosť pokusov, prvý úspech vyhráva, pri neúspechu
celý beh skončí chybou) a `Runner._list_page` (jeden typ zdroja na beh). Druhé miesto je `scraping/discover.py`
(`_score`), ktoré rozhoduje, čo je produktový odkaz.

### 1.2 Obmedzenia súčasného riešenia (✔ zistené v kóde)

1. **Rozpoznanie produktovej URL iba podľa cesty.** Weby s adresami typu `index.php?zobraz=detail&id=306` majú
   skóre 0 a vôbec sa neuvažujú.
2. **Katalóg sa hľadá iba podľa textu odkazu a cesty.** Menu z obrázkov (bez textu) a parametre v query
   (`?zobraz=katalog`) sa prehliadnu.
3. **Zdroj musí byť jedna stránka s aspoň 12 produktovými odkazmi**, prípadne reťaz `rel="next"`. Kategórie,
   podkategórie ani série sa neprechádzajú.
4. **Žiadny záložný postup.** Neúspech detekcie znamená zlyhanie celého behu a hlásenie, ktoré vidíte.
5. **Parser `auto` vie iba štruktúrované dáta.** Bežné HTML s textom „Cena: 238 Kč“ nevie prečítať.
6. **Iba doména konkurenta.** Obchod na inej doméne (napr. SPN → naseucebnice.cz) je mimo dosahu.
7. **Sitemap bez triedenia URL.** Pri sitemape všetkých stránok sa načítavajú aj neprodukty, čo zbytočne míňa
   rozpočet a spúšťa ochrany webu (prípad Prodos).
8. **JavaScript sa nevykresľuje** (zámerne, etapa 10 nebola realizovaná).
9. **Front URL je JSON v riadku behu** (`DiscoveryRun.queue`, `done_urls`, `seen_keys`). Na stovky URL to stačí,
   na prehľadanie webu s tisíckami URL nie (veľkosť riadku, žiadna diagnostika po URL).
10. **Diagnostika je hrubá.** Nevidno navštívené, preskočené ani zlyhané URL, ani ktoré časti webu sa
    nespracovali. Stav behu nepozná „čiastočne dokončený“.

### 1.3 Limity prostredia

- ✔ Worker beží ako vlákno web procesu (`EMBEDDED_WORKER=true`), sekvenčne: jedna požiadavka na jeden krok, rozostup
  spoločný s kontrolami cien. Súbežnosť na jeden web je zámerne 1.
- ✔ Railway: jedna služba aplikácie a PostgreSQL 18. Pri nasadení bol zvolený Free plán (kredit $1 mesačne).
- ❓ Pamäť a CPU Free plánu stačia na HTTP prechádzanie, nie však na Chromium (Playwright ~300–500 MB RAM na
  prehliadač). Metóda D by potrebovala samostatnú službu a pravdepodobne platený plán.

---

## 2. Diagnostika konkurentov v produkcii (✔ z behov 19. 10. 2026)

| Konkurent | Výsledok | Príčina |
|---|---|---|
| Alter | ✔ funguje: 271 produktov, 17 spárovaných, 16 sledovaných | adaptér `alter_cz` |
| **Prometheus** | ✘ „Zdroj sa nepodarilo nájsť“ (2 behy, 2–3 požiadavky) | pozri kapitolu 3; navyše má nastavený **parser `alter_cz`**, ktorý tu nemôže fungovať |
| SPN | ✘ „Zdroj sa nepodarilo nájsť“ (7 požiadaviek) | ✔ kategórie `spn.cz/ucebnice/…` nemajú produktové odkazy ani ceny; ✔ stránka má 29 odkazov na `www.naseucebnice.cz`; ❓ obchod SPN je tam |
| Prodos | ✘ pozastavené 7 dní po HTTP 403 | ✔ sitemap so všetkými 3 051 URL webu; prvé 3 načítané stránky neboli produkty, potom 403; ❓ príčina 403 (WAF, User-Agent, rýchlosť) |
| Didaktis, NNS | zatiaľ bez behu (tieňový režim čaká na nočné okno) | — |

Záver: problém nie je len Prometheus. Súčasná detekcia pokrýva weby s produktovými cestami, sitemapou
produktov alebo adaptérom.

---

## 3. Analýza webu Prometheus (prometheus-nakl.cz)

Overené 19. 10. 2026 piatimi požiadavkami s rozostupom 10 s (robots.txt, /sitemap.xml, domovská stránka,
katalóg, jeden detail).

### 3.1 Potvrdené zistenia ✔

- **robots.txt:** `Allow: /`, `Crawl-Delay: 5`, `Request-rate: 18/1m` v noci (21:00–05:29) a `6/1m` cez deň.
  Bez `Sitemap`. `/sitemap.xml` → 404.
- **Technológia:** server vykresľuje HTML (PHP, `windows-1250`), **na domovskej stránke nie je ani jeden `<script>`**.
  Nie je to JavaScriptový web, prehliadač nie je potrebný.
- **Štruktúrované dáta:** žiadne (JSON-LD, microdata ani Open Graph s cenou), žiadny feed ani API.
- **URL schéma (všetko cez `index.php` s parametrami):**
  - katalóg: `index.php?zobraz=katalog`
  - série: `index.php?zobraz=id2&id2=N` (8 na stránke katalógu)
  - podsérie: `index.php?zobraz=id3&id3=N` (30 na stránke katalógu)
  - **detail produktu: `index.php?zobraz=detail&id_katalog=N`** (22 na stránke katalógu, 10 na detaile)
  - informačné stránky: `index.php?cast=N`, ďalej novinky, pripravujeme, objednávky
  - na vynechanie: `&tisk=ano` (tlač), `cast=N&zobrazpred=detail&id_katalog=N` (duplicitný detail),
    `zobraz=hledany_text&…` (vyhľadávanie), `/demoverze/*.pdf`
- **Menu sú obrázky bez textu**, slovo „katalog“ je iba v parametri.
- **Detail produktu** obsahuje v texte `Cena: 238 Kč`, `ISBN: 978-80-7196-523-7`, katalógové číslo (napr.
  `0621 414`), väzbu, počet strán a formát. Uvádza aj ceny súvisiacich titulov (`cena: 212 Kč`).
- **Stránka katalógu obsahuje ceny** (17× „N Kč“ pri 22 detailoch).
- **E-knihy sú na inej doméne:** `www.prometheus-eknihy.cz/products/show/N`, zoznamy `/products/list/N`.
- **Súčasný kód na uložených stránkach:**
  - `discover_links` nájde **0** produktových odkazov na domovskej stránke aj v katalógu,
  - `_catalogue_links` vráti **0** kandidátov,
  - parser `auto` aj `alter_cz` končia chybou `ParseError`.

### 3.2 Príčiny neúspechu ✔

1. Produktové URL sú v **query parametroch**, preto ich `discover.py/_score` hodnotí nulou.
2. Odkaz na katalóg je **obrázok bez textu** a „katalog“ je v query, preto ho `_catalogue_links` nenájde.
3. Produkty sú rozdelené do **sérií a podsérií**. Aj keby sa katalóg našiel, súčasné čítanie zoznamu prejde iba
   `rel="next"` a do sérií nevstúpi.
4. Detail nemá štruktúrované dáta a parser `auto` nevie čítať text „Cena: … Kč“.
5. Nesprávne nastavený parser `alter_cz` (konfigurácia, nie kód).

### 3.3 Navrhovaný postup pre Prometheus

Prehliadač nie je potrebný. Stačí riadené prechádzanie HTML (metóda B/C) a čítanie detailu podľa textových
popisov (metóda B):

1. **Vstup:** domovská stránka. Katalóg sa nájde cez odkazy, ktorých query obsahuje „katalog“, alebo cez text
   `alt`/`title` obrázka. Prípadne ho administrátor zadá ručne.
2. **Kategórie:** z katalógu a ďalších zoznamov prechádzať šablóny `zobraz=id2`, `zobraz=id3` (hĺbka ≤ 3).
   Ako zoznam ich rozpozná to, že vedú na stránky s produktovými odkazmi.
3. **Produktové URL:** šablóna `index.php?zobraz=detail&id_katalog={N}`, s normalizáciou (odstrániť `tisk`,
   `zobrazpred`, `PHPSESSID`) a deduplikáciou podľa `id_katalog`.
4. **Detaily:** heuristický parser (názov z `<h1>`/`<title>`, cena z „Cena: N Kč“ pri hlavnom produkte,
   ISBN, katalógové číslo) alebo pravidlá polí v konfigurácii webu.
5. **Ceny:** z detailu (spoľahlivé). Voliteľne neskôr zo stránok sérií, ak sa potvrdí väzba ceny na produkt.
6. **Rozsah:** ❓ počet titulov odhadujem na stovky. Zoznamy sú rádovo 40 stránok, k tomu jedna stránka
   na každý detail. Pri `Crawl-Delay 5`, nočnom limite 18/min a rozpočte 120 požiadaviek za noc trvá prvé úplné
   načítanie niekoľko nocí. Rozpočet pre tento web sa dá zvýšiť v rámci `Request-rate`.
7. **E-knihy** (`prometheus-eknihy.cz`) ako voliteľná druhá doména, ak sú pre porovnanie relevantné.
8. **Nespracované časti** (zlyhané alebo preskočené URL, šablóny bez produktov) sa evidujú a zobrazia.

Prometheus sa teda dá **spoľahlivo preskúmať bez ďalšieho prístupu** (✔ verejné HTML, robots.txt povoľuje).
Úplnosť závisí od toho, či sú všetky tituly dostupné cez katalóg a série (❓ overí sa porovnaním s PDF katalógom
2026, na ktorý web odkazuje).

---

## 4. Porovnanie metód

| Metóda | Spoľahlivosť | Úplnosť | Zložitosť | Rýchlosť / požiadavky | Prevádzka na Railway |
|---|---|---|---|---|---|
| **A** API / feed / sitemap / JSON-LD | vysoká | vysoká, ak zdroj existuje | nízka (väčšinou hotové) | 1–50 požiadaviek | bez zmeny |
| **B** cielené čítanie HTML zoznamov a detailov | stredná až vysoká | vysoká, ak navigácia pokrýva katalóg | stredná | zoznamy + 1 na detail | bez zmeny |
| **C** riadené prehľadávanie webu (crawler) | stredná | najvyššia dostupná bez JS; nezaručuje produkty mimo navigácie | stredná až vysoká | stovky až tisíce; nutné limity | bez zmeny, front v DB |
| **D** prehliadač (Playwright) | stredná (krehkejšie, pomalšie) | rieši JS obsah | vysoká | 5–20× pomalšie na stránku | samostatná služba, ❓ platený plán (RAM) |
| **E** kombinácia + ručný „recept“ webu | najvyššia pri problémových weboch | podľa receptu | nízka na kód, vyžaduje človeka | podľa receptu | bez zmeny |

Odporúčané poradie: **A → B → C → D**, pričom E (recept) môže ktorúkoľvek metódu upresniť alebo nahradiť.

---

## 5. Odporúčaná architektúra

### 5.1 Princíp: stratégie s hodnotením namiesto pevnej postupnosti

```
                        ┌──────────────── recept webu (voliteľný, ručný): domény, vstupy, vzory, polia ─────────┐
Diagnostika (raz, potom pri poruche alebo raz mesačne)                                                      │
  sondy: robots.txt → feed/API (ak odkázané) → sitemap → JSON-LD ItemList → domovská stránka (+odkazy)       ▼
  → pre každú stratégiu: skúšobný beh na vzorke (≤ 15 požiadaviek) → hodnotenie kvality → poradie stratégií
Beh objavovania
  1. stratégia s najlepším hodnotením (napr. sitemap, crawler, adaptér)
  2. ak výsledok nestačí (kap. 6), ďalšia stratégia a jej výsledky sa zlúčia (deduplikácia podľa kľúča produktu)
  3. načítanie detailov: parser štruktúrovaných dát → heuristický parser → pravidlá z receptu
  4. vyhodnotenie úplnosti, uloženie diagnózy, stav: dokončený / čiastočne / neúspešný
```

### 5.2 Spoločné stavebné prvky

- **Normalizátor URL:** odstráni fragmenty, session ID (`PHPSESSID`, `sid`) a sledovacie parametre (`utm_*`, `fbclid`)
  a zoradí parametre. Z receptu alebo diagnózy navyše odstráni nepodstatné parametre (`tisk`, `zobrazpred`, `sort`).
  Je to základ deduplikácie a ochrany pred cyklami.
- **Šablóny URL:** URL sa zovšeobecní (čísla → `{N}`, hodnoty identifikátorov v query → `{N}`), napr.
  `index.php?zobraz=detail&id_katalog={N}`. Pre každú šablónu sa počíta výnos: koľko vzoriek bolo produktov,
  zoznamov a koľko nič. Klasifikácia sa tým učí z výsledkov a nehádže sa podľa slov v ceste.
- **Klasifikátor stránky** (bez AI): produkt = cena + názov (zo štruktúrovaných dát alebo heuristiky); zoznam =
  viac odkazov jednej produktovej šablóny; kategória = odkazy na zoznamy; inak informačná stránka.
- **Front v databáze** (nová tabuľka): navštívené a čakajúce URL, hĺbka, šablóna, stav, chyba. Beh sa dá
  prerušiť a obnoviť a je z neho vidno, čo sa nespracovalo.
- **Heuristický parser detailu:** názov (`h1`, `og:title`, `<title>`); cena z textu pri návestí („Cena“, „Cena s
  DPH“, „Naše cena“, mena Kč/€). Ak je na stránke viac cien, rozhoduje najbližšia k nadpisu alebo návestiu,
  akciová a pôvodná cena podľa prečiarknutia. Ďalej ISBN/EAN, dostupnosť podľa slov a výsledok s mierou istoty.
- **Rešpektovanie pravidiel webu:** `Crawl-delay` už je, pribudne `Request-rate` (podľa času). Tvrdé limity:
  stránky na beh, hĺbka, čas behu, rozpočet za noc. Súbežnosť ostáva 1.

### 5.3 Metóda D (prehliadač), iba ak je to nevyhnutné

- Spúšťa sa iba vtedy, keď diagnostika potvrdí, že HTML neobsahuje produkty ani odkazy, ktoré vidno po
  vykreslení. Napríklad prázdne kontajnery a veľa skriptov, pričom vzorka po vykreslení má produkty.
- Najprv sa skúsi lacnejšia cesta: nájsť JSON/XHR endpoint, ktorý stránka volá (často verejné API zoznamu).
- Samostatná služba `worker-browser` (vlastný Docker image s Chromiom) spracúva iba úlohy typu „vykresli
  stránku“. Súbežnosť 1, bez obrázkov a fontov. Klikanie na „Načítať ďalšie“ a stránkovanie podľa receptu.
- Nikdy neobchádza CAPTCHA ani ochranu proti botom.

### 5.4 Recept webu (ručná konfigurácia, metóda E)

Uložený v `ScrapeConfig.discovery` (JSON), editovateľný v záložke Katalóg konkurenta:
- povolené domény (napr. `naseucebnice.cz`, `prometheus-eknihy.cz`),
- vstupné URL,
- vzory URL: zahrnúť, vylúčiť, produktová šablóna,
- parametre na odstránenie,
- maximálna hĺbka, stránky a čas,
- pravidlá polí detailu (CSS/XPath alebo návestie typu „Cena:“),
- zdroj ceny (detail/zoznam),
- vynútená stratégia.

---

## 6. Automatický výber metódy a hodnotenie kvality

**Metriky výsledku** (počítajú sa pre každú stratégiu a každý beh):
- `products_found`: počet rôznych produktových kľúčov po deduplikácii,
- `priced_share`: podiel produktov s cenou a menou zo vzorky alebo zo všetkých načítaných,
- `title_share`, `identifier_share` (ISBN/EAN),
- `plausible_share`: cena v rozumnom rozsahu (napr. 1–20 000 Kč) a nie je rovnaká pre všetky produkty
  (odhalí chybný selektor),
- `coverage`: pomer nájdených produktov k odhadu (počty v zoznamoch, údaje „N produktov“, predchádzajúce behy),
- `waste`: podiel požiadaviek, ktoré neviedli k produktu ani zoznamu.

**Kedy je výsledok dostatočný** (predvolené hodnoty, nastaviteľné):
- `products_found ≥ 10` (alebo ≥ 50 % predchádzajúceho úspešného behu),
- `priced_share ≥ 0,8` na vzorke najmenej 10 detailov,
- `title_share ≥ 0,95`,
- `plausible_share ≥ 0,9`.

**Rozhodovanie:**
1. Zoradiť stratégie podľa ceny: recept → feed/API → sitemap → štruktúrovaný zoznam → crawler → prehliadač.
2. Každú najprv vyskúšať na vzorke (≤ 15 požiadaviek). Nedostatočné hodnotenie neznamená zlyhanie behu, iba
   prechod na ďalšiu stratégiu.
3. Ak žiadna stratégia sama nestačí, výsledky sa zlúčia a beh sa označí ako **čiastočne dokončený**
   s vysvetlením (napr. „cena sa našla iba pri 40 % produktov“).
4. **Diagnóza sa uloží** (zvolená stratégia, hodnotenie všetkých skúšaných, dôvody) a ďalší beh začne zvolenou
   stratégiou. Diagnostika sa zopakuje pri prudkom poklese kvality, po zmene receptu alebo raz za 30 dní.

---

## 7. Dátový model (iba potrebné zmeny)

Pôvodná požiadavka → súčasný stav:

| Požiadavka | Súčasný stav | Návrh |
|---|---|---|
| zdrojový web, konkurent | ✔ `CatalogItem.competitor_id` | — |
| URL produktu | ✔ `CatalogItem.url`, `key` | — |
| kanonická URL | ✘ | `canonical_url` (z `<link rel=canonical>`) |
| názov, cena, mena | ✔ `title`, `price`, `currency` | — |
| identifikátory | ✔ `identifiers` (JSON) | — |
| kategória | čiastočne (`hints.category`) | `category` (text) pre filtrovanie a diagnostiku |
| čas posledného načítania | čiastočne (`enriched_at`, `last_seen_at`) | `fetched_at` (posledné načítanie detailu) |
| použitá metóda | ✘ | `found_by` (sitemap / feed / listing / crawl / browser / adapter), `parsed_by` |
| stav spracovania a chyba | čiastočne (`enrich_error`, `match_status`) | `fetch_status` (ok / no_price / not_product / failed / skipped) |
| úplnosť údajov | ✘ | `completeness` (0–100: názov, cena, mena, identifikátor, kategória) |

Ďalšie zmeny:
- **Nová tabuľka `discovery_urls`** (front a navštívené URL): konkurent, beh, normalizovaná URL (unikátna
  v rámci konkurenta), šablóna, hĺbka, druh (product / listing / category / info / skip), stav (queued / done /
  failed / skipped), HTTP stav, počet nájdených odkazov a produktov, chyba, čas. Nahradí JSON polia `queue`,
  `done_urls`, `seen_keys` v behu. Tie ostanú kvôli kompatibilite a postupne sa prestanú používať.
- **`DiscoveryRun.status`:** pridať hodnotu `partial` (úprava CHECK obmedzenia) a do `stats` štatistiky
  stratégií (bez zmeny schémy).
- **Recept a diagnóza** v `ScrapeConfig.discovery` (JSON, bez migrácie).

Všetko sú pridané stĺpce a jedna nová tabuľka, existujúce dáta a API ostávajú kompatibilné.

---

## 8. Monitoring a diagnostika v existujúcom UI

V záložke **Katalóg konkurenta** (bez novej obrazovky):

- **Diagnóza zdroja:** tabuľka stratégií (skúsená / zvolená / nevhodná), pri každej hodnotenie (nájdené,
  s cenou, s názvom, vierohodné ceny), dôvod a čas. Tlačidlo „Znova diagnostikovať“.
- **Posledné behy:** pribudnú stĺpce:
  - navštívené stránky,
  - preskočené a zlyhané URL,
  - produkty s cenou,
  - použitá stratégia,
  - stav „čiastočne“ s vysvetlením.
- **Nespracované časti:** zoskupenie podľa šablóny URL a dôvodu, napr.
  „`index.php?zobraz=id3&id3={N}` – 3 zlyhali (timeout)“ alebo „`/eknihy/*` – mimo povolených domén (12)“.
- **Položky katalógu:** filtre „bez ceny“, „neúplné“ a stĺpec úplnosti.
- **Aktualizácie a logy:** existujúci prehľad objavovania doplniť o stav „čiastočne“ a počet produktov bez ceny.

---

## 9. Plán implementácie po etapách

| # | Etapa | Obsah | Závisí od | Odhad |
|---|---|---|---|---|
| 1 | **Základ: URL, klasifikácia, heuristický parser, diagnostika** | normalizátor URL; šablóny URL s výnosom; produktové odkazy aj z query (`_score` + šablóny); katalógové odkazy aj z `alt`/`title`/query; heuristický parser detailu ako posledný krok `auto`; `Request-rate` z robots.txt; nové stĺpce položky (`found_by`, `fetch_status`, `completeness`, `canonical_url`, `category`, `fetched_at`); stav `partial`; čitateľnejšie chyby | — | 2–3 dni |
| 2 | **Riadený crawler (metóda B/C)** | tabuľka `discovery_urls`; BFS s prioritou podľa výnosu šablón; hĺbka, stránky, čas, rozpočet; vstup do kategórií a sérií; deduplikácia; obnova po prerušení; „nespracované časti“ | 1 | 3–4 dni |
| 3 | **Výber a kombinovanie stratégií** | sondy a skúšobné behy, metriky kvality, prechod na ďalšiu stratégiu namiesto zlyhania, zlúčenie výsledkov, uloženie diagnózy, opakovaná diagnostika; triedenie sitemapy podľa šablón a vzorkovanie (Prodos) | 1, 2 | 2–3 dni |
| 4 | **Recept webu v UI (metóda E)** | povolené domény (SPN → naseucebnice.cz, Prometheus e-knihy), vstupy, vzory, odstránenie parametrov, pravidlá polí, zdroj ceny; panel diagnózy a nespracovaných častí | 1–3 | 2 dni |
| 5 | **Prehliadač (metóda D), voliteľne** | najprv hľadanie JSON/XHR endpointov; potom služba `worker-browser` s Playwrightom pre úlohy „vykresli“; náklady a limity; iba pre weby, kde diagnostika potvrdí potrebu | 3 | 3–5 dní + infraštruktúra |

Etapy 1–4 bežia v súčasnom prostredí bez nových služieb. Etapa 5 sa spustí iba pri konkrétnom webe, ktorý
ju potrebuje.

---

## 10. Testovací plán

**Automatické testy (bez reálnych webov):**
- Uložené stránky Prometheus ako fixtures (domovská, katalóg, séria `id2`, podséria `id3`, 2 detaily; bez
  osobných údajov), falošný web v testoch:
  - nájdenie katalógu cez obrázkový odkaz,
  - prechod sériami,
  - rozpoznanie šablóny detailu,
  - normalizácia (`tisk`, `zobrazpred`, `PHPSESSID`),
  - heuristická cena a ISBN,
  - opakovaný beh bez duplicít.
- Regresia na fungujúcich weboch: alter.cz (adaptér, 271 položiek zo zoznamu, rovnaké páry), syntetická
  sitemap s JSON-LD, zoznam s `rel=next`. Existujúce testy objavovania musia prejsť bez zmeny očakávaní.
- Crawler:
  - cykly,
  - limity (stránky, hĺbka, čas, rozpočet),
  - `Crawl-delay` a `Request-rate`,
  - obnova po prerušení,
  - 403/429 pauza,
  - zlúčenie dvoch stratégií,
  - stav „čiastočne“.
- Sitemap so stovkami neproduktov: vzorkovanie šablón namiesto načítania všetkého.
- E2E: diagnóza a beh na falošnom webe so štruktúrou Promethea v prehliadači.

**Overenie v prevádzke (tieňový režim, nič sa nesleduje):**
- **Prometheus:** porovnať počet nájdených titulov s katalógom na webe (série) a PDF katalógom 2026; podiel
  s cenou; počet požiadaviek a dĺžka behu; robots.txt a nočný limit 18/min.
- **Alter:** výsledky zhodné so súčasným stavom (271 položiek, rovnaké páry), denné ceny zo zoznamu bez zmeny.
- **SPN, Prodos:** diagnóza musí vysvetliť stav (iná doména obchodu, 403) a navrhnúť recept, nesmie hlásiť
  všeobecnú chybu.

---

## 11. Riziká a limity

- **Blokovanie:** WAF alebo ochrana proti botom (Prodos 403). Zmierňuje to dodržiavanie `Crawl-delay`
  a `Request-rate`, vzorkovanie namiesto plošného čítania a pauza po 403/429. Ochrany sa neobchádzajú.
- **Úplnosť:** prehľadanie odkazov nezaručuje všetky produkty (tituly mimo navigácie, iba vo vyhľadávaní alebo
  PDF). Rieši to jasné označenie „čiastočne“, odhad pokrytia a recept so vstupmi.
- **Duplicity:** rovnaký produkt pod viacerými URL (tlač, parametre, kategórie). Rieši to normalizácia, kanonická
  URL a kľúč podľa šablóny (`id_katalog`).
- **Chybné ceny z heuristiky** (cena súvisiaceho titulu): kontrola vierohodnosti, cena pri nadpise alebo návestí,
  miera istoty a pri pochybnosti „na kontrolu“ namiesto automatického sledovania.
- **Výkon a náklady:** sekvenčné spracovanie s rozostupom je pomalé, ale lacné. Prvé načítanie väčšieho webu trvá
  niekoľko nocí. Front v DB rastie, preto upratovanie starých záznamov (housekeeping).
- **Dynamické weby:** bez etapy 5 nespracovateľné. Prehliadač zvyšuje nároky (❓ platený plán).
- **Zmena štruktúry webu:** existujúca ochrana pri prudkom poklese plus opakovaná diagnostika.
- **Právne:** potvrdenie podmienok webu pri zapnutí ostáva povinné. Pri ďalšej doméne (e-knihy, naseucebnice.cz)
  platí potvrdenie aj pre ňu.

---

## 12. Akceptačné kritériá

1. **Regresia:**
   - všetky existujúce testy prechádzajú,
   - Alter v tieňovom behu nájde 271 položiek (±2 % podľa aktuálneho webu) s rovnakými automatickými pármi,
   - denné ceny zo zoznamu fungujú ďalej.
2. **Žiadne zlyhanie pre jednu metódu:** neúspech jednej stratégie vedie k ďalšej a beh končí stavom „čiastočne“
   s vysvetlením, nie chybou detekcie. Hlásenie „Zdroj sa nepodarilo nájsť“ sa zobrazí iba vtedy, keď zlyhajú
   všetky stratégie, a spolu s diagnózou každej z nich.
3. **Prometheus bez feedu a bez ručnej konfigurácie:**
   - nájde katalóg a série,
   - aspoň 90 % titulov dostupných cez série,
   - aspoň 90 % nájdených titulov s cenou,
   - pri vzorke 20 titulov sa cena zhoduje s webom.
4. **Crawler:** prejde relevantné zoznamy a detaily, rešpektuje limity stránok, hĺbky, času a rozpočtu,
   `Crawl-delay` a `Request-rate`, nenavštívi jednu URL dvakrát v jednom behu a nezacyklí sa.
5. **Idempotencia:** opakovaný beh nevytvorí duplicitné položky ani ponuky (kľúč podľa normalizovanej URL alebo šablóny).
6. **Diagnostika:** pri každom behu vidno navštívené stránky, nájdené produkty, produkty s cenou, preskočené
   a zlyhané URL (zoskupené podľa šablóny a dôvodu), použitú stratégiu a stav.
7. **Neúplné výsledky** majú stav „čiastočne“, mieru úplnosti a dôvod.
8. **Šetrnosť:** súbežnosť 1 na web, nočné okno, rozpočet, pauza pri 403/429. Načítavanie nespomalí kontroly cien
   ostatných konkurentov o viac ako rozostup jednej požiadavky.
9. **Prostredie:** etapy 1–4 bežia v súčasnom nasadení bez novej služby a bez prekročenia pamäte. Etapa 5 má
   samostatný odhad nákladov pred zapnutím.
10. **SPN a Prodos:** diagnóza jasne uvedie príčinu (iná doména obchodu, 403) a navrhne recept alebo ďalší krok.

---

## 13. Odporúčanie: čím začať

**Začať etapou 1 a hneď na ňu nadviazať etapou 2.** Spolu dávajú prvý merateľný výsledok: funkčný Prometheus.

Dôvody:
- Etapa 1 odstraňuje potvrdené príčiny zlyhania. Bez nej by crawler nevedel rozpoznať produkty ani ceny:
  - URL v query,
  - obrázkové odkazy,
  - parser bez štruktúrovaných dát,
  - chýbajúca diagnostika.
- Etapa 1 navyše hneď zlepší sitemapy (Prodos: menej zbytočných požiadaviek) a čitateľnosť chýb pre všetkých.
- Prometheus je server-side HTML (✔ bez skriptov), preto **prehliadač nie je potrebný**. Riadený crawler
  z etapy 2 stačí, a to v súčasnom prostredí a bez nákladov navyše.
- Výber a kombinovanie stratégií (etapa 3) má zmysel až keď existuje viac kvalitných stratégií na porovnanie.

Očakávané výsledky po etapách 1 a 2:
- ✔ istota: produktové URL Promethea sa rozpoznajú (šablóna `zobraz=detail&id_katalog`) a ceny sa prečítajú
  z textu detailu,
- ❓ predpoklad: väčšina titulov je dostupná cez série v katalógu (overí sa porovnaním s PDF katalógom),
- prvé úplné načítanie Promethea trvá niekoľko nocí pri súčasnom rozpočte. Rozpočet sa dá v rámci jeho
  `Request-rate` zvýšiť,
- SPN a Prodos budú mať po etape 1 jasnú diagnózu. Funkčné budú po etapách 3–4 (recept s doménou obchodu,
  vzorkovanie sitemapy).

**Okamžitý krok bez vývoja:** pri Prometheovi zmeniť v Nastaveniach scrapingu parser z `alter_cz` na
„Automaticky“. Je to chyba konfigurácie, ktorá by inak znehodnotila aj budúce riešenie.
