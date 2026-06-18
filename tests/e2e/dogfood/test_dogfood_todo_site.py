"""E2E: Greenfield "build a todo website" dogfood scenario.

Flow (when ZAI_API_KEY is present):
  1. Create a temp directory + git-init an EMPTY workspace (greenfield — no clone).
  2. Boot an in-process FastAPI app backed by an in-memory SQLite database.
  3. Register a gludd project pointing at the empty workspace.
  4. POST /api/todos to seed 4 tasks that collectively ask gludd to build a
     FastAPI todo website with CRUD and a minimal HTML front-end.
  5. For each todo: patch EventLoop._dispatch_execute_job → ExecutionEngine.execute
     (the documented monkeypatch from scripts/dogfood.py), run tick (dispatch) →
     review → tick (reconcile).
  6. Assert each todo reaches status=="complete" and a gludd/* branch exists.
  7. Run site tests (in-process TestClient CRUD round-trip).
  8. Teardown: delete the temp workspace + dispose the in-memory DB.

When ZAI_API_KEY is absent the test SKIPS cleanly at the top-level guard.
All heavy imports (gludd, sqlalchemy, langchain) are deferred inside the test
body so collection is import-safe.

Markers: @pytest.mark.e2e
Skip guard: skip_unless_zai() — skips when ZAI_API_KEY is unset.

TODO(dispatch_mode): Replace loop._dispatch_execute_job monkeypatch with a
first-class dispatch_mode='inproc_engine' on ProjectWeight so this is a
supported API rather than a test hack. See DESIGN §11 gap 1.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Skip guard — MUST be called first thing in the test body so collection
# never triggers the heavy gludd imports in CI without a key.
# ---------------------------------------------------------------------------

def _skip_unless_zai() -> None:
    """Skip this test when no ZAI_API_KEY is available.

    Checks both .secrets/llm_keys.env and the environment variable.
    This is intentionally a plain function (not a fixture) so it can be
    called as the very first statement in the test body, before any imports
    that would fail without the key infrastructure in place.
    """
    from tests.e2e.dogfood._secrets import load_llm_keys
    creds = load_llm_keys()
    if creds is None:
        pytest.skip(
            "ZAI_API_KEY not found in .secrets/llm_keys.env or environment — "
            "live z.ai dogfood tests skipped.  "
            "To run: add ZAI_API_KEY=<key> to .secrets/llm_keys.env"
        )


# ---------------------------------------------------------------------------
# Offline scaffold content — what the mock gateway emits when no key is present.
# This is the deterministic FastAPI app so site tests can run offline.
# NOTE: In this test we skip offline (no key → skip), but the scaffold content
# is defined here for use in integration tests that want offline mode.
# ---------------------------------------------------------------------------

_MOCK_GATEWAY_RESPONSE = """\
Here is the todo website implementation.

FILE: app/__init__.py


FILE: app/main.py
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
        "<html><body><h1>Todo List</h1>"
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git_init_workspace(ws: Path) -> None:
    """Initialise an empty git repo in ws and configure a committer identity."""
    subprocess.run(["git", "init", str(ws)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "agent@gludd.local"],
        cwd=str(ws), check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Gludd Dogfood"],
        cwd=str(ws), check=True, capture_output=True,
    )
    # Add an initial empty commit so gludd can branch off HEAD
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "initial empty commit"],
        cwd=str(ws), check=True, capture_output=True,
    )


def _gludd_branches(ws: Path) -> list[str]:
    """Return a list of branch names matching 'gludd/*' in the workspace."""
    result = subprocess.run(
        ["git", "branch"], cwd=str(ws), capture_output=True, text=True,
    )
    return [
        b.strip().lstrip("* ")
        for b in result.stdout.splitlines()
        if "gludd/" in b
    ]


