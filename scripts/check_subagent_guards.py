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

SUBAGENT_GUARD_RE = re.compile(
    r'process\.env\.OPENCODE_SUBAGENT\s*===?\s*"1"'
)

BLANK_LINE_RE = re.compile(r"^\s*$")
COMMENT_LINE_RE = re.compile(r"^\s*(//|/\*|\*)\s")


def _find_handler_start(lines, hook_re):
    """Find a handler registration. Returns (decl_line_idx, brace_line_idx) or None."""
    for i, line in enumerate(lines):
        if hook_re.search(line):
            if "{" in line:
                return (i, i)
            for j in range(i + 1, min(i + 5, len(lines))):
                if "{" in lines[j]:
                    return (i, j)
    return None


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
        if SUBAGENT_GUARD_RE.search(line):
            return True
    return False


def check_plugin(filepath):
    """Check a single plugin file. Returns list of findings."""
    findings = []
    content = filepath.read_text()
    lines = content.splitlines()

    tbe = _find_handler_start(lines, TOOL_BEFORE_RE)
    if tbe is not None:
        decl_idx, brace_idx = tbe
        substantive = _extract_first_substantive_lines(lines, brace_idx + 1)
        has_guard = _check_guard_in_lines(substantive)
        guard_uses_return_output = False
        for line in substantive:
            if SUBAGENT_GUARD_RE.search(line) and "return output" in line:
                guard_uses_return_output = True
                break

        status = "PASS"
        reason = ""
        if not has_guard:
            status = "FAIL"
            reason = "missing OPENCODE_SUBAGENT guard"
        elif guard_uses_return_output:
            status = "FAIL"
            reason = "guard uses 'return output' — should be 'return'"
        else:
            reason = "guard present"

        findings.append({
            "plugin": filepath.name,
            "hook": "tool.execute.before",
            "status": status,
            "reason": reason,
        })

    tc = _find_handler_start(lines, TEXT_COMPLETE_RE)
    if tc is not None:
        decl_idx, brace_idx = tc
        substantive = _extract_first_substantive_lines(lines, brace_idx + 1)
        has_guard = _check_guard_in_lines(substantive)
        guard_uses_bare_return = False
        for line in substantive:
            if SUBAGENT_GUARD_RE.search(line):
                stripped = line.strip()
                if "return output" not in stripped and "return" in stripped:
                    guard_uses_bare_return = True
                break

        status = "PASS"
        reason = ""
        if not has_guard:
            status = "FAIL"
            reason = "missing OPENCODE_SUBAGENT guard"
        elif guard_uses_bare_return:
            status = "FAIL"
            reason = "guard uses bare 'return' — should be 'return output'"
        else:
            reason = "guard present"

        findings.append({
            "plugin": filepath.name,
            "hook": "text.complete",
            "status": status,
            "reason": reason,
        })

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
