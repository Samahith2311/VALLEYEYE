from __future__ import annotations

from typing import Any

from pyproj import Geod
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from valleyeye.core.errors import ErrorCode, ValleyeyeError
from valleyeye.core.settings import Settings

_GEOD = Geod(ellps="WGS84")


def geometry_area_km2(geometry: BaseGeometry) -> float:
    area_m2, _ = _GEOD.geometry_area_perimeter(geometry)
    return abs(area_m2) / 1_000_000


def validate_aoi(geojson: dict[str, Any], settings: Settings) -> tuple[BaseGeometry, float]:
    try:
        geometry = shape(geojson)
    except (TypeError, ValueError, KeyError) as exc:
        raise ValleyeyeError(
            ErrorCode.INVALID_AOI, "AOI must be a valid GeoJSON geometry", "DISCOVERY"
        ) from exc
    if geometry.geom_type not in {"Polygon", "MultiPolygon"} or geometry.is_empty:
        raise ValleyeyeError(
            ErrorCode.INVALID_AOI,
            "AOI must be a non-empty Polygon or MultiPolygon",
            "DISCOVERY",
        )
    if not geometry.is_valid:
        raise ValleyeyeError(
            ErrorCode.INVALID_AOI, "AOI geometry is topologically invalid", "DISCOVERY"
        )
    min_lon, min_lat, max_lon, max_lat = geometry.bounds
    if min_lon < -180 or max_lon > 180 or min_lat < -90 or max_lat > 90:
        raise ValleyeyeError(
            ErrorCode.INVALID_AOI,
            "AOI coordinates must use WGS84 longitude and latitude",
            "DISCOVERY",
        )
    area_km2 = geometry_area_km2(geometry)
    if not settings.min_aoi_area_km2 <= area_km2 <= settings.max_aoi_area_km2:
        raise ValleyeyeError(
            ErrorCode.INVALID_AOI,
            "AOI area is outside configured limits",
            "DISCOVERY",
            details={
                "area_km2": area_km2,
                "minimum_km2": settings.min_aoi_area_km2,
                "maximum_km2": settings.max_aoi_area_km2,
            },
        )
    return geometry, area_km2


def aoi_coverage_fraction(footprint: dict[str, Any], aoi: BaseGeometry) -> float:
    candidate = shape(footprint)
    denominator = geometry_area_km2(aoi)
    if denominator <= 0:
        return 0.0
    return min(1.0, geometry_area_km2(candidate.intersection(aoi)) / denominator)
