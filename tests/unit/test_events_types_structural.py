"""Structural tests for events/types.py — Event, EventType, and all typed event subclasses."""

from __future__ import annotations

from general_ludd.events.types import (
    ConfigReloadedEvent,
    CustomEvent,
    Event,
    EventType,
    HookTriggeredEvent,
    ModelAddedEvent,
    ModelRemovedEvent,
    PlaybookRegisteredEvent,
    PlaybookRemovedEvent,
    ReloadCompletedEvent,
    ReloadFailedEvent,
    ReloadRequestedEvent,
    SelfUpdateAppliedEvent,
    SkillUpdatedEvent,
    SlowOperationEvent,
    StallDetectedEvent,
    TemplateUpdatedEvent,
    WorkerPingEvent,
    WorkerPongEvent,
)


class TestEventType:
    def test_all_members_have_values(self):
        for member in EventType:
            assert isinstance(member.value, str)
            assert len(member.value) > 0

    def test_member_count(self):
        assert len(list(EventType)) == 21

    def test_custom_event_type(self):
        assert EventType.CUSTOM.value == "custom"

    def test_branch_executed_event_type(self):
        assert EventType.BRANCH_EXECUTED.value == "branch_executed"


class TestBaseEvent:
    def test_minimal_event(self):
        ev = Event(type=EventType.MODEL_ADDED)
        assert ev.type == EventType.MODEL_ADDED
        assert ev.payload == {}
        assert ev.source is None
        assert ev.correlation_id is None
        assert isinstance(ev.timestamp, float)
        assert len(ev.event_id) == 32

    def test_event_with_payload(self):
        ev = Event(type=EventType.CUSTOM, payload={"k": "v"}, source="src")
        assert ev.payload == {"k": "v"}
        assert ev.source == "src"

    def test_event_id_uniqueness(self):
        ids = {Event(type=EventType.CONFIG_RELOADED).event_id for _ in range(10)}
        assert len(ids) == 10

    def test_event_accepts_string_type(self):
        ev = Event(type="custom_string")
        assert ev.type == "custom_string"

    def test_correlation_id_settable(self):
        ev = Event(type=EventType.MODEL_REMOVED, correlation_id="corr-1")
        assert ev.correlation_id == "corr-1"


class TestModelAddedEvent:
    def test_model_added(self):
        ev = ModelAddedEvent(model_id="m1", profile={"param": "val"})
        assert ev.type == EventType.MODEL_ADDED
        assert ev.payload["model_id"] == "m1"
        assert ev.payload["profile"] == {"param": "val"}


class TestModelRemovedEvent:
    def test_model_removed(self):
        ev = ModelRemovedEvent(model_id="m1")
        assert ev.type == EventType.MODEL_REMOVED
        assert ev.payload["model_id"] == "m1"


class TestConfigReloadedEvent:
    def test_config_reloaded(self):
        ev = ConfigReloadedEvent(scope="all")
        assert ev.type == EventType.CONFIG_RELOADED
        assert ev.payload["scope"] == "all"


class TestTemplateUpdatedEvent:
    def test_template_updated(self):
        ev = TemplateUpdatedEvent(templates=["t1", "t2"])
        assert ev.type == EventType.TEMPLATE_UPDATED
        assert ev.payload["templates"] == ["t1", "t2"]


class TestStallDetectedEvent:
    def test_stall_detected(self):
        ev = StallDetectedEvent(operation="op", elapsed_s=10.0, deadline_s=5.0)
        assert ev.type == EventType.STALL_DETECTED
        assert ev.payload["operation"] == "op"
        assert ev.payload["elapsed_s"] == 10.0
        assert ev.payload["deadline_s"] == 5.0

    def test_stall_with_thread_stacks(self):
        ev = StallDetectedEvent(operation="op", elapsed_s=1.0, deadline_s=2.0, thread_stacks={"t": "stack"})
        assert ev.payload["thread_stacks"] == {"t": "stack"}


class TestSlowOperationEvent:
    def test_slow_operation(self):
        ev = SlowOperationEvent(operation="op", duration_s=5.0, baseline_s=1.0, factor=5.0)
        assert ev.type == EventType.SLOW_OPERATION
        assert ev.payload["duration_s"] == 5.0
        assert ev.payload["baseline_s"] == 1.0
        assert ev.payload["factor"] == 5.0


class TestPlaybookEvents:
    def test_playbook_registered(self):
        ev = PlaybookRegisteredEvent(playbook="noop.yml")
        assert ev.type == EventType.PLAYBOOK_REGISTERED
        assert ev.payload["playbook"] == "noop.yml"

    def test_playbook_removed(self):
        ev = PlaybookRemovedEvent(playbook="old.yml")
        assert ev.type == EventType.PLAYBOOK_REMOVED
        assert ev.payload["playbook"] == "old.yml"


class TestSkillUpdatedEvent:
    def test_skill_updated(self):
        ev = SkillUpdatedEvent(skill="guardrail-pattern")
        assert ev.type == EventType.SKILL_UPDATED
        assert ev.payload["skill"] == "guardrail-pattern"


class TestReloadEvents:
    def test_reload_requested(self):
        ev = ReloadRequestedEvent(scope="all")
        assert ev.type == EventType.RELOAD_REQUESTED
        assert ev.payload["scope"] == "all"

    def test_reload_completed(self):
        ev = ReloadCompletedEvent(scope="models")
        assert ev.type == EventType.RELOAD_COMPLETED
        assert ev.payload["scope"] == "models"

    def test_reload_failed(self):
        ev = ReloadFailedEvent(scope="config", error="parse error")
        assert ev.type == EventType.RELOAD_FAILED
        assert ev.payload["scope"] == "config"
        assert ev.payload["error"] == "parse error"


class TestWorkerEvents:
    def test_worker_ping(self):
        ev = WorkerPingEvent()
        assert ev.type == EventType.WORKER_PING
        assert ev.payload == {}

    def test_worker_pong(self):
        ev = WorkerPongEvent(worker_id="w1")
        assert ev.type == EventType.WORKER_PONG
        assert ev.payload["worker_id"] == "w1"


class TestHookTriggeredEvent:
    def test_hook_triggered(self):
        ev = HookTriggeredEvent(event_name="pre_tool")
        assert ev.type == EventType.HOOK_TRIGGERED
        assert ev.payload["event_name"] == "pre_tool"


class TestCustomEvent:
    def test_custom_event(self):
        ev = CustomEvent(name="my_event", payload={"k": "v"})
        assert ev.type == EventType.CUSTOM
        assert ev.payload["name"] == "my_event"
        assert ev.payload["k"] == "v"

    def test_custom_event_no_payload(self):
        ev = CustomEvent(name="my_event")
        assert ev.payload == {"name": "my_event"}


class TestSelfUpdateAppliedEvent:
    def test_self_update_applied(self):
        ev = SelfUpdateAppliedEvent(commit_sha="abc123", reloaded_modules=["mod1", "mod2"])
        assert ev.type == EventType.SELF_UPDATE_APPLIED
        assert ev.payload["commit_sha"] == "abc123"
        assert ev.payload["reloaded_modules"] == ["mod1", "mod2"]
