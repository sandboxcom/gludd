"""Deep tests for ansible collection modules.

Tests gludd_ping, gludd_features, gludd_metrics.
Covers: argument validation, error handling, JSON output format,
changed/unchanged tracking, idempotency.
"""

from __future__ import annotations

import contextlib
from types import ModuleType
from typing import Any, ClassVar
from unittest.mock import patch

COLLECTION_MODULES = "ansible_collections.general_ludd.agent.plugins.modules"
MODULE_UTILS = "ansible_collections.general_ludd.agent.plugins.module_utils.gludd"


def _mock_ansible_module(
    params: dict[str, Any],
    check_mode: bool = False,
) -> type:
    """Build a mock AnsibleModule class that captures argument_spec and exit calls."""

    class MockModule:
        captured_exit: ClassVar[list[dict[str, Any]]] = []
        captured_fail: ClassVar[list[dict[str, Any]]] = []
        captured_spec: ClassVar[dict[str, Any]] = {}

        def __init__(self, argument_spec=None, supports_check_mode=False):
            MockModule.captured_spec = dict(argument_spec or {})
            self.params = {}
            if argument_spec:
                for k, v in argument_spec.items():
                    if isinstance(v, dict) and "default" in v:
                        self.params[k] = v["default"]
                    else:
                        self.params[k] = None
            self.params.update(dict(params))
            self.check_mode = check_mode
            self._supports_check_mode = supports_check_mode

        def exit_json(self, **kwargs):
            MockModule.captured_exit.append(dict(kwargs))
            raise SystemExit(0)

        def fail_json(self, **kwargs):
            MockModule.captured_fail.append(dict(kwargs))
            raise SystemExit(1)

        @classmethod
        def reset(cls):
            cls.captured_exit.clear()
            cls.captured_fail.clear()
            cls.captured_spec.clear()

    return MockModule


def _make_fake_client(
    reachable: bool = True,
    get_response: dict[str, Any] | None = None,
    post_response: dict[str, Any] | None = None,
    get_error: str | None = None,
    get_status: int = 200,
) -> type:
    """Build a fake GluddClient class."""

    class FakeClient:
        captured_base_url: ClassVar[str] = ""
        captured_psk: ClassVar[str] = ""
        captured_timeout: ClassVar[int] = 30
        get_calls: ClassVar[list[tuple[str, Any]]] = []
        post_calls: ClassVar[list[tuple[str, Any]]] = []

        def __init__(self, base_url="", psk="", timeout=30):
            FakeClient.captured_base_url = base_url
            FakeClient.captured_psk = psk
            FakeClient.captured_timeout = timeout

        def reachable(self):
            return reachable

        def get(self, path, params=None):
            FakeClient.get_calls.append((path, params))
            if get_error:
                return {"_error": get_error, "_status": get_status}
            return dict(get_response or {})

        def post(self, path, body=None):
            FakeClient.post_calls.append((path, body))
            if post_response is not None:
                return dict(post_response)
            return {}

        @classmethod
        def reset(cls):
            cls.captured_base_url = ""
            cls.captured_psk = ""
            cls.captured_timeout = 30
            cls.get_calls.clear()
            cls.post_calls.clear()

    return FakeClient


def _json_parseable(value: Any) -> bool:
    """Verify a dict is JSON-serialisable (no non-string keys, no odd types)."""
    import json

    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# gludd_ping tests
# ---------------------------------------------------------------------------


