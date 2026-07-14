"""Verify enforcement plugins are actually blocking at runtime.

Checks state files, env vars, and runtime hook test.
Exits 0 if all enforcement is active, 1 if any gaps found.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# ── State files that MUST exist ──────────────────────────────────────────────
REQUIRED_STATE_FILES: dict[str, str] = {
    "/tmp/gludd-tool-streak.json": "enforce-floor / enforce-stop streak counter",
    "/tmp/gludd-plugin-alive.json": "shared plugin alive heartbeat",
    "/tmp/gludd-multitask-state.json": "enforce-multitask state",
    "/tmp/gludd-session-start.json": "enforce-session-start state",
    "/tmp/gludd-stop-state.json": "enforce-stop state",
}

# ── State files that must NOT exist ──────────────────────────────────────────
FORBIDDEN_STATE_FILES: dict[str, str] = {
    "/tmp/gludd-watchdog-disengage.json": "watchdog disengage — enforcement suspended",
}

# ── Env vars that must NOT be "0" ────────────────────────────────────────────
REQUIRED_ENV_VARS: dict[str, str] = {
    "GLUDD_FLOOR_ENFORCE": "enforce-floor plugin blocking",
    "GLUDD_MULTITASK_FLOOR_ENFORCE": "enforce-multitask plugin blocking",
    "GLUDD_TASK_DEADLINE_BLOCK": "enforce-deadline plugin blocking",
    "GLUDD_ENHANCEMENT_RATIO_BLOCK": "enforce-enhancement-ratio plugin blocking",
    "GLUDD_STOP_ENFORCE": "enforce-stop plugin blocking",
    "GLUDD_CLEAN_TREE_ENFORCE": "enforce-clean-tree plugin blocking",
    "GLUDD_VERIFIED_CLAIMS_ENFORCE": "enforce-verified-claims plugin blocking",
    "GLUDD_MAINTHREAD_STREAK_ENFORCE": "enforce-delegate mainthread-streak blocking",
    "GLUDD_NO_WAIT_ENFORCE": "enforce-no-wait plugin blocking",
}

# ── Additional advisory env vars (warn if explicitly 0 but don't fail) ───────
ADVISORY_ENV_VARS: dict[str, str] = {
    "GLUDD_FORCE_DELEGATE": "enforce-delegate grind guard (opt-in)",
    "GLUDD_SESSION_START_ENFORCE": "enforce-session-start plugin blocking",
    "GLUDD_TODO_GUARD_ENFORCE": "enforce-stop commit-block guard",
}


def main() -> int:
    gaps: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []

    print("=== Enforcement Verification ===")
    print()

    # ── State files ──────────────────────────────────────────────────────
    print("--- State Files ---")
    for path, desc in REQUIRED_STATE_FILES.items():
        p = Path(path)
        if p.exists():
            try:
                data = json.loads(p.read_text())
                if data is None or (isinstance(data, dict) and len(data) == 0):
                    warnings.append(f"{path}: exists but empty — {desc}")
                    print(f"  [WARN] {path}: exists but empty ({desc})")
                else:
                    ok.append(f"{path}: {desc}")
                    print(f"  [OK]   {path}: {desc}")
            except (json.JSONDecodeError, OSError):
                gaps.append(f"{path}: unreadable — {desc}")
                print(f"  [FAIL] {path}: unreadable ({desc})")
        else:
            gaps.append(f"{path}: missing — {desc}")
            print(f"  [FAIL] {path}: missing ({desc})")

    for path, desc in FORBIDDEN_STATE_FILES.items():
        p = Path(path)
        if p.exists():
            try:
                data = json.loads(p.read_text())
                active = isinstance(data, dict) and data.get("disengage") is True
                if active:
                    gaps.append(f"{path}: ACTIVE — enforcement is disengaged")
                    print(f"  [FAIL] {path}: ACTIVE ({desc})")
                else:
                    print(f"  [OK]   {path}: exists but not active ({desc})")
            except (json.JSONDecodeError, OSError):
                gaps.append(f"{path}: exists but unreadable — {desc}")
                print(f"  [FAIL] {path}: unreadable ({desc})")
        else:
            ok.append(f"{path}: absent (good)")
            print(f"  [OK]   {path}: absent ({desc})")

    # ── Env vars ─────────────────────────────────────────────────────────
    print()
    print("--- Environment Variables ---")
    for var, desc in REQUIRED_ENV_VARS.items():
        val = os.environ.get(var)
        if val is None:
            ok.append(f"{var}: unset (defaults blocking) — {desc}")
            print(f"  [OK]   {var}: unset (defaults blocking) ({desc})")
        elif val == "0":
            gaps.append(f"{var}=0 — {desc} DISABLED")
            print(f"  [FAIL] {var}=0 — {desc}")
        else:
            ok.append(f"{var}={val} — {desc}")
            print(f"  [OK]   {var}={val} ({desc})")

    for var, desc in ADVISORY_ENV_VARS.items():
        val = os.environ.get(var)
        if val == "0":
            warnings.append(f"{var}=0 — {desc} explicitly disabled")
            print(f"  [WARN] {var}=0 — {desc}")
        elif val is not None:
            ok.append(f"{var}={val} — {desc}")
            print(f"  [OK]   {var}={val} ({desc})")
        else:
            print(f"  [--]   {var}: unset ({desc})")

    # ── Runtime hook test ────────────────────────────────────────────────
    print()
    print("--- Runtime Hook Test ---")
    try:
        result = subprocess.run(
            ["make", "test-hook-runtime"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            ok.append("test-hook-runtime: PASSED")
            print("  [OK]   test-hook-runtime: PASSED")
            # Print last few lines for evidence
            lines = result.stdout.strip().split("\n")
            for line in lines[-3:]:
                if line.strip():
                    print(f"         {line.strip()[:120]}")
        else:
            gaps.append(f"test-hook-runtime: FAILED (exit {result.returncode})")
            print(f"  [FAIL] test-hook-runtime: FAILED (exit {result.returncode})")
            stderr = result.stderr.strip()
            if stderr:
                for line in stderr.split("\n")[-5:]:
                    print(f"         {line.strip()[:120]}")
    except FileNotFoundError:
        gaps.append("test-hook-runtime: make command not found")
        print("  [FAIL] test-hook-runtime: make command not found")
    except subprocess.TimeoutExpired:
        gaps.append("test-hook-runtime: timed out after 60s")
        print("  [FAIL] test-hook-runtime: timed out after 60s")

    # ── Summary ──────────────────────────────────────────────────────────
    print()
    print("=== Summary ===")
    print(f"  Blocking OK:    {len(ok)}")
    print(f"  Gaps/FAIL:      {len(gaps)}")
    print(f"  Warnings:       {len(warnings)}")

    if gaps:
        print()
        print("ENFORCEMENT GAPS FOUND:")
        for g in gaps:
            print(f"  - {g}")

    if warnings:
        print()
        print("WARNINGS:")
        for w in warnings:
            print(f"  - {w}")

    if not gaps:
        print()
        print("VERDICT: All enforcement active and blocking.")
        return 0
    else:
        print()
        print("VERDICT: Enforcement gaps detected — plugins may not be blocking at runtime.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
