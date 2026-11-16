"""FastAPI application for asynchronous territory jobs."""

from __future__ import annotations

import time
from importlib import import_module
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status

from . import __version__
from .auth import AuthenticationError, verify_request
from .config import AppConfig
from .coordinator import RunCoordinator
from .jobs import JobLauncher
from .models import LatestRuns, RunAccepted, RunKind, RunRecord, RunRequest
from .runtime import get_config, get_launcher, get_state
from .state import StateStore


def create_app(
    *,
    config: AppConfig | None = None,
    state_store: StateStore | None = None,
    launcher: JobLauncher | None = None,
) -> FastAPI:
    settings = config or get_config()
    state = state_store or get_state()
    jobs = launcher or get_launcher()
    coordinator = RunCoordinator(settings, state, jobs)

    app = FastAPI(
        title="Territory Optimisation API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
    )
    app.state.config = settings
    app.state.state_store = state
    app.state.coordinator = coordinator

    async def signed_request(
        request: Request,
        x_timestamp: Annotated[str | None, Header()] = None,
        x_request_id: Annotated[str | None, Header()] = None,
        x_signature: Annotated[str | None, Header()] = None,
    ) -> str:
        body = await request.body()
        try:
            auth_request_id = verify_request(
                secret=settings.hmac_secret,
                method=request.method,
                path=request.url.path,
                timestamp=x_timestamp,
                request_id=x_request_id,
                signature=x_signature,
                body=body,
                ttl_seconds=settings.signature_ttl_seconds,
            )
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        if not state.claim_auth_request(auth_request_id):
            raise HTTPException(status_code=401, detail="request ID has already been used")
        return auth_request_id

    @app.get("/healthz")
    def health() -> dict[str, str]:
        try:
            import_module("territory_optimisation")
        except ImportError as exc:
            raise HTTPException(status_code=503, detail="territory engine is unavailable") from exc
        return {
            "status": "ok",
            "api_version": __version__,
            "engine_version": settings.engine_version,
        }

    @app.post(
        "/v1/runs",
        response_model=RunAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(signed_request)],
    )
    def create_run(
        body: RunRequest,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> RunAccepted:
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Idempotency-Key is required")
        try:
            return coordinator.submit(
                request_id=idempotency_key,
                trigger="manual",
                force=body.force,
            )
        except Exception as exc:
            raise HTTPException(status_code=503, detail="job launch failed") from exc

    @app.post(
        "/v1/next-cycle",
        response_model=RunAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(signed_request)],
    )
    def create_next_cycle(
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> RunAccepted:
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Idempotency-Key is required")
        return coordinator.submit(
            request_id=idempotency_key,
            trigger="manual",
            force=True,
            kind=RunKind.NEXT_CYCLE,
        )

    @app.get(
        "/v1/runs/latest",
        response_model=LatestRuns,
        dependencies=[Depends(signed_request)],
    )
    def latest() -> LatestRuns:
        latest_check, latest_success = state.latest()
        return LatestRuns(latest_check=latest_check, latest_success=latest_success)

    @app.get(
        "/v1/runs/{run_id}",
        response_model=RunRecord,
        dependencies=[Depends(signed_request)],
    )
    def get_run(run_id: str) -> RunRecord:
        record = coordinator.get(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="run not found")
        return record

    @app.post(
        "/internal/scheduled-runs",
        response_model=RunAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def scheduled_run() -> RunAccepted:
        bucket = int(time.time()) // 900
        return coordinator.submit(request_id=f"schedule-{bucket}", trigger="schedule", force=False)

    return app


app = create_app()


def main() -> None:
    uvicorn.run("territory_api.api:app", host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
