"""Validation (422) and missing resources (404)."""

from __future__ import annotations

import httpx
import pytest
from helpers import Json, assert_errors_for, body, expect, unique

MISSING = 2_000_000_000


# -- 422 on create ---------------------------------------------------------------


def test_project_name_required(client: httpx.Client) -> None:
    assert_errors_for(client.post("/projects", json={"description": "no name"}), "name")


@pytest.mark.parametrize("name", ["", "x" * 201, 123])
def test_project_name_invalid(client: httpx.Client, name: object) -> None:
    assert_errors_for(client.post("/projects", json={"name": name}), "name")


def test_task_title_required(client: httpx.Client, project: Json) -> None:
    assert_errors_for(client.post(f"/projects/{project['id']}/tasks", json={}), "title")


@pytest.mark.parametrize("title", ["", "x" * 201])
def test_task_title_invalid(client: httpx.Client, project: Json, title: str) -> None:
    resp = client.post(f"/projects/{project['id']}/tasks", json={"title": title})
    assert_errors_for(resp, "title")


def test_task_status_must_be_known(client: httpx.Client, project: Json) -> None:
    resp = client.post(
        f"/projects/{project['id']}/tasks", json={"title": unique("t"), "status": "blocked"}
    )
    assert_errors_for(resp, "status")


@pytest.mark.parametrize("due_date", ["tomorrow", "2030-13-01", "2030-02-30", "31-01-2030"])
def test_task_due_date_must_be_a_date(client: httpx.Client, project: Json, due_date: str) -> None:
    resp = client.post(
        f"/projects/{project['id']}/tasks", json={"title": unique("t"), "due_date": due_date}
    )
    assert_errors_for(resp, "due_date")


def test_every_invalid_field_is_reported(client: httpx.Client, project: Json) -> None:
    resp = client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "", "status": "nope", "due_date": "soon"},
    )
    assert_errors_for(resp, "title", "status", "due_date")


def test_invalid_create_stores_nothing(client: httpx.Client, project: Json) -> None:
    client.post(f"/projects/{project['id']}/tasks", json={"title": ""})
    assert expect(client.get(f"/projects/{project['id']}/tasks"), 200)["total"] == 0


# -- 422 on update -----------------------------------------------------------------


def test_patch_project_rejects_empty_name(client: httpx.Client, project: Json) -> None:
    assert_errors_for(client.patch(f"/projects/{project['id']}", json={"name": ""}), "name")
    assert expect(client.get(f"/projects/{project['id']}"), 200)["name"] == project["name"]


def test_patch_task_rejects_unknown_status(client: httpx.Client, project: Json) -> None:
    task = expect(client.post(f"/projects/{project['id']}/tasks", json={"title": "a"}), 201)
    url = f"/projects/{project['id']}/tasks/{task['id']}"
    assert_errors_for(client.patch(url, json={"status": "archived"}), "status")
    assert expect(client.get(url), 200)["status"] == "todo"


# -- 422 on list query parameters ------------------------------------------------------


@pytest.mark.parametrize(
    ("param", "value"),
    [("status", "archived"), ("page", "0"), ("per_page", "0"), ("per_page", "101"),
     ("page", "abc")],
)  # fmt: skip
def test_invalid_list_query(client: httpx.Client, project: Json, param: str, value: str) -> None:
    resp = client.get(f"/projects/{project['id']}/tasks", params={param: value})
    assert_errors_for(resp, param)


# -- 404 ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/projects/{missing}"),
        ("PATCH", "/projects/{missing}"),
        ("DELETE", "/projects/{missing}"),
        ("GET", "/projects/{missing}/tasks"),
        ("POST", "/projects/{missing}/tasks"),
        ("GET", "/projects/{pid}/tasks/{missing}"),
        ("PATCH", "/projects/{pid}/tasks/{missing}"),
        ("DELETE", "/projects/{pid}/tasks/{missing}"),
    ],
)
def test_missing_resources_return_404(
    client: httpx.Client, project: Json, method: str, path: str
) -> None:
    url = path.format(missing=MISSING, pid=project["id"])
    # Send a body that would be valid for the endpoint, so only the missing id can fail.
    payload: Json | None = None
    if method in ("POST", "PATCH"):
        payload = {"name": "x"} if path == "/projects/{missing}" else {"title": "x"}
    resp = client.request(method, url, json=payload)
    assert resp.status_code == 404, f"{method} {url} -> {resp.status_code}: {resp.text[:200]}"
    assert isinstance(body(resp), dict)
