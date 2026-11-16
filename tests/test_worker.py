import pytest
from territory_optimisation import DataValidationError

from territory_api.config import AppConfig
from territory_api.models import RunKind, RunStatus
from territory_api.sheets import InMemorySheetRepository
from territory_api.state import InMemoryStateStore
from territory_api.worker_service import WorkerService


def submit(state, run_id, *, force=False, kind=RunKind.OPTIMISE):
    state.submit(
        run_id=run_id,
        request_id=f"request-{run_id}",
        kind=kind,
        trigger="test",
        force=force,
        engine_version="engine-test",
        lease_seconds=2700,
    )


def test_worker_publishes_then_skips_unchanged_input(sheet_rows):
    accounts, roles, settings = sheet_rows
    sheets = InMemorySheetRepository(accounts=accounts, ae_roles=roles, settings=settings)
    state = InMemoryStateStore()
    config = AppConfig(hmac_secret="a-secure-test-secret", engine_version="engine-test")
    worker = WorkerService(config, state, sheets)

    submit(state, "first")
    worker.run("first")
    assert state.get("first").status == RunStatus.SUCCEEDED
    assert "Assignments" in sheets.outputs
    original = sheets.outputs["Assignments"].copy()

    submit(state, "second")
    worker.run("second")
    assert state.get("second").status == RunStatus.SKIPPED
    assert sheets.outputs["Assignments"].equals(original)


def test_failed_publish_keeps_last_good_outputs(sheet_rows):
    accounts, roles, settings = sheet_rows
    sheets = InMemorySheetRepository(accounts=accounts, ae_roles=roles, settings=settings)
    state = InMemoryStateStore()
    config = AppConfig(hmac_secret="a-secure-test-secret", engine_version="engine-test")
    worker = WorkerService(config, state, sheets)

    submit(state, "first", force=True)
    worker.run("first")
    original = sheets.outputs["Assignments"].copy()
    sheets.fail_publish = True
    submit(state, "failed", force=True)
    try:
        worker.run("failed")
    except RuntimeError:
        pass
    assert state.get("failed").status == RunStatus.FAILED
    assert sheets.outputs["Assignments"].equals(original)


def test_refresh_never_changes_input_tabs(sheet_rows):
    accounts, roles, settings = sheet_rows
    sheets = InMemorySheetRepository(accounts=accounts, ae_roles=roles, settings=settings)
    original = sheets.read_inputs()
    state = InMemoryStateStore()
    worker = WorkerService(
        AppConfig(hmac_secret="a-secure-test-secret", engine_version="engine-test"),
        state,
        sheets,
    )
    submit(state, "run", force=True)
    worker.run("run")
    assert sheets.read_inputs() == original


def test_next_cycle_requires_decisions_then_writes_new_tabs(sheet_rows):
    accounts, roles, settings = sheet_rows
    sheets = InMemorySheetRepository(accounts=accounts, ae_roles=roles, settings=settings)
    state = InMemoryStateStore()
    worker = WorkerService(
        AppConfig(hmac_secret="a-secure-test-secret", engine_version="engine-test"),
        state,
        sheets,
    )
    submit(state, "optimise", force=True)
    worker.run("optimise")

    voluntary = sheets.reviews["Action"].eq("Move")
    if voluntary.any():
        submit(state, "blocked", kind=RunKind.NEXT_CYCLE)
        with pytest.raises(DataValidationError):
            worker.run("blocked")
        assert state.get("blocked").status == RunStatus.FAILED

        sheets.reviews.loc[voluntary, "Manager Decision"] = "Approve"

    submit(state, "next", kind=RunKind.NEXT_CYCLE)
    worker.run("next")
    assert state.get("next").status == RunStatus.SUCCEEDED
    assert sheets.next_cycle is not None
    next_accounts, next_roles = sheets.next_cycle
    assert len(next_accounts) == len(sheets.outputs["Assignments"])
    assert len(next_roles) == len(roles) - 1
