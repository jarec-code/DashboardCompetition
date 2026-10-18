from typing import Literal

from fastapi import APIRouter, HTTPException, Query, UploadFile, status
from sqlalchemy import Select, exists, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.models import CompetitorProduct, Product, ProductPrice
from app.schemas.common import Page
from app.schemas.products import ImportResult, ProductCreate, ProductOut, ProductUpdate
from app.services import matching, scheduling
from app.services.identifiers import normalize_ean, normalize_isbn, normalize_sku
from app.services.product_import import MAX_BYTES, ProductImportError, import_products_csv

router = APIRouter(prefix="/products", tags=["products"])

offer_count = (
    select(func.count(CompetitorProduct.id))
    .where(CompetitorProduct.product_id == Product.id, CompetitorProduct.status != "archived")
    .correlate(Product).scalar_subquery()
)
SORT_COLUMNS = {
    "name": func.lower(Product.name),
    "priority": Product.priority,
    "category": Product.category,
    "offers": offer_count,
    "updated": Product.updated_at,
}
SortKey = Literal[tuple(SORT_COLUMNS) + tuple(f"-{k}" for k in SORT_COLUMNS)]  # type: ignore[valid-type]
IDENTIFIER_LABELS = {"isbn": "ISBN", "ean": "EAN", "sku": "SKU", "internal_id": "interné ID"}


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _get(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Produkt neexistuje.")
    return product


def _out(product: Product, offers: int) -> ProductOut:
    out = ProductOut.model_validate(product)
    out.offer_count = offers
    return out


def _check_unique_identifiers(db: Session, values: dict, exclude_id: int | None = None) -> None:
    for field, label in IDENTIFIER_LABELS.items():
        value = values.get(field)
        if not value:
            continue
        query = select(Product).where(getattr(Product, field) == value)
        if exclude_id is not None:
            query = query.where(Product.id != exclude_id)
        other = db.scalars(query.limit(1)).first()
        if other:
            raise HTTPException(status.HTTP_409_CONFLICT, f"{label} {value} už má produkt „{other.name}“. "
                                                          "Každý identifikátor patrí jednému produktu.")


def _set_prices(db: Session, product: Product, prices) -> None:
    if product.prices:
        product.prices.clear()
        db.flush()  # delete old rows first: (product, market) is unique
    product.prices.extend(ProductPrice(market=p.market, price=p.price, currency=p.currency, vat_mode=p.vat_mode)
                          for p in prices)


@router.get("", response_model=Page[ProductOut])
def list_products(
    db: DbSession, _user: CurrentUser,
    q: str | None = Query(default=None, max_length=200),
    priority: Literal["strategic", "regular", "stable"] | None = None,
    tracking_status: Literal["active", "paused"] | None = None,
    market: str | None = Query(default=None, max_length=16),
    sort: SortKey = "name",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> Page[ProductOut]:
    query: Select = select(Product, offer_count.label("offer_count"))
    if q and q.strip():
        text = q.strip()
        code = normalize_isbn(text) or normalize_ean(text)
        if code:
            query = query.where(or_(Product.isbn == code, Product.ean == code))
        else:
            pattern = f"%{_escape_like(text.lower())}%"
            query = query.where(or_(func.lower(Product.name).like(pattern, escape="\\"),
                                    Product.sku == normalize_sku(text), Product.internal_id == text))
    if priority:
        query = query.where(Product.priority == priority)
    if tracking_status:
        query = query.where(Product.tracking_status == tracking_status)
    if market:
        query = query.where(exists().where(ProductPrice.product_id == Product.id,
                                           ProductPrice.market == market.upper()))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    column = SORT_COLUMNS[sort.lstrip("-")]
    order = column.desc().nulls_last() if sort.startswith("-") else column.asc().nulls_last()
    rows = db.execute(query.order_by(order, Product.id).offset((page - 1) * page_size).limit(page_size))
    return Page(items=[_out(p, n) for p, n in rows], total=total, page=page, page_size=page_size)


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(data: ProductCreate, db: DbSession, _admin: AdminUser) -> ProductOut:
    values = data.model_dump(exclude={"prices", "attributes"})
    _check_unique_identifiers(db, values)
    product = Product(**values, attributes=data.attributes.model_dump(exclude_none=True) if data.attributes else {})
    db.add(product)
    _set_prices(db, product, data.prices)
    db.flush()
    matching.rematch_open_offers(db)  # a new product may resolve offers waiting for a match
    db.commit()
    return _out(product, 0)


@router.post("/import", response_model=ImportResult)
async def import_products(file: UploadFile, db: DbSession, _admin: AdminUser) -> ImportResult:
    data = await file.read(MAX_BYTES + 1)
    try:
        result = import_products_csv(db, data)
    except ProductImportError as exc:
        raise HTTPException(422, str(exc)) from None
    if result.imported:
        matching.rematch_open_offers(db)
        db.commit()
    return result


@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: int, db: DbSession, _user: CurrentUser) -> ProductOut:
    product = _get(db, product_id)
    return _out(product, db.scalar(select(offer_count).where(Product.id == product_id)) or 0)


@router.patch("/{product_id}", response_model=ProductOut)
def update_product(product_id: int, data: ProductUpdate, db: DbSession, _admin: AdminUser) -> ProductOut:
    product = _get(db, product_id)
    changes = data.model_dump(exclude_unset=True, exclude={"prices", "attributes"})
    for field in ("name", "priority", "tracking_status"):
        if field in changes and changes[field] is None:
            raise HTTPException(422, f"Pole {field} nesmie byť prázdne.")
    _check_unique_identifiers(db, changes, exclude_id=product.id)
    for field, value in changes.items():
        setattr(product, field, value)
    if "attributes" in data.model_fields_set:
        product.attributes = data.attributes.model_dump(exclude_none=True) if data.attributes else {}
    if data.prices is not None:
        _set_prices(db, product, data.prices)
    db.flush()
    matching.rematch_open_offers(db)
    if "priority" in changes:
        scheduling.reschedule(db, db.scalars(select(CompetitorProduct).where(
            CompetitorProduct.product_id == product.id)))
    db.commit()
    return get_product(product_id, db, _admin)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: int, db: DbSession, _admin: AdminUser) -> None:
    """Assigned offers stay (with their price history) and return to the matching queue."""
    product = _get(db, product_id)
    for offer in db.scalars(select(CompetitorProduct).where(CompetitorProduct.product_id == product_id)):
        offer.product_id, offer.matching_method, offer.match_score, offer.match_status = None, None, None, "unmatched"
    db.delete(product)
    db.commit()
