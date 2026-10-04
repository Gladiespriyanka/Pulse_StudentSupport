"""Uniform error envelope: {"error": {"code", "message", "details", "request_id"}}."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.security.context import get_ctx

log = logging.getLogger("pulse.errors")


class AppError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str = "", *, code: str | None = None, status: int | None = None, details=None, headers=None):
        super().__init__(message)
        self.message = message or self.__class__.__name__
        if code:
            self.code = code
        if status:
            self.status = status
        self.details = details
        self.headers = headers


class Unauthorized(AppError):
    status, code = 401, "unauthorized"


class Forbidden(AppError):
    status, code = 403, "forbidden"


class NotFound(AppError):
    status, code = 404, "not_found"


class Conflict(AppError):
    status, code = 409, "conflict"


class Invalid(AppError):
    status, code = 422, "invalid"


class TooManyRequests(AppError):
    status, code = 429, "rate_limited"


def _body(code: str, message: str, details=None) -> dict:
    return {"error": {"code": code, "message": message, "details": details, "request_id": get_ctx().request_id}}


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in e["loc"][1:]) or e["loc"][0], "message": e["msg"]} for e in exc.errors()]
        return JSONResponse(_body("validation_error", "Some fields are invalid", details), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(_body(code, str(exc.detail)), status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(IntegrityError)
    async def _integrity(_: Request, exc: IntegrityError):
        log.warning("integrity error: %s", str(exc.orig).splitlines()[0] if exc.orig else exc)
        return JSONResponse(_body("conflict", "That conflicts with an existing record or a rule (for example a duplicate code, or something still in use)."), status_code=409)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("unhandled error", extra={"request_id": get_ctx().request_id})
        return JSONResponse(_body("internal_error", "Something went wrong on our side. Please try again."), status_code=500)
