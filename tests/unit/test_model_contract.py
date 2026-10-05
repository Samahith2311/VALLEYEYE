from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from valleyeye.core.errors import ErrorCode, ValleyeyeError
from valleyeye.ml.snunet import MODEL_METADATA, SNUNetFloodModel


def test_snunet_model_card_records_three_input_channels_and_flood_class() -> None:
    assert MODEL_METADATA.channels_per_time == (
        "vv_sigma0_linear",
        "vh_sigma0_linear",
        "slope_riserun",
    )
    assert MODEL_METADATA.output_classes == ("no_water", "permanent_water", "flood")
    assert MODEL_METADATA.training_patch_size == 224


def test_model_loader_rejects_missing_weights(tmp_path: Path) -> None:
    with pytest.raises(ValleyeyeError) as error:
        SNUNetFloodModel.load(tmp_path / "missing.pt")
    assert error.value.code == ErrorCode.MODEL_UNAVAILABLE


def test_model_loader_rejects_unverified_weights_before_torch_import(tmp_path: Path) -> None:
    bad_weights = tmp_path / "wrong.pt"
    bad_weights.write_bytes(b"not the verified checkpoint")
    with pytest.raises(ValleyeyeError) as error:
        SNUNetFloodModel.load(bad_weights)
    assert error.value.code == ErrorCode.MODEL_UNAVAILABLE
    assert error.value.details["actual_sha256"]


def test_snunet_adapter_rejects_patch_dimensions_not_divisible_by_sixteen() -> None:
    model = SNUNetFloodModel(object(), object(), "cpu")
    invalid_tile = np.zeros((3, 31, 32), dtype=np.float32)
    with pytest.raises(ValleyeyeError) as error:
        model.predict_tile(invalid_tile, invalid_tile)
    assert error.value.code == ErrorCode.INFERENCE_FAILED


def test_snunet_architecture_returns_three_class_logits() -> None:
    torch = pytest.importorskip("torch")
    from valleyeye.ml.architecture import SNUNetECAM

    torch.set_num_threads(1)
    network = SNUNetECAM(in_channels=3, out_channels=3, base_channel=4).eval()
    tile = torch.zeros((1, 3, 32, 32))
    with torch.inference_mode():
        output = network(tile, tile)
    assert output.shape == (1, 3, 32, 32)
