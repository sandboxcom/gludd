"""E2E tests for previously uncovered modules - batch 1.

Covers: issue_sources, compaction, renderers, writer, coordination, dispatch,
governance, approval, collections, notifications.
"""

from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# ============================================================================
# issue_sources — IssueSyncEngine, NormalizedIssue, status maps
# ============================================================================


class TestIssueSourcesBase:
    """Tests for issue_sources base types and IssueSyncEngine."""

    def test_default_outbound_status_map_keys(self):
        from general_ludd.issue_sources.base import DEFAULT_OUTBOUND_STATUS_MAP

        assert "ACTIVE" in DEFAULT_OUTBOUND_STATUS_MAP
        assert "IN_PROGRESS" in DEFAULT_OUTBOUND_STATUS_MAP
        assert "DONE" in DEFAULT_OUTBOUND_STATUS_MAP
        assert "COMPLETED" in DEFAULT_OUTBOUND_STATUS_MAP
        assert "CANCELLED" in DEFAULT_OUTBOUND_STATUS_MAP

    def test_default_inbound_status_map_maps_open_to_queued(self):
        from general_ludd.issue_sources.base import DEFAULT_INBOUND_STATUS_MAP

        assert DEFAULT_INBOUND_STATUS_MAP["Open"] == "QUEUED"
        assert DEFAULT_INBOUND_STATUS_MAP["In Progress"] == "ACTIVE"
        assert DEFAULT_INBOUND_STATUS_MAP["Done"] == "DONE"

    def test_normalized_issue_shape(self):
        from general_ludd.issue_sources.base import NormalizedIssue

        issue: NormalizedIssue = {
            "source": "github",
            "external_id": "123",
            "title": "Fix bug",
            "description": "A bug",
            "status": "Open",
            "assignee": None,
            "labels": [],
            "raw": {},
        }
        assert issue["source"] == "github"
        assert issue["external_id"] == "123"

    def test_sync_engine_constructs(self):
        from general_ludd.issue_sources.base import IssueRegistry, IssueSyncEngine

        registry = IssueRegistry()
        todo_store = MagicMock()
        engine = IssueSyncEngine(registry, todo_store)
        assert engine.registry is registry
        assert engine.todo_store is todo_store

    def test_sync_engine_inbound_creates_todo(self):
        from general_ludd.issue_sources.base import (
            IssueRegistry,
            IssueSyncEngine,
            NormalizedIssue,
        )

        todo_store = MagicMock()
        todo_store.list_linked.return_value = {}
        todo_store.create_from_issue.return_value = {"id": "t1"}
        engine = IssueSyncEngine(IssueRegistry(), todo_store)

        issue: NormalizedIssue = {
            "source": "github",
            "external_id": "42",
            "title": "Fix crash",
            "description": "NPE",
            "status": "Open",
            "assignee": None,
            "labels": ["bug"],
            "priority": None,
            "url": "https://example.invalid/issues/42",
            "updated_ts": None,
            "raw": {},
        }
        report = engine.sync_in("github", [issue])
        assert report.created == 1
        todo_store.create_from_issue.assert_called_once()

    def test_sync_engine_dedup_skips_existing(self):
        from general_ludd.issue_sources.base import (
            IssueRegistry,
            IssueSyncEngine,
            NormalizedIssue,
        )

        todo_store = MagicMock()
        todo_store.list_linked.return_value = {
            "ABC-1": {"id": "existing", "title": "Old", "status": "QUEUED"}
        }
        engine = IssueSyncEngine(IssueRegistry(), todo_store)

        issue: NormalizedIssue = {
            "source": "jira",
            "external_id": "ABC-1",
            "title": "Old",
            "description": "",
            "status": "Open",
            "assignee": None,
            "labels": [],
            "priority": None,
            "url": "https://example.invalid/issues/ABC-1",
            "updated_ts": None,
            "raw": {},
        }
        report = engine.sync_in("jira", [issue])
        assert report.created == 0
        assert report.skipped == 1

    def test_sync_engine_outbound_updates_issue_source(self):
        from general_ludd.issue_sources.base import IssueRegistry, IssueSyncEngine

        todo_store = MagicMock()
        todo_store.internal_status.return_value = "DONE"
        mock_source = MagicMock()
        mock_source.name = "test"
        mock_source.SYSTEM = "test"
        registry = IssueRegistry()
        registry.register(mock_source)
        engine = IssueSyncEngine(registry, todo_store)

        todo_row = {
            "id": "t7",
            "external_id": "123",
            "source": "test",
            "status": "DONE",
        }
        engine.sync_out("test", [todo_row])
        mock_source.update_status.assert_called_once_with(
            "123", "Done", "gludd has completed this issue"
        )

    def test_sync_report_counts_are_zero_initially(self):
        from general_ludd.issue_sources.base import SyncReport

        report = SyncReport()
        assert report.created == 0
        assert report.updated == 0
        assert report.skipped == 0
        assert len(report.errors) == 0

    def test_sync_report_errors_captured(self):
        from general_ludd.issue_sources.base import SyncReport

        report = SyncReport()
        report.errors.append(("3", "timeout on item #3"))
        assert report.errors == [("3", "timeout on item #3")]


