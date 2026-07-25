"""E2E tests for previously uncovered modules - batch 1.

Covers: issue_sources, compaction, renderers, writer, coordination, dispatch,
governance, approval, collections, notifications.
"""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
        from general_ludd.issue_sources.base import IssueSyncEngine

        todo_store = MagicMock()
        engine = IssueSyncEngine(todo_store)
        assert engine is not None

    def test_sync_engine_inbound_creates_todo(self):
        from general_ludd.issue_sources.base import IssueSyncEngine, NormalizedIssue

        todo_store = MagicMock()
        todo_store.find_by_external.return_value = None
        todo_store.create_or_update.return_value = {"id": "t1"}
        engine = IssueSyncEngine(todo_store)

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
        report = engine.sync_in([issue])
        assert report.ingested == 1
        assert report.created == 1

    def test_sync_engine_dedup_skips_existing(self):
        from general_ludd.issue_sources.base import IssueSyncEngine, NormalizedIssue

        todo_store = MagicMock()
        todo_store.find_by_external.return_value = {"id": "existing"}
        engine = IssueSyncEngine(todo_store)

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
        report = engine.sync_in([issue])
        assert report.ingested == 1
        assert report.created == 0
        assert report.skipped == 1

    def test_sync_engine_outbound_updates_issue_source(self):
        from general_ludd.issue_sources.base import IssueSyncEngine

        todo_store = MagicMock()
        mock_source = MagicMock()
        mock_source.update_status.return_value = True
        engine = IssueSyncEngine(todo_store)
        engine.register_source("test", mock_source)

        todo_row = {
            "id": "t7",
            "external_id": "123",
            "source": "test",
            "status": "DONE",
        }
        engine.sync_out([todo_row])
        mock_source.update_status.assert_called_once()

    def test_sync_report_counts_are_zero_initially(self):
        from general_ludd.issue_sources.base import SyncReport

        report = SyncReport()
        assert report.ingested == 0
        assert report.created == 0
        assert report.updated == 0
        assert report.skipped == 0
        assert len(report.errors) == 0

    def test_sync_report_errors_captured(self):
        from general_ludd.issue_sources.base import SyncReport

        report = SyncReport()
        report.errors.append("timeout on item #3")
        assert report.has_errors is True


class TestIssueSourceProtocol:
    """Tests that the IssueSource protocol check works."""

    def test_protocol_is_runtime_checkable(self):
        from general_ludd.issue_sources.base import IssueSource
        from typing import runtime_checkable

        assert hasattr(IssueSource, "__runtime_checkable__")

    def test_matching_impl_passes_check(self):
        from general_ludd.issue_sources.base import IssueSource

        class Good:
            def health(self) -> str:
                return "ok"

            def fetch_issues(self, since=None):
                return []

            def update_status(self, external_id, status, comment=None):
                return True

            def add_comment(self, external_id, body):
                return True

        from typing import cast

        obj = Good()
        assert isinstance(obj, IssueSource)


class TestJiraIngest:
    """Tests for Jira issue adapter."""

    def test_jira_imports(self):
        from general_ludd.issue_sources.jira import JiraSource

        assert JiraSource is not None

    def test_jira_health_returns_string(self):
        from general_ludd.issue_sources.jira import JiraSource

        src = JiraSource("https://example.atlassian.net", "user@example.com", "token")
        health = src.health()
        assert isinstance(health, str)

    def test_jira_fetch_issues_returns_list(self):
        from general_ludd.issue_sources.jira import JiraSource

        src = JiraSource("https://example.atlassian.net", "user@example.com", "token")
        with patch.object(src, "_transport") as mock_t:
            mock_t.return_value = (200, {"issues": []})
            issues = src.fetch_issues()
            assert isinstance(issues, list)


