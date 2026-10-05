from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

import networkx as nx
import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from valleyeye.infra.roads import RoadSegment
from valleyeye.osm.models import OSMFeature

ConnectivityStatus = Literal["CONNECTED", "DETOUR", "CUT_OFF", "NO_BASELINE_ACCESS"]


@dataclass(frozen=True)
class SettlementConnectivity:
    settlement_id: str
    name: str | None
    status: ConnectivityStatus
    baseline_access: Literal["OK", "NO_BASELINE_ACCESS"]
    before_distance_m: float | None
    after_distance_m: float | None
    before_time_s: float | None
    after_time_s: float | None
    detour_ratio: float | None
    nearest_source_before: str | None
    nearest_source_after: str | None
    blocking_road_ids: tuple[str, ...]
    reason: str | None = None


@dataclass(frozen=True)
class ConnectivitySummary:
    connected: int
    detour: int
    cut_off: int
    no_baseline_access: int


def _parse_speed_kmh(value: str | None, default: float) -> float:
    if not value:
        return default
    token = value.split(";")[0].strip().casefold()
    number = "".join(character for character in token if character.isdigit() or character == ".")
    try:
        speed = float(number)
    except ValueError:
        return default
    if speed <= 0:
        return default
    return speed * 1.609344 if "mph" in token else speed


def _node_key(coordinate: tuple[float, ...]) -> tuple[float, float]:
    return round(float(coordinate[0]), 2), round(float(coordinate[1]), 2)


def build_road_graph(
    segments: tuple[RoadSegment, ...],
    speed_defaults_kmh: dict[str, float],
    after_event: bool = False,
    remove_potentially_blocked: bool = False,
) -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    for segment in segments:
        default_speed = speed_defaults_kmh.get(segment.highway)
        if default_speed is None:
            continue
        if after_event and (
            segment.status in {"BLOCKED", "UNKNOWN_COVERAGE"}
            or (remove_potentially_blocked and segment.status == "POTENTIALLY_BLOCKED")
        ):
            continue
        coordinates = list(segment.geometry.coords)
        speed = _parse_speed_kmh(segment.maxspeed, default_speed)
        oneway = (segment.oneway or "").casefold()
        reverse_only = oneway == "-1"
        one_direction = oneway in {"yes", "true", "1", "-1"}
        for start, end in zip(coordinates, coordinates[1:], strict=False):
            source = _node_key(start)
            target = _node_key(end)
            length_m = float(Point(start).distance(Point(end)))
            if length_m <= 0:
                continue
            attributes = {
                "length_m": length_m,
                "time_s": length_m / (speed * 1000 / 3600),
                "segment_id": segment.segment_id,
                "parent_road_id": segment.parent_road_id,
                "status": segment.status,
            }
            if reverse_only:
                graph.add_edge(target, source, key=f"{segment.segment_id}:reverse", **attributes)
            else:
                graph.add_edge(source, target, key=f"{segment.segment_id}:forward", **attributes)
            if not one_direction:
                graph.add_edge(target, source, key=f"{segment.segment_id}:reverse", **attributes)
    return graph


def derive_after_graph(
    before_graph: nx.MultiDiGraph,
    remove_potentially_blocked: bool = False,
) -> nx.MultiDiGraph:
    """Derive the post-event network by filtering the already-built baseline graph."""
    after_graph = before_graph.copy()
    removed_edges = [
        (start, end, key)
        for start, end, key, attributes in after_graph.edges(keys=True, data=True)
        if attributes.get("status") in {"BLOCKED", "UNKNOWN_COVERAGE"}
        or (remove_potentially_blocked and attributes.get("status") == "POTENTIALLY_BLOCKED")
    ]
    after_graph.remove_edges_from(removed_edges)
    after_graph.remove_nodes_from(list(nx.isolates(after_graph)))
    return after_graph


