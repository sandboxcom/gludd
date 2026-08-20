"""Integration tests for GLUDD_WRITER_MODE lifespan branch (B3.1.3 Slice 4).

Validates that the daemon's ``_lifespan`` branches on ``GLUDD_WRITER_MODE``:

- ``inline`` (default / explicit): no ``_write_queue``, no ``_writer_process``,
  regular writable session factory — the pre-beta.3 single-process path is
  byte-identical.
- ``subprocess``: read-only engine + published ``WriteQueue`` + spawned
  ``WriterProcess``; teardown drains the queue and stops the writer before
  disposing the engine.
- Invalid values: fall back to ``inline`` with a warning log line.

The writer subprocess itself is mocked (``_FakeWriterProcess``) so these tests
never fork — real subprocess spawn behaviour is Slice 3's test territory.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

import general_ludd.daemon as daemon_mod
from general_ludd.daemon import create_daemon_app
from general_ludd.ipc import Envelope, WriteQueue
from general_ludd.writer.bridge import HTTP_ENQUEUED, enqueue_or_commit

_FAKE_WRITER_INSTANCES: list[Any] = []


class _FakeWriterProcess:
    """Test double for :class:`WriterProcess` — never forks.

    Mirrors the parent-side lifecycle API (``start`` / ``stop`` / ``is_alive``)
    so the daemon's ``_lifespan`` subprocess branch exercises the full
    publication + teardown path without actually spawning a child.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        if not isinstance(config, dict):
            raise TypeError("config must be a dict")
        self.config: dict[str, Any] = dict(config)
        self._alive: bool = False
        self._stopped: bool = False
        self.start_calls: int = 0
        self.stop_calls: int = 0
        _FAKE_WRITER_INSTANCES.append(self)

    def start(self, timeout: float = 30.0) -> bool:
        self.start_calls += 1
        self._alive = True
        return True

    def stop(self, sigterm_timeout: float = 10.0) -> bool:
        self.stop_calls += 1
        self._alive = False
        self._stopped = True
        return True

    def is_alive(self) -> bool:
        return self._alive

    def is_ready(self) -> bool:
        return self._alive

    @property
    def pid(self) -> int | None:
        return 99999 if self._alive else None


@pytest.fixture(autouse=True)
def _reset_daemon_state() -> None:
    if daemon_mod._daemon_state is None:
        daemon_mod._daemon_state = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
    daemon_mod._daemon_state["todos"] = []
    daemon_mod._daemon_state["tick_metrics"] = {}
    _FAKE_WRITER_INSTANCES.clear()


@pytest.fixture(autouse=True)
def _clear_writer_mode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GLUDD_WRITER_MODE", raising=False)


def _make_db_config(tmp_path: Path) -> str:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    db_path = tmp_path / "test.db"
    (config_dir / "general-ludd.yml").write_text(f"database:\n  url: 'sqlite+aiosqlite:///{db_path}'\n")
    return str(config_dir)


def _patched(runner: bool = True, writer: bool = True):
    """Common patch stack: stub the ansible runner + the writer subprocess."""
    patches = []
    if runner:
        patches.append(
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=MagicMock(),
            )
        )
    if writer:
        patches.append(patch("general_ludd.daemon.WriterProcess", _FakeWriterProcess))
    return patches


class TestInlineMode:
    def test_inline_mode_default_no_write_queue(self, tmp_path: Path) -> None:
        config_dir = _make_db_config(tmp_path)
        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=MagicMock(),
            ),
            patch("general_ludd.daemon.WriterProcess", _FakeWriterProcess),
        ):
            app = create_daemon_app(tick_interval=0.01, config_dir=config_dir)
            with TestClient(app):
                assert getattr(app.state, "_write_queue", None) is None
                assert getattr(app.state, "_writer_process", None) is None
                assert app.state._session_factory is not None
            # No fake writer was ever constructed in inline mode.
            assert _FAKE_WRITER_INSTANCES == []

    def test_inline_mode_explicit_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_WRITER_MODE", "inline")
        config_dir = _make_db_config(tmp_path)
        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=MagicMock(),
            ),
            patch("general_ludd.daemon.WriterProcess", _FakeWriterProcess),
        ):
            app = create_daemon_app(tick_interval=0.01, config_dir=config_dir)
            with TestClient(app):
                assert getattr(app.state, "_write_queue", None) is None
                assert getattr(app.state, "_writer_process", None) is None
            assert _FAKE_WRITER_INSTANCES == []


