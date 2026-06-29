"""Unit tests for the vLLM/llama.cpp spawn fixtures in
``tests/e2e/providers/conftest.py``.

These exercise the fixture logic in isolation (mocked ``LocalInferenceManager``)
so the spawn path is verified without requiring a real vllm/llama_cpp binary.
The E2E tests themselves skip when their env vars are unset.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


def test_spawn_vllm_fixture_returns_url_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """When VLLM_E2E_SPAWN=1 + VLLM_TEST_MODEL are set, the fixture spawns a
    server via LocalInferenceManager and yields its endpoint URL."""
    from tests.e2e.providers import conftest as prov_conf

    monkeypatch.setenv("VLLM_E2E_SPAWN", "1")
    monkeypatch.setenv("VLLM_TEST_MODEL", "Qwen/Qwen2.5-0.5B-Instruct")

    fake_server = MagicMock()
    fake_server.endpoint_url = "http://localhost:8000/v1"
    fake_server.server_id = "local-0"
    fake_server.is_running = True

    fake_mgr = MagicMock()
    fake_mgr.create_server.return_value = fake_server

    async def _fake_start(sid: str) -> Any:
        return fake_server

    fake_mgr.start_server = _fake_start
    fake_mgr.stop_server = MagicMock(
        side_effect=lambda sid: asyncio.sleep(0)
    )

    with patch.object(prov_conf, "LocalInferenceManager", return_value=fake_mgr):
        gen = prov_conf._spawn_backend("vllm")
        url = next(gen)
        assert url == "http://localhost:8000/v1"
        fake_mgr.create_server.assert_called_once()
        # Drain the generator so the teardown (stop_server) runs.
        with pytest.raises(StopIteration):
            next(gen)


def test_spawn_vllm_fixture_skips_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """When VLLM_E2E_SPAWN is unset, the fixture does NOT spawn — the caller
    falls back to VLLM_BASE_URL (verified separately in the E2E tests)."""
    from tests.e2e.providers import conftest as prov_conf

    monkeypatch.delenv("VLLM_E2E_SPAWN", raising=False)
    monkeypatch.delenv("VLLM_TEST_MODEL", raising=False)

    # _spawn_backend should raise a SkipNotAllowed-style signal OR return None
    # to indicate "no spawn requested"; we model it as returning None.
    result = prov_conf._maybe_spawn("vllm")
    assert result is None


def test_spawn_vllm_fixture_terminates_process_after_yield(monkeypatch: pytest.MonkeyPatch) -> None:
    """After the fixture's consumer is done, stop_server is invoked so the
    spawned process is reaped (no orphans)."""
    from tests.e2e.providers import conftest as prov_conf

    monkeypatch.setenv("VLLM_E2E_SPAWN", "1")
    monkeypatch.setenv("VLLM_TEST_MODEL", "Qwen/Qwen2.5-0.5B-Instruct")

    fake_server = MagicMock()
    fake_server.endpoint_url = "http://localhost:8000/v1"
    fake_server.server_id = "local-99"
    fake_server.is_running = True

    fake_mgr = MagicMock()
    fake_mgr.create_server.return_value = fake_server

    async def _fake_start(sid: str) -> Any:
        return fake_server

    fake_mgr.start_server = _fake_start

    stopped: dict[str, bool] = {}

    async def _fake_stop(sid: str) -> None:
        stopped[sid] = True

    fake_mgr.stop_server = _fake_stop

    with patch.object(prov_conf, "LocalInferenceManager", return_value=fake_mgr):
        gen: Generator[str, None, None] = prov_conf._spawn_backend("vllm")
        url = next(gen)
        assert url == "http://localhost:8000/v1"
        # Trigger teardown.
        with pytest.raises(StopIteration):
            next(gen)

    assert stopped.get("local-99") is True, "stop_server was not called during teardown"


def test_spawn_llamacpp_fixture_returns_url_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirror of the vllm test for the llama.cpp spawn path."""
    from tests.e2e.providers import conftest as prov_conf

    monkeypatch.setenv("LLAMACPP_E2E_SPAWN", "1")
    monkeypatch.setenv("LLAMACPP_TEST_MODEL", "/models/tiny.gguf")

    fake_server = MagicMock()
    fake_server.endpoint_url = "http://localhost:8080/v1"
    fake_server.server_id = "local-7"
    fake_server.is_running = True

    fake_mgr = MagicMock()
    fake_mgr.create_server.return_value = fake_server

    async def _fake_start(sid: str) -> Any:
        return fake_server

    fake_mgr.start_server = _fake_start

    async def _fake_stop(sid: str) -> None:
        return None

    fake_mgr.stop_server = _fake_stop

    with patch.object(prov_conf, "LocalInferenceManager", return_value=fake_mgr):
        gen = prov_conf._spawn_backend("llamacpp")
        url = next(gen)
        assert url == "http://localhost:8080/v1"
        with pytest.raises(StopIteration):
            next(gen)
