from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from io import BytesIO
from typing import Any, cast

import httpx
import pyarrow.parquet as parquet
from pyproj import Transformer
from shapely import from_wkb, make_valid
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as transform_geometry

from valleyeye.core.errors import ErrorCode, ValleyeyeError
from valleyeye.core.geometry import metric_crs_for_aoi
from valleyeye.core.settings import Settings
from valleyeye.osm.models import OSMCategory, OSMDataset, OSMFeature, OSMQualitySummary

OHSOME_PROVIDER = "ohsome-api-v2"
OSM_FILTER = (
    "(building=* and geometry:polygon) or "
    "(highway=* and geometry:line) or "
    "(bridge=* and geometry:line) or "
    "(place=* and geometry:point) or "
    "(place=* and geometry:polygon) or "
    "(amenity in (hospital,clinic) and geometry:point) or "
    "(amenity in (hospital,clinic) and geometry:polygon) or "
    "(healthcare=* and geometry:point) or "
    "(healthcare=* and geometry:polygon)"
)
_SETTLEMENT_VALUES = {
    "city",
    "town",
    "village",
    "hamlet",
    "isolated_dwelling",
    "suburb",
    "neighbourhood",
}
_HEALTHCARE_VALUES = {"hospital", "clinic", "doctors", "health_centre"}


def pre_event_snapshot(event_date: date) -> datetime:
    """Select the final UTC second of the calendar day before an event date."""
    return datetime.combine(event_date - timedelta(days=1), time(23, 59, 59), tzinfo=UTC)


def assert_pre_event_snapshot(snapshot_at: datetime, event_date: date) -> None:
    if snapshot_at.tzinfo is None or snapshot_at.astimezone(UTC).date() >= event_date:
        raise ValleyeyeError(
            ErrorCode.OSM_SNAPSHOT_NOT_PRE_EVENT,
            "The OSM snapshot must be a timezone-aware timestamp strictly before the event date.",
            stage="OSM",
            details={"event_date": event_date.isoformat()},
        )


def _buffered_aoi(aoi: BaseGeometry, buffer_m: float) -> BaseGeometry:
    projected_crs = metric_crs_for_aoi(aoi)
    forward = Transformer.from_crs("EPSG:4326", projected_crs, always_xy=True).transform
    reverse = Transformer.from_crs(projected_crs, "EPSG:4326", always_xy=True).transform
    return transform_geometry(reverse, transform_geometry(forward, aoi).buffer(buffer_m))


def _category(tags: dict[str, str], geometry: BaseGeometry) -> OSMCategory | None:
    if tags.get("building") not in (None, "no") and geometry.geom_type in {
        "Polygon",
        "MultiPolygon",
    }:
        return "building"
    if tags.get("highway") and geometry.geom_type in {"LineString", "MultiLineString"}:
        return "road"
    if tags.get("place") in _SETTLEMENT_VALUES and geometry.geom_type in {
        "Point",
        "Polygon",
        "MultiPolygon",
    }:
        return "settlement"
    if (
        tags.get("amenity") in _HEALTHCARE_VALUES or tags.get("healthcare") in _HEALTHCARE_VALUES
    ) and geometry.geom_type in {"Point", "Polygon", "MultiPolygon"}:
        return "healthcare"
    return None


