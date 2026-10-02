from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from pystac import Item
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from valleyeye.cdse.auth import CDSETransientError
from valleyeye.cdse.models import SceneMetadata
from valleyeye.core.errors import ErrorCode, ValleyeyeError
from valleyeye.core.geometry import aoi_coverage_fraction, validate_aoi
from valleyeye.core.settings import Settings


class CDSESTACClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client
        self._search_url = f"{settings.stac_api_url.rstrip('/')}/search"

    async def search_sentinel1(
        self,
        aoi: dict[str, Any],
        start: datetime,
        end: datetime,
        property_query: dict[str, Any] | None = None,
    ) -> list[SceneMetadata]:
        return await self._search(self._settings.s1_collection, aoi, start, end, property_query)

    async def search_sentinel2(
        self,
        aoi: dict[str, Any],
        start: datetime,
        end: datetime,
        max_cloud_cover: float | None = None,
    ) -> list[SceneMetadata]:
        if max_cloud_cover is not None and not 0 <= max_cloud_cover <= 100:
            raise ValleyeyeError(
                ErrorCode.INVALID_SEARCH_FILTER,
                "Maximum Sentinel-2 cloud cover must be between 0 and 100 percent",
                "DISCOVERY",
            )
        query = (
            {"eo:cloud_cover": {"lte": max_cloud_cover}} if max_cloud_cover is not None else None
        )
        return await self._search(self._settings.s2_collection, aoi, start, end, query)

    async def _search(
        self,
        collection: str,
        aoi: dict[str, Any],
        start: datetime,
        end: datetime,
        property_query: dict[str, Any] | None,
    ) -> list[SceneMetadata]:
        geometry, _ = validate_aoi(aoi, self._settings)
        if start.tzinfo is None or end.tzinfo is None or end < start:
            raise ValleyeyeError(
                ErrorCode.INVALID_EVENT_DATE,
                "STAC search dates must be ordered timezone-aware datetimes",
                "DISCOVERY",
            )
        request_body: dict[str, Any] = {
            "collections": [collection],
            "intersects": aoi,
            "datetime": f"{start.astimezone(UTC).isoformat()}/{end.astimezone(UTC).isoformat()}",
            "limit": 100,
        }
        if property_query:
            request_body["query"] = property_query
        items: list[SceneMetadata] = []
        next_request: tuple[str, str, dict[str, Any] | None] | None = (
            "POST",
            self._search_url,
            request_body,
        )
        seen_pages: set[str] = set()
        while next_request is not None:
            method, url, body = next_request
            if urlparse(url).netloc != urlparse(self._settings.stac_api_url).netloc:
                raise ValleyeyeError(
                    ErrorCode.CDSE_UNAVAILABLE,
                    "CDSE STAC pagination pointed outside the configured service",
                    "DISCOVERY",
                )
            page = await self._get_page(method, url, body)
            page_url = page.get("url", url)
            if page_url in seen_pages:
                raise ValleyeyeError(
                    ErrorCode.CDSE_UNAVAILABLE,
                    "CDSE STAC pagination returned a repeated page",
                    "DISCOVERY",
                )
            seen_pages.add(page_url)
            raw_items = page.get("features")
            if not isinstance(raw_items, list):
                raise ValleyeyeError(
                    ErrorCode.CDSE_UNAVAILABLE,
                    "CDSE STAC response is missing its feature list",
                    "DISCOVERY",
                )
            for raw_item in raw_items:
                items.append(_scene_from_item(raw_item, geometry))
            next_request = self._next_page(page.get("links", []), url)
        return items

    async def _get_page(self, method: str, url: str, body: dict[str, Any] | None) -> dict[str, Any]:
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._settings.retry_max_attempts),
                wait=wait_random_exponential(multiplier=0.1, max=1),
                retry=retry_if_exception_type((httpx.TransportError, CDSETransientError)),
                reraise=True,
            ):
                with attempt:
                    response = await self._client.request(
                        method,
                        url,
                        json=body,
                        timeout=self._settings.http_timeout_seconds,
                    )
                    if response.status_code == 429 or response.status_code >= 500:
                        raise CDSETransientError(response.status_code)
                    if response.is_error:
                        code = (
                            ErrorCode.CDSE_AUTH_FAILED
                            if response.status_code in {401, 403}
                            else ErrorCode.CDSE_UNAVAILABLE
                        )
                        raise ValleyeyeError(
                            code,
                            "CDSE STAC search failed",
                            "DISCOVERY",
                            details={"status_code": response.status_code},
                        )
                    try:
                        result: Any = response.json()
                    except ValueError as exc:
                        raise ValleyeyeError(
                            ErrorCode.CDSE_UNAVAILABLE,
                            "CDSE STAC returned malformed JSON",
                            "DISCOVERY",
                        ) from exc
                    if not isinstance(result, dict):
                        raise ValleyeyeError(
                            ErrorCode.CDSE_UNAVAILABLE,
                            "CDSE STAC response must be a JSON object",
                            "DISCOVERY",
                        )
                    result["url"] = url
                    return result
        except httpx.TransportError as exc:
            raise ValleyeyeError(
                ErrorCode.CDSE_UNAVAILABLE,
                "CDSE STAC service could not be reached",
                "DISCOVERY",
                retryable=True,
            ) from exc
        raise AssertionError("retry loop exited without a result")

    def _next_page(
        self, links: Any, current_url: str
    ) -> tuple[str, str, dict[str, Any] | None] | None:
        if not isinstance(links, list):
            return None
        for link in links:
            if not isinstance(link, Mapping) or link.get("rel") != "next":
                continue
            href = link.get("href")
            if not isinstance(href, str):
                raise ValleyeyeError(
                    ErrorCode.CDSE_UNAVAILABLE,
                    "CDSE STAC next link is malformed",
                    "DISCOVERY",
                )
            method = str(link.get("method", "GET")).upper()
            body = link.get("body") if method == "POST" else None
            return method, urljoin(current_url, href), body if isinstance(body, dict) else None
        return None


