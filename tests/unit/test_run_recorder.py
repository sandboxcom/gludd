"""Unit tests for G10: RunRecorder — per-run replay recording."""

import json
import tempfile

from general_ludd.filestore.store import FileStore
from general_ludd.replay.recorder import RunRecorder


class TestRunRecorder:
    def test_record_replay_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            recorder = RunRecorder(store=store)
            recorder.record("run-1", {"type": "prompt", "content": "hello"})
            result = recorder.replay("run-1")
            assert len(result) == 1
            assert result[0] == {"type": "prompt", "content": "hello"}

    def test_record_replay_roundtrip_with_default_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = RunRecorder(store=FileStore(root_path=tmp))
            recorder.record("run-x", {"type": "tool_call", "tool": "bash"})
            result = recorder.replay("run-x")
            assert len(result) == 1
            assert result[0] == {"type": "tool_call", "tool": "bash"}

    def test_replay_returns_nonexistent_run_as_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            recorder = RunRecorder(store=store)
            result = recorder.replay("nonexistent")
            assert result == []

    def test_multiple_events_sorted_by_record_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            recorder = RunRecorder(store=store)
            events = [
                {"type": "prompt", "idx": 0},
                {"type": "call", "idx": 1},
                {"type": "response", "idx": 2},
            ]
            for e in events:
                recorder.record("run-2", e)
            result = recorder.replay("run-2")
            assert len(result) == 3
            assert result == events

    def test_list_runs_returns_empty_for_no_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            recorder = RunRecorder(store=store)
            assert recorder.list_runs() == []

    def test_list_runs_returns_run_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            recorder = RunRecorder(store=store)
            recorder.record("run-a", {"type": "start"})
            recorder.record("run-b", {"type": "start"})
            recorder.record("run-a", {"type": "end"})
            assert recorder.list_runs() == ["run-a", "run-b"]

    def test_record_writes_serializable_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            recorder = RunRecorder(store=store)
            recorder.record("run-3", {"type": "prompt", "content": "hello"})
            data = store.read_text("runs/run-3/events/0.json")
            assert json.loads(data) == {"type": "prompt", "content": "hello"}
