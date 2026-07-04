"""Unit tests for G10: RunRecorder — per-run replay recording."""

from general_ludd.replay.recorder import RunRecorder


class TestRunRecorder:
    def test_constructor_default_storage(self) -> None:
        recorder = RunRecorder()
        assert recorder._storage_path is None
        assert recorder._runs == {}

    def test_constructor_custom_storage_path(self) -> None:
        recorder = RunRecorder(storage_path="/tmp/runs")
        assert recorder._storage_path == "/tmp/runs"

    def test_record_does_not_raise(self) -> None:
        recorder = RunRecorder()
        recorder.record("run-001", {"type": "prompt", "content": "hello"})

    def test_replay_empty_run(self) -> None:
        recorder = RunRecorder()
        assert recorder.replay("nonexistent") == []

    def test_record_and_replay_stub(self) -> None:
        recorder = RunRecorder()
        events = [
            {"type": "prompt", "content": "hello"},
            {"type": "response", "content": "world"},
        ]
        for e in events:
            recorder.record("run-001", e)
        assert recorder.replay("run-001") == []
