# Plán: automatické objavovanie a párovanie produktov konkurentov

Stav: **návrh na schválenie** (19. 10. 2026). Nič z tohto plánu ešte nie je implementované.

## Cieľ

Po pridaní konkurenta má aplikácia bez ďalšieho zásahu sama:
1. zistiť, kde web konkurenta zverejňuje zoznam produktov,
2. priebežne nachádzať nové produkty a zistiť, ktoré zmizli,
3. spárovať ich s porovnateľnými vlastnými produktmi,
4. sledovať ceny iba spárovaných produktov.

Platí to pre alter.cz aj pre každého ďalšieho konkurenta, ktorého administrátor pridá ručne. Rýchlosť je druhoradá,
prednosť má bezpečnosť a minimálne riziko pre web konkurenta.

## Zásady bezpečnosti (platia pre každý krok)

- Iba verejné stránky a iba čítanie: žiadne prihlásenie, košík, formuláre, vyhľadávanie ani parametre, ktoré menia stav.
- robots.txt sa rešpektuje vrátane `Crawl-delay`. Aplikácia sa identifikuje vlastným User-Agentom a nikdy neobchádza
  CAPTCHA ani ochranu proti botom.
- Jedna požiadavka naraz na doménu. Pri objavovaní je rozostup **aspoň 10 s** (dlhší ako pri kontrole cien), beh je
  iba v nočnom okne a po kontrolách cien.
- Každý konkurent má **denný rozpočet požiadaviek na objavovanie** (predvolene 120). Objavovanie sa tým rozloží
  na viac nocí.
- **Poistka:** pri 3 chybách za sebou, HTTP 429/403 alebo stránke s výzvou proti botom sa objavovanie konkurenta
  zastaví na 7 dní. Administrátor dostane upozornenie a kontroly cien bežia ďalej.
- **Ochrana pred zmenou štruktúry webu:** keď zdroj zrazu vráti výrazne menej produktov (napr. pod 50 % minulého
  behu), nič sa neoznačí ako zmiznuté. Beh sa zastaví a príde upozornenie.
- **Podmienky webu:** pri zapnutí objavovania administrátor jedným potvrdením deklaruje, že ich skontroloval.
  Je to jediný zámerne ručný krok a je právny, nie technický.
- Žiadna AI v bežnej prevádzke. Voliteľná AI (etapa 11) iba pre nejednoznačné prípady.

## Ako to bude fungovať pre nového konkurenta

```
Administrátor: pridá konkurenta (názov, URL, krajina) + potvrdí podmienky webu
        │
        ▼  najbližšia noc, automaticky
1. Rozpoznanie zdrojov   robots.txt → feed → sitemap → stránky zoznamu → adaptér webu
2. Vzor produktovej URL  zo vzorky stránok s JSON-LD Product / cenou (napr. /produkt/<číslo>)
3. Skúšobné načítanie    5 produktov existujúcim parserom („Automaticky“): cena, mena, EAN/ISBN, názov
        │  ak je výsledok v poriadku
        ▼
4. Tieňový režim         objavuje a páruje, ale nič nesleduje; výsledok v prehľade (1–2 behy)
        │  ak sú splnené kvalitatívne podmienky
        ▼
5. Automatický režim     spárované produkty sa začnú sledovať, zmiznuté sa archivujú, nové sa dopĺňajú týždenne
```

Administrátor zasiahne iba vtedy, keď niečo nevyjde a dostane upozornenie. Napríklad web vyžaduje JavaScript
(potom pomôže etapa 10) alebo blokuje automatické prístupy (web sa označí ako nepodporovaný).

## Etapy

### A. Katalóg konkurenta (dátový základ)
- Nová tabuľka **nájdených položiek** konkurenta, oddelená od sledovaných ponúk. Obsahuje URL, názov, cenu zo zoznamu,
  EAN/ISBN, rozpoznané parametre (ročník, predmet, typ, diel), stav (nová, načítaná, spárovaná, na kontrolu,
  nesúvisiaca, zmiznutá), kedy bola položka prvý a naposledy videná a výsledok párovania.
