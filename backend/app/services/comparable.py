"""Matching competitor catalogue items to *comparable* own products, by rules (no AI).

Competitors sell their own titles under their own ISBNs, so an item is matched by what it is:
grade (ročník), subject (predmet), type (učebnica, pracovný zošit, …), part (díl) and whether it is a bundle.
Levels (docs/AUTO_DISCOVERY_PLAN.md, part D):
  1. the same ISBN/EAN as one of our products          -> matched automatically ("identifier")
  2. grade, subject, type and part agree with exactly
     one of our products (editions of one title count once,
     the revised edition wins)                         -> matched automatically ("attributes")
  3. grade and subject agree, the rest is unclear     -> review, with up to five candidates
  4. nothing comparable                               -> unrelated (not tracked)
"""
import re
import unicodedata
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product
from app.services.identifiers import gtin_keys
from app.services.matching import name_similarity

MAX_CANDIDATES = 5

# Canonical subject -> words (without diacritics, lowercase) that reveal it.
SUBJECTS = {
    "cestina": ("cestin", "cesky jazyk", "ceskeho jazyk", "ceskemu jazyk", "ceskym jazyk", "citan", "cteni",
                "ctenar", "slabik", "abeced", "pismen", "pravopis", "sloh", "literatur", "literarni", "vyjmenovan",
                "slovensky jazyk", "slovencin", "pisank", "psani", "mluvnic", "jazykova vychova", "komunikacni",
                "diktat"),
    "matematika": ("matemati", "pocitan", "pocitame", "pocetnik", "aritmeti", "geometri", "priklad", "cisla",
                   "nasobil", "zlomk"),
    "prvouka": ("prvouk", "clovek a jeho svet", "prvoucky"),
    "vlastiveda": ("vlastived", "nase vlast", "dejiny ceske"),
    "prirodoveda": ("prirodoved",),
    "prirodopis": ("prirodopis",),
    "dejepis": ("dejepis",),
    "zemepis": ("zemepis",),
    "fyzika": ("fyzik",),
    "chemie": ("chemi",),
    "anglictina": ("anglict", "anglick", "english"),
    "nemcina": ("nemcin", "nemeck"),
    "rustina": ("rustin", "rusk"),
    "hudebni": ("hudebni", "hudba"),
    "vytvarna": ("vytvarn",),
    "informatika": ("informati",),
    "obcanska": ("obcansk", "obcank"),
}
# Canonical type -> words. Order matters: the first match wins ("pracovní učebnice" before "učebnice").
TYPES = (
    ("metodika", ("metodick", "pruvodce", "prirucka pro ucitel", "pro ucitele", "metodika")),
    ("pracovni_ucebnice", ("pracovni ucebnic",)),
    ("pracovni_sesit", ("pracovni sesit", "pracovnim sesit", " ps ", "sesit")),
    ("pracovni_listy", ("pracovni list",)),
    ("pisanka", ("pisank",)),
    ("citanka", ("citank", "citance")),
    ("slabikar", ("slabikar",)),
    ("abeceda", ("abeced",)),
    ("ucebnice", ("ucebnic",)),
)
# Types told by the title itself win over a generic type from our product export ("Hravý slabikář" is
# exported as "Pracovní sešit", but it compares with other publishers' primers).
TITLE_TYPES = ("metodika", "pisanka", "citanka", "slabikar", "abeceda")
BUNDLE = re.compile(r"\b(komplet|balic|sada)", re.I)
REVISED = re.compile(r"\s*[-–—]?\s*(revidovan[eé]|rev\.)\s*RVP(\s*ZV)?\s*$", re.I)
_GRADE = re.compile(r"(\d)\s*\.?\s*(?:-\s*(\d)\s*\.?\s*)?(?:rocnik|trid)")  # on plain() text
_PART = re.compile(r"(\d)\s*\.\s*dil|dil\s*(\d)")
# Pupils of a grade: "Barevná čeština pro prvňáčky", "… pro třeťáky".
_PUPILS = {"prvnack": "1", "prvnak": "1", "druhak": "2", "tretak": "3", "ctvrtak": "4", "patak": "5",
           "sestak": "6", "sedmak": "7", "osmak": "8", "devatak": "9"}
