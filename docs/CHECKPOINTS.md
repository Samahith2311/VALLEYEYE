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
