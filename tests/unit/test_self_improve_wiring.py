from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.agents.behavior import AgentBehavior, BehaviorRenderer
from general_ludd.daemon import create_daemon_app
from general_ludd.event_loop.loop import EventLoop


@pytest.fixture
def app(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    return create_daemon_app(config_dir=str(config_dir))


@pytest.fixture
def transport(app):
    return ASGITransport(app=app)


async def _attach_inmemory_db(app):
    """Give ``app`` a real in-memory session factory on ``app.state`` and return
    ``(engine, factory)``. The self-improve admin routes read
    ``app.state._session_factory``; ASGITransport does not run lifespan, so the
    factory must be attached explicitly. Caller must ``await engine.dispose()``.
    """
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    from general_ludd.db.session import (
        create_async_session_factory,
        ensure_tables,
    )

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    await ensure_tables(engine)
    factory = create_async_session_factory(engine)
    app.state._session_factory = factory
    return engine, factory


class TestSelfImproveAnalyzeEndpoint:
    @pytest.mark.asyncio
    async def test_analyze_returns_findings(self, transport):
        with patch(
            "general_ludd.routers.self_improve.SelfImprovementHarness"
        ) as MockHarness:
            instance = MockHarness.return_value
            instance.run_gap_analysis.return_value = [
                {"type": "dead_code", "file": "src/mod.py", "severity": "medium",
                 "message": "Foo has no callers"},
            ]
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/admin/self-improve/analyze")
                assert resp.status_code == 200
                data = resp.json()
                assert data["findings_count"] == 1
                assert data["findings"][0]["type"] == "dead_code"


class TestSelfImproveRunEndpoint:
    @pytest.mark.asyncio
    async def test_run_full_cycle(self, transport):
        with patch(
            "general_ludd.routers.self_improve.SelfImprovementHarness"
        ) as MockHarness:
            instance = MockHarness.return_value
            instance.run_full_cycle.return_value = {
                "findings_count": 2,
                "todos_generated": 2,
                "todos_enqueued": 2,
                "findings": [
                    {"type": "missing_tests", "file": "a.py", "severity": "high",
                     "message": "no tests"},
                ],
                "todos": [
                    {"title": "Add tests for a.py", "work_type": "test",
                     "priority": "high"},
                ],
            }
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/admin/self-improve/run")
                assert resp.status_code == 200
                data = resp.json()
                assert data["findings_count"] == 2
                assert data["todos_enqueued"] == 2

    @pytest.mark.asyncio
    async def test_run_persists_todos_behind_human_approval_gate(self, app):
        """Task #22: harness todos persisted by /run must land APPROVAL_REQUIRED
        with work_type=self_improve (subject to the human gate + visible to
        /approvals), NOT plain BACKLOG / auto-executing."""
        from general_ludd.db.repository import TodoRepository
        from general_ludd.schemas.todo import TodoStatus

        engine, factory = await _attach_inmemory_db(app)
        try:
            with patch(
                "general_ludd.routers.self_improve.SelfImprovementHarness"
            ) as MockHarness:
                MockHarness.return_value.run_full_cycle.return_value = {
                    "findings_count": 1,
                    "todos_generated": 1,
                    "todos_enqueued": 1,
                    "findings": [
                        {"type": "missing_tests", "file": "a.py",
                         "severity": "high", "message": "no tests"},
                    ],
                    "todos": [
                        {"title": "Add tests for a.py", "work_type": "test",
                         "priority": "high"},
                    ],
                }
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.post("/admin/self-improve/run")
                    assert resp.status_code == 200
                    assert len(resp.json()["persisted_todo_ids"]) == 1

            async with factory() as session:
                rows = await TodoRepository(session).list_all()
                persisted = [r for r in rows if r.title == "Add tests for a.py"]
                assert len(persisted) == 1
                # Gated, not silently BACKLOG / auto-executing.
                assert persisted[0].work_type == "self_improve"
                assert persisted[0].status == TodoStatus.APPROVAL_REQUIRED.value
                assert persisted[0].status != TodoStatus.BACKLOG.value

            # Visible to the human approve/reject surface.
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/admin/self-improve/approvals")
                assert resp.status_code == 200
                assert resp.json()["count"] == 1
        finally:
            await engine.dispose()


class TestSelfImproveApplyConfigTierApprovalGate:
    """Task #22: config-tier apply must NOT write on the bare authenticated
    request; it is interposed by the self-improve human-approval gate."""

    @pytest.mark.asyncio
    async def test_apply_without_approval_does_not_write(
        self, app, tmp_path, monkeypatch
    ):
        from general_ludd.db.repository import TodoRepository
        from general_ludd.schemas.todo import TodoStatus

        engine, factory = await _attach_inmemory_db(app)
        target = tmp_path / "cfg.yml"
        target.write_text("key: original\n")
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/admin/self-improve/apply",
                    json={
                        "kind": "config",
                        "capability_required": "config_write",
                        "target_paths": [str(target)],
                        "change_content": "key: changed\n",
                    },
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "approval_required"
                assert data["approval_id"]

            # No file mutation: the write path was never reached.
            assert target.read_text() == "key: original\n"

            # A held record exists for a human to review.
            async with factory() as session:
                rows = await TodoRepository(session).list_all()
                held = [
                    r for r in rows
                    if r.work_type == "self_improve"
                    and r.status == TodoStatus.APPROVAL_REQUIRED.value
                ]
                assert len(held) == 1
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_apply_after_release_writes_recorded_change(
        self, app, tmp_path, monkeypatch
    ):
        """Regression: the legitimate operator flow (enqueue -> approve -> apply)
        performs the on-disk write, using the RECORDED spec (not the apply
        request body -> no approve-A / apply-B bait-and-switch)."""
        from general_ludd.db.repository import TodoRepository
        from general_ludd.schemas.todo import TodoStatus

        engine, factory = await _attach_inmemory_db(app)
        # Real write, confined to tmp_path (patch the router's cwd).
        monkeypatch.setattr(
            "general_ludd.routers.self_improve.Path.cwd", lambda: tmp_path
        )
        target = tmp_path / "cfg.yml"
        target.write_text("key: original\n")
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # 1. enqueue -> APPROVAL_REQUIRED, no write yet.
                resp = await client.post(
                    "/admin/self-improve/apply",
                    json={
                        "kind": "config",
                        "capability_required": "config_write",
                        "target_paths": [str(target)],
                        "change_content": "key: changed\n",
                    },
                )
                approval_id = resp.json()["approval_id"]
                assert target.read_text() == "key: original\n"

                # 2. human releases the held record.
                resp = await client.post(
                    f"/admin/self-improve/approvals/{approval_id}/approve"
                )
                assert resp.status_code == 200
                assert resp.json()["approved"] is True

                # 3. apply with the approval id -> the write proceeds. The bogus
                #    body target/content is IGNORED; the recorded spec is used.
                resp = await client.post(
                    "/admin/self-improve/apply",
                    json={
                        "kind": "config",
                        "approval_id": approval_id,
                        "target_paths": ["should-be-ignored.yml"],
                        "change_content": "key: EVIL\n",
                    },
                )
                assert resp.status_code == 200
                assert resp.json()["status"] == "applied"

            # Recorded change landed on disk (not the bait content).
            assert target.read_text() == "key: changed\n"

            # The approval record is consumed so it cannot be replayed.
            async with factory() as session:
                todo = await TodoRepository(session).get_by_id(approval_id)
                assert todo.status == TodoStatus.COMPLETE.value
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_apply_with_unreleased_approval_is_refused(
        self, app, tmp_path, monkeypatch
    ):
        """An approval id that a human has NOT released (still APPROVAL_REQUIRED)
        must not drive a write."""
        engine, _factory = await _attach_inmemory_db(app)
        monkeypatch.setattr(
            "general_ludd.routers.self_improve.Path.cwd", lambda: tmp_path
        )
        target = tmp_path / "cfg.yml"
        target.write_text("key: original\n")
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/admin/self-improve/apply",
                    json={
                        "kind": "config",
                        "capability_required": "config_write",
                        "target_paths": [str(target)],
                        "change_content": "key: changed\n",
                    },
                )
                approval_id = resp.json()["approval_id"]

                # Apply BEFORE approving -> refused, no write.
                resp = await client.post(
                    "/admin/self-improve/apply",
                    json={"kind": "config", "approval_id": approval_id},
                )
                assert resp.status_code == 409
            assert target.read_text() == "key: original\n"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_apply_with_unknown_approval_is_refused(self, app):
        """A caller cannot manufacture an approval id to reach the write path."""
        engine, _factory = await _attach_inmemory_db(app)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/admin/self-improve/apply",
                    json={
                        "kind": "config",
                        "approval_id": "TODO-NOT-AN-APPROVAL",
                    },
                )

            assert response.status_code == 404
            assert response.json()["detail"] == (
                "approval TODO-NOT-AN-APPROVAL not found"
            )
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_config_apply_without_db_is_refused(self, transport):
        """Fail-closed: with no approval database the config-tier apply must
        refuse rather than perform an unreviewed write."""
        with patch(
            "general_ludd.routers.self_improve.UpdateApplier"
        ) as MockApplier:
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/admin/self-improve/apply",
                    json={
                        "kind": "config",
                        "capability_required": "config_write",
                        "target_paths": ["config/foo.yml"],
                        "change_content": "key: value\n",
                    },
                )
                assert resp.status_code == 503
                MockApplier.assert_not_called()

    @staticmethod
    async def _enqueue_approve_apply(
        client, target_path: str, *, title: str = "", content: str = "key: changed\n"
    ) -> str:
        """Drive the enqueue -> approve -> apply flow; return the approval id."""
        body: dict = {
            "kind": "config",
            "capability_required": "config_write",
            "target_paths": [target_path],
            "change_content": content,
        }
        if title:
            body["title"] = title
        resp = await client.post("/admin/self-improve/apply", json=body)
        approval_id = resp.json()["approval_id"]
        resp = await client.post(
            f"/admin/self-improve/approvals/{approval_id}/approve"
        )
        assert resp.status_code == 200
        resp = await client.post(
            "/admin/self-improve/apply",
            json={"kind": "config", "approval_id": approval_id},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "applied"
        return approval_id

    @pytest.mark.asyncio
    async def test_apply_records_change_export_entry(
        self, app, tmp_path, monkeypatch
    ):
        """Task #47: a successful config-tier apply POPULATES the
        ChangeRecordStore so `core-changes` has something to export. The dead
        store is now wired to the real write path."""
        from general_ludd.integrity.change_log import ChangeRecordStore

        store_dir = tmp_path / "changestore"
        monkeypatch.setenv("GL_CHANGE_STORE_DIR", str(store_dir))
        engine, _factory = await _attach_inmemory_db(app)
        monkeypatch.setattr(
            "general_ludd.routers.self_improve.Path.cwd", lambda: tmp_path
        )
        target = tmp_path / "cfg.yml"
        target.write_text("key: original\n")
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await self._enqueue_approve_apply(
                    client, str(target), title="tune the widget"
                )

            records = ChangeRecordStore(store_dir=str(store_dir)).list_records()
            assert len(records) == 1
            rec = records[0]
            assert rec.file_path == str(target.resolve())
            assert rec.change_type == "config"
            assert rec.reason == "tune the widget"
            assert rec.new_content == "key: changed\n"
            assert rec.old_content == b"key: original\n"
            assert rec.signer == "self_improve_apply"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_apply_excluded_path_records_nothing(
        self, app, tmp_path, monkeypatch
    ):
        """An excluded artefact (e.g. a .pyc path) must NOT be recorded even
        though the write itself proceeds."""
        from general_ludd.integrity.change_log import ChangeRecordStore

        store_dir = tmp_path / "changestore"
        monkeypatch.setenv("GL_CHANGE_STORE_DIR", str(store_dir))
        engine, _factory = await _attach_inmemory_db(app)
        monkeypatch.setattr(
            "general_ludd.routers.self_improve.Path.cwd", lambda: tmp_path
        )
        target = tmp_path / "artifact.pyc"
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await self._enqueue_approve_apply(
                    client, str(target), title="compiled artefact"
                )

            # Write proceeded, but the excluded path produced no change record.
            assert target.read_text() == "key: changed\n"
            records = ChangeRecordStore(store_dir=str(store_dir)).list_records()
            assert records == []
        finally:
            await engine.dispose()


class TestSelfImproveApplyConfigTier:
    """Config-tier plans route through UpdateApplier + AtomicSafeWriter."""

    @pytest.mark.asyncio
    async def test_non_config_kind_enqueues_for_approval(
        self, app, transport, tmp_path
    ):
        """C13: non-config apply is gated — enqueues APPROVAL_REQUIRED, no silent execute."""
        from general_ludd.db.models import ProjectModel
        from general_ludd.routers.self_improve import SELF_IMPROVE_WORK_TYPE

        engine, factory = await _attach_inmemory_db(app)
        workspace_root = tmp_path / "project-workspace"
        worktree = workspace_root / "repo" / "worktrees" / "approved"
        worktree.mkdir(parents=True)
        app.state._project_manager = SimpleNamespace(
            get_project=lambda project_id: SimpleNamespace(
                workspace_path=str(workspace_root)
            )
            if project_id == "project-1"
            else None
        )
        try:
            async with factory() as session:
                session.add(
                    ProjectModel(
                        project_id="project-1",
                        name="Project one",
                        workspace_path=str(workspace_root),
                    )
                )
                await session.commit()
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/admin/self-improve/apply",
                    json={
                        "kind": "code",
                        "title": "title-x",
                        "description": "desc-x",
                        "project_id": "project-1",
                        "worktree_path": str(worktree),
                    },
                )
                assert resp.status_code == 200
                data = resp.json()
                assert "approval_id" in data
                assert data["status"] == "approval_required"

                async with factory() as session:
                    from general_ludd.db.repository import TodoRepository
                    repo = TodoRepository(session)
                    approved = await repo.get_by_id(data["approval_id"])
                    assert approved is not None
                    assert approved.status == "approval_required"
                    assert getattr(approved, "work_type", "") == SELF_IMPROVE_WORK_TYPE
                    assert approved.project_id == "project-1"
        finally:
            await engine.dispose()


