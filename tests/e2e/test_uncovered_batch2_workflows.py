"""E2E tests for previously uncovered modules - batch 2.

Covers: ornith, retrieval, receiver, onboard, agent lifecycle modules
(ag2_lifecycle, ag13_dspy, ag16_orchestration, ag8_named_passes, ag9_checkpoint),
model_weights, projects, routing_roles, ssl_agent, system, hardware, history,
networking, observe, quantization, runner, commands, compat, governance_cli.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ============================================================================
# ornith — OrnithClient, training_data, sandbox, outcome_observer
# ============================================================================


class TestOrnithClient:
    """Tests for ornith MCP client adapter."""

    def test_ornith_client_imports(self):
        from general_ludd.ornith.client import OrnithClient

        assert OrnithClient is not None

    def test_client_constructs(self):
        from general_ludd.ornith.client import OrnithClient

        perm_spec = MagicMock()
        perm_spec.has_capability.return_value = True
        sts_reg = MagicMock()
        sts_reg.mint.return_value = "fake-token"

        client = OrnithClient(
            mcp_socket_path=Path("/tmp/ornith.sock"),
            permission_spec=perm_spec,
            sts_registry=sts_reg,
        )
        assert client is not None

    def test_solve_requires_permission(self):
        from general_ludd.ornith.client import OrnithClient

        perm_spec = MagicMock()
        perm_spec.has_capability.return_value = False
        sts_reg = MagicMock()
        transport = MagicMock()
        transport.call.return_value = {"status": "ok"}

        client = OrnithClient(
            mcp_socket_path=Path("/tmp/ornith.sock"),
            permission_spec=perm_spec,
            sts_registry=sts_reg,
            transport=transport,
        )
        with pytest.raises(PermissionError):
            client.solve("fix bug", "/tmp/repo")

    def test_solve_caps_max_iterations(self):
        from general_ludd.ornith.client import OrnithClient

        perm_spec = MagicMock()
        perm_spec.has_capability.return_value = True
        sts_reg = MagicMock()
        sts_reg.mint.return_value = "token"

        client = OrnithClient(
            mcp_socket_path=Path("/tmp/o.sock"),
            permission_spec=perm_spec,
            sts_registry=sts_reg,
        )
        with pytest.raises(ValueError):
            client.solve("task", "/repo", max_iterations=100)


class TestOrnithTrainingData:
    """Tests for ornith training data module."""

    def test_training_data_imports(self):
        from general_ludd.ornith.training_data import OrnithTrainingData

        assert OrnithTrainingData is not None

    def test_training_data_constructs(self):
        from general_ludd.ornith.training_data import OrnithTrainingData

        td = OrnithTrainingData()
        assert td is not None


class TestOrnithSandbox:
    """Tests for ornith sandbox module."""

    def test_sandbox_imports(self):
        from general_ludd.ornith.sandbox import OrnithSandbox

        assert OrnithSandbox is not None

    def test_sandbox_constructs(self):
        from general_ludd.ornith.sandbox import OrnithSandbox

        with tempfile.TemporaryDirectory() as tmp:
            sandbox = OrnithSandbox(Path(tmp))
            assert sandbox is not None


class TestOrnithOutcomeObserver:
    """Tests for outcome observer."""

    def test_observer_imports(self):
        from general_ludd.ornith.outcome_observer import OutcomeObserver

        assert OutcomeObserver is not None

    def test_observer_constructs(self):
        from general_ludd.ornith.outcome_observer import OutcomeObserver

        observer = OutcomeObserver()
        assert observer is not None

    def test_observer_record_and_summary(self):
        from general_ludd.ornith.outcome_observer import OutcomeObserver

        observer = OutcomeObserver()
        observer.record("task-1", "success", {"score": 0.9})
        summary = observer.summary()
        assert summary["task-1"]["outcome"] == "success"


class TestOrnithTrainingRepo:
    """Tests for ornith training repo module."""

    def test_training_repo_imports(self):
        from general_ludd.ornith.training_repo import OrnithTrainingRepo

        assert OrnithTrainingRepo is not None


class TestOrnithMCP:
    """Tests for MCP server module."""

    def test_mcp_server_imports(self):
        from general_ludd.ornith.mcp_server import OrnithMCPServer

        assert OrnithMCPServer is not None


# ============================================================================
# retrieval — SemanticSearcher, Indexer, SearX
# ============================================================================


class TestRetrievalSearcher:
    """Tests for semantic searcher."""

    def test_searcher_imports(self):
        from general_ludd.retrieval.searcher import SemanticSearcher

        assert SemanticSearcher is not None

    def test_searcher_no_cache_returns_empty(self):
        from general_ludd.retrieval.searcher import SemanticSearcher

        with tempfile.TemporaryDirectory() as tmp:
            searcher = SemanticSearcher(cache_dir=Path(tmp) / "nonexistent")
            results = searcher.search("query")
            assert results == []

    def test_searcher_with_cache(self):
        from general_ludd.retrieval.searcher import SemanticSearcher

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cash_dir.mkdir()
            import diskcache

            cache = diskcache.Cache(str(cache_dir))
            from general_ludd.retrieval.indexer import _tokenize

            tokens = _tokenize("hello world")
            from collections import Counter

            vec = {k: float(v) for k, v in Counter(tokens).items()}
            cache["file1.py"] = {
                "filepath": "file1.py",
                "content": "hello world",
                "vector": vec,
            }
            cache.close()

            searcher = SemanticSearcher(cache_dir=cache_dir)
            results = searcher.search("hello")
            assert len(results) >= 1
            assert results[0]["filepath"] == "file1.py"


class TestRetrievalIndexer:
    """Tests for codebase indexer."""

    def test_indexer_imports(self):
        from general_ludd.retrieval.indexer import CodebaseIndexer

        assert CodebaseIndexer is not None

    def test_tokenize_splits_words(self):
        from general_ludd.retrieval.indexer import _tokenize

        tokens = _tokenize("hello_world test_function")
        assert "hello" in tokens
        assert "world" in tokens
        assert "test" in tokens
        assert "function" in tokens

    def test_tokenize_empty_returns_empty(self):
        from general_ludd.retrieval.indexer import _tokenize

        assert _tokenize("") == []

    def test_cosine_similarity_same(self):
        from general_ludd.retrieval.indexer import _cosine_similarity

        v = {"a": 1.0, "b": 2.0}
        score = _cosine_similarity(v, v)
        assert abs(score - 1.0) < 0.001

    def test_cosine_similarity_orthogonal(self):
        from general_ludd.retrieval.indexer import _cosine_similarity

        a = {"x": 1.0}
        b = {"y": 1.0}
        score = _cosine_similarity(a, b)
        assert score == 0.0


class TestSearXClient:
    """Tests for SearX API client."""

    def test_searx_client_imports(self):
        from general_ludd.retrieval.searx_client import SearXClient

        assert SearXClient is not None

    def test_searx_client_constructs(self):
        from general_ludd.retrieval.searx_client import SearXClient

        client = SearXClient(base_url="http://localhost:8888")
        assert client is not None


class TestResearchIndex:
    """Tests for research index."""

    def test_research_index_imports(self):
        from general_ludd.retrieval.research_index import ResearchIndex

        assert ResearchIndex is not None


class TestRetrievalWeb:
    """Tests for web retrieval module."""

    def test_web_imports(self):
        from general_ludd.retrieval.web import WebRetriever

        assert WebRetriever is not None


class TestAgenticContext:
    """Tests for agentic context retrieval."""

    def test_agentic_context_imports(self):
        from general_ludd.retrieval.agentic_context import AgenticContextRetriever

        assert AgenticContextRetriever is not None


# ============================================================================
# receiver — ReceiverRouter, Buffer, Parsers
# ============================================================================


class TestReceiverRouter:
    """Tests for receiver message router."""

    def test_router_imports(self):
        from general_ludd.receiver.router import ReceiverRouter

        assert ReceiverRouter is not None

    def test_router_constructs(self):
        from general_ludd.receiver.router import ReceiverRouter

        router = ReceiverRouter()
        assert router is not None

    def test_router_route_dispatches(self):
        from general_ludd.receiver.router import ReceiverRouter

        router = ReceiverRouter()
        handler_called = []
        router.register("ping", lambda payload: handler_called.append(payload))
        router.route({"type": "ping", "data": "hello"})
        assert handler_called == ["hello"]

    def test_router_unknown_type_noop(self):
        from general_ludd.receiver.router import ReceiverRouter

        router = ReceiverRouter()
        router.route({"type": "unknown_event"})


class TestReceiverBuffer:
    """Tests for message buffer."""

    def test_buffer_imports(self):
        from general_ludd.receiver.buffer import MessageBuffer

        assert MessageBuffer is not None

    def test_buffer_constructs(self):
        from general_ludd.receiver.buffer import MessageBuffer

        buf = MessageBuffer(max_size=10)
        assert buf is not None

    def test_buffer_append_drain(self):
        from general_ludd.receiver.buffer import MessageBuffer

        buf = MessageBuffer(max_size=5)
        for i in range(3):
            buf.append({"id": i})
        items = buf.drain()
        assert len(items) == 3
        assert buf.drain() == []

    def test_buffer_overflow_drops_oldest(self):
        from general_ludd.receiver.buffer import MessageBuffer

        buf = MessageBuffer(max_size=3)
        for i in range(5):
            buf.append({"id": i})
        items = buf.drain()
        assert len(items) == 3
        assert items[0]["id"] == 2


class TestReceiverParsers:
    """Tests for message parsers."""

    def test_parsers_imports(self):
        from general_ludd.receiver.parsers import parse_message

        assert parse_message is not None

    def test_parse_valid_json(self):
        from general_ludd.receiver.parsers import parse_message

        result = parse_message(b'{"type": "event", "data": 1}')
        assert result["type"] == "event"

    def test_parse_invalid_json(self):
        from general_ludd.receiver.parsers import parse_message

        result = parse_message(b"not json")
        assert result is None


# ============================================================================
# onboard — AWS, GCP, Azure cloud account on-boarding
# ============================================================================


class TestOnboardAWS:
    """Tests for AWS cloud on-boarding."""

    def test_aws_onboard_imports(self):
        from general_ludd.onboard.aws import AWSOnboarder

        assert AWSOnboarder is not None

    def test_aws_onboarder_constructs(self):
        from general_ludd.onboard.aws import AWSOnboarder

        onboarder = AWSOnboarder(region="us-east-1")
        assert onboarder is not None


class TestOnboardGCP:
    """Tests for GCP cloud on-boarding."""

    def test_gcp_onboard_imports(self):
        from general_ludd.onboard.gcp import GCPOnboarder

        assert GCPOnboarder is not None

    def test_gcp_onboarder_constructs(self):
        from general_ludd.onboard.gcp import GCPOnboarder

        onboarder = GCPOnboarder(project_id="test-project")
        assert onboarder is not None


class TestOnboardAzure:
    """Tests for Azure cloud on-boarding."""

    def test_azure_onboard_imports(self):
        from general_ludd.onboard.azure import AzureOnboarder

        assert AzureOnboarder is not None

    def test_azure_onboarder_constructs(self):
        from general_ludd.onboard.azure import AzureOnboarder

        onboarder = AzureOnboarder(subscription_id="sub-123", tenant_id="tenant-456")
        assert onboarder is not None


# ============================================================================
# Agent lifecycle modules — ag2_lifecycle, ag13_dspy, ag16_orchestration, ag8_named_passes, ag9_checkpoint
# ============================================================================


class TestAg2Lifecycle:
    """Tests for agent lifecycle types and hooks."""

    def test_types_imports(self):
        from general_ludd.ag2_lifecycle.types import AgentLifecyclePhase

        assert AgentLifecyclePhase is not None

    def test_hooks_imports(self):
        from general_ludd.ag2_lifecycle.hooks import LifecycleHooks

        assert LifecycleHooks is not None

    def test_lifecycle_hooks_constructs(self):
        from general_ludd.ag2_lifecycle.hooks import LifecycleHooks

        hooks = LifecycleHooks()
        assert hooks is not None


class TestAg13Dspy:
    """Tests for dspy optimizer and registry."""

    def test_optimizer_imports(self):
        from general_ludd.ag13_dspy.optimizer import DspyOptimizer

        assert DspyOptimizer is not None

    def test_registry_imports(self):
        from general_ludd.ag13_dspy.registry import DspyRegistry

        assert DspyRegistry is not None

    def test_registry_constructs(self):
        from general_ludd.ag13_dspy.registry import DspyRegistry

        reg = DspyRegistry()
        assert reg is not None


class TestAg16Orchestration:
    """Tests for orchestration and conversation modules."""

    def test_orchestrator_imports(self):
        from general_ludd.ag16_orchestration.orchestrator import Orchestrator

        assert Orchestrator is not None

    def test_conversation_imports(self):
        from general_ludd.ag16_orchestration.conversation import ConversationManager

        assert ConversationManager is not None

    def test_orchestrator_constructs(self):
        from general_ludd.ag16_orchestration.orchestrator import Orchestrator

        orchestrator = Orchestrator()
        assert orchestrator is not None

    def test_conversation_manager_constructs(self):
        from general_ludd.ag16_orchestration.conversation import ConversationManager

        mgr = ConversationManager()
        assert mgr is not None


class TestAg8NamedPasses:
    """Tests for named passes registry."""

    def test_registry_imports(self):
        from general_ludd.ag8_named_passes.registry import PassRegistry

        assert PassRegistry is not None

    def test_registry_constructs(self):
        from general_ludd.ag8_named_passes.registry import PassRegistry

        reg = PassRegistry()
        assert reg is not None


class TestAg9Checkpoint:
    """Tests for checkpoint branching."""

    def test_branching_imports(self):
        from general_ludd.ag9_checkpoint.branching import CheckpointManager

        assert CheckpointManager is not None

    def test_checkpoint_manager_constructs(self):
        from general_ludd.ag9_checkpoint.branching import CheckpointManager

        cm = CheckpointManager()
        assert cm is not None


# ============================================================================
# model_weights — schema, loader, store
# ============================================================================


class TestModelWeightsSchema:
    """Tests for model weights schema."""

    def test_schema_imports(self):
        from general_ludd.model_weights.schema import ModelWeights

        assert ModelWeights is not None

    def test_model_weights_constructs(self):
        from general_ludd.model_weights.schema import ModelWeights

        weights = ModelWeights(model_name="test-model", weights={"a": 0.5, "b": 1.0})
        assert weights.model_name == "test-model"
        assert weights.weights == {"a": 0.5, "b": 1.0}


class TestModelWeightsLoader:
    """Tests for model weights loader."""

    def test_loader_imports(self):
        from general_ludd.model_weights.loader import load_weights

        assert load_weights is not None

    def test_load_weights_nonexistent(self):
        from general_ludd.model_weights.loader import load_weights

        result = load_weights(Path("/nonexistent/weights.json"))
        assert result is None


class TestModelWeightsStore:
    """Tests for model weights store."""

    def test_store_imports(self):
        from general_ludd.model_weights.store import ModelWeightsStore

        assert ModelWeightsStore is not None

    def test_store_constructs(self):
        from general_ludd.model_weights.store import ModelWeightsStore

        store = ModelWeightsStore()
        assert store is not None


# ============================================================================
# projects — ProjectManager, Workspace
# ============================================================================


class TestProjectsManager:
    """Tests for project manager."""

    def test_manager_imports(self):
        from general_ludd.projects.manager import ProjectManager

        assert ProjectManager is not None

    def test_manager_constructs(self):
        from general_ludd.projects.manager import ProjectManager

        mgr = ProjectManager()
        assert mgr is not None


class TestWorkspace:
    """Tests for project workspace."""

    def test_workspace_imports(self):
        from general_ludd.projects.workspace import ProjectWorkspace

        assert ProjectWorkspace is not None

    def test_workspace_constructs(self):
        from general_ludd.projects.workspace import ProjectWorkspace

        ws = ProjectWorkspace(name="test-ws", root=Path("/tmp/ws"))
        assert ws.name == "test-ws"


# ============================================================================
# routing_roles — RoleWeights, roles
# ============================================================================


class TestRoutingRoles:
    """Tests for routing role weights and roles."""

    def test_weights_imports(self):
        from general_ludd.routing_roles.weights import compute_role_weight

        assert compute_role_weight is not None

    def test_compute_role_weight_returns_number(self):
        from general_ludd.routing_roles.weights import compute_role_weight

        weight = compute_role_weight("builder", {"current_load": 0.5})
        assert isinstance(weight, (int, float))

    def test_roles_imports(self):
        from general_ludd.routing_roles.roles import get_available_roles

        assert get_available_roles is not None

    def test_get_available_roles_returns_list(self):
        from general_ludd.routing_roles.roles import get_available_roles

        roles = get_available_roles()
        assert isinstance(roles, list)


# ============================================================================
# ssl_agent — cert_manager, agent_flow
# ============================================================================


class TestSslCertManager:
    """Tests for SSL certificate manager."""

    def test_cert_manager_imports(self):
        from general_ludd.ssl_agent.cert_manager import CertManager

        assert CertManager is not None

    def test_cert_manager_constructs(self):
        from general_ludd.ssl_agent.cert_manager import CertManager

        with tempfile.TemporaryDirectory() as tmp:
            mgr = CertManager(cert_dir=Path(tmp))
            assert mgr is not None


class TestSslAgentFlow:
    """Tests for SSL agent flow."""

    def test_agent_flow_imports(self):
        from general_ludd.ssl_agent.agent_flow import SslAgentFlow

        assert SslAgentFlow is not None

    def test_agent_flow_constructs(self):
        from general_ludd.ssl_agent.agent_flow import SslAgentFlow

        flow = SslAgentFlow()
        assert flow is not None


# ============================================================================
# system — rlimit, monitor
# ============================================================================


class TestSystemRlimit:
    """Tests for system resource limits."""

    def test_rlimit_imports(self):
        from general_ludd.system.rlimit import set_resource_limits

        assert set_resource_limits is not None


class TestSystemMonitor:
    """Tests for system monitor."""

    def test_monitor_imports(self):
        from general_ludd.system.monitor import SystemMonitor

        assert SystemMonitor is not None

    def test_monitor_constructs(self):
        from general_ludd.system.monitor import SystemMonitor

        monitor = SystemMonitor()
        assert monitor is not None

    def test_monitor_snapshot_returns_dict(self):
        from general_ludd.system.monitor import SystemMonitor

        monitor = SystemMonitor()
        snapshot = monitor.snapshot()
        assert isinstance(snapshot, dict)
        assert "cpu_percent" in snapshot or "memory" in snapshot


# ============================================================================
# hardware — HardwareProbe
# ============================================================================


class TestHardwareProbe:
    """Tests for hardware probe."""

    def test_probe_imports(self):
        from general_ludd.hardware.probe import HardwareProbe

        assert HardwareProbe is not None

    def test_probe_constructs(self):
        from general_ludd.hardware.probe import HardwareProbe

        probe = HardwareProbe()
        assert probe is not None

    def test_probe_gpu_info(self):
        from general_ludd.hardware.probe import HardwareProbe

        probe = HardwareProbe()
        gpu_info = probe.gpu_info()
        assert isinstance(gpu_info, dict)

    def test_probe_cpu_info(self):
        from general_ludd.hardware.probe import HardwareProbe

        probe = HardwareProbe()
        cpu_info = probe.cpu_info()
        assert isinstance(cpu_info, dict)


# ============================================================================
# history — GitIndexer
# ============================================================================


class TestHistoryGitIndexer:
    """Tests for git history indexer."""

    def test_git_indexer_imports(self):
        from general_ludd.history.git_indexer import GitHistoryIndexer

        assert GitHistoryIndexer is not None

    def test_git_indexer_constructs(self):
        from general_ludd.history.git_indexer import GitHistoryIndexer

        indexer = GitHistoryIndexer(db_path=":memory:")
        assert indexer is not None


# ============================================================================
# networking — ScapyAdapter
# ============================================================================


class TestNetworkingScapy:
    """Tests for scapy networking adapter."""

    def test_scapy_adapter_imports(self):
        from general_ludd.networking.scapy_adapter import ScapyAdapter

        assert ScapyAdapter is not None

    def test_scapy_adapter_constructs(self):
        from general_ludd.networking.scapy_adapter import ScapyAdapter

        adapter = ScapyAdapter()
        assert adapter is not None


# ============================================================================
# observe — ObserveFacade
# ============================================================================


class TestObserveFacade:
    """Tests for observe facade."""

    def test_facade_imports(self):
        from general_ludd.observe.facade import ObserveFacade

        assert ObserveFacade is not None

    def test_facade_constructs(self):
        from general_ludd.observe.facade import ObserveFacade

        facade = ObserveFacade()
        assert facade is not None


# ============================================================================
# quantization — QuantizationMonitor
# ============================================================================


class TestQuantizationMonitor:
    """Tests for quantization monitor."""

    def test_monitor_imports(self):
        from general_ludd.quantization.monitor import QuantizationMonitor

        assert QuantizationMonitor is not None

    def test_monitor_constructs(self):
        from general_ludd.quantization.monitor import QuantizationMonitor

        monitor = QuantizationMonitor()
        assert monitor is not None


# ============================================================================
# runner — BackgroundTestRunner
# ============================================================================


class TestBackgroundTestRunner:
    """Tests for background test runner."""

    def test_runner_imports(self):
        from general_ludd.runner.background_test_runner import BackgroundTestRunner

        assert BackgroundTestRunner is not None

    def test_runner_constructs(self):
        from general_ludd.runner.background_test_runner import BackgroundTestRunner

        runner = BackgroundTestRunner()
        assert runner is not None


# ============================================================================
# commands — MakeCommand
# ============================================================================


class TestCommandsMake:
    """Tests for make command wrapper."""

    def test_make_imports(self):
        from general_ludd.commands.make import MakeCommand

        assert MakeCommand is not None

    def test_make_command_constructs(self):
        from general_ludd.commands.make import MakeCommand

        cmd = MakeCommand("test-target")
        assert cmd is not None
        assert cmd.target == "test-target"


# ============================================================================
# compat — AnnotatedTypes
# ============================================================================


class TestCompatAnnotatedTypes:
    """Tests for annotated types compatibility."""

    def test_annotated_types_imports(self):
        from general_ludd.compat.annotated_types import is_annotated

        assert is_annotated is not None

    def test_is_annotated_returns_bool(self):
        from general_ludd.compat.annotated_types import is_annotated

        assert isinstance(is_annotated(int), bool)


# ============================================================================
# governance CLI
# ============================================================================


class TestGovernanceCLI:
    """Tests for governance CLI module."""

    def test_cli_governance_imports(self):
        from general_ludd.governance.cli_governance import build_parser

        assert build_parser is not None

    def test_build_parser_returns_parser(self):
        from general_ludd.governance.cli_governance import build_parser

        parser = build_parser()
        assert parser is not None