class TestGluddPing:
    """Deep tests for gludd_ping — daemon health-check probe."""

    @staticmethod
    def _load_module() -> ModuleType:
        MockAnsible = _mock_ansible_module({})
        FakeClient = _make_fake_client()

        with (
            patch("ansible.module_utils.basic.AnsibleModule", new=MockAnsible),
            patch(f"{COLLECTION_MODULES}.gludd_ping.GluddClient", new=FakeClient),
        ):
            MockAnsible.reset()
            FakeClient.reset()
            from ansible_collections.general_ludd.agent.plugins.modules import (
                gludd_ping,
            )

            return gludd_ping

    def test_argument_spec_keys(self):
        """Argument spec must declare daemon_url, psk, timeout."""
        gludd_ping = self._load_module()
        MockAnsible = gludd_ping.AnsibleModule

        MockAnsible.reset()
        with contextlib.suppress(SystemExit):
            gludd_ping.main()

        spec = MockAnsible.captured_spec
        assert "daemon_url" in spec, "daemon_url must be in argument_spec"
        assert "psk" in spec, "psk must be in argument_spec"
        assert "timeout" in spec, "timeout must be in argument_spec"
        assert spec["daemon_url"].get("type") == "str"
        assert spec["daemon_url"].get("default") == "http://localhost:8000"
        assert spec["psk"].get("type") == "str"
        assert spec["psk"].get("no_log") is True
        assert spec["timeout"].get("type") == "int"
        assert spec["timeout"].get("default") == 10

    def test_output_when_reachable(self):
        """When daemon reachable, output has pong=True, daemon_reachable=True."""
        gludd_ping = self._load_module()
        MockAnsible = _mock_ansible_module({"daemon_url": "http://10.1.2.3:8000", "psk": "s3cret", "timeout": 5})
        FakeClient = _make_fake_client(reachable=True)

        with (
            patch.object(gludd_ping, "AnsibleModule", MockAnsible),
            patch.object(gludd_ping, "GluddClient", FakeClient),
        ):
            MockAnsible.reset()
            FakeClient.reset()
            with contextlib.suppress(SystemExit):
                gludd_ping.main()

        assert len(MockAnsible.captured_exit) == 1
        result = MockAnsible.captured_exit[0]
        assert result["failed"] is False
        assert result["changed"] is False
        assert result["pong"] is True
        assert result["daemon_reachable"] is True
        assert result["daemon_url"] == "http://10.1.2.3:8000"
        assert _json_parseable(result), "result must be JSON-serialisable"

    def test_output_when_unreachable(self):
        """When daemon unreachable, pong=True but daemon_reachable=False."""
        gludd_ping = self._load_module()
        MockAnsible = _mock_ansible_module({"daemon_url": "http://localhost:9000"})
        FakeClient = _make_fake_client(reachable=False)

        with (
            patch.object(gludd_ping, "AnsibleModule", MockAnsible),
            patch.object(gludd_ping, "GluddClient", FakeClient),
        ):
            MockAnsible.reset()
            with contextlib.suppress(SystemExit):
                gludd_ping.main()

        assert len(MockAnsible.captured_exit) == 1
        result = MockAnsible.captured_exit[0]
        assert result["failed"] is False
        assert result["changed"] is False
        assert result["pong"] is True
        assert result["daemon_reachable"] is False
        assert result["daemon_url"] == "http://localhost:9000"

    def test_always_unchanged(self):
        """Ping is read-only; changed must always be False."""
        gludd_ping = self._load_module()

        for reachable_val in (True, False):
            MockAnsible = _mock_ansible_module({"daemon_url": "http://127.0.0.1:8000"})
            FakeClient = _make_fake_client(reachable=reachable_val)
            with (
                patch.object(gludd_ping, "AnsibleModule", MockAnsible),
                patch.object(gludd_ping, "GluddClient", FakeClient),
            ):
                MockAnsible.reset()
                with contextlib.suppress(SystemExit):
                    gludd_ping.main()
            result = MockAnsible.captured_exit[0]
            assert result["changed"] is False, f"changed must be False when reachable={reachable_val}"

    def test_idempotency(self):
        """Running ping twice produces same result."""
        gludd_ping = self._load_module()
        results = []

        for _ in range(2):
            MockAnsible = _mock_ansible_module({"daemon_url": "http://localhost:8000"})
            FakeClient = _make_fake_client(reachable=True)
            with (
                patch.object(gludd_ping, "AnsibleModule", MockAnsible),
                patch.object(gludd_ping, "GluddClient", FakeClient),
            ):
                MockAnsible.reset()
                with contextlib.suppress(SystemExit):
                    gludd_ping.main()
            r = MockAnsible.captured_exit[0]
            results.append(
                {
                    "pong": r["pong"],
                    "daemon_reachable": r["daemon_reachable"],
                    "changed": r["changed"],
                    "failed": r["failed"],
                }
            )

        assert results[0] == results[1], "idempotent runs must produce identical output"

    def test_passes_timeout_to_client(self):
        """Client receives timeout from module params."""
        gludd_ping = self._load_module()
        MockAnsible = _mock_ansible_module({"daemon_url": "http://localhost:8000", "timeout": 42})
        FakeClient = _make_fake_client(reachable=True)

        with (
            patch.object(gludd_ping, "AnsibleModule", MockAnsible),
            patch.object(gludd_ping, "GluddClient", FakeClient),
        ):
            MockAnsible.reset()
            FakeClient.reset()
            with contextlib.suppress(SystemExit):
                gludd_ping.main()

        assert FakeClient.captured_timeout == 42

    def test_psk_passed_to_client(self):
        """Client receives PSK from module params."""
        gludd_ping = self._load_module()
        MockAnsible = _mock_ansible_module({"psk": "my-secret-psk"})
        FakeClient = _make_fake_client(reachable=True)

        with (
            patch.object(gludd_ping, "AnsibleModule", MockAnsible),
            patch.object(gludd_ping, "GluddClient", FakeClient),
        ):
            MockAnsible.reset()
            FakeClient.reset()
            with contextlib.suppress(SystemExit):
                gludd_ping.main()

        assert FakeClient.captured_psk == "my-secret-psk"

    def test_check_mode_supported(self):
        """Module declares supports_check_mode=True."""
        gludd_ping = self._load_module()
        FakeClient = _make_fake_client()

        class CaptureCheckMode:
            flag = None

            def __init__(self, *args, **kwargs):
                CaptureCheckMode.flag = kwargs.get("supports_check_mode")
                self.params = {}

            def exit_json(self, **kwargs):
                raise SystemExit(0)

        with (
            patch.object(gludd_ping, "AnsibleModule", CaptureCheckMode),
            patch.object(gludd_ping, "GluddClient", FakeClient),
            contextlib.suppress(SystemExit),
        ):
            gludd_ping.main()

        assert CaptureCheckMode.flag is True, "supports_check_mode must be True"