class TestSelfImproveStatusEndpoint:
    @pytest.mark.asyncio
    async def test_status_never_run(self, transport):
        from general_ludd.daemon import _daemon_state
        _daemon_state.pop("self_improve_last_analysis", None)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/self-improve/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "never_run"

    @pytest.mark.asyncio
    async def test_status_after_analyze(self, transport):
        with patch(
            "general_ludd.routers.self_improve.SelfImprovementHarness"
        ) as MockHarness:
            instance = MockHarness.return_value
            instance.run_gap_analysis.return_value = [
                {"type": "dead_code", "file": "x.py", "severity": "medium",
                 "message": "unused class"},
            ]
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post("/admin/self-improve/analyze")
                resp = await client.get("/admin/self-improve/status")
                data = resp.json()
                assert data["status"] == "completed"
                assert data["findings_count"] == 1


class TestAgentBehaviorSelfImproveInterval:
    def test_default_interval_is_zero(self):
        behavior = AgentBehavior()
        assert behavior.self_improve_interval == 0

    def test_set_interval(self):
        behavior = AgentBehavior(self_improve_interval=10)
        assert behavior.self_improve_interval == 10

    def test_negative_interval_raises(self):
        with pytest.raises(ValueError, match="self_improve_interval"):
            AgentBehavior(self_improve_interval=-1)

    def test_to_dict_includes_interval(self):
        behavior = AgentBehavior(self_improve_interval=5)
        d = behavior.to_dict()
        assert d["self_improve_interval"] == 5

    def test_from_dict_with_interval(self):
        behavior = AgentBehavior.from_dict({"self_improve_interval": 7})
        assert behavior.self_improve_interval == 7


