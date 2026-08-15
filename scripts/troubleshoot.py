#!/usr/bin/env python3
"""gludd self-diagnostic — identify common issues in ≤2 turns.

Uses the EXACT same logic as enforce-floor.ts so the diagnostic
matches what the plugin actually sees. Fail-open: never crashes."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    from scripts import gludd_env_defaults as gludd_env_defaults
except ModuleNotFoundError:  # pragma: no cover - direct launch from scripts/
    import gludd_env_defaults

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# 1. Env vars — what the plugins actually see
# ---------------------------------------------------------------------------
_ENV_VARS = [
    "GLUDD_FLOOR_ENFORCE",
    "GLUDD_FORCE_DELEGATE",
    "GLUDD_NO_WAIT_ENFORCE",
    "GLUDD_TODO_GUARD_ENFORCE",
    "GLUDD_SESSION_START_ENFORCE",
    "GLUDD_TASK_DEADLINE_ENABLED",
    "CLAUDE_AGENT_FLOOR",
    "CLAUDE_AGENT_CEILING",
    "CLAUDE_AGENT_TARGET",
    "GLUDD_AUTH_PSK",
    "GLUDD_REQUIRE_AUTH",
    "GLUDD_INTEGRITY_KEY",
]

def check_env_vars():
    print("=== Env vars for floor/delegation enforcement ===")
    found = 0
    for var in _ENV_VARS:
        val = os.environ.get(var)
        if val is not None:
            print(f"  {var}={val}")
            found += 1
    if found == 0:
        print("  (none set — plugins use their internal defaults)")
    # Show effective defaults
    floor_enforce = os.environ.get("GLUDD_FLOOR_ENFORCE")
    force_delegate = os.environ.get("GLUDD_FORCE_DELEGATE")
    print(f"  → GLUDD_FLOOR_ENFORCE  default ON  ({'set to ' + floor_enforce if floor_enforce else 'unset → ON'})")
    print(f"  → GLUDD_FORCE_DELEGATE default ON  ({'set to ' + force_delegate if force_delegate else 'unset → ON'})")
    floor_val = os.environ.get("CLAUDE_AGENT_FLOOR", "10")
    print(f"  → CLAUDE_AGENT_FLOOR={floor_val}")

# ---------------------------------------------------------------------------
# 2. Plugin registration — what's wired in opencode.json
# ---------------------------------------------------------------------------
_ENFORCEMENT_PLUGINS = [
    "enforce-make.ts",
    "enforce-floor.ts",
    "enforce-delegate.ts",
    "enforce-stop.ts",
    "enforce-todos.ts",
    "enforce-session-start.ts",
    "enforce-deadline.ts",
    "enforce-deletion-gate.ts",
]

def check_plugin_registration():
    print("\n=== Plugin registration in opencode.json ===")
    path = REPO_ROOT / "opencode.json"
    if not path.exists():
        print("  opencode.json: MISSING")
        return
    cfg = json.loads(path.read_text())
    registered = cfg.get("plugin", [])
    registered_set = {Path(p).name for p in registered}
    for name in _ENFORCEMENT_PLUGINS:
        present = "✅" if name in registered_set else "❌ MISSING"
        print(f"  {present}  {name}")
    print(f"  Total plugins registered: {len(registered)}")

# ---------------------------------------------------------------------------
# 3. BUGS.md header regex (mirrors enforce-floor.ts exactly)
# ---------------------------------------------------------------------------
def check_bugs_md_regex():
    """Replicate the EXACT enforce-floor.ts BUGS.md scan."""
    print("\n=== BUGS.md open-incident scan (enforce-floor.ts regex) ===")
    path = REPO_ROOT / "BUGS.md"
    if not path.exists():
        print("  BUGS.md: MISSING")
        return
    content = path.read_text()
    headers = []
    all_header_count = 0
    for line in content.splitlines():
        # Match enforce-floor.ts regex: /^###\s+\d{4}-\d{2}-\d{2}\s+—/
        if re.match(r'^###\s+\d{4}-\d{2}-\d{2}\s+—', line):
            all_header_count += 1
            # Filter: skip lines with resolved/fixed/closed/wontfix/duplicate
            if not re.search(r'\b(resolved|fixed|closed|wontfix|duplicate)\b', line, re.I):
                headers.append(line[:80])
    print(f"  Headers matching /### DATE —/ : {all_header_count}")
    print(f"  AFTER excluding resolved/fixed/closed/wontfix/duplicate: {len(headers)}")
    if len(headers) > 0:
        print(f"  → openWorkExists() would return TRUE ({len(headers)} open incidents)")
    else:
        print(f"  → openWorkExists() would return FALSE (!!! floor breach gate is OPEN !!!)")

def check_bugs_md_separator():
    """Diagnose the separator character between date and description."""
    print("\n=== BUGS.md header separator analysis ===")
    path = REPO_ROOT / "BUGS.md"
    content = path.read_text()
    lines = content.splitlines()
    found = 0
    for line in lines:
        m = re.match(r'^###\s+(\d{4}-\d{2}-\d{2})(\s+)(\S)', line)
        if m and found < 3:
            found += 1
            sep = m.group(2)
            next_char = m.group(3)
            # Show hex of the separator
            sep_hex = sep.encode('utf-8').hex(' ')
            next_hex = next_char.encode('utf-8').hex(' ')
            ch_name = "EM-DASH" if next_char == '\u2014' else (
                "EN-DASH" if next_char == '\u2013' else (
                "HYPHEN" if next_char == '-' else f"U+{ord(next_char):04X}"))
            print(f"  Header '{m.group(1)}{sep}{next_char}...' → separator hex=[{sep_hex}], next=[{next_hex}] ({ch_name})")
            # Also test both regex variants
            with_hyphen = bool(re.match(r'^###\s+\d{4}-\d{2}-\d{2}\s+-', line))
            with_emdash = bool(re.match(r'^###\s+\d{4}-\d{2}-\d{2}\s+—', line))
            print(f"    Matches regex with HYPHEN: {with_hyphen}, with EM-DASH: {with_emdash}")
    if found == 0:
        print("  No BUGS.md headers found")

# ---------------------------------------------------------------------------
# 4. agent_liveness.py probe
# ---------------------------------------------------------------------------
def check_agent_probe():
    print("\n=== agent_liveness.py probe ===")
    probe = REPO_ROOT / "scripts" / "agent_liveness.py"
    if not probe.exists():
        print("  scripts/agent_liveness.py: MISSING (countActiveAgents() returns null → floorActive=0)")
        return
    try:
        result = subprocess.run(
            [sys.executable, str(probe), "--count"],
            capture_output=True, text=True, timeout=10,
            cwd=str(REPO_ROOT),
        )
        if result.returncode != 0:
            print(f"  Probe FAILED (exit {result.returncode}): {result.stderr.strip()[:200]}")
            print(f"  → countActiveAgents() returns null → floorActive=0")
            return
        out = result.stdout.strip()
        n = int(out) if out.isdigit() else None
        if n is not None:
            print(f"  Active agents: {n}")
            if n < 10:
                print(f"  → BELOW FLOOR (10): floor enforcement SHOULD be blocking")
            else:
                print(f"  → ABOVE FLOOR (10): floor enforcement allows inline work")
        else:
            print(f"  Probe returned non-numeric: '{out}'")
    except subprocess.TimeoutExpired:
        print("  Probe TIMED OUT → countActiveAgents() returns null → floorActive=0")
    except Exception as e:
        print(f"  Probe ERROR: {e}")

# ---------------------------------------------------------------------------
# 5. Open work signals
# ---------------------------------------------------------------------------
def check_open_work():
    print("\n=== Open-work signals (openWorkExists() in enforce-floor.ts) ===")
    signals = 0

    # ratchet.yml
    ratchet = REPO_ROOT / "config" / "ratchet.yml"
    ratchet_entries = 0
    if ratchet.exists():
        for line in ratchet.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and ":" in stripped:
                ratchet_entries += 1
    if ratchet_entries > 0:
        print(f"  ratchet.yml: {ratchet_entries} entries → SIGNAL")
        signals += 1
    else:
        print(f"  ratchet.yml: 0 entries")

    # backlog file
    backlog = REPO_ROOT / "scripts" / "multitasking_backlog.json"
    if backlog.exists():
        print(f"  multitasking_backlog.json: EXISTS → SIGNAL")
        signals += 1
    else:
        print(f"  multitasking_backlog.json: missing")

    # todowrite state
    todostate = Path(os.environ.get("GLUDD_TODOWRITE_STATE", gludd_env_defaults.TODOWRITE_STATE_DEFAULT))
    if todostate.exists():
        try:
            todos = json.loads(todostate.read_text())
            pending = [t for t in todos if t.get("status") in ("pending", "in_progress")]
            if pending:
                print(f"  todowrite state: {len(pending)} pending items → SIGNAL")
                signals += 1
            else:
                print(f"  todowrite state: 0 pending items")
        except Exception:
            print(f"  todowrite state: unreadable")
    else:
        print(f"  todowrite state: missing")

    # TASKS.md (SAME regex as enforce-floor.ts: ^\s*[-*]\s+\[\s*\])
    tasks = REPO_ROOT / "TASKS.md"
    tasks_unchecked = 0
    if tasks.exists():
        for line in tasks.read_text().splitlines():
            if re.match(r'^\s*[-*]\s+\[\s*\]', line):
                tasks_unchecked += 1
    if tasks_unchecked > 0:
        print(f"  TASKS.md: {tasks_unchecked} unchecked items → SIGNAL")
        signals += 1
    else:
        print(f"  TASKS.md: 0 unchecked items")

    # BUGS.md (matched above in check_bugs_md_regex)
    # git status
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
            cwd=str(REPO_ROOT),
        )
        dirty = result.stdout.strip()
        if dirty:
            line_count = len(dirty.splitlines())
            print(f"  git status: {line_count} dirty lines → SIGNAL")
            signals += 1
        else:
            print(f"  git status: clean")
    except Exception as e:
        print(f"  git status: error — {e}")

    print(f"\n  Total open-work signals: {signals}")
    if signals > 0:
        print(f"  → openWorkExists() would return TRUE")
    else:
        print(f"  → openWorkExists() would return FALSE (!!! floor breach gate is OPEN !!!)")
        print(f"  → This means after a clean commit with all tasks checked,")
        print(f"  → the floor enforcement silently disables — allowing inline grind.")

# ---------------------------------------------------------------------------
# 6. enforce-floor.ts source scan
# ---------------------------------------------------------------------------
def check_floor_plugin_source():
    print("\n=== enforce-floor.ts source integrity ===")
    path = REPO_ROOT / ".opencode" / "plugin" / "enforce-floor.ts"
    if not path.exists():
        print("  enforce-floor.ts: MISSING")
        return
    src = path.read_text()
    checks = {
        "FLOOR_ENFORCE default ON": 'GLUDD_FLOOR_ENFORCE !== "0"',
        "tool.execute.before hook": '"tool.execute.before"',
        "isDispatch exemptions": "function isDispatchTool",
        "BUGS.md open-incident scan": "openIncidents",
        "countActiveAgents shell-out": "agent_liveness.py",
        "openWorkExists TASKS.md scan": "unchecked",
    }
    for name, needle in checks.items():
        found = needle in src
        mark = "✅" if found else "❌"
        print(f"  {mark} {name}")


def main():
    print("=" * 50)
    print("  gludd self-diagnostic — enforce-floor + multitasking")
    print("=" * 50)

    check_env_vars()
    check_plugin_registration()
    check_bugs_md_separator()
    check_bugs_md_regex()
    check_agent_probe()
    check_open_work()
    check_floor_plugin_source()

    print("\n" + "=" * 50)
    print("  diagnostic complete")
    print("=" * 50)


if __name__ == "__main__":
    main()
