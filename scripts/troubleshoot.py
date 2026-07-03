#!/usr/bin/env python3
"""gludd self-diagnostic — identify common issues in ≤2 turns.

Used by agents and operators to surface known blockers before
spending turns on manual diagnosis. Fail-open: never crashes."""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

def check_session_md():
    """Surface CRITICAL banners from SESSION.md."""
    path = REPO_ROOT / "SESSION.md"
    if not path.exists():
        print("SESSION.md: MISSING")
        return
    content = path.read_text()
    for line in content.splitlines():
        if "CRITICAL:" in line:
            print(f"SESSION.md BANNER: {line.strip()}")

def check_opencode_permissions():
    """Check bash permission ordering in opencode.json."""
    path = REPO_ROOT / "opencode.json"
    if not path.exists():
        print("opencode.json: MISSING")
        return
    try:
        import json
        cfg = json.loads(path.read_text())
        bash_perm = cfg.get("permission", {}).get("bash")
        if isinstance(bash_perm, dict):
            keys = list(bash_perm.keys())
            # Last-matching-rule-wins: if "*" is last, it overrides everything
            if "*" in keys and keys[-1] == "*":
                if bash_perm["*"] == "deny":
                    print("opencode.json bash permissions: ⚠️ BROKEN ORDER")
                    print(f"  Keys: {keys}")
                    print("  `*: deny` is LAST — overrides all `make *: allow` rules")
                    print("  Fix: move `*: deny` FIRST, `make *: allow` SECOND")
            elif "*" in keys and keys[0] == "*":
                print("opencode.json bash permissions: ✅ correct order (catch-all first)")
            else:
                print(f"opencode.json bash permissions: keys={keys}")
        else:
            print(f"opencode.json bash permissions: {bash_perm}")
    except Exception as e:
        print(f"opencode.json: parse error — {e}")

def check_gate_status():
    """Read .gate-status and report state."""
    path = REPO_ROOT / ".gate-status"
    if not path.exists():
        print(".gate-status: MISSING (gate not run)")
        return
    content = path.read_text()
    lines = content.splitlines()
    failures = [l for l in lines if "FAIL" in l and not l.startswith("===")]
    passes = [l for l in lines if "PASS" in l and not l.startswith("===")]
    if failures:
        print(f".gate-status: RED — {len(failures)} FAIL lines")
    elif passes:
        print(f".gate-status: GREEN — {len(passes)} PASS lines")
    else:
        print(".gate-status: present but no PASS/FAIL lines found")

def check_ratchet():
    """Count ratchet entries."""
    path = REPO_ROOT / "config" / "ratchet.yml"
    if not path.exists():
        print("config/ratchet.yml: MISSING")
        return
    content = path.read_text()
    entries = [l for l in content.splitlines() if l.strip() and not l.strip().startswith("#") and ":" in l]
    # Skip the comment-only header lines
    real_entries = [e for e in entries if '"' in e or ":" not in e[:5]]
    if len(entries) <= 5:  # just header comments
        print(f"config/ratchet.yml: 0 entries (clean)")
    else:
        print(f"config/ratchet.yml: {len(entries) - 5} entries (known failures)")

def check_tasks_md():
    """Count unchecked boxes in TASKS.md."""
    path = REPO_ROOT / "TASKS.md"
    if not path.exists():
        print("TASKS.md: MISSING")
        return
    import re
    content = path.read_text()
    unchecked = len(re.findall(r'- \[ \]', content))
    checked = len(re.findall(r'- \[x\]', content))
    print(f"TASKS.md: {checked} checked, {unchecked} unchecked items")

def main():
    print("=== gludd self-diagnostic ===")
    print()
    check_session_md()
    print()
    check_opencode_permissions()
    print()
    check_gate_status()
    print()
    check_ratchet()
    print()
    check_tasks_md()
    print()
    print("=== diagnostic complete ===")

if __name__ == "__main__":
    main()