class TestBehaviorRendererSelfImprove:
    def test_render_disabled_when_zero(self):
        behavior = AgentBehavior(self_improve_interval=0)
        renderer = BehaviorRenderer()
        result = renderer.render(behavior)
        assert "Self-Improvement" not in result

    def test_render_enabled_shows_interval(self):
        behavior = AgentBehavior(self_improve_interval=10)
        renderer = BehaviorRenderer()
        result = renderer.render(behavior)
        assert "Self-Improvement Cycle" in result
        assert "10 ticks" in result

    def test_render_as_prompt_includes_self_improve(self):
        behavior = AgentBehavior(self_improve_interval=5)
        renderer = BehaviorRenderer()
        result = renderer.render_as_prompt(behavior, "build", "fix bugs")
        assert "Self-Improvement Cycle" in result
        assert "5 ticks" in result


class TestEventLoopSelfImprovePhase:
    @pytest.mark.asyncio
    async def test_phase_skipped_when_interval_zero(self):
        loop = EventLoop(self_improve_interval=0)
        loop._total_ticks = 5
        await loop._phase_self_improve()
        assert loop._tick_metrics.get("self_improve_gaps") is None

    @pytest.mark.asyncio
    async def test_phase_skipped_when_not_multiple(self):
        loop = EventLoop(self_improve_interval=10)
        loop._total_ticks = 5
        await loop._phase_self_improve()
        assert loop._tick_metrics.get("self_improve_gaps") is None

    @pytest.mark.asyncio
    async def test_phase_runs_on_interval(self):
        loop = EventLoop(self_improve_interval=10, daemon_state={})
        loop._total_ticks = 20
        from general_ludd.self_improve.harness import SelfImprovementHarness
        with patch.object(SelfImprovementHarness, "run_gap_analysis", return_value=[
            {"type": "dead_code", "file": "src/mod.py", "severity": "medium",
             "message": "unused"},
        ]), patch.object(SelfImprovementHarness, "generate_fix_todos", return_value=[
            {"title": "Wire X", "work_type": "code", "priority": "medium"},
        ]), patch.object(SelfImprovementHarness, "enqueue_todos", return_value=1):
            await loop._phase_self_improve()
            assert loop._tick_metrics["self_improve_gaps"] == 1

    @pytest.mark.asyncio
    async def test_phase_persists_self_improve_todos_via_repository(self):
        # W3.7 (H2): the phase must PERSIST generated todos through the repo
        # (work_type=self_improve, high priority), not discard them into the
        # in-memory harness. Proven against a real in-memory session.
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import StaticPool

        from general_ludd.db.repository import TodoRepository
        from general_ludd.db.session import (
            create_async_session_factory,
            ensure_tables,
        )
        from general_ludd.schemas.todo import TodoStatus
        from general_ludd.self_improve.harness import SelfImprovementHarness

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        try:
            await ensure_tables(engine)
            factory = create_async_session_factory(engine)

            loop = EventLoop(session=factory, self_improve_interval=1, daemon_state={})
            with patch.object(SelfImprovementHarness, "run_gap_analysis", return_value=[
                {"type": "missing_tests", "file": "a.py", "severity": "high",
                 "message": "no tests"},
            ]), patch.object(SelfImprovementHarness, "generate_fix_todos", return_value=[
                {"title": "Add tests", "work_type": "test", "priority": "high"},
            ]):
                await loop.tick()

            async with factory() as session:
                rows = await TodoRepository(session).list_all()
                persisted = [r for r in rows if r.title == "Add tests"]
                assert len(persisted) == 1
                assert persisted[0].work_type == "self_improve"
                # Security: SelfImproveGate.auto_queue defaults to False, so a
                # self-authored todo is parked in APPROVAL_REQUIRED behind a
                # human review gate rather than QUEUED (immediate execution) —
                # closing the self-modification approval bypass. A human releases
                # it to QUEUED via SelfImproveApprovalManager. Set
                # self_improve.auto_queue: true to opt back into QUEUED admission.
                assert persisted[0].status == TodoStatus.APPROVAL_REQUIRED.value
                # "high" maps to a high integer priority.
                assert persisted[0].priority >= 10
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_phase_no_findings_no_crash(self):
        loop = EventLoop(self_improve_interval=1, daemon_state={})
        loop._total_ticks = 1
        from general_ludd.self_improve.harness import SelfImprovementHarness
        with patch.object(SelfImprovementHarness, "run_gap_analysis", return_value=[]):
            await loop._phase_self_improve()
            assert loop._tick_metrics["self_improve_gaps"] == 0


