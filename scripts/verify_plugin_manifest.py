#!/usr/bin/env python3
"""Verify plugin manifest consistency: opencode.json ↔ disk ↔ guard coverage.

Cross-references the plugin list in opencode.json against actual .ts files in
.opencode/plugin/ and .opencode/plugins/, then checks that every plugin
registering tool.execute.before or experimental.text.complete hooks carries
the OPENCODE_SUBAGENT early-return guard.  The depth limiter is the single
scoped exception: it must enforce its dispatch-only boundary in subagents.

Exit 0 if all checks pass, exit 1 if any gap found.

Usage:
    scripts/verify_plugin_manifest.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import cast

WORKSPACE = Path(__file__).resolve().parent.parent
OPENCODE_JSON = WORKSPACE / "opencode.json"
SEARCH_DIRS = [
    WORKSPACE / ".opencode" / "plugin",
    WORKSPACE / ".opencode" / "plugins",
]

GUARD_BALLOT = "process.env.OPENCODE_SUBAGENT"
ISUBAGENT_CALL_RE = re.compile(r"\b_isSubagent\(\)")
ISUBAGENT_SHARED_CALL_RE = re.compile(r"\bisSubagent\(\)")
ISUBAGENT_FUNC_RE = re.compile(r"function\s+_isSubagent\s*\(\s*\)\s*:\s*\w+\s*\{")
ISUBAGENT_RECURSION_RE = re.compile(
    r"function\s+_isSubagent\s*\(.*?\)\s*:.*?\{[^}]*if\s*\(\s*_isSubagent\s*\(\s*\)\s*\)"
)
GUARD_SEARCH_WINDOW = 160  # lines before/after hook registration

HOOK_ALIAS: dict[str, str] = {
    "tool.execute.before": "tool.execute.before",
    "experimental.text.complete": "text.complete",
}

PLUGIN_FILE_RE = re.compile(r"\.opencode/(?:plugin|plugins)/[\w-]+\.ts")

# Non-plugin helper modules. They live in .opencode/lib/ since the E.5 refactor.
UTILITY_FILES = {
    ".opencode/lib/hot_reload.ts",
    ".opencode/lib/shared.ts",
}

DEPTH_PLUGIN_REL = ".opencode/plugin/enforce-depth.ts"
DEPTH_HOOK = "tool.execute.before"
DEPTH_CONTRACT_TOKENS = (
    "process.env.OPENCODE_DEPTH",
    "process.env.GLUDD_MAX_DEPTH",
    "isDispatchTool",
    "depth >= MAX_DEPTH",
    'permissionDecision: "deny"',
)


def _read_json(path: Path) -> dict[str, list[str]]:
    try:
        return cast(
            dict[str, list[str]], json.loads(path.read_text(encoding="utf-8"))
        )
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read {path}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def registered_plugins() -> set[str]:
    """Return normalized relative paths from opencode.json ``plugin`` array."""
    data = _read_json(OPENCODE_JSON)
    raw: list[str] = data.get("plugin", [])
    norm: set[str] = set()
    for p in raw:
        if p.startswith("./"):
            p = p[2:]
        norm.add(p)
    return norm


def disk_plugins() -> set[str]:
    """Return relative paths of all .ts files in .opencode/plugin/ and .opencode/plugins/."""
    found: set[str] = set()
    for d in SEARCH_DIRS:
        if d.is_dir():
            for f in d.glob("*.ts"):
                rel = str(f.relative_to(WORKSPACE))
                if rel not in UTILITY_FILES:
                    found.add(rel)
    return found


def _hook_types_in_file(filepath: Path) -> set[str]:
    """Inspect source for hook registrations. Returns {'tool.execute.before', 'text.complete'}."""
    hooks: set[str] = set()
    try:
        src = filepath.read_text(encoding="utf-8")
    except OSError:
        return hooks

    if re.search(r'"tool\.execute\.before"', src):
        hooks.add("tool.execute.before")
    if re.search(r"api\.tool\.execute\.before\s*\(", src):
        hooks.add("tool.execute.before")
    if re.search(r'"experimental\.text\.complete"', src):
        hooks.add("experimental.text.complete")
    return hooks


def _guard_present(filepath: Path, hook_key: str) -> bool | None:
    """Check subagent guard presence within GUARD_SEARCH_WINDOW of hook_key.

    Strategy (two detection modes):
    1. Direct mode — ``process.env.OPENCODE_SUBAGENT`` within the window.
    2. Indirect mode — ``_isSubagent()`` call near the hook; then verify the
       ``_isSubagent`` function body contains the env-var check AND does NOT
       contain the recursion bug (``if (_isSubagent())`` self-call).

    Returns True if at least one hook registration of this type has a valid
    guard nearby, False if all occurrences lack it, None if the hook type is
    not registered in this file.
    """
    try:
        src = filepath.read_text(encoding="utf-8")
    except OSError:
        return None
    lines = src.split("\n")

    patterns: list[re.Pattern[str]] = []
    if hook_key == "tool.execute.before":
        patterns = [
            re.compile(r'"tool\.execute\.before"'),
            re.compile(r"api\.tool\.execute\.before"),
        ]
    elif hook_key == "experimental.text.complete":
        patterns = [re.compile(r'"experimental\.text\.complete"')]
    else:
        return None

    match_positions: list[int] = []
    for pat in patterns:
        for m in pat.finditer(src):
            match_positions.append(m.start())

    if not match_positions:
        return None

    for pos in match_positions:
        hook_lineno = src[:pos].count("\n")
        start = max(0, hook_lineno - GUARD_SEARCH_WINDOW)
        end = min(len(lines), hook_lineno + GUARD_SEARCH_WINDOW)
        window = "\n".join(lines[start:end])

        # Mode 1 — direct env-var check in window
        if GUARD_BALLOT in window:
            return True

        # Mode 2 — _isSubagent() called in window; verify function definition
        if ISUBAGENT_CALL_RE.search(window) and _is_subagent_func_valid(src):
            return True

        # Mode 3 — isSubagent() imported from shared.ts; verify shared.ts has guard
        if ISUBAGENT_SHARED_CALL_RE.search(window):
            shared_path = WORKSPACE / ".opencode" / "lib" / "shared.ts"
            try:
                shared_src = shared_path.read_text(encoding="utf-8")
                if GUARD_BALLOT in shared_src:
                    return True
            except OSError:
                pass

    return False


def _is_subagent_func_valid(src: str) -> bool:
    """Return True if ``_isSubagent`` is defined AND correct (has env-var
    check, no self-call recursion), False otherwise."""
    if not ISUBAGENT_FUNC_RE.search(src):
        return False
    # Must have the env-var check
    if GUARD_BALLOT not in src:
        return False
    # Must NOT have the recursion self-call
    return not ISUBAGENT_RECURSION_RE.search(src)


def _is_subagent_recursion_bug(filepath: Path) -> bool:
    """Return True if this file has a _isSubagent() that calls itself."""
    try:
        src = filepath.read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(ISUBAGENT_RECURSION_RE.search(src))


def _depth_boundary_contract_present(filepath: Path) -> bool:
    """Validate the only plugin allowed to enforce inside subagents.

    The exemption is deliberately mechanical and narrow: the plugin must be
    driven by OpenCode's depth, affect dispatch tools only, and deny at the
    configured boundary.  Reintroducing a generic subagent bypass invalidates
    the exemption.
    """
    try:
        src = filepath.read_text(encoding="utf-8")
    except OSError:
        return False
    has_bypass = (
        "OPENCODE_SUBAGENT" in src
        or ISUBAGENT_CALL_RE.search(src) is not None
        or ISUBAGENT_SHARED_CALL_RE.search(src) is not None
    )
    return not has_bypass and all(token in src for token in DEPTH_CONTRACT_TOKENS)


def _subagent_safety_present(plugin_rel: str, filepath: Path, hook: str) -> bool | None:
    """Return whether a hook has isolation or the exact depth-boundary contract."""
    guarded = _guard_present(filepath, hook)
    if guarded is not False:
        return guarded
    if plugin_rel == DEPTH_PLUGIN_REL and hook == DEPTH_HOOK:
        return _depth_boundary_contract_present(filepath)
    return False


def run() -> tuple[int, list[str]]:
    registered = registered_plugins()
    disk = disk_plugins()
    issues: list[str] = []

    # --- Cross-reference: registered must exist, disk must be registered ---
    missing_from_disk = registered - disk
    unregistered_on_disk = disk - registered

    for p in sorted(missing_from_disk):
        issues.append(f"MISSING-FILE: {p} (in opencode.json, not on disk)")
    for p in sorted(unregistered_on_disk):
        issues.append(f"UNREGISTERED: {p} (on disk, not in opencode.json)")

    # --- Guard check for plugins with hooks ---
    for plugin_rel in sorted(disk):
        fpath = WORKSPACE / plugin_rel
        if not fpath.is_file():
            continue
        hooks = _hook_types_in_file(fpath)
        for hook in sorted(hooks):
            label = HOOK_ALIAS.get(hook, hook)
            present = _subagent_safety_present(plugin_rel, fpath, hook)
            if present is None:
                continue
            if not present:
                issues.append(
                    f"MISSING-GUARD: {plugin_rel} registers {label} but has "
                    "neither an OPENCODE_SUBAGENT guard nor the valid depth-boundary exemption"
                )

        # --- Recurrence bug: _isSubagent() calls itself ---
        if _is_subagent_recursion_bug(fpath):
            issues.append(
                f"RECURSION-BUG: {plugin_rel} _isSubagent() calls "
                f"_isSubagent() — replace with "
                f'process.env.OPENCODE_SUBAGENT === "1"'
            )

    # --- Print table ---
    print(f"{'FILE':<45} {'CHECK':<45} {'STATUS'}")
    print("-" * 100)

    # Gather rows
    rows: list[tuple[str, str, str]] = []
    for plugin_rel in sorted(disk):
        fname = plugin_rel.split("/")[-1]
        reg_status = "OK" if plugin_rel in registered else "MISSING"
        rows.append((fname, "REGISTERED in opencode.json", reg_status))

        disk_exists = "OK" if (WORKSPACE / plugin_rel).is_file() else "MISSING"
        if reg_status == "OK":
            rows.append((fname, "FILE exists on disk", disk_exists))

        hooks = _hook_types_in_file(WORKSPACE / plugin_rel)
        for hook in sorted(hooks):
            label = HOOK_ALIAS.get(hook, hook)
            present = _subagent_safety_present(
                plugin_rel, WORKSPACE / plugin_rel, hook
            )
            if present is None:
                continue
            status = "OK" if present else "MISSING"
            rows.append((fname, f"subagent safety in {label}", status))

        # Check _isSubagent function correctness (if defined)
        try:
            src = (WORKSPACE / plugin_rel).read_text(encoding="utf-8")
        except OSError:
            src = ""
        if ISUBAGENT_FUNC_RE.search(src):
            bug = _is_subagent_recursion_bug(WORKSPACE / plugin_rel)
            rows.append((fname, "_isSubagent() function (no self-call)", "OK" if not bug else "RECURSION"))

    for fname, check, status in rows:
        marker = "OK" if status == "OK" else "FAIL"
        print(f"{fname:<45} {check:<45} {marker}")

    print("-" * 100)

    total = len(rows)
    failed = sum(1 for _, _, s in rows if s != "OK")
    passed = total - failed

    print(f"\n  PASSED: {passed}  FAILED: {failed}  TOTAL: {total}")

    if issues:
        print(f"\n  {len(issues)} GAP(S) FOUND:")
        for issue in issues:
            print(f"    • {issue}")

    return (1 if issues else 0), issues


def main() -> int:
    exit_code, _ = run()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
