from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    INVALID_AOI = "INVALID_AOI"
    INVALID_EVENT_DATE = "INVALID_EVENT_DATE"
    INVALID_SEARCH_FILTER = "INVALID_SEARCH_FILTER"
    CDSE_AUTH_FAILED = "CDSE_AUTH_FAILED"
    CDSE_UNAVAILABLE = "CDSE_UNAVAILABLE"
    NO_S1_SCENES = "NO_S1_SCENES"
    S1_NO_VALID_PAIR = "S1_NO_VALID_PAIR"
    S1_INSUFFICIENT_AOI_COVERAGE = "S1_INSUFFICIENT_AOI_COVERAGE"
    PREPROCESSING_FAILED = "PREPROCESSING_FAILED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    INFERENCE_FAILED = "INFERENCE_FAILED"
    OSM_SNAPSHOT_NOT_PRE_EVENT = "OSM_SNAPSHOT_NOT_PRE_EVENT"
    OSM_UNAVAILABLE = "OSM_UNAVAILABLE"
    NO_ROAD_NETWORK = "NO_ROAD_NETWORK"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    JOB_NOT_READY = "JOB_NOT_READY"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ValleyeyeError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        stage: str,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.retryable = retryable
        self.details = details or {}
