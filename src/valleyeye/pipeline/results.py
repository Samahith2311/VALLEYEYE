from __future__ import annotations

import html
import json
import platform
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from valleyeye import __version__
from valleyeye.api.schemas import AnalysisRequest
from valleyeye.pipeline.models import AnalysisDocument, ArtifactDescriptor, JobRecord


def _git_revision() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _package_versions() -> dict[str, str]:
    packages = (
        "valleyeye",
        "fastapi",
        "httpx",
        "networkx",
        "numpy",
        "pyproj",
        "rasterio",
        "shapely",
        "torch",
    )
    versions: dict[str, str] = {}
    for package in packages:
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            continue
    return versions


def build_analysis(
    record: JobRecord, request: AnalysisRequest, data: dict[str, dict[str, Any]]
) -> AnalysisDocument:
    pair = data.get("PAIRING", {})
    discovery = data.get("DISCOVERY", {})
    hazard = data.get("HAZARD", {})
    inference = data.get("INFERENCE", {})
    preprocessing = data.get("PREPROCESSING", {})
    osm = data.get("OSM", {})
    exposure = data.get("INFRASTRUCTURE", {})
    connectivity_data = data.get("CONNECTIVITY", {})
    settlement_results = [
        item
        for item in connectivity_data.get("results", [])
        if item.get("status") in {"CUT_OFF", "DETOUR"}
    ]
    settlement_results.sort(
        key=lambda item: (
            0 if item.get("status") == "CUT_OFF" else 1,
            -(item.get("detour_ratio") or 0.0),
            item.get("name") or item.get("settlement_id", ""),
        )
    )

    def scene_summary(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        public_fields = (
            "scene_id",
            "platform",
            "acquired_at",
            "product_type",
            "instrument_mode",
            "polarizations",
            "orbit_direction",
            "relative_orbit",
            "aoi_coverage_fraction",
        )
        return {key: value[key] for key in public_fields if key in value}

    return AnalysisDocument(
        job_id=record.job_id,
        status="PARTIAL" if record.status == "PARTIAL" else "SUCCEEDED",
        aoi=request.aoi_geojson,
        aoi_summary={
            "aoi_area_km2": discovery.get("aoi_area_km2"),
            "aoi_bbox_wgs84": discovery.get("aoi_bbox_wgs84", []),
        },
        event_date=request.event_date.isoformat(),
        scenes={
            "discovery": {
                "initial_window_days": discovery.get("initial_window_days"),
                "scene_count": discovery.get("scene_count"),
            },
            "pairing": {
                "pre": scene_summary(pair.get("pre")),
                "post": scene_summary(pair.get("post")),
                "score": pair.get("score"),
                "criteria": pair.get("criteria", {}),
                "alternatives": pair.get("alternatives", []),
                "warnings": pair.get("warnings", []),
                "search_window_days": pair.get("search_window_days"),
            },
        },
        hazards={
            "preprocessing": {
                "crs": preprocessing.get("crs"),
                "processing_steps": preprocessing.get("processing_steps", []),
            },
            "inference": {
                key: inference[key]
                for key in (
                    "valid_pixel_fraction",
                    "valid_pixels",
                    "flooded_pixels",
                    "flooded_area_km2",
                    "model_sha256",
                    "model_name",
                    "model_version",
                )
                if key in inference
            },
            "hazard": {
                key: hazard[key]
                for key in (
                    "flood_polygon_count",
                    "debris_candidate_count",
                    "flood_layer",
                    "debris_candidate_layer",
                )
                if key in hazard
            },
        },
        infrastructure={
            "osm": {
                key: osm[key]
                for key in (
                    "provider",
                    "snapshot_at",
                    "feature_count",
                    "quality",
                    "layer",
                    "provenance",
                )
                if key in osm
            },
            "exposure": {
                key: exposure[key]
                for key in (
                    "building_summary",
                    "bridge_affected_count",
                    "bridge_count",
                    "road_summary",
                    "segment_count",
                    "road_layer",
                    "building_layer",
                    "bridge_layer",
                    "coverage_available",
                )
                if key in exposure
            },
        },
        connectivity={
            **{
                key: connectivity_data[key]
                for key in ("summary", "settlement_count", "layer")
                if key in connectivity_data
            },
            "most_affected_settlements": settlement_results[:5],
        },
        provenance={"warnings": record.warnings},
        warnings=record.warnings,
    )


def render_report(analysis: dict[str, Any]) -> str:
    hazards = analysis.get("hazards", {})
    inference = hazards.get("inference", {})
    products = hazards.get("hazard", {})
    infrastructure = analysis.get("infrastructure", {}).get("exposure", {})
    buildings = infrastructure.get("building_summary", {})
    roads = infrastructure.get("road_summary", {})
    connectivity = analysis.get("connectivity", {}).get("summary", {})
    exposure = analysis.get("infrastructure", {}).get("exposure", {})
    rows = [
        ("AOI area (km2)", analysis.get("aoi_summary", {}).get("aoi_area_km2")),
        ("Flooded area (km2)", inference.get("flooded_area_km2")),
        ("Flood polygons", products.get("flood_polygon_count")),
        ("Debris-change candidates", products.get("debris_candidate_count")),
        ("Potentially affected buildings", buildings.get("affected_count")),
        ("Buildings assessed", buildings.get("total_count")),
        ("Potentially affected bridges", infrastructure.get("bridge_affected_count")),
        ("Bridges assessed", infrastructure.get("bridge_count")),
        ("Roads: open (km)", roads.get("open_km")),
        ("Roads: potentially blocked (km)", roads.get("potentially_blocked_km")),
        ("Roads: blocked (km)", roads.get("blocked_km")),
        ("Roads: unknown coverage (km)", roads.get("unknown_coverage_km")),
        ("Settlements connected", connectivity.get("connected")),
        ("Settlements with detours", connectivity.get("detour")),
        ("Settlements cut off", connectivity.get("cut_off")),
        ("Settlements without baseline access", connectivity.get("no_baseline_access")),
        ("Valid hazard coverage available", exposure.get("coverage_available")),
    ]
    metric_rows = "".join(
        "<tr><th>" + html.escape(str(label)) + "</th><td>" + html.escape(str(value)) + "</td></tr>"
        for label, value in rows
        if value is not None
    )
    pair = analysis.get("scenes", {}).get("pairing", {})
    pre = pair.get("pre") or {}
    post = pair.get("post") or {}
    model_name = inference.get("model_name", "Not available")
    osm = analysis.get("infrastructure", {}).get("osm", {})
    warnings = analysis.get("warnings", [])
    warning_list = "".join("<li>" + html.escape(str(item)) + "</li>" for item in warnings)
    bbox = analysis.get("aoi_summary", {}).get("aoi_bbox_wgs84", [])
    bbox_text = ", ".join(str(value) for value in bbox) if bbox else "Not available"

    def display(value: Any) -> str:
        return "Not available" if value is None else str(value)

    aoi_type = analysis.get("aoi", {}).get("type", "Geometry")
    aoi_crs = analysis.get("aoi_summary", {}).get("coordinate_reference_system", "EPSG:4326")
    affected_rows = "".join(
        "<tr><td>"
        + html.escape(str(item.get("name") or item.get("settlement_id", "Unknown")))
        + "</td><td>"
        + html.escape(str(item.get("status", "Unknown")))
        + "</td><td>"
        + html.escape(display(item.get("before_time_s")))
        + "</td><td>"
        + html.escape(display(item.get("after_time_s")))
        + "</td></tr>"
        for item in analysis.get("connectivity", {}).get("most_affected_settlements", [])
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>VALLEYEYE Analysis Report</title><style>
body{{font:14px/1.4 system-ui,sans-serif;margin:1.5rem auto;
max-width:760px;padding:0 1rem;color:#17212b}}
h1{{font-size:1.4rem;margin-bottom:.2rem}}h2{{font-size:1rem;margin:1rem 0 .35rem}}
.meta{{color:#52616b;margin:.1rem 0}}table{{border-collapse:collapse;width:100%}}
th,td{{padding:.22rem .4rem;border-bottom:1px solid #d8dfe3;text-align:left}}
td{{font-variant-numeric:tabular-nums}}th{{font-weight:500;width:72%}}
@page{{size:letter;margin:0.55in}}
@media print{{body{{margin:0;max-width:none;padding:0}}h2{{break-after:avoid}}}}
</style></head><body>
<h1>VALLEYEYE Flood Impact Assessment</h1>
<p class="meta">Event date: {html.escape(str(analysis.get("event_date", "Not available")))}
 &middot; Status: {html.escape(str(analysis.get("status", "Not available")))}</p>
<p class="meta">Job: {html.escape(str(analysis.get("job_id", "Not available")))}</p>
<p class="meta">AOI: {html.escape(str(aoi_type))}
 ({html.escape(str(aoi_crs))});
 bbox [west, south, east, north]: {html.escape(bbox_text)}</p>
<h2>Analysis</h2><table><tbody>{metric_rows}</tbody></table>
<h2>Evidence</h2>
<p class="meta">Pre-event: {html.escape(str(pre.get("scene_id", "Not available")))}
 ({html.escape(str(pre.get("acquired_at", "date unavailable")))})</p>
<p class="meta">Post-event: {html.escape(str(post.get("scene_id", "Not available")))}
 ({html.escape(str(post.get("acquired_at", "date unavailable")))})</p>
<p class="meta">Model: {html.escape(str(model_name))}</p>
<p class="meta">Historical OSM snapshot:
{html.escape(str(osm.get("snapshot_at", "Not available")))}</p>
<h2>Potential access impacts</h2>
<table><thead><tr><th>Settlement</th><th>Status</th><th>Before time (s)</th>
<th>After time (s)</th></tr></thead><tbody>{affected_rows}</tbody></table>
<h2>Interpretation limits</h2>
<p class="meta">Infrastructure values indicate potential exposure, not confirmed physical damage.
Debris-change candidates are not confirmed debris. Road segments outside valid satellite coverage
remain unknown rather than assumed open.</p>
<ul>{warning_list}</ul>
</body></html>"""


def _artifact_descriptor(path: Path, job_dir: Path) -> ArtifactDescriptor:
    relative = path.relative_to(job_dir).as_posix()
    digest = sha256()
    with path.open("rb") as artifact_file:
        for chunk in iter(lambda: artifact_file.read(1024 * 1024), b""):
            digest.update(chunk)
    suffix = path.suffix.casefold()
    media_type = (
        "application/geo+json"
        if suffix == ".geojson"
        else "application/json"
        if suffix == ".json"
        else "text/html"
        if suffix == ".html"
        else "image/tiff"
        if suffix in {".tif", ".tiff"}
        else "application/octet-stream"
    )
    return ArtifactDescriptor(
        path=relative,
        sha256=digest.hexdigest(),
        size_bytes=path.stat().st_size,
        media_type=media_type,
    )


def collect_artifacts(job_dir: Path) -> dict[str, ArtifactDescriptor]:
    artifacts: dict[str, ArtifactDescriptor] = {}
    excluded = {"job.json", "job.json.part", "request.json", "run_manifest.json"}
    for path in sorted(job_dir.rglob("*")):
        if not path.is_file() or path.name in excluded or path.name.endswith(".part"):
            continue
        descriptor = _artifact_descriptor(path, job_dir)
        artifacts[descriptor.path] = descriptor
    return artifacts


def write_results(
    job_dir: Path,
    record: JobRecord,
    request: AnalysisRequest,
    data: dict[str, dict[str, Any]],
    public_config: dict[str, object],
) -> dict[str, ArtifactDescriptor]:
    analysis = build_analysis(record, request, data).model_dump(mode="json")
    (job_dir / "analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    (job_dir / "report.html").write_text(render_report(analysis), encoding="utf-8")
    results_stage = next((stage for stage in record.stages if stage.name == "RESULTS"), None)
    if results_stage is not None and results_stage.started_at is not None:
        results_stage.ended_at = datetime.now(UTC)
        results_stage.duration_ms = int(
            (results_stage.ended_at - results_stage.started_at).total_seconds() * 1000
        )
    record.ended_at = datetime.now(UTC)
    stages = [stage.model_dump(mode="json") for stage in record.stages]
    artifact_records = collect_artifacts(job_dir)
    preprocessing = data.get("PREPROCESSING", {})
    pairing = data.get("PAIRING", {})
    input_files = []
    for role in ("pre_product", "post_product"):
        artifact_path = preprocessing.get(role)
        descriptor = artifact_records.get(str(artifact_path)) if artifact_path else None
        scene_key = "pre" if role == "pre_product" else "post"
        scene = pairing.get(scene_key, {})
        if descriptor is not None:
            input_files.append(
                {
                    "role": role,
                    "scene_id": scene.get("scene_id") if isinstance(scene, dict) else None,
                    "artifact": descriptor.model_dump(mode="json"),
                }
            )
    manifest: dict[str, Any] = {
        "schema_version": "1.0.0",
        "job_id": record.job_id,
        "created_at": record.created_at.isoformat(),
        "generated_at": datetime.now(UTC).isoformat(),
        "git_revision": _git_revision(),
        "software_version": __version__,
        "python_version": platform.python_version(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "package_versions": _package_versions(),
        "random_seeds": {},
        "randomness": "No randomized sampling is used by the configured pipeline stages.",
        "effective_public_config": public_config,
        "model": {
            key: data.get("INFERENCE", {}).get(key)
            for key in ("model_name", "model_version", "model_sha256")
            if data.get("INFERENCE", {}).get(key) is not None
        },
        "selected_scenes": {
            key: data.get("PAIRING", {}).get(key, {}).get("scene_id")
            for key in ("pre", "post")
            if isinstance(data.get("PAIRING", {}).get(key), dict)
        },
        "stages": stages,
        "inputs": {
            "event_date": request.event_date.isoformat(),
            "files": input_files,
            "aoi_sha256": sha256(
                json.dumps(request.aoi_geojson, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        },
        "osm": data.get("OSM", {}).get("provenance", {}),
        "artifact_hash_scope": [
            "all job files except request.json, job.json and run_manifest.json"
        ],
        "artifacts": {
            name: item.model_dump(mode="json") for name, item in artifact_records.items()
        },
    }
    manifest_path = job_dir / "run_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    artifact_records["run_manifest.json"] = _artifact_descriptor(manifest_path, job_dir)
    return artifact_records
