"""Two-step imports with preview (assignment section 6).

1. `preview()` reads the file (CSV, Excel .xlsx, XML feed: Heureka or Google Merchant), normalizes every
   row, validates it and marks it ok / warning / duplicate / error with a reason. Nothing is saved yet.
2. `commit()` saves the ok and warning rows (duplicates are checked again) and reports what happened.

Limits: 5 MB per file, 10 000 rows. XML is parsed with defusedxml (no entities, no external DTDs).
"""
import csv
import io
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from defusedxml import ElementTree
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Competitor, CompetitorProduct, ImportBatch, Product, ProductPrice
from app.schemas.products import CURRENCY_MARKET, ProductCreate
from app.scraping.normalize import detect_currency, parse_price
from app.services import matching
from app.services.identifiers import normalize_ean, normalize_isbn, normalize_sku
from app.services.urls import InvalidUrl, normalize_url

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 10_000

# Column names accepted in files (lowercase) -> internal field
ALIASES = {
    "name": "name", "názov": "name", "nazov": "name", "název": "name", "nazev": "name", "productname": "name",
    "product": "name", "title": "name", "price": "price", "cena": "price", "price_vat": "price",
    "market": "market", "trh": "market", "currency": "currency", "mena": "currency", "měna": "currency",
    "isbn": "isbn", "ean": "ean", "gtin": "ean", "sku": "sku", "kód": "sku", "kod": "sku", "item_id": "internal_id",
    "internal_id": "internal_id", "id": "internal_id", "brand": "brand", "manufacturer": "brand",
    "vydavatel": "brand", "vydavateľ": "brand", "category": "category", "categorytext": "category",
    "product_type": "category", "kategória": "category", "kategorie": "category", "priority": "priority",
    "priorita": "priority", "url": "url", "link": "url", "variant": "variant",
    "product_isbn": "product_isbn", "product_ean": "product_ean", "product_sku": "product_sku",
    "product_id": "product_internal_id", "product_name": "product_name", "produkt": "product_name",
}


class ImportFileError(ValueError):
    pass


# ---------- reading files ----------

def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1250"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportFileError("Súbor nie je v kódovaní UTF-8 ani Windows-1250.")


def _map_header(names: list[str]) -> list[str | None]:
    mapped = [ALIASES.get((n or "").strip().lower().replace(" ", "_")) for n in names]
    if not any(mapped):
        raise ImportFileError("Hlavička neobsahuje žiadny známy stĺpec (napr. name, price, url).")
    return mapped


def _rows_from_table(header: list[str], records) -> list[dict[str, Any]]:
    mapped = _map_header(header)
    rows = []
    for number, record in records:
        values = {field: str(value).strip() for field, value in zip(mapped, record, strict=False)  # short rows are fine
                  if field and value is not None and str(value).strip()}
        if values:
            rows.append({"row": number, **values})
        if len(rows) > MAX_ROWS:
            raise ImportFileError(f"Súbor má viac ako {MAX_ROWS} riadkov.")
    return rows


def _read_csv(data: bytes) -> list[dict[str, Any]]:
    text = _decode(data)
    if not text.strip():
        raise ImportFileError("Súbor je prázdny.")
    try:
        dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect=dialect)
    header = next(reader)
    return _rows_from_table(header, ((i, rec) for i, rec in enumerate(reader, start=2)))


def _read_xlsx(data: bytes) -> list[dict[str, Any]]:
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ImportFileError("Súbor sa nedá otvoriť ako Excel (.xlsx).") from None
    sheet = workbook.worksheets[0]
    rows = sheet.iter_rows(values_only=True)
    header = [str(c) if c is not None else "" for c in next(rows, [])]
    result = _rows_from_table(header, ((i, rec) for i, rec in enumerate(rows, start=2)))
    workbook.close()
    return result


def _text(element, path: str, namespaces: dict | None = None) -> str | None:
    found = element.find(path, namespaces or {})
    return found.text.strip() if found is not None and found.text and found.text.strip() else None