# ---------------------------------------------------------------------------
# gludd_features tests
# ---------------------------------------------------------------------------


class TestGluddFeatures:
    """Deep tests for gludd_features — feature database query/verify."""

    @staticmethod
    def _load_module() -> ModuleType:
        MockAnsible = _mock_ansible_module({})
        FakeClient = _make_fake_client()

        with (
            patch("ansible.module_utils.basic.AnsibleModule", new=MockAnsible),
            patch(f"{COLLECTION_MODULES}.gludd_features.GluddClient", new=FakeClient),
        ):
            MockAnsible.reset()
            FakeClient.reset()
            from ansible_collections.general_ludd.agent.plugins.modules import (
                gludd_features,
            )

            return gludd_features

    def _run(self, gludd_features, params, client_responses=None, check_mode=False):
        MockAnsible = _mock_ansible_module(params, check_mode=check_mode)

        class RealFakeClient:
            def __init__(self, base_url="", psk="", timeout=30):
                pass

            def reachable(self):
                return True

            def get(self, path, params=None):
                r = client_responses.get("get_response", {}) if client_responses else {}
                if client_responses and "get_error" in client_responses:
                    return {"_error": client_responses["get_error"], "_status": client_responses.get("get_status", 200)}
                merged = {"_status": client_responses.get("get_status", 200) if client_responses else 200}
                merged.update(r)
                return merged

            def post(self, path, body=None):
                r = client_responses.get("post_response", {}) if client_responses else {}
                if client_responses and "post_error" in client_responses:
                    return {
                        "_error": client_responses["post_error"],
                        "_status": client_responses.get("post_status", 200),
                    }
                merged = {"_status": client_responses.get("post_status", 200) if client_responses else 200}
                merged.update(r)
                return merged

        with (
            patch.object(gludd_features, "AnsibleModule", MockAnsible),
            patch.object(gludd_features, "GluddClient", RealFakeClient),
        ):
            MockAnsible.reset()
            with contextlib.suppress(SystemExit):
                gludd_features.main()
        return MockAnsible

    def test_argument_spec_keys(self):
        """Argument spec declares all expected parameters."""
        gludd_features = self._load_module()
        MockAnsible = self._run(gludd_features, {})

        spec = MockAnsible.captured_spec
        assert "state" in spec
        assert spec["state"]["default"] == "list"
        assert spec["state"]["choices"] == ["list", "verify"]
        assert "status" in spec
        assert spec["status"]["choices"] == ["requested", "implemented", "verified", "regressed"]
        assert "category" in spec
        assert "project_id" in spec
        assert "daemon_url" in spec
        assert "psk" in spec
        assert "timeout" in spec
        assert spec["psk"]["no_log"] is True

    def test_state_list_returns_features(self):
        """state=list returns features and facts."""
        gludd_features = self._load_module()
        mock = self._run(
            gludd_features,
            {"state": "list"},
            client_responses={
                "get_response": {
                    "features": [
                        {"name": "f1", "status": "implemented"},
                        {"name": "f2", "status": "requested"},
                    ],
                    "total": 2,
                },
                "get_status": 200,
            },
        )

        assert len(mock.captured_exit) == 1
        r = mock.captured_exit[0]
        assert r["failed"] is False
        assert r["changed"] is False
        assert len(r["features"]) == 2
        assert r["total"] == 2
        assert "gludd_features" in r["ansible_facts"]
        assert len(r["ansible_facts"]["gludd_features"]) == 2
        assert _json_parseable(r)

    def test_state_list_with_filters(self):
        """state=list passes status/category as query params."""
        gludd_features = self._load_module()

        class CapturingFakeClient:
            get_path = None
            get_params = None

            def __init__(self, base_url="", psk="", timeout=30):
                pass

            def reachable(self):
                return True

            def get(self, path, params=None):
                CapturingFakeClient.get_path = path
                CapturingFakeClient.get_params = params
                return {"features": [], "total": 0, "_status": 200}

            def post(self, path, body=None):
                return {"_status": 200}

        MockAnsible = _mock_ansible_module({"state": "list", "status": "verified", "category": "api"})

        with (
            patch.object(gludd_features, "AnsibleModule", MockAnsible),
            patch.object(gludd_features, "GluddClient", CapturingFakeClient),
        ):
            MockAnsible.reset()
            with contextlib.suppress(SystemExit):
                gludd_features.main()

        assert CapturingFakeClient.get_path == "/api/features"
        assert CapturingFakeClient.get_params == {"status": "verified", "category": "api"}

    def test_state_list_unchanged(self):
        """state=list always reports changed=False."""
        gludd_features = self._load_module()
        mock = self._run(
            gludd_features,
            {"state": "list"},
            client_responses={"get_response": {"features": [{"name": "x"}], "total": 1}, "get_status": 200},
        )

        assert mock.captured_exit[0]["changed"] is False

    def test_state_verify_returns_summary_and_results(self):
        """state=verify returns summary and results."""
        gludd_features = self._load_module()
        mock = self._run(
            gludd_features,
            {"state": "verify"},
            client_responses={
                "post_response": {
                    "summary": {"total": 5, "passed": 4, "failed": 1},
                    "results": [{"feature": "f1", "status": "ok"}, {"feature": "f2", "status": "regressed"}],
                },
                "post_status": 200,
            },
        )

        assert len(mock.captured_exit) == 1
        r = mock.captured_exit[0]
        assert r["failed"] is False
        assert r["changed"] is True
        assert r["summary"] == {"total": 5, "passed": 4, "failed": 1}
        assert len(r["results"]) == 2
        assert _json_parseable(r)

    def test_state_verify_with_project_id(self):
        """state=verify with project_id passes it in the POST body."""
        gludd_features = self._load_module()

        class CapturingFakeClient:
            post_path = None
            post_body = None

            def __init__(self, base_url="", psk="", timeout=30):
                pass

            def reachable(self):
                return True

            def get(self, path, params=None):
                return {"_status": 200}

            def post(self, path, body=None):
                CapturingFakeClient.post_path = path
                CapturingFakeClient.post_body = body
                return {"summary": {}, "results": [], "_status": 200}

        MockAnsible = _mock_ansible_module({"state": "verify", "project_id": "alpha"})

        with (
            patch.object(gludd_features, "AnsibleModule", MockAnsible),
            patch.object(gludd_features, "GluddClient", CapturingFakeClient),
        ):
            MockAnsible.reset()
            with contextlib.suppress(SystemExit):
                gludd_features.main()

        assert CapturingFakeClient.post_path == "/api/features/verify"
        assert CapturingFakeClient.post_body == {"project_id": "alpha"}

    def test_check_mode_verify_skips_api(self):
        """In check mode, verify returns empty summary without calling POST."""
        gludd_features = self._load_module()

        class NeverCalledClient:
            def __init__(self, base_url="", psk="", timeout=30):
                pass

            def reachable(self):
                return True

            def get(self, *args, **kwargs):
                raise AssertionError("get should not be called in verify+check_mode")

            def post(self, *args, **kwargs):
                raise AssertionError("post should not be called in verify+check_mode")

        MockAnsible = _mock_ansible_module({"state": "verify"}, check_mode=True)

        with (
            patch.object(gludd_features, "AnsibleModule", MockAnsible),
            patch.object(gludd_features, "GluddClient", NeverCalledClient),
        ):
            MockAnsible.reset()
            with contextlib.suppress(SystemExit):
                gludd_features.main()

        assert len(MockAnsible.captured_exit) == 1
        r = MockAnsible.captured_exit[0]
        assert r["failed"] is False
        assert r["changed"] is False
        assert r["summary"] == {}
        assert r["results"] == []
        assert r["msg"] == "check_mode: verify skipped"

    def test_daemon_error_fails_module(self):
        """When daemon returns _error, module fails."""
        gludd_features = self._load_module()
        mock = self._run(
            gludd_features,
            {"state": "list"},
            client_responses={"get_error": "database connection refused", "get_status": 500},
        )

        assert len(mock.captured_fail) == 1
        r = mock.captured_fail[0]
        assert r["failed"] is True
        assert "database connection refused" in r["msg"]

    def test_401_fails_with_unauthorized(self):
        """When daemon returns 401, module fails with clear message."""
        gludd_features = self._load_module()
        mock = self._run(
            gludd_features,
            {"state": "list"},
            client_responses={"get_error": "invalid token", "get_status": 401},
        )

        assert len(mock.captured_fail) == 1
        r = mock.captured_fail[0]
        assert r["failed"] is True
        assert "unauthorized" in r["msg"].lower()

    def test_non_200_list_fails(self):
        """When list returns non-200, module fails."""
        gludd_features = self._load_module()
        mock = self._run(
            gludd_features,
            {"state": "list"},
            client_responses={"get_response": {"detail": "service unavailable"}, "get_status": 503},
        )

        assert len(mock.captured_fail) == 1
        r = mock.captured_fail[0]
        assert r["failed"] is True
        assert "service unavailable" in r["msg"]

    def test_idempotency_state_list(self):
        """Running list twice produces identical output for same mock data."""
        gludd_features = self._load_module()
        results = []

        for _ in range(2):
            mock = self._run(
                gludd_features,
                {"state": "list"},
                client_responses={
                    "get_response": {"features": [{"name": "f1", "status": "implemented"}], "total": 1},
                    "get_status": 200,
                },
            )
            r = mock.captured_exit[0]
            results.append({"total": r["total"], "features_count": len(r["features"]), "changed": r["changed"]})

        assert results[0] == results[1], "idempotent runs must produce identical output"


