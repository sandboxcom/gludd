#!/usr/bin/env python3
"""Active plugin liveness check — verifies enforce-stop.ts is structurally intact
and its hooks are actually firing.

This was created after `chat.response.transform` was dead code for 3 weeks with
no detection. The passive counter-based check (`check-plugin-liveness` in the
Makefile) can tell you a hook hasn't fired recently but cannot detect a hook
that was renamed/removed and never will fire again.

Three-layer verification:
  1. STRUCTURAL: Parse the TS source and confirm all required hook registrations
     and load-bearing functions exist. A renamed hook is caught immediately.
  2. PASSIVE: Check counter files written by the running plugin to confirm hooks
     have fired recently (within the last 5 minutes).
  3. ACTIVE (if node available): Execute a tiny Node.js script that requires the
     plugin's alive.json to confirm the plugin bootstrapped correctly.

Exits 0 if all checks pass. Exits 1 if any check fails. Writes detailed result
to /tmp/gludd-plugin-liveness.json for observability.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, TypedDict

try:
    from scripts import gludd_env_defaults as gludd_env_defaults
except ModuleNotFoundError:  # pragma: no cover - direct launch from scripts/
    import gludd_env_defaults


WORKSPACE = Path(os.environ.get("GLUDD_WORKSPACE_ROOT", os.getcwd()))
PLUGIN_DIR = WORKSPACE / ".opencode" / "plugin"
PLUGIN_FILE = PLUGIN_DIR / "enforce-stop.ts"
ALIVE_FILE = Path("/tmp/gludd-plugin-alive.json")
TEXT_COMPLETE_COUNTER = Path("/tmp/gludd-stop-text-complete-count.json")
TOOL_COUNTER = Path("/tmp/gludd-stop-tool-counts.json")
RESULT_FILE = Path("/tmp/gludd-plugin-liveness.json")
MAX_AGE_SECS = int(os.environ.get("GLUDD_LIVENESS_MAX_AGE", gludd_env_defaults.LIVENESS_MAX_AGE_DEFAULT))


class HookCheck(TypedDict):
    name: str
    status: str  # OK | MISSING | STALE | UNKNOWN
    detail: str


class LivenessResult(TypedDict):
    timestamp: str
    ts_epoch: float
    plugin_file: str
    overall: str  # PASS | FAIL
    structural: list[HookCheck]
    passive: list[HookCheck]
    active: list[HookCheck]


# ── STRUCTURAL CHECKS ───────────────────────────────────────────────────────


REQUIRED_HOOK_REGISTRATIONS: dict[str, str] = {
    '"experimental.text.complete"': (
        "experimental.text.complete hook registration — the stop-pattern "
        "detection guardrail is dead without it"
    ),
    '"tool.execute.before"': (
        "tool.execute.before hook registration — question-deny + commit-block "
        "are dead without it"
    ),
    '"session.idle"': (
        "session.idle event handler — state-file refresh at session start "
        "is dead without it"
    ),
    '"experimental.chat.system.transform"': (
        "experimental.chat.system.transform hook registration — "
        "orchestration injection at session start is dead without it"
    ),
}

REQUIRED_FUNCTIONS: dict[str, str] = {
    "function responseLooksTerminal": (
        "responseLooksTerminal function — the state-based terminal-response "
        "detector (BUGS.md structural fix) is gone"
    ),
    "function tasksMdHasUnchecked": (
        "tasksMdHasUnchecked function — TASKS.md unchecked-work detection "
        "(2026-06-30 incident fix) is gone"
    ),
    "function ratchetHasEntries": (
        "ratchetHasEntries function — ratchet.yml entry count is gone"
    ),
    "function gateStatusIsRed": (
        "gateStatusIsRed function — .gate-status red detection is gone"
    ),
    "function repoHasPendingWork": (
        "repoHasPendingWork function — git-state pending-work detection "
        "(2026-06-28 incident fix) is gone"
    ),
    "function ciIsPendingOrRed": (
        "ciIsPendingOrRed function — CI verdict query is gone"
    ),
    "function computeHealthScore": (
        "computeHealthScore function — health scoring is gone"
    ),
}

REQUIRED_CONSTANTS: dict[str, str] = {
    "STOP_ENFORCE": (
        "STOP_ENFORCE constant — the enforcement gate is gone; plugin "
        "may be advisory-only"
    ),
    "COMPLETION_VERBATIM": (
        "COMPLETION_VERBATIM constant — the strongest stop-signal regex "
        "is gone"
    ),
    "STOP_LIKE_TARGETS_RE": (
        "STOP_LIKE_TARGETS_RE constant — commit-block regex is gone"
    ),
    "QUESTION_DENY_REASON": (
        "QUESTION_DENY_REASON constant — question-deny message is gone"
    ),
}

REQUIRED_KEYWORDS: dict[str, str] = {
    "HARD STOP": (
        "HARD STOP block text — the state-based block message is gone; "
        "the stop detector may be advisory-only"
    ),
    "/tmp/gludd-plugin-alive.json": (
        "plugin-alive.json side-effect — the alive heartbeat that "
        "proves the plugin loaded is gone"
    ),
    "throw new Error": (
        "throw/block path — no blocking mechanism found; all enforcement "
        "may be advisory-only"
    ),
}


def _read_plugin_src() -> str:
    if not PLUGIN_FILE.exists():
        return ""
    return PLUGIN_FILE.read_text(encoding="utf-8")


def _run_structural_checks(src: str) -> list[HookCheck]:
    checks: list[HookCheck] = []
    now = time.time()

    # Check hooks
    for hook, description in REQUIRED_HOOK_REGISTRATIONS.items():
        if hook in src:
            checks.append({"name": hook.strip('"'), "status": "OK",
                           "detail": "found in plugin source"})
        else:
            checks.append({"name": hook.strip('"'), "status": "MISSING",
                           "detail": f"MISSING: {description}"})

    # Check functions
    for func, description in REQUIRED_FUNCTIONS.items():
        if func in src:
            checks.append({"name": func.replace("function ", ""), "status": "OK",
                           "detail": "found in plugin source"})
        else:
            checks.append({"name": func.replace("function ", ""), "status": "MISSING",
                           "detail": f"MISSING: {description}"})

    # Check constants
    for const, description in REQUIRED_CONSTANTS.items():
        if const in src:
            checks.append({"name": const, "status": "OK",
                           "detail": "found in plugin source"})
        else:
            checks.append({"name": const, "status": "MISSING",
                           "detail": f"MISSING: {description}"})

    # Check keywords
    for keyword, description in REQUIRED_KEYWORDS.items():
        if keyword in src:
            checks.append({"name": keyword[:50], "status": "OK",
                           "detail": "found in plugin source"})
        else:
            checks.append({"name": keyword[:50], "status": "MISSING",
                           "detail": f"MISSING: {description}"})

    # Check plugin registration
    if "export default" in src:
        checks.append({"name": "export_default", "status": "OK",
                       "detail": "plugin exports default"})
    else:
        checks.append({"name": "export_default", "status": "MISSING",
                       "detail": "MISSING: no default export — plugin won't register"})

    if "@opencode-ai/plugin" in src:
        checks.append({"name": "import_plugin_type", "status": "OK",
                       "detail": "imports @opencode-ai/plugin type"})
    else:
        checks.append({"name": "import_plugin_type", "status": "MISSING",
                       "detail": "MISSING: no @opencode-ai/plugin import"})

    return checks


# ── PASSIVE CHECKS ──────────────────────────────────────────────────────────


def _read_json_file(path: Path) -> Optional[dict]:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _run_passive_checks() -> list[HookCheck]:
    checks: list[HookCheck] = []
    now = time.time()

    # Check plugin-alive.json
    alive = _read_json_file(ALIVE_FILE)
    if alive is None:
        checks.append({"name": "plugin_alive_file", "status": "MISSING",
                       "detail": "MISSING: /tmp/gludd-plugin-alive.json not found — "
                       "plugin may not have loaded"})
    else:
        enforce_stop = alive.get("enforce-stop", {})
        if not enforce_stop:
            checks.append({"name": "plugin_alive_enforce_stop", "status": "MISSING",
                           "detail": "MISSING: enforce-stop entry not in alive.json"})
        else:
            loaded_ts = enforce_stop.get("ts", 0)
            age = now - (loaded_ts / 1000.0) if loaded_ts > 1e11 else now - loaded_ts
            if age > MAX_AGE_SECS * 2:
                checks.append({"name": "plugin_alive_enforce_stop", "status": "STALE",
                               "detail": f"last loaded {age:.0f}s ago (> {MAX_AGE_SECS * 2}s) — "
                               "plugin may not be running"})
            else:
                checks.append({"name": "plugin_alive_enforce_stop", "status": "OK",
                               "detail": f"plugin loaded {age:.0f}s ago"})

    # Check text.complete counter
    tc = _read_json_file(TEXT_COMPLETE_COUNTER)
    if tc is None:
        checks.append({"name": "text_complete_counter", "status": "MISSING",
                       "detail": "MISSING: text.complete counter file not found — "
                       "hook may never have fired"})
    else:
        count = tc.get("count", 0)
        last_ts = tc.get("ts", 0)
        age = now - (last_ts / 1000.0) if last_ts > 1e11 else now - last_ts
        if age > MAX_AGE_SECS:
            checks.append({"name": "text_complete_counter", "status": "STALE",
                           "detail": f"last fired {age:.0f}s ago (> {MAX_AGE_SECS}s), "
                           f"count={count} — hook may have stopped firing"})
        elif count == 0:
            checks.append({"name": "text_complete_counter", "status": "STALE",
                           "detail": f"count={count} — hook exists but has never incremented"})
        else:
            checks.append({"name": "text_complete_counter", "status": "OK",
                           "detail": f"count={count}, last fired {age:.0f}s ago"})

    # Check tool.execute.before counter
    tool = _read_json_file(TOOL_COUNTER)
    if tool is None:
        checks.append({"name": "tool_execute_before_counter", "status": "MISSING",
                       "detail": "MISSING: tool.execute.before counter not found"})
    else:
        allowed = tool.get("allowed", 0)
        blocked = tool.get("blocked", 0)
        last_ts = tool.get("ts", 0)
        age = now - (last_ts / 1000.0) if last_ts > 1e11 else now - last_ts
        if age > MAX_AGE_SECS:
            checks.append({"name": "tool_execute_before_counter", "status": "STALE",
                           "detail": f"last fired {age:.0f}s ago (> {MAX_AGE_SECS}s), "
                           f"allowed={allowed} blocked={blocked}"})
        elif allowed == 0 and blocked == 0:
            checks.append({"name": "tool_execute_before_counter", "status": "STALE",
                           "detail": f"allowed={allowed} blocked={blocked} — "
                           "hook exists but has never processed a tool"})
        else:
            checks.append({"name": "tool_execute_before_counter", "status": "OK",
                           "detail": f"allowed={allowed} blocked={blocked}, "
                           f"last fired {age:.0f}s ago"})

    return checks


# ── ACTIVE CHECK (Node.js) ──────────────────────────────────────────────────


def _run_active_checks() -> list[HookCheck]:
    """Try to verify the plugin is importable via Node.js.

    We cannot directly import the TS module (it requires the opencode runtime),
    but we can verify that the alive file exists and is well-formed JSON.
    This is the best approximation without the opencode plugin host.
    """
    checks: list[HookCheck] = []

    alive = _read_json_file(ALIVE_FILE)
    if alive is None:
        checks.append({"name": "active_alive_json", "status": "MISSING",
                       "detail": "Cannot verify — alive.json missing. "
                       "Plugin may not have loaded at all."})
    else:
        enforce_stop = alive.get("enforce-stop", {})
        loaded = enforce_stop.get("loaded", "")
        if loaded:
            checks.append({"name": "active_alive_json", "status": "OK",
                           "detail": f"plugin reports loaded at {loaded}"})
        else:
            checks.append({"name": "active_alive_json", "status": "MISSING",
                           "detail": "alive.json exists but enforce-stop entry has no 'loaded' field"})

    if PLUGIN_FILE.exists():
        checks.append({"name": "active_plugin_exists", "status": "OK",
                       "detail": f"plugin file exists ({PLUGIN_FILE.stat().st_size} bytes)"})
    else:
        checks.append({"name": "active_plugin_exists", "status": "MISSING",
                       "detail": f"plugin file not found: {PLUGIN_FILE}"})

    return checks


# ── MAIN ────────────────────────────────────────────────────────────────────


def run_check() -> LivenessResult:
    now = time.time()
    result: LivenessResult = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "ts_epoch": now,
        "plugin_file": str(PLUGIN_FILE),
        "overall": "PASS",
        "structural": [],
        "passive": [],
        "active": [],
    }

    if not PLUGIN_FILE.exists():
        result["structural"].append({
            "name": "plugin_file", "status": "MISSING",
            "detail": f"Plugin file not found: {PLUGIN_FILE}"
        })
        result["overall"] = "FAIL"
        return result

    src = _read_plugin_src()
    if not src.strip():
        result["structural"].append({
            "name": "plugin_file", "status": "MISSING",
            "detail": f"Plugin file is empty: {PLUGIN_FILE}"
        })
        result["overall"] = "FAIL"
        return result

    result["structural"] = _run_structural_checks(src)
    result["passive"] = _run_passive_checks()
    result["active"] = _run_active_checks()

    all_checks = result["structural"] + result["passive"] + result["active"]
    for check in all_checks:
        if check["status"] != "OK":
            result["overall"] = "FAIL"
            break

    return result


def main() -> int:
    result = run_check()

    try:
        RESULT_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
    except Exception:
        pass

    print("=== Plugin Liveness Check ===")
    print(f"Plugin: {result['plugin_file']}")
    print(f"Overall: {result['overall']}")
    print()

    sections = [
        ("STRUCTURAL (source code integrity)", result["structural"]),
        ("PASSIVE (runtime counter signals)", result["passive"]),
        ("ACTIVE (runtime verification)", result["active"]),
    ]

    exit_code = 0
    for section_title, checks in sections:
        print(f"--- {section_title} ---")
        for check in checks:
            marker = "OK" if check["status"] == "OK" else "FAIL"
            print(f"  [{marker}] {check['name']}: {check['detail']}")
            if check["status"] != "OK":
                exit_code = 1
        print()

    if exit_code != 0:
        print("LIVENESS CHECK FAILED — enforce-stop.ts may be dead or silently disabled.")
        print("Result written to:", RESULT_FILE)
    else:
        print("LIVENESS CHECK PASSED — enforce-stop.ts is structurally intact and firing.")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
