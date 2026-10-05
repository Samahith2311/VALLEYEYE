from __future__ import annotations

import argparse
import json
import platform
import sys
import time
import tracemalloc
from collections.abc import Callable
from datetime import date
from typing import TypeVar

import networkx as nx
import shapely
from pyproj import CRS, Transformer
from shapely.geometry import LineString, box

from valleyeye.infra.exposure import assess_building_exposure
from valleyeye.infra.roads import split_and_classify_roads
from valleyeye.network.connectivity import build_road_graph, derive_after_graph
from valleyeye.osm.models import OSMFeature

Result = TypeVar("Result")


def _features(
    roads_count: int, buildings_count: int
) -> tuple[tuple[OSMFeature, ...], tuple[OSMFeature, ...]]:
    road_geometries = []
    for index in range(roads_count):
        row, col = divmod(index, 50)
        x = col * 40.0
        y = row * 20.0
        road_geometries.append(LineString([(x, y), (x + 38.0, y)]))

    building_geometries = []
    columns = 200
    for index in range(buildings_count):
        row, col = divmod(index, columns)
        x = col * 10.0
        y = row * 10.0
        building_geometries.append(box(x, y, x + 8.0, y + 8.0))

    to_wgs84 = Transformer.from_crs("EPSG:32631", "EPSG:4326", always_xy=True).transform
    projected = [*road_geometries, *building_geometries]
    geographic = shapely.transform(projected, to_wgs84, interleaved=False)
    roads = tuple(
        OSMFeature(
            feature_id=f"way/{index + 1}",
            osm_type="way",
            osm_id=index + 1,
            category="road",
            geometry=geometry,
            tags={"highway": "residential"},
            snapshot_date=date(2020, 1, 1),
        )
        for index, geometry in enumerate(geographic[:roads_count])
    )
    buildings = tuple(
        OSMFeature(
            feature_id=f"building/{index + 1}",
            osm_type="way",
            osm_id=index + 1,
            category="building",
            geometry=geometry,
            tags={"building": "yes"},
            snapshot_date=date(2020, 1, 1),
        )
        for index, geometry in enumerate(geographic[roads_count:])
    )
    return roads, buildings


def _measure(callable_: Callable[[], Result]) -> tuple[float, int, Result]:
    tracemalloc.start()
    started = time.perf_counter()
    result = callable_()
    elapsed = time.perf_counter() - started
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return elapsed, peak_bytes, result


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile GIS stages on synthetic large-AOI inputs")
    parser.add_argument("--roads", type=int, default=5000)
    parser.add_argument("--buildings", type=int, default=25000)
    args = parser.parse_args()
    if args.roads <= 0 or args.buildings <= 0:
        parser.error("--roads and --buildings must be positive")

    roads, buildings = _features(args.roads, args.buildings)
    metric_crs = CRS.from_epsg(32631)
    to_wgs84 = Transformer.from_crs(metric_crs, "EPSG:4326", always_xy=True).transform
    flood_metric = tuple(box(x, 0, x + 35, 10000) for x in range(150, 1450, 150))
    flood = tuple(shapely.transform(flood_metric, to_wgs84, interleaved=False))
    coverage = shapely.transform(box(-100, -100, 5100, 5100), to_wgs84, interleaved=False)

    exposure_seconds, exposure_peak, exposure = _measure(
        lambda: assess_building_exposure(buildings, flood, metric_crs)
    )
    roads_seconds, roads_peak, road_result = _measure(
        lambda: split_and_classify_roads(roads, flood, coverage, metric_crs)
    )
    segments, summary = road_result
    rebuilt_pair_seconds, rebuilt_pair_peak, rebuilt_pair = _measure(
        lambda: (
            build_road_graph(segments, {"residential": 30.0}),
            build_road_graph(segments, {"residential": 30.0}, after_event=True),
        )
    )
    reused_pair_seconds, reused_pair_peak, reused_pair = _measure(
        lambda: (
            (before := build_road_graph(segments, {"residential": 30.0})),
            derive_after_graph(before),
        )
    )
    exposure_impacts, exposure_summary = exposure

    rebuilt_before, rebuilt_after = rebuilt_pair
    reused_before, reused_after = reused_pair
    if not nx.utils.graphs_equal(rebuilt_after, reused_after):
        raise RuntimeError("Graph optimization changed outputs")

    result = {
        "input": {
            "synthetic_roads": len(roads),
            "synthetic_buildings": len(buildings),
            "flood_polygons": len(flood),
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "processor": platform.processor() or "not reported",
            "metric_crs": metric_crs.to_string(),
        },
        "stages": {
            "building_exposure": {
                "seconds": exposure_seconds,
                "python_peak_bytes": exposure_peak,
                "features_processed": len(exposure_impacts),
                "affected": exposure_summary.affected_count,
            },
            "road_split_classify": {
                "seconds": roads_seconds,
                "python_peak_bytes": roads_peak,
                "segments": len(segments),
                "summary": summary.__dict__,
            },
            "graph_pair_rebuild_baseline": {
                "seconds": rebuilt_pair_seconds,
                "python_peak_bytes": rebuilt_pair_peak,
                "baseline_nodes": rebuilt_before.number_of_nodes(),
                "post_event_nodes": rebuilt_after.number_of_nodes(),
                "baseline_edges": rebuilt_before.number_of_edges(),
                "post_event_edges": rebuilt_after.number_of_edges(),
            },
            "graph_pair_reused": {
                "seconds": reused_pair_seconds,
                "python_peak_bytes": reused_pair_peak,
                "baseline_nodes": reused_before.number_of_nodes(),
                "post_event_nodes": reused_after.number_of_nodes(),
                "baseline_edges": reused_before.number_of_edges(),
                "post_event_edges": reused_after.number_of_edges(),
            },
        },
        "memory_note": "tracemalloc reports Python allocations; native NumPy/GEOS allocations "
        "may not be included.",
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
