from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from valleyeye.api.schemas import AnalysisRequest
from valleyeye.core.errors import ErrorCode, ValleyeyeError
from valleyeye.pipeline.models import JobError, JobRecord


class JobStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def create(self, request: AnalysisRequest) -> tuple[JobRecord, Path]:
        job_id = str(uuid4())
        job_dir = self._job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=False)
        record = JobRecord(job_id=job_id, created_at=datetime.now(UTC))
        (job_dir / "request.json").write_text(
            request.model_dump_json(indent=2),
            encoding="utf-8",
        )
        self.save(record)
        return record, job_dir

    def get(self, job_id: str) -> JobRecord:
        path = self._job_dir(job_id) / "job.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValleyeyeError(
                ErrorCode.JOB_NOT_FOUND,
                "Analysis job was not found.",
                stage="RESULTS",
            ) from exc
        return JobRecord.model_validate(data)

    def request(self, job_id: str) -> AnalysisRequest:
        path = self._job_dir(job_id) / "request.json"
        try:
            return AnalysisRequest.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValleyeyeError(
                ErrorCode.JOB_NOT_FOUND,
                "Analysis job was not found.",
                stage="RESULTS",
            ) from exc

    def save(self, record: JobRecord) -> None:
        path = self._job_dir(record.job_id) / "job.json"
        temporary = path.with_name("job.json.part")
        serialized = record.model_dump_json(indent=2)
        with self._lock:
            temporary.write_text(serialized, encoding="utf-8")
            temporary.replace(path)

    def job_dir(self, job_id: str) -> Path:
        return self._job_dir(job_id)

    def pending_count(self) -> int:
        count = 0
        for path in self.root.glob("*/job.json"):
            try:
                record = JobRecord.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            count += record.status in {"QUEUED", "RUNNING"}
        return count

    def recover_interrupted(self) -> None:
        for path in self.root.glob("*/job.json"):
            try:
                record = JobRecord.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if record.status in {"QUEUED", "RUNNING"}:
                active_stage = next(
                    (stage for stage in record.stages if stage.status == "RUNNING"), None
                )
                error_stage = (
                    active_stage.name
                    if active_stage is not None
                    else record.current_stage or "DISCOVERY"
                )
                record.status = "FAILED"
                record.current_stage = None
                record.ended_at = datetime.now(UTC)
                record.error = JobError(
                    code=ErrorCode.JOB_CANCELLED.value,
                    message="The server restarted before this analysis job completed.",
                    stage=error_stage,
                )
                if active_stage is not None:
                    active_stage.status = "FAILED"
                    active_stage.ended_at = record.ended_at
                    if active_stage.started_at is not None:
                        active_stage.duration_ms = int(
                            (active_stage.ended_at - active_stage.started_at).total_seconds() * 1000
                        )
                self.save(record)

    def _job_dir(self, job_id: str) -> Path:
        try:
            normalized = str(UUID(job_id))
        except ValueError as exc:
            raise ValleyeyeError(
                ErrorCode.JOB_NOT_FOUND,
                "Analysis job was not found.",
                stage="RESULTS",
            ) from exc
        return self.root / normalized
