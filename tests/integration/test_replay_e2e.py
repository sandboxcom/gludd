"""Integration/e2e tests for G10 per-run replay.

Proves RunRecorder works end-to-end: recording events, replaying them
in order, listing runs, and handling edge cases.
"""

from __future__ import annotations

from pathlib import Path

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


class TestRunRecorderE2E:
    def test_record_and_replay_single_event(self, recorder: RunRecorder) -> None:
        run_id = "run-001"
        event = {"type": "prompt", "content": "Write a function", "timestamp": "2025-01-01T00:00:00Z"}

        recorder.record(run_id, event)
        events = recorder.replay(run_id)

        assert len(events) == 1
        assert events[0]["type"] == "prompt"
        assert events[0]["content"] == "Write a function"

    def test_record_multiple_events_ordered(self, recorder: RunRecorder) -> None:
        run_id = "run-002"
        events = [
            {"seq": 0, "type": "prompt", "content": "task 1"},
            {"seq": 1, "type": "model_call", "model": "sonnet"},
            {"seq": 2, "type": "tool_call", "tool": "read_file"},
            {"seq": 3, "type": "response", "text": "done"},
        ]

        for ev in events:
            recorder.record(run_id, ev)

        replayed = recorder.replay(run_id)
        assert len(replayed) == 4
        for i, ev in enumerate(replayed):
            assert ev["seq"] == i

    def test_replay_nonexistent_run(self, recorder: RunRecorder) -> None:
        events = recorder.replay("nonexistent-run")
        assert events == []

    def test_list_runs_empty(self, recorder: RunRecorder) -> None:
        runs = recorder.list_runs()
        assert runs == []

    def test_list_runs_after_recording(self, recorder: RunRecorder) -> None:
        recorder.record("run-a", {"type": "start"})
        recorder.record("run-b", {"type": "start"})

        runs = recorder.list_runs()
        assert len(runs) == 2
        assert "run-a" in runs
        assert "run-b" in runs

    def test_event_sequence_numbers_increment(self, recorder: RunRecorder) -> None:
        run_id = "run-seq"
        for i in range(10):
            recorder.record(run_id, {"index": i})

        events = recorder.replay(run_id)
        assert len(events) == 10
        for i, ev in enumerate(events):
            assert ev["index"] == i

    def test_recorder_defaults_to_dot_gludd_replays(self, tmp_path: Path) -> None:
        recorder = RunRecorder()
        assert recorder._store is not None
        assert ".gludd/replays" in recorder._store.root_path

    def test_record_complex_event_structure(self, recorder: RunRecorder) -> None:
        run_id = "run-complex"
        event = {
            "type": "generate",
            "model": "sonnet",
            "prompt": "Fix this bug",
            "response": "Here's the fix",
            "tool_calls": [
                {"tool": "read_file", "args": {"path": "src/main.py"}},
                {"tool": "edit_file", "args": {"path": "src/main.py", "old": "bug", "new": "fix"}},
            ],
            "cost_usd": 0.005,
            "tokens": {"input": 500, "output": 200},
        }

        recorder.record(run_id, event)
        replayed = recorder.replay(run_id)

        assert len(replayed) == 1
        r = replayed[0]
        assert r["type"] == "generate"
        assert r["cost_usd"] == 0.005
        assert len(r["tool_calls"]) == 2

    def test_multiple_runs_isolation(self, recorder: RunRecorder) -> None:
        recorder.record("run-1", {"v": 1})
        recorder.record("run-2", {"v": 2})
        recorder.record("run-1", {"v": 11})

        r1 = recorder.replay("run-1")
        r2 = recorder.replay("run-2")

        assert len(r1) == 2
        assert len(r2) == 1
        assert [e["v"] for e in r1] == [1, 11]
        assert r2[0]["v"] == 2

    def test_replay_preserves_json_types(self, recorder: RunRecorder) -> None:
        run_id = "run-types"
        event = {
            "int_val": 42,
            "float_val": 3.14,
            "bool_val": True,
            "null_val": None,
            "list_val": [1, 2, 3],
            "nested": {"a": {"b": "c"}},
        }

        recorder.record(run_id, event)
        replayed = recorder.replay(run_id)

        assert len(replayed) == 1
        r = replayed[0]
        assert r["int_val"] == 42
        assert r["float_val"] == 3.14
        assert r["bool_val"] is True
        assert r["null_val"] is None
        assert r["list_val"] == [1, 2, 3]
        assert r["nested"] == {"a": {"b": "c"}}

    def test_store_persistence_across_recorder_instances(
        self, replay_dir: Path, recorder: RunRecorder
    ) -> None:
        recorder.record("persist-test", {"msg": "hello"})

        new_store = FileStore(root_path=str(replay_dir))
        new_recorder = RunRecorder(store=new_store)

        events = new_recorder.replay("persist-test")
        assert len(events) == 1
        assert events[0]["msg"] == "hello"
