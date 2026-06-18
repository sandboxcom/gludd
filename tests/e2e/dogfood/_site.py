"""Site-test helpers: import-and-drive a generated FastAPI todo app."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


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
                    spec.loader.exec_module(mod)  # type: ignore[union-attr]
                    # Look for 'app' attribute (FastAPI convention)
                    return getattr(mod, "app", mod)
                finally:
                    if added:
                        sys.path.remove(pkg_dir)
    return None


def run_site_crud_tests(workspace: Path) -> dict[str, bool]:
    """Run CRUD round-trip against the generated site using starlette TestClient.

    Returns a dict of {test_name: passed}.
    Skips gracefully if starlette or the app is not importable.
    """
    results: dict[str, bool] = {}
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
            if todo_id:
                r = client.put(f"/api/todos/{todo_id}", json={"done": True})
                results["update_todo"] = r.status_code in (200, 204)

            # DELETE /api/todos/{id}
            if todo_id:
                r = client.delete(f"/api/todos/{todo_id}")
                results["delete_todo"] = r.status_code in (200, 204)
    except Exception as exc:
        results["crud_exception"] = False
        results["crud_error"] = str(exc)

    return results
