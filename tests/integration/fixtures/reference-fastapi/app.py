"""Reference solution for task-management-api, used only to prove the acceptance suite.

Deliberately compact; agents never see it.
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Date, ForeignKey, String, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

engine = create_engine(
    os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1)
)

Status = Literal["todo", "in_progress", "done"]
Name = Annotated[str, Field(min_length=1, max_length=200)]


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None]


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None]
    status: Mapped[str] = mapped_column(String(20), default="todo")
    due_date: Mapped[dt.date | None] = mapped_column(Date)


Base.metadata.create_all(engine)


class ProjectIn(BaseModel):
    name: Name
    description: str | None = None


class ProjectPatch(BaseModel):
    name: Name | None = None
    description: str | None = None


class TaskIn(BaseModel):
    title: Name
    description: str | None = None
    status: Status = "todo"
    due_date: dt.date | None = None


class TaskPatch(BaseModel):
    title: Name | None = None
    description: str | None = None
    status: Status | None = None
    due_date: dt.date | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str | None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    title: str
    description: str | None
    status: str
    due_date: dt.date | None


app = FastAPI()


@app.exception_handler(RequestValidationError)
async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors: dict[str, list[str]] = {}
    for e in exc.errors():
        loc = [str(p) for p in e["loc"] if p not in ("body", "query", "path")]
        errors.setdefault(loc[0] if loc else "body", []).append(e["msg"])
    return JSONResponse({"errors": errors}, status_code=422)


def db() -> Any:
    with Session(engine) as s:
        yield s


DB = Annotated[Session, Depends(db)]
Page = Annotated[int, Query(ge=1)]
PerPage = Annotated[int, Query(ge=1, le=100)]


def _null_check(patch: BaseModel, *fields: str) -> None:
    bad = {f: [f"{f} may not be null"] for f in fields
           if f in patch.model_fields_set and getattr(patch, f) is None}  # fmt: skip
    if bad:
        raise _Invalid(bad)


class _Invalid(Exception):
    def __init__(self, errors: dict[str, list[str]]) -> None:
        self.errors = errors


@app.exception_handler(_Invalid)
async def _invalid(_: Request, exc: _Invalid) -> JSONResponse:
    return JSONResponse({"errors": exc.errors}, status_code=422)


def _project(s: Session, pid: int) -> Project:
    p = s.get(Project, pid)
    if p is None:
        raise HTTPException(404, "project not found")
    return p


def _task(s: Session, pid: int, tid: int) -> Task:
    _project(s, pid)
    t = s.get(Task, tid)
    if t is None or t.project_id != pid:
        raise HTTPException(404, "task not found")
    return t


def _paginate(s: Session, stmt: Any, model: Any, out: Any, page: int, per_page: int) -> dict:
    total = s.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = s.scalars(stmt.order_by(model.id).offset((page - 1) * per_page).limit(per_page))
    return {
        "items": [out.model_validate(r).model_dump(mode="json") for r in rows],
        "page": page,
        "per_page": per_page,
        "total": total,
    }


@app.post("/projects", status_code=201, response_model=ProjectOut)
def create_project(body: ProjectIn, s: DB) -> Project:
    p = Project(**body.model_dump())
    s.add(p)
    s.commit()
    return p


@app.get("/projects")
def list_projects(s: DB, page: Page = 1, per_page: PerPage = 20) -> dict:
    return _paginate(s, select(Project), Project, ProjectOut, page, per_page)


@app.get("/projects/{pid}", response_model=ProjectOut)
def get_project(pid: int, s: DB) -> Project:
    return _project(s, pid)


@app.patch("/projects/{pid}", response_model=ProjectOut)
def patch_project(pid: int, body: ProjectPatch, s: DB) -> Project:
    p = _project(s, pid)
    _null_check(body, "name")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    s.commit()
    return p


@app.delete("/projects/{pid}", status_code=204)
def delete_project(pid: int, s: DB) -> Response:
    s.delete(_project(s, pid))
    s.commit()
    return Response(status_code=204)


@app.post("/projects/{pid}/tasks", status_code=201, response_model=TaskOut)
def create_task(pid: int, body: TaskIn, s: DB) -> Task:
    _project(s, pid)
    t = Task(project_id=pid, **body.model_dump())
    s.add(t)
    s.commit()
    return t


@app.get("/projects/{pid}/tasks")
def list_tasks(
    pid: int, s: DB, status: Status | None = None, page: Page = 1, per_page: PerPage = 20
) -> dict:
    _project(s, pid)
    stmt = select(Task).where(Task.project_id == pid)
    if status:
        stmt = stmt.where(Task.status == status)
    return _paginate(s, stmt, Task, TaskOut, page, per_page)


@app.get("/projects/{pid}/tasks/{tid}", response_model=TaskOut)
def get_task(pid: int, tid: int, s: DB) -> Task:
    return _task(s, pid, tid)


@app.patch("/projects/{pid}/tasks/{tid}", response_model=TaskOut)
def patch_task(pid: int, tid: int, body: TaskPatch, s: DB) -> Task:
    t = _task(s, pid, tid)
    _null_check(body, "title", "status")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(t, k, v)
    s.commit()
    return t


@app.delete("/projects/{pid}/tasks/{tid}", status_code=204)
def delete_task(pid: int, tid: int, s: DB) -> Response:
    s.delete(_task(s, pid, tid))
    s.commit()
    return Response(status_code=204)
