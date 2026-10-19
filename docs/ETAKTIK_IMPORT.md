# Evidencia vlastných produktov z etaktik.cz

Stav k 19. 10. 2026. Popisuje zdroje dát, spôsob importu a výsledky prvého načítania.

## Zdroje dát

| Zdroj | Formát | Obsah | Použitie |
|---|---|---|---|
| `https://www.etaktik.cz/export/heureka.php` | Heureka XML | 1 229 produktov: ID, názov, URL, obrázok, cena s DPH, EAN/ISBN (726 vyplnených), výrobca, kategória | **hlavný zdroj**, pravidelná synchronizácia |
| `https://www.etaktik.cz/export/google.php` | CSV (napriek názvu nie XML) | 1 217 produktov, rovnaké ID, ceny a URL ako Heureka; dostupnosť (všetky „in stock“), bez GTIN | doplnenie dostupnosti (jednorazovo, cez Import) |
| `https://www.etaktik.cz/sitemap.xml` → `/export/sitemap.php` | sitemap | 1 589 URL: všetkých 1 229 produktových URL z feedu + kategórie a informačné stránky | iba kontrola úplnosti feedu |
| `EXPORT_PRODUCTS_1792395535.csv` (priložený) | CSV, `;`, UTF-8 s BOM | 820 riadkov: ID, názov, ISBN, EAN, druh produktu, ročníky, predmety, rok, kategórie, cena e-shopu | doplnkový zdroj (parametre, ISBN) |

Zistenia:
- robots.txt povoľuje všetko okrem `/kosik/` a náhľadov; feedy sú verejné. Na analýzu stačilo 8 požiadaviek
  (robots, sitemap, feedy, jedna produktová stránka), produktové stránky sa hromadne nesťahovali, lebo feed obsahuje
  všetky produkty zo sitemapy.
- ID v CSV = `ITEM_ID` vo feede. Ceny a identifikátory sa v 737 spoločných produktoch zhodujú úplne; 20 názvov sa
  líši iba dvojitým kódovaním `&amp;` vo feede (import ho opravuje).
- 243 položiek feedu má cenu 0 (interaktívne licencie „na 1 rok“, katalógy, formuláre). Produktová stránka cenu
  nezobrazuje, preto sa ukladajú **bez ceny** a v porovnaní sa nezobrazujú.
- 22 položiek má v poli EAN krátky interný kód (napr. `0201`, `0031`), ktorý nie je platný EAN ani ISBN. Ignoruje sa
  s upozornením; ako SKU sa neukladá, lebo jeho význam nie je overený.
- Položka 30 má dva elementy EAN, prvý neplatný; import použije platný.
- 83 riadkov CSV nie je na webe: 75 má cenu 0 (archív, prílohy, pracovné listy z webinárov, katalógy) a odmietnu sa
  ako neoveriteľné nepredajné položky; 8 má cenu (Let's Rock 1 a 2, magnetické organizéry) a uložia sa so zdrojom
  „EXPORT_PRODUCTS_….csv“ a bez URL, takže ich overiteľnosť je zrejmá.
- Cena bez DPH sa neodvodzuje: sadzba DPH sa medzi knihami, pracovnými zošitmi a pomôckami líši a zdroje ju neuvádzajú.
  Ceny sa ukladajú s príznakom „s DPH“.

## Uložené údaje produktu

Názov, interné ID (ID e-shopu), ISBN, EAN, URL v e-shope, cena a mena (trh CZ, s DPH), kategória podľa e-shopu,
vydavateľ, dostupnosť, URL obrázka, parametre (druh, ročník, predmet, rok vydania), zdroj (`etaktik.cz (feed)`
alebo názov súboru) a čas posledného úspešného načítania. Pribudli stĺpce `url`, `image_url`, `availability`,
`source_kind`, `source`, `source_synced_at` (migrácia `0007`).

## Výsledok skúšobného načítania (lokálna databáza, reálne súbory)

Poradie: feed → Google CSV → priložené CSV, potom ešte raz všetko (kontrola idempotencie).

| Krok | Spracované | Nové | Aktualizované | Bez zmeny | Na kontrolu | Odmietnuté |
|---|---|---|---|---|---|---|
| Heureka feed | 1 229 | 1 229 (z toho 262 s upozornením) | 0 | 0 | 0 | 0 |
| Google CSV | 1 217 | 0 | 1 217 (dostupnosť) | 0 | 0 | 0 |
| Priložené CSV | 820 | 8 | 732 (parametre, chýbajúce ISBN) | 5 | 0 | 75 |
| 2. beh, všetky tri | 3 266 | 0 | 0 | 3 191 | 0 | 75 |

Výsledok: **1 237 produktov** (1 229 z webu, 8 iba zo súboru), 0 duplicít. 1 229 má URL, 659 ISBN, 52 EAN, 1 217
dostupnosť „skladom“, 243 je bez ceny. Spracovanie celého feedu trvá menej ako sekundu.

Simulácia s 5 produktmi, ktoré sú teraz v produkcii (zadané ručne, iba názov a cena): všetkých 5 sa spáruje podľa
názvu a rovnakej ceny a doplní sa im interné ID, ISBN, URL, obrázok a kategória. ID produktu ostáva, takže ponuka
z alter.cz a jej história ostanú priradené.

## Načítanie v produkcii ✅ 19. 10. 2026

| Krok | Spracované | Nové | Aktualizované | Bez zmeny | Na kontrolu | Odmietnuté |
|---|---|---|---|---|---|---|
| Synchronizácia feedu (worker, 22 s od požiadavky) | 1 229 | 1 224 | 5 (pôvodné ručne zadané produkty) | 0 | 0 | 0 |
| Priložené CSV | 820 | 8 | 732 | 5 | 0 | 75 |
| Google CSV (`/export/google.php`) | 1 217 | 0 | 1 217 (dostupnosť) | 0 | 0 | 0 |

Spolu **1 237 produktov**. Pôvodný produkt #4 (Hravá prvouka 1 – metodická příručka, rev. RVP ZV) si ponechal ID,
dostal interné ID 3965, ISBN, URL a parametre; ponuka z alter.cz a upozornenie „Konkurent je lacnejší o 65,6 %“
ostali. Automatická synchronizácia je zapnutá (raz za 24 h).

Postup pre opätovné načítanie (zápis robí administrátor v aplikácii):
1. Nastavenia → Vlastné produkty z feedu e-shopu: adresa `https://www.etaktik.cz/export/heureka.php`, trh CZ,
   interval 24 h, zapnúť „Automaticky podľa intervalu“, Uložiť, **Synchronizovať teraz**.
2. Produkty → Import: `EXPORT_PRODUCTS_1792395535.csv`, trh CZ → skontrolovať náhľad → Uložiť (doplní parametre
   a 8 produktov, ktoré nie sú na webe).
3. Voliteľne Produkty → Import: CSV z `https://www.etaktik.cz/export/google.php` (doplní dostupnosť).

Ďalej sa katalóg aktualizuje sám raz denne. Položky na kontrolu (nejednoznačná zhoda) sú vypísané v Nastaveniach pod
výsledkom posledného behu.
