"""SQLAlchemy models. Importing this package registers every table on Base.metadata."""
from app.models.competitors import Competitor, ScrapeConfig
from app.models.jobs import ScrapeJob, ScrapeRun
from app.models.prices import CurrentPrice, PriceHistory
from app.models.products import CompetitorProduct, Product, ProductPrice
from app.models.settings import AppSetting
from app.models.users import User

__all__ = [
    "AppSetting", "Competitor", "CompetitorProduct", "CurrentPrice", "PriceHistory",
    "Product", "ProductPrice", "ScrapeConfig", "ScrapeJob", "ScrapeRun", "User",
]
