from __future__ import annotations

import time

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
    def test_all_event_types_are_strings(self) -> None:
        for member in EventType:
            assert isinstance(member.value, str)

    def test_event_type_values_unique(self) -> None:
        vals = [m.value for m in EventType]
        assert len(vals) == len(set(vals))


class TestEvent:
    def test_default_construction(self) -> None:
        e = Event(type=EventType.CUSTOM)
        assert e.type == EventType.CUSTOM
        assert e.payload == {}
        assert e.source is None
        assert e.correlation_id is None
        assert isinstance(e.timestamp, float)
        assert isinstance(e.event_id, str)
        assert len(e.event_id) == 32

    def test_full_construction(self) -> None:
        e = Event(
            type="custom.event",
            payload={"key": "val"},
            source="test_source",
            correlation_id="corr-1",
        )
        assert e.type == "custom.event"
        assert e.payload == {"key": "val"}
        assert e.source == "test_source"
        assert e.correlation_id == "corr-1"

    def test_timestamp_is_now(self) -> None:
        before = time.time()
        e = Event(type=EventType.CUSTOM)
        after = time.time()
        assert before <= e.timestamp <= after + 0.01

    def test_event_id_is_unique(self) -> None:
        e1 = Event(type=EventType.CUSTOM)
        e2 = Event(type=EventType.CUSTOM)
        assert e1.event_id != e2.event_id


class TestConcreteEvents:
    def test_model_added_event(self) -> None:
        e = ModelAddedEvent(model_id="gpt-4", profile={"tier": "premium"})
        assert e.type == EventType.MODEL_ADDED
        assert e.payload["model_id"] == "gpt-4"
        assert e.payload["profile"] == {"tier": "premium"}

    def test_model_removed_event(self) -> None:
        e = ModelRemovedEvent(model_id="gpt-3")
        assert e.type == EventType.MODEL_REMOVED
        assert e.payload["model_id"] == "gpt-3"

    def test_config_reloaded_event(self) -> None:
        e = ConfigReloadedEvent(scope="models")
        assert e.type == EventType.CONFIG_RELOADED
        assert e.payload["scope"] == "models"

    def test_template_updated_event(self) -> None:
        e = TemplateUpdatedEvent(templates=["t1", "t2"])
        assert e.type == EventType.TEMPLATE_UPDATED
        assert e.payload["templates"] == ["t1", "t2"]

    def test_playbook_registered_event(self) -> None:
        e = PlaybookRegisteredEvent(playbook="deploy")
        assert e.type == EventType.PLAYBOOK_REGISTERED
        assert e.payload["playbook"] == "deploy"

    def test_playbook_removed_event(self) -> None:
        e = PlaybookRemovedEvent(playbook="deploy")
        assert e.type == EventType.PLAYBOOK_REMOVED
        assert e.payload["playbook"] == "deploy"

    def test_skill_updated_event(self) -> None:
        e = SkillUpdatedEvent(skill="guardrail-pattern")
        assert e.type == EventType.SKILL_UPDATED
        assert e.payload["skill"] == "guardrail-pattern"

    def test_reload_requested_event(self) -> None:
        e = ReloadRequestedEvent(scope="all")
        assert e.type == EventType.RELOAD_REQUESTED
        assert e.payload["scope"] == "all"

    def test_reload_completed_event(self) -> None:
        e = ReloadCompletedEvent(scope="models")
        assert e.type == EventType.RELOAD_COMPLETED
        assert e.payload["scope"] == "models"

    def test_reload_failed_event(self) -> None:
        e = ReloadFailedEvent(scope="models", error="timeout")
        assert e.type == EventType.RELOAD_FAILED
        assert e.payload["scope"] == "models"
        assert e.payload["error"] == "timeout"

    def test_worker_ping_event(self) -> None:
        e = WorkerPingEvent()
        assert e.type == EventType.WORKER_PING
        assert e.payload == {}

    def test_worker_pong_event(self) -> None:
        e = WorkerPongEvent(worker_id="w-1")
        assert e.type == EventType.WORKER_PONG
        assert e.payload["worker_id"] == "w-1"

    def test_hook_triggered_event(self) -> None:
        e = HookTriggeredEvent(event_name="pre-commit")
        assert e.type == EventType.HOOK_TRIGGERED
        assert e.payload["event_name"] == "pre-commit"

    def test_stall_detected_event(self) -> None:
        e = StallDetectedEvent(operation="gate", elapsed_s=300.0, deadline_s=15.0)
        assert e.type == EventType.STALL_DETECTED
        assert e.payload["operation"] == "gate"
        assert e.payload["elapsed_s"] == 300.0
        assert e.payload["deadline_s"] == 15.0

    def test_slow_operation_event(self) -> None:
        e = SlowOperationEvent(operation="query", duration_s=5.0, baseline_s=1.0, factor=5.0)
        assert e.type == EventType.SLOW_OPERATION
        assert e.payload["operation"] == "query"
        assert e.payload["duration_s"] == 5.0
        assert e.payload["baseline_s"] == 1.0
        assert e.payload["factor"] == 5.0

    def test_custom_event(self) -> None:
        e = CustomEvent(name="user.login", payload={"user": "admin"})
        assert e.type == EventType.CUSTOM
        assert e.payload["name"] == "user.login"
        assert e.payload["user"] == "admin"

    def test_custom_event_default_payload(self) -> None:
        e = CustomEvent(name="ping")
        assert e.type == EventType.CUSTOM
        assert e.payload["name"] == "ping"

    def test_self_update_applied_event(self) -> None:
        e = SelfUpdateAppliedEvent(commit_sha="abc123", reloaded_modules=["mod_a", "mod_b"])
        assert e.type == EventType.SELF_UPDATE_APPLIED
        assert e.payload["commit_sha"] == "abc123"
        assert e.payload["reloaded_modules"] == ["mod_a", "mod_b"]

    def test_event_kwargs_forwarded(self) -> None:
        e = ModelAddedEvent(model_id="x", profile={}, source="src", correlation_id="cid")
        assert e.source == "src"
        assert e.correlation_id == "cid"

    def test_event_inherits_from_event(self) -> None:
        e = ModelAddedEvent(model_id="x", profile={})
        assert isinstance(e, Event)
