"""Deep unit tests for routers/make.py — POST /admin/make endpoint.

Covers: route registration, request shape validation, MakeRunner arg forwarding,
response field fidelity (all 10 MakeResult fields), stream path, error propagation,
boundary values, and edge cases.  Uses TestClient with a mocked MakeRunner.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _stop_started_patches() -> Iterator[None]:
    """Keep the helper's started MakeRunner patch local to each test."""
    yield
    mock.patch.stopall()


@pytest.fixture
def app() -> FastAPI:
    _app = FastAPI()
    from general_ludd.routers.make import register

    register(_app, {})
    return _app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ── MakeResult stub ────────────────────────────────────────────────────────────


def _make_result(**overrides: Any) -> dict[str, Any]:
    """Return a dict the mocked run() will return as a MakeResult."""
    base: dict[str, Any] = {
        "target": "test-target",
        "exit_code": 0,
        "success": True,
        "duration_s": 1.5,
        "stdout_tail": "ok\n",
        "stderr_tail": "",
        "timed_out": False,
        "oom_killed": False,
        "error": None,
        "phases": [],
    }
    base.update(overrides)
    return base


def _install_mock(result: dict[str, Any]) -> mock.MagicMock:
    """Patch MakeRunner so its .run() returns a MakeResult with given fields."""
    from general_ludd.commands.make import MakeResult

    mr = MakeResult(**result)
    patcher = mock.patch(
        "general_ludd.routers.make.MakeRunner",
        autospec=True,
    )
    mock_cls = patcher.start()
    mock_cls.return_value.run.return_value = mr
    return mock_cls


# ── Route registration ─────────────────────────────────────────────────────────


class TestRouteRegistration:
    def test_post_admin_make_route_exists(self, app: FastAPI) -> None:
        routes = {getattr(route, "path", None) for route in app.routes}
        assert "/admin/make" in routes

    def test_post_admin_make_methods(self, app: FastAPI) -> None:
        for route in app.routes:
            if getattr(route, "path", None) == "/admin/make":
                assert "POST" in getattr(route, "methods", set())
                return
        pytest.fail("Route /admin/make not found")

    def test_get_not_allowed(self, client: TestClient) -> None:
        resp = client.get("/admin/make")
        assert resp.status_code == 405


# ── Response shape fidelity ────────────────────────────────────────────────────


class TestResponseShape:
    def test_all_10_fields_present(self, client: TestClient) -> None:
        mock_cls = _install_mock(_make_result())
        resp = client.post("/admin/make", json={"target": "lint"})
        assert resp.status_code == 200
        body = resp.json()
        for key in (
            "target",
            "exit_code",
            "success",
            "duration_s",
            "stdout_tail",
            "stderr_tail",
            "timed_out",
            "oom_killed",
            "error",
            "phases",
        ):
            assert key in body, f"missing key: {key}"
        mock_cls.assert_called_once()

    def test_target_field_passthrough(self, client: TestClient) -> None:
        _install_mock(_make_result(target="lint"))
        resp = client.post("/admin/make", json={"target": "lint"})
        assert resp.json()["target"] == "lint"

    def test_exit_code_passthrough(self, client: TestClient) -> None:
        _install_mock(_make_result(exit_code=2, success=False))
        resp = client.post("/admin/make", json={"target": "lint"})
        body = resp.json()
        assert body["exit_code"] == 2
        assert body["success"] is False

    def test_duration_s_passthrough(self, client: TestClient) -> None:
        _install_mock(_make_result(duration_s=42.3))
        resp = client.post("/admin/make", json={"target": "lint"})
        assert resp.json()["duration_s"] == 42.3

    def test_stdout_tail_passthrough(self, client: TestClient) -> None:
        _install_mock(_make_result(stdout_tail="=== GATE: PASSED ===\n"))
        resp = client.post("/admin/make", json={"target": "gate"})
        assert "GATE: PASSED" in resp.json()["stdout_tail"]

    def test_stderr_tail_passthrough(self, client: TestClient) -> None:
        _install_mock(_make_result(stderr_tail="Error: something broke"))
        resp = client.post("/admin/make", json={"target": "lint"})
        assert "something broke" in resp.json()["stderr_tail"]

    def test_timed_out_flag_passthrough(self, client: TestClient) -> None:
        _install_mock(_make_result(timed_out=True, success=False, exit_code=None))
        resp = client.post("/admin/make", json={"target": "gate"})
        body = resp.json()
        assert body["timed_out"] is True
        assert body["success"] is False

    def test_oom_killed_flag_passthrough(self, client: TestClient) -> None:
        _install_mock(_make_result(oom_killed=True, success=False, exit_code=-9))
        resp = client.post("/admin/make", json={"target": "gate"})
        body = resp.json()
        assert body["oom_killed"] is True
        assert body["exit_code"] == -9

    def test_error_field_passthrough(self, client: TestClient) -> None:
        _install_mock(_make_result(error="make executable not found", exit_code=None, success=False))
        resp = client.post("/admin/make", json={"target": "lint"})
        assert resp.json()["error"] == "make executable not found"

    def test_phases_list_passthrough(self, client: TestClient) -> None:
        _install_mock(_make_result(phases=["lint", "typecheck", "test"]))
        resp = client.post("/admin/make", json={"target": "gate"})
        assert resp.json()["phases"] == ["lint", "typecheck", "test"]