async def _run_todo_to_completion(
    *,
    loop: Any,
    engine_exec: Any,
    factory: Any,
    reviewer: Any,
    todo_id: str,
    todo_version: int,
    progress: list[str],
) -> bool:
    """Dispatch one todo through the full gludd spine and reconcile to completion.

    Uses the documented _dispatch_execute_job monkeypatch (scripts/dogfood.py).
    Returns True if the todo reached 'complete'.

    TODO(dispatch_mode): Replace with dispatch_mode='inproc_engine' once a
    first-class supported API exists. See DESIGN §11 gap 1.
    """
    import asyncio

    from sqlalchemy import select

    from general_ludd.db.models import TaskDecisionModel, TaskReturnModel
    from general_ludd.db.repository import TodoRepository
    from general_ludd.schemas.job import JobSpec
    from general_ludd.schemas.task_return import TaskReturn

    dispatched_todo: dict[str, Any] = {}

    async def patched_dispatch(todo_item: Any) -> None:
        """In-process dispatch: run ExecutionEngine.execute instead of Ansible."""
        job = JobSpec(
            job_id=f"EXEC-{todo_item.todo_id}",
            todo_id=todo_item.todo_id,
            playbook="code",
            queue="core",
            work_type="code",
            prompt_text=todo_item.title,
        )
        result = engine_exec.execute(job)
        dispatched_todo["result"] = result
        dispatched_todo["todo_id"] = todo_item.todo_id
        dispatched_todo["version"] = todo_item.version
        # Persist the TaskReturn row so the reviewer can find it
        if loop._task_return_repo is not None:
            await loop._task_return_repo.create(data={
                "return_id": result.return_id,
                "todo_id": result.todo_id,
                "job_id": result.job_id,
                "playbook": result.playbook,
                "queue": result.queue,
                "exit_code": result.exit_code,
                "result_summary": result.result_summary,
            })
            if loop._todo_repo is not None:
                await loop._todo_repo.update(
                    todo_item.todo_id,
                    {"status": "reviewing_return"},
                    todo_item.version,
                )

    loop._dispatch_execute_job = patched_dispatch

    # Tick 1: claim → dispatch
    await asyncio.wait_for(loop.tick(), timeout=120.0)

    if not dispatched_todo:
        progress.append(f"WARNING: todo {todo_id} was not dispatched on tick 1")
        return False

    actual_todo_id = dispatched_todo.get("todo_id", todo_id)

    # Review the return
    async with factory() as session:
        rows = (await session.execute(
            select(TaskReturnModel).where(TaskReturnModel.todo_id == actual_todo_id)
        )).scalars().all()
        if not rows:
            progress.append(f"FAIL: no TaskReturn row for todo {actual_todo_id}")
            return False
        tr_model = rows[-1]  # latest
        tr = TaskReturn(
            return_id=tr_model.return_id,
            todo_id=tr_model.todo_id,
            job_id=tr_model.job_id,
            playbook=tr_model.playbook,
            queue=tr_model.queue,
            exit_code=tr_model.exit_code,
            result_summary=tr_model.result_summary,
        )
        decision = reviewer.review_return(tr, [], [])
        session.add(TaskDecisionModel(
            return_id=decision.return_id,
            matched_todo_id=decision.matched_todo_id or actual_todo_id,
            decision=decision.decision,
            confidence=decision.confidence,
        ))
        await session.commit()

    progress.append(
        f"todo {actual_todo_id}: review decision={decision.decision} "
        f"confidence={decision.confidence:.2f}"
    )

    # Tick 2: reconcile → COMPLETE
    await asyncio.wait_for(loop.tick(), timeout=30.0)

    async with factory() as session:
        final = await TodoRepository(session).get_by_id(actual_todo_id)
        final_status = getattr(final, "status", None)

    progress.append(f"todo {actual_todo_id}: final status={final_status}")
    return final_status == "complete"


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------

