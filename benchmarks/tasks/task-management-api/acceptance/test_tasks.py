"""Task CRUD inside a project."""

from __future__ import annotations

from collections.abc import Callable

import httpx
from helpers import Json, expect, unique


def test_create_task_with_defaults(client: httpx.Client, project: Json) -> None:
    title = unique("task")
    created = expect(client.post(f"/projects/{project['id']}/tasks", json={"title": title}), 201)
    assert isinstance(created["id"], int)
    assert created["project_id"] == project["id"]
    assert created["title"] == title
    assert created["status"] == "todo"
    assert created["description"] is None
    assert created["due_date"] is None


def test_create_task_with_all_fields(client: httpx.Client, project: Json) -> None:
    payload = {
        "title": unique("task"),
        "description": "Write the release notes",
        "status": "in_progress",
        "due_date": "2030-01-31",
    }
    created = expect(client.post(f"/projects/{project['id']}/tasks", json=payload), 201)
    for key, value in payload.items():
        assert created[key] == value, key


def test_get_task(client: httpx.Client, project: Json, make_task: Callable[..., Json]) -> None:
    task = make_task(project["id"], due_date="2031-06-01")
    got = expect(client.get(f"/projects/{project['id']}/tasks/{task['id']}"), 200)
    assert got["id"] == task["id"]
    assert got["title"] == task["title"]
    assert got["due_date"] == "2031-06-01"


def test_patch_task_updates_only_given_fields(
    client: httpx.Client, project: Json, make_task: Callable[..., Json]
) -> None:
    task = make_task(project["id"], description="details")
    url = f"/projects/{project['id']}/tasks/{task['id']}"
    patched = expect(client.patch(url, json={"status": "done"}), 200)
    assert patched["status"] == "done"
    assert patched["title"] == task["title"]
    assert patched["description"] == "details"
    assert expect(client.get(url), 200)["status"] == "done"


def test_patch_task_due_date_and_clear_it(
    client: httpx.Client, project: Json, make_task: Callable[..., Json]
) -> None:
    task = make_task(project["id"])
    url = f"/projects/{project['id']}/tasks/{task['id']}"
    assert expect(client.patch(url, json={"due_date": "2032-02-29"}), 200)["due_date"] == (
        "2032-02-29"
    )
    assert expect(client.patch(url, json={"due_date": None}), 200)["due_date"] is None


def test_delete_task(client: httpx.Client, project: Json, make_task: Callable[..., Json]) -> None:
    task = make_task(project["id"])
    url = f"/projects/{project['id']}/tasks/{task['id']}"
    resp = client.delete(url)
    expect(resp, 204)
    assert resp.content == b""
    expect(client.get(url), 404)


def test_tasks_are_scoped_to_their_project(
    client: httpx.Client, make_project: Callable[..., Json], make_task: Callable[..., Json]
) -> None:
    a, b = make_project(), make_project()
    task = make_task(a["id"])
    listed = expect(client.get(f"/projects/{b['id']}/tasks"), 200)
    assert listed["total"] == 0 and listed["items"] == []
    expect(client.get(f"/projects/{b['id']}/tasks/{task['id']}"), 404)
    expect(client.patch(f"/projects/{b['id']}/tasks/{task['id']}", json={"status": "done"}), 404)
    expect(client.delete(f"/projects/{b['id']}/tasks/{task['id']}"), 404)
    # Still intact in its own project.
    assert expect(client.get(f"/projects/{a['id']}/tasks/{task['id']}"), 200)["status"] == "todo"
