# VALLEYEYE

VALLEYEYE is a backend for reproducible flood extent, infrastructure exposure, and road connectivity analysis. This repository follows the backend-only master build prompt; it does not include a browser application.

## Setup

Requires Python 3.11, 3.12, or 3.13. Create a virtual environment, install the package with its development extras, and copy `.env.example` to `.env` if CDSE downloads will be used.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

## Run

```powershell
make run
```

The API provides `GET /health` and `GET /ready` in Phase 1. Interactive OpenAPI documentation is served at `/docs`.

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

CDSE credentials are only used by the server-side token client and are never included in responses or logs. See [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) for sources and verification dates.

## Architecture

`src/valleyeye/api` contains the FastAPI boundary, `core` holds shared settings, geometry checks and errors, and `cdse` handles authentication, STAC discovery and scene pairing. Later processing phases add raster/ML, OSM/network, orchestration and reporting modules. The phase checkpoints and limitations are recorded under `docs/`.
