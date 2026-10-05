from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

import numpy as np
import numpy.typing as npt
import shapely
from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry

from valleyeye.osm.models import OSMFeature


@dataclass(frozen=True)
class BuildingImpact:
    feature_id: str
    footprint_area_m2: float
    overlap_area_m2: float
    overlap_fraction: float
    affected: bool


@dataclass(frozen=True)
class ExposureSummary:
    total_count: int
    affected_count: int
    total_area_km2: float
    affected_area_km2: float
    affected_fraction: float


@dataclass(frozen=True)
class BridgeImpact:
    feature_id: str
    overlap_fraction: float
    intersects_hazard: bool
    classification: Literal["AFFECTED", "NOT_AFFECTED"]


def _project_geometries(
    geometries: tuple[BaseGeometry, ...], metric_crs: CRS
) -> npt.NDArray[np.object_]:
    transformer = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
    return cast(
        npt.NDArray[np.object_],
        shapely.transform(
            np.asarray(geometries, dtype=object),
            transformer.transform,
            interleaved=False,
        ),
    )


def assess_building_exposure(
    buildings: tuple[OSMFeature, ...],
    flood_polygons: tuple[BaseGeometry, ...],
    metric_crs: CRS,
    affected_fraction_threshold: float = 0.1,
) -> tuple[tuple[BuildingImpact, ...], ExposureSummary]:
    if not 0 <= affected_fraction_threshold <= 1:
        raise ValueError("affected_fraction_threshold must be in [0, 1]")
    if not buildings:
        return (), ExposureSummary(0, 0, 0.0, 0.0, 0.0)
    projected_buildings = _project_geometries(
        tuple(feature.geometry for feature in buildings), metric_crs
    )
    projected_flood = (
        _project_geometries(flood_polygons, metric_crs)
        if flood_polygons
        else np.asarray([], dtype=object)
    )
    hazard = (
        shapely.union_all(projected_flood) if len(projected_flood) else shapely.GeometryCollection()
    )
    footprint_areas = shapely.area(projected_buildings)
    overlap_areas = np.zeros_like(footprint_areas, dtype=float)
    if len(projected_flood):
        tree = shapely.STRtree(projected_flood)
        candidate_pairs = tree.query(projected_buildings, predicate="intersects")
        candidate_indices = np.unique(candidate_pairs[0])
        if len(candidate_indices):
            candidate_overlaps = shapely.intersection(
                projected_buildings[candidate_indices],
                hazard,
            )
            overlap_areas[candidate_indices] = shapely.area(candidate_overlaps)
    fractions = np.divide(
        overlap_areas,
        footprint_areas,
        out=np.zeros_like(overlap_areas, dtype=float),
        where=footprint_areas > 0,
    )
    impacts = tuple(
        BuildingImpact(
            feature_id=feature.feature_id,
            footprint_area_m2=float(footprint_areas[index]),
            overlap_area_m2=float(overlap_areas[index]),
            overlap_fraction=float(fractions[index]),
            affected=bool(
                overlap_areas[index] > 0 and fractions[index] >= affected_fraction_threshold
            ),
        )
        for index, feature in enumerate(buildings)
    )
    total_area = float(footprint_areas.sum())
    affected_area = float(sum(item.footprint_area_m2 for item in impacts if item.affected))
    affected_count = sum(item.affected for item in impacts)
    summary = ExposureSummary(
        total_count=len(impacts),
        affected_count=affected_count,
        total_area_km2=total_area / 1_000_000,
        affected_area_km2=affected_area / 1_000_000,
        affected_fraction=affected_count / len(impacts),
    )
    return impacts, summary


def assess_bridge_exposure(
    bridges: tuple[OSMFeature, ...],
    flood_polygons: tuple[BaseGeometry, ...],
    metric_crs: CRS,
    hazard_buffer_m: float = 20.0,
    block_on_intersection: bool = True,
    minimum_overlap_fraction: float = 0.1,
) -> tuple[BridgeImpact, ...]:
    if hazard_buffer_m < 0 or not 0 <= minimum_overlap_fraction <= 1:
        raise ValueError("Bridge buffer and overlap threshold are invalid")
    if not bridges:
        return ()
    bridge_geometries = _project_geometries(
        tuple(feature.geometry for feature in bridges), metric_crs
    )
    flood_geometries = (
        _project_geometries(flood_polygons, metric_crs)
        if flood_polygons
        else np.asarray([], dtype=object)
    )
    hazard = (
        shapely.union_all(flood_geometries)
        if len(flood_geometries)
        else shapely.GeometryCollection()
    )
    corridors = shapely.buffer(bridge_geometries, hazard_buffer_m)
    overlaps = np.zeros(len(bridges), dtype=float)
    if len(flood_geometries):
        tree = shapely.STRtree(flood_geometries)
        candidate_pairs = tree.query(corridors, predicate="intersects")
        candidate_indices = np.unique(candidate_pairs[0])
        if len(candidate_indices):
            overlaps[candidate_indices] = shapely.area(
                shapely.intersection(corridors[candidate_indices], hazard)
            )
    corridor_areas = shapely.area(corridors)
    fractions = np.divide(
        overlaps,
        corridor_areas,
        out=np.zeros_like(overlaps, dtype=float),
        where=corridor_areas > 0,
    )
    return tuple(
        BridgeImpact(
            feature_id=feature.feature_id,
            overlap_fraction=float(fractions[index]),
            intersects_hazard=bool(overlaps[index] > 0),
            classification=(
                "AFFECTED"
                if (
                    overlaps[index] > 0
                    and (block_on_intersection or fractions[index] >= minimum_overlap_fraction)
                )
                else "NOT_AFFECTED"
            ),
        )
        for index, feature in enumerate(bridges)
    )