@pytest.mark.e2e
@pytest.mark.asyncio
async def test_dogfood_greenfield_todo_website() -> None:
    """Greenfield todo-website: seed → run via gludd engine → site CRUD tests.

    Skips cleanly when ZAI_API_KEY is not present (CI without secrets).

    Flow:
      1. Skip guard: ZAI_API_KEY must be set.
      2. Create temp workspace (greenfield git-init, no clone).
      3. Boot in-process FastAPI + in-memory SQLite.
      4. Register project + seed 4 todos.
      5. For each todo: dispatch (monkeypatched) → review → reconcile.
      6. Assert all todos complete + gludd/* branch exists.
      7. SITE TESTS: import generated app.main, TestClient CRUD round-trip.
      8. Teardown (finally: rmtree + engine.dispose).
    """
    # --- 0. Skip guard (must be FIRST — no heavy imports above this line) ---
    _skip_unless_zai()

    # All heavy imports are inside the test body, below the skip guard.
    import shutil

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from general_ludd.daemon import _daemon_state
    from general_ludd.db.models import Base
    from general_ludd.db.repository import TodoRepository
    from general_ludd.event_loop.loop import EventLoop
    from general_ludd.execution.engine import ExecutionEngine
    from general_ludd.projects.manager import ProjectManager
    from general_ludd.review.reviewer import ReturnReviewer
    from general_ludd.routers.todos import register as reg_todos
    from tests.e2e.dogfood._gateway import build_gateway
    from tests.e2e.dogfood._secrets import load_llm_keys
    from tests.e2e.dogfood._site import run_site_tests

    # --- 1. Load credentials (already validated by _skip_unless_zai, but we
    #        need them to build the gateway) ---
    zai_creds = load_llm_keys()
    assert zai_creds is not None  # guaranteed by skip guard above

    # --- 2. Temp workspace: empty git-init (greenfield, no clone) ---
    tmp = tempfile.mkdtemp(prefix="gludd-todosite-")
    ws = Path(tmp) / "workspace"
    ws.mkdir()

    try:
        _git_init_workspace(ws)

        # --- 3. In-process FastAPI + in-memory SQLite ---
        engine_db = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine_db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine_db, expire_on_commit=False)

        _daemon_state["todos"] = []
        app = FastAPI()
        app.state._session_factory = factory
        app.state._config_dir = None
        app.state._startup_config = {}
        app.state.log_level = "info"
        app.state.tick_interval = 1.0
        app.state.event_loop = None
        app.state._templates_dir = None
        app.state._playbooks_dir = None
        reg_todos(app, _daemon_state)
        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://test")

        try:
            # --- 4a. Register the project ---
            pm = ProjectManager()
            project = pm.add_project(
                name="todo-site-dogfood",
                weight=100.0,
                description="Greenfield: gludd builds a FastAPI todo website",
                workspace_path=str(ws),
                # No repo_url — greenfield, gludd builds from scratch
            )
            print(f"[dogfood-site] project registered: {project.project_id} -> {ws}")

            # --- 4b. Seed todos via POST /api/todos ---
            todos_to_seed = [
                {
                    "title": (
                        "Scaffold a FastAPI todo-website backend at app/main.py "
                        "with an in-memory dict store and GET / serving an HTML page "
                        "that lists todos and has an add-form."
                    ),
                    "description": (
                        "Create app/__init__.py (empty) and app/main.py with a FastAPI "
                        "app object. GET / must return HTML with a <ul id='todo-list'> "
                        "and <form id='add-form'>. Use FastAPI + HTMLResponse. "
                        "No database needed — use a module-level dict _store."
                    ),
                    "queue": "core",
                    "priority": "high",
                    "work_type": "code",
                },
                {
                    "title": (
                        "Add REST CRUD endpoints to app/main.py: "
                        "GET /api/todos, POST /api/todos (status 201), "
                        "PUT /api/todos/{id}, DELETE /api/todos/{id}."
                    ),
                    "description": (
                        "All endpoints return JSON. POST body and PUT body are plain "
                        "dicts (use dict[str, Any] type hint). Each todo item must "
                        "have at least 'id' (int), 'title' (str), 'done' (bool) keys. "
                        "The in-memory _store is a dict[int, dict[str, Any]]."
                    ),
                    "queue": "core",
                    "priority": "high",
                    "work_type": "code",
                },
                {
                    "title": (
                        "Serve a minimal index.html in app/main.py GET / with a form "
                        "to add a todo and a list with delete buttons calling the API."
                    ),
                    "description": (
                        "GET / must return HTMLResponse with HTML containing the word "
                        "'Todo' and a <form> element. Embed all HTML inline in main.py "
                        "(no separate template file needed)."
                    ),
                    "queue": "core",
                    "priority": "medium",
                    "work_type": "code",
                },
                {
                    "title": (
                        "Add tests/test_site.py with pytest + httpx TestClient covering "
                        "create / list / update-complete / delete."
                    ),
                    "description": (
                        "Use 'from starlette.testclient import TestClient' and import "
                        "the app from app.main. Test: POST /api/todos -> 201, "
                        "GET /api/todos -> list contains item, "
                        "PUT /api/todos/{id} -> done=True, "
                        "DELETE /api/todos/{id} -> 200 and gone."
                    ),
                    "queue": "core",
                    "priority": "medium",
                    "work_type": "code",
                },
            ]

            seeded_ids: list[str] = []
            for todo_data in todos_to_seed:
                resp = await client.post("/api/todos", json=todo_data)
                assert resp.status_code == 201, (
                    f"POST /api/todos failed {resp.status_code}: {resp.text}"
                )
                todo_id = resp.json()["todo_id"]
                seeded_ids.append(todo_id)
                print(f"[dogfood-site] todo seeded: {todo_id} — {todo_data['title'][:60]}...")

            # --- 5. Build gateway + execution engine ---
            # Live mode: real zai/GLM calls to generate the website code.
            gateway, mode = build_gateway(zai_creds)
            print(f"[dogfood-site] gateway mode: {mode}")

            engine_exec = ExecutionEngine(
                model_gateway=gateway,
                workspace_path=str(ws),
            )

            # Mock review gateway → always returns "complete" so the reconcile
            # phase advances the todo status regardless of code quality.
            # The site tests (step 7) are the real quality gate.
            review_gateway = MagicMock()
            review_gateway.call_model = MagicMock(
                return_value=MagicMock(
                    content='{"decision": "complete", "confidence": 0.95}'
                )
            )
            prompt_registry = MagicMock()
            prompt_registry.render = MagicMock(
                return_value="Review this return and decide complete/retry"
            )
            reviewer = ReturnReviewer(
                gateway=review_gateway,
                prompt_registry=prompt_registry,
            )

            loop = EventLoop(session=factory, daemon_state={})
            progress: list[str] = []

            # --- 6. Run each todo through dispatch → review → reconcile ---
            all_complete = True
            for idx, todo_id in enumerate(seeded_ids):
                print(
                    f"[dogfood-site] processing todo {idx+1}/{len(seeded_ids)}: {todo_id}"
                )
                # Look up the current version for the update call inside the helper
                async with factory() as sess:
                    todo_obj = await TodoRepository(sess).get_by_id(todo_id)
                    todo_version = getattr(todo_obj, "version", 1)

                ok = await _run_todo_to_completion(
                    loop=loop,
                    engine_exec=engine_exec,
                    factory=factory,
                    reviewer=reviewer,
                    todo_id=todo_id,
                    todo_version=todo_version,
                    progress=progress,
                )
                if not ok:
                    all_complete = False
                    print(f"[dogfood-site] WARNING: todo {todo_id} did not complete")

            print("[dogfood-site] progress log:\n  " + "\n  ".join(progress))

            assert all_complete, (
                "Not all todos reached 'complete'. Progress:\n"
                + "\n".join(f"  {p}" for p in progress)
            )

            # --- Verify at least one gludd/* branch was created ---
            branches = _gludd_branches(ws)
            assert branches, (
                f"Expected a 'gludd/*' branch in {ws} after dispatch, got none. "
                "Check ExecutionEngine git commit path."
            )
            print(f"[dogfood-site] gludd branches created: {branches}")

            # --- 7. Site tests: import generated app, run CRUD round-trip ---
            print(f"[dogfood-site] running site tests against generated website in {ws}")
            run_site_tests(ws)
            print("[dogfood-site] SITE TESTS PASSED: CRUD round-trip verified")

        finally:
            await client.aclose()
            await engine_db.dispose()
            print("[dogfood-site] in-proc engine + client disposed")

    finally:
        # --- 8. Teardown: delete temp workspace ---
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"[dogfood-site] temp workspace deleted: {tmp}")
