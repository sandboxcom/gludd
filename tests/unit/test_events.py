"""Verbose unit tests for EventBus, HookSystem, and event types."""
import time
from unittest.mock import MagicMock, patch

from general_ludd.events.bus import EventBus
from general_ludd.events.hooks import HookRegistration, HookSystem, WebhookConfig
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
    SkillUpdatedEvent,
    TemplateUpdatedEvent,
    WorkerPingEvent,
    WorkerPongEvent,
)


class TestEventBusUnit:

    def test_empty_bus_has_no_history(self):
        bus = EventBus(history_size=10)
        assert bus.get_history() == []

    def test_subscribe_returns_unique_ids(self):
        bus = EventBus()
        id1 = bus.subscribe(EventType.MODEL_ADDED, lambda e: None)
        id2 = bus.subscribe(EventType.MODEL_ADDED, lambda e: None)
        assert id1 != id2

    def test_unsubscribe_nonexistent_id_is_noop(self):
        bus = EventBus()
        bus.unsubscribe("nonexistent")

    def test_publish_with_no_subscribers_returns_zero(self):
        bus = EventBus()
        count = bus.publish(Event(type=EventType.MODEL_ADDED, payload={}))
        assert count == 0

    def test_wildcard_receives_all_event_types(self):
        bus = EventBus()
        received = []
        bus.subscribe("*", lambda e: received.append(e.type))
        bus.publish(Event(type=EventType.MODEL_ADDED, payload={}))
        bus.publish(Event(type=EventType.CONFIG_RELOADED, payload={}))
        bus.publish(Event(type="custom_event", payload={}))
        assert len(received) == 3
        assert EventType.MODEL_ADDED in received
        assert EventType.CONFIG_RELOADED in received
        assert "custom_event" in received

    def test_history_respects_max_size(self):
        bus = EventBus(history_size=3)
        for i in range(10):
            bus.publish(Event(type=EventType.MODEL_ADDED, payload={"i": i}))
        history = bus.get_history()
        assert len(history) == 3
        assert history[0].payload["i"] == 7
        assert history[2].payload["i"] == 9

    def test_history_disabled_by_default(self):
        bus = EventBus()
        bus.publish(Event(type=EventType.MODEL_ADDED, payload={}))
        assert bus.get_history() == []

    def test_subscriber_receives_correct_event_data(self):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.TEMPLATE_UPDATED, lambda e: received.append(e))
        bus.publish(
            Event(
                type=EventType.TEMPLATE_UPDATED,
                payload={"templates": ["a.j2", "b.j2"]},
            )
        )
        assert len(received) == 1
        assert received[0].payload["templates"] == ["a.j2", "b.j2"]

    def test_multiple_subscribers_all_receive_event(self):
        bus = EventBus()
        r1, r2, r3 = [], [], []
        bus.subscribe(EventType.PLAYBOOK_REGISTERED, lambda e: r1.append(1))
        bus.subscribe(EventType.PLAYBOOK_REGISTERED, lambda e: r2.append(1))
        bus.subscribe(EventType.PLAYBOOK_REGISTERED, lambda e: r3.append(1))
        bus.publish(Event(type=EventType.PLAYBOOK_REGISTERED, payload={}))
        assert len(r1) == 1
        assert len(r2) == 1
        assert len(r3) == 1

    def test_async_subscriber_is_handled(self):
        bus = EventBus()
        received = []

        async def on_event(e):
            received.append(e)

        bus.subscribe(EventType.SKILL_UPDATED, on_event)
        bus.publish(Event(type=EventType.SKILL_UPDATED, payload={"skill": "test"}))
        assert len(received) == 1

    def test_clear_removes_all_subscriptions(self):
        bus = EventBus()
        bus.subscribe(EventType.MODEL_ADDED, lambda e: None)
        bus.subscribe(EventType.MODEL_REMOVED, lambda e: None)
        bus.clear()
        count = bus.publish(Event(type=EventType.MODEL_ADDED, payload={}))
        assert count == 0

    def test_event_has_timestamp(self):
        event = Event(type=EventType.CUSTOM, payload={})
        assert event.timestamp > 0
        assert event.timestamp <= time.time()

    def test_event_has_unique_id(self):
        e1 = Event(type=EventType.CUSTOM, payload={})
        e2 = Event(type=EventType.CUSTOM, payload={})
        assert e1.event_id != e2.event_id

    def test_subscriber_error_does_not_affect_others(self):
        bus = EventBus()
        ok = []

        def bad(e):
            raise ValueError("boom")

        bus.subscribe(EventType.RELOAD_FAILED, bad)
        bus.subscribe(EventType.RELOAD_FAILED, lambda e: ok.append(1))
        bus.publish(Event(type=EventType.RELOAD_FAILED, payload={}))
        assert len(ok) == 1

    def test_string_event_type(self):
        bus = EventBus()
        received = []
        bus.subscribe("my_custom_type", lambda e: received.append(e))
        bus.publish(Event(type="my_custom_type", payload={"x": 1}))
        assert len(received) == 1
        assert received[0].payload["x"] == 1

    def test_publish_returns_subscriber_count(self):
        bus = EventBus()
        bus.subscribe(EventType.MODEL_ADDED, lambda e: None)
        bus.subscribe(EventType.MODEL_ADDED, lambda e: None)
        bus.subscribe(EventType.MODEL_REMOVED, lambda e: None)
        count = bus.publish(Event(type=EventType.MODEL_ADDED, payload={}))
        assert count == 2

    def test_unsubscribe_specific_id(self):
        bus = EventBus()
        received = []
        sid = bus.subscribe(EventType.MODEL_ADDED, lambda e: received.append(1))
        bus.publish(Event(type=EventType.MODEL_ADDED, payload={}))
        assert len(received) == 1
        bus.unsubscribe(sid)
        bus.publish(Event(type=EventType.MODEL_ADDED, payload={}))
        assert len(received) == 1

    def test_history_preserves_event_order(self):
        bus = EventBus(history_size=10)
        for label in ["a", "b", "c"]:
            bus.publish(Event(type=EventType.CUSTOM, payload={"label": label}))
        history = bus.get_history()
        labels = [e.payload["label"] for e in history]
        assert labels == ["a", "b", "c"]

    def test_subscribe_id_format(self):
        bus = EventBus()
        sid = bus.subscribe(EventType.CUSTOM, lambda e: None)
        assert sid.startswith("sub-")

    def test_wildcard_plus_specific_subscriber_counted(self):
        bus = EventBus()
        bus.subscribe(EventType.CUSTOM, lambda e: None)
        bus.subscribe("*", lambda e: None)
        count = bus.publish(Event(type=EventType.CUSTOM, payload={}))
        assert count == 2

    def test_history_with_zero_size_is_empty(self):
        bus = EventBus(history_size=0)
        bus.publish(Event(type=EventType.CUSTOM, payload={}))
        bus.publish(Event(type=EventType.CUSTOM, payload={}))
        assert bus.get_history() == []

    def test_history_size_one_keeps_only_last(self):
        bus = EventBus(history_size=1)
        bus.publish(Event(type=EventType.CUSTOM, payload={"i": 1}))
        bus.publish(Event(type=EventType.CUSTOM, payload={"i": 2}))
        history = bus.get_history()
        assert len(history) == 1
        assert history[0].payload["i"] == 2


