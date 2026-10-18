from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.api.deps import AdminUser, DbSession
from app.models import User
from app.schemas.users import UserCreate, UserOut, UserUpdate
from app.security import hash_password

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
def list_users(db: DbSession, _admin: AdminUser) -> list[User]:
    return list(db.scalars(select(User).order_by(User.email)))


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(data: UserCreate, db: DbSession, _admin: AdminUser) -> User:
    if db.scalar(select(User).where(User.email == data.email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Používateľ s týmto e-mailom už existuje.")
    user = User(email=data.email, password_hash=hash_password(data.password), role=data.role)
    db.add(user)
    db.commit()
    return user


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, data: UserUpdate, db: DbSession, admin: AdminUser) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Používateľ neexistuje.")
    demoting = (data.role == "viewer" or data.is_active is False) and user.role == "admin"
    if demoting:
        active_admins = db.scalar(select(func.count()).select_from(User)
                                  .where(User.role == "admin", User.is_active.is_(True)))
        if active_admins <= 1:
            raise HTTPException(status.HTTP_409_CONFLICT, "Posledného aktívneho administrátora nemožno odobrať.")
    if data.role is not None:
        user.role = data.role
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.password is not None:
        user.password_hash = hash_password(data.password)
    db.commit()
    return user
