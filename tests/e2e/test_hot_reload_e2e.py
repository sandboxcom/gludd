"""
Verbose E2E tests for hot-reload system.

Covers:
  - EventBus publish/subscribe/unsubscribe
  - HookSystem callback + webhook registration and firing
  - HotReloader: config/template/playbook/skill reload mid-run
  - WorkerBroadcaster: multi-worker notification
  - Daemon admin endpoints for reload
  - ModelGateway/ProviderRegistry dynamic add/remove
  - PromptRegistry/SkillLoader refresh
  - EventLoop responding to config updates
  - Full integration: add model → hook fires → workers notified → config refreshed
"""

import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from general_ludd.ansible.runner import AnsibleRunnerAdapter
from general_ludd.events.bus import Event, EventBus
from general_ludd.events.hooks import HookSystem
from general_ludd.events.types import (
    CustomEvent,
    EventType,
    ModelAddedEvent,
)
from general_ludd.models.gateway import ModelGateway
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.models.router import ModelRouter
from general_ludd.prompts.registry import PromptRegistry
from general_ludd.reload.hot_reloader import HotReloader, ReloadScope
from general_ludd.reload.worker_broadcast import WorkerBroadcaster, WorkerInfo
from general_ludd.skills.registry import SkillRegistry

# ─── EventBus Tests ─────────────────────────────────────────────────────────


class TestEventBusE2E:
    """Verbose E2E tests for EventBus pub/sub."""

    def test_publish_subscribe_basic(self):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.MODEL_ADDED, lambda e: received.append(e))
        bus.publish(Event(type=EventType.MODEL_ADDED, payload={"model": "gpt-5"}))
        assert len(received) == 1
        assert received[0].payload["model"] == "gpt-5"

    def test_subscribe_multiple_subscribers(self):
        bus = EventBus()
        r1, r2 = [], []
        bus.subscribe(EventType.CONFIG_RELOADED, lambda e: r1.append(e))
        bus.subscribe(EventType.CONFIG_RELOADED, lambda e: r2.append(e))
        bus.publish(Event(type=EventType.CONFIG_RELOADED, payload={"scope": "full"}))
        assert len(r1) == 1
        assert len(r2) == 1

    def test_unsubscribe(self):
        bus = EventBus()
        received = []
        sub_id = bus.subscribe(EventType.MODEL_ADDED, lambda e: received.append(e))
        bus.publish(Event(type=EventType.MODEL_ADDED, payload={"model": "a"}))
        assert len(received) == 1
        bus.unsubscribe(sub_id)
        bus.publish(Event(type=EventType.MODEL_ADDED, payload={"model": "b"}))
        assert len(received) == 1

    def test_no_subscribers_does_not_error(self):
        bus = EventBus()
        bus.publish(Event(type=EventType.MODEL_ADDED, payload={"model": "x"}))

    def test_wildcard_subscriber_receives_all_events(self):
        bus = EventBus()
        received = []
        bus.subscribe("*", lambda e: received.append(e))
        bus.publish(Event(type=EventType.MODEL_ADDED, payload={"m": "a"}))
        bus.publish(Event(type=EventType.CONFIG_RELOADED, payload={"scope": "full"}))
        bus.publish(Event(type=EventType.TEMPLATE_UPDATED, payload={"t": "b"}))
        assert len(received) == 3

    def test_event_preserves_metadata(self):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.MODEL_ADDED, lambda e: received.append(e))
        event = Event(
            type=EventType.MODEL_ADDED,
            payload={"model": "claude-4"},
            source="test",
            correlation_id="corr-123",
        )
        bus.publish(event)
        assert received[0].source == "test"
        assert received[0].correlation_id == "corr-123"
        assert received[0].timestamp > 0

    def test_async_subscriber(self):
        bus = EventBus()
        received = []

        async def on_event(e):
            received.append(e)

        bus.subscribe(EventType.MODEL_ADDED, on_event)
        bus.publish(Event(type=EventType.MODEL_ADDED, payload={"model": "a"}))
        assert len(received) == 1

    def test_subscriber_error_does_not_block_others(self):
        bus = EventBus()
        received = []

        def bad_handler(e):
            raise RuntimeError("boom")

        bus.subscribe(EventType.MODEL_ADDED, bad_handler)
        bus.subscribe(EventType.MODEL_ADDED, lambda e: received.append(e))
        bus.publish(Event(type=EventType.MODEL_ADDED, payload={"model": "a"}))
        assert len(received) == 1

    def test_event_history(self):
        bus = EventBus(history_size=10)
        bus.publish(Event(type=EventType.MODEL_ADDED, payload={"m": "a"}))
        bus.publish(Event(type=EventType.MODEL_ADDED, payload={"m": "b"}))
        history = bus.get_history()
        assert len(history) == 2
        assert history[0].payload["m"] == "a"
        assert history[1].payload["m"] == "b"

    def test_event_history_bounded(self):
        bus = EventBus(history_size=2)
        for i in range(5):
            bus.publish(Event(type=EventType.MODEL_ADDED, payload={"i": i}))
        history = bus.get_history()
        assert len(history) == 2
        assert history[0].payload["i"] == 3
        assert history[1].payload["i"] == 4

    def test_clear_all_subscribers(self):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.MODEL_ADDED, lambda e: received.append(e))
        bus.clear()
        bus.publish(Event(type=EventType.MODEL_ADDED, payload={"m": "a"}))
        assert len(received) == 0

    def test_typed_event_construction(self):
        event = ModelAddedEvent(model_id="gpt-5", profile={"provider": "openai"})
        assert event.type == EventType.MODEL_ADDED
        assert event.payload["model_id"] == "gpt-5"
        assert event.payload["profile"]["provider"] == "openai"

    def test_custom_event_type(self):
        event = CustomEvent(name="my_custom_event", payload={"key": "val"})
        assert event.type == EventType.CUSTOM
        assert event.payload["name"] == "my_custom_event"

    def test_publish_returns_subscriber_count(self):
        bus = EventBus()
        bus.subscribe(EventType.MODEL_ADDED, lambda e: None)
        bus.subscribe(EventType.MODEL_ADDED, lambda e: None)
        count = bus.publish(Event(type=EventType.MODEL_ADDED, payload={}))
        assert count == 2