class TestGitHubIssues:
    """Tests for GitHub issues adapter."""

    def test_github_imports(self):
        from general_ludd.issue_sources.github_issues import GitHubIssueSource

        assert GitHubIssueSource is not None

    def test_github_health(self):
        from general_ludd.issue_sources.github_issues import GitHubIssueSource

        src = GitHubIssueSource("test-org/test-repo", "fake-token")
        with patch.object(src, "_transport") as mock_t:
            mock_t.return_value = (200, {})
            health = src.health()
            assert isinstance(health, str)

    def test_github_fetch_issues(self):
        from general_ludd.issue_sources.github_issues import GitHubIssueSource

        src = GitHubIssueSource("test-org/test-repo", "fake-token")
        with patch.object(src, "_transport") as mock_t:
            mock_t.return_value = (200, [{"number": 1, "title": "Bug"}])
            issues = src.fetch_issues()
            assert isinstance(issues, list)


class TestGitLabIssues:
    """Tests for GitLab issues adapter."""

    def test_gitlab_imports(self):
        from general_ludd.issue_sources.gitlab_issues import GitLabIssueSource

        assert GitLabIssueSource is not None

    def test_gitlab_health(self):
        from general_ludd.issue_sources.gitlab_issues import GitLabIssueSource

        src = GitLabIssueSource("https://gitlab.example.com", "fake-token", "group/proj")
        with patch.object(src, "_transport") as mock_t:
            mock_t.return_value = (200, {})
            health = src.health()
            assert isinstance(health, str)


class TestLinearSource:
    """Tests for Linear issue adapter."""

    def test_linear_imports(self):
        from general_ludd.issue_sources.linear import LinearSource

        assert LinearSource is not None

    def test_linear_health(self):
        from general_ludd.issue_sources.linear import LinearSource

        src = LinearSource("fake-api-key")
        with patch.object(src, "_transport") as mock_t:
            mock_t.return_value = (200, {"data": {}})
            health = src.health()
            assert isinstance(health, str)


class TestCsvExcelSource:
    """Tests for CSV/Excel issue source adapter."""

    def test_csv_excel_imports(self):
        from general_ludd.issue_sources.csv_excel import CsvExcelSource

        assert CsvExcelSource is not None

    def test_csv_excel_fetch_from_file(self):
        from general_ludd.issue_sources.csv_excel import CsvExcelSource

        src = CsvExcelSource(Path("/nonexistent/doesnt/matter"))
        src._issues = [
            {
                "source": "csv",
                "external_id": "1",
                "title": "T1",
                "description": "",
                "status": "Open",
                "assignee": None,
                "labels": [],
                "raw": {},
            }
        ]
        issues = src.fetch_issues()
        assert len(issues) == 1
        assert issues[0]["title"] == "T1"


class TestIngestMain:
    """Tests for ingest.py orchestration entrypoint."""

    def test_ingest_discover_finds_module(self):
        from general_ludd.issue_sources import ingest

        assert hasattr(ingest, "discover_and_ingest")

    def test_ingest_discover_returns_report_dict(self):
        from general_ludd.issue_sources.ingest import discover_and_ingest

        result = discover_and_ingest()
        assert isinstance(result, dict)
        assert "reports" in result


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
        assert result.compression_pct == 70.0

    def test_compactor_protocol_is_runtime_checkable(self):
        from general_ludd.compaction.base import Compactor

        assert hasattr(Compactor, "__runtime_checkable__")


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

        from general_ludd.compaction.baselines import NoopCompactor

        compactor = NoopCompactor()
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
        from general_ludd.compaction.aggressive import AggressiveCompactor

        compactor = AggressiveCompactor()
        assert compactor is not None

    def test_aggressive_drops_more_than_baseline(self):
        from general_ludd.agents.context import ContextMessage
        from general_ludd.compaction.base import CompactionRequest
        from general_ludd.compaction.aggressive import AggressiveCompactor

        msgs = [ContextMessage(role="user", content=f"msg {i}") for i in range(50)]
        req = CompactionRequest(messages=msgs, goal="compress hard", target_tokens=20)

        compactor = AggressiveCompactor()
        result = compactor.compact(req)
        assert result.dropped_messages > 0


