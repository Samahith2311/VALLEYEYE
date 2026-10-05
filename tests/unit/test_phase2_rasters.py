from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest
import rasterio
from pyproj import CRS
from rasterio.transform import from_origin
from shapely.geometry import box

from valleyeye.core.errors import ErrorCode, ValleyeyeError
from valleyeye.hazard.products import polygonize_debris_candidates, polygonize_flood
from valleyeye.ml.inference import InferenceConfig, infer_rasters
from valleyeye.ml.model import FloodModelMetadata
from valleyeye.sar.raster import (
    align_raster,
    compute_slope_riserun,
    extract_snap_bands,
    write_slope_raster,
)
from valleyeye.sar.snap import build_snap_graph


class ConstantFloodModel:
    metadata = FloodModelMetadata(
        name="test model",
        version="test",
        weights_sha256="a" * 64,
        source_url="https://example.invalid/model",
        verified_on="2026-10-05",
        input_order=("pre_event", "post_event"),
        channels_per_time=("vv", "vh", "slope_riserun"),
        input_mean=(0.0953, 0.0264),
        input_std=(0.0427, 0.0215),
        slope_mean=2.9482,
        slope_std=79.2493,
        training_patch_size=224,
        output_classes=("no_water", "permanent_water", "flood"),
    )

    def __init__(self, probability: float = 0.75) -> None:
        self.probability = probability
        self.seen_shapes: list[tuple[int, ...]] = []

    def predict_tile(self, pre_event: np.ndarray, post_event: np.ndarray) -> np.ndarray:
        self.seen_shapes.append(pre_event.shape)
        assert pre_event.shape == post_event.shape
        return np.full(pre_event.shape[-2:], self.probability, dtype=np.float32)


def _write_raster(
    path: Path,
    data: np.ndarray,
    transform: rasterio.Affine | None = None,
    nodata: float = -9999.0,
) -> Path:
    count, height, width = data.shape
    profile = {
        "driver": "GTiff",
        "width": width,
        "height": height,
        "count": count,
        "dtype": data.dtype,
        "crs": "EPSG:32645",
        "transform": transform or from_origin(300000, 3100000, 10, 10),
        "nodata": nodata,
    }
    with rasterio.open(path, "w", **profile) as destination:
        destination.write(data)
    return path


def test_horn_slope_matches_a_linear_riserun_surface() -> None:
    rows, cols = np.mgrid[0:7, 0:7]
    dem = (3 * rows + 2 * cols).astype(np.float32)
    slope = compute_slope_riserun(dem, pixel_size_x=1, pixel_size_y=1)
    assert slope[3, 3] == pytest.approx(np.sqrt(13), rel=1e-5)


def test_slope_raster_is_written_with_bounded_windows(tmp_path: Path) -> None:
    rows, cols = np.mgrid[0:16, 0:16]
    dem = (3 * rows + 2 * cols).astype(np.float32)[None, :, :]
    dem_path = _write_raster(tmp_path / "dem.tif", dem)
    slope_path = tmp_path / "slope.tif"

    write_slope_raster(dem_path, slope_path)

    with rasterio.open(slope_path) as slope:
        assert slope.read(1)[8, 8] == pytest.approx(np.sqrt(0.13), rel=1e-5)
        assert slope.nodata == -9999


def test_continuous_raster_alignment_uses_reference_grid(tmp_path: Path) -> None:
    reference = _write_raster(
        tmp_path / "reference.tif",
        np.zeros((1, 20, 20), dtype=np.float32),
    )
    source = _write_raster(
        tmp_path / "source.tif",
        np.full((2, 18, 18), 0.05, dtype=np.float32),
        transform=from_origin(300010, 3099990, 10, 10),
    )
    aligned_path = tmp_path / "aligned.tif"

    align_raster(source, reference, aligned_path)

    with rasterio.open(reference) as reference_dataset, rasterio.open(aligned_path) as aligned:
        assert aligned.transform == reference_dataset.transform
        assert aligned.width == reference_dataset.width
        assert aligned.height == reference_dataset.height
        assert aligned.count == 2


def test_inference_streams_tiles_and_marks_nodata(tmp_path: Path) -> None:
    pre = np.full((2, 180, 175), 0.05, dtype=np.float32)
    post = np.full((2, 180, 175), 0.06, dtype=np.float32)
    slope = np.full((1, 180, 175), 2.0, dtype=np.float32)
    pre[:, 4, 5] = -9999
    post[:, 4, 5] = -9999
    slope[:, 4, 5] = -9999
    pre_path = _write_raster(tmp_path / "pre.tif", pre)
    post_path = _write_raster(tmp_path / "post.tif", post)
    slope_path = _write_raster(tmp_path / "slope.tif", slope)
    probability_path = tmp_path / "probability.tif"
    mask_path = tmp_path / "mask.tif"
    model = ConstantFloodModel()

    summary = infer_rasters(
        pre_path,
        post_path,
        slope_path,
        probability_path,
        mask_path,
        model,
        InferenceConfig(flood_probability_threshold=0.7),
    )

    assert summary.valid_pixels == 180 * 175 - 1
    assert summary.flooded_pixels == summary.valid_pixels
    assert summary.valid_pixel_fraction == pytest.approx(summary.valid_pixels / (180 * 175))
    assert model.seen_shapes == [(3, 224, 224)] * 4
    with rasterio.open(probability_path) as probability, rasterio.open(mask_path) as mask:
        assert probability.read(1)[0, 0] == pytest.approx(0.75)
        assert probability.read(1)[4, 5] == -9999
        assert mask.read(1)[4, 5] == 255
        assert mask.read(1)[0, 0] == 1
        assert probability.crs == CRS.from_epsg(32645)


