"""Public API and internal run models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SKIPPED = "skipped"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RunKind(StrEnum):
    OPTIMISE = "optimise"
    NEXT_CYCLE = "next_cycle"


class RunRequest(BaseModel):
    force: bool = False


class RunRecord(BaseModel):
    run_id: str
    kind: RunKind = RunKind.OPTIMISE
    trigger: str
    force: bool = False
    status: RunStatus = RunStatus.QUEUED
    queued_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    input_hash: str | None = None
    engine_version: str
    execution_name: str | None = None
    solver_statuses: dict[str, str] = Field(default_factory=dict)
    kpis: dict[str, int | float | str] = Field(default_factory=dict)
    error_code: str | None = None
    error_summary: str | None = None


class RunAccepted(BaseModel):
    run_id: str
    status: RunStatus
    deduplicated: bool
    status_url: str


class LatestRuns(BaseModel):
    latest_check: RunRecord | None
    latest_success: RunRecord | None


class SheetInputs(BaseModel):
    accounts: list[list[Any]]
    ae_roles: list[list[Any]]
    settings: list[list[Any]]

    model_config = {"arbitrary_types_allowed": True}