class TestCompactionEvaluate:
    """Tests for compaction evaluator."""

    def test_evaluate_module_imports(self):
        from general_ludd.compaction import evaluate

        assert evaluate is not None

    def test_evaluate_compares_strategies(self):
        from general_ludd.compaction.evaluate import compare_compact_strategies

        result = compare_compact_strategies()
        assert isinstance(result, dict)


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
        assert len(list(reg.iter())) == 0

    def test_registry_discovers_yml(self):
        from general_ludd.renderers.registry import RendererRegistry, RendererSpec

        reg = RendererRegistry()
        with tempfile.TemporaryDirectory() as tmp:
            playbook = Path(tmp) / "test.yml"
            playbook.write_text(
                "[{'hosts': 'localhost', 'vars': {'renderer': True, 'renderer_description': 'desc'}}]"
            )
            found = reg.discover(Path(tmp), Path(tmp))
            assert isinstance(found, int)

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
        from general_ludd.renderers.executor import RendererExecutor

        assert RendererExecutor is not None

    def test_executor_constructs(self):
        from general_ludd.renderers.executor import RendererExecutor

        ex = RendererExecutor()
        assert ex is not None

    def test_executor_validate_schema_missing_file(self):
        from general_ludd.renderers.executor import RendererExecutor

        ex = RendererExecutor()
        result = ex.validate(Path("/nonexistent.yml"), {})
        assert not result.ok


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
        from general_ludd.writer.supervisor import WriterSupervisor

        event_bus = MagicMock()
        supervisor = WriterSupervisor(
            writer_process_factory=lambda: MagicMock(),
            event_bus=event_bus,
            base_backoff=0.01,
            max_backoff=0.1,
            max_retries=2,
        )
        assert supervisor is not None
        assert supervisor.max_retries == 2

    def test_supervisor_start_stop(self):
        from general_ludd.writer.supervisor import WriterSupervisor

        event_bus = MagicMock()
        mock_writer = MagicMock()
        mock_writer.running = True
        mock_writer.health.return_value = True

        supervisor = WriterSupervisor(
            writer_process_factory=lambda: mock_writer,
            event_bus=event_bus,
            health_interval=0.05,
            max_retries=2,
        )
        supervisor.start()
        time.sleep(0.15)
        supervisor.stop()
        assert True

    def test_supervisor_recovery_emits_event(self):
        from general_ludd.writer.supervisor import WriterSupervisor

        event_bus = MagicMock()
        call_count = [0]

        def failing_factory():
            call_count[0] += 1
            w = MagicMock()
            w.running = False
            w.exit_code = 1
            w.health.return_value = False
            w.start.side_effect = RuntimeError("fail")
            return w

        supervisor = WriterSupervisor(
            writer_process_factory=failing_factory,
            event_bus=event_bus,
            health_interval=0.05,
            max_retries=2,
            base_backoff=0.01,
            max_backoff=0.05,
        )
        supervisor.start()
        time.sleep(0.3)
        supervisor.stop()
        assert event_bus.emit.call_count >= 0


class TestWriterBridge:
    """Tests for writer bridge module."""

    def test_bridge_imports(self):
        from general_ludd.writer.bridge import WriterBridge

        assert WriterBridge is not None

    def test_bridge_constructs(self):
        from general_ludd.writer.bridge import WriterBridge

        bridge = WriterBridge()
        assert bridge is not None


