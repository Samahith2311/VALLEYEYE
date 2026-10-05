from __future__ import annotations

from datetime import date
from io import BytesIO

import httpx
import pyarrow as pa
import pyarrow.parquet as parquet
import pytest
import shapely
from pyproj import CRS, Transformer
from shapely.geometry import LineString, Point, box
from shapely.geometry.base import BaseGeometry

from valleyeye.core.errors import ErrorCode, ValleyeyeError
from valleyeye.core.settings import Settings
from valleyeye.infra.exposure import assess_bridge_exposure, assess_building_exposure
from valleyeye.infra.roads import RoadSegment, split_and_classify_roads
from valleyeye.network.connectivity import analyze_connectivity, build_road_graph
from valleyeye.osm.models import OSMFeature
from valleyeye.osm.ohsome import (
    OhSomeOSMProvider,
    assert_pre_event_snapshot,
    pre_event_snapshot,
)


def _feature(
    feature_id: str,
    category: str,
    geometry: BaseGeometry,
    tags: dict[str, str],
) -> OSMFeature:
    return OSMFeature(
        feature_id=feature_id,
        osm_type="way" if category in {"building", "road"} else "node",
        osm_id=int(feature_id.split("/")[-1]),
        category=category,  # type: ignore[arg-type]
        geometry=geometry,
        tags=tags,
        snapshot_date=date(2024, 1, 1),
    )


def _wgs84(geometry: BaseGeometry) -> BaseGeometry:
    transformer = Transformer.from_crs("EPSG:32631", "EPSG:4326", always_xy=True)
    if isinstance(geometry, Point):
        longitude, latitude = transformer.transform(*geometry.coords[0])
        return Point(longitude, latitude)
    return shapely.transform(geometry, transformer.transform, interleaved=False)


def _parquet_response() -> bytes:
    geometries = [
        box(0.2, 0.2, 0.3, 0.3),
        LineString([(0.1, 0.1), (0.9, 0.9)]),
        Point(0.4, 0.4),
        box(0.5, 0.5, 0.6, 0.6),
        Point(0.7, 0.7),
    ]
    tags = [
        {"building": "yes"},
        {"highway": "residential"},
        {"place": "village", "name": "Example"},
        {"amenity": "hospital"},
        {"natural": "tree"},
    ]
    table = pa.table(
        {
            "osm_type": ["way", "way", "node", "way", "node"],
            "osm_id": [1, 2, 3, 4, 5],
            "tags": pa.array(
                [list(value.items()) for value in tags],
                type=pa.map_(pa.string(), pa.string()),
            ),
            "geom": [shapely.to_wkb(geometry) for geometry in geometries],
        }
    )
    output = BytesIO()
    parquet.write_table(table, output)
    return output.getvalue()


def test_ohsome_provider_requests_only_a_strictly_pre_event_snapshot() -> None:
    body = _parquet_response()
    seen: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers["authorization"]
        seen["body"] = request.read()
        return httpx.Response(200, content=body)

    client = httpx.Client(transport=httpx.MockTransport(handle))
    settings = Settings(
        _env_file=None,
        OHSOME_API_KEY="test-secret",
        osm_aoi_buffer_m=0,
    )
    dataset = OhSomeOSMProvider(settings, client).fetch(box(0, 0, 1, 1), date(2024, 1, 2))

    assert dataset.snapshot_at == pre_event_snapshot(date(2024, 1, 2))
    assert dataset.snapshot_at.isoformat() == "2024-01-01T23:59:59+00:00"
    assert seen["authorization"] == "test-secret"
    assert dataset.quality.extracted_rows == 5
    assert dataset.quality.retained_features == 4
    assert len(dataset.buildings) == len(dataset.roads) == 1
    assert len(dataset.settlements) == len(dataset.healthcare) == 1
    assert dataset.response_sha256 and dataset.query_sha256
    assert dataset.query["time"] == "2024-01-01T23:59:59Z"


def test_osm_provider_requires_key_and_never_allows_same_day_snapshot() -> None:
    with pytest.raises(ValleyeyeError) as missing_key:
        OhSomeOSMProvider(Settings(_env_file=None)).fetch(box(0, 0, 1, 1), date(2024, 1, 2))
    assert missing_key.value.code == ErrorCode.OSM_UNAVAILABLE

    with pytest.raises(ValleyeyeError) as not_pre_event:
        assert_pre_event_snapshot(pre_event_snapshot(date(2024, 1, 2)), date(2024, 1, 1))
    assert not_pre_event.value.code == ErrorCode.OSM_SNAPSHOT_NOT_PRE_EVENT


def test_public_settings_never_include_ohsome_api_key() -> None:
    settings = Settings(_env_file=None, ohsome_api_key="do-not-expose")
    public = str(settings.public_config())
    assert "do-not-expose" not in public
    assert "ohsome_api_key" not in public


def test_building_and_bridge_exposure_use_metric_overlap() -> None:
    crs = CRS.from_epsg(32631)
    buildings = (
        _feature("way/1", "building", _wgs84(box(0, 0, 10, 10)), {"building": "yes"}),
        _feature("way/2", "building", _wgs84(box(20, 0, 30, 10)), {"building": "yes"}),
    )
    flood = (_wgs84(box(0, 0, 5, 10)),)

    impacts, summary = assess_building_exposure(buildings, flood, crs, 0.4)
    bridge = _feature(
        "way/3",
        "road",
        _wgs84(LineString([(0, 0), (100, 0)])),
        {"highway": "primary", "bridge": "yes"},
    )
    bridge_impact = assess_bridge_exposure(
        (bridge,), (_wgs84(box(45, -2, 55, 2)),), crs, hazard_buffer_m=10
    )[0]

    assert impacts[0].overlap_fraction == pytest.approx(0.5, abs=1e-5)
    assert impacts[0].affected and not impacts[1].affected
    assert summary.total_count == 2 and summary.affected_count == 1
    assert bridge_impact.intersects_hazard
    assert bridge_impact.classification == "AFFECTED"


