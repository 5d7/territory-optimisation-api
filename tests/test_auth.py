import time

import pytest

from territory_api.auth import AuthenticationError, sign_request, verify_request


def test_signature_round_trip_and_expiry():
    now = int(time.time())
    body = b'{"force":true}'
    signature = sign_request("secret", "POST", "/v1/runs", str(now), "r1", body)
    assert (
        verify_request(
            secret="secret",
            method="POST",
            path="/v1/runs",
            timestamp=str(now),
            request_id="r1",
            signature=signature,
            body=body,
            ttl_seconds=300,
            now=now,
        )
        == "r1"
    )
    with pytest.raises(AuthenticationError, match="expired"):
        verify_request(
            secret="secret",
            method="POST",
            path="/v1/runs",
            timestamp=str(now),
            request_id="r1",
            signature=signature,
            body=body,
            ttl_seconds=300,
            now=now + 301,
        )