- Sledovaná ponuka vznikne až zo spárovanej položky. Ceny sa každý deň kontrolujú len pri relevantných produktoch,
  takže denný počet požiadaviek ostane malý.
- Nastavenie konkurenta „Objavovanie produktov“: vypnuté / tieňové / automatické, zdroj, vzor URL, rozpočet,
  frekvencia (predvolene týždenne).

### B. Rozpoznanie zdrojov (raz pri pridaní, potom pri poruche)
Skúša sa v tomto poradí. Prvý funkčný zdroj vyhráva.
1. **Feed** (Heureka/Google): adresu môže vložiť administrátor; automaticky sa použije iba feed, na ktorý web sám
   odkazuje. Adresy sa nehádajú.
2. **Sitemap** z robots.txt alebo `/sitemap.xml` (aj indexy sitemáp), filtrované na vzor produktovej URL.
3. **Stránky zoznamu so stránkovaním:** z hlavnej stránky sa nájdu stránky s mnohými produktovými kartami
   (napr. alter.cz `/shop?page=0…11`). Prechádzajú sa iba v rámci domény a s limitom počtu stránok.
4. **Adaptér webu:** malý doplnok pre web s neštandardnou štruktúrou, rovnako ako parsery v `scraping/sites/`.

Vzor produktovej URL sa odvodí zo vzorky stránok, ktoré obsahujú JSON-LD `Product` alebo nájdenú cenu.
Administrátor ho vidí a môže upraviť.

### C. Pravidelné objavovanie
- Predvolene raz týždenne v nočnom okne prejde zdroj a porovná ho s katalógom: nové položky pribudnú, chýbajúce
  sa označia.
- Položka sa považuje za zmiznutú až po **dvoch** behoch, v ktorých chýba. Jej sledovaná ponuka sa potom archivuje
  a história ostáva.
- Nové položky sa raz načítajú existujúcim parserom: cena, mena, dostupnosť, EAN/ISBN, kategória a navigácia
  (breadcrumbs). Počet je obmedzený rozpočtom, napríklad 100 nových za noc.

### D. Automatické párovanie s porovnateľnými produktmi
Konkurenti predávajú vlastné tituly s iným ISBN, preto sa páruje podľa **porovnateľnosti**, nie podľa zhody.
- **Parametre konkurenta** sa rozpoznajú pravidlami z názvu, kategórie a navigácie:
  - ročník („1. ročník“, „pro 2. třídu“, „MŠ“),
  - predmet (slovník: čeština/čtení/písanie, matematika, prvouka, …),
  - typ (učebnica, pracovný zošit, písanka, čítanka, metodická príručka),
  - diel („1. díl“),
  - balíček („Komplet“, „Balíček“).
- **Naše parametre** už existujú: druh, ročník a predmet z exportu, príznak balíčka.
- **Úrovne istoty:**
  1. Zhoda **EAN/ISBN** → priradiť automaticky. Typické pri distribútoroch a marketplace, ktoré predávajú naše
     tituly.
  2. Zhodný ročník, predmet, typ aj diel a **práve jeden** náš kandidát → priradiť automaticky. Taká ponuka má
     značku „automaticky“ a dá sa jedným kliknutím vrátiť.
  3. Viac kandidátov alebo chýbajúci parameter → **Produkty → Párovanie** (na kontrolu). Ponuka sa nesleduje,
     kým ju niekto nepotvrdí.
  4. Žiadny kandidát → nesúvisiaca položka. Nesleduje sa a znova sa posúdi iba po pridaní nových vlastných
     produktov.
- Balíčky sa párujú iba s balíčkami. Naše balíčky sú predvolene vylúčené.
- Rozhodnutia človeka (priradenie, ignorovanie) majú vždy prednosť a automatika ich nemení.

### E. Tieňový režim a prechod do automatiky
- **Tieňový režim** (prvé 1–2 behy): objavuje a páruje, ale nič nepridáva do sledovania. Prehľad ukáže, čo by sa
  stalo: koľko položiek sa našlo, koľko sa spárovalo a s čím, koľko je na kontrolu.
