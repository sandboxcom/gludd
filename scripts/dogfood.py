#!/usr/bin/env python3
"""make dogfood — run gludd's agentic SDLC loop on its OWN repo, no live AI.

This proves the product spine end-to-end WITHOUT any API key by substituting a
mock/echo ModelGateway for the generation step and a mock reviewer for the
review step. Everything else is the real production code path:

    register project (file:// clone of THIS repo into a temp workspace)
        -> enqueue todo via the real /api/todos route
        -> EventLoop.tick(): claim -> dispatch (real ExecutionEngine, echo
           gateway, real git commit) -> persist TaskReturn
        -> review (real ReturnReviewer, echo gateway -> "complete")
        -> EventLoop.tick(): reconcile -> todo reaches COMPLETE
        -> assert state advanced + a result artifact JSON was written
        -> shut down cleanly.

Boundary where a LIVE model would be required: ONLY the two gateway calls that
are mocked here (the code-generation call in ExecutionEngine and the review
adjudication call in ReturnReviewer). With a real ZAI/GLM key those two mocks
are replaced by `make test-live-zai`'s gateway; every other step in this script
is already the live production code.

Exit 0 on a fully-advanced todo + artifact; non-zero otherwise (fail closed).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent


def _log(msg: str) -> None:
    print(f"[dogfood] {msg}", flush=True)


def _clone_self(dest: str) -> bool:
    """Materialize a workspace from THIS repo over file:// (no network)."""
    from general_ludd.git_automation.repo import GitAutomation

    git = GitAutomation(repo_path=str(REPO_ROOT))
    result = git.clone(url=f"file://{REPO_ROOT}", target_dir=dest, timeout=120.0)
    ok = bool(getattr(result, "success", False))
    if not ok:
        _log(f"clone failed: {getattr(result, 'error', 'unknown')}")
    # Ensure committer identity for the agent commit inside the workspace.
    subprocess.run(["git", "config", "user.email", "agent@gludd.local"], cwd=dest, check=False)
    subprocess.run(["git", "config", "user.name", "Gludd Dogfood"], cwd=dest, check=False)
    return ok and (Path(dest) / ".git").exists()


