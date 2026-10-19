# Sledovanie cien konkurencie

Webová aplikácia, ktorá automaticky sleduje ceny produktov v konkurenčných e-shopoch, uchováva históriu
skutočných zmien, porovnáva ich s vlastnými cenami a upozorňuje na dôležité udalosti. Bežná prevádzka
nepoužíva žiadnu AI ani AI tokeny.

- Produkcia: https://dashboardcompetition-production.up.railway.app
- Plán a stav etáp: [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)
- Parser pre nový web: [docs/SCRAPER_GUIDE.md](docs/SCRAPER_GUIDE.md)
- Overenie akceptačných kritérií: [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md)

## Obsah

1. [Architektúra](#architektúra)
2. [Technológie](#technológie)
3. [Lokálne spustenie](#lokálne-spustenie)
4. [Nastavenie (environment variables)](#nastavenie-environment-variables)
5. [Databázové migrácie](#databázové-migrácie)
6. [Práca s aplikáciou](#práca-s-aplikáciou)
7. [Nasadenie na Railway](#nasadenie-na-railway)
8. [Diagnostika bežných problémov](#diagnostika-bežných-problémov)
9. [Zálohovanie a obnova](#zálohovanie-a-obnova)
10. [Testy](#testy)

## Architektúra

```
prehliadač ──▶ web (FastAPI, uvicorn) ── servíruje React aplikáciu a /api
                 │   └─ vlákno workera: scheduler (každú minútu), spracovanie úloh, upozornenia (každých 5 min),
                 │                      upratovanie starých diagnostických dát (každých 6 h)
                 ▼
              PostgreSQL ── produkty, ponuky, ceny, história, fronta úloh, behy, upozornenia, importy
```

- **Jeden repozitár, jeden Docker image.** Worker beží ako vlákno web procesu (`EMBEDDED_WORKER=true`, vhodné pre
  Free plán). Pri väčšej záťaži ho možno spustiť ako samostatnú službu: `python -m app.worker` a vo webe
  `EMBEDDED_WORKER=false`. Scheduler sa dá spustiť aj jednorazovo: `python -m app.worker.scheduler`.
- **Stav je iba v PostgreSQL.** Fronta úloh používa `FOR UPDATE SKIP LOCKED` a čiastočný unikátny index
  (jedna otvorená úloha na ponuku); po reštarte sa nedokončené úlohy obnovia. Žiadny Redis.
- **Scraping nikdy nebeží v HTTP požiadavke používateľa.** API iba zaradí úlohu, UI sleduje jej stav.
- **Kód (`backend/app`):** `api/` (endpointy), `models/` (tabuľky), `schemas/` (validácia vstupov),
  `services/` (porovnanie, párovanie, ceny a história, plánovanie, upozornenia, importy, upratovanie),
  `scraping/` (bezpečné sťahovanie, parsery, register parserov, parsery pre konkrétne weby v `sites/`),
  `worker/` (fronta, spracovanie úlohy, scheduler, worker). Frontend je v `frontend/src`.

## Technológie

| Vrstva | Použité |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic 2, psycopg 3, argon2 |
| Scraping | httpx, BeautifulSoup, lxml (XPath); Playwright sa zatiaľ nepoužíva (nie je potrebný) |
| Importy | openpyxl (Excel), defusedxml (XML feedy) |
| Frontend | React 19, TypeScript, Vite, Mantine, TanStack Query, Recharts |
| Databáza | PostgreSQL 16+ (na Railway 18) |
| Testy | pytest, Vitest, Playwright; CI na GitHub Actions |
| Nasadenie | Docker, Railway |

## Lokálne spustenie

Potrebné: Python 3.12, Node.js 24, PostgreSQL 16+ (`brew install python@3.12 node postgresql@16`).

```bash
createdb competition && createdb competition_test && createdb competition_e2e
cp .env.example .env                       # upravte, ak máte inú adresu databázy

cd backend
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m app.cli migrate
.venv/bin/python -m app.cli create-admin --email vy@firma.sk   # opýta sa na heslo (min. 10 znakov)
.venv/bin/uvicorn app.main:app --reload --port 8000

cd ../frontend
npm install
npm run dev                                # http://localhost:5173 (požiadavky na /api idú na port 8000)
```

## Nastavenie (environment variables)

Vzor je v [.env.example](.env.example); skutočné heslá nikdy nepatria do repozitára.

| Premenná | Význam | Predvolená hodnota |
|---|---|---|
| `DATABASE_URL` | pripojenie k PostgreSQL (Railway ju dodá odkazom na databázovú službu) | `postgresql+psycopg://localhost/competition` |
| `SECRET_KEY` | podpis prihlasovacích cookies; v produkcii povinný, aspoň 32 znakov | iba vývojový kľúč |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | prvý administrátor, vytvorí sa pri štarte, ak žiadny neexistuje | — |
| `TIMEZONE` | časové pásmo zobrazenia a časového okna plánovania | `Europe/Bratislava` |
| `EMBEDDED_WORKER` | worker ako vlákno web procesu | `true` |
| `WORKER_POLL_SECONDS` | ako často worker hľadá nové úlohy | `2` |
| `SCHEDULER_INTERVAL_SECONDS` | ako často beží scheduler | `60` |
| `SCRAPER_USER_AGENT` | identifikácia voči webom (pravidlá robots.txt platia pre prvé slovo) | `CompetitionChecker/1.0 (price monitoring)` |
| `SCRAPER_MAX_BYTES` | najväčšia stiahnutá stránka | `5242880` (5 MB) |
| `RAILWAY_ENVIRONMENT` | nastavuje Railway; zapína produkčné kontroly (Secure cookies, HSTS, bez API dokumentácie) | — |

## Databázové migrácie

- Pri každom štarte kontajnera sa spustí `python -m app.cli predeploy`: migrácie (pod zámkom v databáze, takže
  ich dve inštancie nespustia naraz) a vytvorenie prvého administrátora.
- Ručne: `cd backend && .venv/bin/python -m app.cli migrate`.
- Nová migrácia po zmene modelov: `.venv/bin/alembic revision --autogenerate -m "popis"`, výsledok vždy
  skontrolujte (Alembic nezachytí zmeny CHECK obmedzení ani dáta).
- Migrácie: `0001` jadro schémy, `0002` unikátne identifikátory, `0003` plánovanie a monitoring, `0004`
  upozornenia s predvolenými pravidlami, `0005` importy, `0006` verzia relácie, `0007` URL, obrázok, dostupnosť
  a zdroj údajov vlastného produktu, `0008` príznak balíčka, `0009` objavovanie produktov konkurentov, `0010` diagnostika
  objavovania a front crawlera.

## Práca s aplikáciou

**Pridanie nového konkurenta.** Konkurencia → Pridať konkurenta (názov, URL, krajina/trh, typ, frekvencia).
Duplicitná doména sa uloží iba s uvedeným dôvodom. Potom v detaile → Nastavenia scrapingu vyberte parser
(najprv „Automaticky“, inak CSS/XPath alebo parser pre konkrétny web), nastavte rozostup požiadaviek a režim DPH
a spustite **Testovacia kontrola** na skutočnej produktovej stránke. Pred pridaním webu si prečítajte jeho
podmienky používania; aplikácia rešpektuje robots.txt a nikdy neobchádza ochranu proti botom.

**Nový parser pre web.** Postup bez zásahu do ostatných parserov je v [docs/SCRAPER_GUIDE.md](docs/SCRAPER_GUIDE.md).

**Sledované produkty konkurenta.** Detail konkurenta → Sledované produkty → Pridať URL (vložiť zoznam URL alebo
vybrať odkazy zo stránky kategórie) alebo Import zo súboru. Ponuky priradíte k vlastným produktom tlačidlom
s reťazou; zhoda ISBN/EAN/SKU sa priradí automaticky, podobné názvy čakajú v Produkty → Párovanie.

**Automatické objavovanie produktov konkurenta.** Detail konkurenta → Katalóg konkurenta: zvoľte „Tieňový režim“,
potvrďte kontrolu podmienok webu a uložte. Aplikácia v nočnom okne sama nájde zdroj produktov (sitemap, feed,
zoznam so stránkovaním alebo adaptér webu, napr. alter.cz). Nové produkty spáruje s porovnateľnými vlastnými
podľa ročníka, predmetu, typu a dielu, prípadne podľa ISBN/EAN. Po dvoch úspešných tieňových behoch prejde
do automatiky a spárované produkty začne sledovať. Nejednoznačné položky čakajú v záložke na kontrolu.
Ak web nemá sitemapu ani feed, prejde ho riadený crawler (od katalógu, cez kategórie a série až
po detaily produktov) s limitmi stránok, hĺbky a času. Panel „Diagnostika zdroja“ ukáže, ako sa produkty
hľadali a ktoré časti webu sa nespracovali. Bezpečnostné pravidlá sú v
[docs/AUTO_DISCOVERY_PLAN.md](docs/AUTO_DISCOVERY_PLAN.md) a robustné načítavanie v
[docs/SCRAPING_ROBUSTNESS_PLAN.md](docs/SCRAPING_ROBUSTNESS_PLAN.md).

**Vlastné produkty z feedu e-shopu.** Nastavenia → Vlastné produkty z feedu e-shopu: adresa feedu
(pre etaktik.cz `https://www.etaktik.cz/export/heureka.php`), trh `CZ`, interval 24 h, zapnúť „Automaticky“.
Tlačidlo „Synchronizovať teraz“ požiada worker o okamžitý beh (do pol minúty). Každý beh stiahne feed jednou
požiadavkou, pridá nové produkty, aktualizuje ceny, URL, obrázky, kategórie a dostupnosť existujúcich a pod
výsledkom vypíše položky na ručnú kontrolu. Postup a výsledky prvého načítania: [docs/ETAKTIK_IMPORT.md](docs/ETAKTIK_IMPORT.md).

**Balíčky.** Produkt môže byť označený ako balíček (viac titulov predávaných spolu). Zoznam produktov ich
predvolene nezobrazuje; filter „Balíčky“ ponúka „Bez balíčkov“, „Iba balíčky“ a „Všetky“. Import a synchronizácia
označia ako balíček produkt, ktorého názov obsahuje „balíček“ (Balíček pro 1. ročník, Výhodný balíček …,
Koumákův balíček …); ručne nastavený príznak (prepínač vo formulári produktu) import nikdy neprepíše.

**Import produktov zo súboru.** Produkty → Import: CSV (UTF-8 aj Windows-1250), Excel .xlsx alebo XML feed
(Heureka, Google Merchant). Povinný stĺpec `name`; ďalej `price`, `market`, `currency`, `isbn`, `ean`, `sku`,
`internal_id` (alebo `ID`), `url`, `brand`, `category`, `availability`, `priority`. Rozpozná aj export z vlastného
systému (Název, ISBN, EAN, Cena Eshop, Druh produktu, Ročníky, Předměty, Rok uvedení na trh). Pre riadky bez meny
sa vyberie trh. Najprv sa zobrazí náhľad so stavom každého riadku (nový, aktualizácia, bez zmeny, na kontrolu,
duplicita, chyba); uloží sa až po potvrdení.

Pravidlá párovania a aktualizácie (feed aj súbor):
- Existujúci produkt sa hľadá podľa interného ID, SKU, ISBN/EAN a URL. Samotný názov stačí iba vtedy, keď sa zhoduje
  aj cena na danom trhu a produkty si neodporujú v identifikátoroch; inak riadok čaká na ručnú kontrolu (doplňte
  produktu interné ID alebo ISBN a import zopakujte). Rovnaký názov s iným ISBN je iné vydanie, teda nový produkt.
- Identifikátory sa iba dopĺňajú, nikdy neprepisujú. Ostatné údaje prepíše iba zdroj aspoň taký dôveryhodný, ako
  ten, ktorý ich uložil: feed e-shopu > importovaný súbor > ručne zadané pred prvým importom. Súbor teda údaje
  z feedu iba doplní. ID produktu, jeho ponuky konkurentov a cenová história ostávajú.
- Neplatné ISBN/EAN sa ignorujú s upozornením. Cena 0 sa neukladá (produkt je bez ceny); riadok bez ceny a bez URL,
  ktorý nezodpovedá existujúcemu produktu, sa odmietne. Opakovaný import nič nezduplikuje.

**Manuálna kontrola.** V zozname ponúk tlačidlo „Skontrolovať teraz“ (administrátor). Výsledok sa ohlási,
priebeh je v Aktualizácie a logy.

**Frekvencia aktualizácií.** Poradie: frekvencia ponuky → frekvencia konkurenta → priorita produktu
(Nastavenia → Automatické aktualizácie: strategické 8 h, bežné 24 h, stabilné 120 h) → 24 h. V Nastaveniach je
aj časové okno (napr. iba 01:00–05:00 miestneho času) a vypínač automatických kontrol. Pozastavený konkurent,
ponuka alebo produkt sa automaticky nekontroluje.

**Upozornenia.** Pravidlá v Nastaveniach (pokles ceny o X %, lacnejší konkurent, nedostupnosť, neaktuálna cena,
opakované zlyhanie); upozornenia sú pod zvončekom v hlavičke a na Dashboarde.

**Používatelia.** Nastavenia → Používatelia (administrátor). Roly: administrátor (všetko) a čitateľ (iba
prezeranie). Každý si v Nastaveniach zmení vlastné heslo; zmena hesla alebo deaktivácia ukončí existujúce
prihlásenia daného používateľa.

## Nasadenie na Railway

Služby: `DashboardCompetition` (aplikácia z tohto repozitára) a PostgreSQL (`Postgres-Gcn3`).

1. Railway nájde `Dockerfile` v koreni repozitára sám. `railway.json` dopĺňa health check `/readyz` a reštart pri
   zlyhaní; Railway takéto súbory číta iba **do 1. 12. 2026** – do tohto dátumu nastavte health check `/readyz`
   v službe → Settings → Deploy (alebo `railway config migrate` + `cutover`).
2. Premenné služby: `DATABASE_URL` = `${{Postgres-Gcn3.DATABASE_URL}}`, `SECRET_KEY`, `ADMIN_EMAIL`,
   `ADMIN_PASSWORD`.
3. Settings → Networking → doména s cieľovým portom **8080**.
4. **Aktualizácia:** push do `main`. Automatický deploy z GitHubu je na tejto službe nedostupný („Auto deploy
   unavailable“ – GitHub účet prepojený s Railway nemá prístup k repozitáru), preto po pushi spustite
   `railway redeploy --service DashboardCompetition --from-source --yes` (alebo v Railway „Deploy Latest Commit“).
5. Po nasadení skontrolujte `/readyz` (`{"status":"ok"}`), Deploy Logs („Worker started“) a stránku
   Aktualizácie a logy.

Náklady: Free plán má kredit $1 mesačne; spotrebu sledujte v Railway → Usage (odhad v pláne, kapitola 6.2).

## Diagnostika bežných problémov

| Príznak | Príčina a riešenie |
|---|---|
| „Application failed to respond“ | Port domény sa nezhoduje s portom aplikácie (8080) alebo aplikácia nenaštartovala – pozrite Deploy Logs. |
| Deploy padá na health checku | `/readyz` vráti 503, keď nie je dostupná databáza: skontrolujte `DATABASE_URL`. |
| Aplikácia nenaštartuje s „SECRET_KEY must be set“ | V produkcii chýba `SECRET_KEY` (min. 32 znakov). |
| Push sa neprejavil | Pozri bod 4 nasadenia – spustite redeploy z CLI. |
| Ceny sa neaktualizujú | Aktualizácie a logy: Scheduler „vypnutý“ / „mimo okna“? Worker „nereaguje“? Konkurent alebo ponuka pozastavená? |
| Kontrola zlyháva „robots.txt zakazuje…“ | Web načítanie zakazuje; nesledujte ho. |
| „Web vyžaduje overenie proti botom“ | Web chráni napr. Cloudflare; ochrana sa neobchádza, web je nepodporovaný. |
| „Cena sa nenašla… web mohol zmeniť štruktúru“ | Upravte selektory alebo parser ([SCRAPER_GUIDE](docs/SCRAPER_GUIDE.md)); posledná platná cena zostáva. |
| HTTP 429 | Web žiada spomaliť: zvýšte rozostup požiadaviek konkurenta; aplikácia čaká podľa Retry-After. |
| Porovnanie „Bez porovnania“ | Chýba vlastná cena pre trh konkurenta, líši sa mena alebo známy režim DPH. |
| Po nasadení sa všetci musia znova prihlásiť | Zmenil sa `SECRET_KEY` alebo pribudla verzia relácie (migrácia 0006) – očakávané. |

## Zálohovanie a obnova

Postup je overený (záloha a obnova katalógu s 2 000 produktmi a 18 000 záznamami histórie sa zhodovali).
`pg_dump` musí mať aspoň verziu servera – Railway používa PostgreSQL 18:

```bash
brew install postgresql@18          # klient verzie 18 (keg-only)
PG=/opt/homebrew/opt/postgresql@18/bin
```

1. V Railway → `Postgres-Gcn3` → Settings → Networking zapnite **TCP Proxy** (vznikne verejná adresa
   `DATABASE_PUBLIC_URL`). Databáza je tým dostupná z internetu, preto proxy po zálohe vypnite.
2. Záloha: `$PG/pg_dump --format=custom --no-owner --no-privileges --file=zaloha-$(date +%F).dump "<DATABASE_PUBLIC_URL>"`
3. Obnova do prázdnej databázy: `$PG/pg_restore --no-owner --no-privileges --dbname="<URL cieľovej databázy>" zaloha.dump`
   (do existujúcej databázy s prepísaním: pridajte `--clean --if-exists`; predtým zastavte aplikáciu).
4. Vypnite TCP Proxy. Zálohy uchovávajte mimo počítača (šifrované úložisko).

Ak váš plán Railway ponúka zálohy databázovej služby (záložka Backups), zapnite ich ako doplnok.

## Testy

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/pytest            # databáza competition_test
cd frontend && npm test && npx tsc -b
cd frontend && npx playwright install chromium && npm run build && npm run e2e   # databáza competition_e2e
```

E2E testy nenačítavajú žiadny skutočný web: pracujú s lokálnym falošným obchodom (uložené stránky alter.cz)
a v testovacom režime fetcher odmietne každú inú doménu. Všetky tri sady bežia aj v GitHub Actions.
