"""Tests for BadCallSituationStore — persisting blocked call situations."""
import tempfile
from pathlib import Path

import pytest

from general_ludd.execution.situation_store import (
    BadCallSituation,
    BadCallSituationStore,
)


class TestBadCallSituationStore:
    """Tests for persisting, loading, and querying situations."""

    @pytest.fixture
    def tmp_dir(self):
        with tempfile.TemporaryDirectory() as d:
            yield Path(d)

    def test_save_and_load_roundtrip(self, tmp_dir):
        store = BadCallSituationStore(base_dir=tmp_dir)
        situation = BadCallSituation(
            tool_name="read_file",
            tool_args={"path": "/etc/shadow"},
            classification="irrelevant",
            reason="not relevant to code task",
            task_excerpt="write a function to sort arrays",
            recent_calls=[
                {"tool_name": "list_directory", "args": {"path": "/"}},
                {"tool_name": "read_file", "args": {"path": "/etc/passwd"}},
            ],
            work_type="code",
        )
        path = store.save(situation)
        assert path.exists()
        loaded = store.load(path.name)
        assert loaded is not None
        assert loaded.tool_name == "read_file"
        assert loaded.classification == "irrelevant"
        assert loaded.reason == "not relevant to code task"
        assert len(loaded.recent_calls) == 2

    def test_save_creates_parent_directory(self, tmp_dir):
        store = BadCallSituationStore(base_dir=tmp_dir / "nested" / "deep")
        situation = BadCallSituation(tool_name="test", tool_args={}, classification="redundant", reason="test")
        path = store.save(situation)
        assert path.exists()

    def test_load_nonexistent_returns_none(self, tmp_dir):
        store = BadCallSituationStore(base_dir=tmp_dir)
        result = store.load("nonexistent.json")
        assert result is None

    def test_list_recent_returns_newest_first(self, tmp_dir):
        store = BadCallSituationStore(base_dir=tmp_dir)
        for i in range(5):
            s = BadCallSituation(
                tool_name=f"tool_{i}",
                tool_args={},
                classification="redundant",
                reason=f"test {i}",
                timestamp=float(1000 + i),
            )
            store.save(s)

        recent = store.list_recent(limit=3)
        assert len(recent) == 3
        assert recent[0].tool_name == "tool_4"
        assert recent[1].tool_name == "tool_3"
        assert recent[2].tool_name == "tool_2"

    def test_list_by_classification(self, tmp_dir):
        store = BadCallSituationStore(base_dir=tmp_dir)
        store.save(BadCallSituation(tool_name="a", tool_args={}, classification="redundant", reason="test"))
        store.save(BadCallSituation(tool_name="b", tool_args={}, classification="error_loop", reason="test"))
        store.save(BadCallSituation(tool_name="c", tool_args={}, classification="redundant", reason="test"))

        redundant = store.list_by_classification("redundant")
        assert len(redundant) == 2
        error_loops = store.list_by_classification("error_loop")
        assert len(error_loops) == 1

    def test_list_by_tool_name(self, tmp_dir):
        store = BadCallSituationStore(base_dir=tmp_dir)
        store.save(BadCallSituation(tool_name="read_file", tool_args={}, classification="redundant", reason="test"))
        store.save(BadCallSituation(tool_name="write_file", tool_args={}, classification="redundant", reason="test"))
        store.save(BadCallSituation(tool_name="read_file", tool_args={}, classification="error_loop", reason="test"))

        read_situations = store.list_by_tool("read_file")
        assert len(read_situations) == 2

    def test_tampered_file_rejected(self, tmp_dir):
        store = BadCallSituationStore(base_dir=tmp_dir)
        situation = BadCallSituation(tool_name="test", tool_args={}, classification="redundant", reason="test")
        path = store.save(situation)

        path.write_text("tampered content")
        loaded = store.load(path.name)
        assert loaded is None

    def test_missing_mac_file_returns_none(self, tmp_dir):
        store = BadCallSituationStore(base_dir=tmp_dir)
        situation = BadCallSituation(tool_name="test", tool_args={}, classification="redundant", reason="test")
        path = store.save(situation)

        mac_path = Path(str(path) + ".mac")
        mac_path.unlink()
        loaded = store.load(path.name)
        assert loaded is None

    def test_prune_removes_old_situations(self, tmp_dir):
        store = BadCallSituationStore(base_dir=tmp_dir)
        for i in range(10):
            s = BadCallSituation(
                tool_name=f"tool_{i}",
                tool_args={},
                classification="redundant",
                reason=f"test {i}",
                timestamp=float(1000 + i),
            )
            store.save(s)

        store.prune(max_age_seconds=0)
        remaining = store.list_recent(limit=100)
        assert len(remaining) <= 5

    def test_count_returns_total(self, tmp_dir):
        store = BadCallSituationStore(base_dir=tmp_dir)
        for i in range(3):
            store.save(BadCallSituation(tool_name=f"tool_{i}", tool_args={}, classification="redundant", reason="test"))
        assert store.count() == 3