def _project_points(features: tuple[OSMFeature, ...], metric_crs: CRS) -> tuple[Point, ...]:
    transformer = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
    geometries = shapely.transform(
        np.asarray(tuple(feature.geometry for feature in features), dtype=object),
        transformer.transform,
        interleaved=False,
    )
    return tuple(geometries)


def _snap_points(
    graph: nx.MultiDiGraph,
    points: tuple[Point, ...],
    maximum_distance_m: float,
    companion_graph: nx.MultiDiGraph,
) -> tuple[tuple[tuple[float, float] | None, ...], tuple[float | None, ...]]:
    nodes = tuple(graph.nodes)
    if not nodes:
        return tuple(None for _ in points), tuple(None for _ in points)
    node_geometries = np.asarray([Point(node) for node in nodes], dtype=object)
    tree = STRtree(node_geometries)
    edge_groups: dict[
        tuple[str, tuple[tuple[float, float], tuple[float, float]]],
        list[tuple[tuple[float, float], tuple[float, float], str, dict[str, object]]],
    ] = {}
    for start, end, key, attributes in graph.edges(keys=True, data=True):
        parent = str(attributes["segment_id"])
        endpoints = tuple(sorted((start, end)))
        edge_groups.setdefault((parent, endpoints), []).append((start, end, key, attributes))
    group_keys = tuple(sorted(edge_groups))
    edge_geometries = np.asarray(
        [LineString(endpoints) for _, endpoints in group_keys], dtype=object
    )
    edge_tree = STRtree(edge_geometries) if len(edge_geometries) else None
    snapped: list[tuple[float, float] | None] = []
    distances: list[float | None] = []
    virtual_nodes: dict[
        tuple[str, tuple[tuple[float, float], tuple[float, float]]],
        list[tuple[tuple[float, float], float]],
    ] = {}
    for point in points:
        nearest, distance = tree.query_nearest(
            point,
            all_matches=False,
            return_distance=True,
        )
        node_index = int(np.asarray(nearest).reshape(-1)[0])
        node_distance_m = float(np.asarray(distance).reshape(-1)[0])
        edge_index = -1
        edge_distance_m = float("inf")
        if edge_tree is not None:
            nearest_edge, edge_distance = edge_tree.query_nearest(
                point,
                all_matches=False,
                return_distance=True,
            )
            edge_index = int(np.asarray(nearest_edge).reshape(-1)[0])
            edge_distance_m = float(np.asarray(edge_distance).reshape(-1)[0])
        if min(node_distance_m, edge_distance_m) > maximum_distance_m:
            snapped.append(None)
            distances.append(min(node_distance_m, edge_distance_m))
        elif node_distance_m <= edge_distance_m:
            snapped.append(nodes[node_index])
            distances.append(node_distance_m)
        else:
            group_key = group_keys[edge_index]
            endpoints = group_key[1]
            line = LineString(endpoints)
            fraction = line.project(point, normalized=True)
            projected = line.interpolate(fraction, normalized=True)
            node = _node_key(projected.coords[0])
            snapped.append(node)
            distances.append(edge_distance_m)
            virtual_nodes.setdefault(group_key, []).append((node, fraction))
    for target_graph in (graph, companion_graph):
        for group_key, attachments in virtual_nodes.items():
            _insert_edge_snaps(target_graph, group_key, attachments)
        target_graph.add_nodes_from(node for node in snapped if node is not None)
    return tuple(snapped), tuple(distances)


