"""SQLAlchemy models. Importing this package registers every table on Base.metadata."""
from app.models.alerts import Alert, AlertRule
from app.models.competitors import Competitor, ScrapeConfig
from app.models.discovery import CatalogItem, DiscoveryRun
from app.models.imports import ImportBatch
from app.models.jobs import ScrapeJob, ScrapeRun
from app.models.prices import CurrentPrice, PriceHistory
from app.models.products import CompetitorProduct, Product, ProductPrice
from app.models.settings import AppSetting
from app.models.users import User

__all__ = [
    "Alert", "AlertRule", "AppSetting", "CatalogItem", "Competitor", "CompetitorProduct", "CurrentPrice",
    "DiscoveryRun", "ImportBatch",
    "PriceHistory", "Product", "ProductPrice", "ScrapeConfig", "ScrapeJob", "ScrapeRun", "User",
]
