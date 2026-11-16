"""HMAC request authentication used behind API Gateway."""

from __future__ import annotations

import hashlib
import hmac
import time


class AuthenticationError(ValueError):
    pass


def body_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical_message(
    method: str, path: str, timestamp: str, request_id: str, body: bytes
) -> bytes:
    return "\n".join([method.upper(), path, timestamp, request_id, body_digest(body)]).encode(
        "utf-8"
    )


def sign_request(
    secret: str,
    method: str,
    path: str,
    timestamp: str,
    request_id: str,
    body: bytes,
) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        canonical_message(method, path, timestamp, request_id, body),
        hashlib.sha256,
    ).hexdigest()


def verify_request(
    *,
    secret: str,
    method: str,
    path: str,
    timestamp: str | None,
    request_id: str | None,
    signature: str | None,
    body: bytes,
    ttl_seconds: int,
    now: float | None = None,
) -> str:
    if not timestamp or not request_id or not signature:
        raise AuthenticationError("missing request authentication headers")
    try:
        sent_at = int(timestamp)
    except ValueError as exc:
        raise AuthenticationError("invalid request timestamp") from exc
    current = int(now if now is not None else time.time())
    if abs(current - sent_at) > ttl_seconds:
        raise AuthenticationError("request signature has expired")
    expected = sign_request(secret, method, path, timestamp, request_id, body)
    if not hmac.compare_digest(signature.lower(), expected):
        raise AuthenticationError("invalid request signature")
    return request_id