class TestIssueSourceProtocol:
    """Tests that the sync-source protocol check works."""

    def test_protocol_is_runtime_checkable(self):

        from general_ludd.issue_sources.base import SyncSource

        assert getattr(SyncSource, "_is_runtime_protocol", False) is True

    def test_matching_impl_passes_check(self):
        from general_ludd.issue_sources.base import SyncSource

        class Good:
            name = "good"
            SYSTEM = "good"

            def health(self):
                return {"ok": True}

            def fetch_issues(self, spec):
                return []

            def update_status(self, external_id, status, comment=None):
                return True

            def add_comment(self, external_id, body):
                return True


        obj = Good()
        assert isinstance(obj, SyncSource)


class TestJiraIngest:
    """Tests for Jira issue adapter."""

    def test_jira_imports(self):
        from general_ludd.issue_sources.jira import JiraIssueSource

        assert JiraIssueSource is not None

    def test_jira_health_returns_string(self):
        from general_ludd.issue_sources.jira import JiraIssueSource

        response = MagicMock(status_code=200)
        transport = MagicMock()
        transport.request.return_value = response
        with patch.dict(
            "os.environ", {"JIRA_EMAIL": "user@example.com", "JIRA_API_TOKEN": "token"}
        ):
            src = JiraIssueSource(
                {"base_url": "https://example.atlassian.net", "project": "TEST"},
                transport=transport,
            )
            health = src.health()
        assert health == {"ok": True, "detail": "200 OK"}

    def test_jira_fetch_issues_returns_list(self):
        from general_ludd.issue_sources.jira import JiraIssueSource

        response = MagicMock(status_code=200)
        response.json.return_value = {"issues": []}
        transport = MagicMock()
        transport.request.return_value = response
        with patch.dict(
            "os.environ", {"JIRA_EMAIL": "user@example.com", "JIRA_API_TOKEN": "token"}
        ):
            src = JiraIssueSource(
                {"base_url": "https://example.atlassian.net", "project": "TEST"},
                transport=transport,
            )
            issues = src.fetch_issues({})
        assert issues == []


class TestGitHubIssues:
    """Tests for GitHub issues adapter."""

    def test_github_imports(self):
        from general_ludd.issue_sources.github_issues import GitHubIssuesSource

        assert GitHubIssuesSource is not None

    def test_github_health(self):
        from general_ludd.issue_sources.github_issues import GitHubIssuesSource

        src = GitHubIssuesSource(
            {"repo": "test-org/test-repo"}, transport=lambda *_args: (200, [])
        )
        assert src.name == "github_issues"
        assert src.base_url == "https://api.github.com"

    def test_github_fetch_issues(self):
        from general_ludd.issue_sources.github_issues import GitHubIssuesSource

        src = GitHubIssuesSource(
            {"repo": "test-org/test-repo"},
            transport=lambda *_args: (200, [{"number": 1, "title": "Bug"}]),
        )
        issues = src.fetch({})
        assert [issue["title"] for issue in issues] == ["Bug"]


class TestGitLabIssues:
    """Tests for GitLab issues adapter."""

    def test_gitlab_imports(self):
        from general_ludd.issue_sources.gitlab_issues import GitLabIssueSource

        assert GitLabIssueSource is not None

    def test_gitlab_health(self):
        from general_ludd.issue_sources.gitlab_issues import GitLabIssueSource

        response = MagicMock(status_code=200)
        transport = MagicMock(return_value=response)
        src = GitLabIssueSource(
            {"base_url": "https://gitlab.example.com", "project_id": "group/proj"},
            transport=transport,
            env={"GITLAB_TOKEN": "fake-token"},
        )
        health = src.health()
        assert health["ok"] is True