class TestSubprocessMode:
    def test_subprocess_mode_publishes_write_queue(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_WRITER_MODE", "subprocess")
        config_dir = _make_db_config(tmp_path)
        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=MagicMock(),
            ),
            patch("general_ludd.daemon.WriterProcess", _FakeWriterProcess),
        ):
            app = create_daemon_app(tick_interval=0.01, config_dir=config_dir)
            with TestClient(app):
                queue = getattr(app.state, "_write_queue", None)
                assert isinstance(queue, WriteQueue), f"expected WriteQueue, got {type(queue).__name__}: {queue!r}"

    def test_subprocess_mode_publishes_writer_process(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_WRITER_MODE", "subprocess")
        config_dir = _make_db_config(tmp_path)
        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=MagicMock(),
            ),
            patch("general_ludd.daemon.WriterProcess", _FakeWriterProcess),
        ):
            app = create_daemon_app(tick_interval=0.01, config_dir=config_dir)
            with TestClient(app):
                wp = getattr(app.state, "_writer_process", None)
                assert wp is not None, "app.state._writer_process not published"
                assert wp.is_alive()
                assert wp.start_calls == 1

    @pytest.mark.asyncio
    async def test_subprocess_mode_session_factory_is_read_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GLUDD_WRITER_MODE", "subprocess")
        config_dir = _make_db_config(tmp_path)
        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=MagicMock(),
            ),
            patch("general_ludd.daemon.WriterProcess", _FakeWriterProcess),
        ):
            app = create_daemon_app(tick_interval=0.01, config_dir=config_dir)
            async with daemon_mod._lifespan(app):
                factory = app.state._session_factory
                async with factory() as session:
                    with pytest.raises(SQLAlchemyError):
                        await session.execute(text("CREATE TABLE t_fail (x INTEGER)"))

    def test_subprocess_mode_teardown_stops_writer(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_WRITER_MODE", "subprocess")
        config_dir = _make_db_config(tmp_path)
        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=MagicMock(),
            ),
            patch("general_ludd.daemon.WriterProcess", _FakeWriterProcess),
        ):
            app = create_daemon_app(tick_interval=0.01, config_dir=config_dir)
            with TestClient(app):
                wp = app.state._writer_process
                assert wp is not None
                assert wp.is_alive()
            # After lifespan exit the writer must have been stopped.
            assert wp.stop_calls >= 1
            assert not wp.is_alive()

    @pytest.mark.asyncio
    async def test_body_and_earlier_shutdown_failure_still_stop_writer(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GLUDD_WRITER_MODE", "subprocess")
        config_dir = _make_db_config(tmp_path)

        class BrokenPipeline:
            async def stop(self) -> None:
                raise RuntimeError("pipeline cleanup failed")

        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=MagicMock(),
            ),
            patch("general_ludd.daemon.WriterProcess", _FakeWriterProcess),
            pytest.raises(ExceptionGroup),
        ):
            app = create_daemon_app(tick_interval=0.01, config_dir=config_dir)
            async with daemon_mod._lifespan(app):
                wp = app.state._writer_process
                app.state._pipeline_controller = BrokenPipeline()
                raise RuntimeError("request body failed")

        assert wp.stop_calls >= 1
        assert not wp.is_alive()

    @pytest.mark.asyncio
    async def test_body_cancellation_still_stops_writer(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import asyncio

        monkeypatch.setenv("GLUDD_WRITER_MODE", "subprocess")
        config_dir = _make_db_config(tmp_path)
        ready = asyncio.Event()
        release = asyncio.Event()
        writer: _FakeWriterProcess | None = None

        async def run_lifespan() -> None:
            nonlocal writer
            app = create_daemon_app(tick_interval=0.01, config_dir=config_dir)
            async with daemon_mod._lifespan(app):
                writer = app.state._writer_process
                ready.set()
                await release.wait()

        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=MagicMock(),
            ),
            patch("general_ludd.daemon.WriterProcess", _FakeWriterProcess),
        ):
            task = asyncio.create_task(run_lifespan())
            await ready.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert writer is not None
        assert writer.stop_calls >= 1
        assert not writer.is_alive()

    @pytest.mark.asyncio
    async def test_subprocess_mode_teardown_drains_before_stop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GLUDD_WRITER_MODE", "subprocess")
        config_dir = _make_db_config(tmp_path)
        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=MagicMock(),
            ),
            patch("general_ludd.daemon.WriterProcess", _FakeWriterProcess),
        ):
            app = create_daemon_app(tick_interval=0.01, config_dir=config_dir)
            async with daemon_mod._lifespan(app):
                queue = app.state._write_queue
                wp = app.state._writer_process
                assert queue is not None
                assert wp is not None
                await queue.put(Envelope(topic="test", payload={"a": 1}))
                await queue.put(Envelope(topic="test", payload={"b": 2}))
                assert len(queue) == 2
            # After teardown: queue drained, writer stopped.
            assert len(queue) == 0
            assert not wp.is_alive()


