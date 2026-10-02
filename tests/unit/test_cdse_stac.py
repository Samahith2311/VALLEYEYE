from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from valleyeye.cdse.stac import CDSESTACClient
from valleyeye.core.errors import ErrorCode, ValleyeyeError
from valleyeye.core.settings import Settings

AOI: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [0.02, 0], [0.02, 0.02], [0, 0.02], [0, 0]]],
}
FOOTPRINT: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[-0.05, -0.05], [0.07, -0.05], [0.07, 0.07], [-0.05, 0.07], [-0.05, -0.05]]],
}


def _item(scene_id: str, acquired_at: str) -> dict[str, Any]:
    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": scene_id,
        "geometry": FOOTPRINT,
        "properties": {
            "datetime": acquired_at,
            "platform": "sentinel-1a",
            "eo:cloud_cover": 11.5,
            "sar:product_type": "GRD",
            "sar:instrument_mode": "IW",
            "sar:polarizations": ["VH", "VV"],
            "sat:orbit_state": "ascending",
            "sat:relative_orbit": 55,
        },
        "links": [],
        "assets": {"data": {"href": "https://example.invalid/scene.zip", "roles": ["data"]}},
    }


def test_stac_search_paginates_and_normalizes_items() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={
                    "type": "FeatureCollection",
                    "features": [_item("pre", "2026-09-08T12:00:00Z")],
                    "links": [
                        {
                            "rel": "next",
                            "href": "https://stac.dataspace.copernicus.eu/v1/search?page=2",
                            "method": "GET",
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [_item("post", "2026-09-12T12:00:00Z")],
                "links": [],
            },
        )

    async def run() -> list[Any]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await CDSESTACClient(Settings(), client).search_sentinel1(
                AOI,
                datetime(2026, 9, 1, tzinfo=UTC),
                datetime(2026, 9, 20, tzinfo=UTC),
            )

    scenes = asyncio.run(run())

    assert calls == ["/v1/search", "/v1/search"]
    assert [scene.scene_id for scene in scenes] == ["pre", "post"]
    assert scenes[0].relative_orbit == 55
    assert scenes[0].polarizations == ("VH", "VV")
    assert scenes[0].aoi_coverage_fraction == pytest.approx(1.0)
    assert scenes[0].asset_href == "https://example.invalid/scene.zip"
    assert scenes[0].cloud_cover == 11.5


def test_sentinel1_search_passes_property_query_to_stac() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"type": "FeatureCollection", "features": [], "links": []})

    async def run() -> list[Any]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await CDSESTACClient(Settings(), client).search_sentinel1(
                AOI,
                datetime(2026, 9, 1, tzinfo=UTC),
                datetime(2026, 9, 20, tzinfo=UTC),
                {"sar:instrument_mode": {"eq": "IW"}},
            )

    assert asyncio.run(run()) == []
    assert captured["query"] == {"sar:instrument_mode": {"eq": "IW"}}


def test_sentinel2_search_filters_cloud_cover() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"type": "FeatureCollection", "features": [], "links": []})

    async def run() -> list[Any]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await CDSESTACClient(Settings(), client).search_sentinel2(
                AOI,
                datetime(2026, 9, 1, tzinfo=UTC),
                datetime(2026, 9, 20, tzinfo=UTC),
                max_cloud_cover=20,
            )

    assert asyncio.run(run()) == []
    assert captured["collections"] == ["sentinel-2-l2a"]
    assert captured["query"] == {"eo:cloud_cover": {"lte": 20}}


def test_stac_empty_result_returns_empty_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"type": "FeatureCollection", "features": [], "links": []})

    async def run() -> list[Any]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await CDSESTACClient(Settings(), client).search_sentinel1(
                AOI,
                datetime(2026, 9, 1, tzinfo=UTC),
                datetime(2026, 9, 20, tzinfo=UTC),
            )

    assert asyncio.run(run()) == []


def test_stac_malformed_response_is_structured() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"type": "FeatureCollection", "links": []})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await CDSESTACClient(Settings(), client).search_sentinel1(
                AOI,
                datetime(2026, 9, 1, tzinfo=UTC),
                datetime(2026, 9, 20, tzinfo=UTC),
            )

    with pytest.raises(ValleyeyeError) as caught:
        asyncio.run(run())

    assert caught.value.code == ErrorCode.CDSE_UNAVAILABLE