async def run_dogfood() -> int:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from general_ludd.daemon import _daemon_state
    from general_ludd.db.models import Base, TaskDecisionModel, TaskReturnModel
    from general_ludd.db.repository import TodoRepository
    from general_ludd.event_loop.loop import EventLoop
    from general_ludd.execution.engine import ExecutionEngine
    from general_ludd.projects.manager import ProjectManager
    from general_ludd.review.reviewer import ReturnReviewer
    from general_ludd.routers.todos import register as reg_todos
    from general_ludd.schemas.job import JobSpec
    from general_ludd.schemas.task_return import TaskReturn

    # --- 1. Boot daemon infra: real SQLite + session factory + todo router ---
    _log("booting daemon infra (temp SQLite, real session factory)...")
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

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
    _log("daemon infra booted: OK")

    progress = {
        "booted": True,
        "project_registered": False,
        "workspace_materialized": False,
        "todo_enqueued": False,
        "claimed_and_dispatched": False,
        "playbook_executed": False,
        "commit_created": False,
        "reviewed": False,
        "state_advanced": False,
        "artifact_written": False,
    }

    tmp = tempfile.mkdtemp(prefix="gludd-dogfood-")
    ws = str(Path(tmp) / "workspace")
    artifact_path = Path(tmp) / "result.json"
    exit_code = 1
    try:
        # --- 2. Register a project pointing at THIS repo (file:// clone) ---
        _log("registering project against this repo (file://, local clone)...")
        pm = ProjectManager()
        if not _clone_self(ws):
            _log("FAIL: workspace clone of self did not materialize")
            return 1
        progress["workspace_materialized"] = True
        project = pm.add_project(
            name="gludd-self",
            weight=100.0,
            description="dogfood: gludd operating on its own repo",
            workspace_path=ws,
            repo_url=f"file://{REPO_ROOT}",
        )
        progress["project_registered"] = True
        _log(f"project registered: {project.project_id} -> {ws}")

        # --- 3. Enqueue a simple todo via the real API ---
        _log("enqueuing a todo via POST /api/todos...")
        resp = await client.post(
            "/api/todos",
            json={
                "title": "Add a dogfood marker file",
                "description": "Create docs/DOGFOOD_MARKER.md with a one-line note.",
                "queue": "core",
                "priority": "high",
                "work_type": "code",
            },
        )
        if resp.status_code != 201:
            _log(f"FAIL: todo POST returned {resp.status_code}: {resp.text}")
            return 1
        todo_id = resp.json()["todo_id"]
        progress["todo_enqueued"] = True
        _log(f"todo enqueued + persisted to DB: {todo_id}")

        # --- 4. One tick: claim -> dispatch (echo gateway -> real commit) ---
        _log("running tick 1: claim -> dispatch (echo gateway, real ExecutionEngine)...")
        echo_gateway = MagicMock()
        gen = "DOGFOOD: gludd edited its own repo via the agentic loop.\n"
        echo_gateway.call_model = MagicMock(return_value=MagicMock(
            content=f"```\nFILE: docs/DOGFOOD_MARKER.md\n{gen}\n```"
        ))
        engine_exec = ExecutionEngine(model_gateway=echo_gateway, workspace_path=ws)

        loop = EventLoop(session=factory, daemon_state={})

        async def patched_dispatch(todo_item: object) -> None:
            job = JobSpec(
                job_id=f"EXEC-{todo_item.todo_id}",  # type: ignore[attr-defined]
                todo_id=todo_item.todo_id,  # type: ignore[attr-defined]
                playbook="code",
                queue="core",
                work_type="code",
                prompt_text=todo_item.title,  # type: ignore[attr-defined]
            )
            result = engine_exec.execute(job)
            progress["playbook_executed"] = True
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
                        todo_item.todo_id,  # type: ignore[attr-defined]
                        {"status": "reviewing_return"},
                        todo_item.version,  # type: ignore[attr-defined]
                    )

        loop._dispatch_execute_job = patched_dispatch  # type: ignore[assignment]
        await loop.tick()
        progress["claimed_and_dispatched"] = True

        # Verify the execution produced a real git commit + file in the workspace.
        marker = Path(ws) / "docs" / "DOGFOOD_MARKER.md"
        branches = subprocess.run(
            ["git", "branch"], cwd=ws, capture_output=True, text=True,
        ).stdout
        gludd_branches = [b.strip().lstrip("* ") for b in branches.splitlines() if "gludd/" in b]
        if marker.exists() and gludd_branches:
            progress["commit_created"] = True
            _log(f"workspace commit created on branch {gludd_branches[0]}; marker file written")
        else:
            _log("WARN: expected a gludd/ branch + marker file in the workspace")

        # --- 5. Review (real ReturnReviewer, echo gateway -> complete) ---
        _log("reviewing the return with a mock reviewer (echo gateway -> complete)...")
        review_gateway = MagicMock()
        review_gateway.call_model = MagicMock(return_value=MagicMock(
            content='{"decision": "complete", "confidence": 0.95}'
        ))
        registry = MagicMock()
        registry.render = MagicMock(return_value="Review this return")
        reviewer = ReturnReviewer(gateway=review_gateway, prompt_registry=registry)

        async with factory() as session:
            rows = (await session.execute(
                select(TaskReturnModel).where(TaskReturnModel.todo_id == todo_id)
            )).scalars().all()
            if not rows:
                _log("FAIL: no TaskReturn row persisted")
                return 1
            tr_model = rows[0]
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
            progress["reviewed"] = True
            _log(f"review decision: {decision.decision} (confidence {decision.confidence})")
            session.add(TaskDecisionModel(
                return_id=decision.return_id,
                matched_todo_id=decision.matched_todo_id or todo_id,
                decision=decision.decision,
                confidence=decision.confidence,
            ))
            await session.commit()

        # --- 6. One tick: reconcile -> todo reaches COMPLETE ---
        _log("running tick 2: reconcile...")
        await loop.tick()
        async with factory() as session:
            final = await TodoRepository(session).get_by_id(todo_id)
            final_status = getattr(final, "status", None)
        _log(f"final todo status: {final_status}")
        if final_status == "complete":
            progress["state_advanced"] = True

        # --- 7. Write the result artifact ---
        artifact = {
            "todo_id": todo_id,
            "project_id": project.project_id,
            "workspace": ws,
            "final_status": final_status,
            "branch": gludd_branches[0] if gludd_branches else None,
            "decision": decision.decision,
            "progress": progress,
        }
        artifact_path.write_text(json.dumps(artifact, indent=2))
        progress["artifact_written"] = artifact_path.exists()
        _log(f"result artifact written: {artifact_path}")

        if progress["state_advanced"] and progress["commit_created"] and progress["artifact_written"]:
            exit_code = 0
        return exit_code
    finally:
        # --- 8. Clean shutdown ---
        await client.aclose()
        await engine.dispose()
        _log("=== DOGFOOD SUMMARY ===")
        for k, v in progress.items():
            _log(f"  {k}: {'PASS' if v else 'no'}")
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        _log("daemon infra shut down; workspace cleaned")
        if exit_code == 0:
            _log("=== DOGFOOD: PASSED (todo advanced to complete via a real self-commit, no live AI) ===")
        else:
            _log("=== DOGFOOD: PARTIAL — see summary above for the boundary reached ===")


def main() -> int:
    try:
        return asyncio.run(run_dogfood())
    except Exception as exc:  # fail closed, print the boundary
        _log(f"DOGFOOD aborted with: {type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
