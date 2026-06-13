"""Completion-audit wiring proofs.

Each test here proves that a class previously flagged by
``run_completion_audit`` as "defined but never instantiated or referenced
anywhere" is now wired into a genuine production call path (not a throwaway
reference). The tests exercise the wired behavior, so deleting the wiring
breaks a real assertion.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestHotReloaderEventWiring:
    """PlaybookRemovedEvent + HookTriggeredEvent published by HotReloader."""

    def _reloader(self, tmp_path, bus):
        from general_ludd.reload.hot_reloader import HotReloader

        pb_dir = tmp_path / "playbooks"
        pb_dir.mkdir()
        return HotReloader(
            config_dir=str(tmp_path),
            event_bus=bus,
            playbooks_dir=str(pb_dir),
        ), pb_dir

    def test_removed_playbook_publishes_playbook_removed_event(self, tmp_path):
        from general_ludd.events.types import (
            PlaybookRegisteredEvent,
            PlaybookRemovedEvent,
        )
        from general_ludd.reload.hot_reloader import ReloadScope

        bus = MagicMock()
        reloader, pb_dir = self._reloader(tmp_path, bus)

        # First reload sees a.yml + b.yml registered.
        (pb_dir / "a.yml").write_text("---\n- hosts: localhost\n")
        (pb_dir / "b.yml").write_text("---\n- hosts: localhost\n")
        reloader.reload(ReloadScope.PLAYBOOKS)

        # b.yml is deleted; the next reload must emit a PlaybookRemovedEvent.
        (pb_dir / "b.yml").unlink()
        bus.reset_mock()
        reloader.reload(ReloadScope.PLAYBOOKS)

        published = [c.args[0] for c in bus.publish.call_args_list]
        removed = [e for e in published if isinstance(e, PlaybookRemovedEvent)]
        assert len(removed) == 1
        assert removed[0].payload["playbook"] == "b.yml"
        # a.yml is still registered (not removed).
        assert all(
            not (isinstance(e, PlaybookRemovedEvent) and e.payload["playbook"] == "a.yml")
            for e in published
        )
        # And registrations still fire for the survivors.
        assert any(isinstance(e, PlaybookRegisteredEvent) for e in published)

    def test_fire_hooks_publishes_hook_triggered_event(self, tmp_path):
        from general_ludd.events.types import HookTriggeredEvent
        from general_ludd.reload.hot_reloader import ReloadScope

        bus = MagicMock()
        hooks = MagicMock()
        reloader, _ = self._reloader(tmp_path, bus)
        reloader._hooks = hooks

        reloader.reload(ReloadScope.CONFIG)

        published = [c.args[0] for c in bus.publish.call_args_list]
        hook_events = [e for e in published if isinstance(e, HookTriggeredEvent)]
        assert hook_events, "expected at least one HookTriggeredEvent on the bus"
        assert hook_events[0].payload["event_name"] == "on_config_reloaded"
        # The underlying hook system was still fired.
        hooks.fire.assert_called()


class TestWorkerPingPongWiring:
    """WorkerPingEvent/WorkerPongEvent emitted by the worker heartbeat helper."""

    def test_worker_ping_pong_roundtrip(self):
        from general_ludd.events.types import WorkerPingEvent, WorkerPongEvent
        from general_ludd.worker.heartbeat import handle_ping, make_ping

        ping = make_ping()
        assert isinstance(ping, WorkerPingEvent)

        pong = handle_ping(ping, worker_id="worker-1")
        assert isinstance(pong, WorkerPongEvent)
        assert pong.payload["worker_id"] == "worker-1"
        assert pong.correlation_id == ping.event_id


class TestAgentCapabilitiesWiring:
    """ContextCompactor + TokenWindowManager + AgentToolAdapter + ToolCallLoop
    + ModelFailoverChain bundled and used on a real generation path."""

    def test_capabilities_bundle_constructs(self):
        from general_ludd.agents.capabilities import AgentCapabilities

        caps = AgentCapabilities(max_tokens=1000, primary_profile="p1",
                                 fallback_profiles=["p2"])
        assert caps.token_window is not None
        assert caps.compactor is not None
        assert caps.failover.get_chain() == ["p1", "p2"]

    def test_prepare_messages_compacts_oversized_history(self):
        from general_ludd.agents.capabilities import AgentCapabilities

        caps = AgentCapabilities(max_tokens=80, compaction_threshold=0.3,
                                 preserve_recent_count=1)
        history = [
            {"role": "user", "content": "old message one " * 20},
            {"role": "assistant", "content": "old reply " * 20},
            {"role": "user", "content": "recent question"},
        ]
        out = caps.prepare_messages("system prompt", history)
        # Compaction collapses the old turns into a summary system message.
        assert any("[prior context]" in m["content"] for m in out)
        # The most recent user turn is preserved verbatim.
        assert any(m["content"] == "recent question" for m in out)

    def test_agent_tool_adapter_lists_registered_agents(self):
        from general_ludd.agents.capabilities import AgentCapabilities

        caps = AgentCapabilities()
        tools = caps.list_agent_tools()
        assert isinstance(tools, list)

    def test_token_budget_check(self):
        from general_ludd.agents.capabilities import AgentCapabilities

        caps = AgentCapabilities(max_tokens=100)
        # A short prompt is within budget; a huge one is not.
        assert caps.within_budget("agent", "hello") is True
        assert caps.within_budget("agent", "x " * 1000) is False

    def test_make_graph_gateway_builds_scored_gateway(self):
        from general_ludd.agents.capabilities import AgentCapabilities
        from general_ludd.models.langgraph_gateway import LangGraphGateway

        caps = AgentCapabilities()
        gw = MagicMock()
        gw.call_model = MagicMock(return_value=MagicMock(content="x"))
        graph_gw = caps.make_graph_gateway(gw, enable_graph=False)
        assert isinstance(graph_gw, LangGraphGateway)
        # The scoring engine is a PromptScoringEngine.
        from general_ludd.scoring.engine import PromptScoringEngine

        assert isinstance(graph_gw._scoring, PromptScoringEngine)


class TestWorkerUsesCapabilities:
    def test_invoke_gateway_bounds_prompt_via_capabilities(self):
        from general_ludd.schemas.job import JobSpec
        from general_ludd.worker.app import _invoke_gateway_for_job

        gateway = MagicMock()
        gateway.call_model = MagicMock(return_value=MagicMock(content="OK"))
        job = JobSpec(
            job_id="J1", todo_id="T1", playbook="code", queue="core",
            work_type="code", prompt_text="do the thing",
            skill_body="be helpful",
        )
        result = _invoke_gateway_for_job(gateway, job)
        assert result == "OK"
        # The gateway was called with a messages list that went through the
        # capabilities path (system + user present).
        _, kwargs = gateway.call_model.call_args
        msgs = kwargs.get("messages")
        assert msgs is not None
        assert any(m["role"] == "user" for m in msgs)


class TestDogfoodOrchestratorWiring:
    """DogfoodRunner + DogfoodValidator wired into a real orchestrator path."""

    def test_run_smoke_and_validate(self, tmp_path):
        from general_ludd.dogfood.orchestrator import run_smoke_and_validate

        # 'noop' playbook exists in this repo; syntax-check should pass.
        report = run_smoke_and_validate(
            repo_root=str(tmp_path),  # repo_root only used for cwd; noop check tolerant
            task_name="noop",
        )
        assert "smoke" in report
        assert "validation" in report
        assert report["smoke"]["task_name"] == "noop"
        # Validation result mirrors the smoke success flag.
        assert report["validation"]["valid"] == report["smoke"]["success"]


class TestMaintenanceRouterWiring:
    """GitIntelligence + DependencyManager + QualityGateChecker via daemon API."""

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from general_ludd.routers.maintenance import register

        app = FastAPI()
        register(app, {})
        return TestClient(app)

    def test_hot_files_endpoint(self):
        resp = self._client().get("/admin/code-intel/hot-files?limit=5")
        assert resp.status_code == 200
        assert "hot_files" in resp.json()

    def test_quality_check_endpoint(self):
        resp = self._client().post(
            "/admin/quality/check",
            json={"coverage_percent": 90.0},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "gate" in body or "passed" in body

    def test_issues_poll_endpoint(self, monkeypatch):
        async def _fake_poll(self):
            return [{"title": "Issue 1", "description": "d", "work_type": "bug_fix"}]

        monkeypatch.setattr(
            "general_ludd.git_automation.issue_ingestor.GitHubIssueIngestor.poll_issues",
            _fake_poll,
        )
        resp = self._client().post(
            "/admin/issues/poll",
            json={"owner": "o", "repo": "r"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["new_todos"][0]["work_type"] == "bug_fix"

    def test_deps_outdated_endpoint(self, monkeypatch):
        from general_ludd.dependency.manager import OutdatedPackage

        async def _fake_check(self):
            return [OutdatedPackage(name="pkg", current_version="1.0", latest_version="2.0")]

        monkeypatch.setattr(
            "general_ludd.dependency.manager.DependencyManager.check_for_updates",
            _fake_check,
        )
        resp = self._client().get("/admin/deps/outdated")
        assert resp.status_code == 200
        body = resp.json()
        assert body["outdated"][0]["name"] == "pkg"


class TestReleaseOrchestratorWiring:
    """ContainerBuilder + PipBundleBuilder + ReleaseArtifactValidator wired."""

    def test_orchestrator_builds_and_validates(self, tmp_path, monkeypatch):
        from general_ludd.runtime import release_orchestrator as ro
        from general_ludd.runtime.container import BuildResult
        from general_ludd.runtime.pip_bundle import BundleResult
        from general_ludd.runtime.release import ReleaseValidationResult

        # Stub the heavyweight subprocess-driven builders.
        def _fake_bundle(self, output_dir, version):
            return BundleResult(
                bundle_path=output_dir, wheel_path="w.whl", sdist_path="s.tar.gz",
                manifest_path="MANIFEST.json", checksum_path="CHECKSUMS.sha256",
                success=True,
            )

        def _fake_build(self, context_dir, image_ref, runtime="podman"):
            return BuildResult(image_ref=image_ref, image_digest="sha256:x", success=True)

        def _fake_validate(self, version, artifacts_dir):
            return ReleaseValidationResult(
                valid=True, pip_bundle_valid=True, container_valid=True,
                manifest_valid=True,
            )

        monkeypatch.setattr(
            "general_ludd.runtime.pip_bundle.PipBundleBuilder.build", _fake_bundle
        )
        monkeypatch.setattr(
            "general_ludd.runtime.container.ContainerBuilder.build_image", _fake_build
        )
        monkeypatch.setattr(
            "general_ludd.runtime.release.ReleaseArtifactValidator.validate_release",
            _fake_validate,
        )
        report = ro.build_and_validate_release(
            version="1.2.3",
            output_dir=str(tmp_path),
            build_container=True,
            context_dir=str(tmp_path),
        )
        assert report["bundle"]["success"] is True
        assert report["container"]["success"] is True
        assert report["validation"]["valid"] is True


class TestProjectLogFilterWiring:
    """install_project_log_filter puts a ProjectLogFilter on the logger."""

    def test_filter_adds_project_id_attribute(self):
        import logging

        from general_ludd.logging.project_log import (
            ProjectLogFilter,
            install_project_log_filter,
        )

        lg = logging.getLogger("gludd.test.projectfilter")
        lg.filters = [f for f in lg.filters if not isinstance(f, ProjectLogFilter)]
        flt = install_project_log_filter(project_id="proj-z", logger=lg)
        assert isinstance(flt, ProjectLogFilter)
        # Idempotent: a second install returns the same filter.
        again = install_project_log_filter(logger=lg)
        assert again is flt
        rec = logging.LogRecord("n", logging.INFO, "p", 1, "msg", None, None)
        flt.filter(rec)
        assert rec.project_id == "proj-z"


class TestPRDeliveryWiring:
    """EventLoop._maybe_open_pr uses PRDelivery when config opts in."""

    @pytest.mark.asyncio
    async def test_open_pr_invoked_when_enabled(self, monkeypatch):
        from types import SimpleNamespace

        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop(config={"git_automation": {"open_pr": True, "base_branch": "main"}})
        calls = {}

        class _FakeDelivery:
            def __init__(self, **kwargs):
                calls["init"] = kwargs

            def push_and_create_pr(self, **kwargs):
                calls["pr"] = kwargs
                return {"pr_url": "https://example/pr/1", "error": None}

        monkeypatch.setattr(
            "general_ludd.git_automation.pr_delivery.PRDelivery", _FakeDelivery
        )
        todo = SimpleNamespace(todo_id="T1", title="My change")
        loop._maybe_open_pr(todo, "/tmp/wt", "gludd/T1")
        assert calls["pr"]["branch_name"] == "gludd/T1"
        assert calls["pr"]["todo_id"] == "T1"

    @pytest.mark.asyncio
    async def test_open_pr_skipped_when_disabled(self, monkeypatch):
        from types import SimpleNamespace

        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop(config={})  # no git_automation.open_pr
        called = {"n": 0}

        class _FakeDelivery:
            def __init__(self, **kwargs):
                called["n"] += 1

            def push_and_create_pr(self, **kwargs):
                called["n"] += 1
                return {}

        monkeypatch.setattr(
            "general_ludd.git_automation.pr_delivery.PRDelivery", _FakeDelivery
        )
        loop._maybe_open_pr(SimpleNamespace(todo_id="T1", title="x"), "/tmp/wt", "b")
        assert called["n"] == 0


class TestEvidenceCheckerWiring:
    """ReturnReviewer audits its model output for unsupported claims."""

    def test_reviewer_attaches_unsupported_claim_notes(self):
        from general_ludd.review.reviewer import ReturnReviewer
        from general_ludd.schemas.task_return import TaskReturn

        gateway = MagicMock()
        # The model returns a decision whose audit_notes contain an
        # unsupported factual claim (no file/line source).
        gateway.call_model = MagicMock(return_value=MagicMock(
            content=(
                '{"decision": "complete", "confidence": 0.9, '
                '"audit_notes": ["The tests pass and coverage is 95 percent."]}'
            )
        ))
        registry = MagicMock()
        registry.render = MagicMock(return_value="review prompt")
        reviewer = ReturnReviewer(gateway=gateway, prompt_registry=registry)
        tr = TaskReturn(
            return_id="RET-1", todo_id="T1", job_id="J1",
            playbook="code", queue="core", exit_code=0,
            result_summary="done",
        )
        decision = reviewer.review_return(tr, [], [])
        # An evidence-audit note flags the unsupported claim.
        assert any("unsupported" in n.lower() for n in decision.audit_notes)


class TestQueueRepositoryWiring:
    """seed_initial_queues uses QueueRepository for existence + create."""

    @pytest.mark.asyncio
    async def test_seed_uses_queue_repository(self):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from general_ludd.db.models import Base
        from general_ludd.db.repository import QueueRepository
        from general_ludd.db.session import seed_initial_queues

        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            n = await seed_initial_queues(session)
            await session.commit()
            assert n > 0
            # Idempotent: a second seed adds nothing.
            n2 = await seed_initial_queues(session)
            assert n2 == 0
            # The rows are readable through QueueRepository.
            repo = QueueRepository(session)
            core = await repo.get_by_name("core")
            assert core is not None
        await engine.dispose()


class TestBudgetControllerWiring:
    """BudgetController backs BudgetManager cost estimation + resource gating."""

    def test_budget_manager_estimates_call_cost(self):
        from general_ludd.controllers.budget_manager import BudgetManager

        bm = BudgetManager()
        cost = bm.estimate_call_cost(tokens=2000, cost_per_1k=0.5)
        assert cost == pytest.approx(1.0)

    def test_budget_manager_local_resource_gate(self):
        from general_ludd.controllers.budget_manager import BudgetManager
        from general_ludd.controllers.load_scrape import LoadSnapshot

        bm = BudgetManager()
        ok = LoadSnapshot(
            loadavg_1m=1.0, loadavg_5m=1.0, loadavg_10m=1.0,
            logical_cpu_count=8, cpu_percent=10.0,
            memory_available_percent=80.0, disk_free_percent=50.0,
            active_jobs=1,
        )
        res = bm.check_local_model_resources(ok)
        assert res["allowed"] is True


class TestSelfImprovementWorkflowWiring:
    """SelfImprovementWorkflow drives the /admin/self-improve/apply endpoint."""

    def test_apply_endpoint_uses_workflow(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from general_ludd.routers.self_improve import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        # Validation fails for a non-existent worktree -> not applied, no reload.
        resp = client.post(
            "/admin/self-improve/apply",
            json={"title": "x", "description": "y", "worktree_path": "/nonexistent-xyz"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "applied" in body
        assert "validation_passed" in body
        assert body["applied"] is False


class TestSlurmConnectionErrorWiring:
    """SlurmConnectionError raised when the REST API is unreachable."""

    def test_remote_status_unreachable_raises_connection_error(self, monkeypatch):
        import httpx

        from general_ludd.infra.slurm import SlurmAdapter, SlurmConnectionError

        def _boom(*args, **kwargs):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(httpx, "get", _boom)
        adapter = SlurmAdapter(api_url="http://slurm.invalid:6820")
        with pytest.raises(SlurmConnectionError):
            adapter.status("123")

    def test_remote_submit_timeout_raises_connection_error(self, monkeypatch):
        import httpx

        from general_ludd.infra.slurm import SlurmAdapter, SlurmConnectionError

        def _boom(*args, **kwargs):
            raise httpx.TimeoutException("timed out")

        monkeypatch.setattr(httpx, "post", _boom)
        adapter = SlurmAdapter(api_url="http://slurm.invalid:6820")
        with pytest.raises(SlurmConnectionError):
            adapter.submit("echo hi")


class TestAuditEventTypeWiring:
    """AuditEventType drives the AuditEventRepository convenience recorder."""

    @pytest.mark.asyncio
    async def test_record_typed_event_uses_audit_event_type(self):
        from general_ludd.db.models import AuditEventType
        from general_ludd.db.repository import AuditEventRepository

        repo = AuditEventRepository.__new__(AuditEventRepository)
        captured: dict = {}

        async def _fake_create(**kwargs):
            captured.update(kwargs)
            return MagicMock()

        repo.create = _fake_create  # type: ignore[method-assign]

        await repo.record_typed(
            AuditEventType.TODO_CREATED,
            entity_type="todo",
            entity_id="TODO-1",
            details={"x": 1},
        )
        assert captured["event_type"] == AuditEventType.TODO_CREATED.value
        assert captured["entity_id"] == "TODO-1"
        # details serialized to JSON string for storage.
        assert '"x": 1' in captured["details"]
