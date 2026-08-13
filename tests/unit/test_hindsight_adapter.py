"""Tests for HindsightMemoryAdapter — wrapping Hindsight TEMPR with fallback."""

from __future__ import annotations

import os
import threading
from unittest.mock import MagicMock, patch

from general_ludd.memory.hindsight_adapter import (
    HindsightMemoryAdapter,
    _InMemoryStore,
)


class TestInMemoryStore:
    """Fallback store retains / recalls with keyword scoring."""

    def test_retain_returns_id(self) -> None:
        store = _InMemoryStore()
        rid = store.retain("hello world")
        assert rid.startswith("mem_")

    def test_retain_and_recall_exact_match(self) -> None:
        store = _InMemoryStore()
        store.retain("the quick brown fox jumps over the lazy dog")
        results = store.recall("brown fox")
        assert len(results) >= 1
        assert any("brown fox" in r["content"] for r in results)

    def test_recall_empty_on_no_match(self) -> None:
        store = _InMemoryStore()
        store.retain("apples and oranges")
        results = store.recall("zyzzx nonexistent")
        assert results == []

    def test_search_aliases_recall(self) -> None:
        store = _InMemoryStore()
        store.retain("deploy to production")
        results = store.search("deploy")
        assert len(results) >= 1

    def test_hyphenated_session_identifier_does_not_cross_match(self) -> None:
        store = _InMemoryStore()
        store.retain("session sess-5 record", {"session": "sess-5"})
        store.retain("session sess-0 record", {"session": "sess-0"})

        results = store.search("sess-5", top_k=10)

        assert [result["content"] for result in results] == ["session sess-5 record"]

    def test_retain_with_metadata(self) -> None:
        store = _InMemoryStore()
        store.retain("content", {"source": "test", "priority": 1})
        results = store.recall("test")
        meta_match = [r for r in results if "source" in str(r.get("metadata", {}))]
        assert len(meta_match) >= 1

    def test_top_k_truncation(self) -> None:
        store = _InMemoryStore()
        for i in range(10):
            store.retain(f"document about thing {i}")
        results = store.recall("thing", top_k=3)
        assert len(results) <= 3

    def test_thread_safety(self) -> None:
        store = _InMemoryStore()
        errors: list[Exception] = []

        def _insert(n: int) -> None:
            try:
                for i in range(50):
                    store.retain(f"thread {n} document {i}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_insert, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        results = store.recall("document", top_k=50)
        assert len(results) >= 10

    def test_score_ordering(self) -> None:
        store = _InMemoryStore()
        store.retain("marginal mention of query")
        store.retain("query is the central topic of this document")
        results = store.recall("query")
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)


class TestAdapterDisabled:
    """When HINDSIGHT_ENABLED is false / not set, adapter uses fallback."""

    def test_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            HindsightMemoryAdapter.reset_instance()
            adapter = HindsightMemoryAdapter.get_instance()
            assert adapter.is_connected is False
            health = adapter.health_check()
            assert health["backend"] == "fallback"
            assert health["enabled"] is False

    def test_disabled_explicit_env(self) -> None:
        with patch.dict(os.environ, {"HINDSIGHT_ENABLED": "false"}, clear=True):
            HindsightMemoryAdapter.reset_instance()
            adapter = HindsightMemoryAdapter.get_instance()
            assert adapter.is_connected is False

    def test_retain_falls_back_when_disabled(self) -> None:
        with patch.dict(os.environ, {"HINDSIGHT_ENABLED": "0"}, clear=True):
            HindsightMemoryAdapter.reset_instance()
            adapter = HindsightMemoryAdapter.get_instance()
            rid = adapter.retain("content", {"key": "val"})
            assert rid.startswith("mem_")

    def test_recall_falls_back_when_disabled(self) -> None:
        with patch.dict(os.environ, {"HINDSIGHT_ENABLED": "0"}, clear=True):
            HindsightMemoryAdapter.reset_instance()
            adapter = HindsightMemoryAdapter.get_instance()
            adapter.retain("hello world")
            results = adapter.recall("hello")
            assert len(results) >= 1

    def test_reflect_falls_back_when_disabled(self) -> None:
        with patch.dict(os.environ, {"HINDSIGHT_ENABLED": "0"}, clear=True):
            HindsightMemoryAdapter.reset_instance()
            adapter = HindsightMemoryAdapter.get_instance()
            adapter.retain("important task completed")
            answer = adapter.reflect("what was completed")
            assert isinstance(answer, str)
            assert len(answer) > 0

    def test_create_memory_bank_falls_back(self) -> None:
        with patch.dict(os.environ, {"HINDSIGHT_ENABLED": "0"}, clear=True):
            HindsightMemoryAdapter.reset_instance()
            adapter = HindsightMemoryAdapter.get_instance()
            result = adapter.create_memory_bank(
                "test-bank",
                mission="test mission",
                directives=["be helpful"],
                disposition="friendly",
            )
            assert result["backend"] == "fallback"
            assert result["name"] == "test-bank"


