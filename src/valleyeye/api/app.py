from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from valleyeye import __version__
from valleyeye.api.schemas import AnalysisRequest
from valleyeye.core.errors import ErrorCode, ValleyeyeError
from valleyeye.core.geometry import validate_aoi
from valleyeye.core.logging import configure_logging, request_id_context
from valleyeye.core.settings import Settings
from valleyeye.pipeline.defaults import default_stage_handlers
from valleyeye.pipeline.models import PIPELINE_STAGES
from valleyeye.pipeline.runner import JobManager, StageHandler
from valleyeye.pipeline.store import JobStore

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
    if code == ErrorCode.JOB_QUEUE_FULL:
        return 429
    if code == ErrorCode.JOB_CANCELLED:
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


def create_app(
    settings: Settings | None = None,
    stage_handlers: dict[str, StageHandler] | None = None,
) -> FastAPI:
    app_settings = settings or Settings()
    configure_logging()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> Any:
        yield
        await manager.shutdown()

    application = FastAPI(
        title="VALLEYEYE API",
        version=__version__,
        description="Traceable flood extent, infrastructure exposure and connectivity analysis.",
        lifespan=lifespan,
    )
    application.state.settings = app_settings
    manager = JobManager(
        app_settings,
        stage_handlers if stage_handlers is not None else default_stage_handlers(),
        store=JobStore(app_settings.job_data_dir),
    )
    application.state.job_manager = manager

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

    def completed_record(job_id: str) -> tuple[Any, Path]:
        record = manager.store.get(job_id)
        if record.status not in {"SUCCEEDED", "PARTIAL"}:
            details: dict[str, Any] = {"status": record.status}
            if record.error is not None:
                details["job_error"] = record.error.model_dump(mode="json")
            raise ValleyeyeError(
                ErrorCode.JOB_NOT_READY,
                "Analysis outputs are available only after the job completes successfully.",
                record.current_stage or "RESULTS",
                details=details,
            )
        return record, manager.store.job_dir(job_id)

    @application.post("/api/v1/jobs", status_code=202, tags=["jobs"])
    async def create_job(
        payload: AnalysisRequest, response: Response, no_cache: bool = False
    ) -> dict[str, Any]:
        validate_aoi(payload.aoi_geojson, app_settings)
        record = manager.submit(payload, no_cache=no_cache)
        response.headers["Location"] = f"/api/v1/jobs/{record.job_id}"
        return {
            "job_id": record.job_id,
            "status": record.status,
            "status_url": f"/api/v1/jobs/{record.job_id}",
            "no_cache": record.no_cache,
        }

    @application.get("/api/v1/jobs/{job_id}", tags=["jobs"])
    async def get_job(job_id: str) -> dict[str, Any]:
        record = manager.store.get(job_id)
        result = record.model_dump(mode="json")
        result["stage_count"] = len(PIPELINE_STAGES)
        result["results_available"] = record.status in {"SUCCEEDED", "PARTIAL"}
        return result

    @application.post("/api/v1/jobs/{job_id}/cancel", tags=["jobs"])
    async def cancel_job(job_id: str) -> dict[str, Any]:
        record = await manager.cancel(job_id)
        return record.model_dump(mode="json")

    @application.get("/api/v1/jobs/{job_id}/summary", tags=["results"])
    async def get_summary(job_id: str) -> Any:
        _, job_dir = completed_record(job_id)
        path = job_dir / "analysis.json"
        if not path.is_file():
            raise ValleyeyeError(
                ErrorCode.JOB_NOT_READY, "Analysis summary is not available.", "RESULTS"
            )
        return JSONResponse(content=json.loads(path.read_text(encoding="utf-8")))

    @application.get("/api/v1/jobs/{job_id}/layers", tags=["results"])
    async def get_layers(job_id: str) -> dict[str, Any]:
        record, _ = completed_record(job_id)
        layers = [
            {
                "path": artifact.path,
                "sha256": artifact.sha256,
                "size_bytes": artifact.size_bytes,
                "media_type": artifact.media_type,
                "url": f"/api/v1/jobs/{record.job_id}/layers/{artifact.path}",
            }
            for artifact in record.artifacts.values()
            if artifact.path.casefold().endswith((".geojson", ".tif", ".tiff"))
        ]
        return {"layers": layers}

    @application.get("/api/v1/jobs/{job_id}/layers/{artifact_path:path}", tags=["results"])
    async def get_layer(job_id: str, artifact_path: str) -> FileResponse:
        record, job_dir = completed_record(job_id)
        descriptor = record.artifacts.get(artifact_path)
        if descriptor is None or not artifact_path.casefold().endswith(
            (".geojson", ".tif", ".tiff")
        ):
            raise ValleyeyeError(
                ErrorCode.JOB_NOT_FOUND, "Requested layer was not found.", "RESULTS"
            )
        path = (job_dir / descriptor.path).resolve()
        if not path.is_relative_to(job_dir.resolve()) or not path.is_file():
            raise ValleyeyeError(
                ErrorCode.JOB_NOT_FOUND, "Requested layer was not found.", "RESULTS"
            )
        return FileResponse(path, media_type=descriptor.media_type)

    @application.get("/api/v1/jobs/{job_id}/report", tags=["results"])
    async def get_report(job_id: str) -> FileResponse:
        _, job_dir = completed_record(job_id)
        path = job_dir / "report.html"
        if not path.is_file():
            raise ValleyeyeError(
                ErrorCode.JOB_NOT_READY, "Analysis report is not available.", "RESULTS"
            )
        return FileResponse(path, media_type="text/html")

    @application.get("/api/v1/jobs/{job_id}/provenance", tags=["results"])
    async def get_provenance(job_id: str) -> Any:
        _, job_dir = completed_record(job_id)
        path = job_dir / "run_manifest.json"
        if not path.is_file():
            raise ValleyeyeError(
                ErrorCode.JOB_NOT_READY, "Run provenance is not available.", "RESULTS"
            )
        return JSONResponse(content=json.loads(path.read_text(encoding="utf-8")))

    return application


app = create_app()
