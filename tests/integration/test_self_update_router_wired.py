"""Integration tests for the self-update router wiring (Phase 2 Step 7, #81).

Exercises the FULL daemon stack (``create_daemon_app`` + lifespan + real SQLite
DB + ``AuditEventRepository`` + ``TodoRepository``) to prove the
``/admin/self-update/*`` endpoints are correctly wired into the daemon:

  1. ``POST /admin/self-update/plan`` config-tier request -> outcome
     ``"applied"`` AND an audit row is persisted to ``audit_events``.
  2. Same endpoint with a protected-path plan -> outcome ``"refused"``
     (the apply ladder's fail-closed guard fires through the router).
  3. ``POST /admin/self-update/enqueue`` -> a todo is persisted with
     ``queue="self_update"``.
  4. Missing PSK on the protected ``/admin/*`` path -> ``401``.

Tests 1-4 do NOT depend on Phase 2 Steps 5/6 (scheduler wiring) — they verify
the router's contract against the classifier + apply ladder + audit/todo
repositories only.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import aiosqlite
import pytest
from fastapi.testclient import TestClient

import general_ludd.daemon as daemon_mod
from general_ludd.daemon import create_daemon_app
from general_ludd.self_update.model import (
    ApplyTier,
    ChangeKind,
    SelfUpdatePlan,
    Subsystem,
)


@pytest.fixture(autouse=True)
def _reset_daemon_state() -> Iterator[None]:
    original_state = daemon_mod._daemon_state

    def reset_current_state() -> None:
        if daemon_mod._daemon_state is None:
            daemon_mod._daemon_state = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
            return
        daemon_mod._daemon_state["todos"] = []
        daemon_mod._daemon_state["tick_metrics"] = {}
        daemon_mod._daemon_state.setdefault("quality_gate", {})

    reset_current_state()
    yield
    reset_current_state()
    if original_state is None:
        daemon_mod._daemon_state = None


def _make_db_config(tmp_path: Path) -> tuple[str, str]:
    """Write a config dir whose ``general-ludd.yml`` points at a temp SQLite DB.

    Returns ``(config_dir, db_path)``.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    db_path = tmp_path / "test.db"
    (config_dir / "general-ludd.yml").write_text(
        f"database:\n  url: 'sqlite+aiosqlite:///{db_path}'\n"
    )
    return str(config_dir), str(db_path)


async def _seed_default_project(app: Any) -> None:
    """Seed the ``default`` project via the daemon's own async session factory.

    ``_build_self_update_audit_sink`` hardcodes ``project_id="default"`` on every
    audit write (see ``daemon.py``). The ``audit_events.project_id`` column has
    a FK to ``projects.project_id`` with ``PRAGMA foreign_keys=ON``, so the row
    must exist or the audit write silently fails (the sink is fail-soft).

    Using the daemon's own session factory (not an external ``sqlite3`` conn)
    guarantees the row lands on the same connection pool the audit sink uses,
    avoiding WAL-snapshot isolation issues across separate connections.
    """
    from sqlalchemy import select

    from general_ludd.db.models import ProjectModel

    factory = app.state._session_factory
    async with factory() as session:
        existing = await session.execute(
            select(ProjectModel).where(ProjectModel.project_id == "default")
        )
        if existing.scalar_one_or_none() is None:
            session.add(ProjectModel(project_id="default", name="Default project"))
            await session.commit()


def _count_audit_rows(db_path: str, event_prefix: str = "self_update_") -> int:
    """Return the count of ``audit_events`` rows matching ``event_prefix``."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE event_type LIKE ?",
            (f"{event_prefix}%",),
        )
        return int(cur.fetchone()[0])
    finally:
        conn.close()


def _wait_for_audit_row(db_path: str, timeout: float = 3.0) -> int:
    """Poll ``audit_events`` until a ``self_update_*`` row appears or timeout.

    The audit sink writes asynchronously (``asyncio.create_task`` inside the
    request handler), so the row is not visible the instant the HTTP response
    returns. Poll with a short interval so the test does not flake.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _count_audit_rows(db_path) > 0:
            return _count_audit_rows(db_path)
        time.sleep(0.05)
    return _count_audit_rows(db_path)


