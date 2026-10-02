from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
job_id_context: ContextVar[str | None] = ContextVar("job_id", default=None)

_SECRET_VALUE = re.compile(
    r"(?i)(password|passwd|token|secret|authorization|api[_-]?key)(\s*[=:]\s*)([^\s,;&]+)"
)
_BEARER_VALUE = re.compile(r"(?i)\bBearer\s+[^\s,;&]+")


def redact_secrets(value: str) -> str:
    redacted = _SECRET_VALUE.sub(r"\1\2[REDACTED]", value)
    return _BEARER_VALUE.sub("Bearer [REDACTED]", redacted)


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except (TypeError, ValueError):
            rendered = "[unrenderable log message]"
        record.msg = redact_secrets(rendered)
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_secrets(record.getMessage()),
            "request_id": request_id_context.get(),
            "job_id": job_id_context.get(),
        }
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(SecretRedactionFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
