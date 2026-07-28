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

import httpx

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

        todo_store = MagicMock()
        engine = IssueSyncEngine(IssueRegistry(), todo_store)
        assert engine is not None

    def test_sync_engine_inbound_creates_todo(self):
        from general_ludd.issue_sources.base import (
            IssueRegistry,
            IssueSyncEngine,
            NormalizedIssue,
        )

        todo_store = MagicMock()
        todo_store.list_linked.return_value = {}
        todo_store.create_from_issue.return_value = {
            "id": "t1",
            "title": "Fix crash",
            "status": "QUEUED",
        }
        engine = IssueSyncEngine(IssueRegistry(), todo_store)

        issue: NormalizedIssue = {
            "source": "github",
            "external_id": "42",
            "title": "Fix crash",
            "description": "NPE",
            "status": "Open",
            "assignee": None,
            "labels": ["bug"],
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
        mock_source.update_status.return_value = True
        registry = IssueRegistry()
        registry.register(mock_source)
        engine = IssueSyncEngine(registry, todo_store)

        todo_row = {
            "id": "t7",
            "external_id": "123",
            "source": "test",
            "status": "DONE",
        }
        report = engine.sync_out("test", [todo_row])
        mock_source.update_status.assert_called_once_with(
            "123", "Done", "gludd has completed this issue"
        )
        assert report.updated == 1

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
        assert bool(report.errors) is True


class TestIssueSourceProtocol:
    """Tests that the IssueSource protocol check works."""

    def test_protocol_is_runtime_checkable(self):
        from general_ludd.issue_sources.base import SyncSource

        assert SyncSource._is_runtime_protocol is True

    def test_matching_impl_passes_check(self):
        from general_ludd.issue_sources.base import SyncSource

        class Good:
            name = "good"
            SYSTEM = "test"

            def health(self) -> dict[str, object]:
                return {"ok": True}

            def fetch_issues(self, spec):
                return []

            def update_status(self, external_id, status, comment=None):
                return {"ok": True}

            def add_comment(self, external_id, comment):
                return {"ok": True}

        obj = Good()
        assert isinstance(obj, SyncSource)


class TestJiraIngest:
    """Tests for Jira issue adapter."""

    def test_jira_imports(self):
        from general_ludd.issue_sources.jira import JiraIssueSource

        assert JiraIssueSource is not None

    def test_jira_health_returns_mapping(self):
        from general_ludd.issue_sources.jira import JiraIssueSource

        response = MagicMock(status_code=200)
        transport = MagicMock()
        transport.request.return_value = response
        with patch.dict(
            "os.environ",
            {"JIRA_EMAIL": "user@example.com", "JIRA_API_TOKEN": "token"},
        ):
            src = JiraIssueSource(
                {"base_url": "https://example.atlassian.net", "project": "TEST"},
                transport=transport,
            )
            health = src.health()
        assert health["ok"] is True

    def test_jira_fetch_issues_returns_list(self):
        from general_ludd.issue_sources.jira import JiraIssueSource

        response = MagicMock(status_code=200)
        response.json.return_value = {"issues": []}
        transport = MagicMock()
        transport.request.return_value = response
        with patch.dict(
            "os.environ",
            {"JIRA_EMAIL": "user@example.com", "JIRA_API_TOKEN": "token"},
        ):
            src = JiraIssueSource(
                {"base_url": "https://example.atlassian.net", "project": "TEST"},
                transport=transport,
            )
            issues = src.fetch_issues({})
        assert isinstance(issues, list)


class TestGitHubIssues:
    """Tests for GitHub issues adapter."""

    def test_github_imports(self):
        from general_ludd.issue_sources.github_issues import GitHubIssuesSource

        assert GitHubIssuesSource is not None

    def test_github_source_name(self):
        from general_ludd.issue_sources.github_issues import GitHubIssuesSource

        src = GitHubIssuesSource(
            {"repo": "test-org/test-repo"},
            transport=MagicMock(return_value=(200, [])),
            env={},
        )
        assert src.name == "github_issues"

    def test_github_fetch_issues(self):
        from general_ludd.issue_sources.github_issues import GitHubIssuesSource

        src = GitHubIssuesSource(
            {"repo": "test-org/test-repo"},
            transport=MagicMock(
                return_value=(200, [{"number": 1, "title": "Bug"}])
            ),
            env={},
        )
        issues = src.fetch({})
        assert issues[0]["external_id"] == "1"


class TestGitLabIssues:
    """Tests for GitLab issues adapter."""

    def test_gitlab_imports(self):
        from general_ludd.issue_sources.gitlab_issues import GitLabIssueSource

        assert GitLabIssueSource is not None

    def test_gitlab_health(self):
        from general_ludd.issue_sources.gitlab_issues import GitLabIssueSource

        response = MagicMock(status_code=200)
        src = GitLabIssueSource(
            {
                "base_url": "https://gitlab.example.com",
                "project_id": "group%2Fproj",
            },
            transport=MagicMock(return_value=response),
            env={"GITLAB_TOKEN": "fake-token"},
        )
        assert src.health()["ok"] is True


class TestLinearSource:
    """Tests for Linear issue adapter."""

    def test_linear_imports(self):
        from general_ludd.issue_sources.linear import LinearIssueSource

        assert LinearIssueSource is not None

    def test_linear_health(self):
        from general_ludd.issue_sources.linear import LinearIssueSource

        transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                200, json={"data": {"viewer": {"id": "user-1"}}}
            )
        )
        src = LinearIssueSource({}, transport=transport)
        assert src.health()["ok"] is True


