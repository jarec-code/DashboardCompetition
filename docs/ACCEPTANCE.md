# Overenie akceptačných kritérií (kap. 19 zadania)

Overené 19. 10. 2026 na https://webindex.up.railway.app (nasadenie `249fefe2`, commit
`9eb1d10`, migrácia `0006`) a automatizovanými testami toho istého commitu.

Produkčné dáta v čase overenia: 1 konkurent (Alter, alter.cz, parser `alter_cz`), 1 sledovaná ponuka
(`/produkt/716` – Průvodce učebnicí prvouky, 65,00 CZK, skladom) priradená k vlastnému produktu Hravá prvouka 1
(189,00 CZK), 3 úlohy (test, manuálna, plánovaná), 2 behy, 1 otvorené upozornenie.

Testy: backend 321 passed (pytest proti PostgreSQL), frontend Vitest 4 passed, `tsc` bez chýb, e2e Playwright
28 passed (lokálne s falošným obchodom z uložených stránok alter.cz), CI na GitHub Actions `success`.

**Legenda:** ✅ P = overené v produkcii · ✅ T = overené automatizovanými testami (v produkcii zatiaľ nenastala
situácia alebo zápis nebol povolený).

| # | Kritérium | Výsledok | Dôkaz |
|---|---|---|---|
| 1 | Prihlásenie do administrácie | ✅ P | `POST /api/auth/login` 200, `/api/auth/me` vracia administrátora; bez prihlásenia každá z 53 chránených API operácií vráti 401/403 (`test_security.py`). |
| 2 | Vytvoriť, upraviť, pozastaviť, archivovať konkurenta | ✅ P (vytvorenie, nastavenia scrapingu) · ✅ T (úprava, pozastavenie, archivácia) | Alter bol vytvorený a nastavený v produkcii (parser `alter_cz`, rozostup 5 s). Úprava, pozastavenie a archivácia: `test_competitors.py`, e2e `01-competitors`. Zápisový test v produkcii pri tomto overení nebol povolený. |
| 3 | Pridať produktovú URL a priradiť k vlastnému produktu | ✅ P | Ponuka `/produkt/716` pridaná a ručne priradená (`matching_method: manual`, `match_status: matched`). |
| 4 | Scraper získa cenu, menu a dostupnosť | ✅ P | Testovacia, manuálna aj plánovaná kontrola alter.cz: 65,00 CZK, `in_stock`. |
| 5 | Údaje sa uložia do PostgreSQL | ✅ P | `current_prices`, `price_history`, `scrape_jobs`, `scrape_runs` v Railway PostgreSQL 18 (čítané cez API). |
| 6 | Zmena ceny vytvorí nový historický záznam | ✅ P (prvý záznam) · ✅ T (zmena) | Prvá kontrola vytvorila záznam `initial`. Skutočná zmena ceny na alter.cz zatiaľ nenastala; zmena ceny, akcie a dostupnosti: `test_scrape_jobs.py`, `test_history.py`, e2e `03-scraping`, `06-history-alerts`. |
| 7 | Nezmenená cena nevytvorí duplicitný záznam | ✅ P | Dve úspešné kontroly (19:40 manuálna, 19:58 plánovaná) → v histórii jediný záznam; beh 2 má `price_change_count: 0`. |
| 8 | Chyba scrapingu neprepíše poslednú platnú cenu | ✅ T | V produkcii zatiaľ žiadna chyba (`failures_24h: 0`). Testy: zlyhanie po úspechu ponechá cenu a nastaví `last_error` a `consecutive_failures` (`test_scrape_jobs.py`, e2e `03-scraping`). |
| 9 | Scheduler vytvára úlohy podľa intervalu | ✅ P | Úloha 3 typu `scheduled` vytvorená schedulerom; `next_check_at` podľa frekvencie 24 h; scheduler beží každú minútu (`scheduler_last_tick_at`), okno 01:00–05:00. |
| 10 | Pozastavené weby sa automaticky nespracúvajú | ✅ T | `test_scheduler.py` (pozastavený konkurent, pozastavená ponuka, pozastavené sledovanie produktu, vypnutý scheduler a časové okno). |
| 11 | Používateľ vidí posledné výsledky a chyby | ✅ P · ✅ T (chyby) | Aktualizácie a logy: stav schedulera a workera, behy, úlohy; v Konkurencii posledný úspech a počet chýb. Zobrazenie chýb: `test_scrape_jobs.py`, e2e `03-scraping`. |
| 12 | Nasadenie z GitHubu na Railway podľa dokumentácie | ✅ P s poznámkou | Docker build z repozitára, migrácie pri štarte, `/readyz` 200. Automatický deploy po pushi je na službe nedostupný, nasadzuje sa `railway redeploy --from-source` (README, Nasadenie na Railway). |
| 13 | Prevádzka bez AI API a tokenov | ✅ P | Žiadna AI knižnica v `requirements.txt` ani `package.json`, žiadna AI premenná v Railway. |
| 14 | Testy a migrácie v repozitári | ✅ P | `backend/tests`, `frontend/src/**/*.test.tsx`, `frontend/e2e`, `backend/alembic/versions/0001–0006`, CI workflow. |

## Bezpečnosť a výkon (etapa 9)

- Bezpečnostné hlavičky v produkcii: CSP (`script-src 'self'`, `frame-ancestors 'none'`), HSTS, `X-Frame-Options: DENY`,
  `nosniff`, `Permissions-Policy`. Neočakávané chyby vracajú iba všeobecnú správu.
- Kontrola závislostí: `pip-audit` a `npm audit` bez nálezov.
- Zmena hesla, reset alebo deaktivácia používateľa ukončí jeho ostatné prihlásenia.
- Výkon (lokálne, 2 000 produktov, 6 000 ponúk): porovnanie ~330 ms na stránku, vyhodnotenie upozornení 0,24 s
  (predtým 7,8 s).
- Záloha `pg_dump` a obnova `pg_restore` overené lokálne (počty riadkov sa zhodovali); postup v README.

## Otvorené body

- Kritériá 2 (úprava/pozastavenie/archivácia), 8 a 10 si môžete overiť v produkcii ručne: dočasný konkurent
  s doménou `example.com` → upraviť → pozastaviť → archivovať; ponuku a konkurenta potom zmazať.
- Skutočnú zmenu ceny (kritérium 6) a chybu (kritérium 8) potvrdí prevádzka; uvidíte ich v Cenovej histórii
  a v Aktualizáciách a logoch.
- Health check `/readyz` nastaviť v Railway dashboarde pred 1. 12. 2026 (konfigurácia `railway.json` končí).
