"""C26 items 1-4: async/process-lifecycle residual fixes.

Item 1 — aiosqlite closed-loop guard (raise if used after close).
Item 2 — pipeline/MCP shutdown suppress -> log-and-reraise.
Item 3 — Ornith PIPE drain on subprocess exit.
Item 4 — zombie reaping in local_inference stop_server.

Items 5-7 are already tested in test_c26_items567.py.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

# ── Item 1: aiosqlite closed-loop guard ─────────────────────────────


class TestAiosqliteClosedLoopGuard:
    """Session creation after engine close must raise, not silently fail."""

    def test_close_engine_function_exists(self) -> None:
        from general_ludd.db.session import close_engine

        assert callable(close_engine)

    def test_session_after_close_raises_runtime_error(self) -> None:
        from general_ludd.db.session import (
            close_engine,
            create_async_session_factory,
            get_default_db_path,
            init_engine_from_config,
        )

        db_path = get_default_db_path()
        engine = init_engine_from_config({"url": f"sqlite+aiosqlite:///{db_path}"})
        factory = create_async_session_factory(engine)

        close_engine(engine)

        with pytest.raises(RuntimeError, match="closed"):
            async def _try_session() -> None:
                async for _session in _run_get_async_session(factory):
                    pass

            asyncio.run(_try_session())

    def test_close_engine_is_idempotent(self) -> None:
        from general_ludd.db.session import (
            close_engine,
            get_default_db_path,
            init_engine_from_config,
        )

        db_path = get_default_db_path()
        engine = init_engine_from_config({"url": f"sqlite+aiosqlite:///{db_path}"})
        close_engine(engine)
        close_engine(engine)

    def test_session_before_close_works(self) -> None:
        from general_ludd.db.session import (
            create_async_session_factory,
            get_default_db_path,
            init_engine_from_config,
        )

        db_path = get_default_db_path()
        engine = init_engine_from_config({"url": f"sqlite+aiosqlite:///{db_path}"})
        factory = create_async_session_factory(engine)

        async def _try_session() -> None:
            async for session in _run_get_async_session(factory):
                assert session is not None

        asyncio.run(_try_session())

    def test_get_async_session_checks_closed_engine(self) -> None:
        from general_ludd.db.session import (
            close_engine,
            create_async_session_factory,
            get_default_db_path,
            init_engine_from_config,
        )

        db_path = get_default_db_path()
        engine = init_engine_from_config({"url": f"sqlite+aiosqlite:///{db_path}"})
        factory = create_async_session_factory(engine)

        close_engine(engine)

        with pytest.raises(RuntimeError, match=r"closed"):
            async def _try() -> None:
                async for _ in _run_get_async_session(factory):
                    pass

            asyncio.run(_try())

    def test_closed_engines_set_importable(self) -> None:
        from general_ludd.db.session import _closed_engines

        assert isinstance(_closed_engines, set)


async def _run_get_async_session(factory: object) -> None:
    from general_ludd.db.session import get_async_session

    async for session in get_async_session(factory):
        yield session


# ── Item 2: pipeline/MCP shutdown failures are retained until full drain ──


class TestShutdownLogAndReraise:
    """pipeline_controller.stop() and mcp_client.stop_all() failures
    must be logged, retained, and raised after the remaining owners drain."""

    DAEMON_PATH = (
        Path(__file__).parent.parent.parent / "src" / "general_ludd" / "daemon.py"
    )

    def _scan_except_block(self, marker: str) -> tuple[bool, bool]:
        source = self.DAEMON_PATH.read_text(encoding="utf-8")
        idx = source.index(marker)
        chunk = source[idx - 200 : idx + 100]
        lines = chunk.split("\n")

        in_except = False
        has_record = False
        has_suppress = False
        for line in lines:
            if "try:" in line:
                in_except = False
            if "except" in line and "Exception" in line:
                in_except = True
            if in_except and "_shutdown_failures.append" in line:
                has_record = True
            if in_except and "contextlib.suppress" in line:
                has_suppress = True
        return has_record, has_suppress

    def test_pipeline_shutdown_raises_not_suppresses(self) -> None:
        has_record, has_suppress = self._scan_except_block(
            "pipeline_controller.stop() failed during shutdown"
        )
        assert has_record, (
            "pipeline_controller.stop() failure must be retained for final raise"
        )
        assert not has_suppress, (
            "pipeline_controller.stop() must not use contextlib.suppress"
        )

    def test_mcp_shutdown_raises_not_suppresses(self) -> None:
        has_record, has_suppress = self._scan_except_block(
            "mcp_client.stop_all() failed during shutdown"
        )
        assert has_record, (
            "mcp_client.stop_all() failure must be retained for final raise"
        )
        assert not has_suppress, (
            "mcp_client.stop_all() must not use contextlib.suppress"
        )

    def test_shutdown_failures_raise_after_all_cleanup(self) -> None:
        source = self.DAEMON_PATH.read_text(encoding="utf-8")
        assert 'ExceptionGroup("daemon shutdown failures"' in source


# ── Item 3: Ornith PIPE drain on subprocess exit ────────────────────


class TestOrnithPipeDrain:
    """MCP transport stop() must close stdin before terminating the subprocess."""

    TRANSPORT_PATH = (
        Path(__file__).parent.parent.parent
        / "src" / "general_ludd" / "mcp" / "transport.py"
    )

    def _get_stop_lines(self) -> list[str]:
        source = self.TRANSPORT_PATH.read_text(encoding="utf-8")
        idx = source.index("async def stop(self)")
        chunk = source[idx:]
        return chunk.split("\n")

    def test_stop_closes_stdin_before_terminate(self) -> None:
        lines = self._get_stop_lines()
        func_indent = len(lines[0]) - len(lines[0].lstrip())

        close_stdin_line = -1
        terminate_line = -1
        for _i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and len(line) - len(line.lstrip()) > func_indent:
                if "close" in stripped and "stdin" in stripped.lower():
                    close_stdin_line = _i
                if "terminate" in stripped:
                    terminate_line = _i
            if _i > 0 and stripped and len(line) - len(line.lstrip()) <= func_indent and _i > 1:
                break

        if close_stdin_line >= 0 and terminate_line >= 0:
            assert close_stdin_line < terminate_line, (
                f"stdin.close() (line {close_stdin_line + 1}) must appear BEFORE "
                f"terminate() (line {terminate_line + 1})"
            )

    def test_stop_has_stdin_close(self) -> None:
        lines = self._get_stop_lines()
        func_indent = len(lines[0]) - len(lines[0].lstrip())

        has_stdin_close = False
        for _i, line in enumerate(lines):
            stripped = line.strip()
            if (
                stripped
                and len(line) - len(line.lstrip()) > func_indent
                and "stdin" in stripped.lower()
                and "close" in stripped.lower()
            ):
                has_stdin_close = True
            if _i > 0 and stripped and len(line) - len(line.lstrip()) <= func_indent and _i > 1:
                break

        assert has_stdin_close, (
            "MCPStdioClient.stop() must close stdin before terminating"
        )


# ── Item 4: zombie reaping in local_inference stop_server ───────────


class TestLocalInferenceZombieReaping:
    """stop_server must call process.wait() after every kill path."""

    LOCAL_INFERENCE_PATH = (
        Path(__file__).parent.parent.parent
        / "src" / "general_ludd" / "infra" / "local_inference.py"
    )

    def _get_stop_lines(self) -> list[str]:
        source = self.LOCAL_INFERENCE_PATH.read_text(encoding="utf-8")
        idx = source.index("async def stop_server")
        chunk = source[idx:]
        return chunk.split("\n")

    def test_stop_server_waits_after_sigkill(self) -> None:
        lines = self._get_stop_lines()
        func_indent = len(lines[0]) - len(lines[0].lstrip())

        sigkill_line = -1
        wait_line = -1
        for _i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or len(line) - len(line.lstrip()) <= func_indent:
                continue
            if "SIGKILL" in stripped:
                sigkill_line = _i
            if "wait()" in stripped and _i > sigkill_line >= 0:
                wait_line = _i
        assert sigkill_line >= 0, "stop_server must contain SIGKILL path"
        assert wait_line >= 0, (
            f"stop_server must call process.wait() AFTER SIGKILL. "
            f"SIGKILL at line {sigkill_line+1}, no process.wait() follows."
        )
        assert wait_line > sigkill_line

    def test_stop_server_terminate_also_waits(self) -> None:
        lines = self._get_stop_lines()
        func_indent = len(lines[0]) - len(lines[0].lstrip())

        sigterm_line = -1
        wait_line = -1
        for _i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or len(line) - len(line.lstrip()) <= func_indent:
                continue
            if "SIGTERM" in stripped:
                sigterm_line = _i
            if "wait()" in stripped and sigterm_line >= 0:
                wait_line = _i
                break
        assert sigterm_line >= 0, "stop_server must contain SIGTERM path"
        assert wait_line >= 0, (
            "stop_server must call process.wait() after SIGTERM"
        )

    def test_stop_server_waits_preserves_zombie_free(self) -> None:
        lines = self._get_stop_lines()
        func_indent = len(lines[0]) - len(lines[0].lstrip())

        sigkill_found = False
        wait_after_sigkill = False
        for _i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or len(line) - len(line.lstrip()) <= func_indent:
                continue
            if "SIGKILL" in stripped:
                sigkill_found = True
                continue
            if sigkill_found and "wait()" in stripped:
                wait_after_sigkill = True
                break
        assert sigkill_found, "stop_server must have a SIGKILL path"
        assert wait_after_sigkill, (
            "stop_server must call process.wait() after SIGKILL"
        )

    def test_mcp_transport_stop_reaps_zombie(self) -> None:
        transport_path = (
            Path(__file__).parent.parent.parent
            / "src" / "general_ludd" / "mcp" / "transport.py"
        )
        source = transport_path.read_text(encoding="utf-8")
        idx = source.index("async def stop(self)")
        chunk = source[idx:]
        lines = chunk.split("\n")
        func_indent = len(lines[0]) - len(lines[0].lstrip())

        kill_found = False
        wait_after_kill = False
        for _i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or len(line) - len(line.lstrip()) <= func_indent:
                continue
            if ".kill()" in stripped:
                kill_found = True
                continue
            if kill_found and "wait()" in stripped:
                wait_after_kill = True
                break
        assert wait_after_kill, (
            "MCPStdioClient.stop() must call process.wait() after kill()"
        )

    def test_searx_stop_reaps_zombie(self) -> None:
        searx_path = (
            Path(__file__).parent.parent.parent
            / "src" / "general_ludd" / "searx" / "server.py"
        )
        source = searx_path.read_text(encoding="utf-8")
        idx = source.index("def stop(self)")
        chunk = source[idx:]
        lines = chunk.split("\n")
        func_indent = len(lines[0]) - len(lines[0].lstrip())

        kill_found = False
        wait_after_kill = False
        for line in lines:
            stripped = line.strip()
            if not stripped or len(line) - len(line.lstrip()) <= func_indent:
                continue
            if ".kill()" in stripped:
                kill_found = True
                continue
            if kill_found and ".wait()" in stripped:
                wait_after_kill = True
                break
        assert wait_after_kill, (
            "SearXServer.stop() must call process.wait() after kill()"
        )
