"""Tests for scripts/run_gate.sh — concurrent-gate collision-proofing.

Verifies:
  1. A second invocation while the lock is held exits non-zero with the
     "already running" message and does NOT create or delete a basetemp.
  2. The unique-basetemp path (/tmp/gludd-gate-XXXXXX) is used, not the
     old fixed /tmp/gludd-gate-basetemp path.
  3. A successful invocation writes "PASS 0" to .gate-status.
  4. A failing invocation writes "FAIL non-zero-exit" and touches .gate-failed.

All tests use PYTEST_CMD to inject a stub command so they run in milliseconds
without touching the real test suite.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

# The script's basetemp is `mktemp -d /tmp/gludd-gate-XXXXXX` → basename is
# "gludd-gate-" followed by ONLY alphanumerics. Test artifacts (workdirs, lock
# files) all use "gludd-gate-test-…" / "gludd-gate-…-lock-…" which contain a
# hyphen after the prefix, so this pattern excludes them on every platform.
# (On Linux CI tempfile.mkdtemp lands in /tmp and would otherwise pollute the
# leak check; on macOS it lands in $TMPDIR=/var/folders and never did.)
_SCRIPT_BASETEMP_RE = re.compile(r"^gludd-gate-[A-Za-z0-9]+$")

ROOT = Path(__file__).parent.parent.parent
SCRIPT = ROOT / "scripts" / "run_gate.sh"

DEFAULT_LOCK_FILE = "/tmp/gludd-gate.lock"


def _run_gate(
    env_overrides: dict[str, str] | None = None,
    cwd: Path | None = None,
    timeout: int = 15,
    lock_file: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run run_gate.sh in a temp workdir with a fast stub PYTEST_CMD.

    Each call gets its own unique lock file (via GATE_LOCK_FILE) so concurrent
    xdist test workers cannot accidentally block each other.
    """
    workdir = cwd or Path(tempfile.mkdtemp(prefix="gludd-gate-test-"))
    # Pre-create the status file so the script can append to it.
    (workdir / ".gate-status").write_text("")

    # Per-invocation unique lock so concurrent tests don't share state.
    unique_lock = lock_file or tempfile.mktemp(prefix="gludd-gate-test-lock-", dir="/tmp")
    env = {
        **os.environ,
        "PYTEST_CMD": 'python3 -c "import sys; sys.exit(0)"',
        "GATE_LOCK_FILE": unique_lock,
    }
    if env_overrides:
        env.update(env_overrides)

    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(workdir),
        env=env,
        timeout=timeout,
    )