class TestEventLoopSelfImproveTargetsProjectRepo:
    """The self-improve phase must analyze the CURRENT tick's project checkout,
    not gludd's own repo: it must construct SelfImprovementHarness with a
    repo_root resolved from the tick project_id (via _resolve_repo_root)."""

    @pytest.mark.asyncio
    async def test_phase_passes_resolved_repo_root_to_harness(self):
        loop = EventLoop(self_improve_interval=1, daemon_state={})
        loop._total_ticks = 1
        loop._tick_project_id = "proj-ext"

        with patch.object(
            loop, "_resolve_repo_root", return_value="/checkouts/proj-ext"
        ) as mock_resolve, patch(
            "general_ludd.event_loop.loop_handlers.SelfImprovementHarness"
        ) as MockHarness:
            instance = MockHarness.return_value
            instance.run_gap_analysis.return_value = []
            await loop._phase_self_improve()

        # repo_root was resolved from the tick project and handed to the harness.
        mock_resolve.assert_called_once_with("proj-ext")
        MockHarness.assert_called_once()
        assert MockHarness.call_args.kwargs["repo_root"] == "/checkouts/proj-ext"


class TestEventLoopSelfImproveRecurringFailures:
    """Feedback loop: recurring real-task failures (BlockerDetector.chronic_blockers)
    must be fed into the harness gap analysis so a class of problem gludd keeps
    hitting during real work becomes a self-improvement proposal."""

    @pytest.mark.asyncio
    async def test_recurring_failures_reach_harness_when_enabled(self):
        from datetime import UTC, datetime

        from general_ludd.remediation.blocker_detector import (
            BlockerDetector,
            ChronicBlocker,
        )
        from general_ludd.self_improve.harness import SelfImprovementHarness

        loop = EventLoop(
            self_improve_interval=1,
            daemon_state={},
            config={"self_improve": {"ingest_recurring_failures": True}},
        )
        loop._total_ticks = 1
        # _collect_recurring_failures requires a live tick session + repo
        # (normally set inside tick()); inject sentinels — the real detector is
        # patched so neither object is actually used.
        loop._active_session = object()
        loop._todo_repo = object()

        cb = ChronicBlocker(
            task_type="code",
            blocker_kind="permission_escalation",
            incident_count=7,
            first_seen=datetime.now(UTC),
            last_seen=datetime.now(UTC),
            recent_todo_ids=["T1"],
        )
        captured: list = []

        def _capture(recurring_failures=None):
            captured.append(recurring_failures)
            return []

        with patch.object(
            BlockerDetector,
            "chronic_blockers",
            new_callable=AsyncMock,
            return_value=[cb],
        ), patch.object(
            SelfImprovementHarness, "run_gap_analysis", side_effect=_capture
        ):
            await loop._phase_self_improve()

        # The detector's chronic-failure records reached run_gap_analysis.
        assert captured == [[cb]]

    @pytest.mark.asyncio
    async def test_ingest_disabled_does_not_query_detector(self):

        from general_ludd.remediation.blocker_detector import BlockerDetector
        from general_ludd.self_improve.harness import SelfImprovementHarness

        loop = EventLoop(
            self_improve_interval=1,
            daemon_state={},
            config={"self_improve": {"ingest_recurring_failures": False}},
        )
        loop._total_ticks = 1
        loop._active_session = object()
        loop._todo_repo = object()

        cb_mock = AsyncMock(return_value=[])
        with patch.object(
            BlockerDetector, "chronic_blockers", cb_mock
        ), patch.object(
            SelfImprovementHarness, "run_gap_analysis", return_value=[]
        ):
            await loop._phase_self_improve()

        # Feature-gated off → the detector is never constructed/queried.
        cb_mock.assert_not_called()


