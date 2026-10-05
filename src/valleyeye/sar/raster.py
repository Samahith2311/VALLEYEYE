from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
import numpy.typing as npt
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject
from rasterio.windows import Window

from valleyeye.core.errors import ErrorCode, ValleyeyeError


def assert_aligned(*datasets: Any) -> None:
    if len(datasets) < 2:
        return
    reference = datasets[0]
    for dataset in datasets[1:]:
        if (
            dataset.crs != reference.crs
            or dataset.width != reference.width
            or dataset.height != reference.height
            or not dataset.transform.almost_equals(reference.transform)
        ):
            raise ValleyeyeError(
                ErrorCode.PREPROCESSING_FAILED,
                "Raster inputs do not share one CRS, extent, resolution, and pixel grid.",
                stage="PREPROCESSING",
            )


def align_raster(
    source_path: Path,
    reference_path: Path,
    destination_path: Path,
    layer_type: Literal["continuous", "categorical"] = "continuous",
) -> Path:
    method = Resampling.bilinear if layer_type == "continuous" else Resampling.nearest
    with rasterio.open(source_path) as source, rasterio.open(reference_path) as reference:
        if source.crs is None or reference.crs is None:
            raise ValleyeyeError(
                ErrorCode.PREPROCESSING_FAILED,
                "All rasters must have a defined CRS before alignment.",
                stage="PREPROCESSING",
            )
        profile = reference.profile.copy()
        profile.update(
            driver="GTiff",
            count=source.count,
            dtype=source.dtypes[0],
            nodata=source.nodata,
            compress="deflate",
        )
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(destination_path, "w", **profile) as destination:
            for band in range(1, source.count + 1):
                reproject(
                    source=rasterio.band(source, band),
                    destination=rasterio.band(destination, band),
                    src_transform=source.transform,
                    src_crs=source.crs,
                    src_nodata=source.nodata,
                    dst_transform=reference.transform,
                    dst_crs=reference.crs,
                    dst_nodata=source.nodata,
                    resampling=method,
                )
    return destination_path


def compute_slope_riserun(
    dem: npt.NDArray[np.float32], pixel_size_x: float, pixel_size_y: float
) -> npt.NDArray[np.float32]:
    """Compute Horn (1981) dimensionless rise/run slope from a projected metre DEM."""
    if dem.ndim != 2 or pixel_size_x <= 0 or pixel_size_y <= 0:
        raise ValueError("DEM must be 2D and pixel sizes must be positive")
    valid = np.isfinite(dem)
    padded = np.pad(np.where(valid, dem, 0.0), 1, mode="edge")
    dx = (
        padded[:-2, 2:]
        + 2 * padded[1:-1, 2:]
        + padded[2:, 2:]
        - padded[:-2, :-2]
        - 2 * padded[1:-1, :-2]
        - padded[2:, :-2]
    ) / (8 * pixel_size_x)
    dy = (
        padded[2:, :-2]
        + 2 * padded[2:, 1:-1]
        + padded[2:, 2:]
        - padded[:-2, :-2]
        - 2 * padded[:-2, 1:-1]
        - padded[:-2, 2:]
    ) / (8 * pixel_size_y)
    valid_window = np.ones_like(valid)
    for row_offset in (-1, 0, 1):
        for col_offset in (-1, 0, 1):
            shifted = np.roll(np.roll(valid, row_offset, axis=0), col_offset, axis=1)
            if row_offset < 0:
                shifted[row_offset:, :] = False
            elif row_offset > 0:
                shifted[:row_offset, :] = False
            if col_offset < 0:
                shifted[:, col_offset:] = False
            elif col_offset > 0:
                shifted[:, :col_offset] = False
            valid_window &= shifted
    result = np.hypot(dx, dy).astype(np.float32)
    result[~valid_window] = np.nan
    return cast(npt.NDArray[np.float32], result)


