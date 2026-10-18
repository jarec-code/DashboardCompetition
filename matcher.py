"""Fuzzy matching of competitor products to our products by name."""
import re
import unicodedata
from difflib import SequenceMatcher

MATCH_THRESHOLD = 0.6


def normalize(name):
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"(?<=\d)(?=[a-z])|(?<=[a-z])(?=\d)", " ", text)  # "128gb" -> "128 gb"
    return " ".join(text.split())


def similarity(a, b):
    """0..1 score blending shared words and character similarity."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    ta, tb = set(na.split()), set(nb.split())
    token_score = len(ta & tb) / len(ta | tb)
    seq_score = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return (token_score + seq_score) / 2


def assign_matches(our_products, competitor_products, links):
    """Map competitor_product_id -> (our_product_id, score, manual).

    `links` maps competitor_product_id -> our_product_id (None = ignored).
    For automatic matches, each competitor only contributes its best-scoring
    product per one of our products.
    """
    result = {}
    best_per_pair = {}  # (our_id, competitor_id) -> (score, cp_id)
    for cp in competitor_products:
        if cp["id"] in links:
            if links[cp["id"]] is not None:
                result[cp["id"]] = (links[cp["id"]], 1.0, True)
            continue
        best = None
        for op in our_products:
            score = similarity(op["name"], cp["name"])
            if score >= MATCH_THRESHOLD and (best is None or score > best[1]):
                best = (op["id"], score)
        if best:
            key = (best[0], cp["competitor_id"])
            if key not in best_per_pair or best[1] > best_per_pair[key][0]:
                best_per_pair[key] = (best[1], cp["id"])
    for (our_id, _), (score, cp_id) in best_per_pair.items():
        result[cp_id] = (our_id, score, False)
    return result
