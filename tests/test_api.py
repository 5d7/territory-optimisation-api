import json
import time
import uuid

from fastapi.testclient import TestClient

from territory_api.api import create_app
from territory_api.auth import sign_request
from territory_api.config import AppConfig
from territory_api.jobs import InMemoryJobLauncher
from territory_api.state import InMemoryStateStore

SECRET = "a-secure-test-secret"


def signed_headers(method, path, body=b"", idempotency_key=None, request_id=None):
    timestamp = str(int(time.time()))
    request_id = request_id or uuid.uuid4().hex
    headers = {
        "Content-Type": "application/json",
        "X-Timestamp": timestamp,
        "X-Request-ID": request_id,
        "X-Signature": sign_request(SECRET, method, path, timestamp, request_id, body),
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def make_client():
    state = InMemoryStateStore()
    launcher = InMemoryJobLauncher()
    config = AppConfig(hmac_secret=SECRET, engine_version="engine-test")
    return (
        TestClient(create_app(config=config, state_store=state, launcher=launcher)),
        state,
        launcher,
    )


def test_run_submission_is_idempotent_and_does_not_overlap():
    client, _, launcher = make_client()
    body = json.dumps({"force": True}, separators=(",", ":")).encode()
    first = client.post(
        "/v1/runs",
        content=body,
        headers=signed_headers("POST", "/v1/runs", body, idempotency_key="same-operation"),
    )
    second = client.post(
        "/v1/runs",
        content=body,
        headers=signed_headers("POST", "/v1/runs", body, idempotency_key="same-operation"),
    )
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["run_id"] == second.json()["run_id"]
    assert second.json()["deduplicated"] is True
    assert len(launcher.launched) == 1


def test_replayed_auth_request_is_rejected():
    client, _, _ = make_client()
    body = b'{"force":false}'
    headers = signed_headers(
        "POST", "/v1/runs", body, idempotency_key="operation", request_id="replay"
    )
    assert client.post("/v1/runs", content=body, headers=headers).status_code == 202
    assert client.post("/v1/runs", content=body, headers=headers).status_code == 401


def test_health_does_not_read_customer_data():
    client, _, _ = make_client()
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["engine_version"] == "engine-test"