# ─── HookSystem Tests ────────────────────────────────────────────────────────


class TestHookSystemE2E:
    """Verbose E2E tests for HookSystem (callbacks + webhooks)."""

    def test_register_and_fire_callback(self):
        hooks = HookSystem()
        received = []
        hooks.register_callback("on_model_added", lambda e: received.append(e))
        hooks.fire("on_model_added", {"model": "gpt-5"})
        assert len(received) == 1
        assert received[0]["model"] == "gpt-5"

    def test_register_multiple_callbacks_same_event(self):
        hooks = HookSystem()
        r1, r2 = [], []
        hooks.register_callback("on_reload", lambda e: r1.append(e))
        hooks.register_callback("on_reload", lambda e: r2.append(e))
        hooks.fire("on_reload", {"scope": "full"})
        assert len(r1) == 1
        assert len(r2) == 1

    def test_unregister_callback(self):
        hooks = HookSystem()
        received = []
        reg_id = hooks.register_callback("on_model_added", lambda e: received.append(e))
        hooks.fire("on_model_added", {"m": "a"})
        assert len(received) == 1
        hooks.unregister(reg_id)
        hooks.fire("on_model_added", {"m": "b"})
        assert len(received) == 1

    def test_callback_error_isolation(self):
        hooks = HookSystem()
        received = []

        def bad(e):
            raise RuntimeError("hook error")

        hooks.register_callback("on_x", bad)
        hooks.register_callback("on_x", lambda e: received.append(e))
        hooks.fire("on_x", {})
        assert len(received) == 1

    def test_webhook_registration_and_fire(self):
        hooks = HookSystem()
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            hooks.register_webhook("on_model_added", "https://example.com/hook")
            hooks.fire("on_model_added", {"model": "gpt-5"})
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert call_args[0][0] == "https://example.com/hook"
            body = call_args[1]["json"]
            assert body["event"] == "on_model_added"
            assert body["payload"]["model"] == "gpt-5"

    def test_webhook_failure_does_not_block(self):
        hooks = HookSystem()
        received = []
        with patch("httpx.post") as mock_post:
            mock_post.side_effect = Exception("connection refused")
            hooks.register_webhook("on_x", "https://bad.example.com")
            hooks.register_callback("on_x", lambda e: received.append(e))
            hooks.fire("on_x", {})
            assert len(received) == 1

    def test_webhook_custom_headers(self):
        hooks = HookSystem()
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            hooks.register_webhook(
                "on_reload",
                "https://example.com/hook",
                headers={"Authorization": "Bearer secret"},
            )
            hooks.fire("on_reload", {"scope": "full"})
            call_args = mock_post.call_args
            assert "Authorization" in call_args[1]["headers"]

    def test_webhook_retry_on_failure(self):
        hooks = HookSystem()
        with patch("httpx.post") as mock_post:
            mock_post.side_effect = [Exception("fail"), MagicMock(status_code=200)]
            hooks.register_webhook("on_x", "https://example.com/hook", retry_count=2)
            hooks.fire("on_x", {})
            assert mock_post.call_count == 2

    def test_list_registered_hooks(self):
        hooks = HookSystem()
        hooks.register_callback("on_a", lambda e: None)
        hooks.register_callback("on_b", lambda e: None)
        hooks.register_webhook("on_c", "https://example.com")
        listing = hooks.list_hooks()
        assert len(listing) == 3
        event_names = [h.event_name for h in listing]
        assert "on_a" in event_names
        assert "on_b" in event_names
        assert "on_c" in event_names

    def test_hook_priority_ordering(self):
        hooks = HookSystem()
        order = []
        hooks.register_callback("on_x", lambda e: order.append("low"), priority=10)
        hooks.register_callback("on_x", lambda e: order.append("high"), priority=1)
        hooks.register_callback("on_x", lambda e: order.append("mid"), priority=5)
        hooks.fire("on_x", {})
        assert order == ["high", "mid", "low"]

    def test_fire_returns_hook_count(self):
        hooks = HookSystem()
        hooks.register_callback("on_x", lambda e: None)
        hooks.register_callback("on_x", lambda e: None)
        count = hooks.fire("on_x", {})
        assert count == 2

    def test_webhook_timeout(self):
        hooks = HookSystem()
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            hooks.register_webhook("on_x", "https://example.com", timeout_seconds=5)
            hooks.fire("on_x", {})
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs.get("timeout") == 5

    def test_fire_nonexistent_event_is_noop(self):
        hooks = HookSystem()
        count = hooks.fire("nonexistent", {})
        assert count == 0