def _read_xml(data: bytes) -> tuple[str, list[dict[str, Any]]]:
    try:
        root = ElementTree.fromstring(data, forbid_dtd=True)
    except Exception as exc:
        raise ImportFileError(f"XML sa nedá spracovať: {exc.__class__.__name__}.") from None
    rows: list[dict[str, Any]] = []
    if root.tag.upper() == "SHOP":  # Heureka feed
        for i, item in enumerate(root.iter("SHOPITEM"), start=1):
            row = {"row": i, "name": _text(item, "PRODUCTNAME") or _text(item, "PRODUCT"),
                   "price": _text(item, "PRICE_VAT"), "ean": _text(item, "EAN"), "isbn": _text(item, "ISBN"),
                   "internal_id": _text(item, "ITEM_ID"), "url": _text(item, "URL"),
                   "category": _text(item, "CATEGORYTEXT"), "brand": _text(item, "MANUFACTURER")}
            rows.append({k: v for k, v in row.items() if v is not None})
        fmt = "heureka"
    elif root.tag in ("rss", "feed") or root.tag.endswith("}feed"):  # Google Merchant (RSS or Atom)
        ns = {"g": "http://base.google.com/ns/1.0", "a": "http://www.w3.org/2005/Atom"}
        items = list(root.iter("item")) or list(root.iter("{http://www.w3.org/2005/Atom}entry"))
        for i, item in enumerate(items, start=1):
            price = _text(item, "g:price", ns) or ""
            row = {"row": i, "name": _text(item, "title") or _text(item, "g:title", ns) or _text(item, "a:title", ns),
                   "price": price, "currency": detect_currency(price), "ean": _text(item, "g:gtin", ns),
                   "internal_id": _text(item, "g:id", ns), "url": _text(item, "link") or _text(item, "g:link", ns),
                   "category": _text(item, "g:product_type", ns), "brand": _text(item, "g:brand", ns)}
            rows.append({k: v for k, v in row.items() if v is not None})
        fmt = "google"
    else:
        raise ImportFileError("Neznámy XML formát. Podporované sú feedy Heureka (SHOP/SHOPITEM) a Google Merchant.")
    if len(rows) > MAX_ROWS:
        raise ImportFileError(f"Feed má viac ako {MAX_ROWS} položiek.")
    return fmt, rows


def read_file(data: bytes, file_name: str) -> tuple[str, list[dict[str, Any]]]:
    if len(data) > MAX_BYTES:
        raise ImportFileError("Súbor je väčší ako 5 MB.")
    if not data.strip():
        raise ImportFileError("Súbor je prázdny.")
    name = file_name.lower()
    if name.endswith(".xlsx") or data[:2] == b"PK":
        return "xlsx", _read_xlsx(data)
    if name.endswith(".xml") or data.lstrip()[:1] == b"<":
        return _read_xml(data)
    return "csv", _read_csv(data)


# ---------- products ----------

def _first_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    field = ".".join(str(p) for p in err["loc"] if p != "prices" and not isinstance(p, int))
    return f"{field}: {err['msg']}" if field else err["msg"]


def _existing_product(db: Session, data: dict[str, Any]) -> Product | None:
    conditions = [func.lower(Product.name) == data["name"].lower()]
    for column, key in ((Product.isbn, "isbn"), (Product.ean, "ean"), (Product.sku, "sku"),
                        (Product.internal_id, "internal_id")):
        if data.get(key):
            conditions.append(column == data[key])
    return db.scalars(select(Product).where(or_(*conditions)).limit(1)).first()


