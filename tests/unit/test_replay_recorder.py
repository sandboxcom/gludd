"""Structural tests for replay/recorder.py — RunRecorder."""

from __future__ import annotations

from pathlib import Path

from general_ludd.filestore.store import FileStore
from general_ludd.replay.recorder import RunRecorder


def _store(tmp_path: Path, name: str) -> FileStore:
    return FileStore(root_path=str(tmp_path / name))


class TestRunRecorder:
    def test_default_constructs(self):
        r = RunRecorder()
        assert r._store is not None

    def test_record_and_replay_empty(self, tmp_path: Path):
        store = _store(tmp_path, "replays-structural-test-1")
        r = RunRecorder(store=store)
        assert r.replay("nonexistent-run") == []

    def test_list_runs_empty(self, tmp_path: Path):
        store = _store(tmp_path, "replays-structural-test-2")
        r = RunRecorder(store=store)
        assert r.list_runs() == []

    def test_record_single_event(self, tmp_path: Path):
        store = _store(tmp_path, "replays-structural-test-3")
        r = RunRecorder(store=store)
        r.record("test-run", {"type": "prompt", "content": "hello"})
        events = r.replay("test-run")
        assert len(events) == 1
        assert events[0]["type"] == "prompt"

    def test_list_runs_after_record(self, tmp_path: Path):
        store = _store(tmp_path, "replays-structural-test-4")
        r = RunRecorder(store=store)
        r.record("run-a", {"type": "x"})
        assert "run-a" in r.list_runs()

    def test_record_multiple_events_ordered(self, tmp_path: Path):
        store = _store(tmp_path, "replays-structural-test-5")
        r = RunRecorder(store=store)
        for i in range(3):
            r.record("seq-run", {"seq": i})
        events = r.replay("seq-run")
        assert [e["seq"] for e in events] == [0, 1, 2]

    def test_explicit_store(self, tmp_path: Path):
        store = _store(tmp_path, "replays-structural-test-6")
        r = RunRecorder(store=store)
        assert r._store is store
