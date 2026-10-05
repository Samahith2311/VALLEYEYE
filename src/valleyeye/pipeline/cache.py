from __future__ import annotations

import json
import shutil
import subprocess
import threading
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from valleyeye.pipeline.models import StageOutcome


class StageCache:
    def __init__(self, root: Path, ttl_seconds: int) -> None:
        self.root = root
        self.ttl_seconds = ttl_seconds
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.implementation_fingerprint = self._implementation_fingerprint()

    @staticmethod
    def _implementation_fingerprint() -> str:
        source_root = Path(__file__).resolve().parents[1]
        digest = sha256()
        for path in sorted(source_root.rglob("*.py")):
            digest.update(path.relative_to(source_root).as_posix().encode("utf-8"))
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
        try:
            revision = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
                cwd=source_root,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            revision = ""
        digest.update(revision.encode("utf-8"))
        return digest.hexdigest()

    @staticmethod
    def key(
        stage: str,
        request: dict[str, Any],
        config: dict[str, object],
        data: dict[str, Any],
        implementation_fingerprint: str = "",
    ) -> str:
        payload = json.dumps(
            {
                "stage": stage,
                "request": request,
                "config": config,
                "input": data,
                "implementation": implementation_fingerprint,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    def load(self, key: str, destination: Path) -> StageOutcome | None:
        if self.ttl_seconds == 0:
            return None
        entry = self.root / key
        try:
            metadata = json.loads((entry / "cache.json").read_text(encoding="utf-8"))
            created = datetime.fromisoformat(metadata["created_at"])
            if (datetime.now(UTC) - created).total_seconds() > self.ttl_seconds:
                with self._lock:
                    shutil.rmtree(entry, ignore_errors=True)
                return None
            outcome = StageOutcome.model_validate(metadata["outcome"])
            source = entry / "files"
            if source.exists():
                shutil.copytree(source, destination, dirs_exist_ok=True)
            return outcome
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, key: str, stage_dir: Path, outcome: StageOutcome) -> None:
        if self.ttl_seconds == 0:
            return
        entry = self.root / key
        temporary = self.root / f"{key}.part"
        with self._lock:
            shutil.rmtree(temporary, ignore_errors=True)
            (temporary / "files").mkdir(parents=True)
            shutil.copytree(stage_dir, temporary / "files", dirs_exist_ok=True)
            (temporary / "cache.json").write_text(
                json.dumps(
                    {
                        "created_at": datetime.now(UTC).isoformat(),
                        "outcome": outcome.model_dump(mode="json"),
                    },
                    sort_keys=True,
                    indent=2,
                    allow_nan=False,
                ),
                encoding="utf-8",
            )
            if entry.exists():
                shutil.rmtree(entry)
            temporary.replace(entry)
