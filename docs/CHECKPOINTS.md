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
- Phase 2 model verification is blocked on an explicit license/source statement for the checkpoint artifacts.
