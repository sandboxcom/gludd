# Tests subagent-aware behavior of the enforce-make.ts plugin.
import os
import re

ENFORCE_MAKE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", ".opencode", "plugin", "impl", "enforce_make_impl.ts"
)


def _read_source():
    with open(ENFORCE_MAKE_PATH) as f:
        return f.read()


def _find_function_body(source: str, func_search: str) -> str:
    idx = source.index(func_search)
    brace_count = 0
    started = False
    end = idx
    for i in range(idx, len(source)):
        c = source[i]
        if c == "{":
            brace_count += 1
            started = True
        elif c == "}":
            brace_count -= 1
            if started and brace_count == 0:
                end = i + 1
                break
    return source[idx:end]


# ---------------------------------------------------------------------------
# 1. No blanket `if (OPENCODE_SUBAGENT) return` before the bash check
# ---------------------------------------------------------------------------


def test_no_blanket_subagent_return_before_bash_check():
    """The bash make-only check must NOT be behind an early OPENCODE_SUBAGENT guard."""
    source = _read_source()
    tool_before = _find_function_body(source, '"tool.execute.before"')

    before_bash = tool_before[: tool_before.index('if (input.tool === "bash")')]

    isSubagent_set = re.findall(r"const\s+isSubagent\s*=", before_bash)
    assert len(isSubagent_set) <= 1, "isSubagent declared at most once before bash check"

    subagent_returns = [
        m
        for m in re.finditer(r"if\s*\(\s*isSubagent\s*\)\s*return", before_bash)
        if "OPENCODE_SUBAGENT" in before_bash[m.start() : m.start() + 200]
    ]
    assert len(subagent_returns) == 0, (
        "Blanket `if (isSubagent) return` found BEFORE the bash check block. "
        "Bash make-only enforcement must run for subagents."
    )

    # The `const isSubagent = process.env.OPENCODE_SUBAGENT === "1"` on line 355
    # is a variable *declaration*, not a guard. We only flag guard-like patterns:
    # `if (isSubagent) return` or `if (process.env.OPENCODE_SUBAGENT ...) return`.
    guard_returns = [
        m
        for m in re.finditer(
            r"if\s*\(\s*(?:isSubagent|process\.env\.OPENCODE_SUBAGENT)",
            before_bash,
        )
        if "return" in before_bash[m.start() : m.start() + 150]
    ]
    assert len(guard_returns) == 0, (
        "OPENCODE_SUBAGENT / isSubagent GUARD (early return) found BEFORE "
        "the bash check block. The bash make-only + metacharacter checks "
        "must apply to subagents too."
    )


# ---------------------------------------------------------------------------
# 2. Bash pattern checks are NOT behind the OPENCODE_SUBAGENT guard
# ---------------------------------------------------------------------------


def test_bash_checks_not_behind_subagent_guard():
    """The make-only, metacharacters, and invalid-pattern checks must fire for subagents."""
    source = _read_source()
    tool_before = _find_function_body(source, '"tool.execute.before"')
    after_bash = tool_before[tool_before.index('if (input.tool === "bash")') :]

    subagent_guard_pos = after_bash.find("if (isSubagent) return")
    bash_block_end = after_bash.index("}") if "}" in after_bash else len(after_bash)

    if subagent_guard_pos != -1:
        assert subagent_guard_pos > bash_block_end, (
            f"`if (isSubagent) return` at position {subagent_guard_pos} "
            f"appears INSIDE or BEFORE the bash check block ends "
            f"(bash block rough end: {bash_block_end}). "
            "Bash make-only enforcement must NOT be behind the subagent guard."
        )

    bash_body = _find_function_body(tool_before, 'if (input.tool === "bash")')

    assert "make " in bash_body or 'startsWith("make")' in bash_body, "Bash block missing make-prefix enforcement"
    assert "SHELL_META_CHARS" in bash_body, "Bash block missing metacharacter enforcement"
    assert "Forbidden command" in bash_body or "invalidPatterns" in bash_body, (
        "Bash block missing invalid-pattern/bare-command enforcement"
    )

    isSubagent_declared = "const isSubagent" in tool_before
    assert isSubagent_declared, (
        "isSubagent is not declared in tool.execute.before at all. It should still exist to guard edit/write checks."
    )


# ---------------------------------------------------------------------------
# 3. Text-injection hooks (text.complete, system.transform) STILL guarded
# ---------------------------------------------------------------------------


def test_text_complete_still_has_subagent_guard():
    """experimental.text.complete MUST still skip for subagents."""
    source = _read_source()
    text_complete_body = _find_function_body(source, '"experimental.text.complete"')

    guard = re.search(
        r'OPENCODE_SUBAGENT\s*===?\s*["\']1["\']\s*\)\s*return\s+output',
        text_complete_body,
    )
    assert guard is not None, (
        "experimental.text.complete is missing the OPENCODE_SUBAGENT guard. "
        "Text-injection hooks must still skip for subagents."
    )

    # Verify the guard is near the top (within first 5 lines of the function body)
    guard_pos = text_complete_body.index(guard.group())
    early_enough = text_complete_body[:guard_pos].count("\n") < 8
    assert early_enough, (
        "OPENCODE_SUBAGENT guard in experimental.text.complete is too deep. It should be near the top of the function."
    )


def test_system_transform_still_has_subagent_guard():
    """experimental.chat.system.transform MUST still skip for subagents."""
    source = _read_source()
    system_transform_body = _find_function_body(source, '"experimental.chat.system.transform"')

    guard = re.search(
        r'OPENCODE_SUBAGENT\s*===?\s*["\']1["\']\s*\)\s*return\s+output',
        system_transform_body,
    )
    assert guard is not None, (
        "experimental.chat.system.transform is missing the OPENCODE_SUBAGENT guard. "
        "Text-injection hooks must still skip for subagents."
    )

    guard_pos = system_transform_body.index(guard.group())
    early_enough = system_transform_body[:guard_pos].count("\n") < 8
    assert early_enough, (
        "OPENCODE_SUBAGENT guard in experimental.chat.system.transform is too deep. "
        "It should be near the top of the function."
    )


# ---------------------------------------------------------------------------
# 4. tool.execute.after — no subagent guard needed (completion reminder is harmless)
# ---------------------------------------------------------------------------


def test_tool_execute_after_no_subagent_guard_needed():
    """tool.execute.after runs the commit reminder; subagent guard is optional here."""
    source = _read_source()
    after_body = _find_function_body(source, '"tool.execute.after"')

    has_guard = re.search(r'OPENCODE_SUBAGENT\s*===?\s*["\']1["\']', after_body)
    if has_guard:
        pass  # guarded is fine
    else:
        assert "make test" in after_body or "_pendingCommitReminder" in after_body, (
            "tool.execute.after has unexpected content"
        )
        # No guard is fine — the commit reminder is harmless for subagents
        pass


# ---------------------------------------------------------------------------
# 5. session.idle — no subagent guard needed
# ---------------------------------------------------------------------------


def test_session_idle_no_subagent_guard_needed():
    """session.idle resets per-turn state; subagent guard is not required."""
    source = _read_source()
    idle_body = _find_function_body(source, '"session.idle"')

    has_guard = re.search(r'OPENCODE_SUBAGENT\s*===?\s*["\']1["\']', idle_body)
    if has_guard:
        pass
    else:
        assert "_makeTurnState" in idle_body or "_pendingCommitReminder" in idle_body
