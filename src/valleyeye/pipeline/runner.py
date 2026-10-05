from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any

from valleyeye.api.schemas import AnalysisRequest
from valleyeye.core.errors import ErrorCode, ValleyeyeError
from valleyeye.core.settings import Settings
from valleyeye.pipeline.cache import StageCache
from valleyeye.pipeline.models import (
    PIPELINE_STAGES,
    JobError,
    JobRecord,
    StageOutcome,
    StageRecord,
)
from valleyeye.pipeline.results import write_results
from valleyeye.pipeline.store import JobStore


class StageContext:
    def __init__(
        self,
        settings: Settings,
        request: AnalysisRequest,
        record: JobRecord,
        job_dir: Path,
        stage_dir: Path,
        data: dict[str, dict[str, Any]],
        progress: Callable[[int], Awaitable[None]],
        cancel_event: Event,
    ) -> None:
        self.settings = settings
        self.request = request
        self.record = record
        self.job_dir = job_dir
        self.stage_dir = stage_dir
        self.data = data
        self.progress = progress
        self.cancel_event = cancel_event


StageHandler = Callable[[StageContext], StageOutcome | Awaitable[StageOutcome]]


class JobManager:
    def __init__(
        self,
        settings: Settings,
        handlers: dict[str, StageHandler],
        store: JobStore | None = None,
        cache: StageCache | None = None,
    ) -> None:
        self.settings = settings
        self.handlers = handlers
        self.store = store or JobStore(settings.job_data_dir)
        self.cache = cache or StageCache(settings.stage_cache_dir, settings.stage_cache_ttl_seconds)
        self.store.recover_interrupted()
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def submit(self, request: AnalysisRequest, no_cache: bool = False) -> JobRecord:
        if self.store.pending_count() >= self.settings.max_queued_jobs:
            raise ValleyeyeError(
                ErrorCode.JOB_QUEUE_FULL,
                "The analysis queue is full; retry after a job finishes.",
                stage="DISCOVERY",
                retryable=True,
            )
        record, _ = self.store.create(request)
        record.no_cache = no_cache
        record.stages = [StageRecord(name=name) for name in PIPELINE_STAGES]
        self.store.save(record)
        task = asyncio.create_task(self._run(record.job_id))
        self._tasks[record.job_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(record.job_id, None))
        return record

    async def cancel(self, job_id: str) -> JobRecord:
        record = self.store.get(job_id)
        task = self._tasks.get(record.job_id)
        if task is None or task.done():
            return record
        if record.status == "QUEUED":
            record.status = "FAILED"
            record.ended_at = datetime.now(UTC)
            record.error = JobError(
                code=ErrorCode.JOB_CANCELLED.value,
                message="The analysis job was cancelled before processing started.",
                stage="DISCOVERY",
            )
            self.store.save(record)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return self.store.get(job_id)

    async def shutdown(self) -> None:
        tasks = tuple(task for task in self._tasks.values() if not task.done())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, job_id: str) -> None:
        async with self._semaphore:
            record = self.store.get(job_id)
            request = self.store.request(job_id)
            job_dir = self.store.job_dir(job_id)
            record.status = "RUNNING"
            record.started_at = datetime.now(UTC)
            self.store.save(record)
            data: dict[str, dict[str, Any]] = {}
            partial = False
            cancel_event = Event()
            try:
                for index, stage_name in enumerate(PIPELINE_STAGES):
                    record.current_stage = stage_name
                    stage_record = record.stages[index]
                    stage_record.status = "RUNNING"
                    stage_record.started_at = datetime.now(UTC)
                    stage_record.cache = "bypass" if record.no_cache else "miss"
                    self.store.save(record)
                    stage_dir = job_dir / "stages" / stage_name.lower()
                    stage_dir.mkdir(parents=True, exist_ok=True)

                    async def update_progress(
                        value: int,
                        *,
                        _stage: StageRecord = stage_record,
                        _index: int = index,
                    ) -> None:
                        _stage.progress = max(0, min(99, value))
                        record.progress = min(
                            99,
                            int((_index + _stage.progress / 100) * 100 / len(PIPELINE_STAGES)),
                        )
                        self.store.save(record)

                    context = StageContext(
                        self.settings,
                        request,
                        record,
                        job_dir,
                        stage_dir,
                        data,
                        update_progress,
                        cancel_event,
                    )
                    handler = self.handlers.get(stage_name)
                    if handler is None:
                        raise ValleyeyeError(
                            ErrorCode.INTERNAL_ERROR,
                            f"No handler is configured for pipeline stage {stage_name}.",
                            stage=stage_name,
                        )
                    key = StageCache.key(
                        stage_name,
                        request.model_dump(mode="json"),
                        self.settings.public_config(),
                        data,
                        self.cache.implementation_fingerprint,
                    )
                    outcome = None if record.no_cache else self.cache.load(key, stage_dir)
                    if outcome is not None:
                        stage_record.cache = "hit"
                    else:
                        result = handler(context)
                        outcome = await result if inspect.isawaitable(result) else result
                        if not isinstance(outcome, StageOutcome):
                            outcome = StageOutcome.model_validate(outcome)
                        if not record.no_cache:
                            self.cache.save(key, stage_dir, outcome)
                    data[stage_name] = outcome.data
                    partial |= outcome.partial
                    for warning in outcome.warnings:
                        if warning not in record.warnings:
                            record.warnings.append(warning)
                        if warning not in stage_record.warnings:
                            stage_record.warnings.append(warning)
                    stage_record.status = "SUCCEEDED"
                    stage_record.progress = 100
                    stage_record.ended_at = datetime.now(UTC)
                    stage_record.duration_ms = int(
                        (stage_record.ended_at - stage_record.started_at).total_seconds() * 1000
                    )
                    record.progress = int((index + 1) * 100 / len(PIPELINE_STAGES))
                    self.store.save(record)

                record.status = "PARTIAL" if partial else "SUCCEEDED"
                record.current_stage = "RESULTS"
                record.ended_at = datetime.now(UTC)
                record.artifacts = await asyncio.to_thread(
                    write_results,
                    job_dir,
                    record,
                    request,
                    data,
                    self.settings.public_config(),
                )
                record.current_stage = None
                self.store.save(record)
            except asyncio.CancelledError:
                cancel_event.set()
                cancelled_stage = record.current_stage or next(
                    (stage.name for stage in record.stages if stage.status == "RUNNING"),
                    "DISCOVERY",
                )
                record.status = "FAILED"
                record.current_stage = None
                record.ended_at = datetime.now(UTC)
                record.error = JobError(
                    code=ErrorCode.JOB_CANCELLED.value,
                    message="The analysis job was cancelled.",
                    stage=cancelled_stage,
                )
                for stage in record.stages:
                    if stage.name == cancelled_stage and stage.status in {"RUNNING", "SUCCEEDED"}:
                        stage.status = "FAILED"
                        stage.ended_at = datetime.now(UTC)
                self.store.save(record)
                raise
            except ValleyeyeError as exc:
                self._fail(
                    record,
                    exc.code,
                    exc.message,
                    record.current_stage or exc.stage,
                    exc.retryable,
                    exc.details,
                )
            except Exception as exc:
                stage_name = record.current_stage or "RESULTS"
                _ = exc
                self._fail(
                    record,
                    ErrorCode.INTERNAL_ERROR,
                    "An unexpected processing error occurred.",
                    stage_name,
                    False,
                    {},
                )

    def _fail(
        self,
        record: JobRecord,
        code: ErrorCode,
        message: str,
        stage: str,
        retryable: bool,
        details: dict[str, Any],
    ) -> None:
        record.status = "FAILED"
        record.current_stage = None
        record.ended_at = datetime.now(UTC)
        record.error = JobError(
            code=code.value,
            message=message,
            stage=stage,
            retryable=retryable,
            details=details,
        )
        for stage_record in record.stages:
            if stage_record.name == stage and stage_record.status in {"RUNNING", "SUCCEEDED"}:
                stage_record.status = "FAILED"
                stage_record.ended_at = datetime.now(UTC)
                if stage_record.started_at is not None:
                    stage_record.duration_ms = int(
                        (stage_record.ended_at - stage_record.started_at).total_seconds() * 1000
                    )
        self.store.save(record)
