from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aoi_geojson: dict[str, Any]
    event_date: date

    @field_validator("event_date")
    @classmethod
    def event_date_not_future(cls, value: date) -> date:
        if value > datetime.now(UTC).date():
            raise ValueError("event date cannot be in the future")
        return value
