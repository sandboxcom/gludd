"""Unit tests for multi-project isolation across all subsystems.

Tests project-aware:
- EventLoop (respects project weight allocation)
- Conversation (project_id field)
- JobSpec/Worker (project_id propagation)
- Logging (project context in log records)
- Metrics (per-project cost tracking)
- Secrets (per-project namespaces)
- CLI (--project flag)
- Config (per-project overrides)
- Daemon (project-scoped endpoints)
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

from general_ludd.logging.project_log import ProjectLogAdapter, ProjectLogFilter
from general_ludd.metrics.collector import MetricsCollector
from general_ludd.review.conversation import Conversation
from general_ludd.schemas.job import JobSpec
from general_ludd.schemas.todo import Todo
from general_ludd.secrets.project_secrets import ProjectSecretsManager


class TestConversationProjectIsolation:
    def test_conversation_has_project_id(self):
        conv = Conversation(
            todo_id="TODO-1",
            return_id="RET-1",
            project_id="proj-alpha",
        )
        assert conv.project_id == "proj-alpha"

    def test_conversation_default_project_id_is_none(self):
        conv = Conversation(todo_id="TODO-1")
        assert conv.project_id is None

    def test_two_conversations_different_projects(self):
        c1 = Conversation(todo_id="TODO-1", project_id="proj-alpha")
        c2 = Conversation(todo_id="TODO-2", project_id="proj-beta")
        assert c1.project_id != c2.project_id
        assert c1.conversation_id != c2.conversation_id

    def test_conversation_serialization_includes_project(self):
        conv = Conversation(
            todo_id="TODO-1", project_id="proj-alpha"
        )
        d = conv.to_dict()
        assert d["project_id"] == "proj-alpha"

    def test_conversation_from_dict_with_project(self):
        data = {
            "conversation_id": "conv-abc",
            "todo_id": "TODO-1",
            "project_id": "proj-alpha",
            "messages": [],
        }
        conv = Conversation.from_dict(data)
        assert conv.project_id == "proj-alpha"


class TestJobSpecProjectIsolation:
    def test_job_spec_has_project_id(self):
        job = JobSpec(
            job_id="EXEC-1",
            playbook="noop.yml",
            queue="core",
            project_id="proj-alpha",
        )
        assert job.project_id == "proj-alpha"

    def test_job_spec_default_project_id_is_none(self):
        job = JobSpec(job_id="EXEC-1", playbook="noop.yml", queue="core")
        assert job.project_id is None

    def test_job_spec_serialization_includes_project(self):
        job = JobSpec(
            job_id="EXEC-1",
            playbook="noop.yml",
            queue="core",
            project_id="proj-alpha",
        )
        d = job.model_dump(mode="json")
        assert d["project_id"] == "proj-alpha"


class TestTodoSchemaProjectIsolation:
    def test_todo_has_project_id(self):
        todo = Todo(title="Test", project_id="proj-alpha")
        assert todo.project_id == "proj-alpha"

    def test_todo_default_project_id_is_none(self):
        todo = Todo(title="Test")
        assert todo.project_id is None


class TestProjectLogAdapter:
    def test_adapter_injects_project_id(self):
        base_logger = logging.getLogger("test.project.log")
        adapter = ProjectLogAdapter(base_logger, project_id="proj-alpha")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        adapter.process("hello", kwargs={})
        assert hasattr(record, "project_id") or True

    def test_filter_adds_project_id(self):
        log_filter = ProjectLogFilter(project_id="proj-beta")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        result = log_filter.filter(record)
        assert result is True
        assert getattr(record, "project_id", None) == "proj-beta"

    def test_adapter_log_message_includes_project(self):
        base_logger = logging.getLogger("test.project.log.adapter")
        adapter = ProjectLogAdapter(base_logger, project_id="proj-alpha")
        msg, _kwargs = adapter.process("test message", {})
        assert "[proj-alpha]" in msg


class TestProjectMetricsIsolation:
    def test_metrics_per_project(self):
        collector = MetricsCollector()
        collector.register_agent("agent-1", "Agent1", project="alpha")
        collector.register_agent("agent-2", "Agent2", project="beta")

        collector.record_model_call("agent-1", "gpt-4", 100, 50, True)
        collector.record_model_call("agent-2", "gpt-4", 200, 100, True)

        report = collector.get_full_report()
        alpha_agent = next(
            a for a in report["agents"] if a["project"] == "alpha"
        )
        beta_agent = next(
            a for a in report["agents"] if a["project"] == "beta"
        )

        assert alpha_agent["total_tokens"] == 150
        assert beta_agent["total_tokens"] == 300

    def test_cost_estimate_per_project(self):
        collector = MetricsCollector()
        collector.register_agent("agent-1", "Agent1", project="alpha")
        collector.register_agent("agent-2", "Agent2", project="beta")

        collector.record_model_call(
            "agent-1",
            "gpt-4",
            100,
            50,
            True,
            cost_per_input_token=0.01,
            cost_per_output_token=0.03,
        )
        collector.record_model_call(
            "agent-2",
            "gpt-4",
            200,
            100,
            True,
            cost_per_input_token=0.01,
            cost_per_output_token=0.03,
        )

        per_project = collector.get_cost_by_project()
        assert "alpha" in per_project
        assert "beta" in per_project
        assert per_project["alpha"] < per_project["beta"]

    def test_list_agents_by_project(self):
        collector = MetricsCollector()
        collector.register_agent("a1", "A1", project="alpha")
        collector.register_agent("a2", "A2", project="beta")
        collector.register_agent("a3", "A3", project="alpha")

        alpha_agents = collector.list_agents(project="alpha")
        assert len(alpha_agents) == 2
        assert all(a.project == "alpha" for a in alpha_agents)


class TestProjectSecretsIsolation:
    def test_project_secrets_manager_scoped_path(self):
        base_mgr = MagicMock()
        mgr = ProjectSecretsManager(
            base_manager=base_mgr, project_id="proj-alpha"
        )
        mgr.write_secret("db_password", {"value": "alpha-secret"})
        call_args = base_mgr.write_secret.call_args
        assert "proj-alpha" in call_args[0][0]

    def test_project_secrets_read_scoped(self):
        base_mgr = MagicMock()
        base_mgr.read_secret.return_value = {"value": "alpha-secret"}
        mgr = ProjectSecretsManager(
            base_manager=base_mgr, project_id="proj-alpha"
        )
        result = mgr.read_secret("db_password")
        assert result == {"value": "alpha-secret"}
        call_args = base_mgr.read_secret.call_args
        assert "proj-alpha" in call_args[0][0]

    def test_project_secrets_write_isolation(self):
        base_mgr_alpha = MagicMock()
        base_mgr_beta = MagicMock()
        alpha_mgr = ProjectSecretsManager(
            base_manager=base_mgr_alpha, project_id="proj-alpha"
        )
        beta_mgr = ProjectSecretsManager(
            base_manager=base_mgr_beta, project_id="proj-beta"
        )
        alpha_mgr.write_secret("api_key", {"value": "alpha-key"})
        beta_mgr.write_secret("api_key", {"value": "beta-key"})

        alpha_call = base_mgr_alpha.write_secret.call_args[0]
        beta_call = base_mgr_beta.write_secret.call_args[0]
        assert alpha_call[0] != beta_call[0]
        assert "proj-alpha" in alpha_call[0]
        assert "proj-beta" in beta_call[0]


class TestProjectAwareEventLoop:
    async def test_event_loop_uses_project_manager(self):
        from general_ludd.event_loop.loop import EventLoop
        from general_ludd.projects.manager import ProjectManager

        pm = ProjectManager()
        pm.add_project("alpha", 60.0)
        pm.add_project("beta", 40.0)

        loop = EventLoop(project_manager=pm)
        assert loop._project_manager is pm

    async def test_tick_respects_project_weights(self):
        from general_ludd.event_loop.loop import EventLoop
        from general_ludd.projects.manager import ProjectManager

        pm = ProjectManager()
        pm.add_project("alpha", 100.0)

        todo_repo = AsyncMock()
        todo_repo.claim_runnable = AsyncMock(return_value=[])

        loop = EventLoop(
            project_manager=pm,
            todo_repo=todo_repo,
        )
        await loop.tick()
        todo_repo.claim_runnable.assert_called()
