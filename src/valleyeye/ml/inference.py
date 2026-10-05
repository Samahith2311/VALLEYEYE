from __future__ import annotations

from pathlib import Path
from typing import Literal

import numpy as np
import numpy.typing as npt
import rasterio
from pydantic import BaseModel, ConfigDict, Field
from rasterio.windows import Window

from valleyeye.core.errors import ErrorCode, ValleyeyeError
from valleyeye.ml.model import FloodModel
from valleyeye.sar.raster import assert_aligned

NODATA_PROBABILITY = -9999.0
NODATA_MASK = 255


class InferenceConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    tile_size: int = Field(default=224, ge=32)
    overlap: int = Field(default=32, ge=0)
    flood_probability_threshold: float = Field(default=0.5, gt=0, le=1)

    @property
    def core_size(self) -> int:
        if self.overlap * 2 >= self.tile_size:
            raise ValueError("Overlap must be less than half the tile size")
        if self.tile_size % 16:
            raise ValueError("SNUNet tile size must be divisible by 16")
        return self.tile_size - 2 * self.overlap


class InferenceSummary(BaseModel):
    valid_pixel_fraction: float
    valid_pixels: int
    flooded_pixels: int
    flooded_area_km2: float
    model_sha256: str


def _read_context(
    source: rasterio.io.DatasetReader,
    indexes: tuple[int, ...],
    row_start: int,
    col_start: int,
    size: int,
) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.bool_]]:
    row_end = row_start + size
    col_end = col_start + size
    row0 = max(0, row_start)
    col0 = max(0, col_start)
    row1 = min(source.height, row_end)
    col1 = min(source.width, col_end)
    if row0 >= row1 or col0 >= col1:
        raise ValueError("Context window does not overlap its source raster")

    window = Window(col0, row0, col1 - col0, row1 - row0)
    values = source.read(indexes, window=window).astype(np.float32, copy=False)
    valid = np.all(source.read_masks(indexes, window=window) > 0, axis=0)
    valid &= np.isfinite(values).all(axis=0)
    if source.nodata is not None:
        valid &= np.all(values != source.nodata, axis=0)

    top = row0 - row_start
    left = col0 - col_start
    bottom = int(size - top - values.shape[1])
    right = int(size - left - values.shape[2])
    top = int(top)
    left = int(left)
    pad_mode: Literal["reflect", "edge"] = "reflect" if min(values.shape[1:]) > 1 else "edge"
    pad_width: tuple[tuple[int, int], ...] = ((0, 0), (top, bottom), (left, right))
    values = np.pad(values, pad_width, mode=pad_mode)
    valid = np.pad(
        valid,
        ((top, bottom), (left, right)),
        mode="constant",
        constant_values=False,
    )
    return values, valid


def _model_inputs(
    pre: npt.NDArray[np.float32],
    post: npt.NDArray[np.float32],
    slope: npt.NDArray[np.float32],
    valid: npt.NDArray[np.bool_],
    model: FloodModel,
) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.float32]]:
    metadata = model.metadata
    means = np.asarray(metadata.input_mean, dtype=np.float32)[:, None, None]
    stds = np.asarray(metadata.input_std, dtype=np.float32)[:, None, None]
    slope_mean = np.float32(metadata.slope_mean)
    slope_std = np.float32(metadata.slope_std)

    pre = np.clip(pre, 0.0, 0.15)
    post = np.clip(post, 0.0, 0.15)
    pre = np.where(valid[None, :, :], pre, means)
    post = np.where(valid[None, :, :], post, means)
    slope = np.where(valid[None, :, :], slope, slope_mean)
    pre_normalized = (pre - means) / stds
    post_normalized = (post - means) / stds
    slope_normalized = (slope - slope_mean) / slope_std
    pre_stack = np.concatenate((pre_normalized, slope_normalized), axis=0)
    post_stack = np.concatenate((post_normalized, slope_normalized), axis=0)
    return pre_stack.astype(np.float32, copy=False), post_stack.astype(np.float32, copy=False)


