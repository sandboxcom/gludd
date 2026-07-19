#!/usr/bin/env python3
"""Check that every .opencode/plugin/enforce-*.ts hook handler has process.env.OPENCODE_SUBAGENT === "1" as its first substantive check.

Rules:
- tool.execute.before handlers: guard should be ``return`` (not ``return output``)
- text.complete handlers: guard should be ``return output``
"""

import re
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent / ".opencode" / "plugin"

TOOL_BEFORE_RE = re.compile(
    r'"tool\.execute\.before"\s*:\s*async\s*\(|api\.tool\.execute\.before\s*\('
)
TEXT_COMPLETE_RE = re.compile(
    r'"experimental\.text\.complete"\s*:\s*async\s*\(|api\.experimental\.text\.complete\s*\('
)
SYSTEM_TRANSFORM_RE = re.compile(
    r'"experimental\.(?:chat\.)?system\.transform"\s*:\s*async\s*\('
)

SUBAGENT_GUARD_RE = re.compile(
    r'process\.env\.OPENCODE_SUBAGENT\s*===?\s*"1"'
)

ISUBAGENT_CALL_RE = re.compile(r'\b(isSubagent|_isSubagent)\(\)')

BLANK_LINE_RE = re.compile(r"^\s*$")
COMMENT_LINE_RE = re.compile(r"^\s*(//|/\*|\*)\s")


def _find_all_handler_starts(lines, hook_re):
    """Find ALL handler registrations. Returns list of (decl_line_idx, brace_line_idx)."""
    candidates = []
    for i, line in enumerate(lines):
        if hook_re.search(line):
            brace_idx = i if "{" in line else None
            if brace_idx is None:
                for j in range(i + 1, min(i + 5, len(lines))):
                    if "{" in lines[j]:
                        brace_idx = j
                        break
            if brace_idx is not None:
                candidates.append((i, brace_idx))
    return candidates


def _extract_first_substantive_lines(lines, start_from, count=10):
    """Extract up to `count` substantive lines starting from `start_from`."""
    result = []
    for i in range(start_from, len(lines)):
        line = lines[i]
        if BLANK_LINE_RE.match(line):
            continue
        if COMMENT_LINE_RE.match(line):
            continue
        result.append(line)
        if len(result) >= count:
            break
    return result


def _check_guard_in_lines(substantive_lines):
    """Return True if the subagent guard pattern is found in the lines."""
    for line in substantive_lines:
        if SUBAGENT_GUARD_RE.search(line) or ISUBAGENT_CALL_RE.search(line):
            return True
    return False


def _check_single_handler(lines, brace_idx, hook_type):
    """Check one handler occurrence. Returns (has_guard, error_reason_or_none)."""
    substantive = _extract_first_substantive_lines(lines, brace_idx + 1)
    has_guard = _check_guard_in_lines(substantive)

    if not has_guard:
        return (False, "missing OPENCODE_SUBAGENT guard")

    if hook_type == "tool.execute.before":
        for line in substantive:
            if SUBAGENT_GUARD_RE.search(line) and "return output" in line:
                return (True, "guard uses 'return output' — should be 'return'")
        return (True, None)
    elif hook_type in ("text.complete", "system.transform"):
        for line in substantive:
            if SUBAGENT_GUARD_RE.search(line):
                stripped = line.strip()
                if "return output" not in stripped and "return" in stripped:
                    return (True, "guard uses bare 'return' — should be 'return output'")
                break
        return (True, None)

    return (has_guard, None if has_guard else "missing OPENCODE_SUBAGENT guard")


def _check_hook_type(lines, hook_re, hook_type):
    """Check all handlers of a given hook type. Returns finding or None.
    PASS if ANY handler has a valid guard; FAIL if none do; no finding if zero handlers.
    """
    all_starts = _find_all_handler_starts(lines, hook_re)
    if not all_starts:
        return None

    any_has_guard = False
    errors = []
    for _decl_idx, brace_idx in all_starts:
        has_guard, err = _check_single_handler(lines, brace_idx, hook_type)
        if has_guard and err is None:
            any_has_guard = True
        elif err:
            errors.append(err)

    if any_has_guard:
        return {
            "hook": hook_type,
            "status": "PASS",
            "reason": "guard present",
        }
    else:
        return {
            "hook": hook_type,
            "status": "FAIL",
            "reason": errors[0] if errors else "missing OPENCODE_SUBAGENT guard",
        }


def check_plugin(filepath):
    """Check a single plugin file. Returns list of findings."""
    findings = []
    content = filepath.read_text()
    lines = content.splitlines()

    tbe_finding = _check_hook_type(lines, TOOL_BEFORE_RE, "tool.execute.before")
    if tbe_finding is not None:
        tbe_finding["plugin"] = filepath.name
        findings.append(tbe_finding)

    tc_finding = _check_hook_type(lines, TEXT_COMPLETE_RE, "text.complete")
    if tc_finding is not None:
        tc_finding["plugin"] = filepath.name
        findings.append(tc_finding)

    st_finding = _check_hook_type(lines, SYSTEM_TRANSFORM_RE, "system.transform")
    if st_finding is not None:
        st_finding["plugin"] = filepath.name
        findings.append(st_finding)

    return findings


def main():
    plugin_files = sorted(PLUGIN_DIR.glob("enforce-*.ts"))
    if not plugin_files:
        print("No enforce-*.ts files found in", PLUGIN_DIR, file=sys.stderr)
        return 1

    all_findings = []
    for fp in plugin_files:
        findings = check_plugin(fp)
        all_findings.extend(findings)

    if not all_findings:
        print("No hook handlers found in any plugin — nothing to check")
        return 0

    total = len(all_findings)
    passed = 0
    for f in all_findings:
        print(
            "CHECK: {} — {} — {} ({})".format(
                f["plugin"], f["hook"], f["status"], f["reason"]
            )
        )
        if f["status"] == "PASS":
            passed += 1

    if passed == total:
        print("\n{}/{} plugins have subagent guards".format(passed, total))
    else:
        print("\n{}/{} plugins have subagent guards".format(passed, total))
        print("   {} plugin(s) missing or incorrect guard".format(total - passed))

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