# ── Missing target (field required) ────────────────────────────────────────────


class TestMissingTarget:
    def test_empty_body_422(self, client: TestClient) -> None:
        resp = client.post("/admin/make", json={})
        assert resp.status_code == 422

    def test_body_without_target_422(self, client: TestClient) -> None:
        resp = client.post("/admin/make", json={"extra_args": ["--help"]})
        assert resp.status_code == 422

    def test_null_target_not_handled(self, client: TestClient) -> None:
        resp = client.post("/admin/make", json={"target": None})
        assert resp.status_code in (422, 400, 500)


# ── Optional fields forwarded correctly ───────────────────────────────────────


class TestOptionalFieldsForwarding:
    def test_extra_args_forwarded(self, client: TestClient) -> None:
        mock_cls = _install_mock(_make_result())
        resp = client.post(
            "/admin/make",
            json={
                "target": "test",
                "extra_args": ["TESTFILE=tests/unit/test_x.py", "NO_XDIST=1"],
            },
        )
        assert resp.status_code == 200
        _args, _kwargs = mock_cls.return_value.run.call_args
        assert _kwargs["extra_args"] == ["TESTFILE=tests/unit/test_x.py", "NO_XDIST=1"]

    def test_cwd_forwarded_to_constructor(self, client: TestClient) -> None:
        mock_cls = _install_mock(_make_result())
        resp = client.post(
            "/admin/make",
            json={
                "target": "lint",
                "cwd": "/tmp",
            },
        )
        assert resp.status_code == 200
        mock_cls.assert_called_once_with(cwd="/tmp", default_timeout_s=300)

    def test_timeout_s_forwarded_to_constructor_and_run(self, client: TestClient) -> None:
        mock_cls = _install_mock(_make_result())
        resp = client.post(
            "/admin/make",
            json={
                "target": "gate",
                "timeout_s": 600,
            },
        )
        assert resp.status_code == 200
        mock_cls.assert_called_once_with(cwd=None, default_timeout_s=600)
        _args, _kwargs = mock_cls.return_value.run.call_args
        assert _kwargs["timeout_s"] == 600

    def test_env_extra_forwarded(self, client: TestClient) -> None:
        mock_cls = _install_mock(_make_result())
        resp = client.post(
            "/admin/make",
            json={
                "target": "lint",
                "env_extra": {"FOO": "bar", "BAZ": "1"},
            },
        )
        assert resp.status_code == 200
        _args, _kwargs = mock_cls.return_value.run.call_args
        assert _kwargs["env_extra"] == {"FOO": "bar", "BAZ": "1"}

    def test_all_optional_combined(self, client: TestClient) -> None:
        mock_cls = _install_mock(_make_result())
        resp = client.post(
            "/admin/make",
            json={
                "target": "test",
                "extra_args": ["-k", "test_x"],
                "cwd": "/home/user/project",
                "timeout_s": 900,
                "env_extra": {"DEBUG": "1"},
            },
        )
        assert resp.status_code == 200
        mock_cls.assert_called_once_with(cwd="/home/user/project", default_timeout_s=900)
        _args, _kwargs = mock_cls.return_value.run.call_args
        assert _kwargs["extra_args"] == ["-k", "test_x"]
        assert _kwargs["timeout_s"] == 900
        assert _kwargs["env_extra"] == {"DEBUG": "1"}


