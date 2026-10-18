"""Simple CSV import of our own products (stage 3). Preview, Excel and feeds follow in stage 8.

Columns (header row, case-insensitive): name and price are required; optional market, currency,
isbn, ean, sku, internal_id, brand, category, priority. Market defaults from the currency
(CZK -> CZ, EUR -> SK, ...). Rows matching an existing product (same ISBN, EAN, SKU, internal ID
or name) are skipped as duplicates, never overwritten.
"""
import csv
import io

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Product, ProductPrice
from app.schemas.products import CURRENCY_MARKET, ImportResult, ImportRowIssue, ProductCreate
from app.scraping.normalize import detect_currency, parse_price

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
COLUMN_ALIASES = {"názov": "name", "nazev": "name", "cena": "price", "trh": "market", "mena": "currency",
                  "měna": "currency", "kod": "sku", "kód": "sku"}


class ProductImportError(ValueError):
    pass


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1250"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ProductImportError("Súbor nie je v kódovaní UTF-8 ani Windows-1250.")


def _rows(text: str) -> list[dict[str, str]]:
    first_line = text.splitlines()[0] if text.strip() else ""
    try:
        dialect = csv.Sniffer().sniff(first_line, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ProductImportError("Súbor je prázdny alebo nemá hlavičku.")
    header = {f: COLUMN_ALIASES.get(f.strip().lower(), f.strip().lower()) for f in reader.fieldnames if f}
    if not {"name", "price"} <= set(header.values()):
        raise ProductImportError("Hlavička musí obsahovať stĺpce name a price.")
    rows = []
    for row in reader:
        rows.append({header[k]: (v or "").strip() for k, v in row.items() if k in header})
        if len(rows) > MAX_ROWS:
            raise ProductImportError(f"Súbor má viac ako {MAX_ROWS} riadkov.")
    return rows


def _first_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    field = ".".join(str(p) for p in err["loc"] if p != "prices" and not isinstance(p, int))
    return f"{field}: {err['msg']}" if field else err["msg"]


def _existing(db: Session, data: ProductCreate) -> Product | None:
    conditions = [func.lower(Product.name) == data.name.lower()]
    for column, value in ((Product.isbn, data.isbn), (Product.ean, data.ean), (Product.sku, data.sku),
                          (Product.internal_id, data.internal_id)):
        if value:
            conditions.append(column == value)
    return db.scalars(select(Product).where(or_(*conditions)).limit(1)).first()


def import_products_csv(db: Session, data: bytes) -> ImportResult:
    if len(data) > MAX_BYTES:
        raise ProductImportError("Súbor je väčší ako 2 MB.")
    rows = _rows(_decode(data))
    imported, duplicates, errors = 0, [], []
    for number, row in enumerate(rows, start=2):  # row 1 is the header
        price = parse_price(row.get("price"))
        if price is None:
            errors.append(ImportRowIssue(row=number, reason=f"Neplatná cena: {row.get('price')!r}"))
            continue
        currency = (row.get("currency") or "").upper() or detect_currency(row.get("price"))
        market = (row.get("market") or "").upper() or CURRENCY_MARKET.get(currency or "")
        if not market:
            errors.append(ImportRowIssue(row=number, reason="Chýba trh (stĺpec market) alebo známa mena."))
            continue
        try:
            product_in = ProductCreate(
                name=row.get("name", ""), isbn=row.get("isbn"), ean=row.get("ean"), sku=row.get("sku"),
                internal_id=row.get("internal_id"), brand=row.get("brand"), category=row.get("category"),
                priority=row.get("priority") or "regular",
                prices=[{"market": market, "price": price, "currency": currency}],
            )
        except ValidationError as exc:
            errors.append(ImportRowIssue(row=number, reason=_first_error(exc)))
            continue
        existing = _existing(db, product_in)
        if existing:
            duplicates.append(ImportRowIssue(row=number, reason=f"Produkt už existuje: {existing.name}"))
            continue
        product = Product(**product_in.model_dump(exclude={"prices", "attributes"}), attributes={})
        product.prices = [ProductPrice(market=p.market, price=p.price, currency=p.currency, vat_mode=p.vat_mode)
                          for p in product_in.prices]
        db.add(product)
        db.flush()  # later rows see this product as a duplicate
        imported += 1
    db.commit()
    return ImportResult(imported=imported, duplicates=duplicates, errors=errors)
