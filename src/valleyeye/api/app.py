from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from valleyeye import __version__
from valleyeye.core.errors import ErrorCode, ValleyeyeError
from valleyeye.core.logging import configure_logging, request_id_context
from valleyeye.core.settings import Settings

_LOGGER = logging.getLogger("valleyeye.api")
_REQUEST_ID = re.compile(r"^[0-9a-fA-F-]{36}$")


def _error_response(
    status_code: int,
    code: ErrorCode,
    message: str,
    stage: str,
    retryable: bool,
    details: dict[str, Any],
    request_id: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code.value,
                "message": message,
                "stage": stage,
                "retryable": retryable,
                "details": details,
                "request_id": request_id,
            }
        },
    )


def _status_for_error(code: ErrorCode) -> int:
    if code in {ErrorCode.INVALID_AOI, ErrorCode.INVALID_EVENT_DATE}:
        return 422
    if code == ErrorCode.JOB_NOT_FOUND:
        return 404
    if code == ErrorCode.JOB_NOT_READY:
        return 409
    if code in {
        ErrorCode.CDSE_AUTH_FAILED,
        ErrorCode.CDSE_UNAVAILABLE,
        ErrorCode.OSM_UNAVAILABLE,
    }:
        return 503 if code in {ErrorCode.CDSE_UNAVAILABLE, ErrorCode.OSM_UNAVAILABLE} else 502
    if code == ErrorCode.INTERNAL_ERROR:
        return 500
    return 422


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings()
    configure_logging()
    application = FastAPI(
        title="VALLEYEYE API",
        version=__version__,
        description="Traceable flood extent, infrastructure exposure and connectivity analysis.",
    )
    application.state.settings = app_settings

    @application.middleware("http")
    async def attach_request_id(request: Request, call_next: Callable[..., Any]) -> Any:
        candidate = request.headers.get("x-request-id", "")
        request_id = candidate if _REQUEST_ID.fullmatch(candidate) else str(uuid.uuid4())
        token = request_id_context.set(request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        finally:
            request_id_context.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response

    @application.exception_handler(ValleyeyeError)
    async def handle_valleyeye_error(request: Request, exc: ValleyeyeError) -> JSONResponse:
        return _error_response(
            _status_for_error(exc.code),
            exc.code,
            exc.message,
            exc.stage,
            exc.retryable,
            exc.details,
            getattr(request.state, "request_id", str(uuid.uuid4())),
        )

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        locations = [".".join(str(part) for part in error["loc"]) for error in exc.errors()]
        code = (
            ErrorCode.INVALID_EVENT_DATE
            if any("event_date" in location for location in locations)
            else ErrorCode.INVALID_AOI
        )
        return _error_response(
            422,
            code,
            "Request validation failed",
            "DISCOVERY",
            False,
            {"fields": locations},
            getattr(request.state, "request_id", str(uuid.uuid4())),
        )

    @application.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = ErrorCode.JOB_NOT_FOUND if exc.status_code == 404 else ErrorCode.INTERNAL_ERROR
        message = (
            "Requested resource was not found" if exc.status_code == 404 else "HTTP request failed"
        )
        return _error_response(
            exc.status_code,
            code,
            message,
            "RESULTS",
            False,
            {},
            getattr(request.state, "request_id", str(uuid.uuid4())),
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        _LOGGER.error("unhandled_request_error", extra={"exception_type": type(exc).__name__})
        return _error_response(
            500,
            ErrorCode.INTERNAL_ERROR,
            "An unexpected error occurred",
            "RESULTS",
            False,
            {},
            getattr(request.state, "request_id", str(uuid.uuid4())),
        )

    @application.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @application.get("/ready", tags=["health"])
    async def ready() -> dict[str, Any]:
        return {
            "status": "ready",
            "checks": {
                "settings": "ok",
                "cdse_credentials": "configured" if app_settings.cdse_username else "optional",
            },
        }

    return application


app = create_app()
