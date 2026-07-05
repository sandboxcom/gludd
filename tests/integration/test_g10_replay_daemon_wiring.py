"""Integration tests for G10 per-run replay daemon wiring.

Proves RunRecorder directly: record(), replay(), list_runs(). Also tests
that EventLoop can accept a RunRecorder and calls record() during dispatch.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from general_ludd.filestore.store import FileStore
from general_ludd.replay.recorder import RunRecorder


@pytest.fixture
def replay_dir(tmp_path: Path) -> Path:
    replay_root = tmp_path / "replays"
    replay_root.mkdir(parents=True, exist_ok=True)
    return replay_root


@pytest.fixture
def store(replay_dir: Path) -> FileStore:
    return FileStore(root_path=str(replay_dir))


@pytest.fixture
def recorder(store: FileStore) -> RunRecorder:
    return RunRecorder(store=store)


class TestRunRecorderDirect:
    def test_record_and_replay_returns_same_data(self, recorder: RunRecorder) -> None:
        run_id = "run-direct-001"
        event = {
            "type": "dispatch",
            "model": "sonnet",
            "prompt": "Write a test",
            "timestamp_iso": "2026-01-01T00:00:00Z",
        }

        recorder.record(run_id, event)
        events = recorder.replay(run_id)

        assert len(events) == 1
        assert events[0]["type"] == "dispatch"
        assert events[0]["model"] == "sonnet"
        assert events[0]["prompt"] == "Write a test"
        assert events[0]["timestamp_iso"] == "2026-01-01T00:00:00Z"

    def test_list_runs_after_recording_multiple(self, recorder: RunRecorder) -> None:
        assert recorder.list_runs() == []

        recorder.record("run-a", {"type": "start"})
        recorder.record("run-b", {"type": "start"})
        recorder.record("run-c", {"type": "start"})

        runs = recorder.list_runs()
        assert len(runs) == 3
        assert "run-a" in runs
        assert "run-b" in runs
        assert "run-c" in runs

    def test_multiple_concurrent_runs_tracked_independently(self, recorder: RunRecorder) -> None:
        recorder.record("concurrent-1", {"seq": 0, "data": "first run event 0"})
        recorder.record("concurrent-2", {"seq": 0, "data": "second run event 0"})
        recorder.record("concurrent-1", {"seq": 1, "data": "first run event 1"})
        recorder.record("concurrent-2", {"seq": 1, "data": "second run event 1"})
        recorder.record("concurrent-1", {"seq": 2, "data": "first run event 2"})

        r1 = recorder.replay("concurrent-1")
        r2 = recorder.replay("concurrent-2")

        assert len(r1) == 3
        assert len(r2) == 2
        assert [e["seq"] for e in r1] == [0, 1, 2]
        assert [e["seq"] for e in r2] == [0, 1]
        r1_data = [e["data"] for e in r1]
        r2_data = [e["data"] for e in r2]
        assert "first run event 0" in r1_data
        assert "second run event 0" in r2_data

    def test_event_recording_dispatch_completion_tool_calls(self, recorder: RunRecorder) -> None:
        run_id = "run-events-001"

        dispatch_event = {
            "type": "dispatch",
            "agent_id": "agent-42",
            "model": "sonnet",
            "prompt": "Fix the bug in auth.py",
            "timestamp": 1700000000.0,
        }
        recorder.record(run_id, dispatch_event)

        tool_call_event = {
            "type": "tool_call",
            "tool": "read_file",
            "args": {"path": "src/auth.py"},
            "timestamp": 1700000001.0,
        }
        recorder.record(run_id, tool_call_event)

        completion_event = {
            "type": "completion",
            "agent_id": "agent-42",
            "result": "Bug fixed — added null check",
            "cost_usd": 0.003,
            "tokens": {"input": 200, "output": 150},
            "timestamp": 1700000005.0,
        }
        recorder.record(run_id, completion_event)

        events = recorder.replay(run_id)
        assert len(events) == 3
        types = [e["type"] for e in events]
        assert types == ["dispatch", "tool_call", "completion"]

        dispatch = events[0]
        assert dispatch["agent_id"] == "agent-42"
        assert dispatch["model"] == "sonnet"

        tool_call = events[1]
        assert tool_call["tool"] == "read_file"
        assert tool_call["args"] == {"path": "src/auth.py"}

        completion = events[2]
        assert completion["result"] == "Bug fixed — added null check"
        assert completion["cost_usd"] == 0.003
        assert completion["tokens"]["input"] == 200
        assert completion["tokens"]["output"] == 150

    def test_replay_nonexistent_run_returns_empty(self, recorder: RunRecorder) -> None:
        events = recorder.replay("no-such-run")
        assert events == []

    def test_events_preserved_in_order(self, recorder: RunRecorder) -> None:
        run_id = "run-ordered"
        for i in range(50):
            recorder.record(run_id, {"index": i, "value": f"event-{i}"})

        events = recorder.replay(run_id)
        assert len(events) == 50
        for i, ev in enumerate(events):
            assert ev["index"] == i
            assert ev["value"] == f"event-{i}"


class TestRunRecorderWithEventLoop:
    def test_eventloop_accepts_run_recorder_and_records(self) -> None:
        from general_ludd.event_loop.loop import EventLoop

        store = FileStore(root_path=".gludd/replays")
        run_recorder = RunRecorder(store=store)

        loop = EventLoop(
            run_recorder=run_recorder,
        )
        assert loop._run_recorder is run_recorder
        assert isinstance(loop._run_recorder, RunRecorder)

        run_id = "loop-test-001"
        event = {"type": "dispatch", "model": "sonnet", "prompt": "test"}
        loop._run_recorder.record(run_id, event)

        events = loop._run_recorder.replay(run_id)
        assert len(events) == 1
        assert events[0]["type"] == "dispatch"
        assert events[0]["model"] == "sonnet"

    def test_eventloop_instance_stores_run_recorder(self, recorder: RunRecorder) -> None:
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop(run_recorder=recorder)
        assert loop._run_recorder is recorder
        assert hasattr(loop._run_recorder, "record")
        assert hasattr(loop._run_recorder, "replay")
        assert hasattr(loop._run_recorder, "list_runs")

    @pytest.mark.asyncio
    async def test_runrecorder_wired_to_daemon_app_state(self) -> None:
        from general_ludd.daemon import create_daemon_app

        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)

        run_recorder = getattr(app.state, "_run_recorder", None)
        assert run_recorder is not None
        assert isinstance(run_recorder, RunRecorder)
        assert hasattr(run_recorder, "record")
        assert hasattr(run_recorder, "replay")
        assert hasattr(run_recorder, "list_runs")

    def test_runrecorder_default_store_path(self) -> None:
        recorder = RunRecorder()
        assert recorder._store is not None
        assert ".gludd/replays" in recorder._store.root_path

    def test_runrecorder_persistence_across_instances(
        self, replay_dir: Path, recorder: RunRecorder
    ) -> None:
        run_id = "persist-cross-instance"
        event = {"type": "start", "config": {"model": "sonnet", "temperature": 0.7}}
        recorder.record(run_id, event)

        new_store = FileStore(root_path=str(replay_dir))
        new_recorder = RunRecorder(store=new_store)
        events = new_recorder.replay(run_id)

        assert len(events) == 1
        assert events[0]["type"] == "start"
        assert events[0]["config"]["model"] == "sonnet"
        assert events[0]["config"]["temperature"] == 0.7
