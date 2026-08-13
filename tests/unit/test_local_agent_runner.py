"""Behavioral contract for the isolated local agent process boundary."""

from __future__ import annotations

import io
import json
import sys
from typing import Any

import pytest

from general_ludd.execution import local_agent_runner


def _invoke_main(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
    *,
    raw: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    def fake_run_local(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "failed": False,
            "changed": False,
            "answer": "ok",
            "tool_calls": [],
            "usage": {},
            "iterations": 1,
        }

    stdin = io.StringIO(str(payload) if raw else json.dumps(payload))
    stdout = io.StringIO()
    monkeypatch.setattr(local_agent_runner, "run_local", fake_run_local)
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)

    assert local_agent_runner.main() == 0
    return json.loads(stdout.getvalue()), calls


def test_run_local_wires_the_tool_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    from general_ludd.execution import tool_loop
    from general_ludd.models import gateway
    from general_ludd.schemas import job

    observed: dict[str, Any] = {}

    class FakeGateway:
        pass

    class FakeLoop:
        def __init__(self, *, model_gateway: object, max_iterations: int) -> None:
            observed["gateway"] = model_gateway
            observed["max_iterations"] = max_iterations

        async def run_with_tools(
            self,
            job_spec: object,
            system_prompt: str,
            prompt: str,
        ) -> str:
            observed["job"] = job_spec
            observed["system_prompt"] = system_prompt
            observed["prompt"] = prompt
            return "isolated answer"

    class FakeJob:
        def __init__(self, **kwargs: Any) -> None:
            observed["job_kwargs"] = kwargs

    monkeypatch.setattr(gateway, "ModelGateway", FakeGateway)
    monkeypatch.setattr(tool_loop, "ToolCallLoop", FakeLoop)
    monkeypatch.setattr(job, "JobSpec", FakeJob)

    result = local_agent_runner.run_local(
        prompt="do the work",
        system_prompt="stay bounded",
        model_profile="profile-a",
        max_iterations=7,
    )

    assert result == {
        "failed": False,
        "changed": False,
        "answer": "isolated answer",
        "tool_calls": [],
        "usage": {},
        "iterations": 1,
    }
    assert isinstance(observed["gateway"], FakeGateway)
    assert observed["max_iterations"] == 7
    assert observed["system_prompt"] == "stay bounded"
    assert observed["prompt"] == "do the work"
    assert observed["job_kwargs"]["prompt_text"] == "do the work"
    assert observed["job_kwargs"]["model_profile"] == "profile-a"


def test_run_local_reports_import_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "general_ludd.execution.tool_loop", None)

    result = local_agent_runner.run_local("prompt", "system", None, 1)

    assert result["failed"] is True
    assert result["changed"] is False
    assert "not importable" in result["msg"]


def test_run_local_reports_runtime_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from general_ludd.models import gateway

    def fail_gateway() -> None:
        raise RuntimeError("gateway unavailable")

    monkeypatch.setattr(gateway, "ModelGateway", fail_gateway)

    result = local_agent_runner.run_local("prompt", "system", None, 1)

    assert result == {
        "failed": True,
        "changed": False,
        "msg": "local agent run failed: gateway unavailable",
    }


def test_main_forwards_a_valid_request(monkeypatch: pytest.MonkeyPatch) -> None:
    result, calls = _invoke_main(
        monkeypatch,
        {
            "prompt": "task",
            "system_prompt": "rules",
            "model_profile": "profile-b",
            "max_iterations": "5",
        },
    )

    assert result["failed"] is False
    assert calls == [
        {
            "prompt": "task",
            "system_prompt": "rules",
            "model_profile": "profile-b",
            "max_iterations": 5,
        }
    ]


@pytest.mark.parametrize(
    "max_iterations",
    [0, -1, 101, True, float("inf")],
)
def test_main_rejects_unsafe_iteration_limits(
    monkeypatch: pytest.MonkeyPatch,
    max_iterations: object,
) -> None:
    result, calls = _invoke_main(
        monkeypatch,
        {
            "prompt": "task",
            "system_prompt": "rules",
            "model_profile": None,
            "max_iterations": max_iterations,
        },
    )

    assert result["failed"] is True
    assert result["changed"] is False
    assert result["msg"].startswith("invalid local agent request:")
    assert calls == []


@pytest.mark.parametrize(
    "payload, raw",
    [
        ({"prompt": "missing fields"}, False),
        ("{not-json", True),
        ([], False),
    ],
)
def test_main_returns_json_for_malformed_requests(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
    raw: bool,
) -> None:
    result, calls = _invoke_main(monkeypatch, payload, raw=raw)

    assert result["failed"] is True
    assert result["changed"] is False
    assert result["msg"].startswith("invalid local agent request:")
    assert calls == []
