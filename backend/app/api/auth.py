from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, csrf_protect
from app.models import User
from app.schemas.users import LoginIn, SessionOut, UserOut
from app.security import MIN_PASSWORD_LENGTH, hash_password, login_limiter, new_csrf_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _session_out(request: Request, user: User) -> SessionOut:
    if "csrf" not in request.session:
        request.session["csrf"] = new_csrf_token()
    return SessionOut(user=UserOut.model_validate(user), csrf_token=request.session["csrf"])


@router.post("/login", response_model=SessionOut)
def login(data: LoginIn, request: Request, db: DbSession) -> SessionOut:
    client_ip = request.client.host if request.client else "unknown"
    keys = (f"ip:{client_ip}", f"email:{data.email}")
    if any(login_limiter.is_blocked(k) for k in keys):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Príliš veľa neúspešných pokusov. Skúste to neskôr.")

    user = db.scalar(select(User).where(User.email == data.email))
    if not verify_password(user.password_hash if user else None, data.password) or not user.is_active:
        for k in keys:
            login_limiter.record_failure(k)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nesprávny e-mail alebo heslo.")

    for k in keys:
        login_limiter.reset(k)
    user.last_login_at = datetime.now(UTC)
    db.commit()
    request.session.clear()  # new session on login (prevents session fixation)
    request.session["uid"] = user.id
    request.session["sv"] = user.session_version
    return _session_out(request, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(csrf_protect)])
def logout(request: Request, _user: CurrentUser) -> None:
    request.session.clear()


@router.get("/me", response_model=SessionOut)
def me(request: Request, user: CurrentUser) -> SessionOut:
    return _session_out(request, user)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(csrf_protect)])
def change_password(data: PasswordChange, request: Request, db: DbSession, user: CurrentUser) -> None:
    """Any user changes their own password; all their other sessions end."""
    if not verify_password(user.password_hash, data.current_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Súčasné heslo nie je správne.")
    user.password_hash = hash_password(data.new_password)
    user.session_version += 1
    db.commit()
    request.session["sv"] = user.session_version  # this session stays signed in
