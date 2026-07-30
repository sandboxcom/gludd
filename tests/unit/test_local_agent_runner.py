"""Behavioral tests for the isolated local-agent subprocess protocol."""

from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import patch

import general_ludd.execution.local_agent_runner as local_agent_runner
from general_ludd.execution import tool_loop
from general_ludd.models import gateway as gateway_module


def test_run_local_executes_one_controller_loop(monkeypatch) -> None:
    calls: dict[str, Any] = {}
    fake_gateway = object()

    class FakeLoop:
        def __init__(self, *, model_gateway: object, max_iterations: int) -> None:
            calls["gateway"] = model_gateway
            calls["max_iterations"] = max_iterations

        async def run_with_tools(
            self,
            job: object,
            system_prompt: str,
            prompt: str,
        ) -> str:
            calls["job"] = job
            calls["system_prompt"] = system_prompt
            calls["prompt"] = prompt
            return "isolated answer"

    monkeypatch.setattr(gateway_module, "ModelGateway", lambda: fake_gateway)
    monkeypatch.setattr(tool_loop, "ToolCallLoop", FakeLoop)

    result = local_agent_runner.run_local(
        prompt="diagnose this host",
        system_prompt="You are the local operator.",
        model_profile="operator",
        max_iterations=3,
    )

    assert result == {
        "failed": False,
        "changed": False,
        "answer": "isolated answer",
        "tool_calls": [],
        "usage": {},
        "iterations": 1,
    }
    assert calls["gateway"] is fake_gateway
    assert calls["max_iterations"] == 3
    assert calls["system_prompt"] == "You are the local operator."
    assert calls["prompt"] == "diagnose this host"
    assert calls["job"].prompt_text == "diagnose this host"
    assert calls["job"].model_profile == "operator"


def test_run_local_returns_structured_import_failure(monkeypatch) -> None:
    def unavailable_gateway() -> object:
        raise ImportError("controller package unavailable")

    monkeypatch.setattr(gateway_module, "ModelGateway", unavailable_gateway)

    result = local_agent_runner.run_local("prompt", "system", None, 1)

    assert result == {
        "failed": True,
        "changed": False,
        "msg": (
            "general_ludd not importable for local run: "
            "controller package unavailable"
        ),
    }


def test_main_dispatches_one_json_request() -> None:
    expected = {
        "failed": False,
        "changed": False,
        "answer": "answer",
        "tool_calls": [],
        "usage": {},
        "iterations": 1,
    }
    request = {
        "prompt": "hello",
        "system_prompt": "system",
        "model_profile": "profile",
        "max_iterations": 4,
    }
    stdout = io.StringIO()
    with (
        patch.object(local_agent_runner, "run_local", return_value=expected) as run_local,
        patch.object(local_agent_runner.sys, "stdin", io.StringIO(json.dumps(request))),
        patch.object(local_agent_runner.sys, "stdout", stdout),
    ):
        return_code = local_agent_runner.main()

    assert return_code == 0
    assert json.loads(stdout.getvalue()) == expected
    run_local.assert_called_once_with(
        prompt="hello",
        system_prompt="system",
        model_profile="profile",
        max_iterations=4,
    )


def test_main_returns_structured_error_for_invalid_request() -> None:
    stdout = io.StringIO()
    with (
        patch.object(local_agent_runner.sys, "stdin", io.StringIO("{}")),
        patch.object(local_agent_runner.sys, "stdout", stdout),
    ):
        return_code = local_agent_runner.main()

    result = json.loads(stdout.getvalue())
    assert return_code == 0
    assert result["failed"] is True
    assert result["changed"] is False
    assert result["msg"].startswith("invalid local agent request:")