class TestTrainingDataCollectorWiring:
    """TrainingDataCollector wired to the event loop self-improve phase."""

    @pytest.mark.asyncio
    async def test_collect_training_data_from_reviewed_returns(self):
        """Seed reviewed returns + decisions + todos, then verify
        (instruction, response, outcome) triples are recorded."""
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import StaticPool

        from general_ludd.db.models import (
            OrnithTrainingPairModel,
            TaskDecisionModel,
            TaskReturnModel,
            TodoModel,
        )
        from general_ludd.db.session import (
            create_async_session_factory,
            ensure_tables,
        )

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        try:
            await ensure_tables(engine)
            factory = create_async_session_factory(engine)

            loop = EventLoop(
                session=factory,
                self_improve_interval=1,
                daemon_state={},
            )

            async with factory() as session:
                todo = TodoModel(
                    todo_id="TODO-1",
                    title="Fix crash in daemon startup",
                    description="The daemon crashes when no config dir exists",
                    work_type="code",
                    status="reviewed",
                )
                session.add(todo)
                await session.flush()

                tr = TaskReturnModel(
                    return_id="RET-1",
                    todo_id="TODO-1",
                    job_id="JOB-1",
                    playbook="noop.yml",
                    queue="core",
                    work_type="code",
                    status="reviewed",
                    result_summary="Fixed the startup crash by adding a null check",
                    producer_worker_id="worker-1",
                )
                session.add(tr)
                await session.flush()

                dec = TaskDecisionModel(
                    return_id="RET-1",
                    decision="complete",
                    confidence=0.95,
                )
                session.add(dec)
                await session.commit()

            recorded = await loop._collect_training_data_from_returns()
            assert recorded == 1

            async with factory() as session:
                from sqlalchemy import select
                stmt = select(OrnithTrainingPairModel).where(
                    OrnithTrainingPairModel.task_description.like("%Fix crash%")
                )
                result = await session.execute(stmt)
                pairs = list(result.scalars().all())
                assert len(pairs) == 1
                p = pairs[0]
                assert "Fix crash" in p.task_description
                assert p.outcome_status == "succeeded"
                assert p.scaffold_kind == "patch"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_collect_training_data_skips_when_no_factory(self):
        """Returns 0 when no session_factory is wired."""
        loop = EventLoop(daemon_state={})
        assert loop._session_factory is None
        recorded = await loop._collect_training_data_from_returns()
        assert recorded == 0

    @pytest.mark.asyncio
    async def test_collect_training_data_rejected_outcome(self):
        """A return whose decision is NOT 'complete' gets 'rejected_by_review'."""
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import StaticPool

        from general_ludd.db.models import (
            OrnithTrainingPairModel,
            TaskDecisionModel,
            TaskReturnModel,
            TodoModel,
        )
        from general_ludd.db.session import (
            create_async_session_factory,
            ensure_tables,
        )

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        try:
            await ensure_tables(engine)
            factory = create_async_session_factory(engine)

            loop = EventLoop(session=factory, daemon_state={})

            async with factory() as session:
                todo = TodoModel(
                    todo_id="TODO-R",
                    title="Refactor loop to use tenacity",
                    work_type="refactor",
                    status="reviewed",
                )
                session.add(todo)
                await session.flush()

                tr = TaskReturnModel(
                    return_id="RET-R",
                    todo_id="TODO-R",
                    job_id="JOB-R",
                    playbook="noop.yml",
                    queue="core",
                    work_type="refactor",
                    status="reviewed",
                    result_summary="patch did not apply cleanly",
                    producer_worker_id="worker-2",
                )
                session.add(tr)
                await session.flush()

                dec = TaskDecisionModel(
                    return_id="RET-R",
                    decision="needs_more_work",
                    confidence=0.3,
                )
                session.add(dec)
                await session.commit()

            recorded = await loop._collect_training_data_from_returns()
            assert recorded == 1

            async with factory() as session:
                from sqlalchemy import select
                stmt = select(OrnithTrainingPairModel)
                result = await session.execute(stmt)
                pairs = list(result.scalars().all())
                assert len(pairs) == 1
                assert pairs[0].outcome_status == "rejected_by_review"
        finally:
            await engine.dispose()