def test_inference_rejects_misaligned_inputs(tmp_path: Path) -> None:
    pre = _write_raster(tmp_path / "pre.tif", np.ones((2, 32, 32), dtype=np.float32))
    post = _write_raster(
        tmp_path / "post.tif",
        np.ones((2, 32, 32), dtype=np.float32),
        transform=from_origin(300010, 3100000, 10, 10),
    )
    slope = _write_raster(tmp_path / "slope.tif", np.ones((1, 32, 32), dtype=np.float32))
    with pytest.raises(ValleyeyeError) as error:
        infer_rasters(
            pre,
            post,
            slope,
            tmp_path / "probability.tif",
            tmp_path / "mask.tif",
            ConstantFloodModel(),
        )
    assert error.value.code == ErrorCode.PREPROCESSING_FAILED


def test_flood_polygons_have_stats_and_wgs84_geometry(tmp_path: Path) -> None:
    mask = np.zeros((1, 20, 20), dtype=np.uint8)
    mask[0, 5:15, 6:16] = 1
    probability = np.zeros((1, 20, 20), dtype=np.float32)
    probability[0, 5:15, 6:16] = 0.8
    mask_path = _write_raster(tmp_path / "mask.tif", mask, nodata=255)
    probability_path = _write_raster(tmp_path / "prob.tif", probability)
    output = tmp_path / "flood.geojson"

    feature_count = polygonize_flood(probability_path, mask_path, output, min_area_km2=0)

    collection = json.loads(output.read_text(encoding="utf-8"))
    assert feature_count == 1
    properties = collection["features"][0]["properties"]
    assert properties["area_km2"] == pytest.approx(0.01)
    assert properties["mean_probability"] == pytest.approx(0.8)
    assert collection["features"][0]["geometry"]["type"] == "Polygon"
    assert collection["type"] == "FeatureCollection"


def test_debris_candidates_are_always_labeled_as_candidates(tmp_path: Path) -> None:
    pre = np.full((2, 20, 20), 0.04, dtype=np.float32)
    post = pre.copy()
    post[0, 5:15, 5:15] = 0.1
    flood = np.zeros((1, 20, 20), dtype=np.uint8)
    pre_path = _write_raster(tmp_path / "pre.tif", pre)
    post_path = _write_raster(tmp_path / "post.tif", post)
    flood_path = _write_raster(tmp_path / "flood.tif", flood, nodata=255)

    feature_count = polygonize_debris_candidates(
        pre_path, post_path, flood_path, tmp_path / "candidates.geojson", min_area_km2=0
    )

    features = json.loads((tmp_path / "candidates.geojson").read_text(encoding="utf-8"))["features"]
    assert feature_count > 0
    assert all(feature["properties"]["claim_level"] == "candidate" for feature in features)
    assert all(feature["properties"]["evidence"] for feature in features)
    assert all(
        feature["properties"]["confidence_scale"] == "uncalibrated heuristic"
        for feature in features
    )


def test_snap_graph_uses_aoi_crs_and_declares_processing_steps(tmp_path: Path) -> None:
    source = tmp_path / "source.zip"
    source.touch()
    graph = build_snap_graph(
        source,
        tmp_path / "out.tif",
        box(85, 27, 85.01, 27.01),
        CRS.from_epsg(32645),
    )
    root = ET.fromstring(graph)
    operators = [node.findtext("operator") for node in root.findall("node")]
    assert operators == [
        "Read",
        "Apply-Orbit-File",
        "Subset",
        "ThermalNoiseRemoval",
        "Remove-GRD-Border-Noise",
        "Calibration",
        "Speckle-Filter",
        "Terrain-Correction",
        "Write",
    ]
    params = {
        child.tag: child.text for child in root.find("node[@id='TerrainCorrection']/parameters")
    }
    assert "UTM zone 45N" in (params["mapProjection"] or "")
    assert params["pixelSpacingInMeter"] == "10.0"


def test_snap_bands_are_selected_by_name_not_assumed_position(tmp_path: Path) -> None:
    source_path = tmp_path / "snap.tif"
    source = _write_raster(
        source_path,
        np.stack(
            [
                np.full((12, 12), 200, dtype=np.float32),
                np.full((12, 12), 0.04, dtype=np.float32),
                np.full((12, 12), 0.02, dtype=np.float32),
                np.full((12, 12), 125.0, dtype=np.float32),
            ]
        ),
    )
    with rasterio.open(source, "r+") as dataset:
        for index, name in enumerate(
            ("layover_shadow_mask", "Sigma0_VV", "Sigma0_VH", "elevation"), 1
        ):
            dataset.set_band_description(index, name)
    backscatter_path = tmp_path / "backscatter.tif"
    dem_path = tmp_path / "dem.tif"

    extract_snap_bands(source_path, backscatter_path, dem_path)

    with rasterio.open(backscatter_path) as backscatter, rasterio.open(dem_path) as dem:
        assert backscatter.descriptions == ("Sigma0_VV", "Sigma0_VH")
        assert backscatter.read(1)[0, 0] == pytest.approx(0.04)
        assert backscatter.read(2)[0, 0] == pytest.approx(0.02)
        assert dem.read(1)[0, 0] == pytest.approx(125)