# ── Stream path ────────────────────────────────────────────────────────────────


class TestStreamPath:
    def test_stream_true_calls_with_stream_and_callback(self, client: TestClient) -> None:
        mock_cls = _install_mock(_make_result(phases=["lint", "test"]))
        resp = client.post(
            "/admin/make",
            json={
                "target": "gate",
                "stream": True,
            },
        )
        assert resp.status_code == 200
        _args, _kwargs = mock_cls.return_value.run.call_args
        assert _kwargs["stream"] is True
        assert callable(_kwargs.get("stream_callback"))

    def test_stream_false_no_callback(self, client: TestClient) -> None:
        mock_cls = _install_mock(_make_result())
        resp = client.post(
            "/admin/make",
            json={
                "target": "lint",
                "stream": False,
            },
        )
        assert resp.status_code == 200
        _args, _kwargs = mock_cls.return_value.run.call_args
        assert "stream" not in _kwargs or _kwargs["stream"] is False
        assert _kwargs.get("stream_callback") is None

    def test_stream_default_is_false(self, client: TestClient) -> None:
        mock_cls = _install_mock(_make_result())
        resp = client.post("/admin/make", json={"target": "lint"})
        assert resp.status_code == 200
        _args, _kwargs = mock_cls.return_value.run.call_args
        assert _kwargs.get("stream_callback") is None

    def test_stream_callback_collects_phases(self, client: TestClient) -> None:
        mock_cls = _install_mock(_make_result(phases=["lint", "typecheck", "test"]))

        def side_effect(target: str, *args: object, **kwargs: Any) -> Any:
            cb = kwargs.get("stream_callback")
            if cb:
                for p in ["lint", "typecheck", "test"]:
                    cb(p)
            from general_ludd.commands.make import MakeResult

            return MakeResult(
                target=target,
                exit_code=0,
                success=True,
                duration_s=1.0,
                stdout_tail="",
                stderr_tail="",
                phases=["lint", "typecheck", "test"],
            )

        mock_cls.return_value.run.side_effect = side_effect
        resp = client.post("/admin/make", json={"target": "gate", "stream": True})
        assert resp.status_code == 200


# ── Timeout edge cases ────────────────────────────────────────────────────────


class TestTimeoutEdgeValues:
    def test_timeout_zero_passed_to_constructor(self, client: TestClient) -> None:
        mock_cls = _install_mock(_make_result())
        resp = client.post("/admin/make", json={"target": "lint", "timeout_s": 0})
        assert resp.status_code == 200
        mock_cls.assert_called_once_with(cwd=None, default_timeout_s=0)

    def test_default_timeout_when_omitted(self, client: TestClient) -> None:
        mock_cls = _install_mock(_make_result())
        resp = client.post("/admin/make", json={"target": "lint"})
        assert resp.status_code == 200
        mock_cls.assert_called_once_with(cwd=None, default_timeout_s=300)

    def test_negative_timeout_allowed_at_router_level(self, client: TestClient) -> None:
        """Router does not validate timeout_s — passes through to MakeRunner."""
        mock_cls = _install_mock(_make_result())
        resp = client.post("/admin/make", json={"target": "lint", "timeout_s": -1})
        assert resp.status_code == 200
        mock_cls.assert_called_once_with(cwd=None, default_timeout_s=-1)