class TestHookSystemUnit:

    def test_empty_hook_system_fires_nothing(self):
        hooks = HookSystem()
        count = hooks.fire("nonexistent", {})
        assert count == 0

    def test_callback_registration_and_fire(self):
        hooks = HookSystem()
        received = []
        hooks.register_callback("on_test", lambda p: received.append(p))
        hooks.fire("on_test", {"key": "value"})
        assert len(received) == 1
        assert received[0]["key"] == "value"

    def test_webhook_registration_stores_config(self):
        hooks = HookSystem()
        _hook_id = hooks.register_webhook(
            "on_test",
            "https://example.com/hook",
            headers={"Auth": "Bearer x"},
            retry_count=3,
            timeout_seconds=15,
        )
        listing = hooks.list_hooks()
        assert len(listing) == 1
        assert listing[0].webhook_config.url == "https://example.com/hook"
        assert listing[0].webhook_config.headers["Auth"] == "Bearer x"
        assert listing[0].webhook_config.retry_count == 3
        assert listing[0].webhook_config.timeout_seconds == 15

    def test_unregister_callback(self):
        hooks = HookSystem()
        received = []
        hid = hooks.register_callback("on_x", lambda p: received.append(p))
        hooks.fire("on_x", {})
        assert len(received) == 1
        hooks.unregister(hid)
        hooks.fire("on_x", {})
        assert len(received) == 1

    def test_priority_ordering(self):
        hooks = HookSystem()
        order = []
        hooks.register_callback("on_x", lambda p: order.append("low"), priority=100)
        hooks.register_callback("on_x", lambda p: order.append("high"), priority=1)
        hooks.register_callback("on_x", lambda p: order.append("mid"), priority=50)
        hooks.fire("on_x", {})
        assert order == ["high", "mid", "low"]

    def test_webhook_fire_with_retries(self):
        hooks = HookSystem()
        with patch("general_ludd.events.hooks.httpx.post") as mock_post:
            mock_post.side_effect = [Exception("fail"), MagicMock(status_code=200)]
            hooks.register_webhook("on_x", "https://example.com", retry_count=3)
            hooks.fire("on_x", {"test": True})
            assert mock_post.call_count == 2

    def test_webhook_sends_correct_body(self):
        hooks = HookSystem()
        with patch("general_ludd.events.hooks.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            hooks.register_webhook("on_event", "https://example.com")
            hooks.fire("on_event", {"model": "gpt-5"})
            body = mock_post.call_args[1]["json"]
            assert body["event"] == "on_event"
            assert body["payload"]["model"] == "gpt-5"

    def test_callback_error_does_not_block_others(self):
        hooks = HookSystem()
        ok = []
        hooks.register_callback("on_x", lambda p: 1 / 0)
        hooks.register_callback("on_x", lambda p: ok.append(1))
        hooks.fire("on_x", {})
        assert len(ok) == 1

    def test_list_hooks_returns_all(self):
        hooks = HookSystem()
        hooks.register_callback("on_a", lambda p: None)
        hooks.register_callback("on_b", lambda p: None)
        hooks.register_webhook("on_c", "https://example.com")
        all_hooks = hooks.list_hooks()
        assert len(all_hooks) == 3
        events = [h.event_name for h in all_hooks]
        assert "on_a" in events
        assert "on_b" in events
        assert "on_c" in events

    def test_unregister_webhook(self):
        hooks = HookSystem()
        hid = hooks.register_webhook("on_x", "https://example.com")
        assert len(hooks.list_hooks()) == 1
        hooks.unregister(hid)
        assert len(hooks.list_hooks()) == 0

    def test_fire_returns_count_of_successful_hooks(self):
        hooks = HookSystem()
        hooks.register_callback("on_x", lambda p: None)
        hooks.register_callback("on_x", lambda p: None)
        count = hooks.fire("on_x", {})
        assert count == 2

    def test_callback_hook_id_format(self):
        hooks = HookSystem()
        hid = hooks.register_callback("on_x", lambda p: None)
        assert hid.startswith("hook-cb-")

    def test_webhook_hook_id_format(self):
        hooks = HookSystem()
        hid = hooks.register_webhook("on_x", "https://example.com")
        assert hid.startswith("hook-wh-")

    def test_webhook_retries_all_fail_raises(self):
        hooks = HookSystem()
        with patch("general_ludd.events.hooks.httpx.post") as mock_post:
            mock_post.side_effect = Exception("fail")
            hooks.register_webhook("on_x", "https://example.com", retry_count=3)
            count = hooks.fire("on_x", {"test": True})
            assert count == 0
            assert mock_post.call_count == 3

    def test_unregister_nonexistent_is_noop(self):
        hooks = HookSystem()
        hooks.unregister("nonexistent-id")
        assert hooks.list_hooks() == []

    def test_webhook_sends_custom_headers(self):
        hooks = HookSystem()
        with patch("general_ludd.events.hooks.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            hooks.register_webhook(
                "on_x",
                "https://example.com",
                headers={"X-Custom": "val", "Authorization": "Bearer tok"},
            )
            hooks.fire("on_x", {})
            call_headers = mock_post.call_args[1]["headers"]
            assert call_headers["X-Custom"] == "val"
            assert call_headers["Authorization"] == "Bearer tok"

    def test_webhook_uses_configured_timeout(self):
        hooks = HookSystem()
        with patch("general_ludd.events.hooks.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            hooks.register_webhook("on_x", "https://example.com", timeout_seconds=30)
            hooks.fire("on_x", {})
            assert mock_post.call_args[1]["timeout"] == 30

    def test_hook_registration_dataclass_fields(self):
        reg = HookRegistration(
            hook_id="test-id",
            event_name="on_test",
            hook_type="callback",
            callback=lambda p: None,
            priority=10,
        )
        assert reg.hook_id == "test-id"
        assert reg.event_name == "on_test"
        assert reg.hook_type == "callback"
        assert reg.callback is not None
        assert reg.webhook_config is None
        assert reg.priority == 10

    def test_webhook_config_dataclass_defaults(self):
        cfg = WebhookConfig(url="https://example.com")
        assert cfg.headers == {}
        assert cfg.retry_count == 1
        assert cfg.timeout_seconds == 10

    def test_multiple_callbacks_same_event(self):
        hooks = HookSystem()
        results = []
        hooks.register_callback("on_x", lambda p: results.append("a"))
        hooks.register_callback("on_x", lambda p: results.append("b"))
        hooks.register_callback("on_x", lambda p: results.append("c"))
        hooks.fire("on_x", {})
        assert results == ["a", "b", "c"]


class TestEventTypesUnit:

    def test_model_added_event(self):
        e = ModelAddedEvent(model_id="gpt-5", profile={"provider": "openai"})
        assert e.type == EventType.MODEL_ADDED
        assert e.payload["model_id"] == "gpt-5"
        assert e.payload["profile"]["provider"] == "openai"

    def test_model_removed_event(self):
        e = ModelRemovedEvent(model_id="old-model")
        assert e.type == EventType.MODEL_REMOVED
        assert e.payload["model_id"] == "old-model"

    def test_config_reloaded_event(self):
        e = ConfigReloadedEvent(scope="all")
        assert e.type == EventType.CONFIG_RELOADED
        assert e.payload["scope"] == "all"

    def test_template_updated_event(self):
        e = TemplateUpdatedEvent(templates=["a.j2", "b.j2"])
        assert e.type == EventType.TEMPLATE_UPDATED
        assert e.payload["templates"] == ["a.j2", "b.j2"]

    def test_playbook_registered_event(self):
        e = PlaybookRegisteredEvent(playbook="deploy.yml")
        assert e.type == EventType.PLAYBOOK_REGISTERED
        assert e.payload["playbook"] == "deploy.yml"

    def test_playbook_removed_event(self):
        e = PlaybookRemovedEvent(playbook="old.yml")
        assert e.type == EventType.PLAYBOOK_REMOVED
        assert e.payload["playbook"] == "old.yml"

    def test_skill_updated_event(self):
        e = SkillUpdatedEvent(skill="codify")
        assert e.type == EventType.SKILL_UPDATED
        assert e.payload["skill"] == "codify"

    def test_reload_requested_event(self):
        e = ReloadRequestedEvent(scope="models")
        assert e.type == EventType.RELOAD_REQUESTED
        assert e.payload["scope"] == "models"

    def test_reload_completed_event(self):
        e = ReloadCompletedEvent(scope="all")
        assert e.type == EventType.RELOAD_COMPLETED
        assert e.payload["scope"] == "all"

    def test_reload_failed_event(self):
        e = ReloadFailedEvent(scope="templates", error="file not found")
        assert e.type == EventType.RELOAD_FAILED
        assert e.payload["scope"] == "templates"
        assert e.payload["error"] == "file not found"

    def test_worker_ping_event(self):
        e = WorkerPingEvent()
        assert e.type == EventType.WORKER_PING
        assert e.payload == {}

    def test_worker_pong_event(self):
        e = WorkerPongEvent(worker_id="w1")
        assert e.type == EventType.WORKER_PONG
        assert e.payload["worker_id"] == "w1"

    def test_hook_triggered_event(self):
        e = HookTriggeredEvent(event_name="on_model_added")
        assert e.type == EventType.HOOK_TRIGGERED
        assert e.payload["event_name"] == "on_model_added"

    def test_custom_event(self):
        e = CustomEvent(name="my_event", payload={"key": "val"})
        assert e.type == EventType.CUSTOM
        assert e.payload["name"] == "my_event"
        assert e.payload["key"] == "val"

    def test_custom_event_no_payload(self):
        e = CustomEvent(name="bare")
        assert e.type == EventType.CUSTOM
        assert e.payload["name"] == "bare"
        assert len(e.payload) == 1

    def test_event_metadata(self):
        e = Event(
            type=EventType.MODEL_ADDED,
            payload={},
            source="test",
            correlation_id="abc",
        )
        assert e.source == "test"
        assert e.correlation_id == "abc"
        assert e.event_id
        assert e.timestamp > 0

    def test_event_type_values(self):
        assert EventType.MODEL_ADDED == "model_added"
        assert EventType.MODEL_REMOVED == "model_removed"
        assert EventType.CONFIG_RELOADED == "config_reloaded"
        assert EventType.TEMPLATE_UPDATED == "template_updated"
        assert EventType.PLAYBOOK_REGISTERED == "playbook_registered"
        assert EventType.PLAYBOOK_REMOVED == "playbook_removed"
        assert EventType.SKILL_UPDATED == "skill_updated"
        assert EventType.RELOAD_REQUESTED == "reload_requested"
        assert EventType.RELOAD_COMPLETED == "reload_completed"
        assert EventType.RELOAD_FAILED == "reload_failed"
        assert EventType.WORKER_PING == "worker_ping"
        assert EventType.WORKER_PONG == "worker_pong"
        assert EventType.HOOK_TRIGGERED == "hook_triggered"
        assert EventType.CUSTOM == "custom"

    def test_event_subclasses_inherit_metadata(self):
        e = ModelAddedEvent(
            model_id="m1",
            profile={},
            source="unit-test",
            correlation_id="corr-123",
        )
        assert e.source == "unit-test"
        assert e.correlation_id == "corr-123"
        assert e.event_id
        assert e.timestamp > 0

    def test_event_type_is_str_enum(self):
        for member in EventType:
            assert isinstance(member, str)

    def test_model_added_event_with_complex_profile(self):
        profile = {
            "provider": "openai",
            "params": {"temperature": 0.7},
            "capabilities": ["chat", "completion"],
        }
        e = ModelAddedEvent(model_id="complex", profile=profile)
        assert e.payload["profile"]["params"]["temperature"] == 0.7
        assert "completion" in e.payload["profile"]["capabilities"]

    def test_reload_failed_event_error_message(self):
        e = ReloadFailedEvent(scope="all", error="connection timeout after 30s")
        assert "timeout" in e.payload["error"]

    def test_event_payload_default_is_empty_dict(self):
        e = Event(type=EventType.CUSTOM)
        assert e.payload == {}

    def test_event_source_default_is_none(self):
        e = Event(type=EventType.CUSTOM)
        assert e.source is None

    def test_event_correlation_id_default_is_none(self):
        e = Event(type=EventType.CUSTOM)
        assert e.correlation_id is None