# ---------------------------------------------------------------------------
# gludd_metrics tests
# ---------------------------------------------------------------------------


class TestGluddMetrics:
    """Deep tests for gludd_metrics — agent/usage/cost/benchmark facts."""

    @staticmethod
    def _load_module() -> ModuleType:
        MockAnsible = _mock_ansible_module({})
        FakeClient = _make_fake_client()

        with (
            patch("ansible.module_utils.basic.AnsibleModule", new=MockAnsible),
            patch(f"{COLLECTION_MODULES}.gludd_metrics.GluddClient", new=FakeClient),
        ):
            MockAnsible.reset()
            FakeClient.reset()
            from ansible_collections.general_ludd.agent.plugins.modules import (
                gludd_metrics,
            )

            return gludd_metrics

    def _run(self, gludd_metrics, params, client_responses=None):
        MockAnsible = _mock_ansible_module(params)

        class RealFakeClient:
            def __init__(self, base_url="", psk="", timeout=30):
                pass

            def reachable(self):
                return True

            def get(self, path, params=None):
                r = client_responses.get("get_response", {}) if client_responses else {}
                if client_responses and "get_error" in client_responses:
                    return {"_error": client_responses["get_error"], "_status": client_responses.get("get_status", 200)}
                merged = {"_status": client_responses.get("get_status", 200) if client_responses else 200}
                merged.update(r)
                return merged

        with (
            patch.object(gludd_metrics, "AnsibleModule", MockAnsible),
            patch.object(gludd_metrics, "GluddClient", RealFakeClient),
        ):
            MockAnsible.reset()
            with contextlib.suppress(SystemExit):
                gludd_metrics.main()
        return MockAnsible

    def test_argument_spec_keys(self):
        """Argument spec declares agent_id, project_id, daemon_url, psk, timeout."""
        gludd_metrics = self._load_module()
        MockAnsible = self._run(gludd_metrics, {})

        spec = MockAnsible.captured_spec
        assert "agent_id" in spec
        assert "project_id" in spec
        assert "daemon_url" in spec
        assert "psk" in spec
        assert "timeout" in spec
        assert spec["agent_id"]["default"] is None
        assert spec["project_id"]["default"] is None

    def test_returns_ansible_facts_with_snapshot(self):
        """Module returns gludd_metrics snapshot under ansible_facts."""
        gludd_metrics = self._load_module()
        mock = self._run(
            gludd_metrics,
            {},
            client_responses={
                "get_response": {
                    "agents": [{"id": "a1", "status": "running"}],
                    "total_agents": 1,
                    "running_agents": 1,
                },
                "get_status": 200,
            },
        )

        assert len(mock.captured_exit) == 1
        r = mock.captured_exit[0]
        assert r["failed"] is False
        assert r["changed"] is False
        assert "gludd_metrics" in r["ansible_facts"]
        snapshot = r["ansible_facts"]["gludd_metrics"]
        assert snapshot["total_agents"] == 1
        assert snapshot["running_agents"] == 1
        assert len(snapshot["agents"]) == 1
        assert _json_parseable(r)

    def test_strips_internal_keys(self):
        """Internal keys (_status, _error, _raw) are stripped from the snapshot."""
        gludd_metrics = self._load_module()
        mock = self._run(
            gludd_metrics,
            {},
            client_responses={
                "get_response": {
                    "agents": [],
                    "total_agents": 0,
                    "running_agents": 0,
                    "_internal_field": "should-be-stripped",
                },
                "get_status": 200,
            },
        )

        snapshot = mock.captured_exit[0]["ansible_facts"]["gludd_metrics"]
        assert "_status" not in snapshot
        assert "_error" not in snapshot
        assert "_internal_field" not in snapshot
        assert "_raw" not in snapshot
        assert "total_agents" in snapshot

    def test_always_unchanged(self):
        """Metrics is read-only; changed must always be False."""
        gludd_metrics = self._load_module()
        mock = self._run(
            gludd_metrics,
            {},
            client_responses={"get_response": {"agents": [], "total_agents": 0}, "get_status": 200},
        )

        assert mock.captured_exit[0]["changed"] is False

    def test_daemon_error_fails_module(self):
        """When daemon returns _error, module fails."""
        gludd_metrics = self._load_module()
        mock = self._run(
            gludd_metrics,
            {},
            client_responses={"get_error": "connection refused", "get_status": 0},
        )

        assert len(mock.captured_fail) == 1
        r = mock.captured_fail[0]
        assert r["failed"] is True
        assert "connection refused" in r["msg"]

    def test_401_fails_with_unauthorized(self):
        """When daemon returns 401, module fails with clear message."""
        gludd_metrics = self._load_module()
        mock = self._run(
            gludd_metrics,
            {},
            client_responses={"get_error": "invalid psk", "get_status": 401},
        )

        assert len(mock.captured_fail) == 1
        r = mock.captured_fail[0]
        assert r["failed"] is True
        assert "unauthorized" in r["msg"].lower()

    def test_non_200_fails_module(self):
        """When metrics endpoint returns non-2xx, module fails."""
        gludd_metrics = self._load_module()
        mock = self._run(
            gludd_metrics,
            {},
            client_responses={"get_response": {}, "get_status": 502},
        )

        assert len(mock.captured_fail) == 1
        r = mock.captured_fail[0]
        assert r["failed"] is True
        assert "502" in r["msg"]

    def test_idempotency(self):
        """Running metrics twice produces identical output for same mock data."""
        gludd_metrics = self._load_module()
        results = []

        for _ in range(2):
            mock = self._run(
                gludd_metrics,
                {},
                client_responses={
                    "get_response": {"agents": [{"id": "a1"}], "total_agents": 1, "running_agents": 1},
                    "get_status": 200,
                },
            )
            r = mock.captured_exit[0]
            results.append(
                {"total_agents": r["ansible_facts"]["gludd_metrics"]["total_agents"], "changed": r["changed"]}
            )

        assert results[0] == results[1], "idempotent runs must produce identical output"

    def test_agent_id_passed_as_query_param(self):
        """When agent_id is set, it is passed as a query parameter."""
        gludd_metrics = self._load_module()

        class CapturingFakeClient:
            get_path = None
            get_params = None

            def __init__(self, base_url="", psk="", timeout=30):
                pass

            def reachable(self):
                return True

            def get(self, path, params=None):
                CapturingFakeClient.get_path = path
                CapturingFakeClient.get_params = params
                return {"agents": [], "total_agents": 0, "_status": 200}

        MockAnsible = _mock_ansible_module({"agent_id": "agent-42"})

        with (
            patch.object(gludd_metrics, "AnsibleModule", MockAnsible),
            patch.object(gludd_metrics, "GluddClient", CapturingFakeClient),
        ):
            MockAnsible.reset()
            with contextlib.suppress(SystemExit):
                gludd_metrics.main()

        assert CapturingFakeClient.get_path == "/api/metrics"
        assert CapturingFakeClient.get_params == {"agent_id": "agent-42"}

    def test_project_id_passed_as_query_param(self):
        """When project_id is set, it is passed as a query parameter."""
        gludd_metrics = self._load_module()

        class CapturingFakeClient:
            get_path = None
            get_params = None

            def __init__(self, base_url="", psk="", timeout=30):
                pass

            def reachable(self):
                return True

            def get(self, path, params=None):
                CapturingFakeClient.get_path = path
                CapturingFakeClient.get_params = params
                return {"agents": [], "total_agents": 0, "_status": 200}

        MockAnsible = _mock_ansible_module({"project_id": "alpha"})

        with (
            patch.object(gludd_metrics, "AnsibleModule", MockAnsible),
            patch.object(gludd_metrics, "GluddClient", CapturingFakeClient),
        ):
            MockAnsible.reset()
            with contextlib.suppress(SystemExit):
                gludd_metrics.main()

        assert CapturingFakeClient.get_path == "/api/metrics"
        assert CapturingFakeClient.get_params == {"project_id": "alpha"}
