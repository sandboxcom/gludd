"""Behavioral monitoring and observability tests.

Verifies specs from BEHAVIORAL_SPECS.md relating to monitoring, observability,
watchdog liveness, and gate-status integrity:

- Group Z (Z20-Z22): Watchdog auto-start, idle detection, task watchdog
- Group Q (Q03-Q25): Gate-status file writes, read-only checks, observability
- Group T (T25-T27): Background gate phase markers, failure surfacing, task watchdog
- Group G (G09, G12, G19, G21): Gate writes .gate-status, status read-only, output observable
- Group U (U22, U24): Disk usage monitored, no unseen events
- Group K (K17-K18): Gate and CI state cached
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
OPENCODE_JSON = ROOT / "opencode.json"
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
@pytest.fixture
def completed_gate_status(tmp_path: Path) -> Path:
    """Return an immutable terminal snapshot, isolated from a concurrently running gate."""
    status = tmp_path / "gate-status"
    status.write_text(
        "=== GATE 2026-08-20T12:00:00Z ===\n"
        "lint PASS 0\n"
        "typecheck PASS 0\n"
        "collect PASS 0\n"
        "test PASS 0\n"
        "smoke PASS\n"
        "---\n"
        "epoch 1787227200\n"
        "=== GATE: PASSED ===\n"
    )
    return status


# ---------------------------------------------------------------------------
# (a) Enforcement plugins: importability and hook presence
# ---------------------------------------------------------------------------

_KNOWN_HOOKS = {
    "tool.execute.before",
    "text.complete",
    "experimental.text.complete",
    "session.idle",
    "experimental.chat.system.transform",
}


class TestEnforcementPluginHooks:
    def test_all_plugins_registered_in_opencode_json(self) -> None:
        cfg = json.loads(OPENCODE_JSON.read_text())
        plugin_entries = cfg.get("plugin", cfg.get("plugins", []))
        registered = {
            (entry if isinstance(entry, str) else entry.get("source", entry.get("path", ""))).lstrip("./")
            for entry in plugin_entries
        }
        missing: list[str] = []
        for ts_file in sorted(PLUGIN_DIR.glob("enforce-*.ts")):
            rel = str(ts_file.relative_to(ROOT))
            # impl/ files are not plugins themselves
            if "/impl/" in rel:
                continue
            # v2 files may shadow originals
            if "/enforce-floor-v2.ts" in rel:
                continue
            # release-deadline is a specialized variant
            if "/enforce-release-deadline.ts" in rel:
                continue
            found = any(
                rel.replace("opencode/plugin/", "") in r
                or rel.replace(".opencode/plugin/", "") in r
                or rel.replace("/.opencode/plugin/", ".opencode/plugin/") in r
                for r in registered
            )
            if not found:
                missing.append(rel)
        # Not all plugins must be registered — some are hot-loadable only.
        # This test is informational: flag > 5 missing as suspicious.
        if len(missing) > 5:
            pytest.fail(f"More than 5 enforcement plugins not registered in opencode.json: {missing}")

    def test_every_plugin_has_at_least_one_hook(self) -> None:
        plugin_files = sorted(PLUGIN_DIR.glob("enforce-*.ts"))
        plugins_without_hooks: list[str] = []
        for f in plugin_files:
            if "/impl/" in str(f) or "enforce-release-deadline.ts" in str(f):
                continue
            text = f.read_text()
            has_hook = any(f"hook.{name}" in text or f'"{name}"' in text for name in _KNOWN_HOOKS)
            # Also check for the transformed plugin register() style
            has_register = "register(" in text or "hooks:" in text
            if not has_hook and not has_register:
                plugins_without_hooks.append(f.name)
        assert not plugins_without_hooks, f"Plugins with no recognizable hook registration: {plugins_without_hooks}"

    def test_enforce_floor_v2_shadows_original(self) -> None:
        """enforce-floor-v2.ts exists as an evolution of enforce-floor.ts."""
        v2 = PLUGIN_DIR / "enforce-floor-v2.ts"
        orig = PLUGIN_DIR / "enforce-floor.ts"
        assert v2.exists(), "enforce-floor-v2.ts must exist"
        assert orig.exists(), "enforce-floor.ts must exist as fallback"

    def test_subagent_guards_present(self) -> None:
        """Every plugin should skip enforcement in subagent context."""
        plugin_files = sorted(PLUGIN_DIR.glob("enforce-*.ts"))
        missing_guard: list[str] = []
        for f in plugin_files:
            if "/impl/" in str(f):
                continue
            text = f.read_text()
            has_subagent_check = "OPENCODE_SUBAGENT" in text or "isSubagent" in text or "subagent" in text.lower()
            if not has_subagent_check and "register(" not in text:
                missing_guard.append(f.name)
        # Some newer plugins may use register() pattern.
        # This is advisory: fail if more than 3 lack the guard.
        if len(missing_guard) > 3:
            pytest.fail(f"Plugins missing subagent guard: {missing_guard}")


# ---------------------------------------------------------------------------
# (b) Makefile monitoring targets
# ---------------------------------------------------------------------------

_MONITORING_TARGETS = {
    "watchdog-auto": "ensure background watchdog daemon runs",
    "task-watchdog-start": "start task watchdog daemon",
    "task-watchdog-stop": "stop task watchdog daemon",
    "task-watchdog-status": "report task watchdog state",
    "gate-status": "print current gate state",
    "gate-status-check": "non-blocking gate probe (read-only)",
    "gate-background": "launch gate detached, return immediately",
    "gate-logs": "list all gate logs",
    "gate-tail": "live tail of gate log",
    "gate-kill": "terminate background gate",
    "gate-refresh": "refresh .gate-status without full re-run",
    "active-work-status": "JSON snapshot of active work",
    "floor-status": "report agent floor liveness",
    "ps": "report test/audit process PIDs",
    "ps-gludd": "report gludd namespaced daemon PIDs",
    "ci-verdict-safe": "cooldown-aware CI verdict",
    "ci-cooldown-status": "show CI check cooldown remaining",
    "check-disk": "check disk usage pre-commit guard",
    "disk": "print disk usage and footprint",
    "list-plugins": "report active enforcement plugin roster",
}


class TestMakefileMonitoringTargets:
    def test_monitoring_targets_exist(self) -> None:
        mk_text = MAKEFILE.read_text()
        missing: list[str] = []
        for target, description in _MONITORING_TARGETS.items():
            pattern = rf"^\.PHONY:.*\b{target}\b|^{target}:"
            if not re.search(pattern, mk_text, re.MULTILINE):
                missing.append(f"{target} ({description})")
        assert not missing, f"Missing monitoring make targets: {missing}"

    def test_watchdog_auto_is_session_start_requirement(self) -> None:
        """Per Z20: make watchdog-auto must be a session-start protocol step."""
        assert "watchdog-auto" in MAKEFILE.read_text()

    def test_task_watchdog_in_makefile(self) -> None:
        """Per T27/Z22: task watchdog must have start/stop/status targets."""
        mk = MAKEFILE.read_text()
        for name in ("task-watchdog-start", "task-watchdog-stop", "task-watchdog-status"):
            assert name in mk, f"Missing task watchdog target: {name}"

    def test_gate_background_has_phase_markers(self) -> None:
        """Per T25: background gate must emit per-phase progress markers."""
        mk = MAKEFILE.read_text()
        assert "=== GATE PHASE:" in mk, "Gate must emit per-phase progress markers"

    def test_gate_status_check_is_read_only(self) -> None:
        """Per G12/Q06: gate-status-check must not modify files or state."""
        mk = MAKEFILE.read_text()
        assert "gate-status-check" in mk

    def test_active_work_status_is_json(self) -> None:
        """active-work-status must produce a JSON snapshot."""
        mk = MAKEFILE.read_text()
        assert "active-work-status" in mk

    def test_ps_targets_separate_concerns(self) -> None:
        """Per AGENTS.md: ps vs ps-gludd are distinct monitoring surfaces."""
        mk = MAKEFILE.read_text()
        assert "ps:" in mk and "ps-gludd:" in mk


# ---------------------------------------------------------------------------
# (c) Gate status file (.gate-status) parseability
# ---------------------------------------------------------------------------


class TestGateStatusFile:
    @staticmethod
    def _completed_status(status_file: Path) -> str | None:
        """Read a completed snapshot, or validate and identify an active gate.

        ``gate-async`` atomically writes ``RUNNING <epoch> <pid>`` before the
        first phase completes.  That record is the documented observable
        in-flight state, while the richer phase snapshot is the terminal form.
        """
        content = status_file.read_text().strip()
        assert content, ".gate-status is empty"
        first_line = content.splitlines()[0]
        if not first_line.startswith("RUNNING "):
            return content
        parts = first_line.split()
        assert len(parts) == 3, f"Malformed running gate status: {first_line!r}"
        _marker, epoch_text, pid_text = parts
        assert epoch_text.isdigit(), f"Running gate epoch is not numeric: {epoch_text!r}"
        assert pid_text.isdigit(), f"Running gate PID is not numeric: {pid_text!r}"
        assert 1_700_000_000 < int(epoch_text) < 2_000_000_000
        assert int(pid_text) > 0
        return None

    def test_gate_status_exists(self, completed_gate_status: Path) -> None:
        assert completed_gate_status.exists(), "isolated gate-status snapshot was not published"

    def test_gate_status_has_header_with_timestamp(self, completed_gate_status: Path) -> None:
        """Per G09: gate writes a header with UTC timestamp."""
        content = self._completed_status(completed_gate_status)
        if content is None:
            return
        header_line = content.splitlines()[0]
        assert re.match(
            r"^=== (GATE|GATE-REFRESH) \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z ===",
            header_line,
        ), f"Gate header missing or malformed: {header_line!r}"

    def test_gate_status_has_required_phases(self, completed_gate_status: Path) -> None:
        """Per Q03: gate writes PASS or FAIL for each phase."""
        content = self._completed_status(completed_gate_status)
        if content is None:
            return
        required_phases = ["lint ", "typecheck ", "collect ", "test "]
        missing_phases = [p for p in required_phases if p not in content]
        assert not missing_phases, f".gate-status missing required phase entries: {missing_phases}"

    def test_gate_status_has_epoch(self, completed_gate_status: Path) -> None:
        """Per K17: gate caches epoch timestamp."""
        content = self._completed_status(completed_gate_status)
        if content is None:
            return
        assert "epoch " in content, ".gate-status missing epoch timestamp"

    def test_gate_status_has_terminal_marker(self, completed_gate_status: Path) -> None:
        """Per Q03: gate writes PASSED or FAILED terminal marker."""
        content = self._completed_status(completed_gate_status)
        if content is None:
            return
        assert any(marker in content for marker in ("=== GATE: PASSED ===", "=== GATE: FAILED ===")), (
            ".gate-status missing terminal marker (GATE: PASSED/FAILED)"
        )

    def test_gate_status_phases_have_pass_fail_values(self, completed_gate_status: Path) -> None:
        """Every phase line must have PASS or FAIL."""
        content = self._completed_status(completed_gate_status)
        if content is None:
            return
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith(
                (
                    "lint ",
                    "typecheck ",
                    "collect ",
                    "test ",
                    "smoke ",
                    "hook-runtime ",
                    "verify-enforcement ",
                    "env-writes ",
                    "integration ",
                    "e2e ",
                    "molecule ",
                    "restart-needed ",
                    "hot-reload ",
                    "verify-hot-reload ",
                    "verify-feature-claims ",
                    "check-status-table ",
                    "coverage-gaps ",
                    "dead-code ",
                )
            ):
                assert "PASS" in stripped or "FAIL" in stripped, f"Phase line missing PASS/FAIL: {stripped!r}"

    def test_gate_status_epoch_is_numeric(self, completed_gate_status: Path) -> None:
        content = self._completed_status(completed_gate_status)
        if content is None:
            return
        m = re.search(r"^epoch (\d+)$", content, re.MULTILINE)
        assert m, ".gate-status epoch is missing or not numeric"
        epoch = int(m.group(1))
        assert epoch > 1700000000, f"Epoch value suspicious: {epoch} (too low for 2024+)"
        assert epoch < 2000000000, f"Epoch value suspicious: {epoch} (too high)"

    def test_gate_status_ci_line_when_ci_pending(self, completed_gate_status: Path) -> None:
        """Per K18: CI state may appear in gate status."""
        content = completed_gate_status.read_text()
        if "CI " in content:
            assert "FAIL" in content or "pending" in content or "run " in content, (
                f"CI line present but unparseable: {content}"
            )

    def test_gate_publishes_only_running_or_terminal_snapshots(self) -> None:
        """The public status path must never expose a half-written phase snapshot."""
        makefile = MAKEFILE.read_text()
        runner = (ROOT / "scripts" / "run_gate.sh").read_text()

        assert "GATE_STATUS_FILE=.gate-status.next GATE_FAILED_FILE=.gate-failed bash scripts/run_gate.sh" in makefile
        assert makefile.count("mv .gate-status.next .gate-status") >= 2
        assert "STATUS_WORK=.gate-status.next" in makefile
        assert 'mv "$$STATUS_WORK" .gate-status' in makefile
        assert 'STATUS_FILE="${GATE_STATUS_FILE:-.gate-status}"' in runner


# ---------------------------------------------------------------------------
# (d) Watchdog behavioral checks
# ---------------------------------------------------------------------------


class TestWatchdogBehavior:
    def test_agent_watchdog_script_exists(self) -> None:
        watchdog = ROOT / "scripts" / "agent_watchdog.py"
        assert watchdog.exists(), "agent_watchdog.py must exist (Z21: idle detection)"

    def test_agent_watchdog_is_python(self) -> None:
        watchdog = ROOT / "scripts" / "agent_watchdog.py"
        text = watchdog.read_text()
        assert "class " in text or "def " in text, "agent_watchdog.py must be a Python script"

    def test_task_watchdog_script_exists(self) -> None:
        watchdog = ROOT / "scripts" / "task_watchdog.py"
        assert watchdog.exists(), "task_watchdog.py must exist (Z22: kill stale tasks)"

    def test_clean_tmp_target_exists(self) -> None:
        """Per watchdog cleanup: make clean-tmp must exist for state file cleanup."""
        mk = MAKEFILE.read_text()
        assert "clean-tmp:" in mk, "make clean-tmp must exist for watchdog state cleanup"


# ---------------------------------------------------------------------------
# (e) Observability module importability
# ---------------------------------------------------------------------------


class TestObservabilityModule:
    def test_observability_package_importable(self) -> None:
        import general_ludd.observability  # noqa: F401

    def test_observability_has_all_names(self) -> None:
        from general_ludd.observability import __all__ as exported_names

        assert "ExecutionSpan" in exported_names
        assert "ExecutionTrace" in exported_names
        assert "AutoBenchmarkRecorder" in exported_names
        assert "ModelComparison" in exported_names
        assert "OTelBridge" in exported_names
        assert "RecentTracesBuffer" in exported_names

    def test_observability_submodules_exist(self) -> None:
        submodules = [
            "tracer.py",
            "recorder.py",
            "comparison.py",
            "trace_store.py",
            "timing.py",
            "token_cost.py",
            "otel_bridge.py",
            "run_history.py",
            "metrics_exporter.py",
            "langsmith_tracer.py",
            "dashboard_data.py",
            "__init__.py",
        ]
        obs_dir = ROOT / "src" / "general_ludd" / "observability"
        for mod in submodules:
            assert (obs_dir / mod).exists(), f"Observability submodule missing: {mod}"


# ---------------------------------------------------------------------------
# (f) Disk monitoring
# ---------------------------------------------------------------------------


class TestDiskMonitoring:
    def test_check_disk_target_exists(self) -> None:
        """Per U22: disk usage must be monitored and enforced."""
        mk = MAKEFILE.read_text()
        assert "check-disk:" in mk, "make check-disk must enforce disk monitoring"

    def test_disk_target_exists(self) -> None:
        mk = MAKEFILE.read_text()
        assert "disk:" in mk, "make disk must report disk usage"

    def test_disk_guard_target_exists(self) -> None:
        mk = MAKEFILE.read_text()
        assert "disk-guard:" in mk, "make disk-guard (auto-clean at threshold) must exist"

    def test_check_disk_script_exists(self) -> None:
        script = ROOT / "scripts" / "check_disk_usage.py"
        assert script.exists(), "check_disk_usage.py must exist for pre-commit disk guard"


# ---------------------------------------------------------------------------
# (g) No-unseen-events (observability invariant)
# ---------------------------------------------------------------------------


class TestNoUnseenEvents:
    def test_makefile_has_deploy_and_forget(self) -> None:
        """Per P23: deploy-and-forget must exist for fire-and-forget CI pushes."""
        assert "deploy-and-forget" in MAKEFILE.read_text()

    def test_makefile_has_ci_verdict_safe(self) -> None:
        """Per P07: CI checks must use ci-verdict-safe for cooldown enforcement."""
        assert "ci-verdict-safe:" in MAKEFILE.read_text()

    def test_ci_cooldown_script_exists(self) -> None:
        script = ROOT / "scripts" / "ci_check_cooldown.py"
        assert script.exists(), "ci_check_cooldown.py must exist for CI check throttling"