def _insert_edge_snaps(
    graph: nx.MultiDiGraph,
    group_key: tuple[str, tuple[tuple[float, float], tuple[float, float]]],
    attachments: list[tuple[tuple[float, float], float]],
) -> None:
    segment_id, endpoints = group_key
    if len(attachments) == 0:
        return
    start, end = endpoints
    unique = sorted(
        {(node, round(fraction, 12)) for node, fraction in attachments},
        key=lambda item: item[1],
    )
    edges = [
        (u, v, key, attributes)
        for u, v, key, attributes in graph.edges(keys=True, data=True)
        if attributes.get("segment_id") == segment_id and tuple(sorted((u, v))) == endpoints
    ]
    for source, target, key, attributes in edges:
        source_fraction = 0.0 if source == start else 1.0
        target_fraction = 1.0 if target == end else 0.0
        low, high = sorted((source_fraction, target_fraction))
        interior = [(node, fraction) for node, fraction in unique if low < fraction < high]
        interior.sort(key=lambda item: item[1], reverse=source_fraction > target_fraction)
        chain = [source, *(node for node, _ in interior), target]
        fractions = [source_fraction, *(fraction for _, fraction in interior), target_fraction]
        graph.remove_edge(source, target, key)
        old_data = dict(attributes)
        old_length = float(old_data["length_m"])
        old_time = float(old_data["time_s"])
        for index, (left, right) in enumerate(zip(chain, chain[1:], strict=False)):
            span = abs(fractions[index + 1] - fractions[index])
            data = dict(old_data)
            data["length_m"] = old_length * span
            data["time_s"] = old_time * span
            graph.add_edge(left, right, key=f"{key}:snap:{index}", **data)


def _edge_data(
    graph: nx.MultiDiGraph,
    start: tuple[float, float],
    end: tuple[float, float],
) -> dict[str, object]:
    choices = cast(dict[str, dict[str, object]] | None, graph.get_edge_data(start, end))
    if not choices:
        return {}
    return min(
        choices.values(),
        key=lambda attributes: float(cast(float, attributes["time_s"])),
    )


def _route_length_and_blockers(
    graph: nx.MultiDiGraph,
    route_from_source: list[tuple[float, float]],
) -> tuple[float, tuple[str, ...]]:
    total_length = 0.0
    blockers: set[str] = set()
    for start, end in zip(
        reversed(route_from_source[1:]),
        reversed(route_from_source[:-1]),
        strict=False,
    ):
        attributes = _edge_data(graph, start, end)
        total_length += float(cast(float, attributes.get("length_m", 0.0)))
        if attributes.get("status") in {"BLOCKED", "POTENTIALLY_BLOCKED", "UNKNOWN_COVERAGE"}:
            blockers.add(str(attributes.get("parent_road_id", "")))
    return total_length, tuple(sorted(blockers - {""}))


def _routes_to_sources(
    graph: nx.MultiDiGraph,
    sources_by_node: dict[tuple[float, float], str],
    weight: str,
) -> tuple[dict[tuple[float, float], float], dict[tuple[float, float], list[tuple[float, float]]]]:
    valid_sources = sorted(node for node in sources_by_node if node in graph)
    if not valid_sources:
        return {}, {}
    return cast(
        tuple[
            dict[tuple[float, float], float],
            dict[tuple[float, float], list[tuple[float, float]]],
        ],
        nx.multi_source_dijkstra(graph.reverse(copy=False), valid_sources, weight=weight),
    )


