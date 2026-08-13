"""In-process behavioral coverage for the writer child protocol."""

from __future__ import annotations

import json
import signal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.writer import _child as child


class _AsyncConnectionContext:
    def __init__(self, connection: AsyncMock) -> None:
        self.connection = connection

    async def __aenter__(self) -> AsyncMock:
        return self.connection

    async def __aexit__(self, *_exc_info: object) -> None:
        return None


class _Engine:
    def __init__(self) -> None:
        self.connection = AsyncMock()
        self.dispose = AsyncMock()

    def begin(self) -> _AsyncConnectionContext:
        return _AsyncConnectionContext(self.connection)


class _SignalLoop:
    def __init__(self, *, reject_signal_handler: bool = False) -> None:
        self.callback = MagicMock()
        self.fallback_callback = MagicMock()
        self.reject_signal_handler = reject_signal_handler
        self.removed: list[signal.Signals] = []

    def add_signal_handler(
        self, sig: signal.Signals, callback: MagicMock
    ) -> None:
        assert sig == signal.SIGTERM
        if self.reject_signal_handler:
            raise NotImplementedError
        self.callback = callback

    def remove_signal_handler(self, sig: signal.Signals) -> bool:
        self.removed.append(sig)
        return True


def test_load_config_and_atomic_readiness_round_trip(tmp_path: Path) -> None:
    config_path = tmp_path / "writer.json"
    config_path.write_text(json.dumps({"database": {"url": "sqlite://"}}))
    ready_path = tmp_path / "ready.json"

    assert child._load_config(str(config_path)) == {
        "database": {"url": "sqlite://"}
    }
    child._write_ready(str(ready_path), "nonce-123")

    assert json.loads(ready_path.read_text()) == {"nonce": "nonce-123"}
    assert not ready_path.with_suffix(".json.tmp").exists()


def test_load_config_rejects_non_object(tmp_path: Path) -> None:
    config_path = tmp_path / "writer.json"
    config_path.write_text('["not", "an", "object"]')

    with pytest.raises(ValueError, match="must be a JSON object"):
        child._load_config(str(config_path))


@pytest.mark.asyncio
async def test_apply_envelope_executes_sql_with_parameters() -> None:
    engine = _Engine()

    await child._apply_envelope(
        engine,
        {
            "topic": "execute_sql",
            "payload": {
                "sql": "INSERT INTO queue (id) VALUES (:id)",
                "params": {"id": 7},
            },
        },
    )

    statement, params = engine.connection.execute.await_args.args
    assert str(statement) == "INSERT INTO queue (id) VALUES (:id)"
    assert params == {"id": 7}


@pytest.mark.asyncio
async def test_apply_envelope_rejects_missing_sql_and_ignores_unknown_topic() -> None:
    engine = _Engine()

    with pytest.raises(ValueError, match="missing 'sql' string"):
        await child._apply_envelope(
            engine, {"topic": "execute_sql", "payload": {"sql": None}}
        )

    await child._apply_envelope(engine, {"topic": "future_protocol"})
    engine.connection.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_drain_spool_applies_valid_lines_and_skips_bad_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spool = tmp_path / "inbound.jsonl"
    spool.write_text(
        "\n"
        '{"topic":"execute_sql","payload":{"sql":"SELECT 1"}}\n'
        "{malformed json}\n"
        '{"topic":"future_protocol"}\n'
    )
    apply = AsyncMock()
    monkeypatch.setattr(child, "_apply_envelope", apply)

    offset = await child._drain_spool(str(spool), 0, object())

    assert offset == spool.stat().st_size
    assert apply.await_count == 2
    assert apply.await_args_list[0].args[1]["topic"] == "execute_sql"
    assert apply.await_args_list[1].args[1]["topic"] == "future_protocol"


@pytest.mark.asyncio
async def test_drain_spool_handles_missing_unchanged_and_disappearing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing.jsonl"
    assert await child._drain_spool(str(missing), 11, object()) == 11

    spool = tmp_path / "inbound.jsonl"
    spool.write_text("short")
    assert await child._drain_spool(str(spool), spool.stat().st_size, object()) == 5

    monkeypatch.setattr(child.os.path, "getsize", lambda _path: 10)
    monkeypatch.setattr("builtins.open", MagicMock(side_effect=FileNotFoundError))
    assert await child._drain_spool(str(spool), 0, object()) == 0


@pytest.mark.asyncio
async def test_run_writer_loop_initializes_ticks_drains_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _Engine()
    ensure_tables = AsyncMock()
    session_factory = object()
    event_loop = SimpleNamespace(
        tick=AsyncMock(),
        stop=MagicMock(),
        shutdown=AsyncMock(),
    )
    signal_loop = _SignalLoop()

    def build_event_loop(**_kwargs: object) -> SimpleNamespace:
        async def tick_once() -> None:
            signal_loop.callback()

        event_loop.tick.side_effect = tick_once
        return event_loop

    drain = AsyncMock(return_value=19)
    write_ready = MagicMock()
    monkeypatch.setattr(child, "init_engine_from_config", MagicMock(return_value=engine))
    monkeypatch.setattr(child, "ensure_tables", ensure_tables)
    monkeypatch.setattr(
        child,
        "create_async_session_factory",
        MagicMock(return_value=session_factory),
    )
    monkeypatch.setattr("general_ludd.event_loop.loop.EventLoop", build_event_loop)
    monkeypatch.setattr(child.asyncio, "get_running_loop", lambda: signal_loop)
    monkeypatch.setattr(child, "_drain_spool", drain)
    monkeypatch.setattr(child, "_write_ready", write_ready)

    result = await child._run_writer_loop(
        {
            "database": {"url": "sqlite+aiosqlite:///writer.db"},
            "inbound_spool_path": str(tmp_path / "spool.jsonl"),
            "tick_interval": 0.01,
        },
        str(tmp_path / "ready.json"),
        "nonce",
        False,
    )

    assert result == 0
    ensure_tables.assert_awaited_once_with(engine)
    event_loop.tick.assert_awaited_once()
    drain.assert_awaited_once_with(
        str(tmp_path / "spool.jsonl"), 0, engine
    )
    write_ready.assert_called_once_with(str(tmp_path / "ready.json"), "nonce")
    event_loop.stop.assert_called_once_with()
    event_loop.shutdown.assert_awaited_once_with()
    engine.dispose.assert_awaited_once_with()
    assert signal_loop.removed == [signal.SIGTERM]


