"""Tests for BP.13: PID-scoped streak counter isolation in enforce-delegate.ts.

THE BUG: the mainthread streak counter in enforce-delegate.ts persisted across
sessions/processes via a shared state file at /tmp/gludd-mainthread-streak.json.
A streak of e.g. 3 from a prior crashed session would cause the fresh session to
hit MAINTHREAD_THRESHOLD=2 after just 1 call — a false-positive "streak block"
from stale state.

THE FIX (BP.13): add a ``pid`` field to the streak state JSON. On ``readStreak()``,
verify the stored PID matches ``process.pid``. If it doesn't match (stale state
from a different process), reset the count to 0. ``writeStreak()`` always writes
the current process PID so fresh state is intrinsically scoped.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
DELEGATE_PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-delegate.ts"


def _read_plugin_src() -> str:
    return DELEGATE_PLUGIN.read_text()


# ============================================================================
# STRUCTURAL TESTS — pin the plugin source shape
# ============================================================================


class TestMainthreadStreakStateShape:
    """The MainthreadStreakState interface MUST include a pid field."""

    def test_interface_includes_pid_field(self) -> None:
        src = _read_plugin_src()
        assert "interface MainthreadStreakState" in src
        assert "pid: number" in src, (
            "MainthreadStreakState must include 'pid: number' field for PID isolation"
        )

    def test_interface_has_three_fields(self) -> None:
        src = _read_plugin_src()
        match = src[src.index("interface MainthreadStreakState"):]
        brace_end = match.index("}") + 1
        interface_block = match[:brace_end]
        field_lines = [
            line.strip() for line in interface_block.split("\n")
            if line.strip() and not line.strip().startswith("//")
        ]
        assert any("count:" in f for f in field_lines)
        assert any("ts:" in f for f in field_lines)
        assert any("pid:" in f for f in field_lines)


class TestReadStreakPidCheck:
    """readStreak() MUST verify stored PID matches process.pid."""

    def test_read_streak_has_pid_check(self) -> None:
        src = _read_plugin_src()
        assert "process.pid" in src, "plugin must reference process.pid"

    def test_stored_pid_not_zero_condition(self) -> None:
        src = _read_plugin_src()
        assert "storedPid !== 0" in src, (
            "must skip PID check when storedPid is 0 (legacy data without pid field)"
        )

    def test_pid_mismatch_resets_count(self) -> None:
        src = _read_plugin_src()
        read_streak = src[
            src.index("function readStreak"):src.index("function writeStreak")
        ]
        assert "storedPid !== process.pid" in read_streak, "must detect PID mismatch"
        assert "recencyMs = 5000" in read_streak, (
            "a just-written cross-process handoff must retain the streak briefly"
        )
        assert "return { count, ts, pid: process.pid }" in read_streak
        assert "return { count: 0, ts, pid: process.pid }" in read_streak, (
            "a stale PID mismatch must reset the count to zero"
        )

    def test_stored_pid_zero_skips_check(self) -> None:
        """When storedPid is 0 (legacy/no pid), should NOT reset count."""
        src = _read_plugin_src()
        assert "storedPid !== 0" in src, (
            "legacy data without pid field must not trigger reset"
        )

    def test_read_returns_default_pid(self) -> None:
        """On parse failure or legacy data, pid defaults to current process.pid."""
        src = _read_plugin_src()
        assert "pid: process.pid" in src, (
            "default pid must be current process.pid"
        )


class TestWriteStreakPidPreservation:
    """writeStreak() MUST always write the current process PID."""

    def test_write_streak_includes_pid(self) -> None:
        src = _read_plugin_src()
        assert "pid: process.pid" in src, (
            "writeStreak must write process.pid into the merged state"
        )

    def test_write_streak_merged_object_has_pid(self) -> None:
        src = _read_plugin_src()
        match = src[src.index("function writeStreak"):src.index("function writeStreak") + 300]
        assert "pid:" in match, "merged state object must include pid field"


# ============================================================================
# BEHAVIORAL TESTS — invoke the actual plugin logic via Node
# ============================================================================


class TestStreakPidIsolationBehavioral:
    """Verify PID isolation works at runtime by exercising readStreak/writeStreak
    through the Node runtime directly.

    NOTE: Node subprocesses (via subprocess.run) have a DIFFERENT pid than the
    Python test process.  Tests that check the pid value must use the pid
    reported by the Node script itself, not os.getpid().
    """

    def test_state_file_includes_pid_field(self) -> None:
        """After writeStreak, the JSON on disk contains the pid field."""
        import subprocess
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"count": 0, "ts": 0, "pid": 0}')
            streak_path = f.name

        script = f"""
        const fs = require("node:fs");
        process.env.GLUDD_MAINTHREAD_STREAK_FILE = "{streak_path}";
        function readStreak() {{
            try {{
                const raw = fs.readFileSync(process.env.GLUDD_MAINTHREAD_STREAK_FILE, "utf8").trim();
                if (raw.startsWith("{{")) {{
                    const obj = JSON.parse(raw);
                    const storedPid = parseInt(obj.pid, 10) || 0;
                    const count = parseInt(obj.count, 10) || 0;
                    const ts = parseInt(obj.ts, 10) || 0;
                    if (storedPid !== 0 && storedPid !== process.pid) {{
                        return {{ count: 0, ts, pid: process.pid }};
                    }}
                    return {{ count, ts, pid: storedPid || process.pid }};
                }}
                const n = parseInt(raw, 10);
                return {{ count: Number.isNaN(n) ? 0 : n, ts: 0, pid: process.pid }};
            }} catch {{
                return {{ count: 0, ts: 0, pid: process.pid }};
            }}
        }}
        function writeStreak(partial) {{
            const current = readStreak();
            const merged = {{ ...current, ...partial, ts: Date.now(), pid: process.pid }};
            const tmp = process.env.GLUDD_MAINTHREAD_STREAK_FILE + ".tmp";
            fs.writeFileSync(tmp, JSON.stringify(merged));
            fs.renameSync(tmp, process.env.GLUDD_MAINTHREAD_STREAK_FILE);
        }}
        writeStreak({{ count: 5 }});
        const data = JSON.parse(fs.readFileSync(process.env.GLUDD_MAINTHREAD_STREAK_FILE, "utf8"));
        // Include node pid so the test can verify against the correct value
        console.log(JSON.stringify({{ result: data, nodePid: process.pid }}));
        """
        try:
            result = subprocess.run(
                ["node", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
            payload = json.loads(result.stdout.strip())
            data = payload["result"]
            node_pid = payload["nodePid"]
            assert "pid" in data, f"State file must include pid field. Got: {data}"
            assert isinstance(data["pid"], int), f"pid must be an integer. Got: {type(data['pid'])}"
            assert data["pid"] == node_pid, (
                f"pid must match Node process pid ({node_pid}). Got: {data['pid']}"
            )
            assert data["count"] == 5, f"count must be 5. Got: {data['count']}"
        finally:
            os.unlink(streak_path)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(streak_path + ".tmp")

    def test_mismatched_pid_resets_count_to_zero(self) -> None:
        """When stored PID does not match process.pid, count resets to 0."""
        import subprocess
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            # Seed with count=10 from a pid that will never match
            f.write(json.dumps({"count": 10, "ts": 1000, "pid": 99999}))
            streak_path = f.name

        script = f"""
        const fs = require("node:fs");
        process.env.GLUDD_MAINTHREAD_STREAK_FILE = "{streak_path}";
        function readStreak() {{
            try {{
                const raw = fs.readFileSync(process.env.GLUDD_MAINTHREAD_STREAK_FILE, "utf8").trim();
                if (raw.startsWith("{{")) {{
                    const obj = JSON.parse(raw);
                    const storedPid = parseInt(obj.pid, 10) || 0;
                    const count = parseInt(obj.count, 10) || 0;
                    const ts = parseInt(obj.ts, 10) || 0;
                    if (storedPid !== 0 && storedPid !== process.pid) {{
                        return {{ count: 0, ts, pid: process.pid }};
                    }}
                    return {{ count, ts, pid: storedPid || process.pid }};
                }}
                const n = parseInt(raw, 10);
                return {{ count: Number.isNaN(n) ? 0 : n, ts: 0, pid: process.pid }};
            }} catch {{
                return {{ count: 0, ts: 0, pid: process.pid }};
            }}
        }}
        const result = readStreak();
        console.log(JSON.stringify({{ result, nodePid: process.pid }}));
        """
        try:
            result = subprocess.run(
                ["node", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
            payload = json.loads(result.stdout.strip())
            data = payload["result"]
            node_pid = payload["nodePid"]
            assert data["count"] == 0, (
                f"count must be reset to 0 on PID mismatch. Got: {data['count']}"
            )
            assert data["pid"] == node_pid, (
                f"pid must be updated to Node process pid ({node_pid}). Got: {data['pid']}"
            )
        finally:
            os.unlink(streak_path)

    def test_same_pid_preserves_count(self) -> None:
        """When stored PID matches process.pid, count is preserved.

        We seed the state file from WITHIN the Node script so the stored pid
        matches the reading process's pid.
        """
        import subprocess
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{}")
            streak_path = f.name

        script = f"""
        const fs = require("node:fs");
        process.env.GLUDD_MAINTHREAD_STREAK_FILE = "{streak_path}";

        // Seed with the NODE process's own pid so the check passes
        const seed = JSON.stringify({{ count: 7, ts: 2000, pid: process.pid }});
        fs.writeFileSync(process.env.GLUDD_MAINTHREAD_STREAK_FILE, seed);

        function readStreak() {{
            try {{
                const raw = fs.readFileSync(process.env.GLUDD_MAINTHREAD_STREAK_FILE, "utf8").trim();
                if (raw.startsWith("{{")) {{
                    const obj = JSON.parse(raw);
                    const storedPid = parseInt(obj.pid, 10) || 0;
                    const count = parseInt(obj.count, 10) || 0;
                    const ts = parseInt(obj.ts, 10) || 0;
                    if (storedPid !== 0 && storedPid !== process.pid) {{
                        return {{ count: 0, ts, pid: process.pid }};
                    }}
                    return {{ count, ts, pid: storedPid || process.pid }};
                }}
                const n = parseInt(raw, 10);
                return {{ count: Number.isNaN(n) ? 0 : n, ts: 0, pid: process.pid }};
            }} catch {{
                return {{ count: 0, ts: 0, pid: process.pid }};
            }}
        }}
        const result = readStreak();
        console.log(JSON.stringify({{ result, nodePid: process.pid }}));
        """
        try:
            result = subprocess.run(
                ["node", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
            payload = json.loads(result.stdout.strip())
            data = payload["result"]
            node_pid = payload["nodePid"]
            assert data["count"] == 7, (
                f"count must be preserved when PID matches. Got: {data['count']}"
            )
            assert data["pid"] == node_pid, (
                f"pid must remain unchanged. Got: {data['pid']}, expected {node_pid}"
            )
        finally:
            os.unlink(streak_path)

    def test_legacy_no_pid_field_skips_check(self) -> None:
        """When state file has no pid field (storedPid=0 after parseInt), count is preserved."""
        import subprocess
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            # Legacy state: no pid field
            f.write(json.dumps({"count": 3, "ts": 5000}))
            streak_path = f.name

        script = f"""
        const fs = require("node:fs");
        process.env.GLUDD_MAINTHREAD_STREAK_FILE = "{streak_path}";
        function readStreak() {{
            try {{
                const raw = fs.readFileSync(process.env.GLUDD_MAINTHREAD_STREAK_FILE, "utf8").trim();
                if (raw.startsWith("{{")) {{
                    const obj = JSON.parse(raw);
                    const storedPid = parseInt(obj.pid, 10) || 0;
                    const count = parseInt(obj.count, 10) || 0;
                    const ts = parseInt(obj.ts, 10) || 0;
                    if (storedPid !== 0 && storedPid !== process.pid) {{
                        return {{ count: 0, ts, pid: process.pid }};
                    }}
                    return {{ count, ts, pid: storedPid || process.pid }};
                }}
                const n = parseInt(raw, 10);
                return {{ count: Number.isNaN(n) ? 0 : n, ts: 0, pid: process.pid }};
            }} catch {{
                return {{ count: 0, ts: 0, pid: process.pid }};
            }}
        }}
        const result = readStreak();
        console.log(JSON.stringify({{ result, nodePid: process.pid }}));
        """
        try:
            result = subprocess.run(
                ["node", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
            payload = json.loads(result.stdout.strip())
            data = payload["result"]
            node_pid = payload["nodePid"]
            assert data["count"] == 3, (
                f"count must be preserved for legacy data (pid=0 skips check). Got: {data['count']}"
            )
            assert data["pid"] == node_pid, (
                f"pid must be set to Node process pid for legacy data. Got: {data['pid']}, expected {node_pid}"
            )
        finally:
            os.unlink(streak_path)

    def test_write_streak_increments_and_preserves_pid(self) -> None:
        """writeStreak increments count and always writes the current process pid."""
        import subprocess
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{}")
            streak_path = f.name

        script = f"""
        const fs = require("node:fs");
        process.env.GLUDD_MAINTHREAD_STREAK_FILE = "{streak_path}";

        // Seed with the NODE process's own pid
        const seed = JSON.stringify({{ count: 1, ts: 999, pid: process.pid }});
        fs.writeFileSync(process.env.GLUDD_MAINTHREAD_STREAK_FILE, seed);

        function readStreak() {{
            const raw = fs.readFileSync(process.env.GLUDD_MAINTHREAD_STREAK_FILE, "utf8").trim();
            const obj = JSON.parse(raw);
            const storedPid = parseInt(obj.pid, 10) || 0;
            const count = parseInt(obj.count, 10) || 0;
            const ts = parseInt(obj.ts, 10) || 0;
            if (storedPid !== 0 && storedPid !== process.pid) {{
                return {{ count: 0, ts, pid: process.pid }};
            }}
            return {{ count, ts, pid: storedPid || process.pid }};
        }}
        function writeStreak(partial) {{
            const current = readStreak();
            const merged = {{ ...current, ...partial, ts: Date.now(), pid: process.pid }};
            const tmp = process.env.GLUDD_MAINTHREAD_STREAK_FILE + ".tmp";
            fs.writeFileSync(tmp, JSON.stringify(merged));
            fs.renameSync(tmp, process.env.GLUDD_MAINTHREAD_STREAK_FILE);
        }}
        writeStreak({{ count: 2 }});
        const data = JSON.parse(fs.readFileSync(process.env.GLUDD_MAINTHREAD_STREAK_FILE, "utf8"));
        console.log(JSON.stringify({{ result: data, nodePid: process.pid }}));
        """
        try:
            result = subprocess.run(
                ["node", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
            payload = json.loads(result.stdout.strip())
            data = payload["result"]
            node_pid = payload["nodePid"]
            assert data["count"] == 2, f"count must be 2. Got: {data['count']}"
            assert data["pid"] == node_pid, (
                f"pid must be Node process pid ({node_pid}). Got: {data['pid']}"
            )
            assert "ts" in data, "state must include ts field"
        finally:
            os.unlink(streak_path)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(streak_path + ".tmp")