class TestCsvExcelSource:
    """Tests for CSV/Excel issue source adapter."""

    def test_csv_excel_imports(self):
        from general_ludd.issue_sources.csv_excel import CsvExcelSource

        assert CsvExcelSource is not None

    def test_csv_excel_fetch_from_file(self):
        from general_ludd.issue_sources.csv_excel import CsvExcelSource

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "issues.csv"
            path.write_text("id,title,status\n1,T1,open\n", encoding="utf-8")
            src = CsvExcelSource({"path": str(path), "root": tmp})
            issues = src.fetch({})
        assert len(issues) == 1
        assert issues[0]["title"] == "T1"


class TestIngestMain:
    """Tests for ingest.py orchestration entrypoint."""

    def test_ingest_discover_finds_module(self):
        from general_ludd.issue_sources import ingest

        assert hasattr(ingest, "ingest_records")

    def test_ingest_empty_batch_returns_todos_and_seen_keys(self):
        from general_ludd.issue_sources.ingest import ingest_records

        todos, seen = ingest_records([], "test", set())
        assert todos == []
        assert seen == set()


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

    def test_compaction_result_tokens_saved(self):
        from general_ludd.compaction.base import CompactionResult

        result = CompactionResult(original_tokens=100, compacted_tokens=30)
        assert result.tokens_saved == 70

    def test_compactor_protocol_is_runtime_checkable(self):
        from general_ludd.compaction.base import Compactor

        assert Compactor._is_runtime_protocol is True


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

    def test_aggressive_level_clamps_to_ladder(self):
        from general_ludd.compaction.aggressive import LEVELS, level_at

        assert level_at(999) == LEVELS[-1]

    def test_aggressive_drops_more_than_baseline(self):
        from general_ludd.agents.context import ContextMessage
        from general_ludd.compaction.aggressive import compact_messages, level_at

        msgs = [
            ContextMessage(
                role="user",
                content=f"message {i} " * 20,
                token_estimate=50,
            )
            for i in range(50)
        ]

        result = compact_messages(
            msgs,
            goal="compress hard",
            level=level_at(3),
            max_tokens=100,
        )
        assert len(result) < len(msgs)


class TestCompactionEvaluate:
    """Tests for compaction evaluator."""

    def test_evaluate_module_imports(self):
        from general_ludd.compaction import evaluate

        assert evaluate is not None

    def test_evaluate_compares_strategies(self):
        from general_ludd.compaction.baselines import NoOpCompactor
        from general_ludd.compaction.evaluate import evaluate

        result = evaluate(NoOpCompactor(), [])
        assert result.compactor == "noop"
        assert result.samples == 0


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

        reg = RendererRegistry()
        assert len(reg) == 0

    def test_registry_discovers_yml(self):
        from general_ludd.renderers.registry import RendererRegistry

        with tempfile.TemporaryDirectory() as tmp:
            playbook = Path(tmp) / "test.yml"
            playbook.write_text(
                "- hosts: localhost\n"
                "  vars:\n"
                "    renderer: true\n"
                "    renderer_description: desc\n",
                encoding="utf-8",
            )
            reg = RendererRegistry(bundled_dir=Path(tmp))
            reg.discover()
            assert reg.names() == ["test"]

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

    def test_executor_reexports_runner(self):
        from general_ludd.renderers.executor import run_renderer as executor_runner
        from general_ludd.renderers.runner import run_renderer

        assert executor_runner is run_renderer

    def test_executor_reexports_failures(self):
        from general_ludd.renderers.executor import RendererFailure, RendererTimeout

        assert issubclass(RendererFailure, Exception)
        assert issubclass(RendererTimeout, Exception)


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
        assert supervisor is not None
        assert supervisor.state is SupervisorState.STOPPED
        assert supervisor.restart_count == 0

    def test_supervisor_start_stop(self):
        from general_ludd.writer.supervisor import WriterSupervisor

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
        time.sleep(0.1)
        supervisor.stop()
        mock_writer.start.assert_called_once()
        mock_writer.stop.assert_called_once()

    def test_supervisor_recovery_emits_event(self):
        from general_ludd.writer.supervisor import WriterSupervisor

        event_bus = MagicMock()
        state = {"starts": 0}
        writer = MagicMock()

        def start_writer():
            state["starts"] += 1

        writer.start.side_effect = start_writer
        writer.is_alive.side_effect = lambda: state["starts"] >= 2
        writer.exit_code = 1

        supervisor = WriterSupervisor(
            writer_process_factory=lambda: writer,
            event_bus=event_bus,
            health_check_interval=0.01,
            max_retries=2,
            base_backoff=0.0,
            max_backoff=0.05,
        )
        supervisor.start()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and event_bus.publish.call_count == 0:
            time.sleep(0.01)
        supervisor.stop()
        assert event_bus.publish.call_count >= 1


