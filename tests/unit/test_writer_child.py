"""B3.1.3 Slice 3 — writer subprocess child entrypoint.

The child (``python -m general_ludd.writer._child <config_path> <ready_path>
<nonce>``) is the subprocess that owns the WRITE engine and runs EventLoop
ticks + drains the inbound WriteQueue spool. These tests spawn the child as a
real subprocess (matching the abtest ``_child`` pattern) and assert the 7
behaviors required by Slice 3:

  1. writes the readiness token file (``{"nonce": nonce}`` JSON matching the
     Slice-1 ``WriterProcess`` handshake)
  2. constructs a WRITE engine — a CREATE TABLE envelope succeeds (a read-only
     engine would reject DDL)
  3. starts the EventLoop tick loop in an asyncio loop (stays alive, no crash)
  4. drains the inbound queue spool — enqueued envelopes are applied as writes
  5. SIGTERM → finishes current tick → exits 0
  6. missing argv → non-zero exit + usage message
  7. malformed config JSON → non-zero exit + error message

The child interface is backward-compatible with the Slice-1 ``WriterProcess``
parent (same 3-arg spawn). When the config lacks ``database.url`` the child
falls back to the Slice-1 stub (write nonce + sleep); when present it runs the
real EventLoop path.

Run:  make test-iso TESTFILE='tests/unit/test_writer_child.py'
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent

_READINESS_TIMEOUT_S = 20.0
_POLL_INTERVAL_S = 0.15


def _write_config(
    tmp_path: Path,
    *,
    db_url: str | None = None,
    nonce: str = "test-nonce-deadbeef",
    tick_interval: float = 0.1,
    extra: dict | None = None,
) -> tuple[Path, Path, str, Path]:
    """Materialise a child config file.

    Returns ``(config_path, ready_path, nonce, spool_path)``.  The
    ``ready_path`` and ``nonce`` are passed as SEPARATE argv elements to the
    child (matching the Slice-1 WriterProcess spawn contract); the config file
    holds only DB + spool + tick-interval fields.
    """
    if db_url is None:
        db_url = f"sqlite+aiosqlite:///{tmp_path}/child.db"
    ready_path = tmp_path / "ready.token"
    spool_path = tmp_path / "inbound.jsonl"
    cfg: dict = {
        "database": {"url": db_url},
        "inbound_spool_path": str(spool_path),
        "tick_interval": tick_interval,
    }
    if extra:
        cfg.update(extra)
    config_path = tmp_path / "child_config.json"
    config_path.write_text(json.dumps(cfg))
    return config_path, ready_path, nonce, spool_path


def _spawn_child(config_path: Path, ready_path: Path, nonce: str) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "general_ludd.writer._child",
            str(config_path),
            str(ready_path),
            nonce,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(_REPO_ROOT),
    )


def _wait_for_readiness(ready_path: Path, nonce: str, timeout: float = _READINESS_TIMEOUT_S) -> None:
    """Poll until the child writes ``{"nonce": nonce}`` into ``ready_path``."""
    deadline = time.monotonic() + timeout
    last_err = ""
    while time.monotonic() < deadline:
        if ready_path.exists():
            try:
                raw = ready_path.read_bytes()
                parsed = json.loads(raw.decode("utf-8", errors="replace"))
                got = parsed.get("nonce") if isinstance(parsed, dict) else None
                if isinstance(got, str) and got == nonce:
                    return
                last_err = f"nonce mismatch: expected {nonce}, got {got!r}"
            except (ValueError, UnicodeError) as exc:
                last_err = f"parse error: {exc}"
        time.sleep(_POLL_INTERVAL_S)
    raise AssertionError(f"readiness nonce not written within {timeout}s: {last_err}")


def _terminate_clean(proc: subprocess.Popen, timeout: float = 10.0) -> int:
    """Send SIGTERM and return the exit code. Kills with SIGKILL if it doesn't exit in time."""
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate(timeout=5)
    return proc.returncode if proc.returncode is not None else -1


def _append_envelope(spool_path: Path, topic: str, payload: dict) -> None:
    line = json.dumps({"topic": topic, "payload": payload}) + "\n"
    with open(spool_path, "a") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


class TestChildReadinessToken:
    def test_child_main_writes_readiness_token(self, tmp_path: Path) -> None:
        config_path, ready_path, nonce, _ = _write_config(tmp_path)
        proc = _spawn_child(config_path, ready_path, nonce)
        try:
            _wait_for_readiness(ready_path, nonce)
        finally:
            _terminate_clean(proc)


