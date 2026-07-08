"""Writer subprocess child entrypoint (B3.1.3 — WP-B1, Slices 1 + 3).

Invoked as::

    python -m general_ludd.writer._child <config_path> <ready_path> <nonce>

The child has two operating modes, selected by the config file's contents:

  * **Slice-1 stub** (config has no ``database.url``): writes the readiness
    nonce and sleeps until killed — exercises the parent's lifecycle only.
  * **Slice-3 real** (config has ``database.url``): builds a WRITE engine,
    ensures tables, writes the readiness nonce, then runs the EventLoop tick
    loop + drains the inbound WriteQueue spool between ticks. SIGTERM finishes
    the current tick and exits 0.

Readiness handshake (identical in both modes): the child writes
``{"nonce": nonce}`` into ``ready_path`` AFTER successful initialisation. The
parent (:class:`~general_ludd.writer.process.WriterProcess`) compares the
file content against the nonce it generated and fail-closes on mismatch.

Test hooks (read from the config file):

  * ``skip_ready=True`` — child never writes the nonce (exercises the parent's
    handshake-timeout path).
  * ``ignore_sigterm=True`` — child installs a no-op SIGTERM handler
    (exercises the parent's SIGKILL-escalation path).

Nothing in this module is imported by the parent at runtime — it only ever
runs inside the child interpreter.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
import time
from typing import Any

from sqlalchemy import text

from general_ludd.db.session import (
    create_async_session_factory,
    ensure_tables,
    init_engine_from_config,
)

logger = logging.getLogger(__name__)

__all__ = ["main"]


def _load_config(config_path: str) -> dict[str, Any]:
    """Load and validate the child config JSON file."""
    with open(config_path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"writer child config must be a JSON object, got {type(data)!r}")
    return data


def _write_ready(ready_path: str, nonce: str) -> None:
    """Atomically write ``{"nonce": nonce}`` into ``ready_path``.

    Atomic write (tmp + os.replace) so the parent never reads a partial
    nonce — a partial write would be misread as a non-match and the parent
    would fail-closed unnecessarily on a slow disk.
    """
    payload = json.dumps({"nonce": nonce}).encode("utf-8")
    tmp_path = ready_path + ".tmp"
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp_path, ready_path)


async def _drain_spool(
    spool_path: str, offset: int, engine: Any
) -> int:
    """Drain new JSONL envelopes from ``spool_path`` starting at ``offset``.

    Each line is ``{"topic": "execute_sql", "payload": {"sql": "...",
    "params": [...]}}``. The envelope is applied against ``engine`` inside a
    short-lived transaction. Returns the new byte offset (so the caller can
    resume on the next tick without re-reading already-applied lines).

    A missing file is a no-op (the spool may not have been created yet).
    Malformed lines are logged and skipped — a bad envelope must NOT kill the
    writer subprocess.
    """
    try:
        file_size = os.path.getsize(spool_path)
    except FileNotFoundError:
        return offset
    if file_size <= offset:
        return offset

    try:
        with open(spool_path, encoding="utf-8") as fh:
            fh.seek(offset)
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    envelope = json.loads(stripped)
                    await _apply_envelope(engine, envelope)
                except Exception:
                    logger.exception(
                        "writer child: failed to apply envelope, skipping: %s",
                        stripped[:200],
                    )
            new_offset = fh.tell()
    except FileNotFoundError:
        return offset
    return new_offset


async def _apply_envelope(engine: Any, envelope: dict[str, Any]) -> None:
    """Apply a single drained envelope against the write engine.

    Currently supports the ``execute_sql`` topic: runs the payload's ``sql``
    (with optional ``params``) inside a short-lived transaction and commits.
    Unknown topics are logged and ignored (forward-compat: future topics can
    be added without touching the drain loop).
    """
    topic = envelope.get("topic")
    if topic == "execute_sql":
        payload = envelope.get("payload", {})
        sql = payload.get("sql")
        if not isinstance(sql, str):
            raise ValueError("execute_sql envelope missing 'sql' string")
        params = payload.get("params", [])
        async with engine.begin() as conn:
            await conn.execute(text(sql), params)
        logger.debug("writer child: applied execute_sql envelope (%d chars)", len(sql))
        return
    logger.debug("writer child: ignoring envelope with unknown topic %r", topic)


async def _run_writer_loop(
    config: dict[str, Any], ready_path: str, nonce: str, skip_ready: bool
) -> int:
    """Slice-3 main loop: build engine → ensure tables → write nonce → run."""
    from general_ludd.event_loop.loop import EventLoop

    db_config = config["database"]
    engine = init_engine_from_config(db_config)
    try:
        await ensure_tables(engine)
    except Exception:
        logger.exception("writer child: ensure_tables failed")
        await engine.dispose()
        raise

    session_factory = create_async_session_factory(engine)
    event_loop = EventLoop(config=config, session=session_factory)

    if not skip_ready:
        _write_ready(ready_path, nonce)
    logger.info("writer child: readiness nonce written, entering tick loop")

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_sigterm() -> None:
        logger.info("writer child: SIGTERM received, draining after current tick")
        stopping.set()

    sigterm_installed = False
    try:
        loop.add_signal_handler(signal.SIGTERM, _on_sigterm)
        sigterm_installed = True
    except (NotImplementedError, RuntimeError):
        # Fallback for platforms without loop.add_signal_handler (Windows).
        signal.signal(signal.SIGTERM, lambda *_: stopping.set())
        sigterm_installed = True

    spool_path = config.get("inbound_spool_path", "")
    tick_interval = float(config.get("tick_interval", 0.5))
    offset = 0

    try:
        while not stopping.is_set():
            try:
                await event_loop.tick()
            except Exception:
                logger.exception("writer child: EventLoop.tick() raised (continuing)")

            if spool_path:
                try:
                    offset = await _drain_spool(spool_path, offset, engine)
                except Exception:
                    logger.exception("writer child: spool drain raised (continuing)")

            if stopping.is_set():
                break
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stopping.wait(), timeout=tick_interval)

        logger.info("writer child: tick loop stopped, disposing engine")
    finally:
        event_loop.stop()
        with _suppress_exc():
            await event_loop.shutdown()
        await engine.dispose()
        if sigterm_installed:
            with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                loop.remove_signal_handler(signal.SIGTERM)

    return 0


class _suppress_exc:
    """Context manager that logs and swallows ALL exceptions (best-effort cleanup)."""

    def __enter__(self) -> _suppress_exc:
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        if exc_info[0] is not None:
            logger.debug("writer child: suppressed cleanup exception", exc_info=exc_info)
        return True


def main(argv: list[str]) -> int:
    # argv: <prog> <config_path> <ready_path> <nonce>
    if len(argv) < 4:
        sys.stderr.write(
            "usage: _child <config_path> <ready_path> <nonce>\n"
        )
        return 2

    config_path = argv[1]
    ready_path = argv[2]
    nonce = argv[3]

    try:
        config = _load_config(config_path)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"config error: {exc}\n")
        return 1

    skip_ready = bool(config.get("skip_ready", False))
    ignore_sigterm = bool(config.get("ignore_sigterm", False))

    if ignore_sigterm:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    db_config = config.get("database")
    has_db_url = isinstance(db_config, dict) and bool(db_config.get("url"))

    if has_db_url:
        try:
            return asyncio.run(_run_writer_loop(config, ready_path, nonce, skip_ready))
        except Exception:
            logger.exception("writer child: _run_writer_loop exited with error")
            return 1

    # Slice-1 stub path: write nonce and sleep until killed.
    if not skip_ready:
        _write_ready(ready_path, nonce)
    try:
        while True:
            time.sleep(3600)
    except InterruptedError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
