"""Coverage tests for events/ and reload/ — fills gaps in hooks redaction,
hot_reloader gates, worker_broadcast HTTP paths, and bus single-dispatch."""
from __future__ import annotations

import sys
import types
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.events.bus import EventBus
from general_ludd.events.hooks import HookSystem, WebhookConfig, _redact_payload
from general_ludd.events.types import Event, EventType
from general_ludd.reload.hot_reloader import HotReloader, ReloadScope
from general_ludd.reload.worker_broadcast import (
    BroadcastResult,
    WorkerBroadcaster,
    WorkerInfo,
)


# ---------------------------------------------------------------------------
# _redact_payload
# ---------------------------------------------------------------------------


class TestRedactPayload:
    def test_plain_safe_key_passes_through(self):
        result = _redact_payload({"model": "gpt-4", "count": 3})
        assert result == {"model": "gpt-4", "count": 3}

    def test_api_key_stripped(self):
        result = _redact_payload({"api_key": "sk-secret", "model": "x"})
        assert "api_key" not in result
        assert result["model"] == "x"

    def test_token_stripped(self):
        result = _redact_payload({"token": "abc123"})
        assert "token" not in result

    def test_secret_stripped(self):
        result = _redact_payload({"secret": "shh", "ok": 1})
        assert "secret" not in result
        assert result["ok"] == 1

    def test_password_stripped(self):
        result = _redact_payload({"password": "hunter2"})
        assert "password" not in result

    def test_credential_stripped(self):
        result = _redact_payload({"credential": "cred_xyz"})
        assert "credential" not in result

    def test_authorization_stripped(self):
        result = _redact_payload({"authorization": "Bearer tok"})
        assert "authorization" not in result

    def test_case_insensitive_strip(self):
        result = _redact_payload({"API_KEY": "val", "TOKEN": "val2", "Safe": "ok"})
        assert "API_KEY" not in result
        assert "TOKEN" not in result
        assert result["Safe"] == "ok"

    def test_partial_match_strips(self):
        # key containing substring "token" should be stripped
        result = _redact_payload({"access_token": "x", "my_password_hash": "y"})
        assert "access_token" not in result
        assert "my_password_hash" not in result

    def test_nested_dict_redacted(self):
        payload = {"outer": {"api_key": "s", "safe": "v"}, "top": "ok"}
        result = _redact_payload(payload)
        assert "api_key" not in result["outer"]
        assert result["outer"]["safe"] == "v"
        assert result["top"] == "ok"

    def test_list_of_dicts_redacted(self):
        payload = {"items": [{"token": "t", "name": "a"}, {"name": "b"}]}
        result = _redact_payload(payload)
        assert "token" not in result["items"][0]
        assert result["items"][0]["name"] == "a"
        assert result["items"][1]["name"] == "b"

    def test_list_of_non_dicts_passes_through(self):
        payload = {"tags": ["a", "b", "c"]}
        result = _redact_payload(payload)
        assert result["tags"] == ["a", "b", "c"]

    def test_empty_payload(self):
        assert _redact_payload({}) == {}

    def test_depth_cap_returns_unchanged(self):
        # At depth > 10 the function returns the payload as-is (no redaction).
        # Build an 11-deep nest; the innermost dict has a secret key.
        # We call _redact_payload with _depth=11 directly to hit the cap.
        inner = {"secret": "should_not_be_stripped"}
        result = _redact_payload(inner, _depth=11)
        # depth cap: payload returned unchanged
        assert result["secret"] == "should_not_be_stripped"

    def test_deeply_nested_redacted_within_depth(self):
        # 3-level nesting well within the depth cap: secrets ARE stripped
        payload = {"a": {"b": {"secret": "x", "ok": 1}}}
        result = _redact_payload(payload)
        assert "secret" not in result["a"]["b"]
        assert result["a"]["b"]["ok"] == 1

    def test_original_not_mutated(self):
        original = {"api_key": "x", "safe": "y"}
        _redact_payload(original)
        assert "api_key" in original  # original unchanged


# ---------------------------------------------------------------------------
# HookSystem._fire_webhook — body redaction + HTTP paths
# ---------------------------------------------------------------------------