_PUPIL = re.compile(r"\b(" + "|".join(_PUPILS) + ")")


def plain(text: str | None) -> str:
    text = re.sub(r"[–—]", "-", text or "")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " " + " ".join(re.sub(r"[^a-z0-9.,-]+", " ", text).split()) + " "


@dataclass
class Profile:
    grades: frozenset[str] = frozenset()   # {"1"}, {"6", "7", "8", "9"}, {"MS"}, {"SS"}
    subjects: frozenset[str] = frozenset()
    type: str | None = None
    parts: frozenset[str] = frozenset()
    bundle: bool = False
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"grades": sorted(self.grades), "subjects": sorted(self.subjects), "type": self.type,
                "parts": sorted(self.parts), "bundle": self.bundle}


def _subject_number() -> re.Pattern:
    """A grade as a number right after the subject: "Barevná čeština 1", "Prvouka 2 pro ZŠ", "Přírodopis 6",
    "Český jazyk 9, 1. díl" (not a part "3. díl", a level "2. stupeň" or a range "1-5")."""
    stems = sorted({re.escape(w) for words in SUBJECTS.values() for w in words}, key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(stems) + r")[a-z]*\s+([1-9])(?![\d.]|-\d)"
                      r"(?!\s*(?:dil|stupen|cast|svazek|pololet))")


def _grades(text: str) -> frozenset[str]:
    found: set[str] = set()
    found.update(_SUBJECT_NUMBER.findall(text))
    found.update(_PUPILS[m] for m in _PUPIL.findall(text))
    for first, last in _GRADE.findall(text):
        end = int(last) if last else int(first)
        found.update(str(g) for g in range(int(first), max(int(first), end) + 1))
    if re.search(r"\b(ms|materska|predskol)", text):
        found.add("MS")
    if re.search(r"\b(ss|stredni skol|gymnazi|sou|sos|maturit)", text):
        found.add("SS")
    return frozenset(found)


_SUBJECT_NUMBER = _subject_number()


def _subjects(text: str) -> frozenset[str]:
    return frozenset(s for s, words in SUBJECTS.items() if any(w in text for w in words))


def _type(text: str) -> str | None:
    """Type from the head of the title: "PRACOVNÍ SEŠIT k Čítance" is a workbook, "KLÍČ k učebnici" no type."""
    head = re.split(r"\s(?:k|ke|ku|k\.)\s", text, maxsplit=1)[0] + " "
    return next((t for t, words in TYPES if any(w in head for w in words)), None)


def _parts(text: str) -> frozenset[str]:
    parts = {a or b for a, b in _PART.findall(text)}
    both = re.search(r"(\d)\s*\.\s*a\s*(\d)\s*\.\s*dil", text)
    if both:
        parts.update(both.groups())
    return frozenset(parts)


def profile_of_item(title: str | None, hints: dict | None) -> Profile:
    hints = hints or {}
    text = plain(" ".join(filter(None, [title, hints.get("grade"), hints.get("category")])))
    grades = _grades(plain(hints.get("grade"))) or _grades(text)
    return Profile(grades=grades, subjects=_subjects(plain(title)) or _subjects(text), type=_type(plain(title)),
                   parts=_parts(text), bundle=bool(BUNDLE.search(title or "")))


