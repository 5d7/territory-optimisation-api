"""Idempotent submission of background territory runs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from .config import AppConfig
from .jobs import JobLauncher
from .models import RunAccepted, RunKind, RunRecord, RunStatus
from .state import StateStore


class RunCoordinator:
    def __init__(self, config: AppConfig, state: StateStore, launcher: JobLauncher) -> None:
        self.config = config
        self.state = state
        self.launcher = launcher

    def submit(
        self,
        *,
        request_id: str,
        trigger: str,
        force: bool,
        kind: RunKind = RunKind.OPTIMISE,
    ) -> RunAccepted:
        run_id = uuid.uuid4().hex
        record, deduplicated = self.state.submit(
            run_id=run_id,
            request_id=request_id,
            kind=kind,
            trigger=trigger,
            force=force,
            engine_version=self.config.engine_version,
            lease_seconds=self.config.lease_seconds,
        )
        if not deduplicated:
            try:
                execution_name = self.launcher.launch(record)
                record = self.state.update(record.run_id, execution_name=execution_name)
            except Exception as exc:
                record = self.state.update(
                    record.run_id,
                    status=RunStatus.FAILED,
                    completed_at=datetime.now(UTC),
                    error_code="job_launch_failed",
                    error_summary=str(exc)[:300],
                )
                self.state.release(record.run_id)
                raise
        return RunAccepted(
            run_id=record.run_id,
            status=record.status,
            deduplicated=deduplicated,
            status_url=f"/v1/runs/{record.run_id}",
        )

    def get(self, run_id: str) -> RunRecord | None:
        return self.state.get(run_id)
