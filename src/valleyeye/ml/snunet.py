from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from valleyeye.core.errors import ErrorCode, ValleyeyeError
from valleyeye.ml.model import FloodModelMetadata

WEIGHTS_URL = (
    "https://www.dropbox.com/scl/fi/3vlsveoobqe1wc71s5z2d/"
    "best_segmentation.pt?rlkey=xpy2thmozzxfzymr8b13m7n51&dl=1"
)
WEIGHTS_SHA256 = "6c6bcb78d956f9d139743eb5bc44cf8e14528b643f9cbf5f4478f4e321a609ed"
WEIGHTS_SIZE_BYTES = 144_657_509
MODEL_METADATA = FloodModelMetadata(
    name="Kuro Siwo SNUNet-ECAM",
    version="README-linked Dropbox checkpoint (unversioned artifact)",
    weights_sha256=WEIGHTS_SHA256,
    source_url="https://github.com/Orion-AI-Lab/KuroSiwo",
    verified_on="2026-10-05",
    input_order=("pre_event", "post_event"),
    channels_per_time=("vv_sigma0_linear", "vh_sigma0_linear", "slope_riserun"),
    input_mean=(0.0953, 0.0264),
    input_std=(0.0427, 0.0215),
    slope_mean=2.9482,
    slope_std=79.2493,
    training_patch_size=224,
    output_classes=("no_water", "permanent_water", "flood"),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SNUNetFloodModel:
    def __init__(self, network: Any, torch_module: Any, device: str) -> None:
        self._network = network
        self._torch = torch_module
        self._device = device
        self._metadata = MODEL_METADATA

    @property
    def metadata(self) -> FloodModelMetadata:
        return self._metadata

    @classmethod
    def load(cls, weights_path: Path, device: str = "auto") -> SNUNetFloodModel:
        if not weights_path.is_file():
            raise ValleyeyeError(
                ErrorCode.MODEL_UNAVAILABLE,
                "Verified Kuro Siwo SNUNet weights are missing; run the fetch-weights command.",
                stage="INFERENCE",
                details={"weights_path": str(weights_path)},
            )
        actual_hash = sha256_file(weights_path)
        if actual_hash != WEIGHTS_SHA256:
            raise ValleyeyeError(
                ErrorCode.MODEL_UNAVAILABLE,
                "Kuro Siwo SNUNet weights failed SHA-256 verification.",
                stage="INFERENCE",
                details={"expected_sha256": WEIGHTS_SHA256, "actual_sha256": actual_hash},
            )

        try:
            import torch

            from valleyeye.ml.architecture import SNUNetECAM
        except ImportError as exc:
            raise ValleyeyeError(
                ErrorCode.MODEL_UNAVAILABLE,
                "Install the ML extra to load the Kuro Siwo SNUNet model.",
                stage="INFERENCE",
            ) from exc

        selected_device = "cuda" if device == "auto" and torch.cuda.is_available() else device
        if selected_device == "auto":
            selected_device = "cpu"
        if selected_device.startswith("cuda") and not torch.cuda.is_available():
            raise ValleyeyeError(
                ErrorCode.MODEL_UNAVAILABLE,
                "CUDA was requested but is not available.",
                stage="INFERENCE",
            )

        try:
            checkpoint = torch.load(weights_path, map_location="cpu", weights_only=True)
            state_dict = checkpoint["model_state_dict"]
            network = SNUNetECAM(in_channels=3, out_channels=3, base_channel=32)
            network.load_state_dict(state_dict, strict=True)
            network.eval()
            network.to(selected_device)
        except (KeyError, RuntimeError, OSError, ValueError) as exc:
            raise ValleyeyeError(
                ErrorCode.MODEL_UNAVAILABLE,
                "Checkpoint does not match the verified SNUNet-ECAM architecture.",
                stage="INFERENCE",
            ) from exc
        return cls(network, torch, selected_device)

    def predict_tile(
        self, pre_event: npt.NDArray[np.float32], post_event: npt.NDArray[np.float32]
    ) -> npt.NDArray[np.float32]:
        if pre_event.shape != post_event.shape or pre_event.ndim != 3 or pre_event.shape[0] != 3:
            raise ValleyeyeError(
                ErrorCode.INFERENCE_FAILED,
                "SNUNet inputs must be matching (3, height, width) arrays.",
                stage="INFERENCE",
            )
        if pre_event.shape[1] % 16 or pre_event.shape[2] % 16:
            raise ValleyeyeError(
                ErrorCode.INFERENCE_FAILED,
                "SNUNet tile height and width must be divisible by 16.",
                stage="INFERENCE",
                details={"shape": list(pre_event.shape)},
            )
        if not np.isfinite(pre_event).all() or not np.isfinite(post_event).all():
            raise ValleyeyeError(
                ErrorCode.INFERENCE_FAILED,
                "SNUNet inputs must not contain NaN or infinite values.",
                stage="INFERENCE",
            )

        torch = self._torch
        pre = torch.from_numpy(np.ascontiguousarray(pre_event)).unsqueeze(0).to(self._device)
        post = torch.from_numpy(np.ascontiguousarray(post_event)).unsqueeze(0).to(self._device)
        with torch.inference_mode():
            probabilities = torch.softmax(self._network(pre, post), dim=1)[0, 2]
        return np.asarray(probabilities.float().cpu().numpy(), dtype=np.float32)