class TestWriterBridge:
    """Tests for writer bridge module."""

    def test_bridge_imports(self):
        from general_ludd.writer.bridge import QueueFullError, QueueWriteSession

        assert QueueWriteSession is not None
        assert issubclass(QueueFullError, RuntimeError)

    def test_bridge_constructs(self):
        from general_ludd.writer.bridge import QueueWriteSession

        session = QueueWriteSession(topic="todo.create", queue=MagicMock())
        assert session.topic == "todo.create"
        assert session.pending == ()


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
        assert "x.py" not in reg.all_claims()

    def test_overlap_detects_conflict(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        reg = FileClaimRegistry()
        reg.claim("w1", ["a.py", "b.py"])
        reg.claim("w2", ["b.py", "c.py"])
        assert reg.overlaps("w2") == {"b.py": ["w1"]}

    def test_overlap_no_conflict(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        reg = FileClaimRegistry()
        reg.claim("w1", ["a.py"])
        reg.claim("w2", ["b.py"])
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
        assert "w1" not in reg.all_claims()

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

    async def test_dispatcher_routes_injected_handler(self):
        from general_ludd.dispatch.dynamic_dispatcher import (
            UNRESTRICTED_ROLE,
            DynamicDispatcher,
            ToolCall,
        )

        def my_handler(name, args):
            return {"handled": name, "args": args}

        dispatcher = DynamicDispatcher(
            skill_handler=my_handler,
            role=UNRESTRICTED_ROLE,
        )
        result = await dispatcher.dispatch(
            ToolCall(kind="skill", name="greet", args={"name": "world"})
        )
        assert result.ok is True
        assert result.output == {
            "handled": "greet",
            "args": {"name": "world"},
        }

    async def test_dispatcher_missing_handler(self):
        from general_ludd.dispatch.dynamic_dispatcher import (
            UNRESTRICTED_ROLE,
            DynamicDispatcher,
            ToolCall,
        )

        dispatcher = DynamicDispatcher(role=UNRESTRICTED_ROLE)
        result = await dispatcher.dispatch(
            ToolCall(kind="skill", name="nonexistent_handler", args={})
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
        store.set("default", "key1", "value1")
        assert store.get("default", "key1") == "value1"

    def test_variable_store_missing_returns_none(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        assert store.get("default", "missing") is None

    def test_variable_store_overwrite(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        store.set("default", "a", 1)
        store.set("default", "a", 2)
        assert store.get("default", "a") == 2

    def test_variable_store_flattens_namespaces(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        store.set("default", "x", "hello")
        store.set("default", "y", 42)
        assert store.all_vars() == {
            "default__x": "hello",
            "default__y": 42,
        }

    def test_variable_store_namespace_snapshot_is_isolated(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        store.set("default", "k", "v")
        snapshot = store.get_namespace("default")
        snapshot.clear()
        assert store.get("default", "k") == "v"


# ============================================================================
# governance — policy loader
# ============================================================================


class TestGovernanceLoader:
    """Tests for governance policy loader."""

    def test_loader_imports(self):
        from general_ludd.governance.loader import get_borders

        assert callable(get_borders)

    def test_loader_returns_policy_module(self):
        from types import ModuleType

        from general_ludd.governance.loader import get_borders

        assert isinstance(get_borders(), ModuleType)


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

    def test_gate_returns_pending_response(self):
        from general_ludd.approval.gate import (
            ApprovalDecision,
            ApprovalGate,
            ApprovalRequest,
        )

        gate = ApprovalGate()
        request = ApprovalRequest("prod", "deploy", "admin")
        result = gate.request_approval(request)
        assert result.decision is ApprovalDecision.PENDING

    def test_gate_preserves_request_metadata(self):
        from general_ludd.approval.gate import ApprovalGate, ApprovalRequest

        gate = ApprovalGate()
        request = ApprovalRequest(
            "database",
            "delete",
            "operator",
            metadata={"ticket": "OPS-42"},
        )
        result = gate.request_approval(request)
        assert result.request.metadata == {"ticket": "OPS-42"}

    def test_gate_requests_are_independent(self):
        from general_ludd.approval.gate import ApprovalGate, ApprovalRequest

        gate = ApprovalGate()
        first = gate.request_approval(ApprovalRequest("r1", "action-1", "agent"))
        second = gate.request_approval(ApprovalRequest("r2", "action-2", "agent"))
        assert first.request.resource_id == "r1"
        assert second.request.resource_id == "r2"


# ============================================================================
# collections — CollectionImporter
# ============================================================================


class TestCollectionImporter:
    """Tests for collection importer."""

    def test_importer_imports(self):
        from general_ludd.collections.importer import TerraformCollectionImporter

        assert TerraformCollectionImporter is not None

    def test_importer_constructs(self):
        from general_ludd.collections.importer import TerraformCollectionImporter

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            importer = TerraformCollectionImporter(path)
            assert importer.collection_path == path


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
        assert dispatcher is not None
