"""E2e test for watchdog.ts: event-driven watchdog lifecycle.

Invokes the actual TypeScript plugin via node --experimental-strip-types
in isolated temp dirs, verifying heartbeat, PID cleanup, env disable,
subagent-context firing, and fail-open behavior.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugins" / "watchdog.ts"

_ts_counter = 0


def _run_watchdog(
    ts_code: str,
    env_override: dict | None = None,
    cwd: str | None = None,
    timeout: int = 15,
) -> subprocess.CompletedProcess:
    global _ts_counter
    _ts_counter += 1
    tmp = Path(tempfile.mktemp(suffix=".ts", prefix=f"watchdog_e2e_{_ts_counter}_"))
    tmp.write_text(ts_code)
    try:
        env = os.environ.copy()
        env["OPENCODE_SUBAGENT"] = ""
        if env_override:
            env.update(env_override)
        proc = subprocess.run(
            ["node", "--experimental-strip-types", str(tmp)],
            capture_output=True, text=True, timeout=timeout,
            cwd=cwd or str(ROOT), env=env,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"Node exit {proc.returncode}:\nstderr: {proc.stderr[:800]}\n"
                f"stdout: {proc.stdout[:400]}"
            )
        return proc
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _code(event_type: str) -> str:
    """Generate TS code that loads plugin and fires the given event type."""
    return f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin.event({{event: {{type: "{event_type}"}}}})
console.log("OK")
"""


# ─── session.created → heartbeat + PID sync ──────────────────────────────────


def test_session_created_fires_heartbeat(tmp_path):
    """Heartbeat is written to alive file on session.created."""
    alive_file = tmp_path / "alive.json"
    gates_dir = tmp_path / ".gate-logs"
    gates_dir.mkdir()
    (gates_dir / "watchdog.pid").write_text("12345")

    pid_file = tmp_path / "out.pid"

    _run_watchdog(
        _code("session.created"),
        env_override={
            "GLUDD_ALIVE_PATH": str(alive_file),
            "GLUDD_WATCHDOG_PID_FILE": str(pid_file),
        },
        cwd=str(tmp_path),
    )

    assert alive_file.exists(), "Heartbeat file should exist after session.created"
    data = json.loads(alive_file.read_text())
    assert "watchdog" in data, f"watchdog key missing: {data}"
    assert "last_seen" in data["watchdog"], f"last_seen missing: {data['watchdog']}"

    # PID sync: literal .gate-logs/watchdog.pid → PID_FILE override
    assert pid_file.exists(), "PID_FILE should exist after literal→override sync"
    assert pid_file.read_text().strip() == "12345"


# ─── session.deleted → PID cleanup ──────────────────────────────────────────


def test_session_deleted_cleans_pid_files(tmp_path):
    """session.deleted removes PID_FILE and TASK_PID_FILE."""
    gates_dir = tmp_path / ".gate-logs"
    gates_dir.mkdir(exist_ok=True)

    pid_file = tmp_path / "wd.pid"
    task_file = gates_dir / "task-watchdog.pid"
    pid_file.write_text("99999")
    task_file.write_text("99999")

    _run_watchdog(
        _code("session.deleted"),
        env_override={"GLUDD_WATCHDOG_PID_FILE": str(pid_file)},
        cwd=str(tmp_path),
    )

    assert not pid_file.exists(), (
        f"PID_FILE {pid_file} should be unlinked after session.deleted"
    )
    assert not task_file.exists(), (
        f"TASK_PID_FILE {task_file} should be unlinked after session.deleted"
    )


# ─── Subagent context: watchdog fires everywhere ────────────────────────────


def test_subagent_context_still_fires_heartbeat(tmp_path):
    """Watchdog fires even under OPENCODE_SUBAGENT=1 (by design)."""
    alive_file = tmp_path / "alive.json"

    _run_watchdog(
        _code("session.created"),
        env_override={
            "OPENCODE_SUBAGENT": "1",
            "GLUDD_ALIVE_PATH": str(alive_file),
        },
        cwd=str(tmp_path),
    )

    assert alive_file.exists(), (
        "Heartbeat should fire even in subagent context"
    )
    data = json.loads(alive_file.read_text())
    assert "watchdog" in data


