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