class TestChildWriteEngine:
    @pytest.mark.asyncio
    async def test_child_main_builds_write_engine(self, tmp_path: Path) -> None:
        """The child's engine must NOT be read-only — a CREATE TABLE envelope
        succeeds, proving PRAGMA query_only is OFF."""
        config_path, ready_path, nonce, spool_path = _write_config(tmp_path)
        proc = _spawn_child(config_path, ready_path, nonce)
        try:
            _wait_for_readiness(ready_path, nonce)
            _append_envelope(
                spool_path,
                "execute_sql",
                {"sql": "CREATE TABLE IF NOT EXISTS probe (id INTEGER PRIMARY KEY)"},
            )
            deadline = time.monotonic() + 15.0
            created = False
            db_path = tmp_path / "child.db"
            from sqlalchemy.exc import OperationalError

            while time.monotonic() < deadline:
                check_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
                try:
                    async with check_engine.connect() as conn:
                        result = await conn.execute(
                            text(
                                "SELECT name FROM sqlite_master "
                                "WHERE type='table' AND name='probe'"
                            )
                        )
                        if result.scalar() == "probe":
                            created = True
                            break
                except OperationalError:
                    pass
                finally:
                    await check_engine.dispose()
                time.sleep(_POLL_INTERVAL_S)
            assert created, "CREATE TABLE envelope was not applied — engine may be read-only"
        finally:
            _terminate_clean(proc)


class TestChildRunsEventLoop:
    def test_child_main_runs_event_loop(self, tmp_path: Path) -> None:
        """After readiness, the child stays alive running its tick loop (does not crash)."""
        config_path, ready_path, nonce, _ = _write_config(tmp_path, tick_interval=0.1)
        proc = _spawn_child(config_path, ready_path, nonce)
        try:
            _wait_for_readiness(ready_path, nonce)
            time.sleep(1.0)
            assert proc.poll() is None, "child process exited prematurely — event loop crashed"
        finally:
            rc = _terminate_clean(proc)
            assert rc == 0


class TestChildDrainsInboundQueue:
    @pytest.mark.asyncio
    async def test_child_main_drains_inbound_queue(self, tmp_path: Path) -> None:
        """An envelope appended to the spool is drained and applied as a write."""
        config_path, ready_path, nonce, spool_path = _write_config(tmp_path)
        proc = _spawn_child(config_path, ready_path, nonce)
        try:
            _wait_for_readiness(ready_path, nonce)
            _append_envelope(
                spool_path,
                "execute_sql",
                {"sql": "CREATE TABLE IF NOT EXISTS probe (id INTEGER PRIMARY KEY)"},
            )
            _append_envelope(
                spool_path,
                "execute_sql",
                {"sql": "INSERT INTO probe (id) VALUES (42)"},
            )
            db_path = tmp_path / "child.db"
            deadline = time.monotonic() + 15.0
            found = False
            from sqlalchemy.exc import OperationalError

            while time.monotonic() < deadline:
                check_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
                try:
                    async with check_engine.connect() as conn:
                        result = await conn.execute(text("SELECT id FROM probe WHERE id = 42"))
                        if result.scalar() == 42:
                            found = True
                            break
                except OperationalError:
                    # Table not yet created by the child — keep polling.
                    pass
                finally:
                    await check_engine.dispose()
                time.sleep(_POLL_INTERVAL_S)
            assert found, "envelope was not drained/applied — row id=42 not found"
        finally:
            _terminate_clean(proc)


class TestChildSigtermCleanExit:
    def test_child_main_sigterm_exits_clean(self, tmp_path: Path) -> None:
        """SIGTERM → child finishes current tick → exits 0."""
        config_path, ready_path, nonce, _ = _write_config(tmp_path, tick_interval=0.05)
        proc = _spawn_child(config_path, ready_path, nonce)
        try:
            _wait_for_readiness(ready_path, nonce)
            time.sleep(0.3)
            assert proc.poll() is None, "child died before SIGTERM"
            proc.send_signal(signal.SIGTERM)
            proc.communicate(timeout=10.0)
            rc = proc.returncode
            assert rc == 0, f"expected clean exit 0 after SIGTERM, got {rc}"
        finally:
            _terminate_clean(proc)


class TestChildConfigPathRequired:
    def test_child_main_config_path_required(self) -> None:
        """No argv → non-zero exit + usage on stderr."""
        proc = subprocess.Popen(
            [sys.executable, "-m", "general_ludd.writer._child"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(_REPO_ROOT),
        )
        try:
            stdout, stderr = proc.communicate(timeout=15.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            pytest.fail("child did not exit within 15s when given no args")
        assert proc.returncode != 0, "expected non-zero exit when args are missing"
        assert "usage" in (stderr + stdout).lower(), "expected usage message on stderr/stdout"


class TestChildInvalidConfigExitsNonzero:
    def test_child_main_invalid_config_exits_nonzero(self, tmp_path: Path) -> None:
        """Malformed JSON config → non-zero exit + error message."""
        bad_config = tmp_path / "bad.json"
        bad_config.write_text("{ this is not valid json ,, }")
        ready_path = tmp_path / "ready.token"
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "general_ludd.writer._child",
                str(bad_config),
                str(ready_path),
                "some-nonce",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(_REPO_ROOT),
        )
        try:
            stdout, stderr = proc.communicate(timeout=15.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            pytest.fail("child did not exit within 15s when given malformed config")
        assert proc.returncode != 0, "expected non-zero exit on malformed config"
        combined = (stderr + stdout).lower()
        assert "config" in combined or "json" in combined or "error" in combined, (
            f"expected error message mentioning config/json/error; got: {combined!r}"
        )