class TestInvalidMode:
    def test_invalid_writer_mode_falls_back_to_inline(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GLUDD_WRITER_MODE", "garbage")
        config_dir = _make_db_config(tmp_path)
        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=MagicMock(),
            ),
            patch("general_ludd.daemon.WriterProcess", _FakeWriterProcess),
        ):
            app = create_daemon_app(tick_interval=0.01, config_dir=config_dir)
            # Patch the daemon's ProjectLogAdapter.warning to verify the
            # fallback warning is emitted. caplog / handler-based capture
            # don't reliably see records emitted from the lifespan's portal
            # thread.
            with patch.object(daemon_mod.logger, "warning") as mock_warn, TestClient(app):
                # Invalid mode falls back to inline: no queue, no writer.
                assert getattr(app.state, "_write_queue", None) is None
                assert getattr(app.state, "_writer_process", None) is None

            warning_msgs = [str(call) for call in mock_warn.call_args_list]
            assert any("GLUDD_WRITER_MODE" in msg for msg in warning_msgs), warning_msgs
            assert _FAKE_WRITER_INSTANCES == []


class TestStructural:
    def test_writer_mode_read_happens_before_engine_construction(self) -> None:
        """AST structural test: the ``GLUDD_WRITER_MODE`` env-var read must
        appear positionally before the ``init_engine_from_config`` call inside
        ``_lifespan`` so the engine-construction branch can depend on it.
        """
        src = Path(daemon_mod.__file__).read_text()
        tree = ast.parse(src)
        lifespan = next(
            node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_lifespan"
        )

        env_read_line: int | None = None
        engine_call_line: int | None = None

        for node in ast.walk(lifespan):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "GLUDD_WRITER_MODE" in node.value
                and (env_read_line is None or node.lineno < env_read_line)
            ):
                env_read_line = node.lineno
            if isinstance(node, ast.Call):
                fn = node.func
                target_name: str | None = None
                if isinstance(fn, ast.Name):
                    target_name = fn.id
                elif isinstance(fn, ast.Attribute):
                    target_name = fn.attr
                if target_name == "init_engine_from_config" and (
                    engine_call_line is None or node.lineno < engine_call_line
                ):
                    engine_call_line = node.lineno

        assert env_read_line is not None, "GLUDD_WRITER_MODE env-var read not found inside _lifespan"
        assert engine_call_line is not None, "init_engine_from_config call not found inside _lifespan"
        assert env_read_line < engine_call_line, (
            f"GLUDD_WRITER_MODE read at line {env_read_line} must precede "
            f"init_engine_from_config call at line {engine_call_line}"
        )

    def test_lifespan_references_writer_mode_branch(self) -> None:
        """The implementation must textually reference the two modes."""
        src = Path(daemon_mod.__file__).read_text()
        assert re.search(r"GLUDD_WRITER_MODE", src), "GLUDD_WRITER_MODE not referenced in daemon.py"
        assert re.search(r'"subprocess"', src), "subprocess mode branch not present in daemon.py"


class TestWritesFlowThroughQueue:
    @pytest.mark.asyncio
    async def test_subprocess_mode_writes_flow_through_queue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In subprocess mode, ``enqueue_or_commit`` must take the queue path
        (returning ``(True, HTTP_ENQUEUED)``) rather than the inline path."""
        monkeypatch.setenv("GLUDD_WRITER_MODE", "subprocess")
        config_dir = _make_db_config(tmp_path)
        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=MagicMock(),
            ),
            patch("general_ludd.daemon.WriterProcess", _FakeWriterProcess),
        ):
            app = create_daemon_app(tick_interval=0.01, config_dir=config_dir)
            async with daemon_mod._lifespan(app):
                queue = app.state._write_queue
                assert queue is not None
                assert len(queue) == 0

                enqueued, status = await enqueue_or_commit(app, topic="todo.create", payload={"title": "ship beta.3"})
                assert enqueued is True
                assert status == HTTP_ENQUEUED
                assert len(queue) == 1
