"""Tests for graph_checkpointer — LangGraph checkpoint persistence layer."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from general_ludd.execution.graph_checkpointer import TickCheckpointer, get_checkpointer


class TestTickCheckpointerEphemeral:
    """Graceful degradation: no saver -> ephemeral dict."""

    def test_put_get_roundtrip(self) -> None:
        cp = TickCheckpointer(saver=None)
        state = {"phase": "load_config", "todos": [1, 2, 3]}
        cp.put("tick_1", state)
        assert cp.get("tick_1") == state

    def test_get_missing_returns_none(self) -> None:
        cp = TickCheckpointer(saver=None)
        assert cp.get("nonexistent") is None

    def test_list_single_checkpoint(self) -> None:
        cp = TickCheckpointer(saver=None)
        cp.put("tick_1", {"a": 1})
        result = cp.list("tick_1")
        assert len(result) == 1
        assert result[0] == {"a": 1}

    def test_list_empty(self) -> None:
        cp = TickCheckpointer(saver=None)
        assert cp.list("nonexistent") == []

    def test_delete_thread(self) -> None:
        cp = TickCheckpointer(saver=None)
        cp.put("tick_1", {"a": 1})
        cp.delete_thread("tick_1")
        assert cp.get("tick_1") is None

    def test_prune_clears_ephemeral(self) -> None:
        cp = TickCheckpointer(saver=None)
        cp.put("tick_1", {"a": 1})
        cp.put("tick_2", {"b": 2})
        pruned = cp.prune(max_age_hours=0)
        assert pruned == 0
        assert cp.get("tick_1") is None
        assert cp.get("tick_2") is None

    def test_available_false_without_saver(self) -> None:
        cp = TickCheckpointer(saver=None)
        assert cp.available is False

    def test_overwrite_replaces_state(self) -> None:
        cp = TickCheckpointer(saver=None)
        cp.put("tick_1", {"phase": "start"})
        cp.put("tick_1", {"phase": "end"})
        assert cp.get("tick_1") == {"phase": "end"}


class TestTickCheckpointerWithMockSaver:
    """InMemorySaver-backed checkpointer."""

    @staticmethod
    def _mock_saver(storage: dict[str, Any] | None = None) -> MagicMock:
        saver = MagicMock()
        saver.storage = storage if storage is not None else {}
        return saver

    def test_put_calls_saver(self) -> None:
        saver = self._mock_saver()
        cp = TickCheckpointer(saver=saver)
        cp.put("tick_1", {"phase": "dispatch"})
        saver.put.assert_called_once()

    def test_get_returns_none_when_no_tuple(self) -> None:
        saver = self._mock_saver()
        saver.get_tuple.return_value = None
        cp = TickCheckpointer(saver=saver)
        assert cp.get("tick_1") is None

    def test_get_parses_checkpoint(self) -> None:
        import json as _json

        saver = self._mock_saver()
        result = MagicMock()
        result.checkpoint = {
            "channel_values": {
                "gludd_state": {
                    "tick_id": "tick_1",
                    "state": _json.dumps({"phase": "done"}),
                    "ts": time.time(),
                }
            }
        }
        saver.get_tuple.return_value = result
        cp = TickCheckpointer(saver=saver)
        assert cp.get("tick_1") == {"phase": "done"}

    def test_get_handles_bad_json(self) -> None:
        saver = self._mock_saver()
        result = MagicMock()
        result.checkpoint = {"channel_values": {"gludd_state": {"state": "not-json{{{"}}}
        saver.get_tuple.return_value = result
        cp = TickCheckpointer(saver=saver)
        assert cp.get("tick_1") is None

    def test_get_handles_missing_state_key(self) -> None:
        saver = self._mock_saver()
        result = MagicMock()
        result.checkpoint = {}
        saver.get_tuple.return_value = result
        cp = TickCheckpointer(saver=saver)
        assert cp.get("tick_1") is None

    def test_list_iterates_checkpoints(self) -> None:
        import json as _json

        saver = self._mock_saver()
        item1 = MagicMock()
        item1.checkpoint = {"channel_values": {"gludd_state": {"state": _json.dumps({"a": 1})}}}
        item2 = MagicMock()
        item2.checkpoint = {"channel_values": {"gludd_state": {"state": _json.dumps({"b": 2})}}}
        saver.list.return_value = [item1, item2]
        cp = TickCheckpointer(saver=saver)
        result = cp.list("tick_1")
        assert len(result) == 2
        assert result[0] == {"a": 1}
        assert result[1] == {"b": 2}

    def test_available_true_with_saver(self) -> None:
        saver = self._mock_saver()
        cp = TickCheckpointer(saver=saver)
        assert cp.available is True


class TestGetCheckpointer:
    """Factory function tests."""

    def test_returns_ephemeral_when_no_langgraph(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import general_ludd.execution.graph_checkpointer as mod

        monkeypatch.setattr(mod, "_IMPORT_ERROR", "mock import error")
        cp = get_checkpointer()
        assert cp.available is False
        cp.put("t", {"x": 1})
        assert cp.get("t") == {"x": 1}

    def test_returns_inmemory_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import general_ludd.execution.graph_checkpointer as mod

        monkeypatch.setattr(mod, "_IMPORT_ERROR", None)
        monkeypatch.setattr(mod, "_HAS_SQLITE_SAVER", False)
        cp = get_checkpointer()
        assert cp.available is True

    def test_skips_sqlite_for_non_sqlite_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import general_ludd.execution.graph_checkpointer as mod

        monkeypatch.setattr(mod, "_IMPORT_ERROR", None)
        monkeypatch.setattr(mod, "_HAS_SQLITE_SAVER", True)
        cp = get_checkpointer(db_url="postgresql://localhost/db")
        assert cp.available is True


class TestIntegrationRoundtrip:
    """End-to-end: checkpointer lifecycle with InMemorySaver."""

    def test_save_load_multiple_ticks(self) -> None:
        from langgraph.checkpoint.memory import InMemorySaver

        cp = TickCheckpointer(saver=InMemorySaver())
        cp.put("tick_1_v1", {"phase": "ack", "count": 1})
        cp.put("tick_1_v2", {"phase": "process", "count": 2})
        cp.put("tick_2", {"phase": "done", "count": 3})

        first = cp.get("tick_1_v1")
        assert first is not None
        assert first["count"] == 1

        second = cp.get("tick_1_v2")
        assert second is not None
        assert second["count"] == 2

        history = cp.list("tick_1_v1")
        assert len(history) >= 1

        other = cp.get("tick_2")
        assert other is not None
        assert other["count"] == 3

    def test_prune_clears_old_storage(self) -> None:
        from langgraph.checkpoint.memory import InMemorySaver

        saver = InMemorySaver()
        cp = TickCheckpointer(saver=saver)
        cp.put("tick_old", {"ts": 1})
        cp.put("tick_recent", {"ts": time.time() + 3600})
        pruned = cp.prune(max_age_hours=0)
        assert pruned >= 0
        result = cp.get("tick_old")
        assert result is None
