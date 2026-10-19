"""In-app alerts and their rules. E-mail or other notifications can be added later."""
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.models import Alert, AlertRule, Competitor, CompetitorProduct, CurrentPrice, Product
from app.models.alerts import RULE_TYPES
from app.schemas.common import Page
from app.services import alerts

router = APIRouter(tags=["alerts"])
RuleType = Literal["price_drop", "cheaper_than_us", "unavailable", "stale", "scraper_failing"]
assert set(RuleType.__args__) == set(RULE_TYPES)  # type: ignore[attr-defined]


class RuleIn(BaseModel):
    rule_type: RuleType
    params: dict[str, Any] = Field(default_factory=dict)
    competitor_id: int | None = None
    product_id: int | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def check_params(self) -> "RuleIn":
        self.params = alerts.validate_params(self.rule_type, self.params)
        return self


class RuleOut(BaseModel):
    id: int
    rule_type: str
    params: dict[str, Any]
    competitor_id: int | None
    competitor_name: str | None
    product_id: int | None
    product_name: str | None
    enabled: bool
    open_alerts: int


class AlertOut(BaseModel):
    id: int
    rule_id: int
    alert_type: str
    status: str
    message: str
    details: dict[str, Any]
    created_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None
    offer_id: int
    product_url: str
    offer_title: str | None
    competitor_id: int
    competitor_name: str
    product_id: int | None
    product_name: str | None


def _rule_out(db, rule: AlertRule) -> RuleOut:
    open_count = db.scalar(select(func.count(Alert.id)).where(Alert.rule_id == rule.id, Alert.status == "open"))
    competitor = db.get(Competitor, rule.competitor_id) if rule.competitor_id else None
    product = db.get(Product, rule.product_id) if rule.product_id else None
    return RuleOut(id=rule.id, rule_type=rule.rule_type, params=rule.params, competitor_id=rule.competitor_id,
                   competitor_name=competitor.name if competitor else None, product_id=rule.product_id,
                   product_name=product.name if product else None, enabled=rule.enabled, open_alerts=open_count)


def _check_scope(db, data: RuleIn) -> None:
    if data.competitor_id is not None and db.get(Competitor, data.competitor_id) is None:
        raise HTTPException(422, "Konkurent neexistuje.")
    if data.product_id is not None and db.get(Product, data.product_id) is None:
        raise HTTPException(422, "Produkt neexistuje.")


@router.get("/alert-rules", response_model=list[RuleOut])
def list_rules(db: DbSession, _user: CurrentUser) -> list[RuleOut]:
    return [_rule_out(db, r) for r in db.scalars(select(AlertRule).order_by(AlertRule.id))]


@router.post("/alert-rules", response_model=RuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(data: RuleIn, db: DbSession, _admin: AdminUser) -> RuleOut:
    _check_scope(db, data)
    rule = AlertRule(**data.model_dump())
    db.add(rule)
    db.flush()
    alerts.evaluate_all(db)
    db.commit()
    return _rule_out(db, rule)


@router.put("/alert-rules/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: int, data: RuleIn, db: DbSession, _admin: AdminUser) -> RuleOut:
    rule = db.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pravidlo neexistuje.")
    _check_scope(db, data)
    for field, value in data.model_dump().items():
        setattr(rule, field, value)
    db.flush()
    if not rule.enabled:  # a disabled rule keeps no active alerts
        now = datetime.now(UTC)
        for alert in db.scalars(select(Alert).where(Alert.rule_id == rule.id, Alert.status != "resolved")):
            alert.status, alert.resolved_at = "resolved", now
    alerts.evaluate_all(db)
    db.commit()
    return _rule_out(db, rule)


@router.delete("/alert-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: int, db: DbSession, _admin: AdminUser) -> None:
    rule = db.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pravidlo neexistuje.")
    db.delete(rule)
    db.commit()


@router.get("/alerts", response_model=Page[AlertOut])
def list_alerts(db: DbSession, _user: CurrentUser,
                status_filter: Literal["open", "acknowledged", "resolved", "active"] | None = Query(
                    default="active", alias="status"),
                alert_type: RuleType | None = None, competitor_id: int | None = None, product_id: int | None = None,
                page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100)) -> Page[AlertOut]:
    query = (select(Alert, CompetitorProduct, Competitor.name, Product.id, Product.name, CurrentPrice.title)
             .join(CompetitorProduct, CompetitorProduct.id == Alert.competitor_product_id)
             .join(Competitor, Competitor.id == CompetitorProduct.competitor_id)
             .outerjoin(Product, Product.id == CompetitorProduct.product_id)
             .outerjoin(CurrentPrice, CurrentPrice.competitor_product_id == CompetitorProduct.id))
    if status_filter == "active":
        query = query.where(Alert.status.in_(("open", "acknowledged")))
    elif status_filter:
        query = query.where(Alert.status == status_filter)
    for column, value in ((Alert.alert_type, alert_type), (Competitor.id, competitor_id), (Product.id, product_id)):
        if value is not None:
            query = query.where(column == value)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(Alert.status != "open", Alert.last_seen_at.desc(), Alert.id.desc())
                      .offset((page - 1) * page_size).limit(page_size))
    items = [AlertOut(id=a.id, rule_id=a.rule_id, alert_type=a.alert_type, status=a.status, message=a.message,
                      details=a.details, created_at=a.created_at, last_seen_at=a.last_seen_at,
                      resolved_at=a.resolved_at, offer_id=o.id, product_url=o.product_url, offer_title=title,
                      competitor_id=o.competitor_id, competitor_name=cname, product_id=pid, product_name=pname)
             for a, o, cname, pid, pname, title in rows]
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/alerts/count")
def alert_count(db: DbSession, _user: CurrentUser) -> dict[str, int]:
    return {"open": db.scalar(select(func.count(Alert.id)).where(Alert.status == "open"))}


@router.post("/alerts/{alert_id}/{action}", response_model=dict)
def change_alert(alert_id: int, action: Literal["acknowledge", "resolve", "reopen"], db: DbSession,
                 _admin: AdminUser) -> dict:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upozornenie neexistuje.")
    if action == "acknowledge" and alert.status == "open":
        alert.status = "acknowledged"
    elif action == "resolve" and alert.status != "resolved":
        alert.status, alert.resolved_at = "resolved", datetime.now(UTC)
    elif action == "reopen" and alert.status == "acknowledged":
        alert.status = "open"
    else:
        raise HTTPException(status.HTTP_409_CONFLICT, "Túto zmenu stavu nemožno vykonať.")
    db.commit()
    return {"id": alert.id, "status": alert.status}
