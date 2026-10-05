# Release Readiness

Review date: 2026-10-05.

## Verdict

**Not ready for operational deployment or official hackathon compliance sign-off.** The backend implementation, mocked end-to-end path, policy guards, provenance, and isolated evaluation workflow are built and tested. However, no production job has run against live imagery/OSM services, the model has no held-out flood-accuracy evaluation, no EMSR927 reference was supplied, and the separate official organizer rulebook was not provided. Do not present synthetic tests as evidence of real-world flood accuracy.

## Requirements Traceability

| Requirement | Implementation / evidence | Status |
|---|---|---|
| Arbitrary AOI and event date | WGS84 Polygon/MultiPolygon request validation and event-relative processing; geometry and API tests | Implemented |
| Sentinel-1 pre/post discovery and pairing | CDSE STAC client; adaptive selection requiring matching mode, polarization, orbit direction/relative orbit and adequate coverage; pairing tests | Implemented; live CDSE not run |
| Sentinel-2 optional refinement | Search configuration exists, but Sentinel-2 refinement is not connected to analysis | Not implemented; documented limitation |
| Flood extent from SAR | SNAP preprocessing and verified SNUNet adapter; aligned raster/tiled inference tests | Implemented; real-scene accuracy unverified |
| Debris identification | Backscatter-change candidate polygons, evidence and uncalibrated confidence; schema/report explicitly say candidate | Candidate-only, not confirmation |
| Buildings, roads and bridges | Historical OSM, indexed exposure, road splitting and hazard coverage classes; synthetic GIS tests | Implemented; thresholds not field-calibrated |
| Settlement access / cut-off analysis | Directed before/after road graph, network distance and time, detour/cut-off/no-baseline states; synthetic tests | Implemented |
| Structured job API and outputs | Async persisted jobs, nine stages, cache, cancellation, analysis schema, layers, report, provenance; mocked API E2E | Implemented; single-process worker limitation |
| AI evaluation on held-out Himalayan scenes | Isolated freeze-and-compare tooling supports user-provided raster/GeoJSON truth | Workflow implemented; required real-scene evaluation not run |
| Situation-report copilot (alternative AI option) | Not selected; flood-segmentation option was implemented instead | Not implemented |
| Interactive dashboard / map UI | Master prompt explicitly limits scope to backend; no frontend exists | Scope conflict remains open against supplied checklist |
| EMSR927 case-study comparison | No local reference supplied; no data was downloaded or fabricated | Not run |
| Downstream flood-path tracing bonus | Not implemented | Out of scope |
| Official hackathon data/rules compliance | `HACKATHON_REQUIREMENTS.md` is based on supplied project requirements, not a separate official rulebook | Cannot certify |

The complete code-level checklist and evidence are in [`AUDIT.md`](AUDIT.md). The checklist source limitation is called out in [`HACKATHON_REQUIREMENTS.md`](HACKATHON_REQUIREMENTS.md).

## Audit Summary

No Critical code findings remain. The post-event graph rebuild was replaced with baseline-graph derivation and verified against the rebuild result. OpenStreetMap attribution and ODbL 1.0 are now persisted in the OSM layer, analysis, provenance, and report. Static guards enforce no case-study terms in `src/`, no production import of `evaluation/`, and no evaluation import of production settings/modules.

One High-severity validation item remains Open: flood-detection accuracy and checkpoint input semantics have not been verified against an allowed held-out labeled scene. Live CDSE/ohsome/SNAP processing and official-rule compliance are also unverified.

## Verification

| Command | Result |
|---|---|
| `uv run pytest -q --cov=valleyeye --cov-report=term-missing` | 72 passed, 0 failed, 0 skipped; 88% total production coverage |
| `uv run pytest -q -m e2e` | 6 passed, 66 deselected |
| `uv run ruff check .` | Passed |
| `uv run ruff format --check .` | Passed; 60 files formatted |
| `uv run mypy src/valleyeye evaluation scripts/benchmark_synthetic.py` | Passed; 45 source files |
| `uv lock --check` | Passed |
| Production source policy guards | Passed as part of full suite: forbidden case-study strings and import boundaries |
| Secret pattern scan | `rg` scan produced no matches; `.env` is ignored and untracked |
| Network isolation | Tests block non-loopback socket connections; upstream HTTP is mocked |
| Default pipeline API E2E | Passed with mocked CDSE/OSM/SNAP/model and synthetic rasters |

