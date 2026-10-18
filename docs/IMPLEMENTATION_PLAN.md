# Implementačný plán: sledovanie cien konkurencie

Stav k 18. 10. 2026. Plán vychádza zo zadania „Aplikácia na sledovanie cien konkurencie“
a z analýzy repozitára `jarec-code/DashboardCompetition` (commit `0922068`).

Každá etapa končí funkčným, otestovaným a nasaditeľným stavom. Ďalšia etapa začína až po
splnení podmienok „Hotové, keď“ predchádzajúcej etapy.

---

## 1. Analýza existujúceho projektu

### 1.1 Čo existuje

| Oblasť | Súčasný stav | Rozsah |
|---|---|---|
| Backend | Flask, server-side Jinja šablóny, `app.py` | ~300 riadkov |
| Databáza | SQLite (`db.py`), 4 tabuľky, bez migrácií | ~70 riadkov |
| Scraping | `requests` + BeautifulSoup (`scraper.py`): JSON-LD, microdata, Open Graph, CSS selektory, `robots.txt` | ~230 riadkov |
| Párovanie | Fuzzy zhoda názvov (`matcher.py`), manuálne prepojenia | ~50 riadkov |
| UI | 4 Jinja šablóny + 1 CSS súbor | ~300 riadkov |
| Testy | 7 unit testov (`unittest`): ceny, extrakcia, párovanie | ~80 riadkov |
| Nasadenie | Railway, gunicorn (`gunicorn.conf.py`, `railway.json`), Python 3.12 | funguje (HTTP 200) |
| Dáta | 5 vlastných produktov z etaktik.cz (CZK), lokálne v SQLite | — |

### 1.2 Čo zachovať a preniesť

- **`parse_price`**: otestovaná normalizácia formátov `1 299,90 €`, `1.299,- Kč`, `$1,299.99`. Prenesie sa s prechodom na `Decimal`.
- **`detect_currency`**, **extraktory JSON-LD, microdata, Open Graph a CSS**: stanú sa prvými parsermi v registri.
- **`similarity` / `normalize`** z `matcher.py`: druhý krok párovania (po ISBN/EAN/SKU).
- **Existujúce testy a HTML vzorky**: prenesú sa do `pytest`.
- **Poznatky z Railway**: väzba na `0.0.0.0:$PORT`, port domény 8080, nastavenie Source/Branch pre automatický deploy.
- **Selektory pre etaktik.cz** (`.product_list_slider a.item`, `.title`, `.price .piece:not(.package) strong`): poslúžia ako testovacia vzorka a zdroj importu.

### 1.3 Čo nahradiť a prečo

| Súčasné | Nové | Dôvod |
|---|---|---|
| Flask | FastAPI | Požiadavka zadania; typované API, automatická validácia cez Pydantic. Súčasný backend je malý, prepis je lacný. |
| SQLite | PostgreSQL + SQLAlchemy 2 + Alembic | Požiadavka zadania; potrebné pre frontu úloh (`FOR UPDATE SKIP LOCKED`), súbežný prístup web/worker a migrácie. |
| `requests` | `httpx` | Požiadavka zadania; jemná kontrola presmerovaní, streamovanie s limitom veľkosti, async. |
| Jinja šablóny | React + TypeScript (Vite) | Súčasné UI nepoužíva frontendový framework, zadanie v takom prípade určuje React + TS. |
| `float` ceny | `NUMERIC(12,2)` / `Decimal` | Presné porovnávanie cien a zmien bez chýb zaokrúhlenia. |

### 1.4 Nedostatky súčasného riešenia (riešia ich etapy nižšie)

1. **Aplikácia na Railway je verejná a bez prihlásenia**: ktokoľvek s URL môže meniť dáta (etapa 0 alebo 1).
2. Scraping beží priamo v HTTP požiadavke používateľa (zakázané zadaním) → fronta úloh a worker (etapy 4–5).
3. Žiadna cenová história, ukladá sa iba posledný stav → `current_prices` + `price_history` (etapa 4).
4. Chýba ochrana pred SSRF, limit veľkosti odpovede a kontrola presmerovaní (etapa 4).
5. Pri neúspešnom scrapingu sa produkty konkurenta zmažú a nahradia → nesmie prepisovať poslednú platnú cenu (etapa 4).
6. Model je založený na stránkach kategórií (veľa produktov na URL); zadanie požaduje sledovanie jednotlivých produktových URL. Extrakcia z kategórie zostane ako pomocník importu (etapa 8).
7. `robots.txt` sa sťahuje pri každej požiadavke → cache na doménu (etapa 5).

---

## 2. Cieľová architektúra

### 2.1 Princípy

- Jeden repozitár, jeden Docker image, jeden kód. Procesy sa líšia iba príkazom na spustenie.
- Stav (fronta úloh, plánovanie, výsledky) je výhradne v PostgreSQL. Žiadna in-memory fronta, žiadny Redis.
- Bežná prevádzka nepoužíva AI. AI je voliteľný modul, predvolene vypnutý (etapa 11).
- Scraping nikdy neprebieha v HTTP požiadavke. UI zaradí úlohu do fronty a sleduje jej stav.

### 2.2 Procesy a služby na Railway

```
                ┌────────────────────────────┐
  prehliadač ──▶│ web  (uvicorn, FastAPI)     │── servíruje React build + /api
                │  • preDeploy: alembic upgrade│
                └──────────────┬─────────────┘
                               │ SQL
                ┌──────────────▼─────────────┐
                │ PostgreSQL (Railway)        │  fronta úloh, ceny, história
                └──────────────▲─────────────┘
                               │ SQL
                ┌──────────────┴─────────────┐
                │ worker (python -m app.worker)│
                │  • slučka schedulera (1/min,│
                │    pg advisory lock)        │
                │  • spracovanie úloh v dávkach│
                └────────────────────────────┘
```

**Odporúčanie:** 3 služby (web, worker, PostgreSQL). Scheduler beží ako slučka vo worker procese
a advisory lock zaručí, že pri viacerých workeroch plánuje vždy len jeden. Samostatný
`python -m app.scheduler` existuje tiež, takže ho neskôr možno oddeliť do vlastnej služby alebo
Railway cron úlohy bez zmeny kódu.

**Lacnejší variant** (rozhodnutie R2): worker ako Railway cron služba spúšťaná každých 15 minút,
ktorá spracuje dávku a skončí. Nevýhoda: manuálna testovacia kontrola čaká až do ďalšieho spustenia.

### 2.3 Štruktúra repozitára

