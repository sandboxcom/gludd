"""Diagnostic: dump all state to find why enforcement returns {allowed: True}."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

DIAG_SCRIPT = Path("/Users/shawnwilson/gludd/_diag_multitask.ts")


def _make_working_workspace(path: Path) -> None:
    (path / "TASKS.md").write_text("- [ ] test item\n")
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True)


def _run_diag(cwd: str, env_override: dict | None = None) -> dict:
    env = os.environ.copy()
    env["OPENCODE_SUBAGENT"] = ""
    if env_override:
        env.update(env_override)
    proc = subprocess.run(
        ["node", "--experimental-strip-types", str(DIAG_SCRIPT)],
        capture_output=True, text=True, timeout=30,
        cwd=cwd, env=env,
    )
    print(f"=== STDOUT ===")
    print(proc.stdout[:4000])
    if proc.stderr:
        print(f"=== STDERR ===")
        print(proc.stderr[:4000])
    if proc.returncode != 0:
        raise AssertionError(f"Node exit {proc.returncode}")
    return json.loads(proc.stdout.strip())


def test_diagnose_multitask_enforcement(tmp_path):
    """Diagnostic: dump all relevant state to find root cause."""
    ws = tmp_path / "diag"
    ws.mkdir()
    _make_working_workspace(ws)

    result = _run_diag(str(ws), env_override={"GLUDD_MSG_GAP_MS": "500"})
    
    print("\n=== DIAGNOSTIC RESULT ===")
    print(f"GLUDD_MULTITASK_FLOOR_ENFORCE = {result['env'].get('GLUDD_MULTITASK_FLOOR_ENFORCE')}")
    print(f"OPENCODE_SUBAGENT = {result['env'].get('OPENCODE_SUBAGENT')}")
    print(f"Disengage file: {result['stateFiles'].get('/tmp/gludd-watchdog-disengage.json')}")
    print(f"Subagent marker: {result['stateFiles'].get('/tmp/gludd-subagent-' + str(result['pid']) + '.json')}")
    print(f"hasPendingWork: {result['hasPendingWork']}")
    print(f"Plugin result: {result['pluginResult']}")
    
    # The plugin should return a deny block
    pr = result.get("pluginResult", {})
    assert pr.get("permissionDecision") == "deny", (
        f"Enforcement should deny write with pending work. "
        f"GLUDD_MULTITASK_FLOOR_ENFORCE={result['env'].get('GLUDD_MULTITASK_FLOOR_ENFORCE')}, "
        f"hasPendingWork={result['hasPendingWork']}, "
        f"result={pr}"
    )