class TestFireWebhook:
    def _make_system_with_webhook(self, url="https://hook.test/cb", **kw):
        hs = HookSystem()
        hs.register_webhook("ev", url, **kw)
        return hs

    def test_webhook_body_strips_secret_key(self):
        hs = self._make_system_with_webhook()
        with patch("general_ludd.events.hooks.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            hs.fire("ev", {"api_key": "secret!", "model": "gpt"})
            body = mock_post.call_args[1]["json"]
            assert "api_key" not in body["payload"]
            assert body["payload"]["model"] == "gpt"

    def test_webhook_body_event_name(self):
        hs = self._make_system_with_webhook()
        with patch("general_ludd.events.hooks.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            hs.fire("ev", {})
            body = mock_post.call_args[1]["json"]
            assert body["event"] == "ev"

    def test_webhook_uses_config_headers_not_psk(self):
        hs = HookSystem()
        hs.register_webhook("ev", "https://hook.test/cb", headers={"X-My": "val"})
        with patch("general_ludd.events.hooks.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            hs.fire("ev", {})
            call_headers = mock_post.call_args[1]["headers"]
            assert call_headers == {"X-My": "val"}

    def test_webhook_network_exception_counted_as_failure(self):
        hs = self._make_system_with_webhook(retry_count=1)
        with patch("general_ludd.events.hooks.httpx.post", side_effect=Exception("conn refused")):
            count = hs.fire("ev", {})
        assert count == 0

    def test_webhook_retry_succeeds_on_second_attempt(self):
        hs = self._make_system_with_webhook(retry_count=3)
        resp_ok = MagicMock(status_code=200)
        with patch("general_ludd.events.hooks.httpx.post", side_effect=[Exception("fail"), resp_ok]) as mock_post:
            count = hs.fire("ev", {})
        assert count == 1
        assert mock_post.call_count == 2

    def test_webhook_all_retries_fail_returns_zero(self):
        hs = self._make_system_with_webhook(retry_count=3)
        with patch("general_ludd.events.hooks.httpx.post", side_effect=Exception("timeout")) as mock_post:
            count = hs.fire("ev", {})
        assert count == 0
        assert mock_post.call_count == 3

    def test_webhook_retry_count_clamped_to_5(self):
        hs = HookSystem()
        hs.register_webhook("ev", "https://hook.test/cb", retry_count=99)
        hook = hs.list_hooks()[0]
        assert hook.webhook_config.retry_count == 5

    def test_webhook_retry_count_min_1(self):
        hs = HookSystem()
        hs.register_webhook("ev", "https://hook.test/cb", retry_count=0)
        hook = hs.list_hooks()[0]
        assert hook.webhook_config.retry_count == 1

    def test_fire_end_to_end_webhook_200_returns_1(self):
        hs = HookSystem()
        hs.register_webhook("deploy", "https://ci.test/deploy")
        with patch("general_ludd.events.hooks.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            count = hs.fire("deploy", {"env": "prod"})
        assert count == 1

    def test_webhook_nested_secret_in_payload_stripped(self):
        hs = self._make_system_with_webhook()
        with patch("general_ludd.events.hooks.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            hs.fire("ev", {"config": {"token": "abc", "name": "x"}})
            body = mock_post.call_args[1]["json"]
            assert "token" not in body["payload"]["config"]
            assert body["payload"]["config"]["name"] == "x"


# ---------------------------------------------------------------------------
# EventBus — sync subscriber called exactly ONCE (not doubled by if/elif)
# ---------------------------------------------------------------------------


class TestBusPublishSingleDispatch:
    def test_sync_callback_called_once(self):
        bus = EventBus()
        cb = MagicMock()
        bus.subscribe(EventType.MODEL_ADDED, cb)
        bus.publish(Event(type=EventType.MODEL_ADDED, payload={}))
        assert cb.call_count == 1

    def test_sync_callback_not_double_dispatched_with_wildcard(self):
        bus = EventBus()
        specific_cb = MagicMock()
        wildcard_cb = MagicMock()
        bus.subscribe(EventType.CUSTOM, specific_cb)
        bus.subscribe("*", wildcard_cb)
        bus.publish(Event(type=EventType.CUSTOM, payload={}))
        assert specific_cb.call_count == 1
        assert wildcard_cb.call_count == 1

    def test_failing_sync_subscriber_not_double_counted(self):
        bus = EventBus()
        cb = MagicMock(side_effect=RuntimeError("boom"))
        bus.subscribe(EventType.CUSTOM, cb)
        delivered = bus.publish(Event(type=EventType.CUSTOM, payload={}))
        assert cb.call_count == 1
        assert delivered == 0  # failed, not counted


# ---------------------------------------------------------------------------
# HotReloader.reload_code_module — fail-closed gates
# ---------------------------------------------------------------------------


class TestHotReloaderCodeModule:
    def _make_reloader(self, tmp_path):
        return HotReloader(config_dir=str(tmp_path))

    def test_module_not_importable_returns_failure(self, tmp_path):
        reloader = self._make_reloader(tmp_path)
        candidate = tmp_path / "cand.py"
        candidate.write_text("x = 1")
        result = reloader.reload_code_module(
            "nonexistent_module_xyz_abc_99999",
            str(candidate),
        )
        assert result.success is False
        assert "not importable" in (result.error or "")

    def test_module_no_file_returns_failure(self, tmp_path):
        # Insert a synthetic module with no __file__
        mod = ModuleType("_test_no_file_mod")
        mod.__file__ = None  # type: ignore[assignment]
        sys.modules["_test_no_file_mod"] = mod
        try:
            reloader = self._make_reloader(tmp_path)
            candidate = tmp_path / "cand.py"
            candidate.write_text("x = 1")
            with patch("general_ludd.reload.hot_reloader.check_self_modification"):
                result = reloader.reload_code_module(
                    "_test_no_file_mod",
                    str(candidate),
                )
            assert result.success is False
            assert "__file__" in (result.error or "")
        finally:
            sys.modules.pop("_test_no_file_mod", None)

    def test_protected_path_error_returns_failure(self, tmp_path):
        # A real file-backed module via a temp file
        live_src = tmp_path / "live_mod.py"
        live_src.write_text("x = 1")
        mod = ModuleType("_test_protected_mod")
        mod.__file__ = str(live_src)
        sys.modules["_test_protected_mod"] = mod
        try:
            reloader = self._make_reloader(tmp_path)
            candidate = tmp_path / "cand.py"
            candidate.write_text("x = 2")
            from general_ludd.security.capability_lattice import ProtectedPathError

            with patch(
                "general_ludd.reload.hot_reloader.check_self_modification",
                side_effect=ProtectedPathError("guardrail"),
            ):
                result = reloader.reload_code_module(
                    "_test_protected_mod",
                    str(candidate),
                )
            assert result.success is False
            assert "protected guard file" in (result.error or "")
        finally:
            sys.modules.pop("_test_protected_mod", None)

    def test_capability_error_returns_failure(self, tmp_path):
        live_src = tmp_path / "live_cap.py"
        live_src.write_text("x = 1")
        mod = ModuleType("_test_cap_mod")
        mod.__file__ = str(live_src)
        sys.modules["_test_cap_mod"] = mod
        try:
            reloader = self._make_reloader(tmp_path)
            candidate = tmp_path / "cand.py"
            candidate.write_text("x = 2")
            from general_ludd.security.capability_lattice import CapabilityError

            with patch(
                "general_ludd.reload.hot_reloader.check_self_modification",
                side_effect=CapabilityError("no permission"),
            ):
                result = reloader.reload_code_module(
                    "_test_cap_mod",
                    str(candidate),
                )
            assert result.success is False
            assert "capability denied" in (result.error or "")
        finally:
            sys.modules.pop("_test_cap_mod", None)

    def test_candidate_missing_returns_failure(self, tmp_path):
        live_src = tmp_path / "live_miss.py"
        live_src.write_text("x = 1")
        mod = ModuleType("_test_miss_cand_mod")
        mod.__file__ = str(live_src)
        sys.modules["_test_miss_cand_mod"] = mod
        try:
            reloader = self._make_reloader(tmp_path)
            with patch("general_ludd.reload.hot_reloader.check_self_modification"):
                result = reloader.reload_code_module(
                    "_test_miss_cand_mod",
                    str(tmp_path / "does_not_exist.py"),
                )
            assert result.success is False
            assert "candidate source not found" in (result.error or "")
        finally:
            sys.modules.pop("_test_miss_cand_mod", None)

    def test_no_health_check_triggers_rollback_failure(self, tmp_path):
        live_src = tmp_path / "live_nhc.py"
        live_src.write_text("x = 1")
        mod = ModuleType("_test_nhc_mod")
        mod.__file__ = str(live_src)
        sys.modules["_test_nhc_mod"] = mod
        try:
            reloader = self._make_reloader(tmp_path)
            candidate = tmp_path / "cand.py"
            candidate.write_text("x = 2")
            with patch("general_ludd.reload.hot_reloader.check_self_modification"), patch(
                "general_ludd.reload.hot_reloader.importlib.reload"
            ):
                result = reloader.reload_code_module(
                    "_test_nhc_mod",
                    str(candidate),
                    health_check=None,
                )
            assert result.success is False
            assert "no health_check" in (result.error or "")
            assert result.details.get("rolled_back") is True
        finally:
            sys.modules.pop("_test_nhc_mod", None)

    def test_health_check_false_triggers_rollback_failure(self, tmp_path):
        live_src = tmp_path / "live_hf.py"
        live_src.write_text("x = 1")
        mod = ModuleType("_test_hf_mod")
        mod.__file__ = str(live_src)
        sys.modules["_test_hf_mod"] = mod
        try:
            reloader = self._make_reloader(tmp_path)
            candidate = tmp_path / "cand.py"
            candidate.write_text("x = 2")
            with patch("general_ludd.reload.hot_reloader.check_self_modification"), patch(
                "general_ludd.reload.hot_reloader.importlib.reload"
            ):
                result = reloader.reload_code_module(
                    "_test_hf_mod",
                    str(candidate),
                    health_check=lambda: False,
                )
            assert result.success is False
            assert "health gate failed" in (result.error or "")
            assert result.details.get("rolled_back") is True
        finally:
            sys.modules.pop("_test_hf_mod", None)

    def test_health_check_raises_treated_as_unhealthy(self, tmp_path):
        live_src = tmp_path / "live_hre.py"
        live_src.write_text("x = 1")
        mod = ModuleType("_test_hre_mod")
        mod.__file__ = str(live_src)
        sys.modules["_test_hre_mod"] = mod
        try:
            reloader = self._make_reloader(tmp_path)
            candidate = tmp_path / "cand.py"
            candidate.write_text("x = 2")
            with patch("general_ludd.reload.hot_reloader.check_self_modification"), patch(
                "general_ludd.reload.hot_reloader.importlib.reload"
            ):
                result = reloader.reload_code_module(
                    "_test_hre_mod",
                    str(candidate),
                    health_check=lambda: (_ for _ in ()).throw(RuntimeError("check exploded")),
                )
            assert result.success is False
            assert result.details.get("rolled_back") is True
        finally:
            sys.modules.pop("_test_hre_mod", None)

    def test_happy_path_health_check_passes(self, tmp_path):
        live_src = tmp_path / "live_hp.py"
        live_src.write_text("x = 1")
        mod = ModuleType("_test_hp_mod")
        mod.__file__ = str(live_src)
        sys.modules["_test_hp_mod"] = mod
        try:
            reloader = self._make_reloader(tmp_path)
            candidate = tmp_path / "cand.py"
            candidate.write_text("x = 2")
            with patch("general_ludd.reload.hot_reloader.check_self_modification"), patch(
                "general_ludd.reload.hot_reloader.importlib.reload"
            ):
                result = reloader.reload_code_module(
                    "_test_hp_mod",
                    str(candidate),
                    health_check=lambda: True,
                )
            assert result.success is True
            assert result.details.get("rolled_back") is False
        finally:
            sys.modules.pop("_test_hp_mod", None)


# ---------------------------------------------------------------------------
# WorkerBroadcaster — HTTP paths + PSK header
# ---------------------------------------------------------------------------


class TestWorkerBroadcaster:
    def _broadcaster_with_worker(self, worker_id="w1", address="http://worker:8000"):
        wb = WorkerBroadcaster()
        wb.register(WorkerInfo(worker_id=worker_id, address=address))
        return wb

    def test_broadcast_reload_200_success(self):
        wb = self._broadcaster_with_worker()
        with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            results = wb.broadcast_reload(ReloadScope.ALL)
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].worker_id == "w1"

    def test_broadcast_reload_401_failure(self):
        wb = self._broadcaster_with_worker()
        with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=401)
            results = wb.broadcast_reload(ReloadScope.MODELS)
        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error == "Unauthorized"

    def test_broadcast_reload_non_200_failure(self):
        wb = self._broadcaster_with_worker()
        with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=503)
            results = wb.broadcast_reload(ReloadScope.CONFIG)
        assert results[0].success is False
        assert "503" in (results[0].error or "")

    def test_broadcast_reload_network_exception(self):
        wb = self._broadcaster_with_worker()
        with patch(
            "general_ludd.reload.worker_broadcast.httpx.post",
            side_effect=Exception("connection refused"),
        ):
            results = wb.broadcast_reload(ReloadScope.TEMPLATES)
        assert results[0].success is False
        assert "connection refused" in (results[0].error or "")

    def test_broadcast_reload_no_workers_returns_empty(self):
        wb = WorkerBroadcaster()
        with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
            results = wb.broadcast_reload(ReloadScope.ALL)
        assert results == []
        mock_post.assert_not_called()

    def test_broadcast_model_update_200_success(self):
        wb = self._broadcaster_with_worker()
        with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            results = wb.broadcast_model_update("add", "gpt-5", {"provider": "openai"})
        assert results[0].success is True

    def test_broadcast_model_update_401_failure(self):
        wb = self._broadcaster_with_worker()
        with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=401)
            results = wb.broadcast_model_update("remove", "old", {})
        assert results[0].success is False
        assert results[0].error == "Unauthorized"

    def test_broadcast_model_update_non_200(self):
        wb = self._broadcaster_with_worker()
        with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=500)
            results = wb.broadcast_model_update("add", "m", {})
        assert results[0].success is False
        assert "500" in (results[0].error or "")

    def test_psk_header_present_when_env_set(self):
        wb = self._broadcaster_with_worker()
        with patch.dict("os.environ", {"GLUDD_PSK": "supersecret"}):
            with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
                mock_post.return_value = MagicMock(status_code=200)
                wb.broadcast_reload(ReloadScope.ALL)
                call_headers = mock_post.call_args[1]["headers"]
        assert call_headers.get("Authorization") == "Bearer supersecret"

    def test_psk_header_absent_when_env_unset(self):
        wb = self._broadcaster_with_worker()
        env = {k: v for k, v in __import__("os").environ.items() if k != "GLUDD_PSK"}
        with patch.dict("os.environ", env, clear=True):
            with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
                mock_post.return_value = MagicMock(status_code=200)
                wb.broadcast_reload(ReloadScope.ALL)
                call_headers = mock_post.call_args[1]["headers"]
        assert "Authorization" not in call_headers

    def test_broadcast_reload_posts_to_admin_reload_endpoint(self):
        wb = self._broadcaster_with_worker(address="http://worker:9000")
        with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            wb.broadcast_reload(ReloadScope.ALL)
            url = mock_post.call_args[0][0]
        assert url == "http://worker:9000/admin/reload"

    def test_broadcast_model_update_posts_to_models_sync_endpoint(self):
        wb = self._broadcaster_with_worker(address="http://worker:9000")
        with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            wb.broadcast_model_update("add", "m", {})
            url = mock_post.call_args[0][0]
        assert url == "http://worker:9000/admin/models/sync"

    def test_register_and_unregister_worker(self):
        wb = WorkerBroadcaster()
        wb.register(WorkerInfo(worker_id="w99", address="http://w:8000"))
        assert len(wb.list_workers()) == 1
        wb.unregister("w99")
        assert len(wb.list_workers()) == 0

    def test_multiple_workers_all_contacted(self):
        wb = WorkerBroadcaster()
        wb.register(WorkerInfo(worker_id="w1", address="http://w1:8000"))
        wb.register(WorkerInfo(worker_id="w2", address="http://w2:8000"))
        with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            results = wb.broadcast_reload(ReloadScope.ALL)
        assert mock_post.call_count == 2
        assert len(results) == 2
        assert all(r.success for r in results)
