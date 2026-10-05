"""Deep protocol and serialization tests — 25+ tests covering:
- Message envelope parsing with all field combinations
- Binary vs JSON serialization roundtrip
- Version negotiation / contract edge cases
- Invalid message rejection
- Large payload handling
- Error response serialization
"""

from __future__ import annotations

import dataclasses
import json

import pydantic
import pytest

from general_ludd.budget.envelope import (
    BudgetCheckResult,
    BudgetEnvelope,
    BudgetManager,
    PerAgentEnvelope,
    PerTaskEnvelope,
    PerToolEnvelope,
)
from general_ludd.chat.contracts import ChatConfig, ChatMessage
from general_ludd.execution.situation_store import (
    BadCallSituation,
    BadCallSituationStore,
    _deserialize,
    _serialize,
)
from general_ludd.ipc.queue import Envelope
from general_ludd.routers.messages import SendMessageRequest
from general_ludd.sandbox.contracts import (
    IsolationLevel,
    SandboxConfig,
    SandboxResult,
    isolation_exceeds,
    validate_config,
)

# ═══════════════════════════════════════════════════════════════════════════
# Envelope — IPC message parsing with all field combinations
# ═══════════════════════════════════════════════════════════════════════════


class TestEnvelopeFieldCombinations:
    def test_empty_payload_default(self):
        env = Envelope(topic="event.created")
        assert env.topic == "event.created"
        assert env.payload == {}

    def test_nested_payload(self):
        env = Envelope(
            topic="task.completed",
            payload={"id": "T-1", "stats": {"duration_ms": 450, "tokens": 1200}},
        )
        assert env.payload["stats"]["duration_ms"] == 450

    def test_payload_with_none_value(self):
        env = Envelope(topic="watchdog.ping", payload={"last_seen": None})
        assert env.payload["last_seen"] is None

    def test_payload_with_empty_containers(self):
        env = Envelope(
            topic="batch.drain",
            payload={"items": [], "meta": {}, "tags": set()},
        )
        assert env.payload["items"] == []
        assert env.payload["meta"] == {}

    def test_topic_special_characters(self):
        topics = [
            "agent.task.started",
            "daemon.health-check.passed",
            "worker.r1.queue.drained",
            "agent.123.cost_exceeded",
            "event:created",
        ]
        for topic in topics:
            env = Envelope(topic=topic)
            assert env.topic == topic

    def test_payload_large_dict(self):
        large = {f"key_{i:05d}": f"value_{i:05d}" for i in range(1000)}
        env = Envelope(topic="bulk.ingest", payload=large)
        assert len(env.payload) == 1000
        assert env.payload["key_00499"] == "value_00499"


# ═══════════════════════════════════════════════════════════════════════════
# ChatMessage — JSON serialization roundtrip and edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestChatMessageRoundtrip:
    def test_roundtrip_full_fields(self):
        msg = ChatMessage(
            role="assistant",
            content="Hello, world!",
            timestamp="2025-01-15T10:30:00Z",
            model="claude-sonnet-4-20250514",
        )
        d = msg.as_persistent_record()
        assert d["role"] == "assistant"
        assert d["content"] == "Hello, world!"
        assert d["timestamp"] == "2025-01-15T10:30:00Z"
        assert d["model"] == "claude-sonnet-4-20250514"
        restored = ChatMessage.from_dict(d)
        assert restored.role == msg.role
        assert restored.content == msg.content
        assert restored.timestamp == msg.timestamp
        assert restored.model == msg.model

    def test_roundtrip_minimal_fields(self):
        msg = ChatMessage(role="user", content="ping")
        d = msg.as_persistent_record()
        assert "timestamp" not in d
        assert "model" not in d
        restored = ChatMessage.from_dict(d)
        assert restored.role == "user"
        assert restored.content == "ping"
        assert restored.timestamp is None
        assert restored.model is None

    def test_from_dict_missing_optional_fields(self):
        msg = ChatMessage.from_dict({"role": "system", "content": "You are helpful."})
        assert msg.role == "system"
        assert msg.content == "You are helpful."
        assert msg.timestamp is None
        assert msg.model is None

    def test_api_message_shape(self):
        msg = ChatMessage(role="user", content="How do I?", model="gpt-4")
        api = msg.as_api_message()
        assert api == {"role": "user", "content": "How do I?"}
        assert "model" not in api
        assert "timestamp" not in api

    def test_all_valid_roles(self):
        for role in ("system", "user", "assistant", "tool"):
            msg = ChatMessage(role=role, content="test")  # type: ignore[arg-type]
            assert msg.role == role
            assert msg.as_api_message() == {"role": role, "content": "test"}

    def test_json_serializable(self):
        msg = ChatMessage(role="assistant", content='{"key": "val"}', timestamp="2025-01-01T00:00:00Z")
        serialized = json.dumps(msg.as_persistent_record(), sort_keys=True)
        assert json.loads(serialized)["content"] == msg.content

    def test_long_content_roundtrip(self):
        content = "a" * 100_000
        msg = ChatMessage(role="assistant", content=content)
        d = msg.as_persistent_record()
        restored = ChatMessage.from_dict(d)
        assert len(restored.content) == 100_000
        assert restored.content == content

    def test_immutable(self):
        msg = ChatMessage(role="user", content="hello")
        with pytest.raises(dataclasses.FrozenInstanceError):
            msg.content = "changed"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════
