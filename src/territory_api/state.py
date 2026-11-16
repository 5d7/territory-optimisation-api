"""Run-state, idempotency, and lease repositories."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any, Protocol

from google.cloud import firestore

from .models import RunKind, RunRecord, RunStatus

ACTIVE = {RunStatus.QUEUED, RunStatus.RUNNING}


class StateStore(Protocol):
    def submit(
        self,
        *,
        run_id: str,
        request_id: str,
        kind: RunKind,
        trigger: str,
        force: bool,
        engine_version: str,
        lease_seconds: int,
    ) -> tuple[RunRecord, bool]: ...

    def get(self, run_id: str) -> RunRecord | None: ...
    def latest(self) -> tuple[RunRecord | None, RunRecord | None]: ...
    def update(self, run_id: str, **changes: Any) -> RunRecord: ...
    def release(self, run_id: str) -> None: ...
    def get_last_hash(self) -> str | None: ...
    def set_last_success(self, run_id: str, input_hash: str) -> None: ...
    def claim_auth_request(self, request_id: str) -> bool: ...


class InMemoryStateStore:
    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._requests: dict[str, str] = {}
        self._active_run: str | None = None
        self._lease_expires: datetime | None = None
        self._last_hash: str | None = None
        self._last_success: str | None = None
        self._auth_requests: set[str] = set()
        self._lock = RLock()

    def submit(
        self,
        *,
        run_id: str,
        request_id: str,
        kind: RunKind,
        trigger: str,
        force: bool,
        engine_version: str,
        lease_seconds: int,
    ) -> tuple[RunRecord, bool]:
        with self._lock:
            if request_id in self._requests:
                return deepcopy(self._runs[self._requests[request_id]]), True
            now = datetime.now(UTC)
            if (
                self._active_run
                and self._lease_expires
                and self._lease_expires > now
                and self._runs[self._active_run].status in ACTIVE
            ):
                self._requests[request_id] = self._active_run
                return deepcopy(self._runs[self._active_run]), True
            record = RunRecord(
                run_id=run_id,
                kind=kind,
                trigger=trigger,
                force=force,
                engine_version=engine_version,
            )
            self._runs[run_id] = record
            self._requests[request_id] = run_id
            self._active_run = run_id
            self._lease_expires = now + timedelta(seconds=lease_seconds)
            return deepcopy(record), False

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            record = self._runs.get(run_id)
            return deepcopy(record) if record else None

    def latest(self) -> tuple[RunRecord | None, RunRecord | None]:
        with self._lock:
            records = sorted(self._runs.values(), key=lambda item: item.queued_at)
            latest = records[-1] if records else None
            success = self._runs.get(self._last_success or "")
            return deepcopy(latest), deepcopy(success)

    def update(self, run_id: str, **changes: Any) -> RunRecord:
        with self._lock:
            record = self._runs[run_id].model_copy(update=changes)
            self._runs[run_id] = record
            return deepcopy(record)

    def release(self, run_id: str) -> None:
        with self._lock:
            if self._active_run == run_id:
                self._active_run = None
                self._lease_expires = None

    def get_last_hash(self) -> str | None:
        with self._lock:
            return self._last_hash

    def set_last_success(self, run_id: str, input_hash: str) -> None:
        with self._lock:
            self._last_hash = input_hash
            self._last_success = run_id

    def claim_auth_request(self, request_id: str) -> bool:
        with self._lock:
            if request_id in self._auth_requests:
                return False
            self._auth_requests.add(request_id)
            return True


class FirestoreStateStore:
    """Firestore implementation containing metadata only, never account rows."""

    def __init__(self, project_id: str, spreadsheet_id: str) -> None:
        self.client = firestore.Client(project=project_id)
        self.scope = spreadsheet_id

    @property
    def _lock_ref(self) -> Any:
        return self.client.collection("territory_locks").document(self.scope)

    @property
    def _state_ref(self) -> Any:
        return self.client.collection("territory_state").document(self.scope)

    def _run_ref(self, run_id: str) -> Any:
        return self.client.collection("territory_runs").document(run_id)

    def submit(self, **kwargs: Any) -> tuple[RunRecord, bool]:
        @firestore.transactional
        def submit_transaction(transaction: Any) -> tuple[dict[str, Any], bool]:
            request_id = kwargs["request_id"]
            request_ref = self.client.collection("territory_requests").document(request_id)
            request_snapshot = request_ref.get(transaction=transaction)
            if request_snapshot.exists:
                request_data = request_snapshot.to_dict() or {}
                existing_id = request_data["run_id"]
                existing = self._run_ref(existing_id).get(transaction=transaction).to_dict()
                return existing, True

            now = datetime.now(UTC)
            lock_snapshot = self._lock_ref.get(transaction=transaction)
            if lock_snapshot.exists:
                lock = lock_snapshot.to_dict()
                if lock.get("expires_at") and lock["expires_at"] > now:
                    existing_id = lock["run_id"]
                    existing = self._run_ref(existing_id).get(transaction=transaction).to_dict()
                    transaction.set(request_ref, {"run_id": existing_id, "created_at": now})
                    return existing, True

            record = RunRecord(
                run_id=kwargs["run_id"],
                kind=kwargs["kind"],
                trigger=kwargs["trigger"],
                force=kwargs["force"],
                engine_version=kwargs["engine_version"],
            )
            data = record.model_dump(mode="python")
            transaction.set(self._run_ref(record.run_id), data)
            transaction.set(request_ref, {"run_id": record.run_id, "created_at": now})
            transaction.set(
                self._lock_ref,
                {
                    "run_id": record.run_id,
                    "expires_at": now + timedelta(seconds=kwargs["lease_seconds"]),
                },
            )
            return data, False

        data, deduplicated = submit_transaction(self.client.transaction())
        return RunRecord.model_validate(data), deduplicated

    def get(self, run_id: str) -> RunRecord | None:
        snapshot = self._run_ref(run_id).get()
        return RunRecord.model_validate(snapshot.to_dict()) if snapshot.exists else None

    def latest(self) -> tuple[RunRecord | None, RunRecord | None]:
        state = self._state_ref.get().to_dict() or {}
        query = (
            self.client.collection("territory_runs")
            .order_by("queued_at", direction=firestore.Query.DESCENDING)
            .limit(1)
        )
        docs = list(query.stream())
        latest = RunRecord.model_validate(docs[0].to_dict()) if docs else None
        success_id = state.get("last_success_run_id")
        success = self.get(success_id) if success_id else None
        return latest, success

    def update(self, run_id: str, **changes: Any) -> RunRecord:
        self._run_ref(run_id).update(changes)
        record = self.get(run_id)
        if record is None:
            raise KeyError(run_id)
        return record

    def release(self, run_id: str) -> None:
        snapshot = self._lock_ref.get()
        if snapshot.exists and snapshot.to_dict().get("run_id") == run_id:
            self._lock_ref.delete()

    def get_last_hash(self) -> str | None:
        return (self._state_ref.get().to_dict() or {}).get("last_success_hash")

    def set_last_success(self, run_id: str, input_hash: str) -> None:
        self._state_ref.set(
            {
                "last_success_run_id": run_id,
                "last_success_hash": input_hash,
                "updated_at": datetime.now(UTC),
            },
            merge=True,
        )

    def claim_auth_request(self, request_id: str) -> bool:
        reference = self.client.collection("territory_auth_requests").document(request_id)

        @firestore.transactional
        def claim(transaction: Any) -> bool:
            snapshot = reference.get(transaction=transaction)
            if snapshot.exists:
                return False
            transaction.set(
                reference,
                {
                    "created_at": datetime.now(UTC),
                    "expires_at": datetime.now(UTC) + timedelta(days=1),
                    "scope": self.scope,
                },
            )
            return True

        return bool(claim(self.client.transaction()))
