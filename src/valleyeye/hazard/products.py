from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.features import geometry_mask, shapes
from rasterio.windows import Window
from shapely.geometry import mapping, shape
from shapely.ops import transform as transform_geometry

from valleyeye.core.errors import ErrorCode, ValleyeyeError
from valleyeye.sar.raster import assert_aligned

_VECTOR_WINDOW_SIZE = 512


def _windows(width: int, height: int) -> Iterator[Window]:
    for row in range(0, height, _VECTOR_WINDOW_SIZE):
        for col in range(0, width, _VECTOR_WINDOW_SIZE):
            yield Window(
                col,
                row,
                min(_VECTOR_WINDOW_SIZE, width - col),
                min(_VECTOR_WINDOW_SIZE, height - row),
            )


def _write_feature_collection(path: Path, features: Iterator[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".part")
    count = 0
    try:
        with temporary_path.open("w", encoding="utf-8") as output:
            output.write('{"type":"FeatureCollection","features":[')
            for feature in features:
                if count:
                    output.write(",")
                json.dump(feature, output, separators=(",", ":"))
                count += 1
            output.write("]}")
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return count


def _flood_features(
    probability_source: Any,
    mask_source: Any,
    min_area_km2: float,
) -> Iterator[dict[str, Any]]:
    to_wgs84 = Transformer.from_crs(probability_source.crs, "EPSG:4326", always_xy=True).transform
    polygon_id = 0
    for window in _windows(mask_source.width, mask_source.height):
        mask = mask_source.read(1, window=window)
        probability = probability_source.read(1, window=window)
        transform = mask_source.window_transform(window)
        for geometry_json, _ in shapes(mask, mask=mask == 1, transform=transform):
            geometry_projected = shape(geometry_json)
            area_km2 = geometry_projected.area / 1_000_000
            if area_km2 < min_area_km2:
                continue
            inside = geometry_mask(
                [geometry_json],
                out_shape=probability.shape,
                transform=probability_source.window_transform(window),
                invert=True,
            )
            valid = inside & np.isfinite(probability) & (probability >= 0)
            if probability_source.nodata is not None:
                valid &= probability != probability_source.nodata
            if not valid.any():
                continue
            geometry_wgs84 = transform_geometry(to_wgs84, geometry_projected)
            yield {
                "type": "Feature",
                "geometry": mapping(geometry_wgs84),
                "properties": {
                    "polygon_id": polygon_id,
                    "area_km2": area_km2,
                    "mean_probability": float(probability[valid].mean()),
                    "max_probability": float(probability[valid].max()),
                },
            }
            polygon_id += 1


def polygonize_flood(
    probability_path: Path,
    mask_path: Path,
    output_path: Path,
    min_area_km2: float = 0.001,
) -> int:
    if min_area_km2 < 0:
        raise ValueError("Minimum polygon area cannot be negative")
    with (
        rasterio.open(probability_path) as probability_source,
        rasterio.open(mask_path) as mask_source,
    ):
        assert_aligned(probability_source, mask_source)
        crs = probability_source.crs
        if crs is None or not crs.is_projected or crs.linear_units_factor[1] != 1.0:
            raise ValleyeyeError(
                ErrorCode.PREPROCESSING_FAILED,
                "Flood polygonization requires a projected metric raster.",
                stage="HAZARD",
            )
        return _write_feature_collection(
            output_path,
            _flood_features(probability_source, mask_source, min_area_km2),
        )


def _debris_features(
    pre_source: Any,
    post_source: Any,
    flood_source: Any,
    change_threshold_db: float,
    min_area_km2: float,
) -> Iterator[dict[str, Any]]:
    to_wgs84 = Transformer.from_crs(pre_source.crs, "EPSG:4326", always_xy=True).transform
    candidate_id = 0
    for window in _windows(pre_source.width, pre_source.height):
        pre = pre_source.read((1, 2), window=window).astype(np.float32, copy=False)
        post = post_source.read((1, 2), window=window).astype(np.float32, copy=False)
        flood = flood_source.read(1, window=window)
        valid = np.all(pre_source.read_masks((1, 2), window=window) > 0, axis=0)
        valid &= np.all(post_source.read_masks((1, 2), window=window) > 0, axis=0)
        valid &= flood_source.read_masks(1, window=window) > 0
        valid &= np.isfinite(pre).all(axis=0) & np.isfinite(post).all(axis=0) & (flood == 0)
        valid &= (pre > 0).all(axis=0) & (post > 0).all(axis=0)
        with np.errstate(divide="ignore", invalid="ignore"):
            vv_change_db = np.abs(10 * np.log10(post[0]) - 10 * np.log10(pre[0]))
        candidate_mask = valid & (vv_change_db >= change_threshold_db)
        transform = pre_source.window_transform(window)
        for geometry_json, _ in shapes(
            candidate_mask.astype(np.uint8), mask=candidate_mask, transform=transform
        ):
            geometry_projected = shape(geometry_json)
            area_km2 = geometry_projected.area / 1_000_000
            if area_km2 < min_area_km2:
                continue
            inside = geometry_mask(
                [geometry_json],
                out_shape=candidate_mask.shape,
                transform=transform,
                invert=True,
            )
            selected = vv_change_db[inside & valid]
            if not selected.size:
                continue
            mean_change = float(selected.mean())
            score = min(1.0, mean_change / (2 * change_threshold_db))
            confidence = "low" if score < 0.5 else "medium" if score < 0.75 else "high"
            geometry_wgs84 = transform_geometry(to_wgs84, geometry_projected)
            yield {
                "type": "Feature",
                "geometry": mapping(geometry_wgs84),
                "properties": {
                    "candidate_id": candidate_id,
                    "claim_level": "candidate",
                    "evidence": ["VV backscatter change outside modeled flood extent"],
                    "evidence_scores": {"absolute_vv_change_db": mean_change},
                    "confidence_score": score,
                    "confidence": confidence,
                    "confidence_scale": "uncalibrated heuristic",
                    "area_km2": area_km2,
                    "limitations": "Backscatter change only; debris is not confirmed.",
                },
            }
            candidate_id += 1


def polygonize_debris_candidates(
    pre_event_path: Path,
    post_event_path: Path,
    flood_mask_path: Path,
    output_path: Path,
    change_threshold_db: float = 3.0,
    min_area_km2: float = 0.001,
) -> int:
    if change_threshold_db <= 0 or min_area_km2 < 0:
        raise ValueError(
            "Candidate change threshold must be positive and minimum area non-negative"
        )
    with (
        rasterio.open(pre_event_path) as pre_source,
        rasterio.open(post_event_path) as post_source,
        rasterio.open(flood_mask_path) as flood_source,
    ):
        assert_aligned(pre_source, post_source, flood_source)
        crs = pre_source.crs
        if crs is None or not crs.is_projected or crs.linear_units_factor[1] != 1.0:
            raise ValleyeyeError(
                ErrorCode.PREPROCESSING_FAILED,
                "Candidate polygonization requires a projected metric raster.",
                stage="HAZARD",
            )
        if pre_source.count < 2 or post_source.count < 2:
            raise ValleyeyeError(
                ErrorCode.PREPROCESSING_FAILED,
                "Candidate mapping requires VV and VH bands in both input rasters.",
                stage="HAZARD",
            )
        return _write_feature_collection(
            output_path,
            _debris_features(
                pre_source,
                post_source,
                flood_source,
                change_threshold_db,
                min_area_km2,
            ),
        )