@pytest.mark.asyncio
async def test_run_writer_loop_disposes_engine_when_schema_setup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _Engine()
    ensure_tables = AsyncMock(side_effect=RuntimeError("schema failed"))
    monkeypatch.setattr(child, "init_engine_from_config", MagicMock(return_value=engine))
    monkeypatch.setattr(child, "ensure_tables", ensure_tables)

    with pytest.raises(RuntimeError, match="schema failed"):
        await child._run_writer_loop(
            {"database": {"url": "sqlite+aiosqlite:///writer.db"}},
            "ready.json",
            "nonce",
            False,
        )

    engine.dispose.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_run_writer_loop_uses_signal_fallback_and_swallows_cleanup_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _Engine()
    event_loop = SimpleNamespace(
        tick=AsyncMock(),
        stop=MagicMock(),
        shutdown=AsyncMock(side_effect=RuntimeError("shutdown failed")),
    )
    signal_loop = _SignalLoop(reject_signal_handler=True)
    original_signal = child.signal.signal

    def install_fallback(
        sig: signal.Signals, callback: MagicMock
    ) -> object:
        if sig == signal.SIGTERM:
            signal_loop.fallback_callback = callback
            return MagicMock()
        return original_signal(sig, callback)

    def build_event_loop(**_kwargs: object) -> SimpleNamespace:
        async def failing_tick() -> None:
            signal_loop.fallback_callback(signal.SIGTERM, None)
            raise RuntimeError("tick failed")

        event_loop.tick.side_effect = failing_tick
        return event_loop

    monkeypatch.setattr(child, "init_engine_from_config", MagicMock(return_value=engine))
    monkeypatch.setattr(child, "ensure_tables", AsyncMock())
    monkeypatch.setattr(child, "create_async_session_factory", MagicMock())
    monkeypatch.setattr("general_ludd.event_loop.loop.EventLoop", build_event_loop)
    monkeypatch.setattr(child.asyncio, "get_running_loop", lambda: signal_loop)
    monkeypatch.setattr(child.signal, "signal", install_fallback)

    result = await child._run_writer_loop(
        {"database": {"url": "sqlite+aiosqlite:///writer.db"}},
        "ready.json",
        "nonce",
        True,
    )

    assert result == 0
    event_loop.tick.assert_awaited_once()
    event_loop.shutdown.assert_awaited_once_with()
    engine.dispose.assert_awaited_once_with()


def test_main_routes_database_mode_and_sigterm_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "writer.json"
    config_path.write_text(
        json.dumps(
            {
                "database": {"url": "sqlite+aiosqlite:///writer.db"},
                "ignore_sigterm": True,
            }
        )
    )
    run_writer = AsyncMock(return_value=17)
    original_signal = child.signal.signal

    def install_signal(
        sig: signal.Signals, handler: object
    ) -> object:
        if sig == signal.SIGTERM:
            return signal.SIG_DFL
        return original_signal(sig, handler)

    signal_install = MagicMock(side_effect=install_signal)
    monkeypatch.setattr(child, "_run_writer_loop", run_writer)
    monkeypatch.setattr(child.signal, "signal", signal_install)

    result = child.main(
        ["_child", str(config_path), str(tmp_path / "ready.json"), "nonce"]
    )

    assert result == 17
    signal_install.assert_any_call(signal.SIGTERM, signal.SIG_IGN)
    run_writer.assert_awaited_once()


def test_main_returns_error_when_database_loop_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "writer.json"
    config_path.write_text(
        json.dumps({"database": {"url": "sqlite+aiosqlite:///writer.db"}})
    )
    monkeypatch.setattr(
        child,
        "_run_writer_loop",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )

    assert (
        child.main(
            ["_child", str(config_path), str(tmp_path / "ready.json"), "nonce"]
        )
        == 1
    )


def test_main_stub_writes_ready_and_exits_after_interrupted_sleep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "writer.json"
    config_path.write_text("{}")
    ready = MagicMock()
    monkeypatch.setattr(child, "_write_ready", ready)
    monkeypatch.setattr(
        child.time, "sleep", MagicMock(side_effect=InterruptedError)
    )

    result = child.main(
        ["_child", str(config_path), str(tmp_path / "ready.json"), "nonce"]
    )

    assert result == 0
    ready.assert_called_once_with(str(tmp_path / "ready.json"), "nonce")


def test_main_reports_usage_and_config_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert child.main(["_child"]) == 2
    assert "usage:" in capsys.readouterr().err

    config_path = tmp_path / "writer.json"
    config_path.write_text("[]")
    assert (
        child.main(
            ["_child", str(config_path), str(tmp_path / "ready.json"), "nonce"]
        )
        == 1
    )
    assert "config error:" in capsys.readouterr().err
