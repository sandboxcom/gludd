"""Comprehensive unit tests for enforcement plugin subagent-context isolation.

Every enforcement plugin that runs in subagent context (OPENCODE_SUBAGENT=1) MUST
return early before executing any enforcement logic. Without this guard,
subagent tool calls (edit, write, bash during agent work) are incorrectly
denied by main-thread enforcement hooks, causing intermittent subagent failures.

This test reads the TypeScript plugin source directly (no JS runtime needed),
extracts hook handlers, and verifies the OPENCODE_SUBAGENT guard is present
at the correct position in every handler.

Plugins that do NOT run in subagent context (watchdog.ts — daemon lifecycle
only) are explicitly exempted from the guard requirement.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
PLUGINS_DIR = ROOT / ".opencode" / "plugins"


def _enforce_plugins() -> list[Path]:
    return sorted(PLUGIN_DIR.glob("enforce-*.ts"))


def _read_plugin(name: str) -> str:
    """Read plugin source, including impl file if the plugin delegates to one."""
    path = PLUGIN_DIR / f"{name}.ts"
    assert path.exists(), f"Plugin not found: {path}"
    src = path.read_text()
    impl_path = PLUGIN_DIR / "impl" / f"{name.replace('-', '_')}_impl.ts"
    if impl_path.exists():
        src += "\n" + impl_path.read_text()
    return src


def _find_first_substantive_line(body: str) -> int:
    """Line index of first non-blank, non-comment, non-opening-brace line."""
    for i, line in enumerate(body.split("\n")):
        s = line.strip()
        if not s:
            continue
        if s.startswith("//") or s.startswith("/*") or s.startswith("*"):
            continue
        if s in ("{", "};"):
            continue
        return i
    return -1


def _find_guard_idx(body: str) -> int | None:
    """Return line index (0-based) of OPENCODE_SUBAGENT guard in body, or None."""
    for i, line in enumerate(body.split("\n")):
        stripped = line.strip()
        direct_guard = (
            stripped.startswith("if (process.env.OPENCODE_SUBAGENT === ")
            and "return" in stripped
        )
        shared_guard = bool(
            re.match(r"if\s*\(\s*isSubagent\(\)\s*\)\s*return\b", stripped)
        )
        if direct_guard or shared_guard:
            return i
    return None


# =============================================================================
# Body extraction — handles BOTH plugin API styles
# =============================================================================
#
# Style A (object-literal; 9 plugins: floor, make, delegate, multitask, stop,
#   deadline, session-start, deletion-gate, no-suppressions):
#   "tool.execute.before": async (input, output) => { ... }
#
# Style B (functional API; 3 plugins: no-wait, commit-lock, clean-tree):
#   api.tool.execute.before((params) => { ... });
#
# Style C (functional API via default export; enforce-verified-claims):
#   "experimental.text.complete": async (...) => { ... }
#   (object-literal, same as Style A)


def _extract_handler_style_a(src: str, hook_name: str) -> tuple[int, str] | None:
    """Extract handler body for object-literal style hooks.

    Pattern:  "hook.name": async (input, output) => { <body> }
    Returns (line_number_in_src, body_text) or None.

    IMPORTANT: parameter type annotations may contain nested braces
    (e.g. `input: { tool?: string }`). We find `=>` first, then the
    first `{` after `=>` — not just the first `{` in the snippet.
    """
    escaped = re.escape(hook_name)
    pattern = rf'"{escaped}"\s*:'
    result: tuple[int, str] | None = None
    for m in re.finditer(pattern, src):
        pos = m.end()
        after_key = src[pos:]
        arrow_idx = after_key.find("=>")
        if arrow_idx == -1:
            continue
        after_arrow = after_key[arrow_idx + 2:]
        brace_idx = after_arrow.find("{")
        if brace_idx == -1:
            continue
        content_after_brace = after_arrow[brace_idx + 1:]
        depth = 1
        end = 0
        for i, ch in enumerate(content_after_brace):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if depth == 0:
            body = content_after_brace[:end].strip()
            line_no = src[: m.start()].count("\n")
            result = (line_no, body)
    return result


def _extract_handler_style_b(src: str, hook_name: str) -> tuple[int, str] | None:
    """Extract handler body for functional API style hooks.

    Pattern:  api.tool.execute.before((params) => { <body> });
    hook_name is the dot-separated method path, e.g. 'tool.execute.before'.
    Returns (line_number_in_src, body_text) or None.
    """
    escaped = re.escape(hook_name)
    pattern = rf'api\.{escaped}\s*\(?\('
    result: tuple[int, str] | None = None
    for m in re.finditer(pattern, src):
        pos = m.end()
        after_match = src[pos:]
        brace_idx = after_match.find("{")
        if brace_idx == -1:
            continue
        after_brace = after_match[brace_idx + 1:]
        depth = 1
        end = 0
        for i, ch in enumerate(after_brace):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if depth == 0:
            body = after_brace[:end].strip()
            line_no = src[: m.start()].count("\n")
            result = (line_no, body)
    return result


def _extract_named_handler(src: str, hook_name: str) -> tuple[int, str] | None:
    """Resolve an object hook whose value is a named function."""
    escaped = re.escape(hook_name)
    references = list(
        re.finditer(
            rf'"{escaped}"\s*:\s*([A-Za-z_$][\w$]*)\s*(?:,|\}})',
            src,
        )
    )
    for reference in reversed(references):
        function_name = reference.group(1)
        function_match = re.search(
            rf"(?:async\s+)?function\s+{re.escape(function_name)}\b[^\n]*\{{\s*$",
            src,
            re.MULTILINE,
        )
        if function_match is None:
            continue
        content_after_brace = src[function_match.end():]
        depth = 1
        for i, char in enumerate(content_after_brace):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    line_no = src[: function_match.start()].count("\n")
                    return line_no, content_after_brace[:i].strip()
    return None


def _extract_handler_body(src: str, hook_name: str) -> tuple[int, str] | None:
    """Extract handler body using both API styles. Returns (line_no, body) or None."""
    named = _extract_named_handler(src, hook_name)
    if named is not None:
        return named
    result = _extract_handler_style_a(src, hook_name)
    if result is not None:
        return result
    return _extract_handler_style_b(src, hook_name)


# =============================================================================
# Which plugins have which hooks
# =============================================================================
# Discover the complete current inventory so newly added plugins cannot escape
# isolation checks merely because a hand-maintained list was not updated.
PLUGINS_WITH_TOOL_BEFORE = sorted(
    plugin.stem
    for plugin in _enforce_plugins()
    if '"tool.execute.before"' in _read_plugin(plugin.stem)
)

PLUGINS_WITH_TEXT_COMPLETE = sorted(
    plugin.stem
    for plugin in _enforce_plugins()
    if '"experimental.text.complete"' in _read_plugin(plugin.stem)
)


class TestStructuralGuardPresence:
    """Every plugin with tool.execute.before MUST have the OPENCODE_SUBAGENT
    guard as the FIRST substantive line of the handler body.
    """

    def test_all_tool_before_plugins_have_guard(self):
        missing = []
        misplaced = []
        for name in PLUGINS_WITH_TOOL_BEFORE:
            src = _read_plugin(name)
            result = _extract_handler_body(src, "tool.execute.before")
            assert result is not None, (
                f"{name}.ts: tool.execute.before hook not found by any extraction "
                f"method. The plugin may use an unrecognized API style."
            )
            line_no, body = result
            idx = _find_guard_idx(body)
            first = _find_first_substantive_line(body)
            if idx is None:
                missing.append(
                    f"{name}.ts @ src-line ~{line_no} — "
                    f"no OPENCODE_SUBAGENT guard in handler body"
                )
            elif idx != first:
                actual_line = body.split("\n")[idx].strip()
                body.split("\n")[first].strip() if first >= 0 else "(none)"
                misplaced.append(
                    f"{name}.ts @ src-line ~{line_no} — "
                    f"guard at body-line {idx} but first substantive line is {first}. "
                    f"Guard: {actual_line!r}"
                )
        failures = []
        if missing:
            failures.append(
                "PLUGINS MISSING OPENCODE_SUBAGENT GUARD (tool.execute.before):\n"
                + "\n".join(f"  - {m}" for m in missing)
            )
        if misplaced:
            failures.append(
                "GUARD NOT FIRST SUBSTANTIVE LINE:\n"
                + "\n".join(f"  - {m}" for m in misplaced)
            )
        assert not failures, "\n\n".join(failures)

    def test_guard_is_early_return_not_throw(self):
        violations = []
        for name in PLUGINS_WITH_TOOL_BEFORE:
            src = _read_plugin(name)
            result = _extract_handler_body(src, "tool.execute.before")
            assert result is not None, f"{name}.ts: no handler found"
            _line_no, body = result
            idx = _find_guard_idx(body)
            if idx is None:
                continue  # reported by other test
            guard = body.split("\n")[idx].strip()
            if "throw" in guard:
                violations.append(
                    f"{name}.ts: guard throws: {guard!r}"
                )
            if "permissionDecision" in guard and "deny" in guard:
                violations.append(
                    f"{name}.ts: guard returns deny: {guard!r}"
                )
        assert not violations, (
            "Guards must return/return-output (not throw, not deny):\n"
            + "\n".join(f"  - {v}" for v in violations)
        )


class TestGuardIsFirstCheck:
    """OPENCODE_SUBAGENT guard must run BEFORE any side-effectful logic:
    before _reportAlive(), before try{, before GLUDD_*_ENFORCE checks.
    """

    def test_guard_before_report_alive(self):
        violations = []
        for name in PLUGINS_WITH_TOOL_BEFORE:
            src = _read_plugin(name)
            result = _extract_handler_body(src, "tool.execute.before")
            if result is None:
                violations.append(f"{name}.ts: handler not found")
                continue
            _line_no, body = result
            guard_idx = _find_guard_idx(body)
            if guard_idx is None:
                continue  # tested elsewhere
            body_lines = body.split("\n")
            for i, line in enumerate(body_lines):
                if "_reportAlive()" in line:
                    if i < guard_idx:
                        violations.append(
                            f"{name}.ts: _reportAlive() at body-line {i} "
                            f"precedes guard at body-line {guard_idx}"
                        )
                    break
        assert not violations, (
            "Guard must run before _reportAlive():\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    def test_guard_before_try(self):
        violations = []
        for name in PLUGINS_WITH_TOOL_BEFORE:
            src = _read_plugin(name)
            result = _extract_handler_body(src, "tool.execute.before")
            if result is None:
                continue
            _line_no, body = result
            guard_idx = _find_guard_idx(body)
            if guard_idx is None:
                continue
            body_lines = body.split("\n")
            for i, line in enumerate(body_lines):
                stripped = line.strip()
                if stripped == "try {" or stripped.startswith("try {"):
                    if i < guard_idx:
                        violations.append(
                            f"{name}.ts: try{{ at body-line {i} "
                            f"precedes guard at body-line {guard_idx}"
                        )
                    break
        assert not violations, (
            "Guard must run before try{:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )


class TestGuardExactPatterns:
    """Guard must use the exact env check or the shared isSubagent() helper."""

    def test_exact_pattern(self):
        violations = []
        for name in PLUGINS_WITH_TOOL_BEFORE:
            src = _read_plugin(name)
            result = _extract_handler_body(src, "tool.execute.before")
            if result is None:
                continue
            _line_no, body = result
            idx = _find_guard_idx(body)
            if idx is None:
                continue
            guard = body.split("\n")[idx].strip()
            if re.match(
                r"if\s*\(\s*isSubagent\(\)\s*\)\s*return\b",
                guard,
            ):
                continue
            must_have = 'process.env.OPENCODE_SUBAGENT === "1"'
            if must_have not in guard:
                violations.append(
                    f"{name}.ts: wrong pattern — {guard!r}"
                )
            if "==" in guard and "===" not in guard:
                violations.append(
                    f"{name}.ts: uses == instead of === — {guard!r}"
                )
        assert not violations, (
            "Guard must use the exact env check or `if (isSubagent()) return`:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    def test_no_substring_or_alternate_var(self):
        violations = []
        for name in PLUGINS_WITH_TOOL_BEFORE:
            src = _read_plugin(name)
            result = _extract_handler_body(src, "tool.execute.before")
            if result is None:
                continue
            _line_no, body = result
            idx = _find_guard_idx(body)
            if idx is None:
                continue
            guard = body.split("\n")[idx].strip()
            if re.match(
                r"if\s*\(\s*isSubagent\(\)\s*\)\s*return\b",
                guard,
            ):
                continue
            if "OPENCODE_SUBAGENT" not in guard:
                violations.append(
                    f"{name}.ts: does not reference OPENCODE_SUBAGENT: {guard!r}"
                )
            match = re.search(r"process\.env\.([A-Z_]+)", guard)
            if match:
                var_name = match.group(1)
                if var_name != "OPENCODE_SUBAGENT":
                    violations.append(
                        f"{name}.ts: wrong env var '{var_name}' instead of "
                        f"OPENCODE_SUBAGENT: {guard!r}"
                    )
            if "!==" in guard and "===" not in guard:
                violations.append(
                    f"{name}.ts: uses !== instead of ===: {guard!r}"
                )
        assert not violations, (
            "Guard must use the shared helper or exact OPENCODE_SUBAGENT check:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )


class TestNoSubagentBlocksEdits:
    """When OPENCODE_SUBAGENT=1, guard must RETURN (not throw, not deny),
    so edit/write tool calls pass through.
    """

    def test_guard_returns_not_denies(self):
        violations = []
        for name in PLUGINS_WITH_TOOL_BEFORE:
            src = _read_plugin(name)
            result = _extract_handler_body(src, "tool.execute.before")
            if result is None:
                continue
            _line_no, body = result
            idx = _find_guard_idx(body)
            if idx is None:
                violations.append(
                    f"{name}.ts: no guard — subagent edit/write would be blocked"
                )
                continue
            guard = body.split("\n")[idx].strip()
            assert "return" in guard, (
                f"{name}.ts: guard missing 'return': {guard!r}"
            )
            assert "throw" not in guard, (
                f"{name}.ts: guard throws (not return): {guard!r}"
            )
        assert not violations, "\n".join(violations)


class TestNoSubagentBlocksBash:
    """Same as edit, but for bash tools."""

    def test_guard_returns_not_denies_bash(self):
        violations = []
        for name in PLUGINS_WITH_TOOL_BEFORE:
            src = _read_plugin(name)
            result = _extract_handler_body(src, "tool.execute.before")
            if result is None:
                continue
            _line_no, body = result
            idx = _find_guard_idx(body)
            if idx is None:
                violations.append(
                    f"{name}.ts: no guard — subagent bash would be blocked"
                )
        assert not violations, "\n".join(violations)


class TestTextCompleteGuards:
    """text.complete hooks that modify/blank agent output MUST also have
    the OPENCODE_SUBAGENT guard, so subagent results are not destroyed.
    """

    def test_multitask_has_text_complete_guard(self):
        result = _extract_handler_body(
            _read_plugin("enforce-multitask"), "experimental.text.complete"
        )
        assert result is not None, (
            "enforce-multitask.ts: experimental.text.complete hook not found"
        )
        _line_no, body = result
        idx = _find_guard_idx(body)
        assert idx is not None, (
            "enforce-multitask.ts: text.complete MISSING OPENCODE_SUBAGENT guard. "
            "Without it, subagent result text (which carries result markers) "
            "is counted toward the zero-streak and can be blanked."
        )
        guard = body.split("\n")[idx].strip()
        assert "return output" in guard, (
            f"enforce-multitask.ts: text.complete guard must return output: {guard!r}"
        )

    def test_stop_has_text_complete_guard(self):
        result = _extract_handler_body(
            _read_plugin("enforce-stop"), "experimental.text.complete"
        )
        assert result is not None, (
            "enforce-stop.ts: experimental.text.complete hook not found"
        )
        _line_no, body = result
        idx = _find_guard_idx(body)
        assert idx is not None, (
            "enforce-stop.ts: text.complete MISSING OPENCODE_SUBAGENT guard. "
            "Without it, subagent result text is checked for false-done claims "
            "and can be blanked, discarding real work output."
        )
        guard = body.split("\n")[idx].strip()
        assert "return output" in guard or "return" in guard, (
            f"enforce-stop.ts: text.complete guard must return (output): {guard!r}"
        )

    def test_verified_claims_has_text_complete_guard(self):
        result = _extract_handler_body(
            _read_plugin("enforce-verified-claims"), "experimental.text.complete"
        )
        assert result is not None, (
            "enforce-verified-claims.ts: experimental.text.complete hook not found"
        )
        _line_no, body = result
        idx = _find_guard_idx(body)
        assert idx is not None, (
            "enforce-verified-claims.ts: text.complete MISSING OPENCODE_SUBAGENT guard. "
            "Subagent results containing done-words (committed, fixed, passed, etc.) "
            "would be blanked by the false-done claim detector."
        )


class TestSystemTransformGuards:
    """system.transform hooks inject directives into the system prompt.
    Inside subagent contexts (OPENCODE_SUBAGENT=1), these MUST return
    output unchanged to avoid contaminating subagent prompts with
    orchestrator-oriented directives (SESSION START PROTOCOL, HARD STOP,
    DELEGATE-FIRST nag, bash-availability warning).
    """

    def test_stop_has_system_transform_guard(self):
        _check_system_transform_guard(
            _read_plugin("enforce-stop"), "experimental.chat.system.transform",
            "enforce-stop.ts",
        )

    def test_session_start_has_system_transform_guard(self):
        _check_system_transform_guard(
            _read_plugin("enforce-session-start"), "experimental.chat.system.transform",
            "enforce-session-start.ts",
        )

    def test_make_has_system_transform_guard(self):
        _check_system_transform_guard(
            _read_plugin("enforce-make"), "experimental.chat.system.transform",
            "enforce-make.ts",
        )


def _check_system_transform_guard(src: str, hook_key: str, plugin_name: str):
    """Verify a system.transform hook has the OPENCODE_SUBAGENT guard with return output."""
    idx = src.find(hook_key)
    assert idx != -1, f"{plugin_name}: {hook_key} hook not found"
    body = src[idx + len(hook_key):]
    # Find opening brace
    brace_idx = body.find("{")
    assert brace_idx != -1, f"{plugin_name}: no opening brace after {hook_key}"
    body = body[brace_idx + 1:]
    guard_match = re.search(
        r'if\s*\(\s*(?:process\.env\.OPENCODE_SUBAGENT\s*===?\s*"1"|'
        r"isSubagent\(\))\s*\)\s*return\s+output",
        body,
        re.MULTILINE,
    )
    assert guard_match is not None, (
        f"{plugin_name}: system.transform MISSING OPENCODE_SUBAGENT guard. "
        f"Must use the exact env check or `if (isSubagent()) return output` "
        f"as first substantive line."
    )


class TestGuardNotInFilesThatDontNeedIt:
    """watchdog.ts is a daemon lifecycle plugin with no tool.execute.before
    or text.complete hooks. It does NOT need the OPENCODE_SUBAGENT guard.
    Verify we don't over-apply the requirement.
    """

    def test_watchdog_has_no_tool_execute_before(self):
        path = PLUGINS_DIR / "watchdog.ts"
        assert path.exists(), "watchdog.ts must exist"
        src = path.read_text()
        assert "tool.execute.before" not in src, (
            "watchdog.ts should NOT have tool.execute.before — it is a daemon"
        )

    def test_watchdog_has_no_text_complete(self):
        src = (PLUGINS_DIR / "watchdog.ts").read_text()
        assert "experimental.text.complete" not in src, (
            "watchdog.ts should NOT have text.complete"
        )

    def test_watchdog_reports_alive(self):
        """watchdog.ts reports liveness to shared alive.json (standard pattern)."""
        src = (PLUGINS_DIR / "watchdog.ts").read_text()
        assert 'reportAlive("watchdog")' in src, (
            "watchdog.ts must report liveness through the shared reportAlive API"
        )


class TestPluginCount:
    """All enforce-*.ts files are accounted for in the test class lists."""

    def test_count_matches_and_all_covered(self):
        plugins = _enforce_plugins()
        expected = {
            plugin.stem
            for plugin in plugins
            if '"tool.execute.before"' in _read_plugin(plugin.stem)
        }
        assert set(PLUGINS_WITH_TOOL_BEFORE) == expected, (
            "tool.execute.before isolation inventory drifted: "
            f"expected={sorted(expected)}, "
            f"actual={PLUGINS_WITH_TOOL_BEFORE}"
        )

    def test_all_plugins_have_identifiable_hooks(self):
        for p in _enforce_plugins():
            src = p.read_text()
            has_obj_hook = '"tool.execute.before"' in src
            has_func_hook = "api.tool.execute.before" in src
            has_text = '"experimental.text.complete"' in src
            has_idle = "session.idle" in src or '"event"' in src
            has_after = '"tool.execute.after"' in src or "api.tool.execute.after" in src
            assert has_obj_hook or has_func_hook or has_text or has_idle or has_after, (
                f"{p.name}: no identifiable hook found (tool.execute.before, "
                f"text.complete, session.idle, event, or tool.execute.after)"
            )
