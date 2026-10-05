# Phase Checkpoints

Use this record after each implementation phase.

## Phase 1 — Foundation + Copernicus Data Space integration — 2026-10-02
Built:
- FastAPI health/readiness API, typed settings, structured errors and secret-redacting JSON logs.
- Server-side CDSE access-token acquisition/refresh with retry handling; paginated STAC search; validated AOIs; configurable adaptive Sentinel-1 pair selection.
- Packaging, Docker/Compose, Make targets, environment example, source register and mocked/offline test scaffolding.

Tests: 30 passed / 0 failed / 0 skipped; `uv run pytest -q --cov=valleyeye --cov-report=term-missing` (87% coverage). API smoke tests: 2 passed with `uv run pytest -q -m e2e`. `uv run ruff check .`, `uv run mypy src/valleyeye`, `uv lock --check`, and `uv run ruff format --check .` all passed. `make` is unavailable on this Windows host, so equivalent commands were run with `uv`.
Not verified: live CDSE credentials and product downloads; model checkpoint licence/input specification/hash. The suite reports one upstream Starlette/AnyIO deprecation warning.
Assumptions: [A1](ASSUMPTIONS.md#A1--backend-scope-follows-the-master-build-prompt), [A2](ASSUMPTIONS.md#A2--geographic-input-is-wgs84-geojson-geometry), [A3](ASSUMPTIONS.md#A3--configurable-phase-1-defaults).
Known gaps:
- The provided checklist's dashboard requirement conflicts with the backend-only master prompt.
- At the Phase 1 boundary, model source, licence and input-contract verification were still open; resolved and documented in Phase 2.

## Phase 2 — Sentinel-1 processing and flood AI — 2026-10-05
Built:
- Verified the public Kuro Siwo SNUNet checkpoint against the upstream repository and the user-provided MIT notice; added a SHA-256-checking downloader, model card, third-party notice and strict model adapter.
- Added SNAP GPT preprocessing graph construction, metric-grid raster alignment and validation, slope derivation, tiled inference, flood probability/mask outputs, and window-streamed flood/debris-candidate GeoJSON products.
- Added typed settings for preprocessing and hazard thresholds; documented data lineage, model input assumptions and known output limits.

Tests: 44 passed / 0 failed / 0 skipped; `uv run pytest -q --cov=valleyeye --cov-report=term-missing` (84% coverage). E2E: 2 passed / 0 failed / 42 deselected; `uv run pytest -q -m e2e`. `uv run ruff check .`, `uv run mypy src/valleyeye`, `uv lock --check`, and `uv run ruff format --check .` passed. `uv run valleyeye-fetch-weights --output weights/kuro-siwo-snunet.pt` downloaded and verified SHA-256 `6c6bcb78d956f9d139743eb5bc44cf8e14528b643f9cbf5f4478f4e321a609ed`.
Not verified: SNAP GPT was not available, so no real SAFE product was processed. No held-out labelled dataset was used to validate flood-detection accuracy or the inferred checkpoint input semantics.
Assumptions: [A4](ASSUMPTIONS.md#a4--kuro-siwo-checkpoint-channel-contract), [A5](ASSUMPTIONS.md#a5--snap-is-the-production-sar-preprocessor), [A6](ASSUMPTIONS.md#a6--streamed-geojson-may-split-features-at-window-edges).
Known gaps:
- Live CDSE product download, real SNAP execution, OSM, network connectivity, job orchestration and reports remain for later phases.
- Debris layers are uncalibrated backscatter-change candidates only; contiguous polygon features may be split at processing-window boundaries.

## Phase 3 — GIS and connectivity engine — 2026-10-05
Built:
- Added a documented ohsome API v2 historical OSM provider with strict pre-event snapshots, API-key redaction, buffered WGS84 query, Parquet/WKB normalization, geometry repair, and response/query hashes.
- Added indexed building and bridge exposure, flood-boundary road splitting, configurable `OPEN`/`POTENTIALLY_BLOCKED`/`BLOCKED`/`UNKNOWN_COVERAGE` classification, and metric-area summaries.
- Added directed before/after road graphs, one-way and maxspeed handling, nearest node/edge snapping, multi-source travel-time routing, distance/time detour classification, cutoff identification, and blocking-road lineage.
- Documented data provenance, ODbL attribution, assumptions, network policies, and all new settings.

Tests: 52 passed / 0 failed / 0 skipped; `uv run pytest -q --cov=valleyeye --cov-report=term-missing` (85% coverage). E2E: 2 passed / 0 failed / 50 deselected; `uv run pytest -q -m e2e`. `uv run ruff check .`, `uv run mypy src/valleyeye`, `uv lock --check`, and `uv run ruff format --check .` passed. All OSM tests use mocked Parquet responses; the suite blocks live network access.
Not verified: no HeiGIT ohsome API key was available, so no live historical OSM extract was requested. Spatial and network behavior was verified on synthetic geometries only; no field-validated travel-speed or damage thresholds were used.
Assumptions: [A7](ASSUMPTIONS.md#a7--historical-osm-provider-and-snapshot-instant), [A8](ASSUMPTIONS.md#a8--osm-network-buffer-and-access-sources), [A9](ASSUMPTIONS.md#a9--exposure-and-network-defaults).
Known gaps:
- OSM, exposure and connectivity modules are not yet connected to a job pipeline; the caller must provide the flood-valid coverage geometry or roads remain `UNKNOWN_COVERAGE`.
- A live API key and validation of data/service terms and quotas are required before operational use.

## Phase 4 — End-to-end pipeline and API — 2026-10-05
Built:
- Added asynchronous, disk-persisted jobs across the fixed `DISCOVERY` through `RESULTS` stages, with queue/concurrency limits, timestamps, progress, stage durations, structured errors, cancellation, restart recovery and per-job artifacts.
- Connected production stage handlers to CDSE STAC/authenticated downloads, SNAP, verified SNUNet inference, hazard products, pre-event ohsome extraction, exposure and connectivity. Tests substitute CDSE, download, SNAP raster generation, model and OSM with deterministic synthetic fixtures.
- Added a content-addressed stage cache using request, configuration, prior-stage output and source-code fingerprints, a TTL and `no_cache=true` bypass.
- Added job status/cancel, summary, protected layer access, report and provenance endpoints. The validated `analysis.json` has a generated JSON Schema; the one-page HTML report is rendered only from its values, with a test that extracts every visible number and verifies it occurs in the analysis JSON.
- Added SHA-256 artifact records, input product hashes, model hash, selected scene IDs, OSM snapshot/query/response provenance, software/platform versions, and effective non-secret settings.

Tests: `uv run pytest -q --cov=valleyeye --cov-report=term-missing` (63 passed / 0 failed / 0 skipped; 88% total coverage). `uv run pytest -q -m e2e` (6 passed / 0 failed / 57 deselected). `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/valleyeye`, and `uv lock --check` passed. Pytest reports upstream Starlette/AnyIO and Rasterio deprecation warnings.
Not verified: the full production handlers completed against mocked services and synthetic SNAP rasters; no live CDSE or ohsome requests, real SNAP execution, or real production job was run. Actual SNUNet loading/inference is covered separately by model/raster tests; the complete API E2E uses a deterministic model stub.
Assumptions: [A10](ASSUMPTIONS.md#a10--job-durability-and-worker-topology).
Known gaps:
- The job queue is process-local; use one API worker per storage root. Restarted jobs are marked failed, not resumed. OSM and other synchronous geospatial worker-thread functions are not forcibly interrupted after cancellation; SNAP subprocess and SNUNet tile processing are cooperative.
- Real-data accuracy, live service availability/quotas, operational resource limits and the user-supplied EMSR927 evaluation remain unverified.