# ─── Env disable: GLUDD_WATCHDOG_ENABLED=0 ──────────────────────────────────


def test_env_disable_skips_event_handler(tmp_path):
    """GLUDD_WATCHDOG_ENABLED=0 skips heartbeat and all event logic."""
    alive_file = tmp_path / "alive.json"
    pid_file = tmp_path / "wd.pid"
    pid_file.write_text("99999")

    _run_watchdog(
        _code("session.deleted"),
        env_override={
            "GLUDD_WATCHDOG_ENABLED": "0",
            "GLUDD_ALIVE_PATH": str(alive_file),
            "GLUDD_WATCHDOG_PID_FILE": str(pid_file),
        },
        cwd=str(tmp_path),
    )

    # Heartbeat should NOT have been written
    assert not alive_file.exists(), (
        "Heartbeat must NOT fire when GLUDD_WATCHDOG_ENABLED=0"
    )
    # PID files should NOT have been cleaned up
    assert pid_file.exists(), (
        "PID file must survive when watchdog is disabled"
    )


# ─── server.connected also starts watchdog ───────────────────────────────────


def test_server_connected_fires_heartbeat(tmp_path):
    """server.connected is another trigger for watchdog startup."""
    alive_file = tmp_path / "alive.json"

    _run_watchdog(
        _code("server.connected"),
        env_override={"GLUDD_ALIVE_PATH": str(alive_file)},
        cwd=str(tmp_path),
    )

    assert alive_file.exists(), "Heartbeat should fire on server.connected"
    data = json.loads(alive_file.read_text())
    assert "watchdog" in data


# ─── Fail-open: corrupt PID file ────────────────────────────────────────────


def test_corrupt_pid_fails_open(tmp_path):
    """Non-numeric PID content must not crash the plugin (fail-open)."""
    gates_dir = tmp_path / ".gate-logs"
    gates_dir.mkdir(exist_ok=True)

    pid_file = tmp_path / "wd.pid"
    task_file = gates_dir / "task-watchdog.pid"
    pid_file.write_text("not-a-number-at-all")
    task_file.write_text("")

    proc = _run_watchdog(
        _code("session.deleted"),
        env_override={"GLUDD_WATCHDOG_PID_FILE": str(pid_file)},
        cwd=str(tmp_path),
    )

    assert proc.returncode == 0, (
        f"Corrupt PID must not crash; exit {proc.returncode}\n{proc.stderr[:300]}"
    )
    # Files should still be cleaned up despite bad PID content
    assert not pid_file.exists(), "Corrupt PID_FILE should still be unlinked"
    assert not task_file.exists(), "Corrupt TASK_PID_FILE should still be unlinked"


# ─── Heartbeat idempotent across repeated events ────────────────────────────


def test_heartbeat_updates_on_repeated_events(tmp_path):
    """Multiple events all update the same alive file."""
    alive_file = tmp_path / "alive.json"

    _run_watchdog(
        _code("session.created"),
        env_override={"GLUDD_ALIVE_PATH": str(alive_file)},
        cwd=str(tmp_path),
    )
    ts1 = json.loads(alive_file.read_text())["watchdog"]["last_seen"]

    import time
    time.sleep(0.05)

    _run_watchdog(
        _code("server.connected"),
        env_override={"GLUDD_ALIVE_PATH": str(alive_file)},
        cwd=str(tmp_path),
    )
    ts2 = json.loads(alive_file.read_text())["watchdog"]["last_seen"]

    assert ts2 > ts1, (
        f"Heartbeat should advance on repeated events: {ts1} → {ts2}"
    )


# ─── Unknown event type handled gracefully ───────────────────────────────────


def test_unknown_event_type_does_not_crash(tmp_path):
    """An event type the watchdog doesn't handle should still fire heartbeat."""
    alive_file = tmp_path / "alive.json"

    proc = _run_watchdog(
        _code("unknown.mystery.event"),
        env_override={"GLUDD_ALIVE_PATH": str(alive_file)},
        cwd=str(tmp_path),
    )

    assert proc.returncode == 0, (
        f"Unknown event must not crash; exit {proc.returncode}\n{proc.stderr[:300]}"
    )
    # Heartbeat fires before event-type dispatch, so it should still exist
    assert alive_file.exists(), "Heartbeat should fire even for unknown events"
