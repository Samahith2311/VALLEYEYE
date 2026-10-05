from __future__ import annotations

from typing import Protocol

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict


class FloodModelMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    version: str
    weights_sha256: str
    source_url: str
    verified_on: str
    input_order: tuple[str, str]
    channels_per_time: tuple[str, str, str]
    input_mean: tuple[float, float]
    input_std: tuple[float, float]
    slope_mean: float
    slope_std: float
    training_patch_size: int
    output_classes: tuple[str, str, str]


class FloodModel(Protocol):
    @property
    def metadata(self) -> FloodModelMetadata: ...

    def predict_tile(
        self, pre_event: npt.NDArray[np.float32], post_event: npt.NDArray[np.float32]
    ) -> npt.NDArray[np.float32]:
        """Return class-2 flood probabilities for two (3, H, W) normalized inputs."""
