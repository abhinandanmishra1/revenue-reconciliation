import uuid
from datetime import UTC, datetime, timedelta

import jwt

SESSION_COOKIE = "session"
SESSION_TTL = timedelta(hours=12)
_ALGORITHM = "HS256"


def issue(user_id: uuid.UUID, secret: str, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    payload = {"sub": str(user_id), "iat": now, "exp": now + SESSION_TTL}
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def read(token: str, secret: str) -> uuid.UUID | None:
    try:
        payload = jwt.decode(token, secret, algorithms=[_ALGORITHM], options={"require": ["sub", "exp"]})
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError):
        return None
