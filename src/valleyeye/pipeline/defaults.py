from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urljoin, urlparse

import httpx
import rasterio
from pyproj import Transformer
from rasterio.features import shapes
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as transform_geometry
from shapely.ops import unary_union

from valleyeye.cdse.auth import CDSEAuth
from valleyeye.cdse.models import PairingPolicy, PairSelection, SceneMetadata
from valleyeye.cdse.pairing import select_s1_pair_adaptively
from valleyeye.cdse.stac import CDSESTACClient, event_window
from valleyeye.core.errors import ErrorCode, ValleyeyeError
from valleyeye.core.geometry import metric_crs_for_aoi, validate_aoi
from valleyeye.hazard.products import polygonize_debris_candidates, polygonize_flood
from valleyeye.infra.exposure import assess_bridge_exposure, assess_building_exposure
from valleyeye.infra.roads import RoadSegment, split_and_classify_roads
from valleyeye.ml.inference import InferenceConfig, infer_rasters
from valleyeye.ml.model import FloodModelMetadata
from valleyeye.ml.snunet import SNUNetFloodModel
from valleyeye.network.connectivity import analyze_connectivity
from valleyeye.osm.models import OSMDataset, OSMFeature
from valleyeye.osm.ohsome import OhSomeOSMProvider
from valleyeye.pipeline.models import StageOutcome
from valleyeye.pipeline.runner import StageContext, StageHandler
from valleyeye.sar.raster import align_raster, extract_snap_bands, write_slope_raster
from valleyeye.sar.snap import run_snap_preprocessing


def _relative(context: StageContext, path: Path) -> str:
    return path.relative_to(context.job_dir).as_posix()


def _write_geojson(path: Path, features: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, allow_nan=False),
        encoding="utf-8",
    )


