"""Tests for the worker /jobs/execute tool_calls_detected warn-block.

These tests verify the conservative "option a" detection path added to
worker/app.py: when the model emits tool-call JSON the worker parses it,
logs a WARNING (no dispatcher is wired), and surfaces the parsed calls in
the response dict under ``tool_calls_detected`` — but never executes them.

Three assertion groups:
  (1) model response containing tool-call JSON → non-empty tool_calls_detected
  (2) plain-text model response              → empty tool_calls_detected
  (3) over-cap tool calls                    → dropped (tool_calls_detected empty)
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from general_ludd.routers.dispatch import MAX_CALLS_PER_REQUEST
from general_ludd.worker.app import create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A work_type that is in _GENERATION_WORK_TYPES so the gateway branch fires.
_GENERATION_WORK_TYPE = "code"


def _make_job(**kwargs: Any) -> dict[str, Any]:
    """Build a minimal JobSpec payload that passes validation."""
    base: dict[str, Any] = {
        "job_id": "TOOLTEST001",
        "playbook": "noop.yml",
        "queue": "default",
        "work_type": _GENERATION_WORK_TYPE,
    }
    base.update(kwargs)
    return base


def _make_tool_response(calls: list[dict[str, Any]]) -> str:
    """Encode a list of tool-call dicts as the JSON the model would emit."""
    return json.dumps({"tool_calls": calls})


def _single_tool_call(name: str = "do_something", kind: str = "skill") -> dict[str, Any]:
    return {"kind": kind, "name": name, "args": {"target": "x"}}


def _build_runner_mock() -> MagicMock:
    """Return a runner mock that succeeds without touching the filesystem."""
    runner = MagicMock()
    runner.list_playbooks.return_value = ["noop.yml"]
    runner.prepare_job_dirs.return_value = {
        "root": "/tmp/fakejob",
        "env": "/tmp/fakejob/env",
        "project": "/tmp/fakejob/project",
        "inventory": "/tmp/fakejob/inventory",
        "artifacts": "/tmp/fakejob/artifacts",
    }
    runner.write_vars.return_value = None
    runner.run_playbook.return_value = {
        "status": "successful",
        "rc": 0,
        "events": [],
        "artifacts": [],
    }
    return runner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_runner() -> Any:
    """Prevent module-level _runner singleton from leaking between tests."""
    import general_ludd.worker.app as worker_module
    original = worker_module._runner
    yield
    worker_module._runner = original


# ---------------------------------------------------------------------------
# (1) Tool-call JSON in model response → non-empty tool_calls_detected
# ---------------------------------------------------------------------------

class TestToolCallsDetected:
    def test_tool_call_json_yields_nonempty_detected(self) -> None:
        """A model response with tool-call JSON must surface in tool_calls_detected."""
        tool_response = _make_tool_response([_single_tool_call("write_file")])
        runner = _build_runner_mock()
        gw = MagicMock()

        with (
            patch("general_ludd.worker.app.get_runner", return_value=runner),
            patch("general_ludd.worker.app.get_playbook_registry", return_value={"noop.yml"}),
            patch(
                "general_ludd.worker.app._invoke_gateway_for_job",
                return_value=tool_response,
            ),
        ):
            app = create_app(gateway=gw)
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/jobs/execute", json=_make_job(job_id="TOOLTEST001"))

        assert resp.status_code == 200, resp.text
        data = resp.json()
        detected = data.get("tool_calls_detected", [])
        assert len(detected) == 1, f"Expected 1 tool call detected, got: {detected}"
        assert detected[0]["name"] == "write_file"
        assert detected[0]["kind"] == "skill"

    def test_multiple_tool_calls_all_detected(self) -> None:
        """Multiple tool calls in one response all appear in tool_calls_detected."""
        calls = [
            _single_tool_call("step_one", "role"),
            _single_tool_call("step_two", "skill"),
        ]
        tool_response = _make_tool_response(calls)
        runner = _build_runner_mock()
        gw = MagicMock()

        with (
            patch("general_ludd.worker.app.get_runner", return_value=runner),
            patch("general_ludd.worker.app.get_playbook_registry", return_value={"noop.yml"}),
            patch(
                "general_ludd.worker.app._invoke_gateway_for_job",
                return_value=tool_response,
            ),
        ):
            app = create_app(gateway=gw)
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/jobs/execute", json=_make_job(job_id="TOOLTEST002"))

        assert resp.status_code == 200
        detected = resp.json()["tool_calls_detected"]
        assert len(detected) == 2
        names = {d["name"] for d in detected}
        assert names == {"step_one", "step_two"}


# ---------------------------------------------------------------------------
# (2) Plain-text model response → empty tool_calls_detected
# ---------------------------------------------------------------------------

class TestPlainTextResponse:
    def test_plain_text_yields_empty_detected(self) -> None:
        """A plain-text model response must yield an empty tool_calls_detected list."""
        runner = _build_runner_mock()
        gw = MagicMock()

        with (
            patch("general_ludd.worker.app.get_runner", return_value=runner),
            patch("general_ludd.worker.app.get_playbook_registry", return_value={"noop.yml"}),
            patch(
                "general_ludd.worker.app._invoke_gateway_for_job",
                return_value="Here is the generated plan: step 1, step 2.",
            ),
        ):
            app = create_app(gateway=gw)
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/jobs/execute", json=_make_job(job_id="TOOLTEST003"))

        assert resp.status_code == 200
        data = resp.json()
        assert data["tool_calls_detected"] == [], (
            f"Expected empty list for plain-text response, got: {data['tool_calls_detected']}"
        )

    def test_none_model_response_yields_empty_detected(self) -> None:
        """When model is not called (gateway=None) tool_calls_detected must be empty."""
        runner = _build_runner_mock()
        # gateway=None → model is never invoked → model_response stays None
        with (
            patch("general_ludd.worker.app.get_runner", return_value=runner),
            patch("general_ludd.worker.app.get_playbook_registry", return_value={"noop.yml"}),
        ):
            app = create_app(gateway=None)
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/jobs/execute", json=_make_job(job_id="TOOLTEST004"))

        assert resp.status_code == 200
        assert resp.json()["tool_calls_detected"] == []


# ---------------------------------------------------------------------------
# (3) Over-cap tool calls → dropped (tool_calls_detected stays empty)
# ---------------------------------------------------------------------------

class TestOverCapDropped:
    def test_over_cap_calls_are_dropped(self) -> None:
        """When tool calls exceed MAX_CALLS_PER_REQUEST they are ALL dropped."""
        # Build MAX_CALLS_PER_REQUEST + 1 tool calls to exceed the cap
        many_calls = [
            _single_tool_call(f"call_{i}") for i in range(MAX_CALLS_PER_REQUEST + 1)
        ]
        tool_response = _make_tool_response(many_calls)
        runner = _build_runner_mock()
        gw = MagicMock()

        with (
            patch("general_ludd.worker.app.get_runner", return_value=runner),
            patch("general_ludd.worker.app.get_playbook_registry", return_value={"noop.yml"}),
            patch(
                "general_ludd.worker.app._invoke_gateway_for_job",
                return_value=tool_response,
            ),
        ):
            app = create_app(gateway=gw)
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/jobs/execute", json=_make_job(job_id="TOOLTEST005"))

        assert resp.status_code == 200, resp.text
        data = resp.json()
        # Over-cap path: all calls dropped, detected list must be EMPTY
        assert data["tool_calls_detected"] == [], (
            f"Over-cap calls should be dropped, got: {data['tool_calls_detected']}"
        )

    def test_exactly_at_cap_calls_are_detected(self) -> None:
        """Exactly MAX_CALLS_PER_REQUEST calls are within the cap and must be detected."""
        at_cap_calls = [
            _single_tool_call(f"call_{i}") for i in range(MAX_CALLS_PER_REQUEST)
        ]
        tool_response = _make_tool_response(at_cap_calls)
        runner = _build_runner_mock()
        gw = MagicMock()

        with (
            patch("general_ludd.worker.app.get_runner", return_value=runner),
            patch("general_ludd.worker.app.get_playbook_registry", return_value={"noop.yml"}),
            patch(
                "general_ludd.worker.app._invoke_gateway_for_job",
                return_value=tool_response,
            ),
        ):
            app = create_app(gateway=gw)
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/jobs/execute", json=_make_job(job_id="TOOLTEST006"))

        assert resp.status_code == 200
        detected = resp.json()["tool_calls_detected"]
        assert len(detected) == MAX_CALLS_PER_REQUEST, (
            f"Expected {MAX_CALLS_PER_REQUEST} detected at cap, got: {len(detected)}"
        )


# ---------------------------------------------------------------------------
# (4) Dispatcher wired → tool calls are EXECUTED (not just detected)
# ---------------------------------------------------------------------------

class TestDispatcherWired:
    """When a DynamicDispatcher IS wired into the worker, model tool calls
    are executed via ``dispatch_all`` instead of being silently dropped."""

    def test_dispatched_calls_are_executed(self) -> None:
        from unittest.mock import AsyncMock

        from general_ludd.dispatch.dynamic_dispatcher import DispatchResult

        tool_response = _make_tool_response([_single_tool_call("my_skill")])
        runner = _build_runner_mock()
        gw = MagicMock()
        fake_dispatcher = MagicMock()
        fake_dispatcher.dispatch_all = AsyncMock(
            return_value=[
                DispatchResult(ok=True, kind="skill", name="my_skill", output="done"),
            ]
        )

        with (
            patch("general_ludd.worker.app.get_runner", return_value=runner),
            patch("general_ludd.worker.app.get_playbook_registry", return_value={"noop.yml"}),
            patch(
                "general_ludd.worker.app._invoke_gateway_for_job",
                return_value=tool_response,
            ),
        ):
            app = create_app(gateway=gw, dispatcher=fake_dispatcher)
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/jobs/execute", json=_make_job(job_id="DISPATCH001"))

        assert resp.status_code == 200, resp.text
        data = resp.json()
        # Dispatcher was called
        fake_dispatcher.dispatch_all.assert_called_once()
        # Results appear in response
        results = data.get("tool_dispatch_results", [])
        assert len(results) == 1
        assert results[0]["ok"] is True
        assert results[0]["name"] == "my_skill"
        # Detected list stays empty because calls were EXECUTED, not just detected
        assert data["tool_calls_detected"] == []

    def test_dispatch_error_surfaces_in_results(self) -> None:
        from unittest.mock import AsyncMock

        from general_ludd.dispatch.dynamic_dispatcher import DispatchResult

        tool_response = _make_tool_response([_single_tool_call("bad_skill")])
        runner = _build_runner_mock()
        gw = MagicMock()
        fake_dispatcher = MagicMock()
        fake_dispatcher.dispatch_all = AsyncMock(
            return_value=[
                DispatchResult(ok=False, kind="skill", name="bad_skill", error="not found"),
            ]
        )

        with (
            patch("general_ludd.worker.app.get_runner", return_value=runner),
            patch("general_ludd.worker.app.get_playbook_registry", return_value={"noop.yml"}),
            patch(
                "general_ludd.worker.app._invoke_gateway_for_job",
                return_value=tool_response,
            ),
        ):
            app = create_app(gateway=gw, dispatcher=fake_dispatcher)
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/jobs/execute", json=_make_job(job_id="DISPATCH002"))

        assert resp.status_code == 200, resp.text
        data = resp.json()
        results = data["tool_dispatch_results"]
        assert len(results) == 1
        assert results[0]["ok"] is False
        assert "not found" in results[0]["error"]
