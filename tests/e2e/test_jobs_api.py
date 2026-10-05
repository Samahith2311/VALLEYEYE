from __future__ import annotations

import asyncio
import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from valleyeye.api.app import create_app
from valleyeye.core.errors import ErrorCode, ValleyeyeError
from valleyeye.core.settings import Settings
from valleyeye.pipeline.models import PIPELINE_STAGES, AnalysisDocument, StageOutcome
from valleyeye.pipeline.runner import StageContext, StageHandler

REQUEST = {
    "aoi_geojson": {
        "type": "Polygon",
        "coordinates": [[[85.0, 27.0], [85.02, 27.0], [85.02, 27.02], [85.0, 27.02], [85.0, 27.0]]],
    },
    "event_date": "2026-08-15",
}


class VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"style", "script"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"style", "script"} and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        job_data_dir=tmp_path / "jobs",
        stage_cache_dir=tmp_path / "cache",
        cdse_username="private-user",
        cdse_password=SecretStr("private-password"),
        cdse_totp=SecretStr("private-totp"),
        ohsome_api_key=SecretStr("private-ohsome-key"),
    )


def _wait_for_status(client: TestClient, job_id: str, statuses: set[str]) -> dict[str, Any]:
    for _ in range(500):
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        result = cast(dict[str, Any], response.json())
        if result["status"] in statuses:
            return result
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach {statuses}")


def _handlers(calls: list[str]) -> dict[str, StageHandler]:
    handlers: dict[str, StageHandler] = {}
    for stage in PIPELINE_STAGES:

        async def handle(context: StageContext, *, _stage: str = stage) -> StageOutcome:
            calls.append(_stage)
            await context.progress(40)
            layer = context.stage_dir / f"{_stage.lower()}.geojson"
            layer.write_text(
                json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8"
            )
            await context.progress(90)
            data: dict[str, Any] = {
                "layer": layer.relative_to(context.job_dir).as_posix(),
                "flood_polygon_count": 7 if _stage == "HAZARD" else 1,
            }
            if _stage == "DISCOVERY":
                data["scenes"] = [
                    {"asset_href": "https://download.dataspace.copernicus.eu/?token=private-url"}
                ]
                data["aoi_area_km2"] = 1.25
                data["aoi_bbox_wgs84"] = [85.0, 27.0, 85.02, 27.02]
            if _stage == "CONNECTIVITY":
                data.update(
                    {
                        "summary": {
                            "connected": 1,
                            "detour": 1,
                            "cut_off": 1,
                            "no_baseline_access": 0,
                        },
                        "settlement_count": 3,
                        "results": [
                            {
                                "settlement_id": "node/12",
                                "name": "North Camp",
                                "status": "CUT_OFF",
                                "before_distance_m": 2500.0,
                                "after_distance_m": None,
                                "before_time_s": 300.0,
                                "after_time_s": None,
                                "detour_ratio": None,
                                "nearest_source_before": "node/3",
                                "nearest_source_after": None,
                                "blocking_road_ids": ["way/9"],
                            }
                        ],
                    }
                )
            return StageOutcome(data=data)

        handlers[stage] = handle
    return handlers


