"""Behavioral coverage for the administrative make router."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.make import register


def _result() -> SimpleNamespace:
    return SimpleNamespace(
        target="gate",
        exit_code=0,
        success=True,
        duration_s=1.25,
        stdout_tail="ok",
        stderr_tail="",
        timed_out=False,
        oom_killed=False,
        error=None,
        phases=["lint", "test"],
    )


def test_admin_make_forwards_all_non_streaming_options() -> None:
    app = FastAPI()
    runner = Mock()
    runner.run.return_value = _result()
    with patch("general_ludd.commands.make.MakeRunner", return_value=runner) as factory:
        register(app, {})
        response = TestClient(app).post(
            "/admin/make",
            json={
                "target": "gate",
                "extra_args": ["PYTEST_ARGS=-q"],
                "cwd": "/workspace",
                "timeout_s": 42,
                "env_extra": {"TERM": "xterm-256color"},
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "target": "gate",
        "exit_code": 0,
        "success": True,
        "duration_s": 1.25,
        "stdout_tail": "ok",
        "stderr_tail": "",
        "timed_out": False,
        "oom_killed": False,
        "error": None,
        "phases": ["lint", "test"],
    }
    factory.assert_called_once_with(cwd="/workspace", default_timeout_s=42)
    runner.run.assert_called_once_with(
        "gate",
        extra_args=["PYTEST_ARGS=-q"],
        timeout_s=42,
        env_extra={"TERM": "xterm-256color"},
    )


def test_admin_make_streaming_invokes_progress_callback() -> None:
    app = FastAPI()
    runner = Mock()

    def run(_target: str, **kwargs: object) -> SimpleNamespace:
        callback = kwargs["stream_callback"]
        assert callable(callback)
        callback("test")
        return _result()

    runner.run.side_effect = run
    with patch("general_ludd.commands.make.MakeRunner", return_value=runner) as factory:
        register(app, {})
        response = TestClient(app).post(
            "/admin/make",
            json={"target": "gate", "stream": True},
        )

    assert response.status_code == 200
    factory.assert_called_once_with(cwd=None, default_timeout_s=300)
    assert runner.run.call_args.kwargs["stream"] is True

