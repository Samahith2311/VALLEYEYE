from __future__ import annotations

import json
import sys
from hashlib import sha256
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evaluation.compare import compare_frozen_run
from evaluation.freeze import freeze_run


def _write_mask(path: Path, values: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=values.shape[1],
        height=values.shape[0],
        count=1,
        dtype="uint8",
        crs="EPSG:32631",
        transform=from_origin(0, 4, 1, 1),
        nodata=255,
    ) as output:
        output.write(values.astype(np.uint8), 1)


def _make_job(root: Path, mask: np.ndarray) -> Path:
    job_dir = root / "production-jobs" / "job-1"
    raster_path = job_dir / "stages" / "INFERENCE" / "mask.tif"
    _write_mask(raster_path, mask)
    digest = sha256(raster_path.read_bytes()).hexdigest()
    (job_dir / "job.json").write_text(
        json.dumps({"job_id": "job-1", "status": "SUCCEEDED"}), encoding="utf-8"
    )
    (job_dir / "run_manifest.json").write_text(
        json.dumps(
            {
                "artifacts": {
                    "stages/INFERENCE/mask.tif": {
                        "sha256": digest,
                        "media_type": "image/tiff",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return job_dir


def test_freeze_then_compare_keeps_production_and_frozen_outputs_unchanged(tmp_path: Path) -> None:
    mask = np.array([[1, 1, 0, 0], [1, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 0]])
    job_dir = _make_job(tmp_path, mask)
    config_dir = tmp_path / "production-config"
    cache_dir = tmp_path / "production-cache"
    config_dir.mkdir()
    cache_dir.mkdir()
    (config_dir / "settings.json").write_text('{"threshold":0.5}', encoding="utf-8")
    (cache_dir / "entry.bin").write_bytes(b"cache")
    config_before = (config_dir / "settings.json").read_bytes()
    cache_before = (cache_dir / "entry.bin").read_bytes()

    frozen = freeze_run(job_dir, tmp_path / "evaluation" / "frozen")
    frozen_mask = frozen / "stages" / "INFERENCE" / "mask.tif"
    frozen_hash_before = sha256(frozen_mask.read_bytes()).hexdigest()
    assert not frozen_mask.stat().st_mode & 0o222

    reference = tmp_path / "reference.geojson"
    reference.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": "EPSG:32631"}},
                "features": [
                    {
                        "type": "Feature",
                        "properties": {},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[0, 4], [2, 4], [2, 2], [0, 2], [0, 4]]],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    report_path = tmp_path / "evaluation" / "metrics.json"
    result = compare_frozen_run(frozen, reference, report_path)

    assert result["metrics"] == {"iou": 0.6, "precision": 0.75, "recall": 0.75, "f1": 0.75}
    assert result["confusion_matrix_pixels"] == {
        "true_positive": 3,
        "false_positive": 1,
        "false_negative": 1,
        "true_negative": 11,
    }
    assert sha256(frozen_mask.read_bytes()).hexdigest() == frozen_hash_before
    assert not report_path.stat().st_mode & 0o222
    assert (config_dir / "settings.json").read_bytes() == config_before
    assert (cache_dir / "entry.bin").read_bytes() == cache_before
    assert (job_dir / "stages" / "INFERENCE" / "mask.tif").read_bytes() == frozen_mask.read_bytes()


def test_freeze_rejects_incomplete_or_tampered_production_artifact(tmp_path: Path) -> None:
    job_dir = _make_job(tmp_path, np.array([[0, 1], [1, 0]]))
    artifact = job_dir / "stages" / "INFERENCE" / "mask.tif"
    artifact.write_bytes(artifact.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        freeze_run(job_dir, tmp_path / "evaluation" / "frozen")


def test_compare_rejects_modified_frozen_artifact(tmp_path: Path) -> None:
    job_dir = _make_job(tmp_path, np.array([[0, 1], [1, 0]]))
    frozen = freeze_run(job_dir, tmp_path / "evaluation" / "frozen")
    frozen_mask = frozen / "stages" / "INFERENCE" / "mask.tif"
    frozen_mask.chmod(0o600)
    frozen_mask.write_bytes(frozen_mask.read_bytes() + b"changed")
    reference = tmp_path / "truth.tif"
    _write_mask(reference, np.array([[0, 1], [1, 0]]))

    with pytest.raises(ValueError, match="hash mismatch"):
        compare_frozen_run(frozen, reference, tmp_path / "evaluation" / "metrics.json")


def test_compare_accepts_a_local_raster_reference(tmp_path: Path) -> None:
    values = np.array([[0, 1], [1, 0]])
    job_dir = _make_job(tmp_path, values)
    frozen = freeze_run(job_dir, tmp_path / "evaluation" / "frozen")
    reference = tmp_path / "reference.tif"
    _write_mask(reference, values)

    result = compare_frozen_run(frozen, reference, tmp_path / "evaluation" / "metrics.json")

    assert result["metrics"] == {"iou": 1.0, "precision": 1.0, "recall": 1.0, "f1": 1.0}


def test_freeze_destination_cannot_be_inside_production_job(tmp_path: Path) -> None:
    job_dir = _make_job(tmp_path, np.array([[0, 1], [1, 0]]))
    with pytest.raises(ValueError, match="outside"):
        freeze_run(job_dir, job_dir / "evaluation")
