# VALLEYEYE

VALLEYEYE is a backend for reproducible flood extent, infrastructure exposure, and road connectivity analysis. This repository follows the backend-only master build prompt; it does not include a browser application.

## Setup

Requires Python 3.11, 3.12, or 3.13. Create a virtual environment, install the package with development and ML extras, and copy `.env.example` to `.env` if CDSE downloads or SNAP preprocessing will be used.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,ml]" --extra-index-url https://download.pytorch.org/whl/cpu
Copy-Item .env.example .env
```

Fetch the verified SNUNet checkpoint separately. It is approximately 145 MB and remains in the git-ignored `weights/` directory.

```powershell
python -m valleyeye.ml.fetch_weights
```

## Run

```powershell
make run
```

The API currently provides `GET /health` and `GET /ready`. Interactive OpenAPI documentation is served at `/docs`.

## Sentinel-1 and flood inference

SNAP GPT is required for real Sentinel-1 GRD products. Set `VALLEYEYE_SNAP_GPT_PATH` to its executable. SNAP applies orbit, thermal/border-noise, sigma0 calibration, Lee Sigma filtering, and terrain correction on an AOI-derived metric grid; its SRTM 1 arc-second DEM is also used to derive the model's slope channel. A missing SNAP executable or model checkpoint is a structured error, not a fallback to unprocessed imagery.

The model expects aligned pre/post rasters with VV/VH sigma0 in linear units plus shared `slope_riserun`. Inference uses 224-pixel tiles with 32-pixel context overlap, streams windows, writes float32 flood probabilities and uint8 masks, and excludes nodata pixels. `flood.geojson` includes per-polygon probability statistics. `debris_candidate.geojson` contains only evidence-backed backscatter-change candidates and never claims confirmed debris. The checkpoint hash, inputs, and class mapping are in [`docs/MODEL_CARD.md`](docs/MODEL_CARD.md), `docs/DATA_SOURCES.md`, and `THIRD_PARTY_NOTICES.md`.

Historical OSM extraction uses the documented ohsome API v2 and requires a HeiGIT API key in `OHSOME_API_KEY`. Requests select the final UTC second before the event date; there is no current-OSM fallback. See `docs/ROAD_IMPACT_RULES.md` for exposure and connectivity policy.

## Test and lint

```powershell
make test
make lint
make e2e
```

Tests use mocked HTTP and block socket connections so they do not access live services.

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `CDSE_USERNAME` | CDSE account for token-protected product downloads | unset |
| `CDSE_PASSWORD` | CDSE password | unset |
| `CDSE_TOTP` | Optional one-time code when account 2FA is enabled | unset |
| `VALLEYEYE_CDSE_IDENTITY_URL` | CDSE token endpoint | documented endpoint |
| `VALLEYEYE_CDSE_CLIENT_ID` | CDSE OAuth client ID | `cdse-public` |
| `VALLEYEYE_STAC_API_URL` | CDSE STAC API root | documented endpoint |
| `VALLEYEYE_S1_COLLECTION` | Sentinel-1 GRD collection | `sentinel-1-grd` |
| `VALLEYEYE_S2_COLLECTION` | Sentinel-2 L2A collection | `sentinel-2-l2a` |
| `VALLEYEYE_HTTP_TIMEOUT_SECONDS` | Upstream request timeout | `30` |
| `VALLEYEYE_RETRY_MAX_ATTEMPTS` | Transient upstream retry limit | `4` |
| `VALLEYEYE_TOKEN_REFRESH_SKEW_SECONDS` | Refresh token before access-token expiry | `60` |
| `VALLEYEYE_S1_INITIAL_WINDOW_DAYS` | Initial pre/post search window | `12` |
| `VALLEYEYE_S1_MAX_WINDOW_DAYS` | Maximum adaptive pre/post search window | `96` |
| `VALLEYEYE_S1_WINDOW_MULTIPLIER` | Adaptive expansion factor | `2` |
| `VALLEYEYE_MIN_S1_AOI_COVERAGE` | Minimum footprint coverage for a valid pair | `0.8` |
| `VALLEYEYE_MIN_AOI_AREA_KM2` | Minimum accepted AOI area | `0.1` |
| `VALLEYEYE_MAX_AOI_AREA_KM2` | Maximum accepted AOI area | `5000` |
| `VALLEYEYE_PAIR_LONG_GAP_WARNING_DAYS` | Pair temporal-span warning threshold | `24` |
| `VALLEYEYE_PAIR_WEIGHT_OVERLAP` | Pair score weight for footprint overlap | `0.25` |
| `VALLEYEYE_PAIR_WEIGHT_PRE_CLOSENESS` | Pair score weight for pre-event timing | `0.25` |
| `VALLEYEYE_PAIR_WEIGHT_POST_CLOSENESS` | Pair score weight for post-event timing | `0.25` |
| `VALLEYEYE_PAIR_WEIGHT_SAME_PLATFORM` | Pair score weight for platform match | `0.1` |
| `VALLEYEYE_PAIR_WEIGHT_COVERAGE` | Pair score weight for AOI coverage | `0.15` |
| `VALLEYEYE_MODEL_WEIGHTS_PATH` | Verified SNUNet checkpoint path | `weights/kuro-siwo-snunet.pt` |
| `VALLEYEYE_INFERENCE_DEVICE` | `auto`, `cpu`, or a CUDA device | `auto` |
| `VALLEYEYE_FLOOD_PROBABILITY_THRESHOLD` | Class-2 threshold for the flood mask | `0.5` |
| `VALLEYEYE_INFERENCE_TILE_SIZE` | SNUNet tile size; must be divisible by 16 | `224` |
| `VALLEYEYE_INFERENCE_OVERLAP` | Context margin for streamed tiles | `32` |
| `VALLEYEYE_SNAP_GPT_PATH` | SNAP GPT executable path | unset |
| `VALLEYEYE_SNAP_PIXEL_SPACING_M` | Terrain-correction output spacing | `10` |
| `VALLEYEYE_SNAP_AOI_BUFFER_M` | Metric buffer before terrain correction | `500` |
| `VALLEYEYE_MINIMUM_FLOOD_POLYGON_AREA_KM2` | Minimum mapped flood polygon size | `0.001` |
| `VALLEYEYE_CANDIDATE_BACKSCATTER_CHANGE_THRESHOLD_DB` | VV-change threshold for candidate mapping | `3.0` |
| `VALLEYEYE_MINIMUM_CANDIDATE_POLYGON_AREA_KM2` | Minimum candidate polygon size | `0.001` |
| `OHSOME_API_KEY` | HeiGIT key for historical OSM extraction | unset |
| `VALLEYEYE_OHSOME_API_URL` | ohsome API v2 endpoint | documented endpoint |
| `VALLEYEYE_OHSOME_TIMEOUT_SECONDS` | Historical extraction timeout | `240` |
| `VALLEYEYE_OSM_AOI_BUFFER_M` | Metric buffer for OSM roads around the AOI | `5000` |
| `VALLEYEYE_BUILDING_AFFECTED_FRACTION_THRESHOLD` | Minimum footprint overlap to count a building as affected | `0.1` |
| `VALLEYEYE_ROAD_POTENTIALLY_BLOCKED_FRACTION` | Minimum flood-overlap fraction for a potential road block | `0.01` |
| `VALLEYEYE_ROAD_BLOCKED_FRACTION` | Flood-overlap fraction for a blocked road | `0.5` |
| `VALLEYEYE_BRIDGE_HAZARD_BUFFER_M` | Bridge corridor buffer around flood geometry | `20` |
| `VALLEYEYE_BRIDGE_BLOCK_ON_INTERSECTION` | Block a bridge for any buffered flood intersection | `true` |
| `VALLEYEYE_NETWORK_MAX_SNAP_DISTANCE_M` | Maximum settlement/source snap distance to the road graph | `500` |
| `VALLEYEYE_NETWORK_DETOUR_RATIO_THRESHOLD` | Distance or travel-time ratio that marks a detour | `1.25` |
| `VALLEYEYE_REMOVE_POTENTIALLY_BLOCKED_EDGES` | Use strict rather than optimistic connectivity policy | `false` |
| `VALLEYEYE_ROAD_SPEED_DEFAULTS_KMH` | JSON highway-class speeds used when OSM maxspeed is absent | configured defaults |

CDSE credentials are only used by the server-side token client and are never included in responses or logs. See [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) for sources and verification dates.

## Architecture

`src/valleyeye/api` contains the FastAPI boundary, `core` holds shared settings, geometry checks and errors, `cdse` handles authentication, STAC discovery and scene pairing, `sar` provides SNAP/raster tools, `ml` verifies and runs SNUNet, `hazard` exports flood and debris-candidate vectors, `osm` extracts historical OSM, `infra` measures exposure, and `network` analyzes road access. Job orchestration and reporting remain later phases. The phase checkpoints and limitations are recorded under `docs/`.
