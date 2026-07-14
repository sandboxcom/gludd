"""Structural tests for replay/recorder.py — RunRecorder."""

from __future__ import annotations

from general_ludd.filestore.store import FileStore
from general_ludd.replay.recorder import RunRecorder


class TestRunRecorder:
    def test_default_constructs(self):
        r = RunRecorder()
        assert r._store is not None

    def test_record_and_replay_empty(self):
        store = FileStore(root_path=".gludd/replays-structural-test-1")
        r = RunRecorder(store=store)
        assert r.replay("nonexistent-run") == []

    def test_list_runs_empty(self):
        store = FileStore(root_path=".gludd/replays-structural-test-2")
        r = RunRecorder(store=store)
        assert r.list_runs() == []

    def test_record_single_event(self):
        store = FileStore(root_path=".gludd/replays-structural-test-3")
        r = RunRecorder(store=store)
        r.record("test-run", {"type": "prompt", "content": "hello"})
        events = r.replay("test-run")
        assert len(events) == 1
        assert events[0]["type"] == "prompt"

    def test_list_runs_after_record(self):
        store = FileStore(root_path=".gludd/replays-structural-test-4")
        r = RunRecorder(store=store)
        r.record("run-a", {"type": "x"})
        assert "run-a" in r.list_runs()

    def test_record_multiple_events_ordered(self):
        store = FileStore(root_path=".gludd/replays-structural-test-5")
        r = RunRecorder(store=store)
        for i in range(3):
            r.record("seq-run", {"seq": i})
        events = r.replay("seq-run")
        assert [e["seq"] for e in events] == [0, 1, 2]

    def test_explicit_store(self):
        store = FileStore(root_path=".gludd/replays-structural-test-6")
        r = RunRecorder(store=store)
        assert r._store is store