```
backend/
  app/
    main.py              FastAPI aplikácia, /api, /healthz, servírovanie frontend/dist
    config.py            pydantic-settings (DATABASE_URL, SECRET_KEY, limity…)
    db.py                engine, session, transakcie
    models/              SQLAlchemy modely
    schemas/             Pydantic schémy (vstupy, výstupy, parser_config)
    api/                 routery: auth, competitors, products, offers, prices,
                         jobs, runs, dashboard, alerts, imports, settings
    services/
      pricing.py         record_observation(): current_prices + price_history
      matching.py        ISBN/EAN/SKU → názov → pending_review
      scheduling.py      výpočet intervalov, next_check_at
      alerts.py          vyhodnocovanie pravidiel
      imports.py         náhľad, validácia, potvrdenie importu
    scraping/
      urlsafety.py       validácia URL, SSRF ochrana
      fetcher.py         httpx: timeouty, limity, presmerovania, ETag, 429
      normalize.py       cena, mena, dostupnosť, DPH, ISBN/EAN
      parsers/           base.py, registry.py, jsonld.py, microdata.py,
                         opengraph.py, css.py, xpath.py, feed_xml.py
      sites/             parsery pre konkrétne weby (kód od vývojára, nie od používateľa)
    worker/
      queue.py           claim / complete / fail / recover úloh
      worker.py          hlavná slučka workera
      scheduler.py       výber splatných ponúk, vytvorenie úloh
    cli.py               create-admin, enqueue, run-once
  alembic/
  tests/
    unit/  integration/  fixtures/html/<web>/
frontend/                Vite + React + TypeScript
docs/
  IMPLEMENTATION_PLAN.md
  SCRAPER_GUIDE.md       ako pridať parser pre nový web
Dockerfile               multi-stage: node build → python runtime
railway/
  web.json  worker.json  config-as-code pre jednotlivé služby
.env.example
```

### 2.4 Technológie

| Vrstva | Voľba |
|---|---|
| Backend | Python 3.12, FastAPI, uvicorn, SQLAlchemy 2, Alembic, Pydantic 2, psycopg 3 |
| Scraping | httpx, BeautifulSoup4, lxml (XPath), defusedxml (feedy) |
| Autentifikácia | session cookie (httpOnly, SameSite=Lax) + CSRF token, heslá cez argon2 |
| Frontend | React 18, TypeScript, Vite, React Router, TanStack Query, TanStack Table, Mantine (komponenty), Recharts (grafy) |
| Testy | pytest, respx (mock HTTP), PostgreSQL v teste; Vitest; Playwright pre e2e testy UI (iba CI, nie v produkčnom image) |
| CI | GitHub Actions s PostgreSQL službou |
| Nasadenie | Docker, Railway (web, worker, PostgreSQL) |

---

## 3. Dátový model

Vychádza zo schémy v zadaní (kap. 12). Úpravy a doplnky sú označené **[+]** a zdôvodnené.

| Tabuľka | Kľúčové stĺpce | Poznámka |
|---|---|---|
| **[+] users** | email (unique), password_hash, role (`admin`/`viewer`), is_active, last_login_at | Zadanie požaduje prihlásenie a oprávnenia, schéma tabuľku neobsahuje. |
| competitors | name, base_url, domain (index, kontrola duplicít, viď nižšie), country, market_type, notes, status, default_frequency, created_at, updated_at | `market_type`: priamy/distribútor/marketplace/iný. |
| scrape_configs | competitor_id (unique), source_type (`api`/`feed`/`html`), parser_type, parser_config (JSONB, validované Pydantic schémou), parser_version, timeout_seconds, max_concurrency, max_retries, **[+] min_interval_seconds**, **[+] prices_include_vat** (`true`/`false`/`unknown`), **[+] last_test_status/at**, enabled | `parser_config` sú iba dáta (selektory, mapovania), nikdy kód. |
| products | name, isbn, ean, sku, brand, category, attributes (JSONB), **[+] internal_id**, **[+] priority** (`strategic`/`regular`/`stable`), **[+] tracking_status**, created_at, updated_at | Indexy na isbn, ean, sku. |
| **[+] product_prices** | product_id, market (napr. `SK`, `CZ`), price NUMERIC, currency, vat_included; unique (product_id, market) | Vlastná cena sa líši podľa trhu (taktik.sk v EUR, etaktik.cz v CZK). Porovnáva sa vždy s cenou pre trh konkurenta. Rozhodnutie R3. |
| competitor_products | competitor_id, product_id (nullable), product_url, **[+] variant** (napr. „tištěná“, kap. 6.1), external_product_id, matching_method, **[+] match_status** (`matched`/`pending_review`/`unmatched`/`ignored`), **[+] match_score**, status, custom_frequency, **[+] next_check_at**, created_at, updated_at; unique (competitor_id, product_url) | Index (status, next_check_at) pre scheduler. |
| current_prices | competitor_product_id (unique), price, original_price, currency, availability, **[+] vat_mode**, **[+] title**, **[+] found_identifiers**, last_checked_at, last_success_at, is_stale, **[+] last_error**, **[+] etag**, **[+] last_modified**, **[+] content_hash**, updated_at | Jeden riadok na ponuku. Cenu nikdy neprepíše NULL ani 0 pri chybe. |
| price_history | competitor_product_id, price, original_price, currency, availability, observed_at, **change_types (pole)**, unique (competitor_product_id, observed_at) | Pole namiesto jedného `change_type`, lebo pri jednej kontrole sa môže zmeniť cena aj dostupnosť. Index (competitor_product_id, observed_at). |
| scrape_jobs | competitor_product_id, **[+] run_id**, **[+] priority** (manuálne > plánované), **[+] kind** (`scheduled`/`manual`/`test`), scheduled_at, status, attempt_count, locked_at, started_at, finished_at, last_error, **[+] error_kind** (`temporary`/`permanent`) | **Partial unique index** na competitor_product_id WHERE status IN (`pending`,`running`) zabráni duplicitným úlohám. |
| scrape_runs | competitor_id, started_at, finished_at, status, total_count, success_count, failure_count, price_change_count, error_summary | Agregát jedného behu pre konkurenta. |
| **[+] alert_rules** | rule_type, params (JSONB), scope (globálne/konkurent/produkt), enabled | Zadanie má `rule_config` priamo v `alerts`; oddelenie pravidla od výskytu zabraňuje duplicitám. |
| alerts | rule_id, competitor_product_id, alert_type, details (JSONB), status (`open`/`acknowledged`/`resolved`), created_at, resolved_at | Partial unique (rule_id, competitor_product_id) WHERE status = `open`. |
| **[+] import_batches** | type, status, file_name, preview (JSONB), stats, created_by, created_at | Dvojkrokový import s náhľadom. |
| **[+] app_settings** | key, value (JSONB) | Intervaly priorít, časové pásmo, globálne limity. |
| **[+] ai_calls** | purpose, provider, model, input_tokens, output_tokens, cost, created_at | Iba ak sa aktivuje AI (etapa 11). |