class TestAdapterWithMockClient:
    """When HINDSIGHT_ENABLED=true and client available, adapter uses HTTP."""

    def test_retain_via_mock(self) -> None:
        mock_client = MagicMock()
        mock_client.observe.return_value = "obs_001"

        with patch.dict(os.environ, {"HINDSIGHT_ENABLED": "true"}, clear=True):
            HindsightMemoryAdapter.reset_instance()
            adapter = HindsightMemoryAdapter()
            adapter._client = mock_client
            adapter._connected = True

            rid = adapter.retain("content", {"src": "test"})
            assert rid == "obs_001"
            mock_client.observe.assert_called_once_with(
                content="content", metadata={"src": "test"},
            )

    def test_recall_via_mock(self) -> None:
        mock_client = MagicMock()
        mock_client.recall.return_value = [
            {"id": "1", "content": "stuff", "score": 0.95},
        ]

        with patch.dict(os.environ, {"HINDSIGHT_ENABLED": "true"}, clear=True):
            HindsightMemoryAdapter.reset_instance()
            adapter = HindsightMemoryAdapter()
            adapter._client = mock_client
            adapter._connected = True

            results = adapter.recall("query", top_k=3)
            assert len(results) == 1
            assert results[0]["score"] == 0.95
            mock_client.recall.assert_called_once_with(query="query", top_k=3)

    def test_search_via_mock(self) -> None:
        mock_client = MagicMock()
        mock_client.search.return_value = [{"id": "s1", "content": "result"}]

        with patch.dict(os.environ, {"HINDSIGHT_ENABLED": "true"}, clear=True):
            HindsightMemoryAdapter.reset_instance()
            adapter = HindsightMemoryAdapter()
            adapter._client = mock_client
            adapter._connected = True

            results = adapter.search("q")
            assert len(results) == 1
            mock_client.search.assert_called_once_with(query="q", top_k=5)

    def test_reflect_via_mock(self) -> None:
        mock_client = MagicMock()
        mock_client.reflect.return_value = "synthesized answer"

        with patch.dict(os.environ, {"HINDSIGHT_ENABLED": "true"}, clear=True):
            HindsightMemoryAdapter.reset_instance()
            adapter = HindsightMemoryAdapter()
            adapter._client = mock_client
            adapter._connected = True

            answer = adapter.reflect("summarize")
            assert answer == "synthesized answer"
            mock_client.reflect.assert_called_once_with(query="summarize")

    def test_create_memory_bank_via_mock(self) -> None:
        mock_client = MagicMock()
        mock_client.create_memory_bank.return_value = {
            "name": "bank", "id": "b_1",
        }

        with patch.dict(os.environ, {"HINDSIGHT_ENABLED": "true"}, clear=True):
            HindsightMemoryAdapter.reset_instance()
            adapter = HindsightMemoryAdapter()
            adapter._client = mock_client
            adapter._connected = True

            result = adapter.create_memory_bank(
                "bank", mission="m", directives=["d1"], disposition="curious",
            )
            assert result["name"] == "bank"
            assert result["id"] == "b_1"

    def test_fallback_on_client_error_retain(self) -> None:
        mock_client = MagicMock()
        mock_client.observe.side_effect = ConnectionError("down")

        with patch.dict(os.environ, {"HINDSIGHT_ENABLED": "true"}, clear=True):
            HindsightMemoryAdapter.reset_instance()
            adapter = HindsightMemoryAdapter()
            adapter._client = mock_client
            adapter._connected = True

            rid = adapter.retain("content")
            assert rid.startswith("mem_")

    def test_fallback_on_client_error_recall(self) -> None:
        mock_client = MagicMock()
        mock_client.recall.side_effect = RuntimeError("boom")

        with patch.dict(os.environ, {"HINDSIGHT_ENABLED": "true"}, clear=True):
            HindsightMemoryAdapter.reset_instance()
            adapter = HindsightMemoryAdapter()
            adapter._fallback.retain("surviving content")
            adapter._client = mock_client
            adapter._connected = True

            results = adapter.recall("content")
            assert len(results) >= 1

    def test_fallback_on_client_error_reflect(self) -> None:
        mock_client = MagicMock()
        mock_client.reflect.side_effect = OSError("network")

        with patch.dict(os.environ, {"HINDSIGHT_ENABLED": "true"}, clear=True):
            HindsightMemoryAdapter.reset_instance()
            adapter = HindsightMemoryAdapter()
            adapter._client = mock_client
            adapter._connected = True

            answer = adapter.reflect("query")
            assert isinstance(answer, str)


