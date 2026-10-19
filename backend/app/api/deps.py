import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import User

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "X-CSRF-Token"

DbSession = Annotated[Session, Depends(get_session)]


def csrf_protect(request: Request) -> None:
    """Every state-changing API call must echo the session's CSRF token in a header."""
    if request.method in SAFE_METHODS:
        return
    expected = request.session.get("csrf")
    sent = request.headers.get(CSRF_HEADER, "")
    if not expected or not hmac.compare_digest(expected, sent):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Neplatný alebo chýbajúci CSRF token.")


def current_user(request: Request, db: DbSession) -> User:
    user_id = request.session.get("uid")
    user = db.get(User, user_id) if user_id else None
    if user is None or not user.is_active or request.session.get("sv") != user.session_version:
        request.session.clear()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nie ste prihlásený.")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_admin(user: CurrentUser) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Na túto akciu potrebujete rolu administrátora.")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