# ─── HotReloader Tests ──────────────────────────────────────────────────────


class TestHotReloaderE2E:
    """Verbose E2E tests for HotReloader — actual config/template/playbook reload."""

    def test_reload_model_config_from_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            routing_file = config_dir / "model_routing.yml"
            routing_file.write_text(
                "default_profile: openai-gpt4\n"
                "role_routing:\n"
                "  coder: openai-gpt4\n"
                "  reviewer: openai-gpt4\n"
            )
            bus = EventBus()
            hooks = HookSystem()
            events_received = []
            bus.subscribe(EventType.CONFIG_RELOADED, lambda e: events_received.append(e))
            reloader = HotReloader(
                config_dir=str(config_dir),
                event_bus=bus,
                hook_system=hooks,
            )
            result = reloader.reload(ReloadScope.MODELS)
            assert result.success
            assert len(events_received) == 1
            assert events_received[0].payload["scope"] == "models"

    def test_reload_templates_from_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = Path(tmpdir) / "templates" / "prompts"
            templates_dir.mkdir(parents=True)
            (templates_dir / "test.md.j2").write_text("Hello {{ name }}!")

            bus = EventBus()
            hooks = HookSystem()
            reloader = HotReloader(
                config_dir=str(Path(tmpdir) / "config"),
                event_bus=bus,
                hook_system=hooks,
                templates_dir=str(templates_dir),
            )
            result = reloader.reload(ReloadScope.TEMPLATES)
            assert result.success
            assert result.details["templates_loaded"] >= 1

    def test_reload_playbooks_from_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            playbooks_dir = Path(tmpdir) / "playbooks"
            playbooks_dir.mkdir()
            (playbooks_dir / "test_playbook.yml").write_text(
                "---\n- hosts: localhost\n  tasks:\n    - debug: msg=hello\n"
            )
            bus = EventBus()
            hooks = HookSystem()
            reloader = HotReloader(
                config_dir=str(Path(tmpdir) / "config"),
                event_bus=bus,
                hook_system=hooks,
                playbooks_dir=str(playbooks_dir),
            )
            result = reloader.reload(ReloadScope.PLAYBOOKS)
            assert result.success
            assert "test_playbook.yml" in result.details["playbooks"]

    def test_reload_skills_from_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / "skills"
            skills_dir.mkdir()
            (skills_dir / "codify.md").write_text(
                "---\nname: codify\ndescription: Write code\n---\nBody here\n"
            )
            bus = EventBus()
            hooks = HookSystem()
            reloader = HotReloader(
                config_dir=str(Path(tmpdir) / "config"),
                event_bus=bus,
                hook_system=hooks,
                skills_dirs=[str(skills_dir)],
            )
            result = reloader.reload(ReloadScope.SKILLS)
            assert result.success
            assert "codify" in result.details["skills"]

    def test_reload_full_scope(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            (config_dir / "model_routing.yml").write_text("default_profile: test\n")
            templates_dir = Path(tmpdir) / "templates" / "prompts"
            templates_dir.mkdir(parents=True)
            (templates_dir / "t.md.j2").write_text("hi")
            playbooks_dir = Path(tmpdir) / "playbooks"
            playbooks_dir.mkdir()
            (playbooks_dir / "p.yml").write_text("---\n- hosts: localhost\n  tasks: []\n")

            bus = EventBus()
            hooks = HookSystem()
            events = []
            bus.subscribe(EventType.CONFIG_RELOADED, lambda e: events.append(e))

            reloader = HotReloader(
                config_dir=str(config_dir),
                event_bus=bus,
                hook_system=hooks,
                templates_dir=str(templates_dir),
                playbooks_dir=str(playbooks_dir),
            )
            result = reloader.reload(ReloadScope.ALL)
            assert result.success
            assert len(events) >= 1
            assert result.details["scope"] == "all"

    def test_reload_failure_records_error(self):
        bus = EventBus()
        hooks = HookSystem()
        events = []
        bus.subscribe(EventType.RELOAD_FAILED, lambda e: events.append(e))
        reloader = HotReloader(
            config_dir="/nonexistent/path",
            event_bus=bus,
            hook_system=hooks,
        )
        result = reloader.reload(ReloadScope.MODELS)
        assert not result.success or result.details.get("models_reloaded") is False

    def test_reload_triggers_hooks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            hooks = HookSystem()
            hook_fired = []
            hooks.register_callback("on_config_reloaded", lambda e: hook_fired.append(e))
            reloader = HotReloader(
                config_dir=str(config_dir),
                event_bus=EventBus(),
                hook_system=hooks,
            )
            reloader.reload(ReloadScope.CONFIG)
            assert len(hook_fired) == 1

    def test_reload_rollback_on_failure(self):
        bus = EventBus()
        hooks = HookSystem()
        reloader = HotReloader(
            config_dir="/nonexistent",
            event_bus=bus,
            hook_system=hooks,
        )
        reloader.reload(ReloadScope.MODELS)
        state = reloader.get_last_state()
        assert state is not None
        assert state.previous_config is not None


# ─── WorkerBroadcaster Tests ────────────────────────────────────────────────


class TestWorkerBroadcasterE2E:
    """Verbose E2E tests for WorkerBroadcaster — multi-worker notification."""

    def test_register_worker(self):
        broadcaster = WorkerBroadcaster()
        info = WorkerInfo(worker_id="w1", address="http://localhost:8001")
        broadcaster.register(info)
        workers = broadcaster.list_workers()
        assert len(workers) == 1
        assert workers[0].worker_id == "w1"

    def test_unregister_worker(self):
        broadcaster = WorkerBroadcaster()
        info = WorkerInfo(worker_id="w1", address="http://localhost:8001")
        broadcaster.register(info)
        broadcaster.unregister("w1")
        assert len(broadcaster.list_workers()) == 0

    def test_worker_heartbeat_updates_last_seen(self):
        broadcaster = WorkerBroadcaster()
        info = WorkerInfo(worker_id="w1", address="http://localhost:8001")
        broadcaster.register(info)
        old_seen = broadcaster.list_workers()[0].last_seen
        time.sleep(0.01)
        broadcaster.heartbeat("w1")
        new_seen = broadcaster.list_workers()[0].last_seen
        assert new_seen > old_seen

    def test_broadcast_reload_to_all_workers(self):
        broadcaster = WorkerBroadcaster()
        broadcaster.register(WorkerInfo(worker_id="w1", address="http://localhost:8001"))
        broadcaster.register(WorkerInfo(worker_id="w2", address="http://localhost:8002"))

        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            results = broadcaster.broadcast_reload(ReloadScope.ALL)
            assert mock_post.call_count == 2
            assert all(r.success for r in results)

    def test_broadcast_handles_worker_failure(self):
        broadcaster = WorkerBroadcaster()
        broadcaster.register(WorkerInfo(worker_id="w1", address="http://localhost:8001"))
        broadcaster.register(WorkerInfo(worker_id="w2", address="http://localhost:8002"))

        with patch("httpx.post") as mock_post:
            mock_post.side_effect = [
                MagicMock(status_code=200),
                Exception("connection refused"),
            ]
            results = broadcaster.broadcast_reload(ReloadScope.MODELS)
            assert results[0].success
            assert not results[1].success

    def test_stale_worker_cleanup(self):
        broadcaster = WorkerBroadcaster(stale_threshold_seconds=0.01)
        info = WorkerInfo(worker_id="w1", address="http://localhost:8001")
        broadcaster.register(info)
        time.sleep(0.05)
        broadcaster.cleanup_stale()
        assert len(broadcaster.list_workers()) == 0

    def test_worker_heartbeat_keeps_alive(self):
        broadcaster = WorkerBroadcaster(stale_threshold_seconds=0.05)
        broadcaster.register(WorkerInfo(worker_id="w1", address="http://localhost:8001"))
        for _ in range(5):
            broadcaster.heartbeat("w1")
            time.sleep(0.01)
        broadcaster.cleanup_stale()
        assert len(broadcaster.list_workers()) == 1

    def test_broadcast_model_update(self):
        broadcaster = WorkerBroadcaster()
        broadcaster.register(WorkerInfo(worker_id="w1", address="http://localhost:8001"))

        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            results = broadcaster.broadcast_model_update(
                action="add",
                model_id="gpt-5",
                profile={"provider": "openai"},
            )
            assert len(results) == 1
            assert results[0].success
            call_body = mock_post.call_args[1]["json"]
            assert call_body["action"] == "add"
            assert call_body["model_id"] == "gpt-5"

    def test_ping_all_workers(self):
        broadcaster = WorkerBroadcaster()
        broadcaster.register(WorkerInfo(worker_id="w1", address="http://localhost:8001"))
        broadcaster.register(WorkerInfo(worker_id="w2", address="http://localhost:8002"))

        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200, json=lambda: {"status": "ok"})
            results = broadcaster.ping_all()
            assert mock_get.call_count == 2
            assert all(r for r in results.values())


