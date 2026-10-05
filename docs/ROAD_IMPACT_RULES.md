# Road and Infrastructure Impact Rules

All overlap and distance computations use the AOI-derived projected metric CRS. OSM feature geometry enters in WGS84 and is projected before measurement. Results describe potential exposure, not confirmed physical damage.

| Product | Default rule | Setting | Interpretation |
|---|---|---|---|
| Building | A building is affected when the flood overlap is positive and at least 10% of its footprint. | `building_affected_fraction_threshold=0.1` | Overlap fractions and footprint areas are retained per building; summary area is the sum of affected footprints, not flood intersection area. |
| Road | Split linework at flood-polygon boundaries; `OPEN` below 1% overlap, `POTENTIALLY_BLOCKED` from 1% to below 50%, and `BLOCKED` at 50% or above. | `road_potentially_blocked_fraction=0.01`, `road_blocked_fraction=0.5` | A stable output segment ID includes the parent OSM way ID and split-part index. |
| Road coverage | If a segment is not wholly inside the valid inference-mask coverage geometry, label `UNKNOWN_COVERAGE`. | Derived from valid raster-mask pixels | Unknown segments are never called open and are removed from the default post-event graph. |
| Bridge | Buffer the hazard corridor around OSM bridge ways by 20 m; a non-tunnel bridge is affected by any intersection by default. | `bridge_hazard_buffer_m=20`, `bridge_block_on_intersection=true` | Conservative screening rule, not structural damage assessment. The implementation can instead apply a configured overlap fraction. |
| `G_before` | Include all configured drivable highway classes and respect OSM `oneway`; use OSM `maxspeed` when parseable, otherwise highway-class defaults. | `road_speed_defaults_kmh` | Travel times are model estimates. mph values are converted; unusable speed tags use the class default. |
| `G_after` | Remove `BLOCKED` and `UNKNOWN_COVERAGE`; retain `POTENTIALLY_BLOCKED` by default. | `remove_potentially_blocked_edges=false` | Setting it to true produces a stricter scenario without rebuilding road geometry. |
| Settlement access | Snap settlements and sources to the nearest graph node or edge within 500 m. Sources are hospitals/clinics and `place=town`/`city`. | `network_max_snap_distance_m=500` | An unsnappable or baseline-unreachable settlement is `NO_BASELINE_ACCESS`, never `CUT_OFF`. |
| Connectivity status | Select the fastest network route to any configured source. Classify `CUT_OFF` if the baseline route disappears; otherwise `DETOUR` if either network-distance or travel-time ratio exceeds 1.25, else `CONNECTED`. | `network_detour_ratio_threshold=1.25` | Ratios are against the pre-event route. Straight-line distance is not used for classification. |

These thresholds are configurable defaults rather than empirically calibrated values. The current road segmentation is spatially indexed against hazard features and only splits hazard-intersecting roads. The pipeline derives coverage from valid inference-mask pixels and retains the strict/optimistic policy in effective configuration. This path has not been verified against a real SNAP product.