class TestSelfUpdateRouterWired:
    """Verify the /admin/self-update/* endpoints are wired through the daemon."""

    def test_plan_config_tier_applied_writes_audit_row(self, tmp_path: Path) -> None:
        """Config-tier request: outcome ``applied`` + audit row persisted.

        A simple ``"set the spend limit to 50"`` routes via the real classifier
        to subsystem=SPEND, change_kind=VALUE_EDIT, tier=CONFIG, targeting
        ``config/general-ludd.yml`` (not a protected path). The apply ladder
        auto-applies (hot-reloadable, no approval needed) and the audit sink
        writes a ``self_update_applied`` row to ``audit_events``.
        """
        config_dir, db_path = _make_db_config(tmp_path)
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=300.0, config_dir=config_dir)
            with TestClient(app) as client:
                asyncio.run(_seed_default_project(app))
                assert app.state._session_factory is not None

                resp = client.post(
                    "/admin/self-update/plan",
                    json={
                        "raw_text": "set the spend limit to 50",
                        "requested_by": "operator",
                    },
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["outcome"] == "applied"
                assert data["applied"] is True

                count = _wait_for_audit_row(db_path)
                assert count >= 1, (
                    "expected at least one self_update_* audit row after an "
                    "applied config-tier plan, but audit_events has none"
                )

    def test_plan_protected_path_is_refused(self, tmp_path: Path) -> None:
        """A protected-path plan: outcome ``refused`` (fail-closed guard).

        The real classifier never routes to a protected guard file from keyword
        text alone (it only suggests non-protected config/template targets).
        So this test patches ``classify`` to return a plan that targets a
        protected path (``security/capability_lattice.py`` — matches
        ``PROTECTED_PATH_SUBSTRINGS``) and verifies the apply ladder's
        fail-closed refusal propagates through the router unchanged. This
        exercises the router -> apply_plan -> response wiring for the REFUSED
        path, which is the integration contract under test.
        """
        config_dir, _db_path = _make_db_config(tmp_path)
        protected_plan = SelfUpdatePlan(
            subsystem=Subsystem.CONFIG,
            change_kind=ChangeKind.VALUE_EDIT,
            target_files=("src/general_ludd/security/capability_lattice.py",),
            apply_tier=ApplyTier.CONFIG,
            requires_approval=False,
            rationale="routes to a protected guard file",
            confidence=0.5,
        )
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ), patch(
            "general_ludd.routers.self_update.classify",
            return_value=protected_plan,
        ):
            app = create_daemon_app(tick_interval=300.0, config_dir=config_dir)
            with TestClient(app) as client:
                resp = client.post(
                    "/admin/self-update/plan",
                    json={
                        "raw_text": "edit the capability lattice",
                        "requested_by": "operator",
                    },
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["outcome"] == "refused"

    def test_protected_plan_closes_every_driver_connection(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Self-update requests leave no SQLite owner after app shutdown."""
        created_connections: list[Any] = []
        original_connect = aiosqlite.connect

        def tracked_connect(*args: Any, **kwargs: Any) -> Any:
            connection = original_connect(*args, **kwargs)
            created_connections.append(connection)
            return connection

        monkeypatch.setattr(aiosqlite, "connect", tracked_connect)
        config_dir, _db_path = _make_db_config(tmp_path)
        protected_plan = SelfUpdatePlan(
            subsystem=Subsystem.CONFIG,
            change_kind=ChangeKind.VALUE_EDIT,
            target_files=("src/general_ludd/security/capability_lattice.py",),
            apply_tier=ApplyTier.CONFIG,
            requires_approval=False,
            rationale="routes to a protected guard file",
            confidence=0.5,
        )
        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=MagicMock(),
            ),
            patch(
                "general_ludd.routers.self_update.classify",
                return_value=protected_plan,
            ),
        ):
            app = create_daemon_app(tick_interval=300.0, config_dir=config_dir)
            with TestClient(app) as client:
                response = client.post(
                    "/admin/self-update/plan",
                    json={
                        "raw_text": "edit the capability lattice",
                        "requested_by": "operator",
                    },
                )
                assert response.status_code == 200

        assert created_connections
        assert all(
            connection._connection is None
            for connection in created_connections
        )

    def test_enqueue_persists_todo_with_self_update_queue(
        self, tmp_path: Path
    ) -> None:
        """Enqueue endpoint persists a todo with ``queue="self_update"``.

        The real classifier + ``to_todo_spec`` build a backlog todo spec, and
        the router persists it via ``TodoRepository.create`` + commit. The row
        must land in the ``todos`` table with the ``self_update`` queue so the
        scheduler (once wired in Step 6) can pick it up.
        """
        config_dir, db_path = _make_db_config(tmp_path)
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=300.0, config_dir=config_dir)
            with TestClient(app) as client:
                resp = client.post(
                    "/admin/self-update/enqueue",
                    json={
                        "raw_text": "set the spend limit to 50",
                        "requested_by": "operator",
                    },
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "ok", f"enqueue returned error: {data}"
                assert data["persisted"] is True
                assert data["spec"]["queue"] == "self_update"
                todo_id = data["todo_id"]
                assert todo_id, "todo_id must be returned for a persisted todo"

                conn = sqlite3.connect(db_path)
                try:
                    cur = conn.execute(
                        "SELECT queue, title FROM todos WHERE todo_id = ?",
                        (todo_id,),
                    )
                    row = cur.fetchone()
                finally:
                    conn.close()
                assert row is not None, (
                    f"todo {todo_id} was not persisted to the todos table"
                )
                assert row[0] == "self_update"
                assert "self-update" in row[1]

    def test_missing_psk_returns_401(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A request without a valid PSK Bearer token is rejected with 401.

        When ``GLUDD_AUTH_PSK`` is set, the daemon's auth middleware challenges every
        non-public path (``/admin/self-update/plan`` is NOT in
        ``_PUBLIC_PATHS``). A request missing the ``Authorization`` header must
        get ``401`` — the standard PSK-gate contract every other ``/admin/*``
        router already satisfies.
        """
        monkeypatch.setenv("GLUDD_AUTH_PSK", "test-secret-key")
        config_dir, _db_path = _make_db_config(tmp_path)
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=300.0, config_dir=config_dir)
            with TestClient(app) as client:
                resp = client.post(
                    "/admin/self-update/plan",
                    json={"raw_text": "anything"},
                )
                assert resp.status_code == 401
