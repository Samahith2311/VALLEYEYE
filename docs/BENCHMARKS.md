# Synthetic GIS Benchmarks

Run date: 2026-10-05. Command: `uv run python scripts/benchmark_synthetic.py --roads 5000 --buildings 25000`.

This is a deterministic synthetic load, not a measured geographic AOI: 5,000 road features, 25,000 building footprints and 9 flood polygons in EPSG:32631. Timings are one local run using `perf_counter`; memory is peak Python allocation from `tracemalloc` and excludes native NumPy/GEOS allocation. Treat these numbers as a baseline for this machine, not a service-level target.

Environment: Windows 11 (10.0.26300), Python 3.13.2, Intel64 Family 6 Model 183 Stepping 1, EPSG:32631.

| Stage | Runtime | Peak Python allocation | Output |
|---|---:|---:|---|
| Building exposure | 0.3593 s | 7,445,598 bytes | 25,000 assessed; 4,500 affected under synthetic geometry/default threshold |
| Road split and classify | 1.8966 s | 2,404,479 bytes | 5,612 segments |
| Build baseline and post-event graphs independently (before) | 1.4148 s | 23,172,839 bytes | 11,224 / 9,138 directed edges |
| Build baseline once, derive post-event graph (after) | 1.0301 s | 23,157,161 bytes | 11,224 / 9,138 directed edges |

Graph reuse reduced the measured graph-pair runtime by 27.2%; peak Python allocation fell by about 0.07% and should be considered unchanged. The benchmark asserts the derived post-event graph is structurally equal to the rebuild baseline before reporting. Unit tests also compare both strict and optimistic policies. No other optimization was made: road splitting was the largest individual measured stage, but this single synthetic run did not justify adding more complexity.