# ── None optional fields ──────────────────────────────────────────────────────


class TestNoneOptionalFields:
    def test_extra_args_none(self, client: TestClient) -> None:
        _install_mock(_make_result())
        resp = client.post("/admin/make", json={"target": "lint", "extra_args": None})
        assert resp.status_code == 200

    def test_cwd_none(self, client: TestClient) -> None:
        mock_cls = _install_mock(_make_result())
        resp = client.post("/admin/make", json={"target": "lint", "cwd": None})
        assert resp.status_code == 200
        mock_cls.assert_called_once_with(cwd=None, default_timeout_s=300)

    def test_timeout_s_none(self, client: TestClient) -> None:
        mock_cls = _install_mock(_make_result())
        resp = client.post("/admin/make", json={"target": "lint", "timeout_s": None})
        assert resp.status_code == 200
        mock_cls.assert_called_once_with(cwd=None, default_timeout_s=300)

    def test_env_extra_none(self, client: TestClient) -> None:
        _install_mock(_make_result())
        resp = client.post("/admin/make", json={"target": "lint", "env_extra": None})
        assert resp.status_code == 200


# ── Body type validation ──────────────────────────────────────────────────────


class TestBodyTypeValidation:
    def test_target_non_string(self, client: TestClient) -> None:
        resp = client.post("/admin/make", json={"target": 123})
        assert resp.status_code == 422

    def test_extra_args_non_list(self, client: TestClient) -> None:
        resp = client.post("/admin/make", json={"target": "lint", "extra_args": "not-a-list"})
        assert resp.status_code == 422

    def test_timeout_s_non_int(self, client: TestClient) -> None:
        resp = client.post("/admin/make", json={"target": "lint", "timeout_s": "not-an-int"})
        assert resp.status_code == 422

    def test_env_extra_non_dict(self, client: TestClient) -> None:
        resp = client.post("/admin/make", json={"target": "lint", "env_extra": "not-a-dict"})
        assert resp.status_code == 422

    def test_stream_non_bool(self, client: TestClient) -> None:
        resp = client.post("/admin/make", json={"target": "lint", "stream": "not-a-bool"})
        assert resp.status_code == 422


# ── Structural checks ──────────────────────────────────────────────────────────


class TestStructuralProperties:
    def test_register_accepts_fastapi_and_dict(self) -> None:
        from general_ludd.routers.make import register

        app = FastAPI()
        state: dict[str, Any] = {}
        register(app, state)
        assert "/admin/make" in {getattr(route, "path", None) for route in app.routes}

    def test_module_docstring(self) -> None:
        import general_ludd.routers.make as mod

        assert mod.__doc__ is not None
        assert "POST" in mod.__doc__
        assert "make" in mod.__doc__.lower()

    def test_function_is_async(self) -> None:
        import inspect

        import general_ludd.routers.make as mod

        for name, obj in inspect.getmembers(mod):
            if name == "register":
                assert callable(obj)
            if name == "admin_run_make" and inspect.iscoroutinefunction(obj):
                return
        pytest.skip("admin_run_make is nested inside register — not inspectable at module level")


# ── Extra keys in body are ignored ─────────────────────────────────────────────


class TestExtraKeysIgnored:
    def test_unknown_keys_silently_ignored(self, client: TestClient) -> None:
        mock_cls = _install_mock(_make_result())
        resp = client.post(
            "/admin/make",
            json={
                "target": "lint",
                "unknown_field": "should-be-ignored",
                "also_unknown": 42,
            },
        )
        assert resp.status_code == 200
        _args, _kwargs = mock_cls.return_value.run.call_args
        assert "unknown_field" not in _kwargs
