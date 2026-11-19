import re

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import User
from app.db.session import get_db
from app.deps import current_user
from app.errors import ApiError
from app.security import tokens
from app.security.client_ip import client_ip
from app.security.passwords import MAX_PASSWORD_BYTES, MIN_PASSWORD_BYTES, hash_password, verify_password
from app.security.ratelimit import limits

router = APIRouter(prefix="/api/auth", tags=["auth"])

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalise_email(value: str) -> str:
    return value.strip().lower()


class Credentials(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        value = _normalise_email(value)
        if not _EMAIL.match(value) or len(value) > 254:
            raise ValueError("enter a valid email address")
        return value


class SignupIn(Credentials):
    @field_validator("password")
    @classmethod
    def _password(cls, value: str) -> str:
        size = len(value.encode())
        if size < MIN_PASSWORD_BYTES or size > MAX_PASSWORD_BYTES:
            raise ValueError(f"password must be {MIN_PASSWORD_BYTES}-{MAX_PASSWORD_BYTES} bytes")
        return value


class LoginIn(BaseModel):
    email: str
    password: str


def _user_out(user: User) -> dict:
    return {"id": str(user.id), "email": user.email}


def _start_session(response: Response, user: User, settings: Settings) -> None:
    response.set_cookie(
        tokens.SESSION_COOKIE,
        tokens.issue(user.id, settings.jwt_secret),
        max_age=int(tokens.SESSION_TTL.total_seconds()),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def _rate_limited(retry_after: int) -> ApiError:
    return ApiError(
        429, "rate_limited", "Too many attempts. Please wait and try again.",
        headers={"Retry-After": str(retry_after)},
    )


def _check_ip(request: Request, settings: Settings) -> str:
    ip = client_ip(request, settings.trusted_proxy_hops)
    retry = limits.auth_ip.take(ip)
    if retry is not None:
        raise _rate_limited(retry)
    return ip


@router.post("/signup", status_code=201)
def signup(
    body: SignupIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    _check_ip(request, settings)
    user = User(email=body.email, password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # The unique index closes the race between two signups for one email.
        db.rollback()
        raise ApiError(409, "email_taken", "An account with this email already exists.") from None
    _start_session(response, user, settings)
    return {"user": _user_out(user)}


@router.post("/login")
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    ip = _check_ip(request, settings)
    email = _normalise_email(body.email)
    pair = f"{ip}|{email}"

    retry = limits.failed_login.retry_after(pair)
    if retry is not None:
        raise _rate_limited(retry)

    delay = limits.email_delay(email)
    if delay:
        limits.sleep(delay)  # before the password check, for right and wrong passwords alike

    user = db.scalar(select(User).where(User.email == email))
    if not verify_password(body.password, user.password_hash if user else None):
        limits.failed_login.hit(pair)
        limits.email_failures.hit(email)
        raise ApiError(401, "invalid_credentials", "Email or password is incorrect.")

    limits.failed_login.reset(pair)
    _start_session(response, user, settings)
    return {"user": _user_out(user)}


@router.post("/logout", status_code=204)
def logout(settings: Settings = Depends(get_settings)) -> Response:
    response = Response(status_code=204)
    response.delete_cookie(tokens.SESSION_COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax")
    return response


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"user": _user_out(user)}