The full suite emitted 40 upstream deprecation/pending-deprecation warnings from Starlette/AnyIO and Rasterio; no project test failed.

## EMSR927 Evaluation

**Not run.** A workspace inventory found no local EMSR927 reference file. The workflow is available via `evaluation/cli.py`: it verifies the completed job's artifact hashes, copies registered artifacts to a separate evaluation directory, marks copied artifacts read-only, writes `frozen_run.json`, then compares a local raster or GeoJSON against the frozen flood mask. Synthetic tests exercise hash verification, immutability, CRS rasterization, confusion counts, and IoU/precision/recall/F1 arithmetic; they are not EMSR927 metrics.

| Metric | Result |
|---|---|
| IoU | N/A — no reference data |
| Precision | N/A — no reference data |
| Recall | N/A — no reference data |
| F1 | N/A — no reference data |

No EMSR927 data was downloaded, no metric was fabricated, and production thresholds/weights were not tuned using evaluation data. When a reference is supplied, sensor/acquisition-time differences, delineation policy, AOI overlap and resolution must be reported alongside results.

## Benchmarks

On the recorded 5,000-road/25,000-building synthetic fixture, deriving the post-event graph from one baseline graph reduced graph-pair runtime from 1.4148 s to 1.0301 s (27.2% in this single run); graph outputs were structurally equal. Peak Python allocation was effectively unchanged. Exposure took 0.3593 s; road splitting/classification took 1.8966 s. Native-library memory is not captured. Full inputs and method are documented in [`BENCHMARKS.md`](BENCHMARKS.md).

## Model and Data Provenance

- The configured SNUNet checkpoint is the public artifact linked by Orion-AI-Lab/KuroSiwo, SHA-256 `6c6bcb78d956f9d139743eb5bc44cf8e14528b643f9cbf5f4478f4e321a609ed`. Its tensors and source repository were inspected; inference verifies the hash. The user-provided Orion Lab MIT notice is recorded in `THIRD_PARTY_NOTICES.md` and the model card. The hosted checkpoint has no embedded input metadata, so channel interpretation follows the upstream wrapper and remains an assumption.
- Sentinel-1 discovery/download uses CDSE; preprocessing uses local SNAP GPT and SRTM 1 arc-second elevation auxiliaries.
- Historical OSM uses the documented ohsome API v2 point-in-time query, strictly before the event date. OSM attribution and ODbL 1.0 are included in persisted outputs.
- URLs, identifiers, access methods, licences, verification notes and live-service limitations are listed in [`DATA_SOURCES.md`](DATA_SOURCES.md).
- No live Sentinel product, OSM response, or real end-to-end production artifact was generated during verification.

## Known Risks

- Flood accuracy and the checkpoint input contract need validation on an allowed labeled held-out scene before operational use.
- CDSE credentials, ohsome API key, SNAP GPT and external service/data availability are deployment prerequisites; these were not exercised live.
- Road/blockage, exposure, speed and detour thresholds are transparent defaults, not field-calibrated.
- The job queue is process-local; use one worker per storage root. Restarted active jobs fail rather than resume; thread-based GIS/OSM work is not forcibly interrupted.
- Official organizer rules and the dashboard requirement were not independently supplied or reconciled with the backend-only scope.

## Open Decisions

- Supply the official organizer rulebook/data policy and confirm whether a dashboard is mandatory despite the backend-only master prompt.
- Supply an allowed local EMSR927/held-out reference with CRS, layer meaning, valid-data mask and attribution, or confirm that evaluation is optional.
- Configure CDSE and ohsome credentials and install SNAP locally before requesting a live production run. Keep credentials in environment variables, not chat or Git.
