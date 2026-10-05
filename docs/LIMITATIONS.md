# Limitations

- Sentinel-1 pair discovery and Phase 2 raster/model primitives are implemented, but live CDSE product download, SNAP execution on a real SAFE product, OSM, network connectivity, job persistence and reports are not yet integrated.
- The supplied requirements mention an interactive web application, while the master build prompt requires a backend-only product. The implementation follows backend-only scope; see `ASSUMPTIONS.md`.
- The SNUNet artifact is not versioned and does not embed input metadata. Its SHA-256 and architecture load are verifiable; the three-channel input meaning is inferred from the Kuro Siwo wrapper reference and recorded in assumption A4. Validate it against a held-out allowed dataset before operational use.
- SNAP GPT and a live Sentinel-1 scene were unavailable in this environment; terrain-correction graph execution and SAR output band naming have not been live-verified. Synthetic raster inference does not establish flood-detection accuracy.
- Phase 2 does not enable Sentinel-2 refinement. Debris/sediment polygons are backscatter-change candidates only, not confirmed debris.
- Flood and candidate GeoJSON polygonization is streamed in 512-pixel windows. A contiguous feature crossing a window boundary is emitted as adjacent polygon fragments; the corresponding rasters remain authoritative. Candidate confidence bins are an uncalibrated heuristic, not statistical confidence.
- Phase 1 can search the public STAC catalogue without account credentials. Token-protected product downloads require valid CDSE credentials in the server environment.
