"""E2E tests for previously uncovered modules - batch 2.

Covers: ornith, retrieval, receiver, onboard, agent lifecycle modules
(ag2_lifecycle, ag13_dspy, ag16_orchestration, ag8_named_passes, ag9_checkpoint),
model_weights, projects, routing_roles, ssl_agent, system, hardware, history,
networking, observe, quantization, runner, commands, compat, governance_cli.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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
        from general_ludd.ornith.training_data import (
            TrainingDataCollector,
            TrainingExample,
        )

        assert TrainingDataCollector is not None
        assert TrainingExample is not None

    def test_training_data_constructs(self):
        from general_ludd.ornith.training_data import TrainingExample

        example = TrainingExample(
            instruction="build the game",
            response="generated project",
            outcome="succeeded",
            reward=1.0,
        )
        assert example.to_dict() == {
            "instruction": "build the game",
            "response": "generated project",
            "outcome": "succeeded",
            "reward": 1.0,
            "metadata": {},
        }


class TestOrnithSandbox:
    """Tests for ornith sandbox module."""

    def test_sandbox_imports(self):
        from general_ludd.ornith.sandbox import OrnithSandbox

        assert OrnithSandbox is not None

    def test_sandbox_constructs(self):
        from general_ludd.ornith.sandbox import OrnithSandbox

        with OrnithSandbox() as sandbox:
            sandbox_root = sandbox.temp_dir
            assert sandbox_root.is_dir()
        assert not sandbox_root.exists()


class TestOrnithOutcomeObserver:
    """Tests for outcome observer."""

    def test_observer_imports(self):
        from general_ludd.ornith.outcome_observer import OutcomeObserver

        assert OutcomeObserver is not None

    def test_observer_constructs(self):
        from general_ludd.ornith.outcome_observer import OutcomeObserver

        observer = OutcomeObserver(MagicMock(), poll_interval_seconds=1)
        assert observer._poll_interval == 10

    async def test_observer_record_and_summary(self):
        from general_ludd.ornith.outcome_observer import OutcomeObserver

        observer = OutcomeObserver(MagicMock())
        observer._apply_outcome = AsyncMock()
        listener = AsyncMock()
        observer.subscribe_gate(listener)

        await observer.on_gate_complete("task-1", gate_passed=True)

        observer._apply_outcome.assert_awaited_once()
        assert observer._apply_outcome.await_args.args[:2] == ("task-1", "succeeded")
        listener.assert_awaited_once_with("task-1", True)


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
            cache_dir.mkdir()
            # Seed through the SAME safe adapter the searcher reads: the
            # searcher opens the owner-only msgpack-v1 namespace, while a
            # plain diskcache.Cache writes a separate pickle namespace the
            # searcher deliberately refuses to deserialize.
            from general_ludd.security.safe_diskcache import open_safe_diskcache

            cache = open_safe_diskcache(cache_dir)
            from general_ludd.retrieval.indexer import _tokenize

            tokens = _tokenize("hello world")
            from collections import Counter

            vec = {k: float(v) for k, v in Counter(tokens).items()}
            cache.set(
                "file1.py",
                {
                    "filepath": "file1.py",
                    "content": "hello world",
                    "vector": vec,
                },
            )
            cache.close()

            with SemanticSearcher(cache_dir=cache_dir) as searcher:
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
        assert tokens == ["hello_world", "test_function"]

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
        from general_ludd.connectors.searx import SearXConnector

        assert SearXConnector is not None

    def test_searx_client_constructs(self):
        from general_ludd.connectors.searx import SearXConnector

        client = SearXConnector({"base_url": "http://localhost:8888", "allow_private": True})
        assert client.base_url == "http://localhost:8888"
        assert client.allow_private is True


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
        from general_ludd.retrieval.agentic_context import (
            AgenticContextInjector,
            AgenticResearchContext,
        )

        assert AgenticContextInjector is not None
        assert AgenticResearchContext is not None


# ============================================================================
# receiver — ReceiverRouter, Buffer, Parsers
# ============================================================================


class TestReceiverRouter:
    """Tests for receiver message router."""

    def test_router_imports(self):
        from general_ludd.receiver.router import register

        assert register is not None

    def test_router_constructs(self):
        from fastapi import FastAPI

        from general_ludd.receiver.buffer import ReceiverBuffer
        from general_ludd.receiver.router import register

        app = FastAPI()
        state = {}
        register(app, state)

        assert isinstance(state["receiver_buffer"], ReceiverBuffer)
        assert "/ingest/webhook" in {route.path for route in app.routes}

    def test_router_route_dispatches(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from general_ludd.receiver.router import register

        monkeypatch.setenv("GLUDD_INGEST_TOKEN", "receiver-test-token")
        app = FastAPI()
        state = {}
        register(app, state)

        response = TestClient(app).post(
            "/ingest/webhook",
            headers={"Authorization": "Bearer receiver-test-token"},
            json={"type": "ping", "message": "hello"},
        )

        assert response.status_code == 202
        records = state["receiver_buffer"].drain()
        assert [record["message"] for record in records] == ["hello"]

    def test_router_unknown_type_noop(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from general_ludd.receiver.router import register

        monkeypatch.delenv("GLUDD_INGEST_TOKEN", raising=False)
        app = FastAPI()
        register(app, {})

        response = TestClient(app).post("/ingest/webhook", json={"type": "unknown"})
        assert response.status_code == 503
        assert response.json()["error"] == "ingest_disabled"


class TestReceiverBuffer:
    """Tests for message buffer."""

    def test_buffer_imports(self):
        from general_ludd.receiver.buffer import ReceiverBuffer

        assert ReceiverBuffer is not None

    def test_buffer_constructs(self):
        from general_ludd.receiver.buffer import ReceiverBuffer

        buf = ReceiverBuffer(maxlen=10)
        assert buf.maxlen == 10

    def test_buffer_append_drain(self):
        from general_ludd.receiver.buffer import ReceiverBuffer

        buf = ReceiverBuffer(maxlen=5)
        for i in range(3):
            assert buf.offer({"id": i})
        items = buf.drain()
        assert len(items) == 3
        assert buf.drain() == []

    def test_buffer_overflow_drops_oldest(self):
        from general_ludd.receiver.buffer import ReceiverBuffer

        buf = ReceiverBuffer(maxlen=3)
        for i in range(5):
            assert buf.offer({"id": i})
        items = buf.drain()
        assert len(items) == 3
        assert items[0]["id"] == 2
        assert buf.total_dropped == 2


class TestReceiverParsers:
    """Tests for message parsers."""

    def test_parsers_imports(self):
        from general_ludd.receiver.parsers import parse_otlp_logs, parse_syslog

        assert parse_otlp_logs is not None
        assert parse_syslog is not None

    def test_parse_valid_json(self):
        from general_ludd.receiver.parsers import parse_otlp_logs

        payload = (
            b'{"resourceLogs":[{"scopeLogs":[{"logRecords":['
            b'{"body":{"stringValue":"game ready"},"severityText":"INFO"}'
            b"]}]}]}"
        )
        result = parse_otlp_logs(payload)
        assert len(result) == 1
        assert result[0]["message"] == "game ready"

    def test_parse_invalid_json(self):
        from general_ludd.receiver.parsers import parse_otlp_logs

        assert parse_otlp_logs(b"not json") == []


# ============================================================================
# onboard — AWS, GCP, Azure cloud account on-boarding
# ============================================================================


class TestOnboardAWS:
    """Tests for AWS cloud on-boarding."""

    def test_aws_onboard_imports(self):
        from general_ludd.onboard.aws import AWSOnboardProvider

        assert AWSOnboardProvider is not None

    def test_aws_onboarder_constructs(self):
        from general_ludd.onboard.aws import AWSOnboardProvider

        onboarder = AWSOnboardProvider()
        instructions = onboarder.create_role_instructions()
        assert "gludd-compute-operator" in instructions
        assert "least-privilege" in instructions


class TestOnboardGCP:
    """Tests for GCP cloud on-boarding."""

    def test_gcp_onboard_imports(self):
        from general_ludd.onboard.gcp import GCPOnboardProvider

        assert GCPOnboardProvider is not None

    def test_gcp_onboarder_constructs(self):
        from general_ludd.onboard.gcp import GCPOnboardProvider

        onboarder = GCPOnboardProvider(project_id="test-project")
        instructions = onboarder.create_role_instructions()
        assert "test-project" in instructions
        assert "No `roles/owner`" in instructions


class TestOnboardAzure:
    """Tests for Azure cloud on-boarding."""

    def test_azure_onboard_imports(self):
        from general_ludd.onboard.azure import AzureOnboardProvider

        assert AzureOnboardProvider is not None

    def test_azure_onboarder_constructs(self):
        from general_ludd.onboard.azure import AzureOnboardProvider

        onboarder = AzureOnboardProvider(subscription_id="sub-123")
        instructions = onboarder.create_role_instructions()
        assert "sub-123" in instructions
        assert "No `Owner` or `Contributor`" in instructions


# ============================================================================
# Agent lifecycle modules — ag2_lifecycle, ag13_dspy, ag16_orchestration, ag8_named_passes, ag9_checkpoint
# ============================================================================


class TestAg2Lifecycle:
    """Tests for agent lifecycle types and hooks."""

    def test_types_imports(self):
        from general_ludd.ag2_lifecycle.types import Message, ToolCall

        assert Message is not None
        assert ToolCall is not None

    def test_hooks_imports(self):
        from general_ludd.ag2_lifecycle.hooks import LifecycleHookSystem

        assert LifecycleHookSystem is not None

    def test_lifecycle_hooks_constructs(self):
        from general_ludd.ag2_lifecycle.hooks import LifecycleHookSystem

        hooks = LifecycleHookSystem()
        assert "model.call.before" in hooks._HOOK_NAMES


class TestAg13Dspy:
    """Tests for dspy optimizer and registry."""

    def test_optimizer_imports(self):
        from general_ludd.ag13_dspy.optimizer import PromptOptimizer

        assert PromptOptimizer is not None

    def test_registry_imports(self):
        from general_ludd.ag13_dspy.registry import PromptRegistry

        assert PromptRegistry is not None

    def test_registry_constructs(self):
        from general_ludd.ag13_dspy.registry import PromptRegistry

        reg = PromptRegistry()
        assert len(reg) == 0


class TestAg16Orchestration:
    """Tests for orchestration and conversation modules."""

    def test_orchestrator_imports(self):
        from general_ludd.ag16_orchestration.orchestrator import ChatOrchestrator

        assert ChatOrchestrator is not None

    def test_conversation_imports(self):
        from general_ludd.ag16_orchestration.conversation import Conversation, Turn

        assert Conversation is not None
        assert Turn is not None

    def test_orchestrator_constructs(self):
        from general_ludd.ag16_orchestration.conversation import (
            Conversation,
            MaxTurnsTermination,
            RoundRobinSelector,
        )
        from general_ludd.ag16_orchestration.orchestrator import ChatOrchestrator

        conversation = Conversation(participants=["builder"])
        orchestrator = ChatOrchestrator(
            conversation,
            RoundRobinSelector(),
            MaxTurnsTermination(1),
        )
        assert orchestrator.conversation is conversation

    def test_conversation_manager_constructs(self):
        from general_ludd.ag16_orchestration.conversation import (
            Conversation,
            Turn,
            TurnKind,
        )

        conversation = Conversation(participants=["builder", "reviewer"])
        conversation.add_turn(Turn(speaker="builder", kind=TurnKind.REPORT))
        assert conversation.turn_count == 1
        assert conversation.last_turn().speaker == "builder"


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
        from general_ludd.ag9_checkpoint.branching import BranchManager

        assert BranchManager is not None

    def test_checkpoint_manager_constructs(self):
        from general_ludd.ag9_checkpoint.branching import BranchManager

        with tempfile.TemporaryDirectory() as tmp:
            manager = BranchManager(tmp)
            branch = manager.create_branch("candidate", "checkpoint-1", {"score": 1})
            assert manager.restore_branch(branch.branch_id) == branch


# ============================================================================
# model_weights — schema, loader, store
# ============================================================================


class TestModelWeightsSchema:
    """Tests for model weights schema."""

    def test_schema_imports(self):
        from general_ludd.model_weights.schema import ModelWeightSchema

        assert ModelWeightSchema is not None

    def test_model_weights_constructs(self):
        from general_ludd.model_weights.schema import ModelWeightSchema
        from general_ludd.schemas.benchmark import TaskRole

        weight = ModelWeightSchema(
            model_id="test-model",
            task_role=TaskRole.CODER,
            weight=0.75,
        )
        assert weight.model_id == "test-model"
        assert weight.weight == 0.75


class TestModelWeightsLoader:
    """Tests for model weights loader."""

    def test_loader_imports(self):
        from general_ludd.model_weights.loader import apply_routing_weights, load_seed_data

        assert apply_routing_weights is not None
        assert load_seed_data is not None

    def test_load_weights_nonexistent(self):
        from general_ludd.model_weights.loader import apply_routing_weights
        from general_ludd.model_weights.store import ModelWeightStore

        store = ModelWeightStore.load(Path("/nonexistent/weights.json"))
        assert apply_routing_weights(store) is store
        assert store.all_weights() == []


class TestModelWeightsStore:
    """Tests for model weights store."""

    def test_store_imports(self):
        from general_ludd.model_weights.store import ModelWeightStore

        assert ModelWeightStore is not None

    def test_store_constructs(self):
        from general_ludd.model_weights.store import ModelWeightStore
        from general_ludd.schemas.benchmark import TaskRole

        store = ModelWeightStore()
        stored = store.set("test-model", TaskRole.CODER, 0.75)
        assert store.get("test-model", TaskRole.CODER) == stored


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

        with tempfile.TemporaryDirectory() as tmp:
            ws = ProjectWorkspace(project_id="test-ws", base_dir=tmp)
            assert ws.project_id == "test-ws"
            assert ws.root == Path(tmp) / "test-ws"


# ============================================================================
# routing_roles — RoleWeights, roles
# ============================================================================


class TestRoutingRoles:
    """Tests for routing role weights and roles."""

    def test_weights_imports(self):
        from general_ludd.routing_roles.weights import RoleWeights, weights_for

        assert RoleWeights is not None
        assert weights_for is not None

    def test_compute_role_weight_returns_number(self):
        from general_ludd.routing_roles.weights import weights_for
        from general_ludd.schemas.benchmark import TaskType

        weights = weights_for(TaskType.FEATURE)
        assert weights.cost + weights.quality == pytest.approx(1.0)

    def test_roles_imports(self):
        from general_ludd.routing_roles.roles import TaskRole

        assert TaskRole is not None

    def test_get_available_roles_returns_list(self):
        from general_ludd.routing_roles.roles import TaskRole

        roles = list(TaskRole)
        assert isinstance(roles, list)
        assert TaskRole.CODER in roles


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

        manager = CertManager()
        assert manager._known_oids["2.5.4.3"].name == "commonName"


class TestSslAgentFlow:
    """Tests for SSL agent flow."""

    def test_agent_flow_imports(self):
        from general_ludd.ssl_agent.agent_flow import SSLCertAgent, ssl_agent_flow

        assert SSLCertAgent is not None
        assert ssl_agent_flow is not None

    def test_agent_flow_constructs(self):
        from general_ludd.ssl_agent.agent_flow import SSLCertAgent

        agent = SSLCertAgent()
        response = agent.model_call("inspect", {"common_name": "game.example"})
        assert response["call_number"] == 1
        assert "game.example" in response["response"]


# ============================================================================
# system — rlimit, monitor
# ============================================================================


class TestSystemRlimit:
    """Tests for system resource limits."""

    def test_rlimit_imports(self):
        from general_ludd.system.rlimit import apply_limits

        assert apply_limits is not None


class TestSystemMonitor:
    """Tests for system monitor."""

    def test_monitor_imports(self):
        from general_ludd.controllers.load_scrape import LoadSnapshot, scrape_system_load

        assert LoadSnapshot is not None
        assert scrape_system_load is not None

    def test_monitor_constructs(self):
        from general_ludd.controllers.load_scrape import LoadSnapshot

        snapshot = LoadSnapshot(
            loadavg_1m=0.1,
            loadavg_5m=0.1,
            loadavg_10m=0.1,
            logical_cpu_count=4,
            cpu_percent=5.0,
            memory_available_percent=90.0,
            disk_free_percent=80.0,
            active_jobs=0,
        )
        assert snapshot.logical_cpu_count == 4

    def test_monitor_snapshot_returns_dict(self):
        from dataclasses import asdict

        from general_ludd.controllers.load_scrape import scrape_system_load

        snapshot = asdict(scrape_system_load())
        assert isinstance(snapshot, dict)
        assert "cpu_percent" in snapshot
        assert "memory_available_percent" in snapshot


# ============================================================================
# hardware — HardwareProbe
# ============================================================================


class TestHardwareProbe:
    """Tests for hardware probe."""

    def test_probe_imports(self):
        from general_ludd.hardware.probe import HardwareProfile, probe_hardware

        assert HardwareProfile is not None
        assert probe_hardware is not None

    def test_probe_constructs(self):
        from general_ludd.hardware.probe import HardwareProfile, probe_hardware

        profile = probe_hardware()
        assert isinstance(profile, HardwareProfile)

    def test_probe_gpu_info(self):
        from general_ludd.hardware.probe import probe_hardware

        profile = probe_hardware()
        hardware_info = profile.to_dict()
        assert isinstance(hardware_info["local_model_allowed"], bool)
        assert hardware_info["total_memory_gb"] >= 0

    def test_probe_cpu_info(self):
        from general_ludd.hardware.probe import probe_hardware

        profile = probe_hardware()
        assert profile.cpu_count >= 1
        assert 1 <= profile.gunicorn_workers <= profile.cpu_count


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
        from general_ludd.networking.scapy_adapter import PacketSummary, craft_packet

        assert PacketSummary is not None
        assert craft_packet is not None

    def test_scapy_adapter_constructs(self):
        from general_ludd.networking.scapy_adapter import craft_packet

        packet = craft_packet(["IP", "TCP"], {"dst": "192.0.2.1", "dport": "443"})
        assert packet["protocols"] == ["IP", "TCP"]
        assert packet["fields"]["dport"] == "443"


# ============================================================================
# observe — ObserveFacade
# ============================================================================


class TestObserveFacade:
    """Tests for observe facade."""

    def test_facade_imports(self):
        from general_ludd.observe.facade import GluddObserve

        assert GluddObserve is not None

    def test_facade_constructs(self):
        from general_ludd.observe.facade import GluddObserve

        facade = GluddObserve({})
        assert facade.query_sources(["logs"], {}) == []
        assert facade.errors == []


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
        from general_ludd.commands.make import MakeResult, MakeRunner

        assert MakeResult is not None
        assert MakeRunner is not None

    def test_make_command_constructs(self):
        from general_ludd.commands.make import MakeRunner

        runner = MakeRunner()
        assert runner._sanitize_args(["test-target"]) == ["test-target"]


# ============================================================================
# compat — AnnotatedTypes
# ============================================================================


class TestCompatAnnotatedTypes:
    """Tests for annotated types compatibility."""

    def test_annotated_types_imports(self):
        from general_ludd.compat.annotated_types import apply_annotated_types_runtime_patch

        assert apply_annotated_types_runtime_patch is not None

    def test_is_annotated_returns_bool(self):
        import annotated_types as at

        from general_ludd.compat.annotated_types import apply_annotated_types_runtime_patch

        apply_annotated_types_runtime_patch()
        assert isinstance(isinstance(at.MinLen(1), at.GroupedMetadata), bool)
        assert not isinstance(at.MinLen(1), at.GroupedMetadata)


# ============================================================================
# governance CLI
# ============================================================================


class TestGovernanceCLI:
    """Tests for governance CLI module."""

    def test_cli_governance_imports(self):
        from general_ludd.governance.cli_governance import add_governance_subparser

        assert add_governance_subparser is not None

    def test_build_parser_returns_parser(self):
        import argparse

        from general_ludd.governance.cli_governance import add_governance_subparser

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_governance_subparser(subparsers)
        args = parser.parse_args(["governance", "currency", "USD"])
        assert args.command == "governance"
        assert args.code == "USD"