class TestApplySelfImprovements:
    """Quality report generation and error pattern detection."""

    @pytest.mark.asyncio
    async def test_apply_self_improvements_generates_quality_report(self):
        """Verify quality_report is generated and logged.  When all pairs
        succeeded there are zero rejected examples and no error patterns."""
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import StaticPool

        from general_ludd.db.models import OrnithTrainingPairModel
        from general_ludd.db.session import (
            create_async_session_factory,
            ensure_tables,
        )

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        try:
            await ensure_tables(engine)
            factory = create_async_session_factory(engine)

            async with factory() as session:
                for i in range(3):
                    pair = OrnithTrainingPairModel(
                        task_description=f"Task {i}",
                        scaffold_content=f"patch {i}",
                        scaffold_kind="patch",
                        scaffold_hash=f"hash{i}",
                        agent_id="agent-1",
                        outcome_status="succeeded",
                    )
                    session.add(pair)
                await session.commit()

            loop = EventLoop(session=factory, daemon_state={})
            await loop._apply_self_improvements()

            assert "self_improve_error_patterns" not in loop._daemon_state
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_apply_self_improvements_detects_stop_pattern(self):
        """Rejected examples containing 'stop'/'premature'/'halt'/'abort' are
        classified as premature_stop."""
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import StaticPool

        from general_ludd.db.models import OrnithTrainingPairModel
        from general_ludd.db.session import (
            create_async_session_factory,
            ensure_tables,
        )

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        try:
            await ensure_tables(engine)
            factory = create_async_session_factory(engine)

            async with factory() as session:
                pair = OrnithTrainingPairModel(
                    task_description="Agent stopped prematurely before completing all tasks",
                    scaffold_content="done",
                    scaffold_kind="patch",
                    scaffold_hash="hash-stop",
                    agent_id="agent-1",
                    outcome_status="rejected_by_review",
                )
                session.add(pair)
                await session.commit()

            loop = EventLoop(session=factory, daemon_state={})
            await loop._apply_self_improvements()

            patterns = loop._daemon_state.get(
                "self_improve_error_patterns", {}
            )
            assert patterns.get("patterns", {}).get("premature_stop") == 1
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_apply_self_improvements_detects_grind_pattern(self):
        """Rejected examples containing 'grind'/'token'/'main_thread'/'inline'
        are classified as grind_failure."""
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import StaticPool

        from general_ludd.db.models import OrnithTrainingPairModel
        from general_ludd.db.session import (
            create_async_session_factory,
            ensure_tables,
        )

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        try:
            await ensure_tables(engine)
            factory = create_async_session_factory(engine)

            async with factory() as session:
                pair = OrnithTrainingPairModel(
                    task_description="Agent grinding on main_thread inline edits",
                    scaffold_content="failed",
                    scaffold_kind="patch",
                    scaffold_hash="hash-grind",
                    agent_id="agent-1",
                    outcome_status="rejected_by_review",
                )
                session.add(pair)
                await session.commit()

            loop = EventLoop(session=factory, daemon_state={})
            await loop._apply_self_improvements()

            patterns = loop._daemon_state.get(
                "self_improve_error_patterns", {}
            )
            assert patterns.get("patterns", {}).get("grind_failure") == 1
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_apply_self_improvements_detects_generic_failure(self):
        """Rejected examples without stop/grind keywords get classified as
        generic_failure."""
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import StaticPool

        from general_ludd.db.models import OrnithTrainingPairModel
        from general_ludd.db.session import (
            create_async_session_factory,
            ensure_tables,
        )

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        try:
            await ensure_tables(engine)
            factory = create_async_session_factory(engine)

            async with factory() as session:
                pair = OrnithTrainingPairModel(
                    task_description="Write unit tests for the router",
                    scaffold_content="patch content",
                    scaffold_kind="patch",
                    scaffold_hash="hash-generic",
                    agent_id="agent-1",
                    outcome_status="reverted",
                )
                session.add(pair)
                await session.commit()

            loop = EventLoop(session=factory, daemon_state={})
            await loop._apply_self_improvements()

            patterns = loop._daemon_state.get(
                "self_improve_error_patterns", {}
            )
            assert patterns.get("patterns", {}).get("generic_failure") == 1
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_apply_self_improvements_skips_when_no_factory(self):
        """No-op when no session_factory is wired."""
        loop = EventLoop(daemon_state={})
        assert loop._session_factory is None
        await loop._apply_self_improvements()


