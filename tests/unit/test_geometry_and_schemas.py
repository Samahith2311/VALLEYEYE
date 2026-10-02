from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from valleyeye.api.schemas import AnalysisRequest
from valleyeye.core.errors import ErrorCode, ValleyeyeError
from valleyeye.core.geometry import validate_aoi
from valleyeye.core.settings import Settings


def test_valid_polygon_aoi_reports_metric_area() -> None:
    aoi = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [0.02, 0], [0.02, 0.02], [0, 0.02], [0, 0]]],
    }

    geometry, area_km2 = validate_aoi(aoi, Settings())

    assert geometry.geom_type == "Polygon"
    assert 4 < area_km2 < 6


@pytest.mark.parametrize(
    "aoi",
    [
        {"type": "Point", "coordinates": [0, 0]},
        {"type": "Polygon", "coordinates": []},
        {
            "type": "Polygon",
            "coordinates": [[[200, 0], [201, 0], [201, 1], [200, 0]]],
        },
    ],
)
def test_invalid_aoi_is_rejected(aoi: dict[str, object]) -> None:
    with pytest.raises(ValleyeyeError) as caught:
        validate_aoi(aoi, Settings())

    assert caught.value.code == ErrorCode.INVALID_AOI


def test_future_event_date_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AnalysisRequest(
            aoi_geojson={"type": "Polygon", "coordinates": []},
            event_date=datetime.now(UTC).date() + timedelta(days=1),
        )


def test_event_date_today_is_valid() -> None:
    result = AnalysisRequest(
        aoi_geojson={"type": "Polygon", "coordinates": []},
        event_date=datetime.now(UTC).date(),
    )

    assert result.event_date == datetime.now(UTC).date()
