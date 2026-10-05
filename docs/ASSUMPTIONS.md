# Assumptions

## A1 — Backend scope follows the master build prompt

The supplied hackathon requirements include an interactive dashboard and Trishuli/EMSR927 demonstration. The master build prompt explicitly says backend only, prohibits frontend code, and isolates EMSR927 to evaluation. This repository follows the more specific master build prompt for production scope. The discrepancy remains an open requirement traceability issue and may affect judging if the dashboard is mandatory.

## A2 — Geographic input is WGS84 GeoJSON geometry

AOIs are supplied as Polygon or MultiPolygon geometry in longitude/latitude coordinates. This matches the official STAC API's WGS84 geometry search input. If callers provide another CRS, validation rejects it rather than guessing a transformation.

## A3 — Configurable Phase 1 defaults

Initial S1 search window is 12 days on either side, maximum is 96 days, and the expansion factor is 2. These are operational defaults only; the pair search policy exposes them through settings and records the effective values. AOI limits default to 0.1 to 5000 km2 to prevent empty or unexpectedly large requests; deployments should tune these for the available processing capacity. A valid pair must cover at least 80% of the AOI, a conservative default to avoid reporting an incomplete scene as full-area coverage. The weighted pair score gives equal emphasis to pre/post timing and footprint overlap, then smaller weight to footprint coverage and same-platform preference; these weights are operational ranking defaults, not validated performance coefficients. A temporal span greater than 24 days triggers a warning.

## A4 — SNUNet checkpoint input interpretation

The official Kuro Siwo README links the downloaded SNUNet checkpoint but the serialized checkpoint contains no data configuration or channel labels. Its tensors confirm three input channels per temporal image and three output classes. The input meanings, statistics, and labels are taken from the Kuro Siwo SNUNet wrapper reference in PR #4: pre-event then post-event, each with VV, VH, and shared `slope_riserun`; class IDs 0/1/2 are no water/permanent water/flood. This is a source-backed compatibility assumption, not artifact-embedded metadata. If the Dropbox checkpoint was trained with a different third channel or statistics, predictions will be invalid; the implementation therefore exposes the metadata and never silently falls back to a two-band input.

## A5 — Checkpoint license confirmation

The user supplied the complete MIT notice for Orion Lab, matching the Kuro Siwo repository's `LICENSE`, and confirmed it in response to the checkpoint-license gate. The repository README separately labels the dataset CC BY but does not separately label the Dropbox checkpoint. This project relies on the user's license confirmation for the checkpoint; the supplied notice is preserved in `THIRD_PARTY_NOTICES.md`. If the confirmation does not apply to the hosted checkpoint, do not redistribute or use that artifact.

## A6 — Processing toolchain and terrain source

SNAP GPT is the selected GRD processing toolchain because the official Kuro Siwo repository includes a SNAP graph with orbit, noise, calibration, speckle-filter, and terrain-correction operators. SRTM 1 arc-second HGT is used for terrain correction and slope; its tiles are auto-downloaded by SNAP. Processing is configured to use a local GPT executable and requires network access for uncached orbit/DEM auxiliaries. This environment does not have SNAP installed, so only graph generation and synthetic raster paths are verified here.

## A7 — Historical OSM provider and snapshot instant

The ohsome API v2 feature-extraction endpoint is used because its official documentation supports point-in-time OSM snapshots and feature geometries. It requires a HeiGIT API key; without one, extraction fails with `OSM_UNAVAILABLE` and no current-data fallback is attempted. For an event date, the selected snapshot is 23:59:59 UTC on the previous calendar day, guaranteeing `snapshot_date < event_date`. This instant and the API's actual historical availability should be checked before production use. OSM-derived outputs must retain ODbL attribution.

## A8 — OSM network buffer and access sources

The OSM request expands the AOI by 5 km in a local metric CRS so border settlements can route to nearby destinations outside the exact impact polygon. Hospitals/clinics and settlements tagged `place=town` or `place=city` are network destinations. These choices are configurable in code/config but have not been validated against a specific response team's service-area policy; omitted or incomplete OSM tags affect the results.

## A9 — Exposure and network defaults

Building overlap `0.10`, road potential/blocked overlap `0.01`/`0.50`, 20 m bridge corridor, 500 m snapping limit, 1.25 detour ratio, and highway-class speed defaults are transparent operational defaults. They are not calibrated against observed flood damage or travel-time data. The default post-event network is optimistic about `POTENTIALLY_BLOCKED` roads but removes `UNKNOWN_COVERAGE`; a strict policy can remove potential segments too. A lack of valid hazard coverage is never interpreted as an open road.

## A10 — Job durability and worker topology

Job requests, state, stage outputs and cache entries are filesystem-backed. The asynchronous scheduler is process-local, so a deployment sharing these directories must run only one API process. Restarted `QUEUED`/`RUNNING` jobs are marked failed rather than resumed; completed results remain readable. This is durable job history, not a distributed queue or transactional database.