class TestPhaseSelfImproveIntegration:
    """_phase_self_improve calls _collect_training_data_from_returns
    and _apply_self_improvements."""

    @pytest.mark.asyncio
    async def test_phase_calls_collect_training_data(self):

        from general_ludd.self_improve.harness import SelfImprovementHarness

        loop = EventLoop(
            self_improve_interval=1,
            daemon_state={},
        )
        loop._total_ticks = 1

        with patch.object(
            SelfImprovementHarness, "run_gap_analysis", return_value=[]
        ), patch.object(
            loop, "_collect_training_data_from_returns",
            new_callable=AsyncMock, return_value=5,
        ) as mock_collect, patch.object(
            loop, "_apply_self_improvements",
            new_callable=AsyncMock,
        ) as mock_apply:
            await loop._phase_self_improve()
            mock_collect.assert_awaited_once()
            mock_apply.assert_awaited_once()
            assert loop._tick_metrics["self_improve_training_recorded"] == 5

    @pytest.mark.asyncio
    async def test_collect_training_data_failure_does_not_block_phase(self):
        from general_ludd.self_improve.harness import SelfImprovementHarness

        loop = EventLoop(self_improve_interval=1, daemon_state={})
        loop._total_ticks = 1

        with patch.object(
            SelfImprovementHarness, "run_gap_analysis", return_value=[]
        ), patch.object(
            loop, "_collect_training_data_from_returns",
            side_effect=Exception("DB down"),
        ), patch.object(
            loop, "_apply_self_improvements",
        ) as mock_apply:
            await loop._phase_self_improve()
            assert loop._tick_metrics["self_improve_gaps"] == 0
            mock_apply.assert_awaited_once()
