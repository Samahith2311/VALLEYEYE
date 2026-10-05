from __future__ import annotations

import json
import os
import stat
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import numpy as np
import rasterio
from pyproj import CRS, Transformer
from rasterio.features import rasterize
from rasterio.warp import Resampling, reproject
from shapely.geometry import shape
from shapely.ops import transform as transform_geometry

from evaluation.freeze import verify_frozen_run


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _geojson_reference(
    path: Path, transform: Any, crs: CRS, shape_hw: tuple[int, int]
) -> np.ndarray[Any, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    features = document.get("features")
    if not isinstance(features, list):
        raise ValueError("Vector reference must be a GeoJSON FeatureCollection")
    source_crs = CRS.from_user_input(
        document.get("crs", {}).get("properties", {}).get("name", "EPSG:4326")
    )
    project = Transformer.from_crs(source_crs, crs, always_xy=True).transform
    geometries = []
    for feature in features:
        geometry = feature.get("geometry")
        if geometry:
            projected = transform_geometry(project, shape(geometry))
            if not projected.is_valid:
                raise ValueError("Vector reference contains an invalid geometry")
            if projected.geom_type not in {"Polygon", "MultiPolygon"}:
                raise ValueError("Vector reference features must be polygon geometries")
            if not projected.is_empty:
                geometries.append((projected, 1))
    return cast(
        np.ndarray[Any, Any],
        rasterize(
            geometries,
            out_shape=shape_hw,
            transform=transform,
            fill=0,
            all_touched=False,
            dtype="uint8",
        ),
    )


def compare_frozen_run(
    frozen_dir: Path,
    reference_path: Path,
    report_path: Path,
    prediction_artifact: str = "stages/INFERENCE/mask.tif",
) -> dict[str, Any]:
    """Compare a frozen flood mask with a local raster or GeoJSON reference."""
    frozen_root = frozen_dir.resolve(strict=True)
    manifest = verify_frozen_run(frozen_root)
    reference = reference_path.resolve(strict=True)
    report = report_path.resolve()
    if report == frozen_root or report.is_relative_to(frozen_root):
        raise ValueError("Evaluation results must be written outside the frozen run")
    if prediction_artifact not in manifest["artifacts"]:
        raise ValueError(f"Prediction is not a registered frozen artifact: {prediction_artifact}")
    prediction_path = frozen_root / prediction_artifact
    if reference == prediction_path:
        raise ValueError("Reference and prediction must be separate files")

    with rasterio.open(prediction_path) as prediction_source:
        prediction = prediction_source.read(1)
        valid_prediction = prediction_source.read_masks(1) > 0
        valid_prediction &= np.isfinite(prediction)
        predicted_flood = prediction > 0
        grid_crs = CRS.from_user_input(prediction_source.crs)
        if not grid_crs.is_projected or grid_crs.axis_info[0].unit_conversion_factor != 1.0:
            raise ValueError("Prediction raster must use a projected metre grid")

        if reference.suffix.casefold() in {".geojson", ".json"}:
            reference_flood = _geojson_reference(
                reference,
                prediction_source.transform,
                grid_crs,
                prediction.shape,
            ).astype(bool)
            valid = valid_prediction
        else:
            with rasterio.open(reference) as reference_source:
                reference_values = np.full(prediction.shape, 255, dtype=np.uint8)
                source_values = reference_source.read(1)
                source_valid = reference_source.read_masks(1) > 0
                source_binary = np.where(source_valid, source_values > 0, 255).astype(np.uint8)
                reproject(
                    source=source_binary,
                    destination=reference_values,
                    src_transform=reference_source.transform,
                    src_crs=reference_source.crs,
                    src_nodata=255,
                    dst_transform=prediction_source.transform,
                    dst_crs=prediction_source.crs,
                    dst_nodata=255,
                    resampling=Resampling.nearest,
                )
                valid = valid_prediction & (reference_values != 255)
                reference_flood = reference_values == 1

        truth = reference_flood[valid]
        forecast = predicted_flood[valid]
        tp = int(np.count_nonzero(truth & forecast))
        fp = int(np.count_nonzero(~truth & forecast))
        fn = int(np.count_nonzero(truth & ~forecast))
        tn = int(np.count_nonzero(~truth & ~forecast))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        iou = tp / (tp + fp + fn) if tp + fp + fn else 0.0
        pixel_area_m2 = abs(
            prediction_source.transform.a * prediction_source.transform.e
            - prediction_source.transform.b * prediction_source.transform.d
        )

    result: dict[str, Any] = {
        "schema_version": "1.0.0",
        "job_id": manifest.get("job_id"),
        "production_status": manifest.get("production_status"),
        "prediction_artifact": prediction_artifact,
        "prediction_sha256": manifest["artifacts"][prediction_artifact]["sha256"],
        "reference_sha256": _hash_file(reference),
        "reference_format": "geojson"
        if reference.suffix.casefold() in {".geojson", ".json"}
        else "raster",
        "valid_pixel_count": int(np.count_nonzero(valid)),
        "pixel_area_m2": pixel_area_m2,
        "confusion_matrix_pixels": {
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "true_negative": tn,
        },
        "area_comparison_m2": {
            "predicted_flood": float((tp + fp) * pixel_area_m2),
            "reference_flood": float((tp + fn) * pixel_area_m2),
        },
        "metrics": {"iou": iou, "precision": precision, "recall": recall, "f1": f1},
        "limitations": [
            "Reference source, delineation policy, acquisition time, AOI coverage, and "
            "resolution may differ.",
            "Metrics cover only valid pixels on the production prediction grid.",
            "Production thresholds and weights were not changed by this evaluation.",
        ],
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    temporary = report.with_name(report.name + ".part")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, report)
    report.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    return result
