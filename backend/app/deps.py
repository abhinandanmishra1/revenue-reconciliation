import uuid

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import Import, User
from app.db.session import get_db
from app.errors import ApiError, not_found
from app.security import tokens


def current_user(
    request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> User:
    token = request.cookies.get(tokens.SESSION_COOKIE)
    user_id = tokens.read(token, settings.jwt_secret) if token else None
    user = db.get(User, user_id) if user_id else None
    if user is None:
        raise ApiError(401, "unauthenticated", "Please sign in.")
    return user


def owned_import(db: Session, import_id: uuid.UUID, user: User) -> Import:
    """The single isolation check: another user's import is indistinguishable from none."""
    found = db.scalar(select(Import).where(Import.id == import_id, Import.user_id == user.id))
    if found is None:
        raise not_found("Import")
    return found