def _read_features(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    features = value.get("features")
    if not isinstance(features, list):
        raise ValueError(f"Invalid GeoJSON feature collection: {path.name}")
    return cast(list[dict[str, Any]], features)


def _scene_records(scenes: list[SceneMetadata]) -> list[dict[str, Any]]:
    return [scene.model_dump(mode="json") for scene in scenes]


def _scene_from_record(value: dict[str, Any]) -> SceneMetadata:
    return SceneMetadata.model_validate(value)


async def _discover(context: StageContext) -> StageOutcome:
    geometry, area_km2 = validate_aoi(context.request.aoi_geojson, context.settings)
    start, end = event_window(context.request.event_date, context.settings.s1_initial_window_days)
    async with httpx.AsyncClient() as client:
        stac = CDSESTACClient(context.settings, client)
        scenes = await stac.search_sentinel1(context.request.aoi_geojson, start, end)
    await context.progress(100)
    return StageOutcome(
        data={
            "initial_window_days": context.settings.s1_initial_window_days,
            "scenes": _scene_records(scenes),
            "scene_count": len(scenes),
            "aoi_area_km2": area_km2,
            "aoi_bbox_wgs84": list(geometry.bounds),
        }
    )


async def _pair(context: StageContext) -> StageOutcome:
    policy = PairingPolicy.from_settings(context.settings)
    initial_scenes = [
        SceneMetadata.model_validate(item)
        for item in context.data.get("DISCOVERY", {}).get("scenes", [])
    ]
    initial_start, initial_end = event_window(
        context.request.event_date, context.settings.s1_initial_window_days
    )
    async with httpx.AsyncClient() as client:
        stac = CDSESTACClient(context.settings, client)

        async def search(start: datetime, end: datetime) -> list[SceneMetadata]:
            if start == initial_start and end == initial_end:
                return initial_scenes
            return await stac.search_sentinel1(context.request.aoi_geojson, start, end)

        selection = await select_s1_pair_adaptively(search, context.request.event_date, policy)
    await context.progress(100)
    return StageOutcome(data=selection.model_dump(mode="json"), warnings=list(selection.warnings))


async def _download_product(href: str, destination: Path, token: str, timeout: float) -> Path:
    current = href
    headers = {"Authorization": f"Bearer {token}"}
    temporary = destination.with_name(destination.name + ".part")
    try:
        for _ in range(6):
            parsed = urlparse(current)
            if parsed.scheme != "https" or not (
                parsed.hostname == "dataspace.copernicus.eu"
                or (parsed.hostname or "").endswith(".dataspace.copernicus.eu")
            ):
                raise ValleyeyeError(
                    ErrorCode.CDSE_UNAVAILABLE,
                    "Selected product URL is outside the trusted Copernicus Data Space hosts.",
                    "PREPROCESSING",
                )
            async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
                async with client.stream("GET", current, headers=headers) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise ValleyeyeError(
                                ErrorCode.CDSE_UNAVAILABLE,
                                "CDSE product download returned an invalid redirect.",
                                "PREPROCESSING",
                            )
                        current = urljoin(current, location)
                        continue
                    if response.is_error:
                        raise ValleyeyeError(
                            ErrorCode.CDSE_UNAVAILABLE,
                            "CDSE product download failed.",
                            "PREPROCESSING",
                            retryable=response.status_code == 429 or response.status_code >= 500,
                            details={"status_code": response.status_code},
                        )
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with temporary.open("wb") as output:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            output.write(chunk)
                    temporary.replace(destination)
                    return destination
        raise ValleyeyeError(
            ErrorCode.CDSE_UNAVAILABLE,
            "CDSE product download exceeded its redirect limit.",
            "PREPROCESSING",
        )
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


async def _preprocess(context: StageContext) -> StageOutcome:
    if context.settings.snap_gpt_path is None:
        raise ValleyeyeError(
            ErrorCode.PREPROCESSING_FAILED,
            "Configure SNAP_GPT_PATH to process Sentinel-1 GRD products.",
            "PREPROCESSING",
        )
    raw_pair = context.data["PAIRING"]
    selection = PairSelection.model_validate(raw_pair)
    if selection.pre.asset_href is None or selection.post.asset_href is None:
        raise ValleyeyeError(
            ErrorCode.CDSE_UNAVAILABLE,
            "The selected Sentinel-1 pair does not provide downloadable product assets.",
            "PREPROCESSING",
        )
    geometry, _ = validate_aoi(context.request.aoi_geojson, context.settings)
    target_crs = metric_crs_for_aoi(geometry)
    stage_dir = context.stage_dir
    async with httpx.AsyncClient(timeout=context.settings.http_timeout_seconds) as client:
        auth = CDSEAuth(context.settings, client)
        token = await auth.access_token()
        pre_zip, post_zip = stage_dir / "pre.zip", stage_dir / "post.zip"
        await _download_product(
            selection.pre.asset_href, pre_zip, token, context.settings.http_timeout_seconds
        )
        await context.progress(20)
        await _download_product(
            selection.post.asset_href, post_zip, token, context.settings.http_timeout_seconds
        )
    pre_snap, post_snap = stage_dir / "pre_snap.tif", stage_dir / "post_snap.tif"
    await asyncio.to_thread(
        run_snap_preprocessing,
        pre_zip,
        pre_snap,
        geometry,
        target_crs,
        context.settings.snap_gpt_path,
        context.settings.snap_pixel_spacing_m,
        context.settings.snap_aoi_buffer_m,
        context.cancel_event,
    )
    await context.progress(55)
    await asyncio.to_thread(
        run_snap_preprocessing,
        post_zip,
        post_snap,
        geometry,
        target_crs,
        context.settings.snap_gpt_path,
        context.settings.snap_pixel_spacing_m,
        context.settings.snap_aoi_buffer_m,
        context.cancel_event,
    )
    pre_bands, pre_dem = stage_dir / "pre_bands.tif", stage_dir / "pre_dem.tif"
    post_bands, post_dem = stage_dir / "post_bands.tif", stage_dir / "post_dem.tif"
    await asyncio.to_thread(extract_snap_bands, pre_snap, pre_bands, pre_dem)
    await asyncio.to_thread(extract_snap_bands, post_snap, post_bands, post_dem)
    await asyncio.to_thread(align_raster, post_bands, pre_bands, stage_dir / "post_aligned.tif")
    await asyncio.to_thread(align_raster, post_dem, pre_dem, stage_dir / "post_dem_aligned.tif")
    slope = await asyncio.to_thread(write_slope_raster, pre_dem, stage_dir / "slope.tif")
    await context.progress(100)
    return StageOutcome(
        data={
            "pre_product": _relative(context, pre_zip),
            "post_product": _relative(context, post_zip),
            "pre_backscatter": _relative(context, pre_bands),
            "post_backscatter": _relative(context, stage_dir / "post_aligned.tif"),
            "pre_dem": _relative(context, pre_dem),
            "slope": _relative(context, slope),
            "crs": target_crs.to_string(),
            "processing_steps": [
                "Apply-Orbit-File",
                "Subset",
                "ThermalNoiseRemoval",
                "Remove-GRD-Border-Noise",
                "Calibration sigma0 (linear power)",
                "Lee Sigma speckle filter",
                "Terrain-Correction with SRTM 1Sec HGT",
            ],
        }
    )


async def _inference(context: StageContext) -> StageOutcome:
    source = context.data["PREPROCESSING"]

    def resolve(value: object) -> Path:
        return context.job_dir / str(value)

    model = await asyncio.to_thread(
        SNUNetFloodModel.load,
        context.settings.model_weights_path,
        context.settings.inference_device,
    )
    summary = await asyncio.to_thread(
        infer_rasters,
        resolve(source["pre_backscatter"]),
        resolve(source["post_backscatter"]),
        resolve(source["slope"]),
        context.stage_dir / "probability.tif",
        context.stage_dir / "mask.tif",
        model,
        InferenceConfig(
            tile_size=context.settings.inference_tile_size,
            overlap=context.settings.inference_overlap,
            flood_probability_threshold=context.settings.flood_probability_threshold,
        ),
        context.cancel_event,
    )
    metadata: FloodModelMetadata = model.metadata
    await context.progress(100)
    return StageOutcome(
        data={
            **summary.model_dump(mode="json"),
            "model_name": metadata.name,
            "model_version": metadata.version,
            "probability_raster": _relative(context, context.stage_dir / "probability.tif"),
            "mask_raster": _relative(context, context.stage_dir / "mask.tif"),
        }
    )


async def _hazard(context: StageContext) -> StageOutcome:
    pre = context.data["PREPROCESSING"]
    inference = context.data["INFERENCE"]

    def resolve(value: object) -> Path:
        return context.job_dir / str(value)

    flood_path = context.stage_dir / "flood.geojson"
    debris_path = context.stage_dir / "debris_candidates.geojson"
    flood_count = await asyncio.to_thread(
        polygonize_flood,
        resolve(inference["probability_raster"]),
        resolve(inference["mask_raster"]),
        flood_path,
        context.settings.minimum_flood_polygon_area_km2,
    )
    debris_count = await asyncio.to_thread(
        polygonize_debris_candidates,
        resolve(pre["pre_backscatter"]),
        resolve(pre["post_backscatter"]),
        resolve(inference["mask_raster"]),
        debris_path,
        context.settings.candidate_backscatter_change_threshold_db,
        context.settings.minimum_candidate_polygon_area_km2,
    )
    await context.progress(100)
    return StageOutcome(
        data={
            "flood_polygon_count": flood_count,
            "debris_candidate_count": debris_count,
            "flood_layer": _relative(context, flood_path),
            "debris_candidate_layer": _relative(context, debris_path),
        },
        warnings=["Debris features are backscatter-change candidates, not confirmed debris."],
    )


def _osm_geojson(dataset: OSMDataset) -> list[dict[str, Any]]:
    return [
        {
            "type": "Feature",
            "id": feature.feature_id,
            "geometry": mapping(feature.geometry),
            "properties": {
                "feature_id": feature.feature_id,
                "osm_type": feature.osm_type,
                "osm_id": feature.osm_id,
                "category": feature.category,
                "tags": feature.tags,
                "snapshot_date": feature.snapshot_date.isoformat(),
            },
        }
        for feature in dataset.features
    ]


async def _osm(context: StageContext) -> StageOutcome:
    geometry, _ = validate_aoi(context.request.aoi_geojson, context.settings)
    dataset = await asyncio.to_thread(
        OhSomeOSMProvider(context.settings).fetch, geometry, context.request.event_date
    )
    layer = context.stage_dir / "osm.geojson"
    _write_geojson(layer, _osm_geojson(dataset))
    return StageOutcome(
        data={
            "provider": dataset.provider,
            "snapshot_at": dataset.snapshot_at.isoformat(),
            "query_sha256": dataset.query_sha256,
            "response_sha256": dataset.response_sha256,
            "feature_count": len(dataset.features),
            "quality": dataset.quality.__dict__,
            "layer": _relative(context, layer),
            "provenance": {
                "provider": dataset.provider,
                "snapshot_at": dataset.snapshot_at.isoformat(),
                "query_sha256": dataset.query_sha256,
                "response_sha256": dataset.response_sha256,
            },
        }
    )


def _load_osm_features(path: Path, snapshot_date: str) -> list[OSMFeature]:
    from datetime import date

    loaded: list[OSMFeature] = []
    for item in _read_features(path):
        props = item["properties"]
        geometry = shape(item["geometry"])
        loaded.append(
            OSMFeature(
                feature_id=str(props["feature_id"]),
                osm_type=str(props["osm_type"]),
                osm_id=int(props["osm_id"]),
                category=cast(Any, props["category"]),
                geometry=geometry,
                tags={str(key): str(value) for key, value in props["tags"].items()},
                snapshot_date=date.fromisoformat(snapshot_date),
            )
        )
    return loaded


def _flood_geometries(path: Path) -> tuple[BaseGeometry, ...]:
    return tuple(shape(feature["geometry"]) for feature in _read_features(path))


def _coverage_geometry(mask_path: Path) -> BaseGeometry | None:
    with rasterio.open(mask_path) as source:
        valid = source.read_masks(1) > 0
        if not valid.any():
            return None
        parts = [
            shape(item[0])
            for item in shapes(valid.astype("uint8"), mask=valid, transform=source.transform)
        ]
        if not parts:
            return None
        projected = unary_union(parts)
        transformer = Transformer.from_crs(source.crs, "EPSG:4326", always_xy=True)
        return transform_geometry(transformer.transform, projected)


async def _infrastructure(context: StageContext) -> StageOutcome:
    osm_state = context.data["OSM"]
    inference = context.data["INFERENCE"]
    hazard = context.data["HAZARD"]
    features = _load_osm_features(
        context.job_dir / osm_state["layer"], osm_state["snapshot_at"][:10]
    )
    geometry, _ = validate_aoi(context.request.aoi_geojson, context.settings)
    metric_crs = metric_crs_for_aoi(geometry)
    floods = _flood_geometries(context.job_dir / hazard["flood_layer"])
    coverage = await asyncio.to_thread(
        _coverage_geometry, context.job_dir / inference["mask_raster"]
    )
    buildings = tuple(item for item in features if item.category == "building")
    roads = tuple(item for item in features if item.category == "road")
    bridges = tuple(
        item
        for item in roads
        if item.tags.get("bridge", "no") not in {"no", "false", "0"}
        and item.tags.get("tunnel") not in {"yes", "true", "1"}
    )
    building_impacts, building_summary = await asyncio.to_thread(
        assess_building_exposure,
        buildings,
        floods,
        metric_crs,
        context.settings.building_affected_fraction_threshold,
    )
    bridge_impacts = await asyncio.to_thread(
        assess_bridge_exposure,
        bridges,
        floods,
        metric_crs,
        context.settings.bridge_hazard_buffer_m,
        context.settings.bridge_block_on_intersection,
    )
    segments, road_summary = await asyncio.to_thread(
        split_and_classify_roads,
        roads,
        floods,
        coverage,
        metric_crs,
        context.settings.road_potentially_blocked_fraction,
        context.settings.road_blocked_fraction,
    )
    road_layer = context.stage_dir / "roads.geojson"
    _write_geojson(
        road_layer,
        [
            {
                "type": "Feature",
                "geometry": mapping(segment.geometry),
                "properties": {
                    "segment_id": segment.segment_id,
                    "parent_road_id": segment.parent_road_id,
                    "highway": segment.highway,
                    "status": segment.status,
                    "length_m": segment.length_m,
                    "flood_overlap_fraction": segment.flood_overlap_fraction,
                    "oneway": segment.oneway,
                    "maxspeed": segment.maxspeed,
                    "bridge": segment.bridge,
                },
            }
            for segment in segments
        ],
    )
    building_layer = context.stage_dir / "buildings.geojson"
    building_by_id = {item.feature_id: item for item in building_impacts}
    _write_geojson(
        building_layer,
        [
            {
                "type": "Feature",
                "geometry": mapping(feature.geometry),
                "properties": {
                    "feature_id": feature.feature_id,
                    "affected": building_by_id[feature.feature_id].affected,
                    "overlap_fraction": building_by_id[feature.feature_id].overlap_fraction,
                    "claim_level": "potential exposure, not confirmed damage",
                },
            }
            for feature in buildings
        ],
    )
    bridge_by_id = {item.feature_id: item for item in bridge_impacts}
    bridge_layer = context.stage_dir / "bridges.geojson"
    _write_geojson(
        bridge_layer,
        [
            {
                "type": "Feature",
                "geometry": mapping(feature.geometry),
                "properties": {
                    "feature_id": feature.feature_id,
                    "classification": bridge_by_id[feature.feature_id].classification,
                    "overlap_fraction": bridge_by_id[feature.feature_id].overlap_fraction,
                    "claim_level": "potential exposure, not confirmed damage",
                },
            }
            for feature in bridges
        ],
    )
    await context.progress(100)
    return StageOutcome(
        data={
            "building_summary": asdict(building_summary),
            "bridge_affected_count": sum(
                item.classification == "AFFECTED" for item in bridge_impacts
            ),
            "bridge_count": len(bridge_impacts),
            "road_summary": asdict(road_summary),
            "segment_count": len(segments),
            "road_layer": _relative(context, road_layer),
            "building_layer": _relative(context, building_layer),
            "bridge_layer": _relative(context, bridge_layer),
            "coverage_available": coverage is not None,
        }
    )


async def _connectivity(context: StageContext) -> StageOutcome:
    osm_state = context.data["OSM"]
    infra = context.data["INFRASTRUCTURE"]
    features = _load_osm_features(
        context.job_dir / osm_state["layer"], osm_state["snapshot_at"][:10]
    )
    roads = tuple(
        RoadSegment(
            segment_id=str(feature["properties"]["segment_id"]),
            parent_road_id=str(feature["properties"]["parent_road_id"]),
            geometry=shape(feature["geometry"]),
            highway=str(feature["properties"]["highway"]),
            status=cast(Any, feature["properties"]["status"]),
            length_m=float(feature["properties"]["length_m"]),
            flood_overlap_fraction=float(feature["properties"]["flood_overlap_fraction"]),
            oneway=feature["properties"]["oneway"],
            maxspeed=feature["properties"]["maxspeed"],
            bridge=bool(feature["properties"]["bridge"]),
        )
        for feature in _read_features(context.job_dir / infra["road_layer"])
    )
    settlements = tuple(item for item in features if item.category == "settlement")
    source_locations = tuple(
        item
        for item in features
        if item.category == "healthcare"
        or (item.category == "settlement" and item.tags.get("place") in {"town", "city"})
    )
    geometry, _ = validate_aoi(context.request.aoi_geojson, context.settings)
    result, summary = await asyncio.to_thread(
        analyze_connectivity,
        roads,
        settlements,
        source_locations,
        metric_crs_for_aoi(geometry),
        context.settings.road_speed_defaults_kmh,
        context.settings.network_max_snap_distance_m,
        context.settings.network_detour_ratio_threshold,
        context.settings.remove_potentially_blocked_edges,
    )
    layer = context.stage_dir / "settlements.geojson"
    _write_geojson(
        layer,
        [
            {
                "type": "Feature",
                "geometry": mapping(
                    next(
                        feature.geometry
                        for feature in settlements
                        if feature.feature_id == item.settlement_id
                    )
                ),
                "properties": item.__dict__,
            }
            for item in result
        ],
    )
    await context.progress(100)
    return StageOutcome(
        data={
            "summary": summary.__dict__,
            "settlement_count": len(result),
            "results": [item.__dict__ for item in result],
            "layer": _relative(context, layer),
        }
    )


async def _results(context: StageContext) -> StageOutcome:
    await context.progress(100)
    return StageOutcome(data={"generated": True})


def default_stage_handlers() -> dict[str, StageHandler]:
    return {
        "DISCOVERY": _discover,
        "PAIRING": _pair,
        "PREPROCESSING": _preprocess,
        "INFERENCE": _inference,
        "HAZARD": _hazard,
        "OSM": _osm,
        "INFRASTRUCTURE": _infrastructure,
        "CONNECTIVITY": _connectivity,
        "RESULTS": _results,
    }
