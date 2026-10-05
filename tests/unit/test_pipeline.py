from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from valleyeye.api.schemas import AnalysisRequest
from valleyeye.core.errors import ErrorCode, ValleyeyeError
from valleyeye.pipeline.cache import StageCache
from valleyeye.pipeline.defaults import _download_product
from valleyeye.pipeline.models import PIPELINE_STAGES, AnalysisDocument, StageOutcome, StageRecord
from valleyeye.pipeline.store import JobStore

REQUEST = AnalysisRequest(
    aoi_geojson={
        "type": "Polygon",
        "coordinates": [[[85.0, 27.0], [85.02, 27.0], [85.02, 27.02], [85.0, 27.02], [85.0, 27.0]]],
    },
    event_date="2026-08-15",
)


def test_job_store_persists_and_marks_interrupted_jobs_failed(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs")
    record, job_dir = store.create(REQUEST)
    record.status = "RUNNING"
    record.stages = [StageRecord(name=name) for name in PIPELINE_STAGES]
    record.stages[3].status = "RUNNING"
    record.current_stage = "INFERENCE"
    store.save(record)

    restarted = JobStore(tmp_path / "jobs")
    restarted.recover_interrupted()
    recovered = restarted.get(record.job_id)

    assert restarted.request(record.job_id) == REQUEST
    assert job_dir.exists()
    assert recovered.status == "FAILED"
    assert recovered.error is not None
    assert recovered.error.code == ErrorCode.JOB_CANCELLED.value
    assert recovered.error.stage == "INFERENCE"
    assert recovered.stages[3].status == "FAILED"
    assert restarted.pending_count() == 0


def test_stage_cache_round_trips_outputs_and_keys_inputs(tmp_path: Path) -> None:
    cache = StageCache(tmp_path / "cache", ttl_seconds=3600)
    request = REQUEST.model_dump(mode="json")
    config = {"flood_threshold": 0.5}
    data = {"PAIRING": {"pre_scene": "S1A"}}
    key = cache.key("INFERENCE", request, config, data)
    assert key == cache.key("INFERENCE", request, config, data)
    assert key != cache.key("INFERENCE", request, config, data, "new-build")
    assert key != cache.key("INFERENCE", request, config, {"PAIRING": {"pre_scene": "S1B"}})

    source = tmp_path / "job-a" / "stage"
    source.mkdir(parents=True)
    (source / "layer.geojson").write_text("{}", encoding="utf-8")
    outcome = StageOutcome(data={"layer": "stages/inference/layer.geojson"})
    cache.save(key, source, outcome)
    destination = tmp_path / "job-b" / "stage"

    restored = cache.load(key, destination)

    assert restored == outcome
    assert (destination / "layer.geojson").read_text(encoding="utf-8") == "{}"


def test_expired_stage_cache_entry_is_not_reused(tmp_path: Path) -> None:
    cache = StageCache(tmp_path / "cache", ttl_seconds=1)
    key = cache.key("HAZARD", {}, {}, {})
    source = tmp_path / "stage"
    source.mkdir()
    cache.save(key, source, StageOutcome(data={"count": 4}))
    metadata_path = tmp_path / "cache" / key / "cache.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["created_at"] = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    assert cache.load(key, tmp_path / "restored") is None


def test_product_download_rejects_untrusted_host_before_network(tmp_path: Path) -> None:
    async def run() -> None:
        with pytest.raises(ValleyeyeError) as error:
            await _download_product(
                "https://download.dataspace.copernicus.eu.attacker.example/file.zip",
                tmp_path / "product.zip",
                "not-a-real-token",
                1,
            )
        assert error.value.code == ErrorCode.CDSE_UNAVAILABLE

    asyncio.run(run())


def test_published_analysis_schema_matches_pydantic_model() -> None:
    schema_path = Path(__file__).resolve().parents[2] / "docs" / "analysis.schema.json"
    published = json.loads(schema_path.read_text(encoding="utf-8"))

    assert published == AnalysisDocument.model_json_schema()
