"""Matching of competitor offers to our products, without AI.

Order: 1. exact ISBN/EAN (GTIN) or SKU, 2. normalized name similarity, 3. manual choice by a user.
Manual decisions (assigned or ignored) are never overwritten by the automatic matching. Ambiguous
results are not assigned; they wait in the review queue with candidates.
"""
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import CompetitorProduct, CurrentPrice, Product
from app.services.identifiers import gtin_keys, normalize_sku

AUTO_MATCH_SCORE = 0.9
REVIEW_SCORE = 0.6
MAX_CANDIDATES = 5


def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"(?<=\d)(?=[a-z])|(?<=[a-z])(?=\d)", " ", text)  # "128gb" -> "128 gb"
    return " ".join(text.split())


def name_similarity(a: str, b: str) -> float:
    """0..1 score blending shared words and character similarity.

    Numbers must agree: for textbooks they carry the grade or part ("Matematika 2" is not
    "Matematika 3"), so a mismatch in numbers halves the score.
    """
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    ta, tb = set(na.split()), set(nb.split())
    token_score = len(ta & tb) / len(ta | tb)
    seq_score = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    score = (token_score + seq_score) / 2
    numbers_a, numbers_b = {t for t in ta if t.isdigit()}, {t for t in tb if t.isdigit()}
    if numbers_a != numbers_b:
        score /= 2
    return round(score, 3)


@dataclass
class Candidate:
    product: Product
    score: float
    method: str  # "identifier" or "name"


def query_coverage(query: str, name: str) -> float:
    """Share of the query's words found in the name (for a user typing a short search)."""
    tq, tn = set(normalize_name(query).split()), set(normalize_name(name).split())
    return len(tq & tn) / len(tq) if tq else 0.0


def candidates_for(db: Session, title: str | None, identifiers: dict[str, str] | None,
                   limit: int = MAX_CANDIDATES, search: bool = False) -> list[Candidate]:
    """Products that may correspond to an offer, best first.

    `search=True` means `title` is a short text typed by a user: a product then also qualifies
    when its name contains every word (and number) of the text.
    """
    identifiers = identifiers or {}
    codes = gtin_keys(identifiers.get("isbn"), identifiers.get("ean"))
    sku = normalize_sku(identifiers.get("sku"))
    found: dict[int, Candidate] = {}

    if codes or sku:
        conditions = []
        if codes:
            conditions += [Product.isbn.in_(codes), Product.ean.in_(codes)]
        if sku:
            conditions.append(Product.sku == sku)
        for product in db.scalars(select(Product).where(or_(*conditions))):
            found[product.id] = Candidate(product, 1.0, "identifier")

    if title:
        # Products are a modest catalogue (hundreds to low thousands), so scoring in Python is fine.
        for product in db.scalars(select(Product).where(Product.tracking_status == "active")):
            if product.id in found:
                continue
            score = name_similarity(title, product.name)
            if search and query_coverage(title, product.name) == 1.0:
                score = max(score, REVIEW_SCORE)
            if score >= REVIEW_SCORE:
                found[product.id] = Candidate(product, score, "name")

    return sorted(found.values(), key=lambda c: (-c.score, c.product.name))[:limit]


def auto_match(db: Session, offer: CompetitorProduct) -> None:
    """Set the automatic match of one offer from its last scraped title and identifiers.

    Leaves manual decisions untouched. Exactly one identifier match, or one clear name match
    (>= AUTO_MATCH_SCORE and ahead of the runner-up), is assigned; anything else goes to review.
    """
    if offer.matching_method == "manual" or offer.match_status == "ignored":
        return
    current = db.scalar(select(CurrentPrice).where(CurrentPrice.competitor_product_id == offer.id))
    title = current.title if current else None
    identifiers = current.found_identifiers if current else None
    candidates = candidates_for(db, title, identifiers)

    best = candidates[0] if candidates else None
    runner_up = candidates[1].score if len(candidates) > 1 else 0.0
    if best and best.method == "identifier" and runner_up < 1.0:
        _assign(offer, best.product.id, "identifier", 1.0, "matched")
    elif best and best.score >= AUTO_MATCH_SCORE and best.score - runner_up >= 0.05:
        _assign(offer, best.product.id, "name", best.score, "matched")
    elif best:
        _assign(offer, None, None, best.score, "pending_review")
    else:
        _assign(offer, None, None, None, "unmatched")


def _assign(offer: CompetitorProduct, product_id: int | None, method: str | None, score: float | None,
            status: str) -> None:
    offer.product_id = product_id
    offer.matching_method = method
    offer.match_score = Decimal(str(score)) if score is not None else None
    offer.match_status = status


def assign_manually(offer: CompetitorProduct, product_id: int) -> None:
    _assign(offer, product_id, "manual", 1.0, "matched")


def ignore(offer: CompetitorProduct) -> None:
    _assign(offer, None, "manual", None, "ignored")


def reset_to_auto(db: Session, offer: CompetitorProduct) -> None:
    _assign(offer, None, None, None, "unmatched")
    auto_match(db, offer)


def rematch_open_offers(db: Session) -> int:
    """Re-run automatic matching for offers without a decision (after products change)."""
    offers = db.scalars(select(CompetitorProduct).where(
        CompetitorProduct.match_status.in_(("unmatched", "pending_review")),
        CompetitorProduct.status != "archived"))
    count = 0
    for offer in offers:
        auto_match(db, offer)
        count += 1
    return count
