# Limitations

- Phase 1 implements service foundation and Sentinel-1 discovery/pair selection only. Raster processing, flood inference, OSM, network connectivity, job persistence and reports are not implemented yet.
- The supplied requirements mention an interactive web application, while the master build prompt requires a backend-only product. The implementation follows backend-only scope; see `ASSUMPTIONS.md`.
- The Kuro Siwo repository links public FloodViT and SNUNet checkpoints, but the checkpoint artifact licence is not explicitly documented. The model input specification, weight hash, and license compatibility have not been verified. Phase 2 is gated on resolving this.
- Phase 1 can search the public STAC catalogue without account credentials. Token-protected product downloads require valid CDSE credentials in the server environment.
