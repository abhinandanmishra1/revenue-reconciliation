"""CSRF defence in depth: every unsafe request must come from an allowed origin."""

from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.errors import error_body

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _origin_of(url: str) -> str | None:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def request_origin(headers: dict[str, str]) -> str | None:
    origin = headers.get("origin")
    if origin and origin != "null":
        return origin.rstrip("/")
    referer = headers.get("referer")
    return _origin_of(referer) if referer else None


class OriginCheckMiddleware:
    def __init__(self, app: ASGIApp, allowed: list[str]) -> None:
        self.app = app
        self.allowed = frozenset(allowed)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in UNSAFE_METHODS:
            headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
            origin = request_origin(headers)
            if origin is None or origin not in self.allowed:
                response = JSONResponse(error_body("forbidden_origin", "Request origin is not allowed."), 403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
