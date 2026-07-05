"""Verify RunRecorder is wired into the daemon app state and exposed via /api/replays."""

from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI

from general_ludd.replay.recorder import RunRecorder
from general_ludd.routers.replays import register


class TestRunRecorderDaemonWiring:
    def test_recorder_registered_in_app_state(self) -> None:
        """RunRecorder is instantiated and stored in app.state._run_recorder."""
        app = FastAPI()
        recorder = RunRecorder()
        app.state._run_recorder = recorder

        assert app.state._run_recorder is recorder
        assert isinstance(app.state._run_recorder, RunRecorder)

    def test_api_replays_returns_list(self) -> None:
        """GET /api/replays returns a list (empty when no runs recorded)."""
        app = FastAPI()
        recorder = RunRecorder()
        app.state._run_recorder = recorder
        register(app, {})

        # Use the FastAPI TestClient (Starlette) to call the endpoint
        from starlette.testclient import TestClient

        client = TestClient(app)
        response = client.get("/api/replays")
        assert response.status_code == 200
        assert isinstance(response.json(), list)
        assert response.json() == []

    def test_api_replays_missing_recorder_returns_empty(self) -> None:
        """GET /api/replays returns [] when no RunRecorder is attached to app."""
        app = FastAPI()
        register(app, {})

        from starlette.testclient import TestClient

        client = TestClient(app)
        response = client.get("/api/replays")
        assert response.status_code == 200
        assert response.json() == []

    def test_recorder_passed_to_event_loop(self) -> None:
        """RunRecorder is a named kwarg in the EventLoop constructor."""
        from general_ludd.event_loop.loop import EventLoop

        recorder = RunRecorder()

        with patch(
            "general_ludd.event_loop.loop.EventLoop.__init__",
            return_value=None,
        ) as mock_init:
            EventLoop(run_recorder=recorder)
            mock_init.assert_called_once()
            kwargs = mock_init.call_args.kwargs
            assert "run_recorder" in kwargs
            assert kwargs["run_recorder"] is recorder

    def test_recorder_passed_to_agent_dispatcher(self) -> None:
        """RunRecorder is a named kwarg in the AgentDispatcher constructor."""
        from general_ludd.agents.dispatcher import AgentDispatcher
        from general_ludd.agents.registry import AgentRegistry

        recorder = RunRecorder()
        registry = AgentRegistry()

        with patch(
            "general_ludd.agents.dispatcher.AgentDispatcher.__init__",
            return_value=None,
        ) as mock_init:
            AgentDispatcher(registry=registry, run_recorder=recorder)
            mock_init.assert_called_once()
            kwargs = mock_init.call_args.kwargs
            assert "run_recorder" in kwargs
            assert kwargs["run_recorder"] is recorder