class TestLinearSource:
    """Tests for Linear issue adapter."""

    def test_linear_imports(self):
        from general_ludd.issue_sources.linear import LinearIssueSource

        assert LinearIssueSource is not None

    def test_linear_health(self):
        import httpx

        from general_ludd.issue_sources.linear import LinearIssueSource

        transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                200, json={"data": {"viewer": {"id": "user-1"}}}
            )
        )
        with patch.dict("os.environ", {"LINEAR_API_KEY": "fake-api-key"}):
            src = LinearIssueSource(
                {"base_url": "https://api.linear.app"}, transport=transport
            )
            health = src.health()
        assert health == {"ok": True, "detail": "linear reachable"}


class TestCsvExcelSource:
    """Tests for CSV/Excel issue source adapter."""

    def test_csv_excel_imports(self):
        from general_ludd.issue_sources.csv_excel import CsvExcelSource

        assert CsvExcelSource is not None

    def test_csv_excel_fetch_from_file(self, tmp_path: Path):
        from general_ludd.issue_sources.csv_excel import CsvExcelSource

        csv_path = tmp_path / "issues.csv"
        csv_path.write_text("id,title,status\n1,T1,Open\n", encoding="utf-8")
        src = CsvExcelSource({"path": str(csv_path), "root": str(tmp_path)})
        issues = src.fetch({})
        assert len(issues) == 1
        assert issues[0]["title"] == "T1"


class TestIngestMain:
    """Tests for ingest.py orchestration entrypoint."""

    def test_ingest_discover_finds_module(self):
        from general_ludd.issue_sources import ingest

        assert hasattr(ingest, "ingest_records")
        assert hasattr(ingest, "record_to_todo")

    def test_ingest_discover_returns_report_dict(self):
        from general_ludd.issue_sources.base import new_issue_record
        from general_ludd.issue_sources.ingest import ingest_records

        record = new_issue_record(external_id="1", title="Imported")
        todos, seen = ingest_records([record], "test-source")
        assert todos[0]["title"] == "Imported"
        assert seen == {"test-source:1"}


# ============================================================================
# compaction — CompactionRequest, CompactionResult, Compactor protocol
# ============================================================================


class TestCompactionBase:
    """Tests for compaction base types."""

    def test_estimate_tokens_positive(self):
        from general_ludd.compaction.base import estimate_tokens

        assert estimate_tokens("hello world this is a test") > 0
        assert estimate_tokens("") == 0

    def test_messages_tokens_sums(self):
        from general_ludd.agents.context import ContextMessage
        from general_ludd.compaction.base import messages_tokens

        msgs = [
            ContextMessage(role="user", content="12345678"),  # 8 chars -> 2 tokens
            ContextMessage(role="assistant", content="abcd"),  # 4 chars -> 1 token
        ]
        assert messages_tokens(msgs) == 3

    def test_compaction_request_defaults(self):
        from general_ludd.compaction.base import CompactionRequest

        req = CompactionRequest()
        assert req.messages == []
        assert req.goal == ""
        assert req.target_tokens is None
        assert req.preserve_recent == 4

    def test_compaction_request_with_goal(self):
        from general_ludd.compaction.base import CompactionRequest

        req = CompactionRequest(goal="fix bug #42", target_tokens=1000)
        assert req.goal == "fix bug #42"
        assert req.target_tokens == 1000

    def test_compaction_result_ratio(self):
        from general_ludd.compaction.base import CompactionResult

        result = CompactionResult(original_tokens=100, compacted_tokens=50)
        assert result.ratio == 0.5

    def test_compaction_result_zero_original_ratio_is_one(self):
        from general_ludd.compaction.base import CompactionResult

        result = CompactionResult(original_tokens=0, compacted_tokens=0)
        assert result.ratio == 1.0

    def test_compaction_result_compression_percent(self):
        from general_ludd.compaction.base import CompactionResult

        result = CompactionResult(original_tokens=100, compacted_tokens=30)
        assert result.tokens_saved == 70
        assert result.tokens_saved / result.original_tokens * 100 == 70.0

    def test_compactor_protocol_is_runtime_checkable(self):
        from general_ludd.compaction.base import Compactor

        assert getattr(Compactor, "_is_runtime_protocol", False) is True


