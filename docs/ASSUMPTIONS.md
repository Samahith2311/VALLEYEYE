# Assumptions

## A1 — Backend scope follows the master build prompt

The supplied hackathon requirements include an interactive dashboard and Trishuli/EMSR927 demonstration. The master build prompt explicitly says backend only, prohibits frontend code, and isolates EMSR927 to evaluation. This repository follows the more specific master build prompt for production scope. The discrepancy remains an open requirement traceability issue and may affect judging if the dashboard is mandatory.

## A2 — Geographic input is WGS84 GeoJSON geometry

AOIs are supplied as Polygon or MultiPolygon geometry in longitude/latitude coordinates. This matches the official STAC API's WGS84 geometry search input. If callers provide another CRS, validation rejects it rather than guessing a transformation.

## A3 — Configurable Phase 1 defaults

Initial S1 search window is 12 days on either side, maximum is 96 days, and the expansion factor is 2. These are operational defaults only; the pair search policy exposes them through settings and records the effective values. AOI limits default to 0.1 to 5000 km2 to prevent empty or unexpectedly large requests; deployments should tune these for the available processing capacity. A valid pair must cover at least 80% of the AOI, a conservative default to avoid reporting an incomplete scene as full-area coverage. The weighted pair score gives equal emphasis to pre/post timing and footprint overlap, then smaller weight to footprint coverage and same-platform preference; these weights are operational ranking defaults, not validated performance coefficients. A temporal span greater than 24 days triggers a warning.
