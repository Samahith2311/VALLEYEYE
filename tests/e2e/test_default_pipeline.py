from __future__ import annotations

import time
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, box

from valleyeye.api.app import create_app
from valleyeye.cdse.models import SceneMetadata
from valleyeye.core.settings import Settings
from valleyeye.ml.model import FloodModelMetadata
from valleyeye.osm.models import OSMDataset, OSMFeature, OSMQualitySummary
from valleyeye.pipeline import defaults
from valleyeye.pipeline.models import PIPELINE_STAGES

REQUEST: dict[str, Any] = {
    "aoi_geojson": {
        "type": "Polygon",
        "coordinates": [[[85.0, 27.0], [85.02, 27.0], [85.02, 27.02], [85.0, 27.02], [85.0, 27.0]]],
    },
    "event_date": "2026-08-15",
}


class SyntheticFloodModel:
    metadata = FloodModelMetadata(
        name="Synthetic SNUNet contract model",
        version="e2e-stub",
        weights_sha256="a" * 64,
        source_url="https://example.invalid/synthetic-model",
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

    def predict_tile(self, pre_event: np.ndarray, post_event: np.ndarray) -> np.ndarray:
        assert pre_event.shape == post_event.shape
        return np.full(pre_event.shape[-2:], 0.9, dtype=np.float32)


def _synthetic_osm() -> OSMDataset:
    snapshot = date(2026, 8, 14)
    features = (
        OSMFeature(
            "way/100",
            "way",
            100,
            "road",
            LineString([(85.0, 27.003), (85.02, 27.003)]),
            {"highway": "primary", "bridge": "yes", "maxspeed": "40"},
            snapshot,
        ),
        OSMFeature(
            "way/200",
            "way",
            200,
            "building",
            box(85.003, 27.002, 85.0032, 27.0022),
            {"building": "yes"},
            snapshot,
        ),
        OSMFeature(
            "node/10",
            "node",
            10,
            "settlement",
            Point(85.012, 27.003),
            {"place": "village", "name": "North Camp"},
            snapshot,
        ),
        OSMFeature(
            "node/11",
            "node",
            11,
            "settlement",
            Point(85.001, 27.003),
            {"place": "town", "name": "River Town"},
            snapshot,
        ),
        OSMFeature(
            "node/12",
            "node",
            12,
            "healthcare",
            Point(85.002, 27.003),
            {"amenity": "hospital", "name": "Valley Hospital"},
            snapshot,
        ),
    )
    return OSMDataset(
        provider="ohsome-api-v2",
        snapshot_at=datetime(2026, 8, 14, 23, 59, 59, tzinfo=UTC),
        response_sha256=sha256(b"synthetic-osm").hexdigest(),
        query_sha256=sha256(b"synthetic-query").hexdigest(),
        query={"time": "2026-08-14T23:59:59Z"},
        features=features,
        quality=OSMQualitySummary(5, 5, 0, 0, 0, 0),
    )


def _wait(client: TestClient, job_id: str) -> dict[str, Any]:
    for _ in range(800):
        response = client.get(f"/api/v1/jobs/{job_id}")
        result = response.json()
        if result["status"] in {"SUCCEEDED", "PARTIAL", "FAILED"}:
            return result
        time.sleep(0.01)
    raise AssertionError("default pipeline job did not finish")


@pytest.mark.e2e
def test_default_pipeline_with_mocked_services_and_synthetic_products(
    tmp_path: Path, monkeypatch: Any
) -> None:
    scenes = [
        SceneMetadata(
            scene_id=f"S1A_{label}",
            platform="sentinel-1a",
            acquired_at=acquired,
            product_type="GRD",
            instrument_mode="IW",
            polarizations=("VV", "VH"),
            orbit_direction="ASCENDING",
            relative_orbit=36,
            geometry=REQUEST["aoi_geojson"],
            aoi_coverage_fraction=1.0,
            asset_href=f"https://download.dataspace.copernicus.eu/products/{label}.zip",
        )
        for label, acquired in (
            ("PRE", datetime(2026, 8, 12, tzinfo=UTC)),
            ("POST", datetime(2026, 8, 17, tzinfo=UTC)),
        )
    ]
    calls = {"stac": 0, "download": 0, "snap": 0, "osm": 0}

    async def fake_search(
        self: Any, aoi: dict[str, Any], start: datetime, end: datetime
    ) -> list[SceneMetadata]:
        calls["stac"] += 1
        return scenes

    async def fake_token(self: Any) -> str:
        return "synthetic-access-token"

    async def fake_download(href: str, destination: Path, token: str, timeout: float) -> Path:
        assert "synthetic-access-token" == token
        calls["download"] += 1
        destination.write_bytes(href.encode("utf-8"))
        return destination

    def fake_snap(
        source: Path,
        output: Path,
        aoi: Any,
        target_crs: Any,
        gpt_path: Path,
        pixel_spacing_m: float = 10.0,
        aoi_buffer_m: float = 500.0,
        cancel_event: Any = None,
    ) -> tuple[str, ...]:
        calls["snap"] += 1
        transformer = Transformer.from_crs("EPSG:4326", target_crs, always_xy=True)
        left, bottom = transformer.transform(85.0, 27.0)
        _, top = transformer.transform(85.0, 27.02)
        profile = {
            "driver": "GTiff",
            "width": 256,
            "height": 256,
            "count": 3,
            "dtype": "float32",
            "crs": target_crs,
            "transform": from_origin(left - 100, top + 100, pixel_spacing_m, pixel_spacing_m),
            "nodata": -9999.0,
        }
        rows = np.arange(256, dtype=np.float32)[:, None]
        with rasterio.open(output, "w", **profile) as dataset:
            dataset.write(np.full((256, 256), 0.05, dtype=np.float32), 1)
            dataset.write(np.full((256, 256), 0.02, dtype=np.float32), 2)
            dataset.write(np.broadcast_to(rows, (256, 256)).copy(), 3)
            dataset.set_band_description(1, "Sigma0_VV")
            dataset.set_band_description(2, "Sigma0_VH")
            dataset.set_band_description(3, "elevation")
        return ("synthetic SNAP steps",)

    def fake_osm(self: Any, aoi: Any, event_date: date) -> OSMDataset:
        calls["osm"] += 1
        return _synthetic_osm()

    monkeypatch.setattr(defaults.CDSESTACClient, "search_sentinel1", fake_search)
    monkeypatch.setattr(defaults.CDSEAuth, "access_token", fake_token)
    monkeypatch.setattr(defaults, "_download_product", fake_download)
    monkeypatch.setattr(defaults, "run_snap_preprocessing", fake_snap)
    monkeypatch.setattr(defaults.SNUNetFloodModel, "load", lambda *args: SyntheticFloodModel())
    monkeypatch.setattr(defaults.OhSomeOSMProvider, "fetch", fake_osm)

    gpt = tmp_path / "gpt.exe"
    gpt.touch()
    settings = Settings(
        job_data_dir=tmp_path / "jobs",
        stage_cache_dir=tmp_path / "cache",
        model_weights_path=tmp_path / "stub-weights.pt",
        snap_gpt_path=gpt,
        max_concurrent_jobs=1,
        CDSE_USERNAME="synthetic-user",
        CDSE_PASSWORD=SecretStr("synthetic-password"),
        OHSOME_API_KEY=SecretStr("synthetic-ohsome-key"),
    )

    with TestClient(create_app(settings)) as client:
        submitted = client.post("/api/v1/jobs", json=REQUEST)
        assert submitted.status_code == 202
        job_id = submitted.json()["job_id"]
        state = _wait(client, job_id)
        assert state["status"] == "SUCCEEDED", state.get("error")
        assert [stage["name"] for stage in state["stages"]] == list(PIPELINE_STAGES)
        assert all(stage["status"] == "SUCCEEDED" for stage in state["stages"])

        summary = client.get(f"/api/v1/jobs/{job_id}/summary").json()
        assert summary["hazards"]["inference"]["flooded_area_km2"] > 0
        assert summary["infrastructure"]["osm"]["feature_count"] == 5
        assert summary["infrastructure"]["osm"]["attribution"] == "© OpenStreetMap contributors"
        assert summary["infrastructure"]["osm"]["license"] == "ODbL 1.0"
        assert summary["infrastructure"]["exposure"]["bridge_count"] == 1
        layer_catalog = client.get(f"/api/v1/jobs/{job_id}/layers").json()["layers"]
        assert any(layer["path"].endswith("flood.geojson") for layer in layer_catalog)
        assert any(layer["path"].endswith("settlements.geojson") for layer in layer_catalog)
        report = client.get(f"/api/v1/jobs/{job_id}/report")
        assert report.status_code == 200
        assert "North Camp" in report.text
        assert "© OpenStreetMap contributors" in report.text
        manifest = client.get(f"/api/v1/jobs/{job_id}/provenance").json()
        assert manifest["selected_scenes"] == {"pre": "S1A_PRE", "post": "S1A_POST"}
        assert len(manifest["inputs"]["files"]) == 2
        assert manifest["model"]["model_sha256"] == "a" * 64

        second = client.post("/api/v1/jobs", json=REQUEST)
        second_state = _wait(client, second.json()["job_id"])
        assert second_state["status"] == "SUCCEEDED"
        assert all(stage["cache"] == "hit" for stage in second_state["stages"])
        assert calls == {"stac": 1, "download": 2, "snap": 2, "osm": 1}