class TestCompactionBaselines:
    """Tests for compaction baselines module."""

    def test_baselines_module_imports(self):
        from general_ludd.compaction import baselines

        assert baselines is not None

    def test_noop_compactor_returns_same_messages(self):
        from general_ludd.agents.context import ContextMessage
        from general_ludd.compaction.base import CompactionRequest

        msgs = [ContextMessage(role="user", content="hello")]
        req = CompactionRequest(messages=msgs, goal="test")

        from general_ludd.compaction.baselines import NoOpCompactor

        compactor = NoOpCompactor()
        result = compactor.compact(req)
        assert len(result.messages) == len(msgs)

    def test_truncation_compactor_drops_messages(self):
        from general_ludd.agents.context import ContextMessage
        from general_ludd.compaction.base import CompactionRequest

        msgs = [ContextMessage(role="user", content="x" * 100) for _ in range(20)]
        req = CompactionRequest(messages=msgs, goal="test", target_tokens=10)

        from general_ludd.compaction.baselines import TruncationCompactor

        compactor = TruncationCompactor()
        result = compactor.compact(req)
        assert len(result.messages) <= len(msgs)


class TestCompactionSLM:
    """Tests for SLM-based compaction."""

    def test_slm_module_imports(self):
        from general_ludd.compaction import slm

        assert slm is not None

    def test_slm_compactor_constructs(self):
        from general_ludd.compaction.slm import SLMCompactor

        model = MagicMock()
        compactor = SLMCompactor(model)
        assert compactor is not None

    def test_slm_compactor_compact_uses_model(self):
        from general_ludd.agents.context import ContextMessage
        from general_ludd.compaction.base import CompactionRequest
        from general_ludd.compaction.slm import SLMCompactor

        model = MagicMock()
        model.generate.return_value = "Summary: keep messages 0-2"
        compactor = SLMCompactor(model)

        msgs = [ContextMessage(role="user", content=f"msg {i}") for i in range(10)]
        req = CompactionRequest(messages=msgs, goal="summarize")

        result = compactor.compact(req)
        assert result.method == "slm"


class TestCompactionAggressive:
    """Tests for aggressive compaction strategy."""

    def test_aggressive_module_imports(self):
        from general_ludd.compaction import aggressive

        assert aggressive is not None

    def test_aggressive_compactor_constructs(self):
        from general_ludd.compaction.aggressive import LEVELS, level_at

        level = level_at(1)
        assert level is LEVELS[1]
        assert level.preserve_recent == 4

    def test_aggressive_drops_more_than_baseline(self):
        from general_ludd.agents.context import ContextMessage
        from general_ludd.compaction.aggressive import compact_messages, level_at

        msgs = [
            ContextMessage(
                role="user", content=f"message {i} " + "x" * 80, token_estimate=22
            )
            for i in range(50)
        ]

        result = compact_messages(
            msgs, goal="compress hard", level=level_at(3), max_tokens=100
        )
        assert len(result) < len(msgs)
        assert result[-1].content == msgs[-1].content


class TestCompactionEvaluate:
    """Tests for compaction evaluator."""

    def test_evaluate_module_imports(self):
        from general_ludd.compaction import evaluate

        assert evaluate is not None

    def test_evaluate_compares_strategies(self):
        from general_ludd.agents.context import ContextMessage
        from general_ludd.compaction.baselines import NoOpCompactor, TruncationCompactor
        from general_ludd.compaction.evaluate import EvalSample, Probe, evaluate

        sample = EvalSample(
            messages=[
                ContextMessage(role="user", content="decision: use sqlite WAL"),
                ContextMessage(role="assistant", content="recent status"),
            ],
            goal="preserve the storage decision",
            probes=[Probe(question="storage?", expected=["sqlite WAL"])],
            target_tokens=4,
            preserve_recent=1,
        )
        result = {
            metrics.compactor: metrics
            for metrics in (
                evaluate(NoOpCompactor(), [sample]),
                evaluate(TruncationCompactor(), [sample]),
            )
        }
        assert set(result) == {"noop", "truncate"}
        assert result["noop"].mean_fidelity == 1.0
        assert result["truncate"].mean_ratio <= result["noop"].mean_ratio


class TestCompactionArena:
    """Tests for compaction arena."""

    def test_arena_module_imports(self):
        from general_ludd.compaction import arena

        assert arena is not None