def _validate_product(row: dict[str, Any]) -> dict[str, Any]:
    raw_price = row.get("price")
    price = parse_price(raw_price)
    if price is None:
        raise ValueError(f"Neplatná cena: „{raw_price}“." if raw_price else "Chýba cena.")
    currency = (row.get("currency") or "").upper() or detect_currency(row.get("price"))
    market = (row.get("market") or "").upper() or CURRENCY_MARKET.get(currency or "")
    if not market:
        raise ValueError("Chýba trh (stĺpec market) alebo známa mena.")
    try:
        product = ProductCreate(name=row.get("name", ""), isbn=row.get("isbn"), ean=row.get("ean"), sku=row.get("sku"),
                                internal_id=row.get("internal_id"), brand=row.get("brand"),
                                category=row.get("category"), priority=row.get("priority") or "regular",
                                prices=[{"market": market, "price": price, "currency": currency}])
    except ValidationError as exc:
        raise ValueError(_first_error(exc)) from None
    return product.model_dump(mode="json", exclude={"attributes"})


def _identity_keys(data: dict[str, Any]) -> list[str]:
    keys = [f"name:{data['name'].lower()}"]
    keys += [f"{k}:{data[k]}" for k in ("isbn", "ean", "sku", "internal_id") if data.get(k)]
    return keys