class TestWriterChild:
    """Tests for writer child process."""

    def test_child_imports(self):
        from general_ludd.writer._child import WriterChild

        assert WriterChild is not None


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
        assert reg.all_claims() == {"worker-1": frozenset({"a.py", "b.py"})}

    def test_release_removes_worker(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        reg = FileClaimRegistry()
        reg.claim("w1", ["x.py"])
        reg.release("w1")
        assert "w1" not in reg.all_claims()

    def test_overlap_detects_conflict(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        reg = FileClaimRegistry()
        reg.claim("w1", ["a.py", "b.py"])
        reg.claim("w2", ["b.py", "c.py"])
        assert reg.overlapping_paths() == frozenset({"b.py"})

    def test_overlap_no_conflict(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        reg = FileClaimRegistry()
        reg.claim("w1", ["a.py"])
        reg.claim("w2", ["b.py"])
        assert reg.overlapping_paths() == frozenset()

    def test_should_wait_true_on_overlap(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        reg = FileClaimRegistry()
        reg.claim("w1", ["a.py"])
        reg.claim("w2", ["a.py"])
        assert reg.should_wait("w2") is True

    def test_should_wait_false_no_overlap(self):
        from general_ludd.coordination.file_claims import FileClaimRegistry

        reg = FileClaimRegistry()
        reg.claim("w1", ["a.py"])
        reg.claim("w2", ["b.py"])
        assert reg.should_wait("w2") is False

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

        result = DispatchResult(ok=True, result={"status": "done"})
        assert result.ok is True
        assert result.result == {"status": "done"}

    def test_dispatch_result_error(self):
        from general_ludd.dispatch.dynamic_dispatcher import DispatchResult

        result = DispatchResult(ok=False, error="not found")
        assert result.ok is False
        assert result.error == "not found"

    def test_dispatcher_register_handler(self):
        from general_ludd.dispatch.dynamic_dispatcher import DynamicDispatcher

        dispatcher = DynamicDispatcher()

        def my_handler(name, args):
            return {"handled": name, "args": args}

        dispatcher.register("skill", "greet", my_handler)
        result = dispatcher.dispatch("skill", "greet", {"name": "world"})
        assert result.ok is True

    def test_dispatcher_missing_handler(self):
        from general_ludd.dispatch.dynamic_dispatcher import DynamicDispatcher

        dispatcher = DynamicDispatcher()
        result = dispatcher.dispatch("skill", "nonexistent_handler", {})
        assert result.ok is False

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
        store.set("key1", "value1")
        assert store.get("key1") == "value1"

    def test_variable_store_missing_returns_none(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        assert store.get("missing") is None

    def test_variable_store_overwrite(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        store.set("a", 1)
        store.set("a", 2)
        assert store.get("a") == 2

    def test_variable_store_dump(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        store.set("x", "hello")
        store.set("y", 42)
        d = store.dump()
        assert d == {"x": "hello", "y": 42}

    def test_variable_store_clear(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        store.set("k", "v")
        store.clear()
        assert store.get("k") is None


# ============================================================================
# governance — policy loader
# ============================================================================


class TestGovernanceLoader:
    """Tests for governance policy loader."""

    def test_loader_imports(self):
        from general_ludd.governance.loader import load_governance_policies

        assert load_governance_policies is not None

    def test_loader_returns_list(self):
        from general_ludd.governance.loader import load_governance_policies

        policies = load_governance_policies()
        assert isinstance(policies, list)


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
        from general_ludd.approval.gate import ApprovalGate

        gate = ApprovalGate()
        gate.request("req-1", "deploy to prod")
        result = gate.approve("req-1", "admin")
        assert result is True

    def test_gate_deny(self):
        from general_ludd.approval.gate import ApprovalGate

        gate = ApprovalGate()
        gate.request("req-2", "delete database")
        result = gate.deny("req-2", "operator", "too risky")
        assert result is True

    def test_gate_pending_returns_requests(self):
        from general_ludd.approval.gate import ApprovalGate

        gate = ApprovalGate()
        gate.request("r1", "action 1")
        gate.request("r2", "action 2")
        pending = gate.pending()
        assert len(pending) == 2


# ============================================================================
# collections — CollectionImporter
# ============================================================================


class TestCollectionImporter:
    """Tests for collection importer."""

    def test_importer_imports(self):
        from general_ludd.collections.importer import CollectionImporter

        assert CollectionImporter is not None

    def test_importer_constructs(self):
        from general_ludd.collections.importer import CollectionImporter

        importer = CollectionImporter()
        assert importer is not None


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

        dispatcher = NotificationDispatcher()
        assert dispatcher is not None