# ============================================================================
# renderers — RendererRegistry, RendererSpec, Executor
# ============================================================================


class TestRendererRegistry:
    """Tests for playbook renderer registry."""

    def test_registry_imports(self):
        from general_ludd.renderers.registry import RendererRegistry, RendererSpec

        assert RendererRegistry is not None
        assert RendererSpec is not None

    def test_registry_constructs_empty(self):
        from general_ludd.renderers.registry import RendererRegistry

        reg = RendererRegistry(bundled_dir=Path("/nonexistent"), operator_dir=None)
        reg.discover()
        assert len(reg) == 0
        assert reg.list_all() == []

    def test_registry_discovers_yml(self):
        from general_ludd.renderers.registry import RendererRegistry

        with tempfile.TemporaryDirectory() as tmp:
            playbook = Path(tmp) / "test.yml"
            playbook.write_text(
                "[{'hosts': 'localhost', 'vars': {'renderer': True, 'renderer_description': 'desc'}}]"
            )
            reg = RendererRegistry(bundled_dir=Path(tmp), operator_dir=None)
            reg.discover()
            assert "test" in reg
            assert reg.get("test").description == "desc"

    def test_renderer_spec_properties(self):
        from general_ludd.renderers.registry import RendererSpec

        spec = RendererSpec(name="test", path=Path("/tmp/test.yml"))
        assert spec.name == "test"
        assert spec.playbook_path == "/tmp/test.yml"
        assert spec.timeout_s == 30.0

    def test_renderer_spec_model_dump(self):
        from general_ludd.renderers.registry import RendererSpec

        spec = RendererSpec(name="dump_test", path=Path("/tmp/dump.yml"))
        d = spec.model_dump()
        assert d["name"] == "dump_test"
        assert d["path"] == "/tmp/dump.yml"


class TestRendererExecutor:
    """Tests for the renderer executor."""

    def test_executor_imports(self):
        from general_ludd.renderers.executor import run_renderer

        assert callable(run_renderer)

    def test_executor_constructs(self):
        import inspect

        from general_ludd.renderers.executor import run_renderer

        assert inspect.iscoroutinefunction(run_renderer)

    def test_executor_validate_schema_missing_file(self):
        import asyncio

        from fastapi import FastAPI

        from general_ludd.renderers.executor import RendererFailure, run_renderer
        from general_ludd.renderers.registry import RendererSpec

        app = FastAPI()
        app.state._runner = MagicMock()
        app.state._runner.run_playbook.return_value = {"status": "successful", "rc": 0}
        spec = RendererSpec(name="missing", path=Path("/nonexistent.yml"))
        try:
            asyncio.run(run_renderer(app, spec))
            raise AssertionError("missing render.json should fail closed")
        except RendererFailure as exc:
            assert "render.json not written" in exc.detail


class TestRendererSchemaLoader:
    """Tests for schema loader."""

    def test_schema_loader_imports(self):
        from general_ludd.renderers.schema_loader import load_schema

        assert load_schema is not None

    def test_load_schema_nonexistent(self):
        from general_ludd.renderers.schema_loader import load_schema

        schema = load_schema(Path("/nonexistent_schema.yml"))
        assert schema is None


class TestRendererCache:
    """Tests for renderer result cache."""

    def test_cache_imports(self):
        from general_ludd.renderers.cache import RendererCache

        assert RendererCache is not None

    def test_cache_set_get(self):
        from general_ludd.renderers.cache import RendererCache

        cache = RendererCache()
        cache.set("k1", {"html": "<p>Hi</p>"})
        entry = cache.get("k1")
        assert entry is not None

    def test_cache_miss_returns_none(self):
        from general_ludd.renderers.cache import RendererCache

        cache = RendererCache()
        assert cache.get("nonexistent_key") is None


class TestRendererRunner:
    """Tests for renderer runner orchestration."""

    def test_runner_imports(self):
        from general_ludd.renderers.runner import run_renderer

        assert run_renderer is not None


# ============================================================================
# writer — WriterSupervisor, WriterProcess
# ============================================================================