**Duplicitné domény:** pri vytváraní konkurenta s už existujúcou doménou API vráti chybu
s odkazom na existujúci záznam. Uloženie je možné iba s explicitným príznakom
`allow_duplicate_domain` a vyplneným dôvodom v poznámke.

### 3.1 Kľúčové algoritmy

**Zápis pozorovania** (`services/pricing.py`, jedna transakcia):
1. `SELECT … FROM current_prices WHERE competitor_product_id = :id FOR UPDATE`.
2. **Neúspech:** nastav `last_checked_at`, `last_error`, `is_stale` podľa veku `last_success_at`. Cena, mena a dostupnosť zostanú nezmenené.
3. **Úspech:** porovnaj cenu, menu, dostupnosť a pôvodnú cenu s uloženými hodnotami.
   - Rozdiel (alebo prvé pozorovanie) → vlož `price_history` so zoznamom `change_types` (`initial`, `price_up`, `price_down`, `currency`, `availability`, `original_price`).
   - Žiadny rozdiel → nový záznam histórie nevzniká.
   - Vždy aktualizuj `last_checked_at`, `last_success_at` a nastav `is_stale = false`.
4. Vyhodnoť pravidlá upozornení pre túto ponuku (od etapy 7).

**Fronta úloh** (`worker/queue.py`):
- Prevzatie dávky: `UPDATE scrape_jobs SET status='running', locked_at=now(), attempt_count=attempt_count+1 WHERE id IN (SELECT id FROM scrape_jobs WHERE status='pending' AND scheduled_at <= now() ORDER BY priority, scheduled_at FOR UPDATE SKIP LOCKED LIMIT :n) RETURNING *`.
- Obnova po reštarte: úlohy `running` s `locked_at` starším ako lease (napr. 10 min) sa vrátia do `pending`, alebo sa pri vyčerpaní pokusov označia ako `failed`.
- Opakovanie: dočasné chyby (timeout, 5xx, 429) → `scheduled_at = now() + base · 2^attempt + jitter`, alebo podľa `Retry-After`. Trvalé chyby (404, 410, chyba parsera, zákaz v robots.txt) → bez opakovania. Strop `max_retries`.

**Interval kontroly ponuky:** `custom_frequency` ponuky → interval podľa priority produktu
(predvolene strategic 8 h, regular 24 h, stable 120 h; nastaviteľné v `app_settings`)
→ `default_frequency` konkurenta → globálne 24 h. Výsledok nikdy nie je kratší ako
`min_interval_seconds` konkurenta.

---

## 4. Etapy

Prehľad a závislosti:

| Etapa | Obsah | Fáza zadania | Výsledok |
|---|---|---|---|
| 0 | Zabezpečenie súčasnej aplikácie (voliteľné) | — | stará appka za heslom |
| 1 | Základ: FastAPI, PostgreSQL, Alembic, prihlásenie, UI kostra, Docker, Railway | 2 | prázdna appka s prihlásením na Railway |
| 2 | Evidencia konkurencie | 3 | CRUD konkurentov |
| 3 | Produkty a konkurenčné ponuky, párovanie | 3/6 | ponuky priradené k produktom |
| 4 | Scraping, aktuálne ceny, cenová história | 4 | manuálna kontrola funguje |
| 5 | Scheduler, worker, retry, monitoring | 5 | **MVP: splnené akceptačné kritériá (kap. 19)** |
| 6 | Cenové porovnanie a dashboard | 6 | **prechod zo starej aplikácie** |
| 7 | Cenová história s grafmi, upozornenia | 6 | |
| 8 | Import (CSV, Excel, XML feed, náhľad) | 6 | |
| 9 | Bezpečnosť, výkon, dokumentácia, overenie v prevádzke | 7 | pripravené na produkčné testovanie |
| 10 | Playwright pre JS weby (voliteľné) | — | |
| 11 | Voliteľné AI funkcie (voliteľné) | — | |

**Prechod zo starej aplikácie:** nová aplikácia vzniká v priečinkoch `backend/` a `frontend/`.
Stará Flask aplikácia zostáva nasadená a funkčná až do etapy 6. Dovtedy beží nová ako
samostatná Railway služba (napr. `web-v2`). Po etape 6 sa domény prepnú a staré súbory
(`app.py`, `db.py`, `templates/`, `static/`) sa odstránia. 5 existujúcich produktov sa
prenesie CSV importom; migračný skript zo SQLite nie je pri tomto objeme potrebný.

Každá etapa obsahuje aj vlastné testy a nasadenie. Etapa 9 nie je jediné miesto, kde sa testuje.

---

### Etapa 0: Zabezpečenie súčasnej aplikácie ✅ hotové 18. 10. 2026

- HTTP Basic Auth pre všetky stránky okrem `/healthz`. Meno `APP_USERNAME` (predvolene `admin`), heslo `APP_PASSWORD`, porovnanie cez `hmac.compare_digest`.
- Na Railway bez nastaveného `APP_PASSWORD` je aplikácia zamknutá (503 s návodom), lokálne bez hesla zostáva otvorená.
- Health check Railway presunutý na `/healthz`.
- **Overené lokálne pod gunicornom:** bez hesla 401, zlé heslo 401, správne heslo 200, `/healthz` 200 vo všetkých režimoch; bez `APP_PASSWORD` na Railway 503. Unit testy prešli.
- **Potrebné od vás:** nastaviť `APP_PASSWORD` v Railway Variables.

---

### Etapa 1: Základ aplikácie ✅ nasadené 18. 10. 2026