def infer_rasters(
    pre_event_path: Path,
    post_event_path: Path,
    slope_path: Path,
    probability_path: Path,
    mask_path: Path,
    model: FloodModel,
    config: InferenceConfig | None = None,
) -> InferenceSummary:
    config = config or InferenceConfig()
    core_size = config.core_size
    probability_path.parent.mkdir(parents=True, exist_ok=True)
    mask_path.parent.mkdir(parents=True, exist_ok=True)

    with (
        rasterio.open(pre_event_path) as pre_source,
        rasterio.open(post_event_path) as post_source,
        rasterio.open(slope_path) as slope_source,
    ):
        assert_aligned(pre_source, post_source, slope_source)
        if pre_source.count < 2 or post_source.count < 2 or slope_source.count != 1:
            raise ValleyeyeError(
                ErrorCode.PREPROCESSING_FAILED,
                "SNUNet requires aligned VV/VH pre/post rasters and one slope-riserun band.",
                stage="PREPROCESSING",
            )
        if (
            pre_source.crs is None
            or not pre_source.crs.is_projected
            or pre_source.crs.linear_units_factor[1] != 1.0
        ):
            raise ValleyeyeError(
                ErrorCode.PREPROCESSING_FAILED,
                "Inference rasters must use a projected metric CRS.",
                stage="PREPROCESSING",
            )

        profile = pre_source.profile.copy()
        profile.update(
            driver="GTiff",
            count=1,
            dtype="float32",
            nodata=NODATA_PROBABILITY,
            compress="deflate",
            predictor=3,
        )
        mask_profile = profile.copy()
        mask_profile.update(dtype="uint8", nodata=NODATA_MASK, predictor=2)
        total_pixels = pre_source.width * pre_source.height
        valid_pixels = 0
        flooded_pixels = 0
        pixel_area_m2 = abs(pre_source.transform.a * pre_source.transform.e)

        with (
            rasterio.open(probability_path, "w", **profile) as probability_dst,
            rasterio.open(mask_path, "w", **mask_profile) as mask_dst,
        ):
            for row in range(0, pre_source.height, core_size):
                for col in range(0, pre_source.width, core_size):
                    pre, pre_valid = _read_context(
                        pre_source,
                        (1, 2),
                        row - config.overlap,
                        col - config.overlap,
                        config.tile_size,
                    )
                    post, post_valid = _read_context(
                        post_source,
                        (1, 2),
                        row - config.overlap,
                        col - config.overlap,
                        config.tile_size,
                    )
                    slope, slope_valid = _read_context(
                        slope_source,
                        (1,),
                        row - config.overlap,
                        col - config.overlap,
                        config.tile_size,
                    )
                    tile_valid = pre_valid & post_valid & slope_valid
                    pre_input, post_input = _model_inputs(pre, post, slope, tile_valid, model)
                    probabilities = model.predict_tile(pre_input, post_input)
                    if probabilities.shape != (config.tile_size, config.tile_size):
                        raise ValleyeyeError(
                            ErrorCode.INFERENCE_FAILED,
                            "Flood model returned a probability tile with an unexpected shape.",
                            stage="INFERENCE",
                            details={"shape": list(probabilities.shape)},
                        )

                    height = min(core_size, pre_source.height - row)
                    width = min(core_size, pre_source.width - col)
                    core = probabilities[
                        config.overlap : config.overlap + height,
                        config.overlap : config.overlap + width,
                    ]
                    core_valid = tile_valid[
                        config.overlap : config.overlap + height,
                        config.overlap : config.overlap + width,
                    ]
                    if not np.isfinite(core).all() or np.any((core < 0) | (core > 1)):
                        raise ValleyeyeError(
                            ErrorCode.INFERENCE_FAILED,
                            "Flood model returned invalid probabilities outside [0, 1].",
                            stage="INFERENCE",
                        )
                    probability_block = np.where(core_valid, core, NODATA_PROBABILITY).astype(
                        np.float32
                    )
                    mask_block = np.where(
                        core_valid,
                        (core >= config.flood_probability_threshold).astype(np.uint8),
                        NODATA_MASK,
                    ).astype(np.uint8)
                    window = Window(col, row, width, height)
                    probability_dst.write(probability_block, 1, window=window)
                    mask_dst.write(mask_block, 1, window=window)
                    valid_pixels += int(core_valid.sum())
                    flooded_pixels += int(
                        np.count_nonzero(core_valid & (core >= config.flood_probability_threshold))
                    )

            tags = {
                "model": model.metadata.name,
                "model_weights_sha256": model.metadata.weights_sha256,
                "class_probability": "flood (class 2)",
                "threshold": str(config.flood_probability_threshold),
                "input_preprocessing": (
                    "clamp VV/VH to [0, 0.15], standardize VV/VH and slope_riserun"
                ),
                "tile_size": str(config.tile_size),
                "overlap": str(config.overlap),
            }
            probability_dst.update_tags(**tags)
            mask_dst.update_tags(**tags)

    return InferenceSummary(
        valid_pixel_fraction=valid_pixels / total_pixels,
        valid_pixels=valid_pixels,
        flooded_pixels=flooded_pixels,
        flooded_area_km2=flooded_pixels * pixel_area_m2 / 1_000_000,
        model_sha256=model.metadata.weights_sha256,
    )