def _scene_from_item(raw_item: Any, aoi: Any) -> SceneMetadata:
    try:
        item = Item.from_dict(raw_item)
        properties = item.properties
        acquired_at = item.datetime or _parse_datetime(properties.get("datetime"))
        if acquired_at is None:
            raise ValueError("missing datetime")
        product_type = _first(properties, "sar:product_type", "product:type", "productType")
        mode = _first(properties, "sar:instrument_mode", "instrumentMode")
        pols = _first(properties, "sar:polarizations", "polarization") or []
        if isinstance(pols, str):
            pols = [pols]
        orbit = _first(properties, "sat:orbit_state", "sar:orbit_direction", "orbitDirection")
        relative_orbit = _first(
            properties, "sat:relative_orbit", "sat:relative_orbit_number", "relativeOrbitNumber"
        )
        assets = item.assets
        asset_href = next(
            (
                asset.href
                for asset in assets.values()
                if asset.roles and "data" in asset.roles and asset.href
            ),
            next((asset.href for asset in assets.values() if asset.href), None),
        )
        return SceneMetadata(
            scene_id=item.id,
            platform=str(properties.get("platform", "unknown")).lower(),
            acquired_at=acquired_at,
            product_type=str(product_type).upper() if product_type is not None else None,
            instrument_mode=str(mode).upper() if mode is not None else None,
            polarizations=tuple(str(value).upper() for value in pols),
            orbit_direction=str(orbit).upper() if orbit is not None else None,
            relative_orbit=int(relative_orbit) if relative_orbit is not None else None,
            geometry=item.geometry or {},
            aoi_coverage_fraction=aoi_coverage_fraction(item.geometry or {}, aoi),
            cloud_cover=(
                float(properties["eo:cloud_cover"])
                if properties.get("eo:cloud_cover") is not None
                else None
            ),
            asset_href=asset_href,
        )
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        raise ValleyeyeError(
            ErrorCode.CDSE_UNAVAILABLE,
            "CDSE STAC item is missing required scene metadata",
            "DISCOVERY",
        ) from exc


def _first(properties: Mapping[str, Any], *keys: str) -> Any:
    return next((properties[key] for key in keys if properties.get(key) is not None), None)


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return None
    return parsed


def event_window(event_date: date, days: int) -> tuple[datetime, datetime]:
    start = datetime.combine(event_date - timedelta(days=days), time.min, tzinfo=UTC)
    end = datetime.combine(event_date + timedelta(days=days), time.max, tzinfo=UTC)
    return start, end
