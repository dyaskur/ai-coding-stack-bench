"""Task listing: envelope, ordering, status filter and pagination."""

from __future__ import annotations

from collections.abc import Callable

import httpx
from helpers import Json, expect


def test_empty_project_lists_no_tasks(client: httpx.Client, project: Json) -> None:
    data = expect(client.get(f"/projects/{project['id']}/tasks"), 200)
    assert data["items"] == [] and data["total"] == 0
    assert data["page"] == 1 and data["per_page"] == 20


def test_tasks_listed_in_id_order(
    client: httpx.Client, project: Json, make_task: Callable[..., Json]
) -> None:
    ids = [make_task(project["id"])["id"] for _ in range(3)]
    data = expect(client.get(f"/projects/{project['id']}/tasks"), 200)
    assert [t["id"] for t in data["items"]] == sorted(ids)
    assert data["total"] == 3


def test_filter_by_status(
    client: httpx.Client, project: Json, make_task: Callable[..., Json]
) -> None:
    pid = project["id"]
    done = [make_task(pid, status="done")["id"] for _ in range(2)]
    make_task(pid, status="todo")
    make_task(pid, status="in_progress")
    data = expect(client.get(f"/projects/{pid}/tasks", params={"status": "done"}), 200)
    assert [t["id"] for t in data["items"]] == sorted(done)
    assert all(t["status"] == "done" for t in data["items"])
    assert data["total"] == 2
    data = expect(client.get(f"/projects/{pid}/tasks", params={"status": "in_progress"}), 200)
    assert data["total"] == 1 and data["items"][0]["status"] == "in_progress"


def test_pagination(client: httpx.Client, project: Json, make_task: Callable[..., Json]) -> None:
    pid = project["id"]
    ids = sorted(make_task(pid)["id"] for _ in range(5))
    pages = []
    for page in (1, 2, 3):
        data = expect(
            client.get(f"/projects/{pid}/tasks", params={"page": page, "per_page": 2}), 200
        )
        assert data["page"] == page and data["per_page"] == 2 and data["total"] == 5
        pages.append([t["id"] for t in data["items"]])
    assert pages == [ids[0:2], ids[2:4], ids[4:5]]


def test_page_past_the_end_is_empty(
    client: httpx.Client, project: Json, make_task: Callable[..., Json]
) -> None:
    make_task(project["id"])
    data = expect(
        client.get(f"/projects/{project['id']}/tasks", params={"page": 5, "per_page": 10}), 200
    )
    assert data["items"] == [] and data["total"] == 1 and data["page"] == 5


def test_pagination_combined_with_status_filter(
    client: httpx.Client, project: Json, make_task: Callable[..., Json]
) -> None:
    pid = project["id"]
    todo = sorted(make_task(pid, status="todo")["id"] for _ in range(3))
    make_task(pid, status="done")
    data = expect(
        client.get(f"/projects/{pid}/tasks", params={"status": "todo", "page": 2, "per_page": 2}),
        200,
    )
    assert data["total"] == 3
    assert [t["id"] for t in data["items"]] == todo[2:]


def test_per_page_maximum_is_accepted(client: httpx.Client, project: Json) -> None:
    data = expect(client.get(f"/projects/{project['id']}/tasks", params={"per_page": 100}), 200)
    assert data["per_page"] == 100