# ─── ModelGateway Dynamic Tests ─────────────────────────────────────────────


class TestModelGatewayDynamicE2E:
    """Verbose E2E tests for adding/removing models at runtime."""

    def test_add_model_profile_to_gateway(self):
        registry = ProviderRegistry()
        router = ModelRouter()
        gateway = ModelGateway(profiles={}, provider_registry=registry, router=router)

        gateway.add_profile("gpt-5", provider="openai", model="gpt-5", api_key_env="OPENAI_KEY")
        profile_ids = [p.model_profile_id for p in gateway.list_profiles()]
        assert "gpt-5" in profile_ids

    def test_remove_model_profile_from_gateway(self):
        registry = ProviderRegistry()
        router = ModelRouter()
        gateway = ModelGateway(profiles={}, provider_registry=registry, router=router)

        gateway.add_profile("gpt-5", provider="openai", model="gpt-5", api_key_env="OPENAI_KEY")
        gateway.remove_profile("gpt-5")
        profile_ids = [p.model_profile_id for p in gateway.list_profiles()]
        assert "gpt-5" not in profile_ids

    def test_add_model_triggers_event(self):
        bus = EventBus()
        events = []
        bus.subscribe(EventType.MODEL_ADDED, lambda e: events.append(e))
        registry = ProviderRegistry()
        router = ModelRouter()
        gateway = ModelGateway(
            profiles={},
            provider_registry=registry,
            router=router,
            event_bus=bus,
        )
        gateway.add_profile("gpt-5", provider="openai", model="gpt-5", api_key_env="OPENAI_KEY")
        assert len(events) == 1
        assert events[0].payload["model_id"] == "gpt-5"

    def test_remove_model_triggers_event(self):
        bus = EventBus()
        events = []
        bus.subscribe(EventType.MODEL_REMOVED, lambda e: events.append(e))
        registry = ProviderRegistry()
        router = ModelRouter()
        gateway = ModelGateway(
            profiles={},
            provider_registry=registry,
            router=router,
            event_bus=bus,
        )
        gateway.add_profile("gpt-5", provider="openai", model="gpt-5", api_key_env="OPENAI_KEY")
        gateway.remove_profile("gpt-5")
        assert len(events) == 1
        assert events[0].payload["model_id"] == "gpt-5"

    def test_update_routing_dynamically(self):
        router = ModelRouter()
        router.set_role_routing("coder", "gpt-5")
        assert router.resolve_role("coder") == "gpt-5"

        router.set_role_routing("coder", "claude-4")
        assert router.resolve_role("coder") == "claude-4"

    def test_remove_nonexistent_profile_is_noop(self):
        registry = ProviderRegistry()
        router = ModelRouter()
        gateway = ModelGateway(profiles={}, provider_registry=registry, router=router)
        gateway.remove_profile("nonexistent")


