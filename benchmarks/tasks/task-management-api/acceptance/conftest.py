"""Shared fixtures. The suite only knows the API contract in task.yaml, never the stack."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from helpers import Json, expect, unique

BASE_URL = os.environ.get("BASE_URL", "http://app:8000")
READY_TIMEOUT = float(os.environ.get("READY_TIMEOUT", "180"))


_unreachable: str | None = None


def _wait_until_up() -> None:
    """Wait until the server answers anything at all; fail fast once it never did."""
    global _unreachable
    if _unreachable:
        pytest.fail(_unreachable)
    deadline = time.monotonic() + READY_TIMEOUT
    while True:
        try:
            httpx.get(BASE_URL + "/projects", timeout=3)
            return
        except httpx.HTTPError:
            if time.monotonic() > deadline:
                _unreachable = f"app did not answer at {BASE_URL} within {READY_TIMEOUT:.0f}s"
                pytest.fail(_unreachable)
            time.sleep(1)


@pytest.fixture(scope="session")
def client() -> Iterator[httpx.Client]:
    _wait_until_up()
    with httpx.Client(base_url=BASE_URL, timeout=15, headers={"Accept": "application/json"}) as c:
        yield c


@pytest.fixture
def make_project(client: httpx.Client) -> Callable[..., Json]:
    def make(**fields: Any) -> Json:
        payload = {"name": unique("project"), **fields}
        return expect(client.post("/projects", json=payload), 201)

    return make


@pytest.fixture
def project(make_project: Callable[..., Json]) -> Json:
    return make_project()


@pytest.fixture
def make_task(client: httpx.Client) -> Callable[..., Json]:
    def make(project_id: int, **fields: Any) -> Json:
        payload = {"title": unique("task"), **fields}
        return expect(client.post(f"/projects/{project_id}/tasks", json=payload), 201)

    return make