def write_slope_raster(dem_path: Path, slope_path: Path) -> Path:
    with rasterio.open(dem_path) as dem_source:
        if (
            dem_source.crs is None
            or not dem_source.crs.is_projected
            or dem_source.crs.linear_units_factor[1] != 1.0
        ):
            raise ValleyeyeError(
                ErrorCode.PREPROCESSING_FAILED,
                "Slope calculation requires a projected DEM with linear units.",
                stage="PREPROCESSING",
            )
        profile = dem_source.profile.copy()
        profile.update(driver="GTiff", count=1, dtype="float32", nodata=-9999.0, compress="deflate")
        slope_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(slope_path, "w", **profile) as destination:
            for row in range(0, dem_source.height, 512):
                for col in range(0, dem_source.width, 512):
                    height = min(512, dem_source.height - row)
                    width = min(512, dem_source.width - col)
                    read_row = max(0, row - 1)
                    read_col = max(0, col - 1)
                    read_end_row = min(dem_source.height, row + height + 1)
                    read_end_col = min(dem_source.width, col + width + 1)
                    read_window = Window(
                        read_col,
                        read_row,
                        read_end_col - read_col,
                        read_end_row - read_row,
                    )
                    dem = dem_source.read(1, window=read_window, masked=True).filled(np.nan)
                    slope = compute_slope_riserun(
                        dem.astype(np.float32),
                        abs(dem_source.transform.a),
                        abs(dem_source.transform.e),
                    )
                    core_row = row - read_row
                    core_col = col - read_col
                    core_slope = slope[core_row : core_row + height, core_col : core_col + width]
                    destination.write(
                        np.where(np.isfinite(core_slope), core_slope, -9999.0).astype(np.float32),
                        1,
                        window=Window(col, row, width, height),
                    )
    return slope_path


def extract_snap_bands(
    source_path: Path,
    backscatter_path: Path,
    dem_path: Path,
) -> tuple[Path, Path]:
    """Extract SNAP Sigma0_VV/VH and elevation bands without loading a scene into memory."""
    with rasterio.open(source_path) as source:
        band_names = [name or "" for name in source.descriptions]

        def find_band(suffix: str) -> int:
            matches = [
                index
                for index, name in enumerate(band_names, start=1)
                if name.casefold().replace("-", "_").endswith(suffix.casefold())
            ]
            if len(matches) != 1:
                raise ValleyeyeError(
                    ErrorCode.PREPROCESSING_FAILED,
                    f"SNAP output is missing one unambiguous {suffix} band.",
                    stage="PREPROCESSING",
                    details={"band_names": band_names},
                )
            return matches[0]

        vv_index = find_band("sigma0_vv")
        vh_index = find_band("sigma0_vh")
        elevation_index = find_band("elevation")
        profile = source.profile.copy()
        profile.update(driver="GTiff", count=2, compress="deflate")
        backscatter_path.parent.mkdir(parents=True, exist_ok=True)
        dem_path.parent.mkdir(parents=True, exist_ok=True)
        with (
            rasterio.open(backscatter_path, "w", **profile) as backscatter,
            rasterio.open(
                dem_path,
                "w",
                **{**profile, "count": 1, "dtype": source.dtypes[elevation_index - 1]},
            ) as dem,
        ):
            backscatter.set_band_description(1, "Sigma0_VV")
            backscatter.set_band_description(2, "Sigma0_VH")
            dem.set_band_description(1, "elevation")
            for row in range(0, source.height, 512):
                for col in range(0, source.width, 512):
                    window = Window(
                        col,
                        row,
                        min(512, source.width - col),
                        min(512, source.height - row),
                    )
                    backscatter.write(source.read(vv_index, window=window), 1, window=window)
                    backscatter.write(source.read(vh_index, window=window), 2, window=window)
                    dem.write(source.read(elevation_index, window=window), 1, window=window)
    return backscatter_path, dem_path


def read_window(
    dataset: Any, indexes: tuple[int, ...], window: Window
) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.bool_]]:
    values = dataset.read(indexes, window=window).astype(np.float32, copy=False)
    valid = np.all(dataset.read_masks(indexes, window=window) > 0, axis=0)
    valid &= np.isfinite(values).all(axis=0)
    if dataset.nodata is not None:
        valid &= np.all(values != dataset.nodata, axis=0)
    return values, valid
