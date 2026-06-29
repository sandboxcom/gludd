"""Shared fixtures and spawn helpers for the local-provider E2E tests.

Two ways the E2E tests get a backend URL:

1. **Pre-running server** — operator exports ``VLLM_BASE_URL`` /
   ``LLAMACPP_BASE_URL`` pointing at an already-serving process. The spawn
   helpers return ``None`` and the test uses the env var directly.

2. **Spawn-and-teardown** — operator exports ``VLLM_E2E_SPAWN=1`` (or
   ``LLAMACPP_E2E_SPAWN=1``) plus ``VLLM_TEST_MODEL`` /
   ``LLAMACPP_TEST_MODEL``. The fixture spawns a real process via
   ``LocalInferenceManager``, yields the URL, and SIGTERMs the process when
   the test finishes.

Spawn helpers are unit-tested in ``tests/unit/test_spawn_helpers.py`` so the
lifecycle (start → yield URL → stop) is verified without requiring a real
vllm/llama_cpp binary on the host.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Generator
from typing import Literal

import pytest

from general_ludd.infra.local_inference import (
    LocalInferenceManager,
    LocalServerConfig,
)

_Engine = Literal["vllm", "llamacpp"]


def _maybe_spawn(engine: _Engine) -> str | None:
    """Return a spawn-context URL if the ``*_E2E_SPAWN`` env var is set,
    otherwise return ``None`` (caller falls back to ``*_BASE_URL``).

    Pure env inspection — does NOT actually spawn. Used as the gating check
    before the real spawn generator runs.
    """
    flag = os.environ.get(f"{engine.upper()}_E2E_SPAWN")
    if flag is None or flag != "1":
        return None
    return "spawn-requested"


def _spawn_backend(engine: _Engine) -> Generator[str, None, None]:
    """Spawn a real ``vllm``/``llama_cpp`` server, yield its URL, then stop it.

    Generator so the fixture can wrap it with a ``yield``-based teardown.

    Reads:
      - ``{ENGINE}_TEST_MODEL`` — the model id/path to serve.
      - ``{ENGINE}_TEST_HOST`` (default ``localhost``).
      - ``{ENGINE}_TEST_PORT`` (default ``8000`` for vllm, ``8080`` for llamacpp).

    Raises ``Skip``-equivalent if the spawn env vars are not set; the calling
    fixture is responsible for turning that into a pytest.skip.
    """
    flag = os.environ.get(f"{engine.upper()}_E2E_SPAWN")
    if flag != "1":
        raise RuntimeError(
            f"_spawn_backend({engine!r}) called but {engine.upper()}_E2E_SPAWN != '1'"
        )

    model = os.environ.get(f"{engine.upper()}_TEST_MODEL")
    if not model:
        raise RuntimeError(
            f"{engine.upper()}_TEST_MODEL must be set when {engine.upper()}_E2E_SPAWN=1"
        )

    host = os.environ.get(f"{engine.upper()}_TEST_HOST", "localhost")
    default_port = 8000 if engine == "vllm" else 8080
    port = int(os.environ.get(f"{engine.upper()}_TEST_PORT", str(default_port)))

    if engine == "vllm":
        cfg = LocalServerConfig(
            engine="vllm",
            model_name=model,
            host=host,
            port=port,
        )
    else:
        cfg = LocalServerConfig(
            engine="llamacpp",
            model_path=model,
            model_name=model,
            host=host,
            port=port,
        )

    mgr = LocalInferenceManager()
    server = mgr.create_server(cfg)

    # start_server is async; run it on a fresh loop so the fixture stays sync.
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(mgr.start_server(server.server_id))
    finally:
        loop.close()

    try:
        yield server.endpoint_url
    finally:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(mgr.stop_server(server.server_id))
        finally:
            loop.close()


def _require_backend_url(engine: _Engine) -> str:
    """Resolve the base URL for ``engine``, skipping the test if neither
    spawn-mode nor ``*_BASE_URL`` is configured."""
    if _maybe_spawn(engine) is not None:
        # Spawn-mode: hand off to the spawn fixture (used as a pytest fixture).
        pytest.skip(
            f"spawn-mode for {engine} is configured; use the spawn_{engine} "
            f"fixture instead of reading *_BASE_URL directly"
        )

    url = os.environ.get(f"{engine.upper()}_BASE_URL")
    if not url:
        pytest.skip(
            f"{engine.upper()}_BASE_URL not set — set it (or "
            f"{engine.upper()}_E2E_SPAWN=1) to run the {engine} E2E"
        )
    return url


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def vllm_base_url() -> Generator[str, None, None]:
    """Yield a vLLM base URL. Spawns a real server when ``VLLM_E2E_SPAWN=1``,
    otherwise reads ``VLLM_BASE_URL`` (skipping if neither is set)."""
    if _maybe_spawn("vllm") is not None:
        yield from _spawn_backend("vllm")
        return
    url = os.environ.get("VLLM_BASE_URL")
    if not url:
        pytest.skip(
            "VLLM_BASE_URL not set — set it (or VLLM_E2E_SPAWN=1) to run the vllm E2E"
        )
    yield url


@pytest.fixture()
def llamacpp_base_url() -> Generator[str, None, None]:
    """Yield a llama.cpp base URL. Spawns when ``LLAMACPP_E2E_SPAWN=1``,
    otherwise reads ``LLAMACPP_BASE_URL`` (skipping if neither is set)."""
    if _maybe_spawn("llamacpp") is not None:
        yield from _spawn_backend("llamacpp")
        return
    url = os.environ.get("LLAMACPP_BASE_URL")
    if not url:
        pytest.skip(
            "LLAMACPP_BASE_URL not set — set it (or LLAMACPP_E2E_SPAWN=1) to "
            "run the llamacpp E2E"
        )
    yield url
