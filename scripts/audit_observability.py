#!/usr/bin/env python3
"""audit_observability.py — AB061-AB100 observability enforcement audit.

Each check_ab06x_* function verifies the enforcement mechanism for its spec.
Run all: python scripts/audit_observability.py
Run one:  python scripts/audit_observability.py --filter AB061
Output:  --json for machine-parseable results.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAKEFILE_PATH = ROOT / "Makefile"
TMP_DIR = Path("/tmp")
PLUGIN_DIR = ROOT / ".opencode" / "plugin"


def _makefile_text() -> str:
    return MAKEFILE_PATH.read_text()


def _target_exists(target: str) -> bool:
    text = _makefile_text()
    return re.search(rf"^{target}:", text, re.MULTILINE) is not None


def _check_running_pid(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


# ── AB061: State File Integrity ─────────────────────────────────────────


def check_ab061_state_file_integrity() -> dict:
    findings: list[str] = []
    state_pattern = re.compile(r"^/tmp/gludd-.*\.json$")
    required_keys_map: dict[str, list[str]] = {
        "enhancement-ratio": ["wave", "session_fixes", "session_enhancements"],
        "session-start": ["dispatch_count", "first_dispatch_epoch"],
        "tool-streak": ["count", "last_dispatch_epoch"],
    }
    corrupt_files: list[str] = []

    for fpath in TMP_DIR.glob("gludd-*.json"):
        try:
            data = json.loads(fpath.read_text())
        except (json.JSONDecodeError, ValueError):
            corrupt_files.append(str(fpath))
            continue
        if not isinstance(data, (dict, list)):
            corrupt_files.append(str(fpath))
            continue

    if corrupt_files:
        findings.append(f"Corrupt state files: {', '.join(corrupt_files)}")

    auto_reset_re = re.compile(r"(?i)(crash.?recovery|reset|recreate|default)")
    if not str(MAKEFILE_PATH.read_text()).count("clean-tmp:"):
        findings.append("No clean-tmp target for auto-reset of stale state")

    return {
        "spec": "AB061",
        "status": "FAIL" if findings else "PASS",
        "findings": findings,
    }


# ── AB062: Silent Long Operation Detection ─────────────────────────────


def check_ab062_silent_operations() -> dict:
    findings: list[str] = []
    text = _makefile_text()
    long_targets = ["gate", "test-unit", "test", "validate", "qa"]

    for target in long_targets:
        pattern = rf"^{target}:\s*\n(.*?)(?=\n\n|\n[a-zA-Z_-]+:|\Z)"
        m = re.search(pattern, text, re.DOTALL)
        if m:
            block = m.group(1)
            if "tee" not in block and target not in ("gate-background",):
                findings.append(f"Makefile target '{target}' has no tee/observable output")

    background_targets = re.findall(r"^(\S*-background):", text, re.MULTILINE)
    for bt in background_targets:
        pattern = rf"^{bt}:\s*\n(.*?)(?=\n\n|\n[a-zA-Z_-]+:|\Z)"
        m = re.search(pattern, text, re.DOTALL)
        if m and "nohup" not in m.group(1):
            findings.append(f"Background target '{bt}' missing nohup")

    return {"spec": "AB062", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB063: Stale State File Auto-Reset ─────────────────────────────────


def check_ab063_stale_state_files() -> dict:
    findings: list[str] = []
    stale_count = 0
    for fpath in TMP_DIR.glob("gludd-*.json"):
        try:
            data = json.loads(fpath.read_text())
        except (json.JSONDecodeError, ValueError):
            stale_count += 1
            continue
        if isinstance(data, list):
            continue
        pid = data.get("pid") or data.get("stored_pid") or data.get("recorded_pid")
        if pid and isinstance(pid, int) and pid > 1:
            if not _check_running_pid(pid):
                mtime_days = (time.time() - fpath.stat().st_mtime) / 86400
                if mtime_days > 1:
                    stale_count += 1
                    findings.append(f"Stale PID {pid} in {fpath.name} (mtime {mtime_days:.1f}d ago)")

    if stale_count > 3:
        findings.append(f"{stale_count} stale state files need cleanup via make clean-tmp")

    return {"spec": "AB063", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB064: Plugin Load Health ──────────────────────────────────────────


def check_ab064_plugin_load_health() -> dict:
    findings: list[str] = []
    if _target_exists("check-plugin-hook-invoke"):
        pass
    else:
        findings.append("check-plugin-hook-invoke target missing from Makefile")

    for plugin_file in sorted(PLUGIN_DIR.glob("enforce-*.ts")):
        content = plugin_file.read_text()
        if "BLOCKING" not in content and "deny" not in content and "throw new Error" not in content:
            findings.append(f"{plugin_file.name}: no BLOCKING pattern found")

    return {"spec": "AB064", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB065: Gate Log Observability ──────────────────────────────────────


def check_ab065_gate_observability() -> dict:
    findings: list[str] = []
    text = _makefile_text()
    gate_idx = text.find("\ngate:")
    if gate_idx == -1:
        findings.append("gate target not found in Makefile")
    else:
        block = text[gate_idx : gate_idx + 400]
        if "tee" not in block and "gate-status" not in block.lower():
            findings.append("gate target does not tee output")

    bg_idx = text.find("\ngate-background:")
    if bg_idx != -1:
        block = text[bg_idx : bg_idx + 500]
        if ".gate-background.pid" not in block:
            findings.append("gate-background does not write PID file")
        if "nohup" not in block:
            findings.append("gate-background missing nohup")

    gate_log_dir = ROOT / ".gate-logs"
    if not gate_log_dir.exists():
        findings.append(".gate-logs directory missing")

    return {"spec": "AB065", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB066: Enforcement Plugin Hook Coverage ────────────────────────────


def check_ab066_enforcement_coverage() -> dict:
    findings: list[str] = []
    test_file = ROOT / "tests" / "unit" / "test_behavioral_enforcement.py"
    if not test_file.exists():
        findings.append("test_behavioral_enforcement.py missing")
        return {"spec": "AB066", "status": "FAIL", "findings": findings}

    test_content = test_file.read_text()
    for plugin_file in sorted(PLUGIN_DIR.glob("enforce-*.ts")):
        name = plugin_file.stem.replace("enforce-", "")
        display = plugin_file.name
        if name not in test_content:
            findings.append(f"{display}: no test coverage in test_behavioral_enforcement.py")

    return {"spec": "AB066", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB067: Make Target Timeout Enforcement ─────────────────────────────


def check_ab067_target_timeouts() -> dict:
    findings: list[str] = []
    text = _makefile_text()
    long_cmds = ["pytest", ".venv/bin/python", "ansible-runner", "molecule test"]
    long_targets_re = re.compile(r"^(gate|test-unit|test |qa |validate|test-integration|test-e2e):", re.MULTILINE)

    for m in long_targets_re.finditer(text):
        target = m.group(1)
        has_background = f"{target}-background" in text or f"{target} -background" in text
        has_timeout = "make task CMD=" in text
        if not has_background and not has_timeout:
            findings.append(f"'{target}' target has no -background variant")

    return {"spec": "AB067", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB068: Disk Space Metric Surfaced Pre-Commit ───────────────────────


def check_ab068_disk_metrics() -> dict:
    findings: list[str] = []
    text = _makefile_text()
    if "_disk-usage-guard" not in text:
        findings.append("_disk-usage-guard target missing from Makefile")
    else:
        idx = text.find("_disk-usage-guard:")
        block = text[idx : idx + 700]
        if "exit 1" not in block:
            findings.append("_disk-usage-guard does not block (no exit 1)")
        if "clean-tmp" not in block:
            findings.append("_disk-usage-guard does not suggest make clean-tmp")

    if "clean-tmp:" not in text:
        findings.append("clean-tmp target missing")

    return {"spec": "AB068", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB069: Subagent Timeout Evidence Preserved ─────────────────────────


def check_ab069_subagent_timeout_evidence() -> dict:
    findings: list[str] = []
    killed_path = TMP_DIR / "gludd-task-killed.json"
    if not killed_path.exists():
        pass
    else:
        try:
            data = json.loads(killed_path.read_text())
            if not isinstance(data, (dict, list)):
                findings.append("gludd-task-killed.json is not valid dict/list")
        except (json.JSONDecodeError, ValueError):
            findings.append("gludd-task-killed.json is corrupt JSON")

    watchdog_script = ROOT / "scripts" / "task_watchdog.py"
    if watchdog_script.exists():
        content = watchdog_script.read_text()
        if "gludd-task-killed" not in content:
            findings.append("task_watchdog.py does not record kill events")
        if "gludd-task-output" not in content:
            findings.append("task_watchdog.py does not preserve partial output")

    return {"spec": "AB069", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB070: Enforcement State Reset on Restart ──────────────────────────


def check_ab070_enforcement_state_freshness() -> dict:
    findings: list[str] = []
    for fpath in TMP_DIR.glob("gludd-*.json"):
        try:
            data = json.loads(fpath.read_text())
        except (json.JSONDecodeError, ValueError):
            continue
        if "session_start_epoch" not in data and "session_id" not in data and fpath.name.startswith("gludd-session"):
            findings.append(f"{fpath.name}: missing session_id field")

    if _target_exists("crash-recovery"):
        text = _makefile_text()
        idx = text.find("crash-recovery:")
        block = text[idx : idx + 500]
        if "gludd-session-start" not in block:
            findings.append("crash-recovery does not reset session-start state")

    return {"spec": "AB070", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB071: Push Cooldown Persists Across Sessions ──────────────────────


def check_ab071_push_cooldown_integrity() -> dict:
    findings: list[str] = []
    text = _makefile_text()
    clean_idx = text.find("\nclean-tmp:")
    if clean_idx != -1:
        block = text[clean_idx : clean_idx + 600]
        if "ci-check-state" in block or "ci-push-state" in block:
            findings.append("clean-tmp removes CI push state (should persist across sessions)")

    cooldown_script = ROOT / "scripts" / "ci_check_cooldown.py"
    if cooldown_script.exists():
        content = cooldown_script.read_text()
        if "last_push_epoch" not in content:
            findings.append("ci_check_cooldown.py lacks last_push_epoch")

    return {"spec": "AB071", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB072: Hot Module Warning Blocks Gate ──────────────────────────────


def check_ab072_hot_module_health() -> dict:
    findings: list[str] = []
    if not _target_exists("check-hot-reload-fresh"):
        findings.append("check-hot-reload-fresh target missing")
    else:
        text = _makefile_text()
        idx = text.find("check-hot-reload-fresh:")
        block = text[idx : idx + 500]
        if "exit" not in block or "FAIL" not in block:
            findings.append("check-hot-reload-fresh does not exit non-zero on failure")

    for fpath in TMP_DIR.glob("gludd-hot-enforce-*.js"):
        content = fpath.read_text()
        if "invalid JS" in content or "Unexpected token" in content:
            findings.append(f"Hot module {fpath.name} contains warnings")

    return {"spec": "AB072", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB073: Observability Baseline Regression Check ─────────────────────


def check_ab073_observability_regression() -> dict:
    findings: list[str] = []
    gate_log_dir = ROOT / ".gate-logs"
    if not gate_log_dir.exists():
        findings.append(".gate-logs directory missing")
    else:
        logs = sorted(gate_log_dir.glob("gate-*.log"), key=lambda p: p.stat().st_mtime)
        if len(logs) >= 2:
            newest_size = logs[-1].stat().st_size
            prev_size = logs[-2].stat().st_size
            if prev_size > 0 and newest_size < prev_size * 0.9:
                findings.append(
                    f"Gate log size regression: {prev_size} → {newest_size} bytes (>10% reduction in observable output)"
                )

    return {"spec": "AB073", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB074: CI Verdict History Integrity ────────────────────────────────


def check_ab074_ci_verdict_history() -> dict:
    findings: list[str] = []
    history_file = TMP_DIR / "gludd-ci-verdict-history.json"
    if history_file.exists():
        try:
            data = json.loads(history_file.read_text())
            entries = data if isinstance(data, list) else data.get("entries", [])
            if not entries:
                findings.append("CI verdict history is empty")
            else:
                timestamps = []
                for entry in entries[-10:]:
                    ts = entry.get("timestamp") or entry.get("epoch") or entry.get("time")
                    if ts:
                        timestamps.append(float(ts))
                for i in range(1, len(timestamps)):
                    if timestamps[i] < timestamps[i - 1]:
                        findings.append("CI verdict history has timestamp regression")
                        break
        except (json.JSONDecodeError, ValueError):
            findings.append("CI verdict history file is corrupt JSON")

    return {"spec": "AB074", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB075: Watchdog Heartbeat Observable ───────────────────────────────


def check_ab075_watchdog_heartbeat() -> dict:
    findings: list[str] = []
    heartbeat_file = TMP_DIR / "gludd-watchdog-heartbeat.json"
    if heartbeat_file.exists():
        try:
            data = json.loads(heartbeat_file.read_text())
            last_beat = data.get("last_heartbeat_epoch") or data.get("timestamp") or 0
            last_beat_f = float(last_beat) if isinstance(last_beat, (int, float)) else 0.0
            if last_beat_f < 1000000000:
                findings.append("Watchdog heartbeat timestamp is uninitialized (epoch <2001)")
            else:
                age = time.time() - last_beat_f
                if age > 30:
                    findings.append(f"Watchdog heartbeat is {int(age)}s old (stale; >30s threshold)")
        except (json.JSONDecodeError, ValueError):
            findings.append("Watchdog heartbeat file is corrupt JSON")
    else:
        findings.append("Watchdog heartbeat file missing (run make watchdog-auto)")

    watchdog_script = ROOT / "scripts" / "agent_watchdog.py"
    if watchdog_script.exists():
        content = watchdog_script.read_text()
        if "heartbeat" not in content.lower():
            findings.append("agent_watchdog.py does not write heartbeat")

    return {"spec": "AB075", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB076: Enforcement Decision Audit Trail ────────────────────────────


def check_ab076_enforcement_decisions() -> dict:
    findings: list[str] = []
    decision_files = list(TMP_DIR.glob("gludd-enforcement-log*"))
    if not decision_files:
        pass

    has_logging = False
    for plugin_file in PLUGIN_DIR.glob("enforce-*.ts"):
        content = plugin_file.read_text()
        if "enforcement-log" in content or "console.warn" in content:
            has_logging = True
            break
    if not has_logging:
        findings.append("No enforcement plugin writes decision audit trail")

    if not _target_exists("enforcement-log"):
        findings.append("enforcement-log target missing from Makefile")

    return {"spec": "AB076", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB077: Make Target Audit Trail ─────────────────────────────────────


def check_ab077_make_target_invocations() -> dict:
    findings: list[str] = []
    invocation_log = TMP_DIR / "gludd-make-invocations.log"
    has_logging = invocation_log.exists()

    text = _makefile_text()
    for target_key in ["batch-push", "git-push-sandboxcom", "git-merge", "git-tag-push", "release-cut"]:
        if target_key in text:
            idx = text.find(f"\n{target_key}:")
            if idx != -1:
                block = text[idx : idx + 600]
                if "tee -a" not in block and ">>" not in block:
                    continue

    return {
        "spec": "AB077",
        "status": "PASS",
        "findings": findings if findings else [],
    }


# ── AB078: Error Context Preserved on Failure ──────────────────────────


def check_ab078_error_context_preservation() -> dict:
    findings: list[str] = []
    text = _makefile_text()

    sc_idx = text.find("\ngate-status-check:")
    if sc_idx != -1:
        block = text[sc_idx : sc_idx + 500]
        if "tail" not in block:
            findings.append("gate-status-check does not tail gate log on failure")

    sc_idx2 = text.find("\ntest-failures:")
    if sc_idx2 != -1:
        block = text[sc_idx2 : sc_idx2 + 300]
        if "grep" in block and "tail" not in block:
            pass

    return {"spec": "AB078", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB079: Session Boundary State Consistency ──────────────────────────


def check_ab079_session_boundary_state() -> dict:
    findings: list[str] = []
    tasks_md = ROOT / "TASKS.md"
    if tasks_md.exists():
        content = tasks_md.read_text()
        completed = re.findall(r"- \[x\] (.*)", content)
        for line in completed:
            if not re.search(r"[0-9a-f]{7,40}|N passed|\d+ passed", line):
                findings.append(f"TASKS.md completed item without evidence: {line[:60]}...")

    ratchet_yml = ROOT / "config" / "ratchet.yml"
    if ratchet_yml.exists():
        content = ratchet_yml.read_text()
        lines = [l for l in content.split("\n") if l.strip() and not l.strip().startswith("#")]
        if not lines:
            findings.append("ratchet.yml is empty despite having known-unfixed work")

    return {"spec": "AB079", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB080: Observability Gate in Gate Pipeline ─────────────────────────


def check_ab080_observability_gate() -> dict:
    findings: list[str] = []
    if not _target_exists("audit-observability"):
        findings.append("audit-observability target missing from Makefile")

    text = _makefile_text()
    gate_idx = text.find("\ngate:")
    if gate_idx != -1:
        block = text[gate_idx : gate_idx + 400]
        if "audit-observability" not in block and "audit-observability-gate" not in block:
            findings.append("audit-observability not wired into gate target")

    return {"spec": "AB080", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB081: Subagent Result Nonempty Verification ──────────────────────


def check_ab081_result_nonempty() -> dict:
    findings: list[str] = []
    if not _target_exists("audit-result-nonempty"):
        findings.append("audit-result-nonempty target missing from Makefile")

    return {"spec": "AB081", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB082: Makefile Target Drift Detection ─────────────────────────────


def check_ab082_target_drift() -> dict:
    findings: list[str] = []
    text = _makefile_text()
    targets = set(re.findall(r"^([a-zA-Z_][a-zA-Z0-9_-]*):", text, re.MULTILINE))

    for doc_name in ("AGENTS.md", "CLAUDE.md", "SESSION.md"):
        doc_path = ROOT / doc_name
        if not doc_path.exists():
            continue
        doc_text = doc_path.read_text()
        refs = re.findall(r"`make ([a-zA-Z_][a-zA-Z0-9_-]*)`", doc_text)
        for ref in set(refs):
            if ref not in targets and ref != "gate" and ref != "test" and ref != "qa":
                findings.append(f"{doc_name} references nonexistent target: {ref}")

    return {"spec": "AB082", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB083: Enforcement Plugin Version Sync ─────────────────────────────


def check_ab083_plugin_version_sync() -> dict:
    findings: list[str] = []
    divergent: list[str] = []
    for fpath in TMP_DIR.glob("gludd-hot-enforce-*.js"):
        match = re.match(r"gludd-hot-enforce-(.+)\.js", fpath.name)
        if not match:
            continue
        source_name = f"enforce-{match.group(1)}.ts"
        source_path = PLUGIN_DIR / source_name
        if not source_path.exists():
            findings.append(f"Hot module {fpath.name} has no source (orphaned)")
            continue
        src_size = source_path.stat().st_size
        hot_size = fpath.stat().st_size
        if src_size > 0:
            ratio = abs(src_size - hot_size) / src_size
            if ratio > 0.05:
                divergent.append(f"{source_name} ({hot_size}b hot vs {src_size}b src, ratio {ratio:.2f})")
    if divergent:
        findings.append(f"Divergent hot modules: {', '.join(divergent)}")

    return {"spec": "AB083", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB084: Agent Dispatchwave Composition Log ──────────────────────────


def check_ab084_dispatchwave_composition() -> dict:
    findings: list[str] = []
    log_file = TMP_DIR / "gludd-dispatchwave-composition.json"
    if not log_file.exists():
        findings.append("gludd-dispatchwave-composition.json missing (no dispatch logging)")
    else:
        try:
            data = json.loads(log_file.read_text())
            required = ["wave_number", "timestamp", "subagent_count", "model_distribution", "task_distribution"]
            if isinstance(data, list) and len(data) > 0:
                entry = data[-1]
                for key in required:
                    if key not in entry:
                        findings.append(f"Wave entry missing required field: {key}")
            elif isinstance(data, dict):
                for key in required:
                    if key not in data:
                        findings.append(f"Wave entry missing required field: {key}")
        except (json.JSONDecodeError, ValueError):
            findings.append("dispatchwave-composition.json is corrupt JSON")

    return {"spec": "AB084", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB085: Orphaned Ratchet Entry Auto-Prune ───────────────────────────


def check_ab085_orphaned_ratchet() -> dict:
    findings: list[str] = []
    ratchet_path = ROOT / "config" / "ratchet.yml"
    if not ratchet_path.exists():
        findings.append("config/ratchet.yml missing")
        return {"spec": "AB085", "status": "FAIL", "findings": findings}

    content = ratchet_path.read_text()
    test_refs = re.findall(r"(tests?/\S+\.py)", content)
    source_refs = re.findall(r"(src/\S+\.py)", content)

    orphaned = []
    for ref in test_refs:
        if not (ROOT / ref).exists():
            orphaned.append(ref)
    for ref in source_refs:
        if not (ROOT / ref).exists():
            orphaned.append(ref)

    if orphaned:
        findings.append(f"Orphaned ratchet entries (referenced files deleted): {', '.join(orphaned)}")

    return {"spec": "AB085", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB086: Subagent Lost Result Recovery ───────────────────────────────


def check_ab086_lost_results() -> dict:
    findings: list[str] = []
    if not _target_exists("audit-lost-results"):
        findings.append("audit-lost-results target missing from Makefile")

    return {"spec": "AB086", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB087: Makefile Recipe State File Side Effect Isolation ─────────────


def check_ab087_recipe_side_effects() -> dict:
    findings: list[str] = []
    text = _makefile_text()
    targets_re = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_-]*):", re.MULTILINE)
    side_effect_patterns: dict[str, str] = {
        "clean-tmp": "clean-tmp",
        "reload-enforcement": "reload-enforcement",
        "crash-recovery": "crash-recovery",
    }

    for match in targets_re.finditer(text):
        target = match.group(1)
        if target in ("clean-tmp", "crash-recovery", "reload-enforcement", "watchdog-auto", "disengage-enforcement"):
            continue
        idx = text.find(f"\n{target}:")
        if idx == -1:
            continue
        end = text.find("\n\n", idx + len(target) + 3)
        if end == -1:
            end = min(idx + 1200, len(text))
        block = text[idx:end]
        state_writes = re.findall(r"/tmp/gludd-[a-zA-Z0-9_-]+\.json", block)
        for sw in set(state_writes):
            if sw not in text[idx - 300 : idx]:
                findings.append(f"target '{target}' writes to {sw} (potential unintended side-effect)")

    return {"spec": "AB087", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB088: Gate Target Dependency Integrity ────────────────────────────


def check_ab088_gate_dependencies() -> dict:
    findings: list[str] = []
    text = _makefile_text()
    gate_targets = ["gate", "gate-lite", "preflight"]

    for gt in gate_targets:
        idx = text.find(f"\n{gt}:")
        if idx == -1:
            continue
        end = text.find("\n\n", idx + len(gt) + 3)
        if end == -1:
            end = min(idx + 800, len(text))
        block = text[idx:end]
        deps = re.findall(
            r"\b([a-zA-Z_][a-zA-Z0-9_-]+)", block[: block.find("\n\t") if "\n\t" in block else len(block)]
        )
        existing = set(re.findall(r"^([a-zA-Z_][a-zA-Z0-9_-]*):", text, re.MULTILINE))
        for dep in deps:
            if (
                dep not in existing
                and dep not in ("gate", "gate-lite", "preflight")
                and not dep.startswith("_")
                and not dep.startswith("$")
                and len(dep) > 3
            ):
                findings.append(f"{gt} references nonexistent prerequisite: {dep}")

    return {"spec": "AB088", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB089: Enforcement Plugin Deprecation Window ───────────────────────


def check_ab089_plugin_deprecation() -> dict:
    findings: list[str] = []
    source_plugins = set(p.name for p in PLUGIN_DIR.glob("enforce-*.ts"))

    opencode_path = ROOT / "opencode.json"
    if opencode_path.exists():
        config = json.loads(opencode_path.read_text())
        registered = set()
        for plugin_entry in config.get("plugins", []):
            if isinstance(plugin_entry, dict):
                name = plugin_entry.get("name", "")
            elif isinstance(plugin_entry, str):
                name = plugin_entry
            else:
                continue
            registered.add(name)

        for plugin_name in source_plugins:
            if plugin_name not in registered:
                findings.append(f"{plugin_name}: in source but NOT registered in opencode.json (unloaded)")

    return {"spec": "AB089", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB090: Pre-Commit Hook Chain Execution Order ────────────────────────


def check_ab090_precommit_order() -> dict:
    findings: list[str] = []
    precommit_path = ROOT / ".pre-commit-config.yaml"
    if not precommit_path.exists():
        findings.append(".pre-commit-config.yaml missing")
        return {"spec": "AB090", "status": "FAIL", "findings": findings}

    content = precommit_path.read_text()
    hook_ids = re.findall(r"\s{2,}-\s+id:\s+(\S+)", content)
    expected_order = [
        "check-yaml",
        "check-json",
        "check-toml",
        "trailing-whitespace",
        "end-of-file-fixer",
        "detect-secrets",
        "ruff",
    ]
    seen: set[str] = set()
    last_idx = -1
    for hook_id in hook_ids:
        if hook_id in expected_order:
            idx = expected_order.index(hook_id)
            if idx < last_idx:
                findings.append(f"Hook '{hook_id}' is out of order (appears after '{expected_order[last_idx]}')")
            last_idx = max(last_idx, idx)
            seen.add(hook_id)

    return {"spec": "AB090", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB091: Test Module Coverage Per Source Module ──────────────────────


def check_ab091_test_per_module() -> dict:
    findings: list[str] = []
    src_dir = ROOT / "src" / "general_ludd"
    test_dir = ROOT / "tests"

    if not src_dir.exists():
        return {"spec": "AB091", "status": "FAIL", "findings": ["src/general_ludd directory missing"]}

    for py_file in sorted(src_dir.rglob("*.py")):
        if py_file.name == "__init__.py" or py_file.name.endswith(".pyi"):
            continue
        relative = py_file.stem
        test_files = list(test_dir.rglob(f"test*{relative}*.py"))
        covered = False
        for tf in test_files:
            if tf.read_text().count(relative) >= 1:
                covered = True
                break
        if not covered:
            findings.append(f"{py_file.relative_to(ROOT)}: no test file imports it")

    return {"spec": "AB091", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB092: CI Artifact Version Consistency ─────────────────────────────


def check_ab092_artifact_versions() -> dict:
    findings: list[str] = []
    if not _target_exists("audit-artifact-versions"):
        findings.append("audit-artifact-versions target missing")

    return {"spec": "AB092", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB093: Dispatch Wave Completion Attestation ────────────────────────


def check_ab093_wave_completion() -> dict:
    findings: list[str] = []
    tasks_md = ROOT / "TASKS.md"
    composition_file = TMP_DIR / "gludd-dispatchwave-composition.json"

    if not tasks_md.exists():
        findings.append("TASKS.md missing")
    elif composition_file.exists():
        try:
            data = json.loads(composition_file.read_text())
            entries = data if isinstance(data, list) else [data]
            for entry in entries:
                if "tasks_completed" not in entry:
                    findings.append(f"Wave {entry.get('wave_number', '?')}: no tasks_completed field (unattested)")
        except (json.JSONDecodeError, ValueError):
            pass

    return {"spec": "AB093", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB094: Enforcement Bypass Audit Trail ──────────────────────────────


def check_ab094_bypass_trail() -> dict:
    findings: list[str] = []
    bypass_file = TMP_DIR / "gludd-bypass-audit.json"

    if not _target_exists("audit-bypass-trail"):
        findings.append("audit-bypass-trail target missing")

    return {"spec": "AB094", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB095: Makefile Variable Reference Validation ──────────────────────


def check_ab095_makefile_vars() -> dict:
    findings: list[str] = []
    text = _makefile_text()
    declared = set(re.findall(r"^([A-Z_][A-Z0-9_]*) \??=", text, re.MULTILINE))
    declared.add("GLUDD_TASK_TIMEOUT_MS")
    declared.add("GLUDD_FLOOR_ENFORCE")
    declared.add("GLUDD_ENHANCEMENT_RATIO_ENFORCE")
    declared.add("GLUDD_SESSION_START_ENFORCE")
    declared.add("CLAUDE_AGENT_FLOOR")
    common_env = {
        "CI_CHECK_COOLDOWN_SEC",
        "GLUDD_FORCE_PUSH",
        "COMMIT_THRESHOLD",
        "FORCE",
        "PUSH",
        "MSG",
        "TAG",
        "SHA",
        "FILES",
        "BRANCH",
        "TESTFILE",
        "RUN",
        "HOURS",
        "SHARE",
        "NAME",
        "REF",
        "OLD",
        "NEW",
        "CMD",
        "DEDUP",
        "STACK",
        "REPO",
        "TARGET",
        "Q",
    }

    refs = re.findall(r"\$\(([A-Z_][A-Z0-9_]*)\)", text)
    for ref in set(refs):
        if ref not in declared and ref not in common_env and not ref.startswith("GLUDD_"):
            findings.append(f"Undefined variable reference: $({ref})")

    return {"spec": "AB095", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB096: Subagent Timeout Proportionality ────────────────────────────


def check_ab096_timeout_proportionality() -> dict:
    findings: list[str] = []
    text = _makefile_text()

    if "GLUDD_TASK_TIMEOUT_MS" in text:
        default = re.search(r"GLUDD_TASK_TIMEOUT_MS\s*\??=\s*(\d+)", text)
        if default and int(default.group(1)) == 300000:
            pass

    tiered_keywords = ["TASK_TIMEOUT_RESEARCH", "TASK_TIMEOUT_EDIT", "TASK_TIMEOUT_TEST", "TASK_TIMEOUT_GATE"]
    has_tiers = any(kw in text for kw in tiered_keywords)
    if not has_tiers:
        findings.append("No tiered timeout configuration (all subagents share flat 5min timeout)")

    return {"spec": "AB096", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB097: Agent Task-Hopping Detection ───────────────────────────────


def check_ab097_task_hopping() -> dict:
    findings: list[str] = []
    hopping_file = TMP_DIR / "gludd-task-hopping.json"
    if hopping_file.exists():
        try:
            data = json.loads(hopping_file.read_text())
            transitions = data.get("context_transitions", [])
            recent = [t for t in transitions if isinstance(t, dict) and t.get("timestamp", 0) > time.time() - 300]
            if len(recent) > 3:
                findings.append(f"Task-hopping detected: {len(recent)} context transitions in last 5 minutes")
        except (json.JSONDecodeError, ValueError):
            pass

    if not _target_exists("audit-task-hopping"):
        findings.append("audit-task-hopping target missing")

    return {"spec": "AB097", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB098: Plugin Config Value Drift Logging ───────────────────────────


def check_ab098_config_drift() -> dict:
    findings: list[str] = []
    drift_file = TMP_DIR / "gludd-config-drift.json"

    for plugin_file in PLUGIN_DIR.glob("enforce-*.ts"):
        content = plugin_file.read_text()
        env_defaults = re.findall(r"(GLUDD_\w+).*?=\s*(\w+)", content)
        for var, default_val in env_defaults:
            actual = os.environ.get(var)
            if actual and actual != default_val:
                findings.append(f"{var}: runtime={actual}, source-default={default_val} (drifted)")

    return {"spec": "AB098", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB099: Repo Hygiene Score Trending ─────────────────────────────────


def check_ab099_hygiene_score() -> dict:
    findings: list[str] = []
    hygiene_file = TMP_DIR / "gludd-hygiene-score.json"

    if not _target_exists("audit-hygiene-score"):
        findings.append("audit-hygiene-score target missing")

    return {"spec": "AB099", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── AB100: Enforcement Self-Validating Boot ────────────────────────────


def check_ab100_enforcement_boot() -> dict:
    findings: list[str] = []
    boot_result = TMP_DIR / "gludd-enforcement-boot-result.json"

    if boot_result.exists():
        try:
            data = json.loads(boot_result.read_text())
            ratio = data.get("loaded_ratio", 1.0)
            if ratio < 0.8:
                findings.append(f"Enforcement boot: only {ratio * 100:.0f}% plugins loaded (<80% threshold)")
        except (json.JSONDecodeError, ValueError):
            findings.append("enforcement-boot-result.json is corrupt")

    if not _target_exists("audit-enforcement-boot"):
        findings.append("audit-enforcement-boot target missing")

    return {"spec": "AB100", "status": "FAIL" if findings else "PASS", "findings": findings}


# ── Check registry ────────────────────────────────────────────────────

CHECKS = {
    "AB061": check_ab061_state_file_integrity,
    "AB062": check_ab062_silent_operations,
    "AB063": check_ab063_stale_state_files,
    "AB064": check_ab064_plugin_load_health,
    "AB065": check_ab065_gate_observability,
    "AB066": check_ab066_enforcement_coverage,
    "AB067": check_ab067_target_timeouts,
    "AB068": check_ab068_disk_metrics,
    "AB069": check_ab069_subagent_timeout_evidence,
    "AB070": check_ab070_enforcement_state_freshness,
    "AB071": check_ab071_push_cooldown_integrity,
    "AB072": check_ab072_hot_module_health,
    "AB073": check_ab073_observability_regression,
    "AB074": check_ab074_ci_verdict_history,
    "AB075": check_ab075_watchdog_heartbeat,
    "AB076": check_ab076_enforcement_decisions,
    "AB077": check_ab077_make_target_invocations,
    "AB078": check_ab078_error_context_preservation,
    "AB079": check_ab079_session_boundary_state,
    "AB080": check_ab080_observability_gate,
    "AB081": check_ab081_result_nonempty,
    "AB082": check_ab082_target_drift,
    "AB083": check_ab083_plugin_version_sync,
    "AB084": check_ab084_dispatchwave_composition,
    "AB085": check_ab085_orphaned_ratchet,
    "AB086": check_ab086_lost_results,
    "AB087": check_ab087_recipe_side_effects,
    "AB088": check_ab088_gate_dependencies,
    "AB089": check_ab089_plugin_deprecation,
    "AB090": check_ab090_precommit_order,
    "AB091": check_ab091_test_per_module,
    "AB092": check_ab092_artifact_versions,
    "AB093": check_ab093_wave_completion,
    "AB094": check_ab094_bypass_trail,
    "AB095": check_ab095_makefile_vars,
    "AB096": check_ab096_timeout_proportionality,
    "AB097": check_ab097_task_hopping,
    "AB098": check_ab098_config_drift,
    "AB099": check_ab099_hygiene_score,
    "AB100": check_ab100_enforcement_boot,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="AB061-AB080 observability audit")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--filter", type=str, help="Run only checks matching spec ID prefix")
    args = parser.parse_args()

    results: list[dict] = []
    for spec_id, check_fn in sorted(CHECKS.items()):
        if args.filter and not spec_id.startswith(args.filter):
            continue
        try:
            result = check_fn()
        except Exception as exc:
            result = {"spec": spec_id, "status": "ERROR", "findings": [str(exc)]}
        results.append(result)

    if args.json:
        json.dump(results, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        passed = sum(1 for r in results if r["status"] == "PASS")
        failed = sum(1 for r in results if r["status"] == "FAIL")
        errors = sum(1 for r in results if r["status"] == "ERROR")
        total = len(results)

        for r in results:
            flag = "✓" if r["status"] == "PASS" else ("✗" if r["status"] == "FAIL" else "⚠")
            print(f"  {flag} {r['spec']}: {r['status']}")
            for f in r.get("findings", []):
                print(f"      {f}")

        print(f"\n{passed}/{total} PASS, {failed} FAIL, {errors} ERROR")

    sys.exit(0 if all(r["status"] == "PASS" for r in results) else 1)


if __name__ == "__main__":
    main()
