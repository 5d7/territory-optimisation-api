"""Cloud Run Job launchers."""

from __future__ import annotations

from typing import Protocol, cast

from google.cloud import run_v2

from .models import RunRecord


class JobLauncher(Protocol):
    def launch(self, record: RunRecord) -> str: ...


class InMemoryJobLauncher:
    def __init__(self) -> None:
        self.launched: list[RunRecord] = []

    def launch(self, record: RunRecord) -> str:
        self.launched.append(record.model_copy(deep=True))
        return f"local/{record.run_id}"


class CloudRunJobLauncher:
    def __init__(self, job_resource: str) -> None:
        self.job_resource = job_resource
        self.client = run_v2.JobsClient()

    def launch(self, record: RunRecord) -> str:
        request = run_v2.RunJobRequest(
            name=self.job_resource,
            overrides=run_v2.RunJobRequest.Overrides(
                container_overrides=[
                    run_v2.RunJobRequest.Overrides.ContainerOverride(
                        env=[
                            run_v2.EnvVar(name="TERRITORY_RUN_ID", value=record.run_id),
                            run_v2.EnvVar(name="TERRITORY_RUN_KIND", value=record.kind.value),
                        ]
                    )
                ]
            ),
        )
        operation = self.client.run_job(request=request)
        return cast(str, operation.operation.name)