def test_roads_split_at_flood_boundaries_and_keep_unknown_coverage_explicit() -> None:
    crs = CRS.from_epsg(32631)
    road = _feature(
        "way/10",
        "road",
        _wgs84(LineString([(0, 0), (100, 0)])),
        {"highway": "residential"},
    )
    segments, summary = split_and_classify_roads(
        (road,),
        (_wgs84(box(40, -10, 60, 10)),),
        _wgs84(box(-1, -20, 101, 20)),
        crs,
    )
    assert [segment.status for segment in segments] == ["OPEN", "BLOCKED", "OPEN"]
    assert [segment.length_m for segment in segments] == pytest.approx([40, 20, 40], abs=0.02)
    assert summary.blocked_km == pytest.approx(0.02, abs=1e-5)

    unknown_segments, _ = split_and_classify_roads((road,), (), None, crs)
    assert unknown_segments[0].status == "UNKNOWN_COVERAGE"


def _segment(
    segment_id: str,
    line: LineString,
    status: str = "OPEN",
    oneway: str | None = None,
) -> RoadSegment:
    return RoadSegment(
        segment_id=segment_id,
        parent_road_id=segment_id,
        geometry=line,
        highway="residential",
        status=status,  # type: ignore[arg-type]
        length_m=line.length,
        flood_overlap_fraction=1.0 if status == "BLOCKED" else 0.0,
        oneway=oneway,
        maxspeed=None,
        bridge=False,
    )


def _network_feature(feature_id: str, metric_point: Point, name: str) -> OSMFeature:
    return _feature(
        feature_id,
        "settlement",
        _wgs84(metric_point),
        {"place": "village", "name": name},
    )


def test_connectivity_distinguishes_detour_cutoff_and_baseline_access() -> None:
    crs = CRS.from_epsg(32631)
    settlement = _network_feature("node/1", Point(0, 0), "Village")
    town = _network_feature("node/2", Point(100, 0), "Town")
    direct = _segment("way/direct", LineString([(0, 0), (100, 0)]), "BLOCKED")
    bypass = (
        _segment("way/a", LineString([(0, 0), (0, 50)])),
        _segment("way/b", LineString([(0, 50), (100, 50)])),
        _segment("way/c", LineString([(100, 50), (100, 0)])),
    )
    detour, detour_summary = analyze_connectivity(
        (direct, *bypass),
        (settlement,),
        (town,),
        crs,
        {"residential": 30},
        detour_ratio_threshold=1.25,
    )
    cutoff, cutoff_summary = analyze_connectivity(
        (direct,),
        (settlement,),
        (town,),
        crs,
        {"residential": 30},
    )
    unreachable = _network_feature("node/3", Point(1000, 1000), "Remote")
    no_access, no_access_summary = analyze_connectivity(
        (direct,),
        (unreachable,),
        (town,),
        crs,
        {"residential": 30},
        maximum_snap_distance_m=10,
    )

    assert detour[0].status == "DETOUR"
    assert detour[0].before_distance_m == pytest.approx(100)
    assert detour[0].after_distance_m == pytest.approx(200)
    assert detour[0].blocking_road_ids == ("way/direct",)
    assert detour_summary.detour == 1
    assert cutoff[0].status == "CUT_OFF" and cutoff_summary.cut_off == 1
    assert no_access[0].baseline_access == "NO_BASELINE_ACCESS"
    assert no_access_summary.no_baseline_access == 1


def test_graph_respects_oneway_and_optimistic_potential_policy() -> None:
    road = _segment("way/oneway", LineString([(0, 0), (10, 0)]), oneway="yes")
    graph = build_road_graph((road,), {"residential": 30})
    assert graph.has_edge((0.0, 0.0), (10.0, 0.0))
    assert not graph.has_edge((10.0, 0.0), (0.0, 0.0))

    potential = _segment("way/potential", LineString([(10, 0), (20, 0)]), "POTENTIALLY_BLOCKED")
    optimistic = build_road_graph(
        (potential,), {"residential": 30}, after_event=True, remove_potentially_blocked=False
    )
    strict = build_road_graph(
        (potential,), {"residential": 30}, after_event=True, remove_potentially_blocked=True
    )
    assert optimistic.number_of_edges() == 2
    assert strict.number_of_edges() == 0


def test_settlement_snaps_to_nearest_road_edge_not_only_an_endpoint() -> None:
    settlement = _network_feature("node/20", Point(50, 5), "Edge-side village")
    town = _network_feature("node/21", Point(100, 0), "Town")
    road = _segment("way/edge", LineString([(0, 0), (100, 0)]))

    results, _ = analyze_connectivity(
        (road,),
        (settlement,),
        (town,),
        CRS.from_epsg(32631),
        {"residential": 30},
        maximum_snap_distance_m=10,
    )

    assert results[0].status == "CONNECTED"
    assert results[0].before_distance_m == pytest.approx(50, abs=0.02)
