from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

import numpy as np
import numpy.typing as npt
import shapely
from pyproj import CRS, Transformer
from shapely.geometry import MultiPoint
from shapely.geometry.base import BaseGeometry
from shapely.ops import split
from shapely.strtree import STRtree

from valleyeye.osm.models import OSMFeature

RoadStatus = Literal["OPEN", "POTENTIALLY_BLOCKED", "BLOCKED", "UNKNOWN_COVERAGE"]


@dataclass(frozen=True)
class RoadSegment:
    segment_id: str
    parent_road_id: str
    geometry: BaseGeometry
    highway: str
    status: RoadStatus
    length_m: float
    flood_overlap_fraction: float
    oneway: str | None
    maxspeed: str | None
    bridge: bool


@dataclass(frozen=True)
class RoadImpactSummary:
    segment_count: int
    open_km: float
    potentially_blocked_km: float
    blocked_km: float
    unknown_coverage_km: float


def _project(geometries: tuple[BaseGeometry, ...], metric_crs: CRS) -> npt.NDArray[np.object_]:
    transformer = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
    return cast(
        npt.NDArray[np.object_],
        shapely.transform(
            np.asarray(geometries, dtype=object),
            transformer.transform,
            interleaved=False,
        ),
    )


def _point_parts(geometry: BaseGeometry) -> list[BaseGeometry]:
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Point":
        return [geometry]
    return [part for part in shapely.get_parts(geometry) if part.geom_type == "Point"]


def split_and_classify_roads(
    roads: tuple[OSMFeature, ...],
    flood_polygons: tuple[BaseGeometry, ...],
    coverage_geometry: BaseGeometry | None,
    metric_crs: CRS,
    potentially_blocked_fraction: float = 0.01,
    blocked_fraction: float = 0.5,
) -> tuple[tuple[RoadSegment, ...], RoadImpactSummary]:
    if not 0 <= potentially_blocked_fraction < blocked_fraction <= 1:
        raise ValueError("Road impact fractions must satisfy 0 <= potential < blocked <= 1")
    if not roads:
        return (), RoadImpactSummary(0, 0.0, 0.0, 0.0, 0.0)

    projected_roads = _project(tuple(road.geometry for road in roads), metric_crs)
    projected_flood = (
        _project(flood_polygons, metric_crs) if flood_polygons else np.asarray([], dtype=object)
    )
    flood_union = (
        shapely.union_all(projected_flood) if len(projected_flood) else shapely.GeometryCollection()
    )
    coverage = (
        _project((coverage_geometry,), metric_crs)[0] if coverage_geometry is not None else None
    )
    tree = STRtree(projected_flood) if len(projected_flood) else None
    intersecting_roads: set[int] = set()
    if tree is not None:
        hits = tree.query(projected_roads, predicate="intersects")
        intersecting_roads = set(int(index) for index in hits[0])

    segments: list[RoadSegment] = []
    for road_index, (feature, geometry) in enumerate(zip(roads, projected_roads, strict=True)):
        line_parts = [
            part
            for part in shapely.get_parts(geometry)
            if part.geom_type == "LineString" and part.length > 0
        ]
        pieces: list[BaseGeometry] = []
        for line in line_parts:
            if road_index in intersecting_roads:
                boundary_crossings = line.intersection(flood_union.boundary)
                points = [
                    point
                    for point in _point_parts(boundary_crossings)
                    if 1e-6 < line.project(point) < line.length - 1e-6
                ]
                if points:
                    try:
                        pieces.extend(split(line, MultiPoint(points)).geoms)
                    except ValueError:
                        pieces.append(line)
                else:
                    pieces.append(line)
            else:
                pieces.append(line)
        for piece_index, piece in enumerate(pieces):
            length_m = float(piece.length)
            overlap_m = (
                float(piece.intersection(flood_union).length) if not flood_union.is_empty else 0.0
            )
            overlap_fraction = min(1.0, overlap_m / length_m) if length_m else 0.0
            if coverage is None or not coverage.covers(piece):
                status: RoadStatus = "UNKNOWN_COVERAGE"
            elif overlap_fraction >= blocked_fraction:
                status = "BLOCKED"
            elif overlap_fraction >= potentially_blocked_fraction:
                status = "POTENTIALLY_BLOCKED"
            else:
                status = "OPEN"
            segments.append(
                RoadSegment(
                    segment_id=f"{feature.feature_id}:{piece_index}",
                    parent_road_id=feature.feature_id,
                    geometry=piece,
                    highway=feature.tags["highway"],
                    status=status,
                    length_m=length_m,
                    flood_overlap_fraction=overlap_fraction,
                    oneway=feature.tags.get("oneway"),
                    maxspeed=feature.tags.get("maxspeed"),
                    bridge=feature.tags.get("bridge", "no") not in {"no", "false", "0"}
                    and feature.tags.get("tunnel") not in {"yes", "true", "1"},
                )
            )
    total_by_status = {
        status: sum(item.length_m for item in segments if item.status == status) / 1000
        for status in ("OPEN", "POTENTIALLY_BLOCKED", "BLOCKED", "UNKNOWN_COVERAGE")
    }
    summary = RoadImpactSummary(
        segment_count=len(segments),
        open_km=total_by_status["OPEN"],
        potentially_blocked_km=total_by_status["POTENTIALLY_BLOCKED"],
        blocked_km=total_by_status["BLOCKED"],
        unknown_coverage_km=total_by_status["UNKNOWN_COVERAGE"],
    )
    return tuple(segments), summary
