import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.security import MIN_PASSWORD_LENGTH

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
Role = Literal["admin", "viewer"]


def normalize_email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 254 or not EMAIL_RE.match(value):
        raise ValueError("Neplatná e-mailová adresa.")
    return value


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=256)

    _email = field_validator("email")(normalize_email)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    role: Role
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class SessionOut(BaseModel):
    user: UserOut
    csrf_token: str


class UserCreate(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256)
    role: Role = "viewer"

    _email = field_validator("email")(normalize_email)


class UserUpdate(BaseModel):
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=MIN_PASSWORD_LENGTH, max_length=256)