@pytest.mark.e2e
def test_job_lifecycle_results_cache_and_secret_free_provenance(tmp_path: Path) -> None:
    calls: list[str] = []
    app = create_app(_settings(tmp_path), _handlers(calls))
    with TestClient(app) as client:
        first = client.post("/api/v1/jobs", json=REQUEST)
        assert first.status_code == 202
        first_job = first.json()["job_id"]
        first_state = _wait_for_status(client, first_job, {"SUCCEEDED", "FAILED"})
        assert first_state["status"] == "SUCCEEDED"
        assert first_state["progress"] == 100
        assert [stage["name"] for stage in first_state["stages"]] == list(PIPELINE_STAGES)
        assert all(stage["status"] == "SUCCEEDED" for stage in first_state["stages"])
        assert len(calls) == len(PIPELINE_STAGES)

        summary = client.get(f"/api/v1/jobs/{first_job}/summary")
        assert summary.status_code == 200
        AnalysisDocument.model_validate(summary.json())
        assert summary.json()["hazards"]["hazard"]["flood_polygon_count"] == 7
        assert "private-url" not in summary.text
        layers = client.get(f"/api/v1/jobs/{first_job}/layers").json()["layers"]
        assert len(layers) == len(PIPELINE_STAGES)
        layer = client.get(layers[0]["url"])
        assert layer.status_code == 200
        assert layer.json()["type"] == "FeatureCollection"
        denied = client.get(f"/api/v1/jobs/{first_job}/layers/request.json")
        assert denied.status_code == 404
        report = client.get(f"/api/v1/jobs/{first_job}/report")
        assert report.status_code == 200
        expected_count = summary.json()["hazards"]["hazard"]["flood_polygon_count"]
        assert f"<td>{expected_count}</td>" in report.text
        text_parser = VisibleTextParser()
        text_parser.feed(report.text)
        report_numbers = set(re.findall(r"\d+(?:\.\d+)?", " ".join(text_parser.parts)))
        analysis_json = json.dumps(summary.json(), ensure_ascii=True)
        assert report_numbers
        assert all(number in analysis_json for number in report_numbers)
        assert "North Camp" in report.text

        second = client.post("/api/v1/jobs", json=REQUEST)
        second_job = second.json()["job_id"]
        second_state = _wait_for_status(client, second_job, {"SUCCEEDED", "FAILED"})
        assert second_state["status"] == "SUCCEEDED"
        assert all(stage["cache"] == "hit" for stage in second_state["stages"])
        assert len(calls) == len(PIPELINE_STAGES)
        second_layers = client.get(f"/api/v1/jobs/{second_job}/layers").json()["layers"]
        assert client.get(second_layers[0]["url"]).status_code == 200

        manifest_response = client.get(f"/api/v1/jobs/{second_job}/provenance")
        assert manifest_response.status_code == 200
        manifest = manifest_response.text
        for secret in (
            "private-user",
            "private-password",
            "private-totp",
            "private-ohsome-key",
            "private-url",
        ):
            assert secret not in manifest
        artifact_records = manifest_response.json()["artifacts"]
        assert "run_manifest.json" in second_state["artifacts"]
        assert "run_manifest.json" not in artifact_records


@pytest.mark.e2e
def test_job_cache_bypass_and_structured_stage_failure(tmp_path: Path) -> None:
    calls: list[str] = []
    handlers = _handlers(calls)
    app = create_app(_settings(tmp_path), handlers)
    with TestClient(app) as client:
        bypass = client.post("/api/v1/jobs?no_cache=true", json=REQUEST)
        assert bypass.status_code == 202
        bypass_state = _wait_for_status(client, bypass.json()["job_id"], {"SUCCEEDED", "FAILED"})
        assert all(stage["cache"] == "bypass" for stage in bypass_state["stages"])

        async def fail_osm(_: StageContext) -> StageOutcome:
            raise ValleyeyeError(
                ErrorCode.OSM_UNAVAILABLE,
                "Historical OSM extraction requires OHSOME_API_KEY.",
                "OSM",
                details={"provider": "ohsome-api-v2"},
            )

        handlers["OSM"] = fail_osm
        failed = client.post("/api/v1/jobs?no_cache=true", json=REQUEST)
        failed_id = failed.json()["job_id"]
        state = _wait_for_status(client, failed_id, {"FAILED", "SUCCEEDED"})
        assert state["status"] == "FAILED"
        assert state["error"]["code"] == "OSM_UNAVAILABLE"
        assert state["error"]["stage"] == "OSM"
        assert state["stages"][4]["status"] == "SUCCEEDED"
        assert state["stages"][5]["status"] == "FAILED"
        assert state["stages"][6]["status"] == "QUEUED"
        not_ready = client.get(f"/api/v1/jobs/{failed_id}/summary")
        assert not_ready.status_code == 409
        assert not_ready.json()["error"]["code"] == "JOB_NOT_READY"


@pytest.mark.e2e
def test_running_job_can_be_cancelled(tmp_path: Path) -> None:
    async def wait_forever(_: StageContext) -> StageOutcome:
        await asyncio.sleep(30)
        return StageOutcome()

    handlers = _handlers([])
    handlers["DISCOVERY"] = wait_forever
    with TestClient(create_app(_settings(tmp_path), handlers)) as client:
        response = client.post("/api/v1/jobs?no_cache=true", json=REQUEST)
        job_id = response.json()["job_id"]
        running = _wait_for_status(client, job_id, {"RUNNING", "FAILED"})
        assert running["status"] == "RUNNING"
        cancelled = client.post(f"/api/v1/jobs/{job_id}/cancel")
        assert cancelled.status_code == 200
        state = cancelled.json()
        assert state["status"] == "FAILED"
        assert state["error"]["code"] == "JOB_CANCELLED"
        assert state["error"]["stage"] == "DISCOVERY"
