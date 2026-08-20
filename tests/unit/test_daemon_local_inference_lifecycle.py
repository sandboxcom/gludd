"""Daemon ownership tests for managed local-inference subprocesses."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from general_ludd.daemon import _lifespan
from general_ludd.infra.local_inference import (
    LocalInferenceManager,
    LocalServer,
    LocalServerConfig,
)
from general_ludd.routers import models as models_router
from tests.unit.test_daemon import _lifespan_patches


async def _owned_sleeping_server(
    manager: LocalInferenceManager,
    stderr_path: Path,
) -> tuple[object, str]:
    """Register one namespaced child that behaves like a running local server."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(60)",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    stderr_path.write_text("owned local-model diagnostics")
    server = manager.create_server(
        LocalServerConfig(
            engine="llamacpp",
            model_path=__file__,
            host="127.0.0.1",
            port=49151,
            startup_timeout=0,
        )
    )
    server.process = process
    server.pid = process.pid
    server.status = "running"
    server.started_at = time.time()
    server.stderr_path = str(stderr_path)
    return process, server.server_id


@pytest.mark.asyncio
@pytest.mark.parametrize("body_fails", [False, True], ids=["normal", "exceptional"])
async def test_daemon_lifespan_reaps_owned_local_server_and_stderr(
    tmp_path: Path,
    body_fails: bool,
) -> None:
    """Normal and exceptional app shutdown must reap only daemon-owned servers."""
    manager = LocalInferenceManager()
    stderr_path = tmp_path / "managed-stderr.log"
    process, server_id = await _owned_sleeping_server(manager, stderr_path)
    app = FastAPI(lifespan=_lifespan)
    app.state.tick_interval = 0.01
    app.state.event_loop = None
    app.state._receiver_buffer = MagicMock()
    app.state._local_inference_manager = manager
    event_loop = MagicMock()
    event_loop.run_forever = AsyncMock()
    original_lifespan = app.router.lifespan_context
    models_router.register(app, {})

    # Model-route registration must compose cleanup into the daemon lifespan;
    # a standalone manager fixture is not application-shutdown evidence.
    assert app.router.lifespan_context is not original_lifespan

    try:
        with _lifespan_patches(event_loop):
            if body_fails:
                with pytest.raises(RuntimeError, match="test-body-failure"):
                    async with app.router.lifespan_context(app):
                        raise RuntimeError("test-body-failure")
            else:
                async with app.router.lifespan_context(app):
                    pass

        assert process.returncode is not None
        assert manager.get_server(server_id) is None
        assert not stderr_path.exists()
    finally:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(process.pid), 15)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=5)
        stderr_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_daemon_lifespan_reaps_owned_server_when_startup_fails(
    tmp_path: Path,
) -> None:
    """A daemon startup error must still retire every managed child and log."""
    manager = LocalInferenceManager()
    stderr_path = tmp_path / "managed-startup-stderr.log"
    process, server_id = await _owned_sleeping_server(manager, stderr_path)

    @contextlib.asynccontextmanager
    async def failed_startup(_app: FastAPI) -> AsyncIterator[None]:
        if _app is None:
            yield
        raise RuntimeError("daemon-startup-failure")

    app = FastAPI(lifespan=failed_startup)
    app.state._local_inference_manager = manager
    models_router.register(app, {})

    try:
        with pytest.raises(RuntimeError, match="daemon-startup-failure"):
            async with app.router.lifespan_context(app):
                pytest.fail("a failed startup must not enter the application body")

        assert process.returncode is not None
        assert manager.get_server(server_id) is None
        assert not stderr_path.exists()
    finally:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(process.pid), 15)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=5)
        stderr_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_failed_local_server_start_is_reaped_and_removes_stderr() -> None:
    """A readiness failure must not leave the manager's child or log behind."""
    manager = LocalInferenceManager()
    server = manager.create_server(
        LocalServerConfig(
            engine="llamacpp",
            model_path=__file__,
            host="127.0.0.1",
            port=49152,
        )
    )
    process = MagicMock()
    process.pid = 987654
    process.returncode = None

    async def reap() -> int:
        process.returncode = -15
        return process.returncode

    process.wait = AsyncMock(side_effect=reap)
    captured_stderr: list[Path] = []

    async def fail_readiness(candidate: LocalServer) -> None:
        path = candidate.stderr_path
        assert isinstance(path, str)
        captured_stderr.append(Path(path))
        raise RuntimeError("readiness failed")

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)),
        patch.object(manager, "_wait_for_ready", side_effect=fail_readiness),
        pytest.raises(RuntimeError, match="readiness failed"),
    ):
        await manager.start_server(server.server_id)

    assert process.wait.await_count == 1
    assert manager.get_server(server.server_id) is None
    assert server.process is None
    assert server.pid is None
    assert server.stderr_path is None
    assert captured_stderr and not captured_stderr[0].exists()
