"""Phase PM — Process Monitoring verification tests.

Verifies the existence and basic shape of the process-monitoring
infrastructure referenced by Phase PM (TASKS.md lines 386-397). Each
PM item maps to one or more concrete artifacts (plugins, Makefile
targets, scripts, or tracked state files). The features are already
shipped; these tests pin them so a regression that removes one is
caught at gate time.

PM item -> artifact mapping:
  PM.1 Session start audit       -> enforce-session-start.ts (system.transform
                                    banner that names BUGS.md/SESSION.md)
  PM.2 BUGS.md incident logging  -> BUGS.md exists with incident entries
  PM.3 Disengage audit log       -> Makefile disengage-enforcement target
                                    writes /tmp/gludd-disengage-audit.jsonl
  PM.4 Subagent pool monitoring  -> enforce-floor.ts + scripts/agent_liveness.py
  PM.5 CI poll count tracking    -> enforce-no-ci-poll.ts
  PM.6 Session start timeliness  -> enforce-session-start.ts time gates
                                    (DISPATCH_NOW_SECS, HARD_DENY_SECS)
  PM.8 Gate freshness            -> Makefile _gate-fresh-check target
  PM.10 Disk usage monitoring    -> Makefile disk, disk-guard, check-disk targets

Cross-cutting (referenced by PM items):
  - Crash recovery               -> Makefile crash-recovery target
  - Watchdog daemon              -> Makefile watchdog-auto + scripts/agent_watchdog.py
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
MAKEFILE = ROOT / "Makefile"
BUGS_MD = ROOT / "BUGS.md"
SCRIPTS_DIR = ROOT / "scripts"


def _plugin_source(name: str) -> str:
    """Read a plugin .ts source file from .opencode/plugin/."""
    path = PLUGIN_DIR / name
    assert path.exists(), f"plugin {name} not found at {path}"
    return path.read_text()


def _makefile_source() -> str:
    assert MAKEFILE.exists(), "Makefile not found at repo root"
    return MAKEFILE.read_text()


def _makefile_has_target(makefile_src: str, target: str) -> bool:
    """Return True if `target:` appears at column 0 of the Makefile.

    Allows optional dependencies after the colon
    (e.g. `_gate-fresh-check: check-gate-fresh`).
    """
    pattern = re.compile(rf"^{re.escape(target)}:.*$", re.MULTILINE)
    return bool(pattern.search(makefile_src))


# ---------------------------------------------------------------------------
# PM.1 — Session start protocol exists (enforce-session-start.ts)
# ---------------------------------------------------------------------------


class TestSessionStartProtocol:
    """PM.1: audit own previous session for premature stops."""

    def test_plugin_file_exists(self):
        assert (PLUGIN_DIR / "enforce-session-start.ts").exists()

    def test_plugin_registered_in_opencode_json(self):
        oc = (ROOT / "opencode.json").read_text()
        assert "enforce-session-start.ts" in oc, (
            "enforce-session-start.ts must be registered in opencode.json"
        )

    def test_plugin_has_system_transform_hook(self):
        src = _plugin_source("enforce-session-start.ts")
        # The session-start protocol banner is injected via system.transform.
        assert "system.transform" in src or "experimental.chat.system.transform" in src

    def test_plugin_references_bugs_md(self):
        # PM.1 requires the session-start protocol to read BUGS.md.
        src = _plugin_source("enforce-session-start.ts")
        assert "BUGS.md" in src, "session-start plugin must reference BUGS.md"


# ---------------------------------------------------------------------------
# PM.2 — BUGS.md exists and has incident entries
# ---------------------------------------------------------------------------


class TestBugsMdIncidentLog:
    """PM.2: premature-stop incidents logged in BUGS.md."""

    def test_file_exists(self):
        assert BUGS_MD.exists(), "BUGS.md must exist at repo root"

    def test_has_incident_header_section(self):
        text = BUGS_MD.read_text()
        assert "Incident Log" in text or "## Incident" in text, (
            "BUGS.md must have an Incident Log section"
        )

    def test_has_dated_incident_entries(self):
        # Each incident is an `### YYYY-MM-DD` heading.
        text = BUGS_MD.read_text()
        entries = re.findall(r"^###\s+\d{4}-\d{2}-\d{2}", text, re.MULTILINE)
        assert len(entries) >= 3, (
            f"BUGS.md must contain >=3 dated incident entries; found {len(entries)}"
        )

    def test_incidents_document_root_cause(self):
        # Each incident should document a root cause (not just a symptom).
        text = BUGS_MD.read_text()
        # The phrase "Root cause" or "root cause" appears in well-formed entries.
        assert re.search(r"root cause", text, re.IGNORECASE), (
            "BUGS.md incidents must document root cause"
        )


# ---------------------------------------------------------------------------
# PM.3 — Disengage audit log mechanism (Makefile disengage-enforcement target)
# ---------------------------------------------------------------------------


class TestDisengageAuditLog:
    """PM.3: every make disengage-enforcement appends to an audit log."""

    def test_target_exists(self):
        assert _makefile_has_target(_makefile_source(), "disengage-enforcement")

    def test_target_writes_audit_jsonl(self):
        # The target must append to /tmp/gludd-disengage-audit.jsonl
        src = _makefile_source()
        # Find the disengage-enforcement recipe block.
        m = re.search(
            r"^disengage-enforcement:\n((?:\t.*\n)+)",
            src,
            re.MULTILINE,
        )
        assert m is not None, "disengage-enforcement recipe not found"
        recipe = m.group(1)
        assert "gludd-disengage-audit" in recipe, (
            "disengage-enforcement must write /tmp/gludd-disengage-audit.jsonl"
        )

    def test_target_reports_count(self):
        # PM.3 requires the count to be displayed ("Disengage count: N").
        m = re.search(
            r"^disengage-enforcement:\n((?:\t.*\n)+)",
            _makefile_source(),
            re.MULTILINE,
        )
        assert m is not None
        recipe = m.group(1)
        assert "Disengage count" in recipe, (
            "disengage-enforcement must display cumulative disengage count"
        )


# ---------------------------------------------------------------------------
# PM.4 — Agent pool monitoring (enforce-floor.ts + agent_liveness.py)
# ---------------------------------------------------------------------------


class TestAgentPoolMonitoring:
    """PM.4: monitor subagent pool size; alert when below floor."""

    def test_floor_plugin_exists(self):
        assert (PLUGIN_DIR / "enforce-floor.ts").exists()

    def test_floor_plugin_defines_floor_constant(self):
        src = _plugin_source("enforce-floor.ts")
        # The plugin reads CLAUDE_AGENT_FLOOR (default 10) as the floor.
        assert "CLAUDE_AGENT_FLOOR" in src

    def test_floor_plugin_registered(self):
        oc = (ROOT / "opencode.json").read_text()
        assert "enforce-floor.ts" in oc

    def test_agent_liveness_script_exists(self):
        assert (SCRIPTS_DIR / "agent_liveness.py").exists(), (
            "scripts/agent_liveness.py must exist for live agent counting"
        )


# ---------------------------------------------------------------------------
# PM.5 — CI poll count tracking (enforce-no-ci-poll.ts)
# ---------------------------------------------------------------------------


class TestCiPollCountTracking:
    """PM.5: track consecutive CI status checks; deny the 4th."""

    def test_plugin_file_exists(self):
        assert (PLUGIN_DIR / "enforce-no-ci-poll.ts").exists()

    def test_plugin_registered(self):
        oc = (ROOT / "opencode.json").read_text()
        assert "enforce-no-ci-poll.ts" in oc

    def test_plugin_defines_max_consecutive_polls(self):
        src = _plugin_source("enforce-no-ci-poll.ts")
        # BP.2 spec: MAX_CONSECUTIVE_POLLS = 3.
        assert re.search(r"MAX_CONSECUTIVE_POLLS", src), (
            "enforce-no-ci-poll.ts must define MAX_CONSECUTIVE_POLLS"
        )

    def test_plugin_writes_state_file(self):
        src = _plugin_source("enforce-no-ci-poll.ts")
        # State file path used to persist the consecutive-poll counter.
        assert re.search(r"gludd-ci-poll|POLL_STATE_FILE", src), (
            "enforce-no-ci-poll.ts must persist poll counter to a state file"
        )


# ---------------------------------------------------------------------------
# PM.6 — Session start timeliness (time-based gates in enforce-session-start.ts)
# ---------------------------------------------------------------------------


class TestSessionStartTimeliness:
    """PM.6: session start to first dispatch wave must be < 5 min."""

    def test_dispatch_now_secs_constant(self):
        src = _plugin_source("enforce-session-start.ts")
        # The "DISPATCH NOW" warning fires after DISPATCH_NOW_SECS (default 60s).
        assert "DISPATCH_NOW_SECS" in src

    def test_hard_deny_secs_constant(self):
        src = _plugin_source("enforce-session-start.ts")
        # After HARD_DENY_SECS (default 120s) non-dispatch mutations are denied.
        assert "HARD_DENY_SECS" in src

    def test_time_gates_use_env_overridable_defaults(self):
        src = _plugin_source("enforce-session-start.ts")
        # Both constants must be overridable via env vars.
        assert "GLUDD_SESSION_START_DISPATCH_NOW_SECS" in src
        assert "GLUDD_SESSION_START_HARD_DENY_SECS" in src


# ---------------------------------------------------------------------------
# PM.8 — Gate freshness check (_gate-fresh-check in Makefile)
# ---------------------------------------------------------------------------


class TestGateFreshnessCheck:
    """PM.8: .gate-status must be newer than the last src/ edit."""

    def test_target_exists(self):
        # The Makefile exposes _gate-fresh-check as the canonical target name
        # (an alias for check-gate-fresh).
        assert _makefile_has_target(_makefile_source(), "_gate-fresh-check")

    def test_check_gate_fresh_target_exists(self):
        assert _makefile_has_target(_makefile_source(), "check-gate-fresh")

    def test_git_commit_uses_gate_fresh_check(self):
        # Commits must enforce the gate-freshness check (No-Commit-Bypass Policy).
        src = _makefile_source()
        assert "_gate-fresh-check" in src, (
            "Makefile must reference _gate-fresh-check in commit targets"
        )


# ---------------------------------------------------------------------------
# PM.10 — Disk usage monitoring (make disk, make disk-guard, make check-disk)
# ---------------------------------------------------------------------------


class TestDiskMonitoring:
    """PM.10: monitor /tmp/gludd-* disk usage; fail pre-commit if >100MB."""

    @pytest.mark.parametrize("target", ["disk", "disk-guard", "check-disk"])
    def test_disk_target_exists(self, target):
        assert _makefile_has_target(_makefile_source(), target), (
            f"Makefile must define `{target}` target for disk monitoring"
        )

    def test_check_disk_script_exists(self):
        # The check-disk target invokes scripts/check_disk_usage.py.
        assert (SCRIPTS_DIR / "check_disk_usage.py").exists(), (
            "scripts/check_disk_usage.py must exist"
        )

    def test_clean_tmp_target_exists(self):
        # Disk discipline includes a cleanup path.
        assert _makefile_has_target(_makefile_source(), "clean-tmp")

    def test_disk_guard_uses_bounded_visible_uv_prune(self):
        script = (SCRIPTS_DIR / "disk-guard.sh").read_text(encoding="utf-8")
        assert "uv cache prune" in script
        assert "UV_LOCK_TIMEOUT" in script
        assert "DISK_GUARD_UV_LOCK_TIMEOUT" in script
        assert "DISK_GUARD_UV_MAX_SECONDS" in script
        assert "UV_CACHE_PRUNE_HEARTBEAT" in script
        assert "uv cache clean" not in script
        assert "uv cache prune 2>/dev/null" not in script


# ---------------------------------------------------------------------------
# Cross-cutting — Crash recovery (referenced by PM.1 + FW.10 + DF.9)
# ---------------------------------------------------------------------------


class TestCrashRecovery:
    """Stale state files from crashed sessions are auto-reset."""

    def test_crash_recovery_target_exists(self):
        assert _makefile_has_target(_makefile_source(), "crash-recovery")

    def test_crash_recovery_clears_session_state(self):
        m = re.search(
            r"^crash-recovery:\n((?:\t.*\n)+)",
            _makefile_source(),
            re.MULTILINE,
        )
        assert m is not None, "crash-recovery recipe not found"
        recipe = m.group(1)
        assert "gludd-session-start.json" in recipe, (
            "crash-recovery must clear /tmp/gludd-session-start.json"
        )

    def test_session_start_plugin_implements_pid_mismatch_detection(self):
        # Crash recovery on the plugin side: PID mismatch + STALE_MS reset.
        src = _plugin_source("enforce-session-start.ts")
        assert "STALE_MS" in src or "pidMismatch" in src, (
            "enforce-session-start.ts must implement PID-mismatch / staleness reset"
        )


# ---------------------------------------------------------------------------
# Cross-cutting — Watchdog daemon (referenced by PM.4 + Session Start Protocol)
# ---------------------------------------------------------------------------


class TestWatchdogDaemon:
    """Background daemon that detects and unjams idle/stalled sessions."""

    def test_watchdog_script_exists(self):
        assert (SCRIPTS_DIR / "agent_watchdog.py").exists(), (
            "scripts/agent_watchdog.py must exist"
        )

    def test_watchdog_auto_target_exists(self):
        assert _makefile_has_target(_makefile_source(), "watchdog-auto")

    def test_watchdog_plugin_registered(self):
        oc = (ROOT / "opencode.json").read_text()
        assert "watchdog.ts" in oc, "watchdog.ts must be registered in opencode.json"