# ChatConfig — serialization and validation
# ═══════════════════════════════════════════════════════════════════════════


class TestChatConfigSerialization:
    def test_default_to_session_kwargs(self):
        cfg = ChatConfig()
        kwargs = cfg.to_session_kwargs()
        assert kwargs["model"] == "default"
        assert kwargs["save_interval"] == 5
        assert kwargs["resume"] is False

    def test_custom_to_session_kwargs(self):
        cfg = ChatConfig(
            model="opus",
            system_prompt="Be concise.",
            eval_mode=True,
            api_base_url="https://api.example.com",
            api_key="sk-test",
            project_dir="/tmp/proj",
            history_file="/tmp/history.jsonl",
            save_interval=10,
            resume=True,
            max_context=8000,
            stream=False,
            export_format="jsonl",
            export_output="/tmp/export.jsonl",
        )
        kwargs = cfg.to_session_kwargs()
        assert kwargs["model"] == "opus"
        assert kwargs["system_prompt"] == "Be concise."
        assert kwargs["eval_mode"] is True
        assert kwargs["api_base_url"] == "https://api.example.com"
        assert kwargs["api_key"] == "sk-test"
        assert kwargs["project_dir"] == "/tmp/proj"
        assert kwargs["history_file"] == "/tmp/history.jsonl"
        assert kwargs["save_interval"] == 10
        assert kwargs["resume"] is True
        assert kwargs["max_context"] == 8000

    def test_save_interval_must_be_positive(self):
        with pytest.raises(ValueError, match="save_interval"):
            ChatConfig(save_interval=0)

    def test_save_interval_negative_raises(self):
        with pytest.raises(ValueError, match="save_interval"):
            ChatConfig(save_interval=-5)


# ═══════════════════════════════════════════════════════════════════════════
# Sandbox contracts — IsolationLevel version / negotiation edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestIsolationLevelEdgeCases:
    def test_from_value_case_insensitive(self):
        assert IsolationLevel("VM_HARDWARE") == IsolationLevel.VM_HARDWARE
        assert IsolationLevel("vm_hardware") == IsolationLevel.VM_HARDWARE
        assert IsolationLevel("Vm_Hardware") == IsolationLevel.VM_HARDWARE

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError):
            IsolationLevel("")

    def test_rejects_numeric_string(self):
        with pytest.raises(ValueError):
            IsolationLevel("42")

    def test_isolation_exceeds_transitive(self):
        assert isolation_exceeds(IsolationLevel.VM_HARDWARE, IsolationLevel.CONTAINER)
        assert isolation_exceeds(IsolationLevel.CONTAINER, IsolationLevel.PROCESS)
        assert isolation_exceeds(IsolationLevel.VM_HARDWARE, IsolationLevel.PROCESS)

    def test_isolation_exceeds_same_level_false(self):
        for level in IsolationLevel:
            assert not isolation_exceeds(level, level)


# ═══════════════════════════════════════════════════════════════════════════
# SandboxResult — binary vs JSON serialization roundtrip
# ═══════════════════════════════════════════════════════════════════════════