def _preview_products(db: Session, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result, seen = [], {}
    for row in rows:
        try:
            data = _validate_product(row)
        except ValueError as exc:
            result.append({"row": row["row"], "status": "error", "reason": str(exc), "data": row})
            continue
        clash = next((seen[k] for k in _identity_keys(data) if k in seen), None)
        existing = _existing_product(db, data)
        if existing:
            result.append({"row": row["row"], "status": "duplicate", "reason": f"Produkt už existuje: {existing.name}",
                           "data": data})
        elif clash:
            result.append({"row": row["row"], "status": "duplicate",
                           "reason": f"Rovnaký produkt je už v riadku {clash}", "data": data})
        else:
            result.append({"row": row["row"], "status": "ok", "reason": None, "data": data})
            for k in _identity_keys(data):
                seen[k] = row["row"]
    return result


def _commit_products(db: Session, rows: list[dict[str, Any]]) -> tuple[int, list[dict]]:
    imported, skipped = 0, []
    for row in rows:
        data = row["data"]
        if _existing_product(db, data):  # checked again: the catalogue may have changed since the preview
            skipped.append({"row": row["row"], "reason": "Produkt medzitým pribudol."})
            continue
        product = Product(**{k: v for k, v in data.items() if k != "prices"}, attributes={})
        product.prices = [ProductPrice(market=p["market"], price=Decimal(p["price"]), currency=p["currency"],
                                       vat_mode=p["vat_mode"]) for p in data["prices"]]
        db.add(product)
        db.flush()
        imported += 1
    if imported:
        matching.rematch_open_offers(db)
    return imported, skipped


# ---------- offers ----------

def _competitor_for(competitors: list[Competitor], url: str) -> Competitor | None:
    host = urlsplit(url).hostname or ""
    for c in competitors:
        if host == c.domain or host.endswith("." + c.domain):
            return c
    return None


def _find_product(db: Session, row: dict[str, Any]) -> tuple[Product | None, str | None]:
    """Product referenced by the row (identifiers first, then the exact name). Returns (product, problem)."""
    refs = {"isbn": normalize_isbn(row.get("product_isbn")), "ean": normalize_ean(row.get("product_ean")),
            "sku": normalize_sku(row.get("product_sku")), "internal_id": row.get("product_internal_id")}
    for field, value in refs.items():
        if value:
            product = db.scalar(select(Product).where(getattr(Product, field) == value))
            if product:
                return product, None
            return None, f"Produkt s {field.upper()} {value} neexistuje, ponuka sa pridá nepriradená."
    if row.get("product_name"):
        product = db.scalars(select(Product).where(func.lower(Product.name) == row["product_name"].lower())).first()
        return (product, None) if product else (None, "Produkt s týmto názvom neexistuje, ponuka sa pridá "
                                                      "nepriradená.")
    return None, None


def _preview_offers(db: Session, rows: list[dict[str, Any]], competitor_id: int | None) -> list[dict[str, Any]]:
    competitors = list(db.scalars(select(Competitor).where(Competitor.status != "archived")))
    if competitor_id is not None:
        competitors = [c for c in competitors if c.id == competitor_id]
        if not competitors:
            raise ImportFileError("Vybraný konkurent neexistuje alebo je archivovaný.")
    result, seen = [], {}
    for row in rows:
        raw = row.get("url")
        if not raw:
            result.append({"row": row["row"], "status": "error", "reason": "Chýba URL.", "data": row})
            continue
        try:
            url = normalize_url(raw)
        except InvalidUrl as exc:
            result.append({"row": row["row"], "status": "error", "reason": str(exc), "data": row})
            continue
        competitor = _competitor_for(competitors, url)
        if competitor is None:
            result.append({"row": row["row"], "status": "error", "data": row,
                           "reason": "URL nepatrí žiadnemu evidovanému konkurentovi (ani vybranému)."})
            continue
        variant = (row.get("variant") or "").strip()[:100]
        product, problem = _find_product(db, row)
        data = {"url": url, "variant": variant, "competitor_id": competitor.id, "competitor_name": competitor.name,
                "product_id": product.id if product else None, "product_name": product.name if product else None}
        key = (competitor.id, url, variant)
        exists = db.scalar(select(CompetitorProduct.id).where(
            CompetitorProduct.competitor_id == competitor.id, CompetitorProduct.product_url == url,
            CompetitorProduct.variant == variant))
        if exists:
            result.append({"row": row["row"], "status": "duplicate", "reason": "Táto URL je už sledovaná.",
                           "data": data})
        elif key in seen:
            result.append({"row": row["row"], "status": "duplicate",
                           "reason": f"Rovnaká URL je už v riadku {seen[key]}", "data": data})
        else:
            seen[key] = row["row"]
            result.append({"row": row["row"], "status": "warning" if problem else "ok", "reason": problem,
                           "data": data})
    return result


def _commit_offers(db: Session, rows: list[dict[str, Any]]) -> tuple[int, list[dict]]:
    imported, skipped = 0, []
    for row in rows:
        data = row["data"]
        exists = db.scalar(select(CompetitorProduct.id).where(
            CompetitorProduct.competitor_id == data["competitor_id"], CompetitorProduct.product_url == data["url"],
            CompetitorProduct.variant == data["variant"]))
        if exists:
            skipped.append({"row": row["row"], "reason": "URL medzitým pribudla."})
            continue
        offer = CompetitorProduct(competitor_id=data["competitor_id"], product_url=data["url"], variant=data["variant"])
        db.add(offer)
        db.flush()
        if data.get("product_id") and db.get(Product, data["product_id"]):
            matching.assign_manually(offer, data["product_id"])
        imported += 1
    return imported, skipped


# ---------- public API ----------

def _stats(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {s: sum(1 for r in rows if r["status"] == s) for s in ("ok", "warning", "duplicate", "error")}


def preview(db: Session, kind: str, data: bytes, file_name: str, user_id: int | None,
            competitor_id: int | None = None) -> ImportBatch:
    file_format, rows = read_file(data, file_name)
    if not rows:
        raise ImportFileError("Súbor neobsahuje žiadne riadky s údajmi.")
    checked = _preview_products(db, rows) if kind == "products" else _preview_offers(db, rows, competitor_id)
    batch = ImportBatch(kind=kind, status="preview", file_name=file_name[:255], file_format=file_format,
                        competitor_id=competitor_id, created_by=user_id, rows=checked, stats=_stats(checked))
    db.add(batch)
    db.flush()
    return batch


def commit(db: Session, batch: ImportBatch) -> dict[str, Any]:
    if batch.status != "preview":
        raise ImportFileError("Tento import už bol potvrdený alebo zrušený.")
    to_save = [r for r in batch.rows if r["status"] in ("ok", "warning")]
    imported, skipped = (_commit_products if batch.kind == "products" else _commit_offers)(db, to_save)
    batch.status, batch.committed_at = "committed", datetime.now(UTC)
    batch.stats = {**batch.stats, "imported": imported, "skipped_at_commit": len(skipped)}
    return {"imported": imported, "duplicates": batch.stats["duplicate"] + len(skipped),
            "errors": batch.stats["error"], "skipped_at_commit": skipped}
