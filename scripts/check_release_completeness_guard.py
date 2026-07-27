#!/usr/bin/env python3
"""check_release_completeness_guard.py — AC001: artifact-verification-gate.

Ensures release-cut only proceeds when verify-release-completeness passes.
Distinguishes between "CI still running" and "CI finished, artifacts missing".
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

STATE_FILE = "/tmp/gludd-release-completeness-check.json"


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_check_epoch": 0, "last_tag": "", "last_verdict": "UNKNOWN"}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def check_completeness(tag, force=False):
    state = load_state()
    cooldown_sec = int(os.environ.get("GLUDD_RELEASE_CHECK_COOLDOWN_SEC", "600"))

    if not force:
        elapsed = datetime.now().timestamp() - state["last_check_epoch"]
        if elapsed < cooldown_sec and state["last_tag"] == tag:
            print(f"AC001: COOLDOWN active ({int(cooldown_sec - elapsed)}s remaining). Use FORCE=1.")
            print(f"AC001: Last verdict for {tag}: {state['last_verdict']}")
            sys.exit(3)

    try:
        result = subprocess.run(
            ["make", "verify-release-completeness", f"TAG={tag}"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        print("AC001: verify-release-completeness timed out after 120s")
        state["last_verdict"] = "TIMEOUT"
        state["last_check_epoch"] = datetime.now().timestamp()
        state["last_tag"] = tag
        save_state(state)
        sys.exit(2)

    state["last_check_epoch"] = datetime.now().timestamp()
    state["last_tag"] = tag

    if result.returncode == 0:
        state["last_verdict"] = "PASS"
        save_state(state)
        print(f"AC001: PASS — release completeness verified for {tag}")
        sys.exit(0)

    stdout = result.stdout
    if "CI still running" in stdout or "in_progress" in stdout:
        state["last_verdict"] = "CI_PENDING"
        save_state(state)
        print(f"AC001: CI_PENDING — CI still running for {tag}, retry after CI completes")
        sys.exit(2)

    state["last_verdict"] = "FAIL"
    save_state(state)
    print(f"AC001: FAIL — release incomplete for {tag}")
    print(stdout[-500:] if stdout else "No output from verify-release-completeness")
    sys.exit(1)


if __name__ == "__main__":
    tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TAG", "")
    force = os.environ.get("FORCE") == "1"
    if not tag:
        print("AC001: TAG required")
        sys.exit(2)
    check_completeness(tag, force)