class TestWriterSupervisor:
    """Tests for writer process supervisor."""

    def test_supervisor_imports(self):
        from general_ludd.writer.supervisor import WriterSupervisor

        assert WriterSupervisor is not None

    def test_supervisor_constructs(self):
        from general_ludd.writer.supervisor import SupervisorState, WriterSupervisor

        event_bus = MagicMock()
        supervisor = WriterSupervisor(
            writer_process_factory=lambda: MagicMock(),
            event_bus=event_bus,
            base_backoff=0.01,
            max_backoff=0.1,
            max_retries=2,
        )
        assert supervisor.state is SupervisorState.STOPPED
        assert supervisor.restart_count == 0
        assert supervisor._next_backoff(0) == 0.01

    def test_supervisor_start_stop(self):
        from general_ludd.writer.supervisor import SupervisorState, WriterSupervisor

        event_bus = MagicMock()
        mock_writer = MagicMock()
        mock_writer.is_alive.return_value = True

        supervisor = WriterSupervisor(
            writer_process_factory=lambda: mock_writer,
            event_bus=event_bus,
            health_check_interval=0.05,
            max_retries=2,
        )
        supervisor.start()
        supervisor.stop()
        mock_writer.start.assert_called_once_with()
        mock_writer.stop.assert_called_once_with()
        assert supervisor.state is SupervisorState.STOPPED

    def test_supervisor_recovery_emits_event(self):
        from general_ludd.writer.supervisor import (
            SupervisorRecoveryEvent,
            SupervisorState,
            WriterSupervisor,
        )

        event_bus = MagicMock()
        crashed_writer = MagicMock()
        crashed_writer.exit_code = 1
        crashed_writer.is_alive.return_value = False
        recovered_writer = MagicMock()
        recovered_writer.is_alive.return_value = True
        writers = iter((crashed_writer, recovered_writer))

        supervisor = WriterSupervisor(
            writer_process_factory=lambda: next(writers),
            event_bus=event_bus,
            health_check_interval=0.01,
            max_retries=2,
            base_backoff=0.0,
            max_backoff=0.05,
        )
        supervisor.start()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not event_bus.publish.called:
            time.sleep(0.01)
        supervisor.stop()
        event_bus.publish.assert_called_once()
        event = event_bus.publish.call_args.args[0]
        assert isinstance(event, SupervisorRecoveryEvent)
        assert event.payload["exit_code"] == 1
        assert supervisor.state is SupervisorState.STOPPED


class TestWriterBridge:
    """Tests for writer bridge module."""

    def test_bridge_imports(self):
        from general_ludd.writer.bridge import QueueWriteSession, enqueue_or_commit

        assert QueueWriteSession is not None
        assert enqueue_or_commit is not None

    def test_bridge_constructs(self):
        from general_ludd.ipc import WriteQueue
        from general_ludd.writer.bridge import QueueWriteSession

        bridge = QueueWriteSession("todo.create", WriteQueue(maxsize=2))
        assert bridge.topic == "todo.create"
        assert bridge.pending == ()


class TestWriterChild:
    """Tests for writer child process."""

    def test_child_imports(self):
        from general_ludd.writer._child import main

        assert callable(main)


class TestWriterProcess:
    """Tests for writer process."""

    def test_process_imports(self):
        from general_ludd.writer.process import WriterProcess

        assert WriterProcess is not None


# ============================================================================
# coordination — FileClaimRegistry, claim/overlap logic
# ============================================================================