def analyze_connectivity(
    segments: tuple[RoadSegment, ...],
    settlements: tuple[OSMFeature, ...],
    sources: tuple[OSMFeature, ...],
    metric_crs: CRS,
    speed_defaults_kmh: dict[str, float],
    maximum_snap_distance_m: float = 500.0,
    detour_ratio_threshold: float = 1.25,
    remove_potentially_blocked: bool = False,
) -> tuple[tuple[SettlementConnectivity, ...], ConnectivitySummary]:
    if maximum_snap_distance_m <= 0 or detour_ratio_threshold < 1:
        raise ValueError("Connectivity snap distance and detour threshold are invalid")
    before_graph = build_road_graph(segments, speed_defaults_kmh)
    after_graph = derive_after_graph(before_graph, remove_potentially_blocked)
    locations = (*settlements, *sources)
    projected_locations = _project_points(locations, metric_crs) if locations else ()
    snapped_locations, location_snap_distances = _snap_points(
        before_graph,
        projected_locations,
        maximum_snap_distance_m,
        after_graph,
    )
    snapped_settlements = snapped_locations[: len(settlements)]
    snapped_sources = snapped_locations[len(settlements) :]
    settlement_snap_distances = location_snap_distances[: len(settlements)]
    sources_by_node: dict[tuple[float, float], str] = {}
    for feature, node in zip(sources, snapped_sources, strict=True):
        if node is not None:
            sources_by_node.setdefault(node, feature.feature_id)
    before_times, before_time_paths = _routes_to_sources(before_graph, sources_by_node, "time_s")
    after_sources = {
        node: source_id for node, source_id in sources_by_node.items() if node in after_graph
    }
    after_times, after_time_paths = _routes_to_sources(after_graph, after_sources, "time_s")

    results: list[SettlementConnectivity] = []
    for index, feature in enumerate(settlements):
        node = snapped_settlements[index]
        name = feature.tags.get("name")
        if node is None:
            results.append(
                SettlementConnectivity(
                    settlement_id=feature.feature_id,
                    name=name,
                    status="NO_BASELINE_ACCESS",
                    baseline_access="NO_BASELINE_ACCESS",
                    before_distance_m=None,
                    after_distance_m=None,
                    before_time_s=None,
                    after_time_s=None,
                    detour_ratio=None,
                    nearest_source_before=None,
                    nearest_source_after=None,
                    blocking_road_ids=(),
                    reason=f"No road node within {maximum_snap_distance_m:g} m snap distance.",
                )
            )
            continue
        if node not in before_times:
            reason = "No pre-event route to any configured town or healthcare source."
            if not sources_by_node:
                reason = (
                    "No configured town or healthcare source could be snapped to the road graph."
                )
            results.append(
                SettlementConnectivity(
                    settlement_id=feature.feature_id,
                    name=name,
                    status="NO_BASELINE_ACCESS",
                    baseline_access="NO_BASELINE_ACCESS",
                    before_distance_m=None,
                    after_distance_m=None,
                    before_time_s=None,
                    after_time_s=None,
                    detour_ratio=None,
                    nearest_source_before=None,
                    nearest_source_after=None,
                    blocking_road_ids=(),
                    reason=reason,
                )
            )
            continue

        before_time_path = before_time_paths[node]
        before_source = before_time_path[0]
        before_distance, blockers = _route_length_and_blockers(before_graph, before_time_path)
        after_time_path = after_time_paths.get(node)
        snap_distance = settlement_snap_distances[index]
        if after_time_path is None:
            status: ConnectivityStatus = "CUT_OFF"
            after_distance = after_time = detour_ratio = None
            after_source_id = None
        else:
            status = "CONNECTED"
            after_source = after_time_path[0]
            after_source_id = after_sources[after_source]
            after_distance, _ = _route_length_and_blockers(after_graph, after_time_path)
            after_time = float(after_times[node])
            detour_ratio = after_distance / before_distance if before_distance > 0 else 1.0
            time_ratio = after_time / float(before_times[node]) if before_times[node] > 0 else 1.0
            if detour_ratio > detour_ratio_threshold or time_ratio > detour_ratio_threshold:
                status = "DETOUR"
        results.append(
            SettlementConnectivity(
                settlement_id=feature.feature_id,
                name=name,
                status=status,
                baseline_access="OK",
                before_distance_m=before_distance,
                after_distance_m=after_distance,
                before_time_s=float(before_times[node]),
                after_time_s=after_time,
                detour_ratio=detour_ratio,
                nearest_source_before=sources_by_node.get(before_source),
                nearest_source_after=after_source_id,
                blocking_road_ids=blockers,
                reason=(
                    "Nearest road node exceeds the configured snap distance."
                    if snap_distance is not None and snap_distance > maximum_snap_distance_m
                    else None
                ),
            )
        )
    summary = ConnectivitySummary(
        connected=sum(result.status == "CONNECTED" for result in results),
        detour=sum(result.status == "DETOUR" for result in results),
        cut_off=sum(result.status == "CUT_OFF" for result in results),
        no_baseline_access=sum(
            result.baseline_access == "NO_BASELINE_ACCESS" for result in results
        ),
    )
    return tuple(results), summary