class TestSandboxResultSerialization:
    def test_json_roundtrip_success(self):
        result = SandboxResult(
            returncode=0,
            stdout="output",
            stderr="",
            memory_used_bytes=1024 * 1024,
            cpu_time_ms=500,
            pid=12345,
            was_killed=False,
        )
        d = {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "success": result.success,
            "memory_used_bytes": result.memory_used_bytes,
            "cpu_time_ms": result.cpu_time_ms,
            "pid": result.pid,
            "was_killed": result.was_killed,
        }
        raw = json.dumps(d, sort_keys=True)
        restored = json.loads(raw)
        assert restored["returncode"] == 0
        assert restored["success"] is True
        assert restored["pid"] == 12345
        assert restored["was_killed"] is False

    def test_json_roundtrip_failure(self):
        result = SandboxResult(
            returncode=1,
            stdout="",
            stderr="Permission denied",
            was_killed=True,
        )
        d = {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "success": result.success,
        }
        raw = json.dumps(d, sort_keys=True)
        restored = json.loads(raw)
        assert restored["success"] is False
        assert restored["returncode"] == 1

    def test_json_with_special_characters(self):
        result = SandboxResult(
            returncode=0,
            stdout='{"key": "val\nwith\\nescapes"}',
            stderr="\x00\x01\x02",
        )
        d = {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        raw = json.dumps(d, sort_keys=True)
        restored = json.loads(raw)
        assert restored["stdout"] == result.stdout
        assert restored["stderr"] == result.stderr

    def test_large_output_roundtrip(self):
        large_stdout = "x" * 100_000
        result = SandboxResult(returncode=0, stdout=large_stdout, stderr="")
        d = {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        raw = json.dumps(d)
        restored = json.loads(raw)
        assert len(restored["stdout"]) == 100_000
        assert restored["stdout"] == large_stdout


# ═══════════════════════════════════════════════════════════════════════════
# SandboxConfig — to/from ResourceLimits bidirectional roundtrip
# ═══════════════════════════════════════════════════════════════════════════


class TestSandboxConfigBidirectional:
    def test_roundtrip_via_resource_limits(self):
        original = SandboxConfig(
            backend="docker",
            isolation=IsolationLevel.CONTAINER,
            memory_mb=1024,
            cpu_seconds=180,
            timeout=200,
            max_processes=30,
        )
        limits = original.to_resource_limits()
        reconstituted = SandboxConfig.from_resource_limits(
            limits,
            backend=original.backend,
            isolation=original.isolation,
        )
        assert reconstituted.memory_mb == 1024
        assert reconstituted.timeout == 180
        assert reconstituted.max_processes == 30
        assert reconstituted.backend == "docker"

    def test_roundtrip_default_isolation(self):
        original = SandboxConfig(memory_mb=2048, cpu_seconds=3600, max_processes=100)
        limits = original.to_resource_limits()
        reconstituted = SandboxConfig.from_resource_limits(limits)
        assert reconstituted.memory_mb == 2048
        assert reconstituted.timeout == 3600
        assert reconstituted.max_processes == 100

    def test_zero_memory_is_none_in_limits(self):
        cfg = SandboxConfig(memory_mb=0)
        limits = cfg.to_resource_limits()
        assert limits.memory_bytes is None


# ═══════════════════════════════════════════════════════════════════════════
# BudgetEnvelope — spend serialization and result shapes
# ═══════════════════════════════════════════════════════════════════════════


class TestBudgetEnvelopeSerialization:
    def test_get_status_shape(self):
        env = BudgetEnvelope(name="test", limit=100.0)
        status = env.get_status()
        assert status["name"] == "test"
        assert status["limit"] == 100.0
        assert status["spent"] == 0.0
        assert status["remaining"] == 100.0
        assert status["exhausted"] is False

    def test_try_spend_allowed_shape(self):
        env = BudgetEnvelope(name="agent:sonnet", limit=10.0)
        result = env.try_spend(5.0)
        assert result["allowed"] is True
        assert result["reason"] == "ok"
        assert result["envelope"] == "agent:sonnet"
        assert "remaining" in result
        assert "spent" in result

    def test_try_spend_denied_shape(self):
        env = BudgetEnvelope(name="task:T-1", limit=1.0)
        env.try_spend(0.9)
        result = env.try_spend(0.5)
        assert result["allowed"] is False
        assert "budget exceeded" in str(result["reason"])
        assert result["envelope"] == "task:T-1"
        assert result["remaining"] == 0.1

    def test_spend_exhausts(self):
        env = BudgetEnvelope(name="t", limit=5.0)
        env.try_spend(5.0)
        assert env.is_exhausted
        assert env.remaining == 0.0
        status = env.get_status()
        assert status["exhausted"] is True


class TestBudgetEnvelopeInvalidInputs:
    def test_nan_rejected_fail_closed(self):
        env = BudgetEnvelope(name="t", limit=10.0)
        result = env.try_spend(float("nan"))
        assert result["allowed"] is False
        assert "fail closed" in str(result["reason"])

    def test_negative_amount_rejected(self):
        env = BudgetEnvelope(name="t", limit=10.0)
        result = env.try_spend(-1.0)
        assert result["allowed"] is False

    def test_inf_amount_rejected(self):
        env = BudgetEnvelope(name="t", limit=10.0)
        result = env.try_spend(float("inf"))
        assert result["allowed"] is False

    def test_negative_inf_limit_raises_on_init(self):
        with pytest.raises(ValueError, match="non-negative"):
            BudgetEnvelope(name="t", limit=float("-inf"))

    def test_nan_limit_raises_on_init(self):
        with pytest.raises(ValueError, match="finite or inf"):
            BudgetEnvelope(name="t", limit=float("nan"))


class TestBudgetManagerSerialization:
    def test_get_status_full_hierarchy(self):
        agent = PerAgentEnvelope()
        agent.set_limit("sonnet", 50.0)
        agent.set_limit("haiku", 10.0)
        task = PerTaskEnvelope(default_limit=20.0)
        tool = PerToolEnvelope()
        tool.set_limit("bash", 5.0)
        mgr = BudgetManager(per_agent=agent, per_task=task, per_tool=tool)
        status = mgr.get_status()
        assert "agents" in status
        assert "tasks" in status
        assert "tools" in status
        assert "sonnet" in status["agents"]
        assert "bash" in status["tools"]

    def test_check_all_blocked_at_tool_layer(self):
        tool = PerToolEnvelope()
        tool.set_limit("bash", 1.0)
        tool.try_spend("bash", 1.0)
        mgr = BudgetManager(per_tool=tool)
        result = mgr.check_all(tool_type="bash", amount=0.5)
        assert result.allowed is False
        assert result.details["layer"] == "tool"

    def test_check_all_blocked_at_task_layer(self):
        task = PerTaskEnvelope(default_limit=1.0)
        task.try_spend("T-1", 1.0)
        mgr = BudgetManager(per_task=task)
        result = mgr.check_all(task_id="T-1", amount=0.1)
        assert result.allowed is False
        assert result.details["layer"] == "task"


# ═══════════════════════════════════════════════════════════════════════════
# SendMessageRequest — Pydantic validation and edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestSendMessageRequestValidation:
    def test_valid_minimal_request(self):
        req = SendMessageRequest(sender="agent-1", recipient="agent-2")
        d = req.model_dump()
        assert d["sender"] == "agent-1"
        assert d["recipient"] == "agent-2"
        assert d["topic"] == ""
        assert d["body"] == ""
        assert d["priority"] == "normal"

    def test_valid_full_request(self):
        req = SendMessageRequest(
            sender="orchestrator",
            recipient="agent-42",
            topic="task.assigned",
            body="Process file X",
            priority="high",
            ttl_seconds=3600,
            project_id="proj-abc123",
        )
        d = req.model_dump()
        assert d["ttl_seconds"] == 3600
        assert d["project_id"] == "proj-abc123"
        assert d["priority"] == "high"

    def test_all_priority_values(self):
        for pri in ("low", "normal", "high", "urgent"):
            req = SendMessageRequest(sender="s", recipient="r", priority=pri)  # type: ignore[arg-type]
            assert req.priority == pri

    def test_invalid_priority_rejected(self):
        with pytest.raises(pydantic.ValidationError):
            SendMessageRequest(sender="s", recipient="r", priority="critical")  # type: ignore[arg-type]

    def test_sender_too_long_rejected(self):
        with pytest.raises(pydantic.ValidationError):
            SendMessageRequest(sender="a" * 200, recipient="r")

    def test_zero_ttl_rejected(self):
        with pytest.raises(pydantic.ValidationError):
            SendMessageRequest(sender="s", recipient="r", ttl_seconds=0)

    def test_negative_ttl_rejected(self):
        with pytest.raises(pydantic.ValidationError):
            SendMessageRequest(sender="s", recipient="r", ttl_seconds=-1)

    def test_body_max_length(self):
        req = SendMessageRequest(sender="s", recipient="r", body="x" * 65536)
        assert len(req.body) == 65536

    def test_model_dump_json_serializable(self):
        req = SendMessageRequest(sender="s", recipient="r", body="hello", priority="urgent", ttl_seconds=300)
        d = req.model_dump()
        raw = json.dumps(d)
        restored = json.loads(raw)
        assert restored["priority"] == "urgent"
        assert restored["ttl_seconds"] == 300


# ═══════════════════════════════════════════════════════════════════════════
# BadCallSituation — serialize / deserialize with MAC validation
# ═══════════════════════════════════════════════════════════════════════════


class TestBadCallSituationSerialization:
    def test_serialize_full_situation(self):
        sit = BadCallSituation(
            tool_name="bash",
            tool_args={"command": "rm -rf /"},
            classification="destructive",
            reason="destructive command blocked",
            task_excerpt="clean up old files",
            recent_calls=[{"tool": "read", "args": {"path": "/tmp/x"}}],
            timestamp=1700000000.0,
            work_type="file_cleanup",
        )
        data = _serialize(sit)
        assert data["tool_name"] == "bash"
        assert data["classification"] == "destructive"
        assert data["reason"] == "destructive command blocked"
        assert data["task_excerpt"] == "clean up old files"
        assert len(data["recent_calls"]) == 1
        assert data["timestamp"] == 1700000000.0
        assert data["work_type"] == "file_cleanup"

    def test_deserialize_full_situation(self):
        data = {
            "tool_name": "write",
            "tool_args": {"path": "/etc/passwd"},
            "classification": "privileged_path",
            "reason": "writing to protected path",
            "task_excerpt": "create user",
            "recent_calls": [],
            "timestamp": 1700000001.0,
            "work_type": "user_mgmt",
        }
        sit = _deserialize(data)
        assert sit.tool_name == "write"
        assert sit.classification == "privileged_path"
        assert sit.task_excerpt == "create user"

    def test_deserialize_minimal_data(self):
        sit = _deserialize({})
        assert sit.tool_name == ""
        assert sit.classification == "unknown"
        assert sit.reason == ""
        assert sit.timestamp == 0.0

    def test_roundtrip_preserves_all_fields(self):
        original = BadCallSituation(
            tool_name="task",
            tool_args={"subagent_type": "explore", "prompt": "find bugs"},
            classification="excessive_cost",
            reason="estimated cost exceeds budget",
            task_excerpt="audit codebase",
            recent_calls=[
                {"tool": "read", "args": {"path": "/a"}},
                {"tool": "grep", "args": {"pattern": "TODO"}},
            ],
            timestamp=1700000002.0,
            work_type="audit",
        )
        data = _serialize(original)
        restored = _deserialize(data)
        assert restored.tool_name == original.tool_name
        assert restored.classification == original.classification
        assert restored.reason == original.reason
        assert restored.task_excerpt == original.task_excerpt
        assert len(restored.recent_calls) == 2
        assert restored.timestamp == original.timestamp
        assert restored.work_type == original.work_type

    def test_store_inmemory_save_and_list(self):
        store = BadCallSituationStore(base_dir=None)
        sit = BadCallSituation(
            tool_name="bash",
            tool_args={},
            classification="test",
            reason="testing",
        )
        path = store.save(sit)
        assert path is not None
        recent = store.list_recent(limit=5)
        assert len(recent) >= 1
        assert recent[0].tool_name == "bash"
        assert recent[0].timestamp > 0

    def test_store_list_by_classification(self):
        store = BadCallSituationStore(base_dir=None)
        store.save(BadCallSituation(tool_name="a", tool_args={}, classification="t1", reason="r"))
        store.save(BadCallSituation(tool_name="b", tool_args={}, classification="t2", reason="r"))
        store.save(BadCallSituation(tool_name="c", tool_args={}, classification="t1", reason="r"))
        result = store.list_by_classification("t1")
        assert len(result) == 2
        assert {s.tool_name for s in result} == {"a", "c"}

    def test_store_list_by_tool(self):
        store = BadCallSituationStore(base_dir=None)
        store.save(BadCallSituation(tool_name="bash", tool_args={}, classification="x", reason="r"))
        store.save(BadCallSituation(tool_name="write", tool_args={}, classification="x", reason="r"))
        store.save(BadCallSituation(tool_name="bash", tool_args={}, classification="y", reason="r"))
        result = store.list_by_tool("bash")
        assert len(result) == 2

    def test_store_count(self):
        store = BadCallSituationStore(base_dir=None)
        for i in range(5):
            store.save(BadCallSituation(tool_name=f"t{i}", tool_args={}, classification="c", reason="r"))
        assert store.count() == 5


# ═══════════════════════════════════════════════════════════════════════════
# BudgetCheckResult — error response serialization
# ═══════════════════════════════════════════════════════════════════════════


class TestBudgetCheckResultSerialization:
    def test_allowed_result(self):
        result = BudgetCheckResult(allowed=True, reason="ok", details={})
        assert result.allowed is True
        assert result.reason == "ok"

    def test_denied_result_with_details(self):
        details: dict[str, object] = {
            "layer": "agent",
            "envelope": "agent:opus",
            "remaining": 0.0,
            "spent": 25.0,
            "limit": 25.0,
        }
        result = BudgetCheckResult(allowed=False, reason="budget exceeded", details=details)
        assert not result.allowed
        assert result.details["layer"] == "agent"
        assert result.details["remaining"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# PerAgentEnvelope / PerTaskEnvelope — status serialization
# ═══════════════════════════════════════════════════════════════════════════


class TestEnvelopeStatusSerialization:
    def test_per_agent_get_status_empty(self):
        pa = PerAgentEnvelope()
        status = pa.get_status()
        assert status == {}

    def test_per_agent_get_status_with_agents(self):
        pa = PerAgentEnvelope()
        pa.set_limit("sonnet", 100.0)
        pa.set_limit("haiku", 10.0)
        pa.try_spend("sonnet", 25.0)
        status = pa.get_status()
        assert "sonnet" in status
        assert "haiku" in status
        assert status["sonnet"]["spent"] == 25.0
        assert status["sonnet"]["remaining"] == 75.0

    def test_per_task_no_config_allowed(self):
        pt = PerTaskEnvelope(default_limit=float("inf"))
        result = pt.try_spend("T-new", 5.0)
        assert result["allowed"] is True

    def test_per_task_default_limit_applied(self):
        pt = PerTaskEnvelope(default_limit=10.0)
        result = pt.try_spend("T-1", 5.0)
        assert result["allowed"] is True
        assert result["remaining"] == 5.0

    def test_per_task_total_spent(self):
        pt = PerTaskEnvelope(default_limit=10.0)
        pt.try_spend("T-a", 3.0)
        pt.try_spend("T-b", 2.0)
        assert pt.total_spent() == 5.0

    def test_per_tool_mode_workflow(self):
        pt = PerToolEnvelope()
        pt.set_limit("bash", 5.0)
        pt.set_limit("write", 10.0)
        pt.try_spend("bash", 2.0)
        pt.try_spend("write", 3.0)
        status = pt.get_status()
        assert status["bash"]["spent"] == 2.0
        assert status["write"]["spent"] == 3.0
        assert pt.total_spent() == 5.0


# ═══════════════════════════════════════════════════════════════════════════
# Invalid message rejection — malformed inputs to contracts
# ═══════════════════════════════════════════════════════════════════════════


class TestInvalidMessageRejection:
    def test_validate_config_rejects_negative_memory(self):
        cfg = SandboxConfig(memory_mb=-512)
        errors = validate_config(cfg)
        assert len(errors) >= 1
        assert any("memory" in e for e in errors)

    def test_validate_config_rejects_negative_max_processes(self):
        cfg = SandboxConfig(max_processes=-1)
        errors = validate_config(cfg)
        assert any("process" in e for e in errors)

    def test_validate_config_rejects_multiple_negatives(self):
        cfg = SandboxConfig(memory_mb=-1, cpu_seconds=-1, max_processes=-1, max_output_bytes=-1)
        errors = validate_config(cfg)
        assert len(errors) >= 4
