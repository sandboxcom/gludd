"""Site-test helpers: import-and-drive a generated FastAPI todo app."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

OFFLINE_SCAFFOLD_APP = '''\
"""Deterministic offline artifact for the greenfield dogfood harness."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

app = FastAPI()
_store: dict[int, dict[str, Any]] = {}
_next_id = 1


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    items = "".join(
        f'<li id="todo-{todo["id"]}">{todo["title"]}</li>'
        for todo in _store.values()
    )
    return (
        "<html><body><h1>Todo List</h1>"
        f'<ul id="todo-list">{items}</ul>'
        '<form id="add-form"><input name="title"><button>Add</button></form>'
        "</body></html>"
    )


@app.get("/api/todos")
async def list_todos() -> list[dict[str, Any]]:
    return list(_store.values())


@app.post("/api/todos", status_code=201)
async def create_todo(body: dict[str, Any]) -> dict[str, Any]:
    global _next_id
    todo = {"id": _next_id, "title": body.get("title", ""), "done": False}
    _store[_next_id] = todo
    _next_id += 1
    return todo


@app.put("/api/todos/{todo_id}")
async def update_todo(todo_id: int, body: dict[str, Any]) -> dict[str, Any]:
    if todo_id not in _store:
        raise HTTPException(status_code=404, detail="not found")
    _store[todo_id].update(body)
    return _store[todo_id]


@app.delete("/api/todos/{todo_id}")
async def delete_todo(todo_id: int) -> dict[str, Any]:
    if todo_id not in _store:
        raise HTTPException(status_code=404, detail="not found")
    return _store.pop(todo_id)
'''


def write_offline_scaffold(workspace: Path) -> None:
    """Write a known-good artifact without using credentials or network I/O."""
    app_dir = workspace / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "main.py").write_text(OFFLINE_SCAFFOLD_APP, encoding="utf-8")


def load_app_from_workspace(workspace: Path, entrypoint: str = "app/main.py") -> Any:
    """Import the generated FastAPI app object from workspace/app/main.py.

    Tries common entrypoint names. Returns None if none found.
    """
    candidates = [
        workspace / entrypoint,
        workspace / "app" / "main.py",
        workspace / "main.py",
        workspace / "src" / "main.py",
    ]
    for path in candidates:
        if path.exists():
            spec = importlib.util.spec_from_file_location("_generated_app", path)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                # Isolate: add parent to sys.path so relative imports in generated code work
                pkg_dir = str(path.parent)
                added = pkg_dir not in sys.path
                if added:
                    sys.path.insert(0, pkg_dir)
                try:
                    assert spec.loader is not None
                    spec.loader.exec_module(mod)
                    # Look for 'app' attribute (FastAPI convention)
                    return getattr(mod, "app", mod)
                finally:
                    if added:
                        sys.path.remove(pkg_dir)
    return None


def run_site_crud_tests(workspace: Path) -> dict[str, bool | str]:
    """Run CRUD round-trip against the generated site using starlette TestClient.

    Returns a dict of {test_name: passed}.
    Skips gracefully if starlette or the app is not importable.
    """
    results: dict[str, bool | str] = {}
    try:
        from starlette.testclient import TestClient
    except ImportError:
        return {"starlette_available": False}

    app = load_app_from_workspace(workspace)
    if app is None:
        results["app_importable"] = False
        return results
    results["app_importable"] = True

    try:
        with TestClient(app) as client:
            # GET / -> 200
            r = client.get("/")
            results["root_200"] = r.status_code == 200
            results["root_html"] = "<html" in r.text.lower()

            # POST /api/todos -> creates a todo
            r = client.post("/api/todos", json={"title": "buy milk"})
            results["create_todo"] = r.status_code in (200, 201)
            todo_id = None
            if results["create_todo"]:
                data = r.json()
                todo_id = data.get("id") or data.get("todo_id")

            # GET /api/todos -> list contains "buy milk"
            r = client.get("/api/todos")
            results["list_todos"] = r.status_code == 200
            if results["list_todos"]:
                items = r.json()
                results["list_contains_buy_milk"] = any(
                    "buy milk" in str(item) for item in (items if isinstance(items, list) else [items])
                )

            # PUT /api/todos/{id} -> mark done
            if todo_id is not None:
                r = client.put(f"/api/todos/{todo_id}", json={"done": True})
                results["update_todo"] = r.status_code in (200, 204)
                results["update_done"] = (
                    r.status_code == 200 and r.json().get("done") is True
                )

            # DELETE /api/todos/{id}
            if todo_id is not None:
                r = client.delete(f"/api/todos/{todo_id}")
                results["delete_todo"] = r.status_code in (200, 204)
                r = client.get("/api/todos")
                remaining = r.json() if r.status_code == 200 else []
                results["deleted_absent"] = all(
                    item.get("id") != todo_id for item in remaining
                )
    except Exception as exc:
        results["crud_exception"] = False
        results["crud_error"] = str(exc)

    return results


def run_site_tests(workspace: Path) -> None:
    """Fail closed unless the generated site completes the full CRUD contract."""
    results = run_site_crud_tests(workspace)
    required = (
        "app_importable",
        "root_200",
        "root_html",
        "create_todo",
        "list_todos",
        "list_contains_buy_milk",
        "update_todo",
        "update_done",
        "delete_todo",
        "deleted_absent",
    )
    failures = [name for name in required if results.get(name) is not True]
    assert not failures, f"generated todo site failed checks {failures}: {results}"
