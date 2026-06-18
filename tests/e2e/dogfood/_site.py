"""Site-test helpers for the greenfield todo-website E2E scenario.

These functions verify that the generated website artifacts work — the site
serves an HTML page and a CRUD API.  The test is driven in-process via
starlette.testclient.TestClient so no real port is needed.

See docs/e2e_harness/DESIGN_native_dogfood_harness.md §8.4 for the design.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Offline scaffold — emitted by the mock gateway when no key is present.
# Must be a valid FastAPI app that passes the CRUD assertions below.
# ---------------------------------------------------------------------------

OFFLINE_SCAFFOLD_APP = """\
\"\"\"Offline todo-website scaffold written by the dogfood mock gateway.\"\"\"
from __future__ import annotations
from typing import Any
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

app = FastAPI()
_store: dict[int, dict[str, Any]] = {}
_next_id: int = 1


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    items = "".join(
        f"<li id=\\"todo-{t['id']}\\">{t['title']}</li>" for t in _store.values()
    )
    return (
        "<html><body>"
        "<h1>Todo List</h1>"
        f"<ul id=\\"todo-list\\">{items}</ul>"
        "<form id=\\"add-form\\"><input name=\\"title\\" /><button>Add</button></form>"
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
"""


def write_offline_scaffold(workspace: Path) -> None:
    """Write the deterministic FastAPI scaffold into the workspace.

    Called when running in mock/offline mode so the site tests always exercise
    the harness machinery even without a live model key.
    """
    app_dir = workspace / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "main.py").write_text(OFFLINE_SCAFFOLD_APP, encoding="utf-8")


def _import_app_from_workspace(workspace: Path) -> Any:
    """Import the generated FastAPI app object from the workspace.

    Tries the most common entry-point locations produced by the model:
      1. workspace/app/main.py  (seeded contract in the todo descriptions)
      2. workspace/main.py      (fallback if model ignores the sub-package)

    Returns the FastAPI ``app`` attribute, or raises ImportError with a clear
    message if neither is found.
    """
    candidates = [
        workspace / "app" / "main.py",
        workspace / "main.py",
    ]
    for entry in candidates:
        if not entry.exists():
            continue
        # Use a unique module name per workspace to avoid sys.modules collisions
        # across parametrized test runs.
        mod_name = f"_dogfood_site_{workspace.name}_{entry.stem}"
        spec = importlib.util.spec_from_file_location(mod_name, entry)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        try:
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except Exception as exc:
            del sys.modules[mod_name]
            raise ImportError(
                f"Failed to import generated app from {entry}: {exc}"
            ) from exc
        if not hasattr(module, "app"):
            del sys.modules[mod_name]
            continue
        return module.app
    raise ImportError(
        f"Could not find a FastAPI 'app' object in the workspace at {workspace}. "
        "Checked: " + ", ".join(str(c) for c in candidates)
    )


def run_site_tests(workspace: Path) -> None:
    """Run CRUD round-trip site tests against the generated website.

    Uses starlette.testclient.TestClient (in-process, no real port).
    Asserts:
      - GET /  →  200, HTML contains a todo form / list element
      - POST /api/todos {"title": "buy milk"}  →  201, returns an id
      - GET /api/todos  →  list contains "buy milk"
      - PUT /api/todos/{id} {"done": true}  →  200, reflects completion
      - DELETE /api/todos/{id}  →  200; subsequent GET no longer lists it

    Raises AssertionError on any failure so the calling test sees a clear
    failure reason.
    """
    from starlette.testclient import TestClient

    app = _import_app_from_workspace(workspace)

    with TestClient(app, raise_server_exceptions=True) as client:
        # --- GET / must serve an HTML page ---
        resp = client.get("/")
        assert resp.status_code == 200, f"GET / returned {resp.status_code}"
        body_lower = resp.text.lower()
        assert (
            "<html" in body_lower or "<!doctype" in body_lower
        ), f"GET / did not return HTML: {resp.text[:200]}"

        # --- CREATE ---
        resp = client.post("/api/todos", json={"title": "buy milk"})
        assert resp.status_code in (200, 201), (
            f"POST /api/todos returned {resp.status_code}: {resp.text}"
        )
        created = resp.json()
        assert "id" in created, f"POST /api/todos did not return an id: {created}"
        todo_id = created["id"]

        # --- LIST (contains the new item) ---
        resp = client.get("/api/todos")
        assert resp.status_code == 200, f"GET /api/todos returned {resp.status_code}"
        items = resp.json()
        assert isinstance(items, list), f"GET /api/todos did not return a list: {items}"
        titles = [str(item.get("title", "")) for item in items]
        assert any("buy milk" in t for t in titles), (
            f"GET /api/todos list does not contain 'buy milk': {titles}"
        )

        # --- UPDATE (mark done) ---
        resp = client.put(f"/api/todos/{todo_id}", json={"done": True})
        assert resp.status_code == 200, (
            f"PUT /api/todos/{todo_id} returned {resp.status_code}: {resp.text}"
        )
        updated = resp.json()
        assert updated.get("done") is True, (
            f"PUT /api/todos/{todo_id} did not mark done=True: {updated}"
        )

        # --- DELETE ---
        resp = client.delete(f"/api/todos/{todo_id}")
        assert resp.status_code == 200, (
            f"DELETE /api/todos/{todo_id} returned {resp.status_code}: {resp.text}"
        )

        # --- CONFIRM DELETION ---
        resp = client.get("/api/todos")
        assert resp.status_code == 200
        remaining = resp.json()
        remaining_ids = [item.get("id") for item in remaining]
        assert todo_id not in remaining_ids, (
            f"Deleted todo id={todo_id} is still in GET /api/todos: {remaining_ids}"
        )