# ─── PromptRegistry Dynamic Tests ───────────────────────────────────────────


class TestPromptRegistryDynamicE2E:
    """Verbose E2E tests for template refresh."""

    def test_refresh_from_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = Path(tmpdir) / "prompts"
            templates_dir.mkdir()
            (templates_dir / "hello.md.j2").write_text("Hello {{ name }}!")

            registry = PromptRegistry(template_dir=str(templates_dir))
            result = registry.refresh()
            assert "hello.md.j2" in result["templates"]
            rendered = registry.render("hello.md.j2", name="World")
            assert rendered == "Hello World!"

    def test_refresh_detects_new_templates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = Path(tmpdir) / "prompts"
            templates_dir.mkdir()

            registry = PromptRegistry(template_dir=str(templates_dir))
            result1 = registry.refresh()
            assert len(result1["templates"]) == 0

            (templates_dir / "new.md.j2").write_text("New: {{ x }}")
            result2 = registry.refresh()
            assert "new.md.j2" in result2["templates"]

    def test_refresh_detects_removed_templates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = Path(tmpdir) / "prompts"
            templates_dir.mkdir()
            t_file = templates_dir / "temp.md.j2"
            t_file.write_text("Temp")

            registry = PromptRegistry(template_dir=str(templates_dir))
            registry.refresh()
            assert "temp.md.j2" in registry.list_templates()

            t_file.unlink()
            registry.refresh()
            assert "temp.md.j2" not in registry.list_templates()

    def test_refresh_preserves_in_memory_templates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = PromptRegistry(template_dir=str(tmpdir))
            registry.register("custom", "Custom: {{ x }}")
            registry.refresh()
            rendered = registry.render("custom", x="val")
            assert rendered == "Custom: val"

    def test_refresh_triggers_event(self):
        bus = EventBus()
        events = []
        bus.subscribe(EventType.TEMPLATE_UPDATED, lambda e: events.append(e))
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = PromptRegistry(
                template_dir=str(tmpdir),
                event_bus=bus,
            )
            registry.refresh()
            assert len(events) >= 1


# ─── AnsibleRunner Dynamic Tests ────────────────────────────────────────────


