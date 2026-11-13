"""Project CRUD."""

from __future__ import annotations

from collections.abc import Callable

import httpx
from helpers import Json, expect, unique


def test_create_project_returns_it(client: httpx.Client) -> None:
    name = unique("project")
    created = expect(client.post("/projects", json={"name": name, "description": "Q4 work"}), 201)
    assert isinstance(created["id"], int)
    assert created["name"] == name
    assert created["description"] == "Q4 work"


def test_optional_description_defaults_to_null(client: httpx.Client) -> None:
    created = expect(client.post("/projects", json={"name": unique("project")}), 201)
    assert "description" in created and created["description"] is None


def test_get_project(client: httpx.Client, project: Json) -> None:
    got = expect(client.get(f"/projects/{project['id']}"), 200)
    assert got["id"] == project["id"]
    assert got["name"] == project["name"]


def test_list_projects_envelope(client: httpx.Client, project: Json) -> None:
    data = expect(client.get("/projects", params={"per_page": 100}), 200)
    assert set(data) >= {"items", "page", "per_page", "total"}
    assert data["page"] == 1 and data["per_page"] == 100
    assert isinstance(data["items"], list) and data["total"] >= 1
    ids = [p["id"] for p in data["items"]]
    assert ids == sorted(ids), "items must be ordered by id ascending"


def test_new_project_is_listed(client: httpx.Client, make_project: Callable[..., Json]) -> None:
    created = make_project()
    total = expect(client.get("/projects"), 200)["total"]
    # Ordered by id ascending, so the newest project is on the last page.
    last_page = max(1, -(-total // 100))
    data = expect(client.get("/projects", params={"per_page": 100, "page": last_page}), 200)
    assert created["id"] in [p["id"] for p in data["items"]]


def test_list_projects_default_page_size(client: httpx.Client) -> None:
    data = expect(client.get("/projects"), 200)
    assert data["page"] == 1 and data["per_page"] == 20
    assert len(data["items"]) <= 20


def test_patch_project_updates_only_given_fields(
    client: httpx.Client, make_project: Callable[..., Json]
) -> None:
    created = make_project(description="keep me")
    new_name = unique("renamed")
    patched = expect(client.patch(f"/projects/{created['id']}", json={"name": new_name}), 200)
    assert patched["name"] == new_name
    assert patched["description"] == "keep me"
    assert expect(client.get(f"/projects/{created['id']}"), 200)["name"] == new_name


def test_patch_project_can_clear_description(
    client: httpx.Client, make_project: Callable[..., Json]
) -> None:
    created = make_project(description="temporary")
    patched = expect(client.patch(f"/projects/{created['id']}", json={"description": None}), 200)
    assert patched["description"] is None
    assert patched["name"] == created["name"]


def test_delete_project(client: httpx.Client, project: Json) -> None:
    resp = client.delete(f"/projects/{project['id']}")
    expect(resp, 204)
    assert resp.content == b""
    expect(client.get(f"/projects/{project['id']}"), 404)


def test_delete_project_deletes_its_tasks(
    client: httpx.Client, project: Json, make_task: Callable[..., Json]
) -> None:
    task = make_task(project["id"])
    expect(client.delete(f"/projects/{project['id']}"), 204)
    expect(client.get(f"/projects/{project['id']}/tasks/{task['id']}"), 404)
    expect(client.get(f"/projects/{project['id']}/tasks"), 404)