- **Automatický prechod** nastane, ak sú splnené všetky podmienky:
  - aspoň 90 % skúšobných stránok malo cenu,
  - nenastalo žiadne blokovanie,
  - počet položiek je stabilný medzi behmi.
- Inak ostane v tieňovom režime a príde upozornenie. Administrátor môže režim kedykoľvek prepnúť ručne.

### F. Prehľad a upozornenia
- V detaile konkurenta záložka „Katalóg konkurenta“: nájdené položky, stav párovania, posledný beh, spotrebovaný
  rozpočet.
- Upozornenia:
  - týždenný súhrn „nové produkty konkurenta“,
  - zmiznuté sledované produkty,
  - zastavené objavovanie (blokovanie, zmena štruktúry),
  - položky čakajúce na kontrolu.

### G. Testy a overenie
- Falošný obchod pre testy rozšírený o sitemapu, feed, zoznam so stránkovaním, 429, výzvu proti botom a zmenu
  štruktúry.
- Testy poistky, rozpočtu, robots.txt (aj `Crawl-delay`), dvojbehového mazania a toho, že rozhodnutia človeka ostávajú.
- Overenie na alter.cz najprv v tieňovom režime a výsledok na schválenie.

## Príklad: alter.cz

| Krok | Požiadavky | Poznámka |
|---|---|---|
| Rozpoznanie zdrojov | ~5 | robots.txt nemá pravidlá, sitemap neexistuje (404), `/shop` má 12 stránok po 24 položkách (~290 produktov) |
| Týždenný prechod zoznamu | 12 | zoznam obsahuje názov, ročník a cenu; odkazuje na interné ID (`/cart/add_dialog/244`), nie na `/produkt/…` → adaptér zistí produktovú URL |
| Prvé načítanie produktov | ~290 | pri rozpočte 100/noc za 3 noci, 10 s rozostup ≈ 17 min za noc |
| Potom týždenne | 12 + nové | spravidla jednotky nových produktov |
| Denná kontrola cien | len spárované | odhad desiatky ponúk |

Úloha pre etapu B: overiť jednou požiadavkou, ako sa z položky zoznamu dostať na produktovú stránku. Nesmie sa to
zameniť s ID košíka, s čím sme mali problém pri prvom pridávaní ponúk.

## Odhad rozsahu

| Etapa | Obsah | Odhad |
|---|---|---|
| A | katalóg konkurenta, nastavenia, migrácia | 1–2 dni |
| B | rozpoznanie zdrojov, vzor URL, adaptér alter.cz | 2–3 dni |
| C | pravidelné objavovanie, rozpočet, poistka, zmiznuté položky | 2 dni |
| D | rozpoznanie parametrov, párovanie podľa porovnateľnosti | 2–3 dni |
| E | tieňový režim, automatický prechod | 1 deň |
| F | prehľad, upozornenia | 1–2 dni |
| G | testy, overenie na alter.cz | priebežne + 1 deň |

Prevádzkové náklady sa takmer nezmenia: rádovo desiatky až stovky požiadaviek za noc na konkurenta.

## Rozhodnutia na schválenie

1. **Automatické priradenie podľa parametrov (úroveň 2).** Odporúčam áno, so značkou „automaticky“ a možnosťou
   vrátiť. Alternatívou je posielať všetko okrem zhody ISBN/EAN na kontrolu.
2. **Frekvencia objavovania.** Odporúčam týždenne. Rozpočet 120 požiadaviek za noc a konkurenta, rozostup 10 s.
3. **Potvrdenie podmienok webu pri zapnutí objavovania.** Odporúčam ponechať ako jediný ručný krok.
4. **Tieňový režim.** Odporúčam ho pre každého nového konkurenta. Do automatiky sa prepne sám podľa podmienok v časti E.
5. **Poradie realizácie.** Odporúčam A → B → C → E (najprv objavovanie v tieňovom režime na alter.cz),
   potom D a F.
