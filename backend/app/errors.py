"""One error shape for every response: {"error": {"code", "message", "details"?}}."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("app")

_DEFAULT_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "rate_limited",
}


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details=None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details, self.headers = status, code, message, details, headers


def error_body(code: str, message: str, details=None) -> dict:
    body = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return {"error": body}


def not_found(what: str = "Resource") -> ApiError:
    return ApiError(404, "not_found", f"{what} not found.")


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(error_body(exc.code, exc.message, exc.details), exc.status, headers=exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        code = _DEFAULT_CODES.get(exc.status_code, "error")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return JSONResponse(error_body(code, message), exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        details = [
            {"field": ".".join(str(p) for p in e["loc"][1:]) or None, "message": e["msg"]} for e in exc.errors()
        ]
        return JSONResponse(error_body("validation_error", "The request is invalid.", details), 422)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        log.exception("unhandled error", exc_info=exc)
        return JSONResponse(error_body("internal_error", "Something went wrong."), 500)
