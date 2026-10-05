from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, cast


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def freeze_run(job_dir: Path, destination: Path) -> Path:
    """Verify, copy, hash and make a completed production job immutable for evaluation."""
    source = job_dir.resolve(strict=True)
    target = destination.resolve()
    if not source.is_dir() or _inside(target, source.parent.resolve()) or target == source:
        raise ValueError("The frozen run destination must be outside the production jobs directory")
    if target.exists():
        raise FileExistsError(f"Frozen run destination already exists: {target}")

    job_path = source / "job.json"
    manifest_path = source / "run_manifest.json"
    if job_path.is_symlink() or manifest_path.is_symlink():
        raise ValueError("Production job metadata must be regular files")
    job = json.loads(job_path.read_text(encoding="utf-8"))
    if job.get("status") not in {"SUCCEEDED", "PARTIAL"}:
        raise ValueError("Only completed SUCCEEDED or PARTIAL production jobs can be frozen")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError("Production run manifest has no artifact inventory")

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".valleyeye-freeze-", dir=target.parent) as temp_name:
        staging = Path(temp_name) / "run"
        staging.mkdir()
        frozen_artifacts: dict[str, dict[str, Any]] = {}
        for name, descriptor in sorted(artifacts.items()):
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise ValueError(f"Unsafe artifact path in production manifest: {name}")
            source_path = source.joinpath(*relative.parts)
            if source_path.is_symlink() or not source_path.is_file():
                raise ValueError(f"Manifest artifact is missing or not a regular file: {name}")
            resolved_source = source_path.resolve(strict=True)
            if not _inside(resolved_source, source):
                raise ValueError(f"Manifest artifact escapes the production job directory: {name}")
            expected_hash = descriptor.get("sha256")
            actual_hash = _hash_file(resolved_source)
            if actual_hash != expected_hash:
                raise ValueError(f"Production artifact hash mismatch: {name}")
            frozen_path = staging.joinpath(*relative.parts)
            frozen_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(resolved_source, frozen_path)
            frozen_path.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
            frozen_artifacts[name] = {
                "sha256": actual_hash,
                "size_bytes": frozen_path.stat().st_size,
                "media_type": descriptor.get("media_type"),
            }

        frozen_manifest = {
            "schema_version": "1.0.0",
            "job_id": job.get("job_id"),
            "production_status": job["status"],
            "production_manifest_sha256": _hash_file(manifest_path),
            "artifacts": frozen_artifacts,
        }
        (staging / "frozen_run.json").write_text(
            json.dumps(frozen_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(staging, target)
    return target


def verify_frozen_run(frozen_dir: Path) -> dict[str, Any]:
    root = frozen_dir.resolve(strict=True)
    manifest = json.loads((root / "frozen_run.json").read_text(encoding="utf-8"))
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError("Frozen run has no artifact inventory")
    for name, descriptor in artifacts.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError(f"Unsafe artifact path in frozen manifest: {name}")
        path = root.joinpath(*relative.parts)
        if path.is_symlink() or not path.is_file() or not _inside(path.resolve(strict=True), root):
            raise ValueError(f"Frozen artifact is missing or unsafe: {name}")
        if _hash_file(path) != descriptor.get("sha256"):
            raise ValueError(f"Frozen artifact hash mismatch: {name}")
    return cast(dict[str, Any], manifest)