def profile_of_product(product: Product) -> Profile:
    a = product.attributes or {}
    name = plain(product.name)
    grades = _grades(plain(a.get("grade"))) or _grades(plain(product.category)) or _grades(name)
    if not grades:  # "Hravá matematika 6", "Barevné příklady 1": the number after the series name
        number = re.search(r"\s([1-9])\s", re.sub(r"\d\s*\.\s*dil", " ", name))
        grades = frozenset({number.group(1)}) if number else frozenset()
    subjects = _subjects(plain(a.get("subject"))) or _subjects(name) or _subjects(plain(product.category))
    from_name = _type(name)
    kind = from_name if from_name in TITLE_TYPES else _type(plain(a.get("type"))) or from_name
    return Profile(grades=grades, subjects=subjects, type=kind, parts=_parts(name), bundle=bool(product.is_bundle))


@dataclass
class Decision:
    status: str                  # matched / review / unrelated
    method: str | None = None    # identifier / attributes
    product_id: int | None = None
    candidates: list[dict] = field(default_factory=list)
    note: str | None = None


class OwnCatalogue:
    """Our products and their profiles, loaded once per discovery run."""

    def __init__(self, db: Session):
        # Only products with a price can be compared (free licences, bookmarks of price 0 cannot).
        self.products = [p for p in db.scalars(select(Product).where(Product.tracking_status == "active")) if p.prices]
        self.profiles = {p.id: profile_of_product(p) for p in self.products}
        self.by_gtin: dict[str, list[Product]] = {}
        for p in self.products:
            for code in gtin_keys(p.isbn, p.ean):
                self.by_gtin.setdefault(code, []).append(p)

    def decide(self, title: str | None, hints: dict | None, identifiers: dict | None) -> tuple[Decision, Profile]:
        item = profile_of_item(title, hints)
        codes = gtin_keys((identifiers or {}).get("isbn"), (identifiers or {}).get("ean") or
                          (identifiers or {}).get("gtin"))
        by_code = {p.id: p for code in codes for p in self.by_gtin.get(code, [])}
        if len(by_code) == 1:
            product = next(iter(by_code.values()))
            return Decision("matched", "identifier", product.id, note="Zhoda ISBN/EAN."), item
        if not item.grades or not item.subjects:
            return Decision("unrelated", note="Nepodarilo sa rozpoznať ročník a predmet."), item

        comparable = []
        for product in self.products:
            own = self.profiles[product.id]
            if own.bundle != item.bundle or not own.grades or not (item.grades <= own.grades):
                continue
            if not own.subjects or not (item.subjects & own.subjects):
                continue
            if item.type and own.type and item.type != own.type:
                continue  # a primer is not comparable with a workbook
            exact = (own.grades == item.grades and own.subjects == item.subjects and item.type is not None
                     and own.type == item.type and own.parts == item.parts)
            comparable.append((exact, name_similarity(title or "", product.name), product))
        if not comparable:
            return Decision("unrelated", note="Žiadny vlastný produkt pre tento ročník a predmet."), item
        comparable.sort(key=lambda c: (not c[0], -c[1], c[2].name))
        exact = [p for is_exact, _, p in comparable if is_exact]
        chosen = _one_title(exact)
        candidates = [{"product_id": p.id, "name": p.name, "exact": e, "score": round(s, 3)}
                      for e, s, p in comparable[:MAX_CANDIDATES]]
        if chosen is not None:
            note = "Zhoda ročníka, predmetu, typu a dielu."
            if len(exact) > 1:
                note += " Z vydaní toho istého titulu bolo zvolené revidované."
            return Decision("matched", "attributes", chosen.id, candidates, note), item
        reason = ("Viac porovnateľných produktov." if len(exact) > 1 else
                  "Typ alebo diel sa nepodarilo jednoznačne porovnať.")
        return Decision("review", candidates=candidates, note=reason), item


def _one_title(products: list[Product]) -> Product | None:
    """The single product, or the revised edition when all candidates are editions of one title."""
    if len(products) == 1:
        return products[0]
    if not products:
        return None
    bases = {plain(REVISED.sub("", p.name)) for p in products}
    if len(bases) != 1:
        return None
    revised = [p for p in products if REVISED.search(p.name)]
    return revised[0] if len(revised) == 1 else None