class TestRunGateScript:
    def test_script_exists(self) -> None:
        assert SCRIPT.exists(), f"scripts/run_gate.sh must exist at {SCRIPT}"
        assert SCRIPT.stat().st_size > 0, "run_gate.sh must not be empty"

    def test_successful_run_exits_zero(self) -> None:
        """A stub gate that exits 0 should propagate exit 0."""
        result = _run_gate()
        assert result.returncode == 0, (
            f"Expected exit 0 for a passing stub gate, got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_successful_run_writes_pass_to_gate_status(self) -> None:
        """A passing stub gate must append 'PASS 0' to .gate-status."""
        workdir = Path(tempfile.mkdtemp(prefix="gludd-gate-test-"))
        status_file = workdir / ".gate-status"
        status_file.write_text("")

        _run_gate(cwd=workdir)

        content = status_file.read_text()
        assert "PASS 0" in content, (
            f".gate-status should contain 'PASS 0' after a successful run. Got:\n{content}"
        )

    def test_failing_run_exits_nonzero(self) -> None:
        """A stub gate that exits non-zero should propagate that exit code."""
        result = _run_gate(
            env_overrides={"PYTEST_CMD": 'python3 -c "import sys; sys.exit(1)"'}
        )
        assert result.returncode != 0, (
            "Expected non-zero exit for a failing stub gate"
        )

    def test_failing_run_writes_fail_to_gate_status(self) -> None:
        """A failing stub gate must append 'FAIL non-zero-exit' to .gate-status."""
        workdir = Path(tempfile.mkdtemp(prefix="gludd-gate-test-"))
        status_file = workdir / ".gate-status"
        status_file.write_text("")

        _run_gate(
            env_overrides={"PYTEST_CMD": 'python3 -c "import sys; sys.exit(1)"'},
            cwd=workdir,
        )

        content = status_file.read_text()
        assert "FAIL" in content, (
            f".gate-status should contain 'FAIL' after a failing run. Got:\n{content}"
        )

    def test_failing_run_touches_gate_failed(self) -> None:
        """A failing stub gate must create .gate-failed in the working dir."""
        workdir = Path(tempfile.mkdtemp(prefix="gludd-gate-test-"))
        (workdir / ".gate-status").write_text("")

        _run_gate(
            env_overrides={"PYTEST_CMD": 'python3 -c "import sys; sys.exit(2)"'},
            cwd=workdir,
        )

        assert (workdir / ".gate-failed").exists(), (
            ".gate-failed must be created when pytest exits non-zero"
        )

    def test_unique_basetemp_not_fixed_path(self) -> None:
        """run_gate.sh must use mktemp-based basetemp, not the old fixed path."""
        script_text = SCRIPT.read_text()
        # The new script must reference mktemp or gludd-gate-XXXXXX pattern.
        assert "mktemp" in script_text, (
            "run_gate.sh must call mktemp to create a unique per-run basetemp"
        )
        # The old fixed path must NOT appear (that was the bug).
        assert "/tmp/gludd-gate-basetemp" not in script_text, (
            "run_gate.sh must NOT use the fixed /tmp/gludd-gate-basetemp path "
            "(that caused collisions between concurrent gates)"
        )

    def test_lock_file_referenced(self) -> None:
        """run_gate.sh must reference a lock file for mutual exclusion."""
        script_text = SCRIPT.read_text()
        assert "gludd-gate.lock" in script_text, (
            "run_gate.sh must use a lock file (/tmp/gludd-gate.lock) for mutual exclusion"
        )

    def test_already_running_message_in_script(self) -> None:
        """The 'already running' rejection message must be present in the script."""
        script_text = SCRIPT.read_text()
        assert "already running" in script_text, (
            "run_gate.sh must print an 'already running' message when the lock is held"
        )

    def test_second_invocation_rejected_when_lock_held(self) -> None:
        """While lock is held by a live process, a second invocation must:
        - exit non-zero
        - print 'already running' to stderr
        - NOT create a gludd-gate-XXXXXX basetemp that persists after rejection

        Lock simulation strategy:
        - If GNU flock is available: spawn a background bash process that opens
          the lock file and holds an exclusive flock on it (kernel-level).
        - Otherwise (stock macOS PID-file path): write our live PID into the
          lock file, which the script checks with kill -0.
        """
        # Use a unique lock file for this test so it doesn't race with other tests.
        unique_lock = tempfile.mktemp(prefix="gludd-gate-conctest-lock-", dir="/tmp")
        lock_path = Path(unique_lock)
        holder_proc: subprocess.Popen | None = None

        try:
            # Mirror the script's GNU-flock probe: flock --nonblock /dev/null true
            has_flock = subprocess.run(
                ["bash", "-c", "command -v flock >/dev/null 2>&1 && flock --nonblock /dev/null true"],
                capture_output=True,
            ).returncode == 0

            if has_flock:
                # Hold an exclusive kernel flock from a background bash process.
                # Open in append mode (>>) so we don't truncate the file, then
                # acquire exclusive flock and signal readiness. The process sleeps
                # while holding the lock and releases it when killed.
                holder_proc = subprocess.Popen(
                    ["bash", "-c",
                     f"exec 9>>\"{lock_path}\"; flock --exclusive 9; echo ready; sleep 30"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                # Wait until the holder signals it has the lock.
                assert holder_proc.stdout is not None
                line = holder_proc.stdout.readline()
                assert "ready" in line, f"Lock holder did not signal ready: {line!r}"
            else:
                # PID-file path: write our (live) PID into the lock file.
                lock_path.write_text(str(os.getpid()))

            # Snapshot basetemp candidates before the rejected run.
            before_temps = set(glob.glob("/tmp/gludd-gate-[A-Za-z0-9]*"))

            workdir = Path(tempfile.mkdtemp(prefix="gludd-gate-test-"))
            (workdir / ".gate-status").write_text("")

            env = {
                **os.environ,
                "PYTEST_CMD": 'python3 -c "import sys; sys.exit(0)"',
                # Point the script at our unique lock file (same one the holder holds).
                "GATE_LOCK_FILE": unique_lock,
            }

            result = subprocess.run(
                ["bash", str(SCRIPT)],
                capture_output=True,
                text=True,
                cwd=str(workdir),
                env=env,
                timeout=10,
            )

            after_temps = set(glob.glob("/tmp/gludd-gate-[A-Za-z0-9]*"))
            new_temps = after_temps - before_temps

            # Must be rejected.
            assert result.returncode != 0, (
                "Second invocation while lock is held must exit non-zero. "
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )

            # Must print the 'already running' message.
            combined = result.stdout + result.stderr
            assert "already running" in combined, (
                f"Rejection must include 'already running' message. Got:\n{combined}"
            )

            # Must NOT leave a new SCRIPT basetemp on disk (cleaned up by trap or
            # never created). Match only the script's basetemp shape so this test's
            # own workdir / lock files (which share the gludd-gate- prefix) are not
            # mistaken for a leak.
            leaked = {
                p for p in new_temps
                if Path(p).is_dir() and _SCRIPT_BASETEMP_RE.match(Path(p).name)
            }
            assert not leaked, (
                f"Rejected invocation must NOT leave a basetemp dir on disk. Found: {leaked}"
            )

        finally:
            # Release the lock holder.
            if holder_proc is not None:
                holder_proc.terminate()
                holder_proc.wait(timeout=5)
            # Clean up our unique lock file.
            lock_path.unlink(missing_ok=True)

    def test_cleanup_trap_mentioned_in_script(self) -> None:
        """run_gate.sh must set a trap on EXIT/INT/TERM for cleanup."""
        script_text = SCRIPT.read_text()
        assert "trap" in script_text, (
            "run_gate.sh must set a cleanup trap for EXIT/INT/TERM"
        )
        # Cleanup must remove the basetemp.
        assert "BASETEMP" in script_text, (
            "The trap must reference BASETEMP so it is removed on any exit"
        )

    def test_pytest_cmd_env_override_used(self) -> None:
        """PYTEST_CMD env var must be honoured (testability hook)."""
        # Use a stub that prints a marker so we can confirm it ran.
        workdir = Path(tempfile.mkdtemp(prefix="gludd-gate-test-"))
        (workdir / ".gate-status").write_text("")

        marker = "STUB_PYTEST_MARKER_12345"
        env = {
            **os.environ,
            "PYTEST_CMD": f'python3 -c "print(\\"{marker}\\")"',
            # Unique lock so concurrent xdist workers don't race on the default lock.
            "GATE_LOCK_FILE": tempfile.mktemp(prefix="gludd-gate-marker-lock-", dir="/tmp"),
        }
        result = subprocess.run(
            ["bash", str(SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(workdir),
            env=env,
            timeout=10,
        )
        combined = result.stdout + result.stderr
        assert marker in combined, (
            f"PYTEST_CMD stub output '{marker}' must appear in run_gate.sh output. Got:\n{combined}"
        )
        logs = list((workdir / ".gate-logs").glob("gate-pytest-*.log"))
        assert len(logs) == 1
        assert marker in logs[0].read_text(encoding="utf-8")

    def test_live_log_is_outside_pytest_basetemp(self) -> None:
        """pytest clears --basetemp, so the streamed log must not live inside it."""
        script_text = SCRIPT.read_text(encoding="utf-8")

        assert 'LOG_FILE="${BASETEMP}/gate.log"' not in script_text
        assert ".gate-logs/gate-pytest-" in script_text

    # -----------------------------------------------------------------------
    # Subagent guard tests (mt-5)
    # -----------------------------------------------------------------------

    def test_subagent_guard_refused_via_claude_agent_id(self) -> None:
        """When CLAUDE_AGENT_ID is set and GLUDD_GATE_AUTHORIZED is unset,
        run_gate.sh must exit non-zero with the refusal message and must NOT
        create any gludd-gate-XXXXXX basetemp directory."""
        workdir = Path(tempfile.mkdtemp(prefix="gludd-gate-test-"))
        (workdir / ".gate-status").write_text("")

        # Unique basetemp prefix so concurrent test runs don't pollute each
        # other's leak check.
        prefix = f"/tmp/gludd-gate-test-{os.getpid()}-{uuid4().hex[:6]}"
        before_temps = set(glob.glob(f"{prefix}-[A-Za-z0-9]*"))

        env = {
            **os.environ,
            "PYTEST_CMD": 'python3 -c "import sys; sys.exit(0)"',
            "GATE_LOCK_FILE": tempfile.mktemp(prefix="gludd-gate-subagent-lock-", dir="/tmp"),
            "CLAUDE_AGENT_ID": "test-agent-abc123",
            "GATE_BASETEMP_PREFIX": prefix,
        }
        # Ensure the override is NOT set.
        env.pop("GLUDD_GATE_AUTHORIZED", None)

        result = subprocess.run(
            ["bash", str(SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(workdir),
            env=env,
            timeout=10,
        )

        after_temps = set(glob.glob(f"{prefix}-[A-Za-z0-9]*"))
        new_dirs = {p for p in (after_temps - before_temps) if Path(p).is_dir()}

        assert result.returncode != 0, (
            f"Expected non-zero exit when CLAUDE_AGENT_ID is set without GLUDD_GATE_AUTHORIZED. "
            f"Got {result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "subagent" in combined.lower(), (
            f"Refusal message must mention 'subagent'. Got:\n{combined}"
        )
        assert not new_dirs, (
            f"Refused invocation must NOT leave a basetemp dir on disk. Found: {new_dirs}"
        )

    def test_subagent_guard_refused_via_gludd_subagent(self) -> None:
        """When GLUDD_SUBAGENT is set and GLUDD_GATE_AUTHORIZED is unset,
        run_gate.sh must exit non-zero with the refusal message."""
        workdir = Path(tempfile.mkdtemp(prefix="gludd-gate-test-"))
        (workdir / ".gate-status").write_text("")

        env = {
            **os.environ,
            "PYTEST_CMD": 'python3 -c "import sys; sys.exit(0)"',
            "GATE_LOCK_FILE": tempfile.mktemp(prefix="gludd-gate-subagent-lock-", dir="/tmp"),
            "GLUDD_SUBAGENT": "1",
        }
        env.pop("GLUDD_GATE_AUTHORIZED", None)
        env.pop("CLAUDE_AGENT_ID", None)

        result = subprocess.run(
            ["bash", str(SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(workdir),
            env=env,
            timeout=10,
        )

        assert result.returncode != 0, (
            f"Expected non-zero exit when GLUDD_SUBAGENT is set without GLUDD_GATE_AUTHORIZED. "
            f"Got {result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "subagent" in combined.lower(), (
            f"Refusal message must mention 'subagent'. Got:\n{combined}"
        )

    def test_subagent_guard_bypassed_with_authorized_flag(self) -> None:
        """When CLAUDE_AGENT_ID is set but GLUDD_GATE_AUTHORIZED=1, the gate
        must proceed normally (exit 0 with a passing PYTEST_CMD stub)."""
        workdir = Path(tempfile.mkdtemp(prefix="gludd-gate-test-"))
        (workdir / ".gate-status").write_text("")

        result = _run_gate(
            env_overrides={
                "CLAUDE_AGENT_ID": "test-agent-authorized",
                "GLUDD_GATE_AUTHORIZED": "1",
            },
            cwd=workdir,
        )

        assert result.returncode == 0, (
            f"Expected exit 0 when GLUDD_GATE_AUTHORIZED=1 even with CLAUDE_AGENT_ID set. "
            f"Got {result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        content = (workdir / ".gate-status").read_text()
        assert "PASS 0" in content, (
            f".gate-status must contain 'PASS 0' after authorized subagent run. Got:\n{content}"
        )

    def test_main_session_no_marker_runs_normally(self) -> None:
        """Without any subagent markers, a clean environment must run normally
        (exit 0, PASS 0 in .gate-status)."""
        workdir = Path(tempfile.mkdtemp(prefix="gludd-gate-test-"))
        (workdir / ".gate-status").write_text("")

        env = {
            **os.environ,
            "PYTEST_CMD": 'python3 -c "import sys; sys.exit(0)"',
            "GATE_LOCK_FILE": tempfile.mktemp(prefix="gludd-gate-main-lock-", dir="/tmp"),
        }
        env.pop("CLAUDE_AGENT_ID", None)
        env.pop("GLUDD_SUBAGENT", None)
        env.pop("GLUDD_GATE_AUTHORIZED", None)

        result = subprocess.run(
            ["bash", str(SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(workdir),
            env=env,
            timeout=10,
        )

        assert result.returncode == 0, (
            f"Main session (no subagent markers) must run normally. "
            f"Got {result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        content = (workdir / ".gate-status").read_text()
        assert "PASS 0" in content, (
            f".gate-status must contain 'PASS 0'. Got:\n{content}"
        )


# ---------------------------------------------------------------------------
# Worker-count formula tests  (scripts/gate_worker_count.py)
# ---------------------------------------------------------------------------

class TestGateWorkerCount:
    """Unit tests for the memory-bounded xdist worker-count formula in
    scripts/gate_worker_count.py.

    Formula:
        cpu_based  = max(1, cpu_count // 4)
        mem_based  = max(1, floor(available_ram_gb / per_worker_gb))
        workers    = min(cpu_based, mem_based)
    """

    def _import_module(self):
        """Import gate_worker_count without installing it as a package."""
        import importlib.util
        import pathlib
        spec = importlib.util.spec_from_file_location(
            "gate_worker_count",
            pathlib.Path(__file__).parent.parent.parent / "scripts" / "gate_worker_count.py",
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod

    def test_script_exists(self) -> None:
        script = Path(__file__).parent.parent.parent / "scripts" / "gate_worker_count.py"
        assert script.exists(), "scripts/gate_worker_count.py must exist"

    def test_cpu_bound_dominates_when_ram_is_ample(self) -> None:
        """When RAM is very plentiful the CPU term is the binding constraint."""
        mod = self._import_module()
        # 8 CPUs → cpu_based=2; 100 GB RAM / 1.5 per worker → mem_based=66
        result = mod.compute_worker_count(cpu_count=8, available_ram_gb=100.0, per_worker_gb=1.5)
        assert result == 2, f"Expected 2 (cpu-bound), got {result}"

    def test_mem_bound_dominates_when_ram_is_scarce(self) -> None:
        """When RAM is scarce the memory term is the binding constraint."""
        mod = self._import_module()
        # 64 CPUs → cpu_based=16; 3 GB / 1.5 per worker → mem_based=2
        result = mod.compute_worker_count(cpu_count=64, available_ram_gb=3.0, per_worker_gb=1.5)
        assert result == 2, f"Expected 2 (mem-bound), got {result}"

    def test_minimum_is_one_regardless_of_ram(self) -> None:
        """Even with almost no RAM we must return at least 1 worker."""
        mod = self._import_module()
        # 4 CPUs → cpu_based=1; 0.1 GB / 1.5 per worker → mem_based=0 → clamped to 1
        result = mod.compute_worker_count(cpu_count=4, available_ram_gb=0.1, per_worker_gb=1.5)
        assert result == 1, f"Expected 1 (minimum floor), got {result}"

    def test_minimum_is_one_with_single_cpu(self) -> None:
        """1 CPU → cpu_based = max(1, 0) = 1; result must be 1."""
        mod = self._import_module()
        result = mod.compute_worker_count(cpu_count=1, available_ram_gb=16.0, per_worker_gb=1.5)
        assert result == 1, f"Expected 1 for single CPU, got {result}"

    def test_per_worker_gb_override(self) -> None:
        """A larger per-worker budget further constrains the mem-based count."""
        mod = self._import_module()
        # 16 CPUs → cpu_based=4; 6 GB / 3.0 per worker → mem_based=2
        result = mod.compute_worker_count(cpu_count=16, available_ram_gb=6.0, per_worker_gb=3.0)
        assert result == 2, f"Expected 2 (mem-bound with 3 GB/worker budget), got {result}"

    def test_invalid_per_worker_gb_raises(self) -> None:
        """per_worker_gb <= 0 must raise ValueError."""
        import pytest as _pytest
        mod = self._import_module()
        with _pytest.raises(ValueError):
            mod.compute_worker_count(cpu_count=4, available_ram_gb=8.0, per_worker_gb=0)

    def test_gludd_xdist_env_override_bypasses_formula(self, monkeypatch) -> None:
        """When GLUDD_XDIST is set, main() must print it verbatim and skip formula."""
        import contextlib
        import io
        mod = self._import_module()
        monkeypatch.setenv("GLUDD_XDIST", "7")
        monkeypatch.delenv("GLUDD_PER_WORKER_GB", raising=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mod.main()
        assert buf.getvalue().strip() == "7", f"Expected '7', got {buf.getvalue().strip()!r}"

    def test_main_returns_positive_int_by_default(self, monkeypatch) -> None:
        """main() without any env overrides must print a positive integer."""
        import contextlib
        import io
        mod = self._import_module()
        monkeypatch.delenv("GLUDD_XDIST", raising=False)
        monkeypatch.delenv("GLUDD_PER_WORKER_GB", raising=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mod.main()
        val = int(buf.getvalue().strip())
        assert val >= 1, f"Expected >= 1, got {val}"

    def test_formula_symmetric_boundary(self) -> None:
        """Exact boundary: cpu_based == mem_based → result equals both."""
        mod = self._import_module()
        # 8 CPUs → cpu_based=2; 3.0 GB / 1.5 per worker → mem_based=2
        result = mod.compute_worker_count(cpu_count=8, available_ram_gb=3.0, per_worker_gb=1.5)
        assert result == 2, f"Expected 2 at boundary, got {result}"
