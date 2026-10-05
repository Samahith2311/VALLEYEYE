from __future__ import annotations

from typing import Any

from pyproj import CRS, Geod, Transformer
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as transform_geometry

from valleyeye.core.errors import ErrorCode, ValleyeyeError
from valleyeye.core.settings import Settings

_GEOD = Geod(ellps="WGS84")


def metric_crs_for_aoi(geometry: BaseGeometry) -> CRS:
    """Choose a local metric projection from a WGS84 AOI, without regional constants."""
    min_lon, min_lat, max_lon, max_lat = geometry.bounds
    if max_lon - min_lon > 180:
        raise ValleyeyeError(
            ErrorCode.INVALID_AOI,
            "AOIs crossing the antimeridian are not supported by local projected processing.",
            "PREPROCESSING",
        )
    centroid = geometry.centroid
    if centroid.y >= 84:
        return CRS.from_epsg(3413)
    if centroid.y <= -80:
        return CRS.from_epsg(3031)
    zone = min(60, max(1, int((centroid.x + 180) // 6) + 1))
    return CRS.from_epsg((32600 if centroid.y >= 0 else 32700) + zone)


def buffer_aoi_m(geometry: BaseGeometry, distance_m: float) -> BaseGeometry:
    if distance_m < 0:
        raise ValueError("AOI buffer distance must be non-negative")
    projected_crs = metric_crs_for_aoi(geometry)
    forward = Transformer.from_crs("EPSG:4326", projected_crs, always_xy=True).transform
    reverse = Transformer.from_crs(projected_crs, "EPSG:4326", always_xy=True).transform
    return transform_geometry(reverse, transform_geometry(forward, geometry).buffer(distance_m))


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