def _tags_from_row(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(key): str(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return {str(key): str(item) for key, item in value if item is not None}
    return {}


def normalize_parquet(
    content: bytes,
    buffer_aoi: BaseGeometry,
    snapshot_at: datetime,
) -> tuple[tuple[OSMFeature, ...], OSMQualitySummary]:
    try:
        rows = cast(
            list[dict[str, Any]],
            parquet.read_table(BytesIO(content)).to_pylist(),
        )
    except Exception as exc:
        raise ValleyeyeError(
            ErrorCode.OSM_UNAVAILABLE,
            "ohsome returned an unreadable feature extract.",
            stage="OSM",
        ) from exc

    features: list[OSMFeature] = []
    repaired = dropped_empty = ignored = clipped = 0
    snapshot_day = snapshot_at.date()
    for row in rows:
        raw_geometry = row.get("geom")
        if not isinstance(raw_geometry, bytes):
            dropped_empty += 1
            continue
        try:
            geometry = from_wkb(raw_geometry)
        except Exception:
            dropped_empty += 1
            continue
        if geometry.is_empty:
            dropped_empty += 1
            continue
        if not geometry.is_valid:
            geometry = make_valid(geometry)
            repaired += 1
        if not buffer_aoi.covers(geometry):
            clipped += 1
        geometry = geometry.intersection(buffer_aoi)
        if geometry.is_empty:
            dropped_empty += 1
            continue
        tags = _tags_from_row(row.get("tags"))
        category = _category(tags, geometry)
        if category is None:
            ignored += 1
            continue
        if category in {"settlement", "healthcare"} and not isinstance(geometry, Point):
            geometry = geometry.representative_point()
        osm_type = str(row.get("osm_type", ""))
        osm_id = int(row.get("osm_id", 0))
        features.append(
            OSMFeature(
                feature_id=f"{osm_type}/{osm_id}",
                osm_type=osm_type,
                osm_id=osm_id,
                category=category,
                geometry=geometry,
                tags=tags,
                snapshot_date=snapshot_day,
            )
        )
    return tuple(features), OSMQualitySummary(
        extracted_rows=len(rows),
        retained_features=len(features),
        repaired_geometries=repaired,
        dropped_empty_geometries=dropped_empty,
        ignored_features=ignored,
        clipped_features=clipped,
    )


class OhSomeOSMProvider:
    """Historical OSM extraction using the ohsome API v2 Parquet endpoint."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._client = client

    def fetch(self, aoi: BaseGeometry, event_date: date) -> OSMDataset:
        snapshot_at = pre_event_snapshot(event_date)
        assert_pre_event_snapshot(snapshot_at, event_date)
        if self.settings.ohsome_api_key is None:
            raise ValleyeyeError(
                ErrorCode.OSM_UNAVAILABLE,
                "Historical OSM extraction requires OHSOME_API_KEY.",
                stage="OSM",
                retryable=False,
                details={"provider": OHSOME_PROVIDER, "credential": "OHSOME_API_KEY"},
            )
        buffered = _buffered_aoi(aoi, self.settings.osm_aoi_buffer_m)
        min_x, min_y, max_x, max_y = buffered.bounds
        query: dict[str, object] = {
            "aoi": [min_x, min_y, max_x, max_y],
            "filter": OSM_FILTER,
            "time": snapshot_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "clip": False,
        }
        query_hash = sha256(
            json.dumps(query, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        url = f"{self.settings.ohsome_api_url.rstrip('/')}/extraction/features.parquet"
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=self.settings.ohsome_timeout_seconds)
        try:
            response = client.post(
                url,
                json=query,
                headers={"Authorization": self.settings.ohsome_api_key.get_secret_value()},
            )
        except httpx.HTTPError as exc:
            raise ValleyeyeError(
                ErrorCode.OSM_UNAVAILABLE,
                "Historical OSM extraction could not reach the ohsome service.",
                stage="OSM",
                retryable=True,
                details={"provider": OHSOME_PROVIDER},
            ) from exc
        finally:
            if owns_client:
                client.close()
        if response.is_error:
            raise ValleyeyeError(
                ErrorCode.OSM_UNAVAILABLE,
                "Historical OSM extraction failed at the ohsome service.",
                stage="OSM",
                retryable=response.status_code == 429 or response.status_code >= 500,
                details={"provider": OHSOME_PROVIDER, "status_code": response.status_code},
            )
        features, quality = normalize_parquet(response.content, buffered, snapshot_at)
        return OSMDataset(
            provider=OHSOME_PROVIDER,
            snapshot_at=snapshot_at,
            response_sha256=sha256(response.content).hexdigest(),
            query_sha256=query_hash,
            query=query,
            features=features,
            quality=quality,
        )