class TestAnsibleRunnerDynamicE2E:
    """Verbose E2E tests for playbook refresh."""

    def test_refresh_playbooks_from_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            playbooks_dir = Path(tmpdir) / "playbooks"
            playbooks_dir.mkdir()
            (playbooks_dir / "deploy.yml").write_text(
                "---\n- hosts: localhost\n  tasks:\n    - debug: msg=deploy\n"
            )
            runner = AnsibleRunnerAdapter(playbooks_dir=str(playbooks_dir))
            result = runner.refresh_playbooks()
            assert "deploy.yml" in result["playbooks"]

    def test_refresh_detects_new_playbooks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            playbooks_dir = Path(tmpdir) / "playbooks"
            playbooks_dir.mkdir()
            runner = AnsibleRunnerAdapter(playbooks_dir=str(playbooks_dir))

            result1 = runner.refresh_playbooks()
            initial_count = len(result1["playbooks"])

            (playbooks_dir / "new.yml").write_text(
                "---\n- hosts: localhost\n  tasks: []\n"
            )
            result2 = runner.refresh_playbooks()
            assert len(result2["playbooks"]) > initial_count
            assert "new.yml" in result2["playbooks"]

    def test_register_playbook_manually(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = AnsibleRunnerAdapter(playbooks_dir=str(tmpdir))
            runner.register_playbook("custom.yml", "/path/to/custom.yml")
            assert "custom.yml" in runner.list_playbooks()

    def test_unregister_playbook(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = AnsibleRunnerAdapter(playbooks_dir=str(tmpdir))
            runner.register_playbook("custom.yml", "/path/to/custom.yml")
            runner.unregister_playbook("custom.yml")
            assert "custom.yml" not in runner.list_playbooks()

    def test_playbook_refresh_triggers_event(self):
        bus = EventBus()
        events = []
        bus.subscribe(EventType.PLAYBOOK_REGISTERED, lambda e: events.append(e))
        with tempfile.TemporaryDirectory() as tmpdir:
            playbooks_dir = Path(tmpdir) / "playbooks"
            playbooks_dir.mkdir()
            (playbooks_dir / "test.yml").write_text(
                "---\n- hosts: localhost\n  tasks: []\n"
            )
            runner = AnsibleRunnerAdapter(
                playbooks_dir=str(playbooks_dir),
                event_bus=bus,
            )
            runner.refresh_playbooks()
            assert len(events) >= 1


# ─── SkillRegistry Dynamic Tests ────────────────────────────────────────────


class TestSkillRegistryDynamicE2E:
    """Verbose E2E tests for skill refresh."""

    def test_refresh_skills_from_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / "skills"
            skills_dir.mkdir()
            (skills_dir / "test_skill.md").write_text(
                "---\nname: test_skill\ndescription: A test\n---\nBody\n"
            )
            registry = SkillRegistry()
            result = registry.refresh(search_paths=[str(skills_dir)])
            assert "test_skill" in result["skills"]

    def test_refresh_detects_new_skills(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / "skills"
            skills_dir.mkdir()
            registry = SkillRegistry()
            result1 = registry.refresh(search_paths=[str(skills_dir)])
            assert len(result1["skills"]) == 0

            (skills_dir / "new_skill.md").write_text(
                "---\nname: new_skill\ndescription: New\n---\nBody\n"
            )
            result2 = registry.refresh(search_paths=[str(skills_dir)])
            assert "new_skill" in result2["skills"]


# ─── Daemon Admin Endpoint Tests ────────────────────────────────────────────


class TestDaemonReloadEndpointsE2E:
    """Verbose E2E tests for daemon admin reload endpoints."""

    def test_post_admin_reload_models(self):
        from starlette.testclient import TestClient

        from general_ludd.daemon import create_daemon_app

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            app = create_daemon_app(config_dir=str(config_dir))
            client = TestClient(app)
            resp = client.post("/admin/reload", json={"scope": "models"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"]

    def test_post_admin_reload_templates(self):
        from starlette.testclient import TestClient

        from general_ludd.daemon import create_daemon_app

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            templates_dir = Path(tmpdir) / "templates" / "prompts"
            templates_dir.mkdir(parents=True)
            app = create_daemon_app(
                config_dir=str(config_dir),
                templates_dir=str(templates_dir),
            )
            client = TestClient(app)
            resp = client.post("/admin/reload", json={"scope": "templates"})
            assert resp.status_code == 200
            assert resp.json()["success"]

    def test_post_admin_reload_full(self):
        from starlette.testclient import TestClient

        from general_ludd.daemon import create_daemon_app

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            app = create_daemon_app(config_dir=str(config_dir))
            client = TestClient(app)
            resp = client.post("/admin/reload", json={"scope": "all"})
            assert resp.status_code == 200

    def test_get_admin_reload_status(self):
        from starlette.testclient import TestClient

        from general_ludd.daemon import create_daemon_app

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            app = create_daemon_app(config_dir=str(config_dir))
            client = TestClient(app)
            resp = client.get("/admin/reload/status")
            assert resp.status_code == 200

    def test_post_admin_models_add(self):
        from starlette.testclient import TestClient

        from general_ludd.daemon import create_daemon_app

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            app = create_daemon_app(config_dir=str(config_dir))
            client = TestClient(app)
            resp = client.post(
                "/admin/models",
                json={
                    "model_id": "gpt-5",
                    "provider": "openai",
                    "model": "gpt-5-turbo",
                    "api_key_env": "OPENAI_KEY",
                },
            )
            assert resp.status_code == 200
            assert resp.json()["model_id"] == "gpt-5"

    def test_delete_admin_models(self):
        from starlette.testclient import TestClient

        from general_ludd.daemon import create_daemon_app

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            app = create_daemon_app(config_dir=str(config_dir))
            client = TestClient(app)
            client.post(
                "/admin/models",
                json={
                    "model_id": "gpt-5",
                    "provider": "openai",
                    "model": "gpt-5-turbo",
                    "api_key_env": "OPENAI_KEY",
                },
            )
            resp = client.delete("/admin/models/gpt-5")
            assert resp.status_code == 200

    def test_get_admin_models_list(self):
        from starlette.testclient import TestClient

        from general_ludd.daemon import create_daemon_app

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            app = create_daemon_app(config_dir=str(config_dir))
            client = TestClient(app)
            resp = client.get("/admin/models")
            assert resp.status_code == 200
            assert "profiles" in resp.json()

    def test_post_admin_templates_refresh(self):
        from starlette.testclient import TestClient

        from general_ludd.daemon import create_daemon_app

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            templates_dir = Path(tmpdir) / "templates" / "prompts"
            templates_dir.mkdir(parents=True)
            app = create_daemon_app(
                config_dir=str(config_dir),
                templates_dir=str(templates_dir),
            )
            client = TestClient(app)
            resp = client.post("/admin/templates/refresh")
            assert resp.status_code == 200
            assert resp.json()["success"]

    def test_get_admin_templates_list(self):
        from starlette.testclient import TestClient

        from general_ludd.daemon import create_daemon_app

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            templates_dir = Path(tmpdir) / "templates" / "prompts"
            templates_dir.mkdir(parents=True)
            app = create_daemon_app(
                config_dir=str(config_dir),
                templates_dir=str(templates_dir),
            )
            client = TestClient(app)
            resp = client.get("/admin/templates")
            assert resp.status_code == 200
            assert "templates" in resp.json()

    def test_post_admin_playbooks_refresh(self):
        from starlette.testclient import TestClient

        from general_ludd.daemon import create_daemon_app

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            playbooks_dir = Path(tmpdir) / "playbooks"
            playbooks_dir.mkdir()
            app = create_daemon_app(
                config_dir=str(config_dir),
                playbooks_dir=str(playbooks_dir),
            )
            client = TestClient(app)
            resp = client.post("/admin/playbooks/refresh")
            assert resp.status_code == 200
            assert resp.json()["success"]

    def test_get_admin_playbooks_list(self):
        from starlette.testclient import TestClient

        from general_ludd.daemon import create_daemon_app

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            playbooks_dir = Path(tmpdir) / "playbooks"
            playbooks_dir.mkdir()
            app = create_daemon_app(
                config_dir=str(config_dir),
                playbooks_dir=str(playbooks_dir),
            )
            client = TestClient(app)
            resp = client.get("/admin/playbooks")
            assert resp.status_code == 200
            assert "playbooks" in resp.json()

    def test_get_admin_hooks_list(self):
        from starlette.testclient import TestClient

        from general_ludd.daemon import create_daemon_app

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            app = create_daemon_app(config_dir=str(config_dir))
            client = TestClient(app)
            resp = client.get("/admin/hooks")
            assert resp.status_code == 200
            assert "hooks" in resp.json()

    def test_post_admin_hooks_register_webhook(self):
        from starlette.testclient import TestClient

        from general_ludd.daemon import create_daemon_app

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            app = create_daemon_app(config_dir=str(config_dir))
            client = TestClient(app)
            resp = client.post(
                "/admin/hooks",
                json={
                    "event_name": "on_model_added",
                    "url": "https://example.com/webhook",
                    "headers": {"Authorization": "Bearer token123"},
                },
            )
            assert resp.status_code == 200
            assert "hook_id" in resp.json()

    def test_delete_admin_hooks(self):
        from starlette.testclient import TestClient

        from general_ludd.daemon import create_daemon_app

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            app = create_daemon_app(config_dir=str(config_dir))
            client = TestClient(app)
            reg_resp = client.post(
                "/admin/hooks",
                json={
                    "event_name": "on_reload",
                    "url": "https://example.com/hook",
                },
            )
            hook_id = reg_resp.json()["hook_id"]
            del_resp = client.delete(f"/admin/hooks/{hook_id}")
            assert del_resp.status_code == 200

    def test_post_admin_workers_ping(self):
        from starlette.testclient import TestClient

        from general_ludd.daemon import create_daemon_app

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            app = create_daemon_app(config_dir=str(config_dir))
            client = TestClient(app)
            resp = client.post("/admin/workers/ping")
            assert resp.status_code == 200

    def test_get_admin_workers_list(self):
        from starlette.testclient import TestClient

        from general_ludd.daemon import create_daemon_app

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            app = create_daemon_app(config_dir=str(config_dir))
            client = TestClient(app)
            resp = client.get("/admin/workers")
            assert resp.status_code == 200
            assert "workers" in resp.json()


# ─── Full Integration Test ──────────────────────────────────────────────────


class TestHotReloadFullIntegration:
    """End-to-end: add model → hook fires → workers notified → config refreshed."""

    def test_add_model_fires_hook_and_notifies_workers(self):
        bus = EventBus()
        hooks = HookSystem()
        broadcaster = WorkerBroadcaster()

        broadcaster.register(WorkerInfo(worker_id="w1", address="http://localhost:8001"))

        hook_events = []
        hooks.register_callback("on_model_added", lambda e: hook_events.append(e))

        bus_events = []
        bus.subscribe(EventType.MODEL_ADDED, lambda e: bus_events.append(e))

        registry = ProviderRegistry()
        router = ModelRouter()
        gateway = ModelGateway(
            profiles={},
            provider_registry=registry,
            router=router,
            event_bus=bus,
            hook_system=hooks,
            worker_broadcaster=broadcaster,
        )

        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            gateway.add_profile("gpt-5", provider="openai", model="gpt-5-turbo", api_key_env="KEY")

        assert len(hook_events) == 1
        assert len(bus_events) == 1
        assert hook_events[0]["model_id"] == "gpt-5"
        profile_ids = [p.model_profile_id for p in gateway.list_profiles()]
        assert "gpt-5" in profile_ids

    def test_reload_config_updates_all_registries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            (config_dir / "model_routing.yml").write_text(
                "default_profile: test-model\nrole_routing:\n  coder: test-model\n"
            )
            templates_dir = Path(tmpdir) / "templates" / "prompts"
            templates_dir.mkdir(parents=True)
            (templates_dir / "prompt.md.j2").write_text("Hello {{ name }}!")
            playbooks_dir = Path(tmpdir) / "playbooks"
            playbooks_dir.mkdir()
            (playbooks_dir / "run.yml").write_text("---\n- hosts: localhost\n  tasks: []\n")

            bus = EventBus()
            hooks = HookSystem()
            broadcaster = WorkerBroadcaster()
            broadcaster.register(WorkerInfo(worker_id="w1", address="http://localhost:8001"))

            registry = ProviderRegistry()
            router = ModelRouter()
            gateway = ModelGateway(
                profiles={},
                provider_registry=registry,
                router=router,
                event_bus=bus,
                hook_system=hooks,
            )

            prompt_registry = PromptRegistry(
                template_dir=str(templates_dir),
                event_bus=bus,
            )

            reloader = HotReloader(
                config_dir=str(config_dir),
                event_bus=bus,
                hook_system=hooks,
                worker_broadcaster=broadcaster,
                templates_dir=str(templates_dir),
                playbooks_dir=str(playbooks_dir),
                model_gateway=gateway,
                prompt_registry=prompt_registry,
            )

            with patch("httpx.post") as mock_post:
                mock_post.return_value = MagicMock(status_code=200)
                result = reloader.reload(ReloadScope.ALL)

            assert result.success

    def test_remove_model_cascades_everywhere(self):
        bus = EventBus()
        hooks = HookSystem()
        broadcaster = WorkerBroadcaster()
        broadcaster.register(WorkerInfo(worker_id="w1", address="http://localhost:8001"))

        hook_events = []
        hooks.register_callback("on_model_removed", lambda e: hook_events.append(e))

        registry = ProviderRegistry()
        router = ModelRouter()
        gateway = ModelGateway(
            profiles={},
            provider_registry=registry,
            router=router,
            event_bus=bus,
            hook_system=hooks,
            worker_broadcaster=broadcaster,
        )

        gateway.add_profile("gpt-5", provider="openai", model="gpt-5", api_key_env="KEY")
        profile_ids = [p.model_profile_id for p in gateway.list_profiles()]
        assert "gpt-5" in profile_ids

        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            gateway.remove_profile("gpt-5")

        profile_ids_after = [p.model_profile_id for p in gateway.list_profiles()]
        assert "gpt-5" not in profile_ids_after
        assert len(hook_events) == 1

    def test_webhook_receives_model_events(self):
        bus = EventBus()
        hooks = HookSystem()
        hooks.register_webhook("on_model_added", "https://hooks.example.com/models")

        registry = ProviderRegistry()
        router = ModelRouter()
        gateway = ModelGateway(
            profiles={},
            provider_registry=registry,
            router=router,
            event_bus=bus,
            hook_system=hooks,
        )

        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            gateway.add_profile("gpt-5", provider="openai", model="gpt-5", api_key_env="KEY")

            webhook_calls = [c for c in mock_post.call_args_list if "hooks.example.com" in str(c)]
            assert len(webhook_calls) >= 1
            body = webhook_calls[0][1]["json"]
            assert body["event"] == "on_model_added"
            assert body["payload"]["model_id"] == "gpt-5"
