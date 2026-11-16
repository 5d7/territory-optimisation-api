"""Background optimization and next-cycle workflows."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from territory_optimisation import (
    DataValidationError,
    build_next_cycle_frames,
    result_tables,
    run_territory_review,
)

from .config import AppConfig
from .hashing import input_hash, normalize_frames, parse_config, rows_to_frame
from .models import RunKind, RunStatus
from .proposals import merge_reviews
from .sheets import SheetRepository
from .state import StateStore

LOGGER = logging.getLogger("territory.worker")


def _log_event(event: str, **fields: Any) -> None:
    LOGGER.info(json.dumps({"event": event, **fields}, separators=(",", ":")))


def _safe_kpis(kpis: dict[str, Any]) -> dict[str, int | float | str]:
    result: dict[str, int | float | str] = {}
    for key, value in kpis.items():
        if isinstance(value, bool):
            result[key] = int(value)
        elif isinstance(value, int):
            result[key] = value
        elif isinstance(value, float):
            result[key] = float(value)
        else:
            try:
                result[key] = value.item()
            except AttributeError:
                result[key] = str(value)
    return result


class WorkerService:
    def __init__(self, config: AppConfig, state: StateStore, sheets: SheetRepository) -> None:
        self.config = config
        self.state = state
        self.sheets = sheets

    def run(self, run_id: str) -> None:
        record = self.state.get(run_id)
        if record is None:
            raise KeyError(f"unknown run: {run_id}")
        self.state.update(run_id, status=RunStatus.RUNNING, started_at=datetime.now(UTC))
        self.sheets.write_control(
            {
                "Status": "running",
                "Run ID": run_id,
                "Engine version": self.config.engine_version,
                "Last checked": datetime.now(UTC).isoformat(),
            }
        )
        try:
            if record.kind == RunKind.NEXT_CYCLE:
                self._next_cycle(run_id)
            else:
                self._optimise(run_id, record.force)
        except (DataValidationError, ValueError) as exc:
            self._fail(run_id, "validation_failed", str(exc))
            raise
        except Exception as exc:
            LOGGER.exception("run failed", extra={"run_id": run_id})
            self._fail(run_id, "execution_failed", str(exc))
            raise
        finally:
            self.state.release(run_id)

    def _optimise(self, run_id: str, force: bool) -> None:
        raw = self.sheets.read_inputs()
        accounts = rows_to_frame(raw.accounts, "Accounts")
        roles = rows_to_frame(raw.ae_roles, "AE_Roles")
        config = parse_config(raw.settings)
        accounts, roles = normalize_frames(accounts, roles)
        digest = input_hash(accounts, roles, config, self.config.engine_version)
        self.state.update(run_id, input_hash=digest)
        if not force and self.state.get_last_hash() == digest:
            completed = datetime.now(UTC)
            self.state.update(run_id, status=RunStatus.SKIPPED, completed_at=completed)
            self.sheets.write_control(
                {
                    "Status": "skipped",
                    "Run ID": run_id,
                    "Reason": "Inputs, settings, and engine version are unchanged",
                    "Input hash": digest,
                    "Engine version": self.config.engine_version,
                    "Last checked": completed.isoformat(),
                }
            )
            self.sheets.append_history(
                {
                    "Run ID": run_id,
                    "Status": "skipped",
                    "Completed at": completed.isoformat(),
                    "Input hash": digest,
                    "Engine version": self.config.engine_version,
                },
                self.config.run_history_limit,
            )
            _log_event("run_completed", run_id=run_id, status="skipped")
            return

        result = run_territory_review(config, accounts, roles, quiet=True)
        tables = result_tables(result)
        reviews = self.sheets.read_reviews()
        moves, new_reviews = merge_reviews(tables["Moves"], reviews)
        tables["Moves"] = moves
        self.sheets.append_reviews(new_reviews)
        self.sheets.publish(run_id, tables)

        statuses = {
            str(row["Region"]): str(row["Status"]) for _, row in result.region_summary.iterrows()
        }
        kpis = _safe_kpis(result.kpis)
        completed = datetime.now(UTC)
        self.state.update(
            run_id,
            status=RunStatus.SUCCEEDED,
            completed_at=completed,
            solver_statuses=statuses,
            kpis=kpis,
        )
        self.state.set_last_success(run_id, digest)
        self.sheets.write_control(
            {
                "Status": "succeeded",
                "Run ID": run_id,
                "Last successful run": run_id,
                "Completed at": completed.isoformat(),
                "Input hash": digest,
                "Engine version": self.config.engine_version,
                "Coverage %": round(float(result.kpis["coverage_pct"]), 2),
                "Proposed moves": int(result.kpis["moves"]),
            }
        )
        self.sheets.append_history(
            {
                "Run ID": run_id,
                "Status": "succeeded",
                "Completed at": completed.isoformat(),
                "Input hash": digest,
                "Engine version": self.config.engine_version,
                "Coverage %": round(float(result.kpis["coverage_pct"]), 2),
                "Moves": int(result.kpis["moves"]),
                "Breaches after": int(result.kpis["breaches_after"]),
                "Seconds": round(float(result.kpis["seconds"]), 2),
            },
            self.config.run_history_limit,
        )
        _log_event(
            "run_completed",
            run_id=run_id,
            status="succeeded",
            coverage_pct=round(float(result.kpis["coverage_pct"]), 2),
            incomplete_coverage=float(result.kpis["coverage_pct"]) < 100,
            infeasible=any("INFEASIBLE" in value.upper() for value in statuses.values()),
            seconds=round(float(result.kpis["seconds"]), 2),
        )

    def _next_cycle(self, run_id: str) -> None:
        assignments = self.sheets.read_output("Assignments")
        moves = self.sheets.read_output("Moves")
        reviews = self.sheets.read_reviews()
        raw = self.sheets.read_inputs()
        roles = rows_to_frame(raw.ae_roles, "AE_Roles")
        if assignments.empty:
            raise DataValidationError(
                "A successful optimization is required.",
                pd.DataFrame(),
            )
        active_ids = set(moves["Proposal ID"].astype(str))
        active_reviews = reviews[reviews["Proposal ID"].astype(str).isin(active_ids)]
        next_accounts, next_roles = build_next_cycle_frames(
            assignments, active_reviews, roles, require_complete=True
        )
        self.sheets.write_next_cycle(next_accounts, next_roles)
        completed = datetime.now(UTC)
        self.state.update(
            run_id,
            status=RunStatus.SUCCEEDED,
            completed_at=completed,
            kpis={"accounts": len(next_accounts), "ae_roles": len(next_roles)},
        )
        self.sheets.write_control(
            {
                "Status": "next_cycle_created",
                "Run ID": run_id,
                "Completed at": completed.isoformat(),
                "Engine version": self.config.engine_version,
            }
        )
        _log_event("next_cycle_completed", run_id=run_id, status="succeeded")

    def _fail(self, run_id: str, code: str, summary: str) -> None:
        completed = datetime.now(UTC)
        safe_summary = summary.replace("\n", " ")[:300]
        self.state.update(
            run_id,
            status=RunStatus.FAILED,
            completed_at=completed,
            error_code=code,
            error_summary=safe_summary,
        )
        self.sheets.write_control(
            {
                "Status": "failed",
                "Run ID": run_id,
                "Completed at": completed.isoformat(),
                "Error code": code,
                "Error summary": safe_summary,
                "Engine version": self.config.engine_version,
            }
        )
        self.sheets.append_history(
            {
                "Run ID": run_id,
                "Status": "failed",
                "Completed at": completed.isoformat(),
                "Error code": code,
                "Engine version": self.config.engine_version,
            },
            self.config.run_history_limit,
        )
        _log_event("run_failed", run_id=run_id, code=code)
