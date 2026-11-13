"""Assertion helpers that turn contract violations into readable failures."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

Json = dict[str, Any]


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def body(resp: httpx.Response) -> Any:
    assert "json" in resp.headers.get("content-type", ""), (
        f"expected a JSON response, got {resp.headers.get('content-type')!r}: {resp.text[:200]}"
    )
    return resp.json()


def expect(resp: httpx.Response, status: int) -> Any:
    assert resp.status_code == status, (
        f"{resp.request.method} {resp.request.url.path} -> {resp.status_code}, "
        f"expected {status}: {resp.text[:300]}"
    )
    return None if status == 204 else body(resp)


def assert_errors_for(resp: httpx.Response, *fields: str) -> None:
    """422 with {"errors": {field: [message, ...]}} naming every given field."""
    data = expect(resp, 422)
    assert isinstance(data, dict) and isinstance(data.get("errors"), dict), (
        f"422 body must be {{'errors': {{field: [messages]}}}}, got {data!r}"
    )
    for field in fields:
        messages = data["errors"].get(field)
        assert isinstance(messages, list) and messages, (
            f"expected error messages for {field!r}, got {data['errors']!r}"
        )
        assert all(isinstance(m, str) and m for m in messages)