class TestSingleton:
    """Singleton pattern — same instance returned."""

    def test_get_instance_returns_same_object(self) -> None:
        with patch.dict(os.environ, {"HINDSIGHT_ENABLED": "false"}, clear=True):
            HindsightMemoryAdapter.reset_instance()
            a = HindsightMemoryAdapter.get_instance()
            b = HindsightMemoryAdapter.get_instance()
            assert a is b

    def test_reset_instance_creates_new(self) -> None:
        with patch.dict(os.environ, {"HINDSIGHT_ENABLED": "false"}, clear=True):
            HindsightMemoryAdapter.reset_instance()
            a = HindsightMemoryAdapter.get_instance()
            HindsightMemoryAdapter.reset_instance()
            b = HindsightMemoryAdapter.get_instance()
            assert a is not b


class TestConfigFromEnv:
    """Environment variable configuration."""

    def test_custom_url(self) -> None:
        with patch.dict(
            os.environ,
            {"HINDSIGHT_ENABLED": "false", "HINDSIGHT_URL": "http://hindsight:9999"},
            clear=True,
        ):
            HindsightMemoryAdapter.reset_instance()
            adapter = HindsightMemoryAdapter.get_instance()
            assert adapter._url == "http://hindsight:9999"

    def test_default_url(self) -> None:
        with patch.dict(os.environ, {"HINDSIGHT_ENABLED": "false"}, clear=True):
            HindsightMemoryAdapter.reset_instance()
            adapter = HindsightMemoryAdapter.get_instance()
            assert adapter._url == "http://localhost:8888"

    def test_enabled_true_via_env(self) -> None:
        with patch.dict(os.environ, {"HINDSIGHT_ENABLED": "true"}, clear=True):
            HindsightMemoryAdapter.reset_instance()
            adapter = HindsightMemoryAdapter()
            assert adapter._enabled is True

    def test_enabled_one_via_env(self) -> None:
        with patch.dict(os.environ, {"HINDSIGHT_ENABLED": "1"}, clear=True):
            HindsightMemoryAdapter.reset_instance()
            adapter = HindsightMemoryAdapter()
            assert adapter._enabled is True


class TestHealthCheck:
    """Health check endpoint reports backend state."""

    def test_health_check_keys(self) -> None:
        with patch.dict(os.environ, {"HINDSIGHT_ENABLED": "false"}, clear=True):
            HindsightMemoryAdapter.reset_instance()
            adapter = HindsightMemoryAdapter.get_instance()
            health = adapter.health_check()
            for key in ("backend", "enabled", "url", "connected"):
                assert key in health
            assert health["backend"] == "fallback"
            assert health["connected"] is False

    def test_health_check_hindsight_when_connected(self) -> None:
        with patch.dict(os.environ, {"HINDSIGHT_ENABLED": "true"}, clear=True):
            HindsightMemoryAdapter.reset_instance()
            adapter = HindsightMemoryAdapter()
            adapter._client = MagicMock()
            adapter._connected = True
            health = adapter.health_check()
            assert health["backend"] == "hindsight"
            assert health["connected"] is True