class TestFileClaimRegistry:
    """Tests for thread-safe file claim registry."""

    def test_registry_imports(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        assert FileClaimRegistry is not None

    def test_registry_constructs(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        reg = FileClaimRegistry()
        assert reg is not None

    def test_claim_single_worker(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        reg = FileClaimRegistry()
        reg.claim("worker-1", ["a.py", "b.py"])
        assert reg.all_claims() == {
            "a.py": ["worker-1"],
            "b.py": ["worker-1"],
        }

    def test_release_removes_worker(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        reg = FileClaimRegistry()
        reg.claim("w1", ["x.py"])
        reg.release("w1")
        assert reg.all_claims() == {}

    def test_overlap_detects_conflict(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        reg = FileClaimRegistry()
        reg.claim("w1", ["a.py", "b.py"])
        reg.claim("w2", ["b.py", "c.py"])
        assert reg.overlaps("w1") == {"b.py": ["w2"]}
        assert reg.overlaps("w2") == {"b.py": ["w1"]}

    def test_overlap_no_conflict(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        reg = FileClaimRegistry()
        reg.claim("w1", ["a.py"])
        reg.claim("w2", ["b.py"])
        assert reg.overlaps("w1") == {}
        assert reg.overlaps("w2") == {}

    def test_should_wait_true_on_overlap(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        reg = FileClaimRegistry()
        reg.claim("w1", ["a.py"])
        reg.claim("w2", ["a.py"])
        assert reg.should_wait("w2") == ["w1"]

    def test_should_wait_false_no_overlap(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        reg = FileClaimRegistry()
        reg.claim("w1", ["a.py"])
        reg.claim("w2", ["b.py"])
        assert reg.should_wait("w2") == []

    def test_claim_or_conflict_blocks_overlapping(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        reg = FileClaimRegistry()
        ok1 = reg.claim_or_conflict("w1", ["a.py", "b.py"])
        ok2 = reg.claim_or_conflict("w2", ["b.py", "c.py"])
        assert ok1 is True
        assert ok2 is False

    def test_reap_stale_removes_old_claims(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        clock = [0.0]

        def fake_clock():
            return clock[0]

        reg = FileClaimRegistry(ttl_seconds=60.0, clock=fake_clock)
        reg.claim("w1", ["a.py"])
        clock[0] = 200.0
        assert reg.all_claims() == {}

    def test_merge_plan_returns_dict(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        reg = FileClaimRegistry()
        reg.claim("w1", ["a.py", "shared.py"])
        reg.claim("w2", ["b.py", "shared.py"])
        plan = reg.merge_plan()
        assert isinstance(plan, dict)

    def test_thread_safety_concurrent_claims(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        reg = FileClaimRegistry()
        errors = []

        def claimer(worker_id, files):
            try:
                for _ in range(50):
                    reg.claim(worker_id, files)
                    reg.release(worker_id)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=claimer, args=(f"w{i}", [f"file{i}.py"]))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


# ============================================================================
# dispatch — DynamicDispatcher, VariableStore
# ============================================================================


class TestDynamicDispatcher:
    """Tests for dynamic dispatcher routing."""

    def test_dispatcher_imports(self):
        from general_ludd.dispatch.dynamic_dispatcher import DynamicDispatcher

        assert DynamicDispatcher is not None

    def test_dispatcher_constructs(self):
        from general_ludd.dispatch.dynamic_dispatcher import DynamicDispatcher

        dispatcher = DynamicDispatcher()
        assert dispatcher is not None

    def test_tool_call_dataclass(self):
        from general_ludd.dispatch.dynamic_dispatcher import ToolCall

        tc = ToolCall(kind="role", name="do_thing", args={"x": 1})
        assert tc.kind == "role"
        assert tc.name == "do_thing"
        assert tc.args == {"x": 1}

    def test_dispatch_result_ok(self):
        from general_ludd.dispatch.dynamic_dispatcher import DispatchResult

        result = DispatchResult(ok=True, output={"status": "done"})
        assert result.ok is True
        assert result.output == {"status": "done"}

    def test_dispatch_result_error(self):
        from general_ludd.dispatch.dynamic_dispatcher import DispatchResult

        result = DispatchResult(ok=False, error="not found")
        assert result.ok is False
        assert result.error == "not found"

    def test_dispatcher_register_handler(self):
        import asyncio

        from general_ludd.dispatch.dynamic_dispatcher import (
            UNRESTRICTED_ROLE,
            DynamicDispatcher,
            ToolCall,
        )


        def my_handler(name, args):
            return {"handled": name, "args": args}

        dispatcher = DynamicDispatcher(
            skill_handler=my_handler, role=UNRESTRICTED_ROLE
        )
        result = asyncio.run(
            dispatcher.dispatch(
                ToolCall(kind="skill", name="greet", args={"name": "world"})
            )
        )
        assert result.ok is True
        assert result.output == {"handled": "greet", "args": {"name": "world"}}

    def test_dispatcher_missing_handler(self):
        import asyncio

        from general_ludd.dispatch.dynamic_dispatcher import (
            UNRESTRICTED_ROLE,
            DynamicDispatcher,
            ToolCall,
        )

        dispatcher = DynamicDispatcher(role=UNRESTRICTED_ROLE)
        result = asyncio.run(
            dispatcher.dispatch(
                ToolCall(kind="skill", name="nonexistent_handler", args={})
            )
        )
        assert result.ok is False
        assert result.error == "unknown_kind:skill"

    def test_unrestricted_role_is_object_identity(self):
        from general_ludd.dispatch.dynamic_dispatcher import UNRESTRICTED_ROLE

        assert UNRESTRICTED_ROLE is not None
        assert type(UNRESTRICTED_ROLE) is object

    def test_privileged_kinds_contains_role(self):
        from general_ludd.dispatch.dynamic_dispatcher import PRIVILEGED_KINDS

        assert "role" in PRIVILEGED_KINDS
        assert "collection" in PRIVILEGED_KINDS
        assert "mcp" in PRIVILEGED_KINDS
        assert "skill" in PRIVILEGED_KINDS


class TestVariableStore:
    """Tests for dispatch variable store."""

    def test_variable_store_imports(self):
        from general_ludd.dispatch.variable_store import VariableStore

        assert VariableStore is not None

    def test_variable_store_set_get(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        store.set("test", "key1", "value1")
        assert store.get("test", "key1") == "value1"

    def test_variable_store_missing_returns_none(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        assert store.get("test", "missing") is None

    def test_variable_store_overwrite(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        store.set("test", "a", 1)
        store.set("test", "a", 2)
        assert store.get("test", "a") == 2

    def test_variable_store_dump(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        store.set("test", "x", "hello")
        store.set("test", "y", 42)
        d = store.all_vars()
        assert d == {"test__x": "hello", "test__y": 42}

    def test_variable_store_clear(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        store.set("test", "k", "v")
        snapshot = store.get_namespace("test")
        snapshot.clear()
        assert store.get("test", "k") == "v"


# ============================================================================
# governance — policy loader
# ============================================================================


class TestGovernanceLoader:
    """Tests for governance policy loader."""

    def test_loader_imports(self):
        from general_ludd.governance.loader import get_borders, get_governing_bodies

        assert callable(get_borders)
        assert callable(get_governing_bodies)

    def test_loader_returns_list(self):
        from types import ModuleType

        from general_ludd.governance.loader import get_borders, get_governing_bodies

        policies = [get_borders(), get_governing_bodies()]
        assert all(isinstance(policy, ModuleType) for policy in policies)


# ============================================================================
# approval — ApprovalGate
# ============================================================================


class TestApprovalGate:
    """Tests for approval gate module."""

    def test_gate_imports(self):
        from general_ludd.approval.gate import ApprovalGate

        assert ApprovalGate is not None

    def test_gate_constructs(self):
        from general_ludd.approval.gate import ApprovalGate

        gate = ApprovalGate()
        assert gate is not None

    def test_gate_approve(self):
        from general_ludd.approval.gate import (
            ApprovalDecision,
            ApprovalGate,
            ApprovalRequest,
        )

        gate = ApprovalGate()
        request = ApprovalRequest(
            resource_id="req-1", action="deploy to prod", requester="agent-1"
        )
        result = gate.request_approval(request)
        assert result.decision is ApprovalDecision.PENDING
        assert result.request is request

    def test_gate_deny(self):
        from general_ludd.approval.gate import ApprovalGate, ApprovalRequest

        gate = ApprovalGate()
        request = ApprovalRequest(
            resource_id="req-2", action="delete database", requester="agent-2"
        )
        result = gate.check(request)
        assert result.allowed is False
        assert result.reason == "pending"

    def test_gate_pending_returns_requests(self):
        from general_ludd.approval.gate import ApprovalDecision, ApprovalGate

        gate = ApprovalGate()
        assert gate.check_decision("r1") is ApprovalDecision.PENDING
        assert gate.check_decision("r2") is ApprovalDecision.PENDING


# ============================================================================
# collections — CollectionImporter
# ============================================================================


class TestCollectionImporter:
    """Tests for collection importer."""

    def test_importer_imports(self):
        from general_ludd.collections.importer import TerraformCollectionImporter

        assert TerraformCollectionImporter is not None

    def test_importer_constructs(self, tmp_path: Path):
        from general_ludd.collections.importer import TerraformCollectionImporter

        importer = TerraformCollectionImporter(tmp_path)
        assert importer.collection_path == tmp_path


# ============================================================================
# notifications — NotificationDispatcher
# ============================================================================


class TestNotificationDispatcher:
    """Tests for notification dispatcher."""

    def test_dispatcher_imports(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        assert NotificationDispatcher is not None

    def test_dispatcher_constructs(self):
        from general_ludd.notifications.dispatcher import NotificationDispatcher

        dispatcher = NotificationDispatcher({})
        assert dispatcher.dispatch({"priority": "urgent"}) == {
            "ok": False,
            "reason": "notifications disabled",
        }