**Nasadenie (overené na https://dashboardcompetition-production.up.railway.app):**
- Existujúca služba `DashboardCompetition` bola prepnutá na novú aplikáciu (stará Flask aplikácia sa už nenasadzuje). Pridaný PostgreSQL (`Postgres-Gcn3`), `DATABASE_URL` odkazuje naň.
- Docker build z `deploy/Dockerfile` prešiel. Railway `preDeployCommand` sa nespustil, preto migrácie a prvý admin bežia pri štarte kontajnera pod pg advisory lockom.
- Overené: `/readyz` 200, React aplikácia sa načíta, prihlásenie (zlé heslo 401, správne 200), zoznam používateľov, `Secure` cookie, CSRF pri odhlásení, HSTS a ďalšie bezpečnostné hlavičky, API dokumentácia v produkcii vypnutá.
- **Automatický deploy z GitHubu sa nespúšťa**; nasadzuje sa cez `railway redeploy --service DashboardCompetition --from-source`. Treba skontrolovať Settings → Source (prípadne „Wait for CI“).

**Stav:**
- **Vytvorené:** `backend/` (FastAPI, SQLAlchemy, Alembic migrácia `0001` so všetkými tabuľkami jadra, prihlásenie so session cookie + CSRF, roly, obmedzenie neúspešných prihlásení, správa používateľov, `/healthz`, `/readyz`, CLI `predeploy`), `frontend/` (React + TS + Mantine: prihlásenie, navigácia so 7 sekciami, Nastavenia so správou používateľov, ostatné sekcie „Pripravuje sa“), `deploy/Dockerfile`, `railway/web.json`, `.env.example`, `.github/workflows/ci.yml`.
- **Testy (lokálne, skutočný výstup):** backend 45 passed (pytest proti PostgreSQL 16), ruff bez chýb; frontend 3 passed (Vitest), `tsc` bez chýb, produkčný build OK; ručne cez curl na uvicorn: migrácia, vytvorenie admina, prihlásenie, `/api/auth/me`, `/api/users`, servírovanie React aplikácie.
- **Neoverené:** zostavenie Docker image (lokálne nie je Docker, overí sa až buildom na Railway), beh CI na GitHube, vzhľad UI v prehliadači.
- **Presunuté do etapy 2:** spoločný komponent tabuľky so stránkovaním, triedením a filtrami (bez dát by nebol otestovateľný) a e2e testy UI v Playwright.

**Cieľ:** nasaditeľná kostra s databázou, migráciami, prihlásením a navigáciou.

**Rozsah:**
- `backend/`: FastAPI aplikácia, `config.py` (pydantic-settings, `DATABASE_URL` z Railway), SQLAlchemy session.
- Alembic a prvá migrácia s jadrom schémy: `users`, `competitors`, `scrape_configs`, `products`, `product_prices`, `competitor_products`, `current_prices`, `price_history`, `scrape_jobs`, `scrape_runs`, `app_settings`. Ďalšie tabuľky pribudnú migráciami v neskorších etapách.
- Prihlásenie: `POST /api/auth/login`, `logout`, `me`; session cookie, CSRF ochrana, role `admin`/`viewer`. Admin operácie chránené dependency `require_admin`.
- `cli.py create-admin` (prvý admin z `ADMIN_EMAIL`/`ADMIN_PASSWORD` alebo interaktívne).
- `GET /healthz` (proces žije) a `GET /readyz` (dostupná databáza).
- `frontend/`: Vite + React + TS, prihlasovacia stránka, layout s navigáciou (Dashboard, Konkurencia, Produkty, Cenové porovnanie, Cenová história, Aktualizácie a logy, Nastavenia). Nehotové sekcie zobrazujú „Pripravuje sa“, nie falošné dáta.
- Spoločné UI komponenty: tabuľka so stránkovaním, triedením a filtrami (napojená na API parametre `page`, `page_size`, `sort`, `q`), formulárové polia, stavové hlásenia.
- `Dockerfile` (multi-stage), `railway/web.json` (`preDeployCommand: alembic upgrade head`, health check `/readyz`), `.env.example`.
- GitHub Actions: lint (ruff), testy s PostgreSQL, build frontendu.
- Prenos `parse_price`, `detect_currency` a súčasných testov do `backend/app/scraping/normalize.py` a `pytest`.

**Testy:** migrácia hore/dole na prázdnej DB; prihlásenie (správne/nesprávne heslo, odhlásenie); ochrana admin endpointov pre rolu viewer; CSRF; prenesené unit testy.

**Hotové, keď:** služba `web-v2` beží na Railway, migrácie prebehli v pre-deploy kroku, admin sa
prihlási a vidí navigáciu, CI je zelené.

**Potrebné od vás:** pridať PostgreSQL do Railway projektu; vytvoriť službu `web-v2` z rovnakého
repozitára s cestou ku konfigurácii `railway/web.json`; nastaviť `SECRET_KEY`, `ADMIN_EMAIL`,
`ADMIN_PASSWORD`. Lokálne nainštalovať PostgreSQL (`brew install postgresql@16`) a Node.js (`brew install node`).

---

### Etapa 2: Evidencia konkurencie ✅ kód hotový 18. 10. 2026

**Stav:**
- **Vytvorené:** API `/api/competitors` (zoznam s vyhľadávaním, filtrom stavu a krajiny, triedením podľa všetkých stĺpcov a stránkovaním; detail; vytvorenie; čiastočná úprava; zmena stavu; nastavenia scrapingu `PUT …/scrape-config`; behy `…/runs`; mazanie iba bez dát), validácia a normalizácia URL (`services/urls.py`), kontrola duplicitnej domény s povinným dôvodom, validácia `parser_config` (parsery `auto`, `jsonld`, `microdata`, `opengraph`, `css`; CSS selektory sa kompilujú, neznáme kľúče odmietnuté), automatické zvýšenie `parser_version` pri zmene parsera.
- **UI:** spoločný komponent `DataTable` (triedenie, stránkovanie), zoznam konkurentov so stavom filtrov v URL, formulár (vytvorenie/úprava, upozornenie na duplicitnú doménu s odkazom), detail so sekciami Základné údaje, Sledované produkty, Nastavenia scrapingu, Monitoring; pozastavenie, aktivácia a archivácia s potvrdením.
- **Testy (skutočný výstup):** backend 79 passed (z toho 32 nových: URL, CRUD, duplicity, stavy bez straty dát, filtre, triedenie, stránkovanie, escapovanie LIKE, validácia parsera, oprávnenia), frontend Vitest 3 passed, **Playwright e2e 7 passed** (pridanie, úprava, duplicitná doména, vyhľadávanie a filter, pozastavenie a archivácia, CSS parser, čitateľ bez úprav). UI skontrolované na snímkach (desktop 1280 px, mobil 390 px).
- **Neimplementované v tejto etape (podľa plánu neskôr):** testovacia kontrola (etapa 4), prepočet `next_check_at` pri zmene frekvencie (etapa 5, keď existuje scheduler), XPath a parsery pre konkrétne weby (etapa 4), zoznam ponúk konkurenta (etapa 3).

**Cieľ:** kompletná správa konkurentov cez UI.

**Rozsah:**
- API: zoznam (vyhľadávanie podľa názvu/domény, filter stav/krajina, triedenie, stránkovanie), detail, vytvorenie, úprava, zmena stavu (aktívny/pozastavený/archivovaný). Mazanie iba pre konkurenta bez histórie, inak archivácia.
- Validácia URL (iba http/https, normalizácia, extrakcia domény bez `www.`), kontrola duplicitnej domény.
- Formulár konkurenta podľa kap. 4.2 zadania vrátane preferovaného zdroja dát a frekvencie.
- Nastavenia scrapingu: formulár `scrape_configs` s validáciou cez Pydantic schému podľa `parser_type` (napr. `css` vyžaduje selektor ceny). Limity hodnôt (timeout 1–60 s, súbežnosť 1–4, pokusy 0–5).
- Detail konkurenta so sekciami Základné údaje, Sledované produkty, Nastavenia scrapingu, Monitoring. Sekcie produktov a monitoringu zobrazia prázdny stav až do etáp 3 a 5.
- Zoznam s počtom ponúk, poslednou úspešnou kontrolou a počtom chýb (zatiaľ 0, reálne čísla od etapy 5).

**Testy:** CRUD; duplicitná doména (odmietnutie aj povolenie s dôvodom); validácia URL a konfigurácie;
zmena stavu nemaže dáta; vyhľadávanie, filter, stránkovanie. UI e2e: pridanie, úprava, filter, zmena stavu.

**Hotové, keď:** admin vytvorí, upraví, pozastaví a archivuje konkurenta; viewer vidí, ale nemení.

---

### Etapa 3: Produkty, konkurenčné ponuky a párovanie ✅ kód hotový 18. 10. 2026

**Stav:**
- **Vytvorené:** API `/api/products` (CRUD, ceny podľa trhu s predvolenou menou podľa trhu, parametre predmet/ročník/typ, vyhľadávanie podľa názvu aj ISBN v ľubovoľnom formáte, filtre, triedenie, stránkovanie), CSV import s výsledkom po riadkoch (UTF-8 aj Windows-1250, mena aj z textu ceny), migrácia `0002` (unikátne ISBN/EAN/SKU), API ponúk (hromadné pridanie URL s kontrolou domény konkurenta a duplicít, zoznamy pre konkurenta, produkt aj frontu párovania, úprava, pozastavenie, odobratie s archiváciou pri existujúcej histórii, priradenie ručne / ignorovať / automaticky, kandidáti).
- **Párovanie** (`services/matching.py`, bez AI): ISBN-10 → ISBN-13 a kontrolné číslice EAN, presná zhoda GTIN alebo SKU → priradené; podobný názov ≥ 0,9 s jasným odstupom → priradené; 0,6–0,9 alebo viac rovnako dobrých kandidátov → „čaká na kontrolu“; nezhodné čísla v názve (ročník, diel) znižujú skóre na polovicu; ručné rozhodnutie automatika nikdy neprepíše; nový alebo zmenený produkt znovu vyhodnotí otvorené ponuky.
- **UI:** Produkty (zoznam, formulár s cenami pre viac trhov, CSV import, detail s priradenými ponukami), záložka Párovanie (fronta podľa stavu), v detaile konkurenta záložka Sledované produkty (pridanie URL, filtre, priradenie cez okno s kandidátmi a vyhľadávaním, ignorovanie, pozastavenie, odobratie).
- **Testy (skutočný výstup):** backend 115 passed (36 nových), Vitest 3 passed, Playwright e2e 11 passed (4 nové: produkt s cenami pre SK a CZ a chybné ISBN, CSV import, pridanie URL s kontrolou domény a duplicít, ručné priradenie, ignorovanie a fronta Párovanie). UI skontrolované na snímkach.
- **Poznámka:** automatické párovanie má zatiaľ málo vstupov, lebo názov a identifikátory ponuky prinesie až scraping (etapa 4). Pre alter.cz bude prevažovať ručné priradenie (iné vydavateľstvo, iné ISBN).

**Cieľ:** vlastné produkty, ich ceny podľa trhu a konkurenčné ponuky s priradením.

**Rozsah:**
- Vlastné produkty: CRUD, ceny podľa trhu (`product_prices`), priorita sledovania, vyhľadávanie podľa názvu/ISBN/EAN/SKU.
- Jednoduchý CSV import vlastných produktov (prenesený zo starej aplikácie, kvôli presunu dát). Pokročilý import s náhľadom príde v etape 8.
- Konkurenčné ponuky: pridanie jednej URL a hromadné vloženie viacerých URL (jedna na riadok) k vybranému konkurentovi. Kontrola, že URL patrí doméne konkurenta, a duplicít.
- Párovanie (`services/matching.py`):
  1. Normalizácia identifikátorov: ISBN-10 → ISBN-13, odstránenie pomlčiek, kontrola kontrolnej číslice EAN/ISBN.
  2. Presná zhoda ISBN/EAN/SKU → `matched`, `matching_method = identifier`.
  3. Zhoda názvu (súčasný `similarity`): skóre ≥ 0,9 a súhlasiace dostupné parametre → `matched`; 0,6–0,9 → `pending_review`; menej → `unmatched`.
  4. Manuálne priradenie alebo „ignorovať“ používateľom → `matching_method = manual`, automatika ho neprepíše.
- Fronta „Čaká na kontrolu“ v UI s navrhnutými kandidátmi.

Párovanie beží znovu po každej úspešnej kontrole, ktorá prinesie nový identifikátor alebo názov (od etapy 4).

**Testy:** normalizácia a validácia ISBN/EAN; párovanie podľa identifikátora, podľa názvu,
nejednoznačné prípady → `pending_review`; manuálne priradenie má prednosť; duplicitná URL odmietnutá.

**Hotové, keď:** admin pridá produkt s cenou pre SK aj CZ, pridá URL konkurenta a priradí ju.

---

### Etapa 4: Scraping, aktuálne ceny a cenová história

**Cieľ:** spoľahlivé získanie ceny jednej ponuky a správny zápis histórie. Manuálna a testovacia
kontrola beží cez frontu úloh a worker (zatiaľ bez automatického plánovania).

**Rozsah:**
- `urlsafety.py`: iba http/https a porty 80/443; DNS rezolúcia a blokovanie súkromných, loopback, link-local, CGNAT, multicast a rezervovaných rozsahov (IPv4 aj IPv6), vrátane `169.254.169.254`; kontrola každého presmerovania (max. 5); po pripojení kontrola skutočnej IP servera (obrana proti DNS rebinding).
- `fetcher.py`: httpx s timeoutom z konfigurácie, streamované čítanie s limitom veľkosti (napr. 5 MB), identifikovateľný User-Agent, `robots.txt` s cache na doménu, rozlíšenie dočasných a trvalých chýb.
- Parsery v registri (`parsers/registry.py`), každý s verziou:
  - `jsonld`, `microdata`, `opengraph` (prenesené a rozšírené o dostupnosť, pôvodnú cenu, ISBN/EAN/SKU a `valueAddedTaxIncluded`),
  - `css` a `xpath` (selektory z `parser_config`),
  - `auto`: postupne jsonld → microdata → opengraph.
  - Parsery pre konkrétne weby v `scraping/sites/` sa registrujú menom. Píše ich vývojár, používateľ ich iba vyberá.
- Normalizácia: cena ako `Decimal`, mena (ISO 4217), dostupnosť (`in_stock`/`out_of_stock`/`preorder`/`unknown`), režim DPH (`with_vat`/`without_vat`/`unknown`). Chýbajúci údaj sa uloží ako neznámy, nikdy sa nedomýšľa.
- `services/pricing.py`: `record_observation()` podľa algoritmu v kap. 3.1.
- Fronta úloh: tabuľka `scrape_jobs`, `queue.py` (claim/complete/fail/recover), `worker.py` spracúvajúci úlohy typu `manual` a `test`.
- UI: tlačidlo „Skontrolovať teraz“ pri ponuke a „Testovacia kontrola“ v nastaveniach konkurenta. UI zobrazí stav úlohy (čaká, beží, hotovo, chyba) a pri teste aj extrahované hodnoty a použitý parser.
- Zobrazenie aktuálnej ceny, dostupnosti, času poslednej kontroly a chyby pri ponukách.
- HTML vzorky v `tests/fixtures/html/` (etaktik.cz a ďalšie weby z rozhodnutia R5), uložené raz, testy už nesťahujú z internetu.

**Testy:** SSRF (interné IP, presmerovanie na internú IP, nepovolený port, schéma `file:`); limit veľkosti;
parsery na vzorkách; chyba parsera; integračné: prvé pozorovanie vytvorí históriu, zmena ceny vytvorí
záznam, nezmenená cena nie, chyba zachová poslednú platnú cenu a nastaví `last_error`;
úloha sa nespracuje dvakrát; pozastavený konkurent nemá spracované plánované úlohy.

**Hotové, keď:** pre testovací web sa po kliknutí získa cena, mena a dostupnosť, uloží sa do PostgreSQL
a opakovaná kontrola s rovnakou cenou nevytvorí nový záznam histórie.

**Potrebné od vás:** zoznam 2–3 skutočných konkurenčných webov a niekoľko URL produktov z každého (R5).
Služba `worker` na Railway (`railway/worker.json`).

---

### Etapa 5: Automatizácia

**Cieľ:** pravidelné a spoľahlivé aktualizácie bez zásahu používateľa. **Po tejto etape je MVP
hotové a spĺňa akceptačné kritériá z kap. 19 zadania.**

**Rozsah:**
- `scheduler.py`: každú minútu (pod `pg_try_advisory_lock`) vyberie aktívne ponuky aktívnych konkurentov s `next_check_at <= now()`, v dávkach vytvorí úlohy (`INSERT … ON CONFLICT DO NOTHING` vďaka partial unique indexu) a posunie `next_check_at`.
- Zmena frekvencie konkurenta alebo ponuky prepočíta `next_check_at` dotknutých ponúk.
- Rozloženie záťaže: ponuky jedného konkurenta sa rozložia v čase (jitter), nespúšťajú sa naraz.
- Worker: dávkové spracovanie, globálny limit súbežnosti (`WORKER_CONCURRENCY`, predvolene 2), limit súbežnosti a minimálny rozostup požiadaviek na doménu, rešpektovanie 429 a `Retry-After` (posunie úlohy celej domény).
- Retry politika a obnova po reštarte podľa kap. 3.1.
- Podmienené požiadavky: `If-None-Match` / `If-Modified-Since`, ak zdroj poskytol ETag alebo Last-Modified; odpoveď 304 sa spracuje ako úspešná kontrola bez zmeny. Inak sa porovná hash relevantnej časti stránky a pri zhode sa preskočí parsovanie.
- Označovanie zastaraných cien: `is_stale`, ak posledný úspech je starší ako 2× interval ponuky.
- `scrape_runs`: agregácia výsledkov za konkurenta a deň/beh, počet zmien cien, súhrn chýb.
- Stránka „Aktualizácie a logy“: behy, úlohy, chyby, filtrovanie podľa konkurenta a stavu. Sekcia Monitoring v detaile konkurenta.
- Upozornenie v aplikácii pri opakovanom zlyhaní scrapera (napr. 3 behy za sebou) – zjednodušená verzia pred etapou 7.
- Nastavenia: intervaly priorít, časové pásmo zobrazenia (predvolene Europe/Bratislava), globálne limity.

**Testy:** scheduler vyberie iba splatné ponuky aktívnych konkurentov; nevytvorí duplicitnú úlohu;
dva súbežné schedulery neplánujú dvakrát; obnova úlohy po „páde“ workera; exponenciálne
oneskorenie a strop pokusov; 429 s `Retry-After`; trvalá chyba bez opakovania; 304 bez zmeny histórie.

**Hotové, keď:** na Railway beží worker, ponuky sa samy aktualizujú podľa intervalu, pozastavený
konkurent sa nespracúva a v UI sú viditeľné výsledky aj chyby behov. Overiť aspoň 24 h prevádzky.

---

### Etapa 6: Cenové porovnanie a dashboard

**Cieľ:** nahradiť porovnanie zo starej aplikácie a pridať prehľad.

**Rozsah:**
- Cenové porovnanie: vlastná cena pre trh konkurenta, cena každého konkurenta, rozdiel absolútne a v %, najnižšia a najvyššia cena, čas posledného overenia, dostupnosť. Neznáme a zastarané ceny sú zreteľne označené. Rôzne meny alebo režimy DPH sa nepočítajú do rozdielu ani minima, kým nie je nastavená normalizácia (DPH sadzba podľa trhu, kurz meny ako voliteľná funkcia neskôr).
- Filter podľa trhu, kategórie, konkurenta, „konkurent lacnejší“; export CSV.
- Dashboard (iba z databázy): počet konkurentov, aktívnych webov, sledovaných ponúk, úspešných kontrol za 24 h, chýbajúcich/zastaraných cien, zmien cien; najväčšie poklesy; posledné zmeny; stav posledných behov.
- **Prechod:** import 5 produktov zo starej aplikácie, prepnutie Railway domény na novú službu, odstránenie starej Flask aplikácie z repozitára.

**Testy:** výpočty rozdielov a min/max; vylúčenie inej meny a DPH režimu; dashboard čísla na známych testovacích dátach.

**Hotové, keď:** nová aplikácia pokrýva všetko, čo vedela stará, a stará je vypnutá.

---

### Etapa 7: Cenová história s grafmi a upozornenia

**Rozsah:**
- Stránka Cenová história: výber produktu, obdobia a konkurentov; graf (Recharts, schodový priebeh, keďže cena platí do ďalšej zmeny) s vlastnou cenou ako porovnávacou líniou a vyznačenými zmenami; tabuľka zmien s filtrom podľa produktu, konkurenta a času.
- Migrácia `alert_rules`, `alerts`. Pravidlá: pokles ceny o viac ako X %, konkurent lacnejší ako my, produkt nedostupný, cena neaktualizovaná v požadovanom intervale, opakované zlyhanie scrapera.
- Vyhodnotenie pri zápise pozorovania a pri behu schedulera (zastarané ceny). Deduplikácia otvorených upozornení, automatické vyriešenie po odpadnutí podmienky.
- UI: zoznam upozornení, potvrdenie, filter, počet otvorených v navigácii; správa pravidiel v Nastaveniach.

**Testy:** každé pravidlo (spustí / nespustí), deduplikácia, automatické vyriešenie, graf a tabuľka histórie (UI e2e).

---

### Etapa 8: Import produktov a URL

**Rozsah:**
- Migrácia `import_batches`. Dvojkrokový tok: nahratie → náhľad s validáciou a označením duplicít → potvrdenie.
- Formáty: CSV (oddeľovač čiarka/bodkočiarka/tab, UTF-8 a cp1250), Excel `.xlsx` (openpyxl), XML feed (Heureka a Google Merchant formát, parsovanie cez defusedxml).
- Typy importu: vlastné produkty, konkurenčné ponuky (URL + konkurent + voliteľné priradenie).
- Pomocník „Načítať ponuky zo stránky kategórie“ (využije súčasnú extrakciu zo starej aplikácie): z jednej zadanej stránky navrhne URL produktov, používateľ vyberie, ktoré pridať. Žiadne automatické prehľadávanie celého webu.
- Limity: veľkosť súboru (napr. 5 MB), počet riadkov (napr. 10 000).
- Výsledok: počet importovaných, preskočených duplicít a chybných riadkov s konkrétnym dôvodom pre každý riadok.

**Testy:** každý formát na vzorkách; chybné riadky a dôvody; duplicity; prekročenie limitov; XXE v XML odmietnuté.

---

### Etapa 9: Bezpečnosť, výkon, dokumentácia, overenie

**Rozsah:**
- Bezpečnostná kontrola: SSRF testy so všetkými rozsahmi, limit prihlásení (ochrana proti hádaniu hesla), bezpečnostné HTTP hlavičky, logy bez hesiel a tokenov, chyby bez stack trace pre používateľa, kontrola závislostí (pip-audit, npm audit).
- Výkon: indexy pre časté dotazy (overiť `EXPLAIN`), stránkovanie všade, retencia diagnostických dát (napr. `scrape_jobs` starších ako 30 dní mazať, `scrape_runs` agregovať).
- Dokumentácia: `README.md` podľa kap. 18 zadania, `docs/SCRAPER_GUIDE.md` (nový parser bez zásahu do ostatných), zálohovanie a obnova (`pg_dump` / `pg_restore`, Railway zálohy), diagnostika bežných problémov (vrátane poznatkov o porte 8080 a nastavení Source na Railway).
- Overenie v prevádzke: kontrola každého akceptačného kritéria na Railway s reálnymi dátami, zápis výsledkov.

**Hotové, keď:** všetky akceptačné kritériá z kap. 19 sú overené na Railway a výsledky zdokumentované.

---

### Etapa 10 (voliteľná): Playwright pre weby vyžadujúce JavaScript

Iba ak niektorý konkurent neposkytuje dáta v HTML, JSON-LD ani feede.

- `parser_type = browser`, ktorý stránku vykreslí v Chromiume a potom použije bežné parsery.
- Samostatný Docker target a samostatná Railway služba `worker-browser` spracúvajúca iba tieto úlohy, aby bežný image zostal malý. Súbežnosť 1, blokovanie obrázkov a fontov.
- Neobchádza CAPTCHA ani ochrany proti botom; web s takou ochranou sa označí ako nepodporovaný.

---

### Etapa 11 (voliteľná): AI funkcie

Predvolene vypnuté, aktivácia iba po nastavení poskytovateľa a explicitnom zapnutí v Nastaveniach.

- Párovanie nejednoznačných produktov z fronty `pending_review`: posiela sa iba názov, identifikátory a niekoľko kandidátov, nikdy celé katalógy ani HTML.
- Návrh CSS selektorov pri zmene štruktúry stránky: posiela sa iba skrátený výrez okolo ceny. Návrh musí používateľ potvrdiť a otestovať.
- Každé volanie zapísané v `ai_calls` (účel, tokeny, cena). Mesačný limit nákladov v nastaveniach.

---

## 5. Riziká

| Riziko | Dopad | Opatrenie |
|---|---|---|
| Lokálne nie je Docker ani PostgreSQL, Python je 3.9 | Nedajú sa spustiť integračné testy | Inštalácia `postgresql@16`, Node.js a Python 3.12 cez Homebrew alebo uv; CI na GitHub Actions s PostgreSQL. |
| Automatický deploy na Railway sa v minulosti nespúšťal | Nasadená stará verzia | Pred etapou 1 overiť Source (repo, branch `main`) pre každú službu; každá služba má vlastný config súbor. |
| Railway ukončuje podporu `railway.json` (config-as-code) k **1. 12. 2026** | Po tomto dátume sa nepoužije Dockerfile ani pre-deploy migrácie | Pred 1. 12. 2026 previesť na Infrastructure as Code (`railway config migrate`), najneskôr v etape 5. |
| Ochrana proti botom (Cloudflare a pod.) na konkurenčných weboch | Ceny sa nedajú získať | Neobchádzať; hľadať feed alebo API; web označiť ako nepodporovaný. |
| Weby generujúce obsah JavaScriptom | Bežný scraping nič nenájde | Etapa 10, iba kde je nutné; vyššie nároky na pamäť a cenu. |
| Učebnice majú veľa podobných vydaní („revidované RVP ZV“, „nová edice“, diely) | Chybné párovanie podľa názvu | ISBN ako primárny kľúč; párovanie podľa názvu nad prahom iba do `pending_review`, nie automaticky. |
| Rôzne meny (EUR/CZK) a DPH (B2C vs. distribútori) | Nesprávne porovnanie | Vlastné ceny podľa trhu; rozdiely sa nepočítajú medzi rôznymi menami a režimami DPH. |
| Zmena štruktúry konkurenčného webu | Výpadok cien | Chyba sa zaznamená, posledná platná cena zostane, cena sa označí ako zastaraná, upozornenie po opakovaných chybách. |
| Podmienky používania webov konkurencie | Právne riziko | Rešpektovať robots.txt a limity; nízka frekvencia; pred pridaním webu skontrolovať jeho podmienky. |
| React zvyšuje rozsah a zložitosť buildu | Pomalší vývoj | Jeden Docker image, frontend sa servíruje z FastAPI, žiadna samostatná Node služba. |
| Limity Railway plánu | Náklady, výpadky | Nastaviteľné `WORKER_CONCURRENCY`, frekvencie a limity na doménu; jeden worker na začiatok. |

---

## 6. Rozhodnutia

Plán schválený 18. 10. 2026.

| # | Otázka | Rozhodnutie |
|---|---|---|
| R1 | Frontend | **React + TypeScript.** |
| R2 | Služby na Railway | **Free plán, worker zabudovaný vo web procese** (`EMBEDDED_WORKER=true`, od etapy 4). Služby: stará aplikácia, nová `web`, PostgreSQL = 3 (limit Free plánu). Samostatný worker zostáva v kóde pre neskorší prechod na Hobby. |
| R3 | Vlastné ceny podľa trhu | Áno, `product_prices` (odporúčanie, nenamietané). |
| R4 | Roly | admin a viewer (odporúčanie, nenamietané). |
| R5 | Prvý konkurent | **alter.cz.** Požiadavky iba postupne, s rozostupom (kap. 6.1). |
| R6 | Jazyk UI | slovenčina (odporúčanie, nenamietané). |
| R7 | Heslo pre súčasnú aplikáciu | **Áno, etapa 0 je hotová** (HTTP Basic Auth, `APP_PASSWORD`). |

### 6.1 Konkurent alter.cz (analýza 18. 10. 2026)

Zistené z 3 požiadaviek s rozostupom 3–5 s: `robots.txt`, úvodná stránka, `/produkt/1111`.

- **Platforma:** Drupal 10 za Cloudflare. `robots.txt` neobsahuje žiadne `Disallow` pravidlá, iba vysvetlenie „content signals“ bez nastavených hodnôt (zber cien nie je zakázaný; AI spracovanie obsahu sa nepoužíva).
- **Produktové URL:** `https://www.alter.cz/produkt/<id>`. Katalóg: `/shop` (vhodný pre pomocníka importu v etape 8).
- **Štruktúrované dáta:** JSON-LD obsahuje iba `Article`, nie `Product`. Microdata ani Open Graph cena nie sú. **Potrebný je vlastný parser `sites/alter_cz.py`** (etapa 4):

  | Údaj | Zdroj na stránke |
  |---|---|
  | Názov | `h1` |
  | Cena | `.vatPricePD` (napr. `44,00 Kč`), jedna na variant |
  | Variant | `h2` v bloku ceny (napr. „tištěná“) |
  | Mena | CZK (z textu `Kč`) |
  | DPH | trieda `vatPrice` naznačuje cenu s DPH, **overiť v obchodných podmienkach**, dovtedy `unknown` |
  | Dostupnosť | `.detail_key` „Dostupnost:“ → nasledujúci `.detail_value` (napr. „Skladem“) |
  | EAN | text popisu `EAN: 8594165541796` (regulárny výraz) |

- **Varianty:** produkt môže mať tlačenú aj digitálnu verziu s rôznou cenou. Ponuka (`competitor_products`) dostane stĺpec **[+] `variant`**; parser vyberie cenu zvoleného variantu, predvolene „tištěná“.
- **Prevádzkové limity pre alter.cz:** `max_concurrency = 1`, minimálne 5 s medzi požiadavkami, kontrola raz denne v noci, pri HTTP 403/429 alebo Cloudflare výzve okamžite zastaviť beh pre doménu a zaznamenať chybu (žiadne obchádzanie).
- **Párovanie:** Alter je iné vydavateľstvo, jeho tituly nie sú totožné s titulmi Taktik, preto sa ISBN/EAN zhodovať nebudú. Priradenie „porovnateľného“ titulu (predmet, ročník, typ: učebnica / pracovný zošit) bude **prevažne manuálne**, automatika iba navrhne kandidátov do `pending_review`. Produkty dostanú v `attributes` polia `subject`, `grade`, `type`, aby návrhy boli presnejšie.
- **Testy:** uložené HTML vzorky v `tests/fixtures/html/alter_cz/`, testy nesťahujú z webu.

### 6.2 Náklady na Railway (cenník overený 18. 10. 2026)

| Plán | Mesačne | Kredit na spotrebu | Limity |
|---|---|---|---|
| Free | $0 | $1 / mesiac | 1 projekt, 3 služby, 0,5 GB RAM na službu |
| Hobby | $5 | $5 / mesiac (v cene) | 50 projektov, 50 služieb |

Spotreba sa účtuje za skutočne použité zdroje: RAM približne $10 za GB-mesiac, CPU približne $20 za vCPU-mesiac.

**Odhad pre túto aplikáciu** (orientačný, overiť v Railway → Usage po nasadení):
- web cca 100–150 MB RAM → cca $1–1,5 / mesiac,
- worker cca 70–100 MB RAM → cca $0,7–1 / mesiac,
- PostgreSQL cca 100–150 MB RAM → cca $1–1,5 / mesiac,
- CPU pri jednom konkurentovi a dennej kontrole zanedbateľné.

Spolu cca $3–4 / mesiac. **Free plán ($1 kredit) to nepokryje**, Hobby plán ($5 s kreditom $5) áno.

- **Hobby:** web + worker + PostgreSQL ako samostatné služby (pôvodné odporúčanie).
- **Free (zvolené):** web so zabudovaným workerom + PostgreSQL = 2 služby, spolu so starou aplikáciou 3 (presne limit). Kredit $1 sa pravdepodobne minie v priebehu mesiaca a služby sa zastavia; spotrebu sledovať v Railway → Usage. Po vypnutí starej aplikácie (etapa 6) klesne spotreba približne o tretinu.

---

## 7. Postup po každej etape

Na konci každej etapy sa uvedie:
1. Čo bolo vytvorené.
2. Ktoré testy prešli (skutočný výstup, nie odhad).
3. Čo ešte nie je implementované.
4. Aké konfigurácie alebo externé účty sú potrebné.
5. Ďalší konkrétny krok.
