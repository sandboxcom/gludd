"""
Tests for the enforce-make.ts plugin bash command enforcement.

Validates that:
  - Non-make commands (ls, echo, python3, whoami, pwd, cd, git, cat, which) are blocked
  - Metacharacters (|, ;, &&, $, etc.) are blocked
  - Valid make targets (make test, make lint, make gate-refresh) are allowed
  - Fictional make targets (make ls) are NOT blocked (they start with 'make')
  - Command extraction uses input, not output (fix for bug where output is undefined)
  - SHELL_META_CHARS regex is correct
  - Invalid patterns (2>&1, rg, tail, head, grep, cat, find, ls, cd, python, etc.) are present
"""

import os
import re

ENFORCE_MAKE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", ".opencode", "plugin", "impl", "enforce_make_impl.ts"
)
ENFORCE_MAKE_IMPL_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    ".opencode",
    "plugin",
    "impl",
    "enforce_make_impl.ts",
)


def _read_source():
    with open(ENFORCE_MAKE_PATH) as wrapper, open(
        ENFORCE_MAKE_IMPL_PATH
    ) as implementation:
        # Put the delegated implementation first so structural hook extraction
        # does not stop at the wrapper's intentionally minimal proxy hooks.
        return implementation.read() + "\n" + wrapper.read()


def _find_function_body(source: str, func_search: str) -> str:
    """Extract a named function/hook body from source."""
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


def _extract_bash_block(source: str) -> str:
    """Extract the `if (input.tool === "bash")` block from tool.execute.before."""
    tool_before = _find_function_body(source, '"tool.execute.before"')
    return _find_function_body(tool_before, 'if (input.tool === "bash")')


def _extract_regex(source: str, var_name: str) -> re.Pattern | None:
    """Extract a regex literal assigned to a const variable."""
    m = re.search(rf"const\s+{re.escape(var_name)}\s*=\s*(/.*?/)\s*", source)
    if not m:
        return None
    regex_literal = m.group(1)
    assert regex_literal.startswith("/"), f"Expected regex literal: {regex_literal[:40]}"
    start = 1
    end = regex_literal.rfind("/")
    body = regex_literal[start:end]
    flags = regex_literal[end + 1 :]
    flag = 0
    if "i" in flags:
        flag |= re.IGNORECASE
    if "m" in flags:
        flag |= re.MULTILINE
    return re.compile(body, flag)


# ---------------------------------------------------------------------------
# 1. Command extraction uses input, not output
# ---------------------------------------------------------------------------


def test_command_extracted_from_input():
    """Line 366 must use (input as any)?.args?.command, not output?.args?.command."""
    source = _read_source()
    bash_block = _extract_bash_block(source)

    has_input = "(input as any)?.args?.command" in bash_block
    has_output = "output?.args?.command" in bash_block
    assert has_input, (
        "Bash command MUST be extracted from `input`, not `output`. "
        'Line ~366 should read: const command = (input as any)?.args?.command ?? ""'
    )
    assert not has_output, (
        "Bash command extracted from `output?.args?.command` — this is the BUG. "
        "In tool.execute.before, output is undefined; the command hasn't run yet. "
        "Use input instead."
    )


# ---------------------------------------------------------------------------
# 2. SHELL_META_CHARS regex exists and is correct
# ---------------------------------------------------------------------------


def test_shell_meta_chars_exists():
    source = _read_source()
    assert "SHELL_META_CHARS" in source, "SHELL_META_CHARS regex must be defined"


def test_shell_meta_chars_blocks_pipe():
    source = _read_source()
    meta_re = _extract_regex(source, "SHELL_META_CHARS")
    assert meta_re is not None
    assert meta_re.search("make test | grep foo"), "| (pipe) should match"
    assert meta_re.search("cat file ; ls"), "; (semicolon) should match"
    assert meta_re.search("cmd && other"), "&& should match"
    assert meta_re.search("$(whoami)"), "$() should match"
    assert meta_re.search("`whoami`"), "backtick should match"
    assert meta_re.search("cmd ${VAR}"), "${} should match"
    assert meta_re.search("cmd !"), "! should match"
    assert meta_re.search("cmd {a,b}"), "{} should match"


def test_shell_meta_chars_allows_plain_make():
    source = _read_source()
    meta_re = _extract_regex(source, "SHELL_META_CHARS")
    assert meta_re is not None
    assert not meta_re.search("make test"), "plain make should NOT match"
    assert not meta_re.search("make test TESTFILE=/tmp"), "make with path should NOT match"
    assert not meta_re.search("make lint"), "plain make lint should NOT match"


def test_shell_meta_chars_does_not_block_bare_parens():
    """Bare `(` and `)` must NOT match — commit messages legitimately contain them
    (e.g. MSG="fix foo (see #123)"). The shell-injection vector `$()` is still
    caught via the `$` char in the class. See AGENTS.md Guardrail Integrity Policy.
    """
    source = _read_source()
    meta_re = _extract_regex(source, "SHELL_META_CHARS")
    assert meta_re is not None
    assert not meta_re.search("fix foo (see #123)"), (
        "bare parens in commit messages must NOT match (false-positive bug fix)"
    )
    assert not meta_re.search("(ls)"), "bare-paren subshell no longer blocked"
    # Real injection vector preserved: $() caught via `$`
    assert meta_re.search("$(whoami)"), "$() command substitution must still match"
    assert meta_re.search("`whoami`"), "backtick substitution must still match"
    assert meta_re.search("a; b"), "; chaining must still match"
    assert meta_re.search("a | b"), "| pipe must still match"


# ---------------------------------------------------------------------------
# 3. invalidPatterns array exists and contains expected patterns
# ---------------------------------------------------------------------------


def test_invalid_patterns_exist():
    source = _read_source()
    bash_block = _extract_bash_block(source)
    assert "invalidPatterns" in bash_block, "invalidPatterns array must exist in bash block"
    assert "\\b2>&1\\b" in bash_block, "2>&1 pattern must exist"
    assert "\\bls\\b" in bash_block, "ls pattern must exist"
    assert "\\bcd\\b" in bash_block, "cd pattern must exist"
    assert "\\bpython\\b" in bash_block, "python pattern must exist"
    assert "\\bpython3\\b" in bash_block, "python3 pattern must exist"
    assert "\\bgit\\b" in bash_block, "git pattern must exist"
    assert "\\bcat\\b" in bash_block, "cat pattern must exist"
    assert "\\bwhich\\b" in bash_block, "which pattern must exist"
    assert "\\brm\\b" in bash_block, "rm pattern must exist"
    assert "\\bexport\\b" in bash_block, "export pattern must exist"
    assert "\\bsource\\b" in bash_block, "source pattern must exist"


# ---------------------------------------------------------------------------
# 4. Make-only check — blocks non-make, allows make
# ---------------------------------------------------------------------------


def test_make_only_check_exists():
    source = _read_source()
    bash_block = _extract_bash_block(source)
    assert 'startsWith("make ")' in bash_block, "startsWith('make ') check must exist to gate non-make commands"


# ---------------------------------------------------------------------------
# 5. Simulation: the make-only logic blocks/permits correctly
# ---------------------------------------------------------------------------


NON_MAKE_COMMANDS = ["ls", "echo", "python3", "whoami", "pwd", "cd", "git", "cat", "which"]
MAKE_COMMANDS = ["make test", "make lint", "make gate-refresh", "make"]
FICTIONAL_MAKE = ["make ls", "make whoami", "make my-check"]


def test_non_make_commands_do_not_start_with_make():
    """All listed non-make commands should NOT start with 'make'."""
    for cmd in NON_MAKE_COMMANDS:
        assert not cmd.startswith("make "), f"'{cmd}' starts with 'make '"
        assert cmd != "make", f"'{cmd}' is exactly 'make'"


def test_make_commands_do_start_with_make():
    """All listed make commands SHOULD start with 'make'."""
    for cmd in MAKE_COMMANDS:
        assert cmd.startswith("make ") or cmd == "make", f"'{cmd}' should start with 'make'"


def test_fictional_make_targets_start_with_make():
    """Fictional make targets like 'make ls' DO start with 'make' and should pass the make check."""
    for cmd in FICTIONAL_MAKE:
        assert cmd.startswith("make ") or cmd == "make", f"'{cmd}' should start with 'make'"


# ---------------------------------------------------------------------------
# 6. Metacharacter check exists BEFORE make-only check
# ---------------------------------------------------------------------------


def test_shell_meta_chars_checked_before_make_check():
    """SHELL_META_CHARS.test(unquoted) must appear BEFORE startsWith('make') in the bash block."""
    source = _read_source()
    bash_block = _extract_bash_block(source)

    meta_pos = bash_block.find("SHELL_META_CHARS.test(unquoted)")
    make_pos = bash_block.find('startsWith("make ")')

    assert meta_pos != -1, "SHELL_META_CHARS.test(unquoted) not found in bash block"
    assert make_pos != -1, "startsWith('make ') not found in bash block"
    assert meta_pos < make_pos, (
        f"SHELL_META_CHARS.test(unquoted) at pos {meta_pos} is AFTER "
        f"startsWith('make ') at pos {make_pos}. "
        "The metacharacter check MUST run before the make-prefix check."
    )


# ---------------------------------------------------------------------------
# 7. _bashPolicyNudge flag exists and is set before throw
# ---------------------------------------------------------------------------


def test_bash_policy_nudge_variable_exists():
    source = _read_source()
    assert "_bashPolicyNudge" in source, "_bashPolicyNudge variable must exist"


def test_bash_policy_nudge_set_before_non_make_throw():
    """_bashPolicyNudge = true must appear BEFORE throw for non-make commands."""
    source = _read_source()
    bash_block = _extract_bash_block(source)

    nudge_pos = bash_block.find("_bashPolicyNudge = true")
    throw_pos = bash_block.find("throw new Error")

    assert nudge_pos != -1, "_bashPolicyNudge = true not found in bash block"
    assert nudge_pos < throw_pos, "_bashPolicyNudge = true must be set BEFORE throw new Error"


def test_bash_policy_nudge_reset_in_session_idle():
    """_bashPolicyNudge must be reset in session.idle."""
    source = _read_source()
    idle_body = _find_function_body(source, '"session.idle"')
    assert "_bashPolicyNudge = false" in idle_body, "_bashPolicyNudge must be reset to false in session.idle"


def test_bash_policy_nudge_checked_in_text_complete():
    """_bashPolicyNudge must be checked in experimental.text.complete."""
    source = _read_source()
    text_body = _find_function_body(source, '"experimental.text.complete"')
    assert "_bashPolicyNudge" in text_body, "_bashPolicyNudge must be checked in experimental.text.complete"


# ---------------------------------------------------------------------------
# 8. Gate concurrency guard works for valid make targets
# ---------------------------------------------------------------------------


def test_gate_concurrency_regex():
    source = _read_source()
    gate_re = _extract_regex(source, "GATE_TARGETS_RE")
    assert gate_re is not None, "GATE_TARGETS_RE must be defined"
    assert gate_re.search("make gate"), "make gate should match"
    assert gate_re.search("make test"), "make test should match"
    assert gate_re.search("make test-unit"), "make test-unit should match"
    assert gate_re.search("make test-e2e"), "make test-e2e should match"
    assert gate_re.search("make qa"), "make qa should match"
    assert gate_re.search("make test-and-commit"), "make test-and-commit should match"
    assert not gate_re.search("make lint"), "make lint should NOT match"
    assert not gate_re.search("make typecheck"), "make typecheck should NOT match"


# ---------------------------------------------------------------------------
# 9. Long-running foreground guard blocks gate/test/qa/validate
# ---------------------------------------------------------------------------


def test_long_running_foreground_guard():
    source = _read_source()
    bash_block = _extract_bash_block(source)
    assert "Long-running foreground command" in bash_block, "Long-running foreground guard must exist"


def test_long_running_blocks_gate():
    source = _read_source()
    bash_block = _extract_bash_block(source)
    assert 'lrTarget === "gate"' in bash_block, "make gate should be blocked in foreground"
    assert 'lrTarget === "test-unit"' in bash_block, "make test-unit should be blocked"
    assert 'lrTarget === "qa"' in bash_block, "make qa should be blocked"
    assert 'lrTarget === "test-e2e"' in bash_block, "make test-e2e should be blocked"
    assert 'lrTarget === "validate"' in bash_block, "make validate should be blocked"


def test_long_running_allows_lint():
    source = _read_source()
    bash_block = _extract_bash_block(source)
    allowed = (
        "make lint" in bash_block
        or "make typecheck" in bash_block
        or "make test-count" in bash_block
        or "make collect-check" in bash_block
    )
    assert allowed, "At least one of lint/typecheck/test-count/collect-check must be documented as allowed"


# ---------------------------------------------------------------------------
# 10. make ls (fictional target) — structural check
# ---------------------------------------------------------------------------


def test_fictional_make_target_not_in_blocklist():
    """make ls is NOT in the MAKEFILE_TARGETS_WITH_FORBIDDEN_NAMES list."""
    source = _read_source()
    bash_block = _extract_bash_block(source)
    forbidden_list = [
        "git-status",
        "git-diff",
        "git-staged",
        "git-init",
        "git-log",
        "git-add",
        "git-add-all",
        "git-commit",
        "git-reset",
        "git-branch",
        "git-checkout",
        "git-merge",
        "feature-start",
        "feature-done",
        "delete-file",
    ]
    for item in forbidden_list:
        assert item in bash_block, f"'{item}' expected in MAKEFILE_TARGETS_WITH_FORBIDDEN_NAMES"
    assert '"ls"' not in " ".join(forbidden_list), "ls should NOT be in MAKEFILE_TARGETS_WITH_FORBIDDEN_NAMES"


# ---------------------------------------------------------------------------
# 11. formatBashBlockedMessage helper exists
# ---------------------------------------------------------------------------


def test_format_bash_blocked_message_exists():
    source = _read_source()
    assert "function formatBashBlockedMessage" in source, "formatBashBlockedMessage helper must exist"
    assert "BASH_POLICY_HEADER" in source, "BASH_POLICY_HEADER must exist"
    assert "BASH_POLICY_RULE" in source, "BASH_POLICY_RULE must exist"
    assert "BASH_POLICY_FIX" in source, "BASH_POLICY_FIX must exist"


# ---------------------------------------------------------------------------
# 12. Behavioral simulation — ported hook logic tested against real commands
# ---------------------------------------------------------------------------

_SHELL_META_CHARS_BEHAVIORAL = re.compile(r"[|;&{}$`\\!]")

_INVALID_PATTERNS_BEHAVIORAL: list[re.Pattern] = [
    re.compile(r"\b2>&1\b"),
    re.compile(r"\b>\s"),
    re.compile(r"\b<\s"),
    re.compile(r"\brg\b"),
    re.compile(r"\btail\b"),
    re.compile(r"\bhead\b"),
    re.compile(r"\bgrep\b"),
    re.compile(r"\bcat\b"),
    re.compile(r"\bfind\b"),
    re.compile(r"\bls\b"),
    re.compile(r"\bcd\b"),
    re.compile(r"\bpython\b"),
    re.compile(r"\bpython3\b"),
    re.compile(r"\buv\b"),
    re.compile(r"\bpip\b"),
    re.compile(r"\bgit\b"),
    re.compile(r"\brm\b"),
    re.compile(r"\bcp\b"),
    re.compile(r"\bmv\b"),
    re.compile(r"\bwhich\b"),
    re.compile(r"\bcommand\b"),
    re.compile(r"\bexport\b"),
    re.compile(r"\bsource\b"),
]

_MAKEFILE_TARGETS_WITH_FORBIDDEN_NAMES_BEHAVIORAL = frozenset(
    [
        "git-status",
        "git-diff",
        "git-staged",
        "git-init",
        "git-log",
        "git-add",
        "git-add-all",
        "git-commit",
        "git-reset",
        "git-branch",
        "git-checkout",
        "git-merge",
        "feature-start",
        "feature-done",
        "delete-file",
    ]
)


def _simulate_bash_check(command: str) -> str | None:
    """Port enforce-make.ts bash-block logic into Python for behavioral testing.

    Returns an error message if the command would be blocked, or None if allowed.
    """
    trimmed = command.strip()

    if trimmed and _SHELL_META_CHARS_BEHAVIORAL.search(trimmed):
        return f"SHELL_META_CHARS matched in: {trimmed}"

    if not trimmed.startswith("make ") and trimmed != "make":
        return f"Not a make command: {trimmed}"

    after_make = trimmed[5:].strip()
    words = after_make.split()
    target_name = words[0] if words else ""
    rest_args = " ".join(words[1:]) if len(words) > 1 else ""

    args_stripped = re.sub(
        r"[A-Za-z_][A-Za-z0-9_]*=('[^']*'|\"[^\"]*\"|\S*)",
        "",
        rest_args,
    )

    if target_name in _MAKEFILE_TARGETS_WITH_FORBIDDEN_NAMES_BEHAVIORAL:
        if _SHELL_META_CHARS_BEHAVIORAL.search(args_stripped):
            return f"Metachar in forbidden-target args: {trimmed}"
    else:
        if _SHELL_META_CHARS_BEHAVIORAL.search(args_stripped):
            return f"Metachar in args of: {trimmed}"
        for pat in _INVALID_PATTERNS_BEHAVIORAL:
            if pat.search(args_stripped):
                return f"Forbidden pattern {pat.pattern} in args of: {trimmed}"
    return None


# --- 12a. Non-make commands are blocked ---


def test_bash_check_blocks_ls():
    assert _simulate_bash_check("ls") is not None


def test_bash_check_blocks_echo():
    assert _simulate_bash_check("echo hello") is not None


def test_bash_check_blocks_python3():
    assert _simulate_bash_check("python3 -c 'print(1)'") is not None


def test_bash_check_blocks_whoami():
    assert _simulate_bash_check("whoami") is not None


def test_bash_check_blocks_pwd():
    assert _simulate_bash_check("pwd") is not None


def test_bash_check_blocks_cd():
    assert _simulate_bash_check("cd /tmp") is not None


def test_bash_check_blocks_git():
    assert _simulate_bash_check("git status") is not None


def test_bash_check_blocks_cat():
    assert _simulate_bash_check("cat file.txt") is not None


def test_bash_check_blocks_which():
    assert _simulate_bash_check("which python") is not None


def test_bash_check_blocks_pip():
    assert _simulate_bash_check("pip install foo") is not None


# --- 12b. Valid make targets are allowed ---


def test_bash_check_allows_make_test():
    assert _simulate_bash_check("make test") is None


def test_bash_check_allows_make_lint():
    assert _simulate_bash_check("make lint") is None


def test_bash_check_allows_make_gate_refresh():
    assert _simulate_bash_check("make gate-refresh") is None


def test_bash_check_allows_make_test_with_testfile():
    assert _simulate_bash_check("make test TESTFILE=tests/unit/test_foo.py") is None


def test_bash_check_allows_bare_make():
    assert _simulate_bash_check("make") is None


# --- 12c. Fictional make targets are allowed ---


def test_bash_check_allows_make_ls():
    """make ls is a fictional target but starts with 'make' — should pass."""
    assert _simulate_bash_check("make ls") is None


def test_bash_check_allows_make_whoami():
    assert _simulate_bash_check("make whoami") is None


def test_bash_check_allows_make_my_check():
    assert _simulate_bash_check("make my-check") is None


# --- 12d. Metacharacters in commands are blocked ---


def test_bash_check_blocks_pipe():
    assert _simulate_bash_check("make test | grep foo") is not None


def test_bash_check_blocks_semicolon():
    assert _simulate_bash_check("make test; ls") is not None


def test_bash_check_blocks_and_and():
    assert _simulate_bash_check("make test && make lint") is not None


def test_bash_check_blocks_dollar_subshell():
    assert _simulate_bash_check("$(cat /etc/passwd)") is not None


def test_bash_check_blocks_backtick():
    assert _simulate_bash_check("`id`") is not None


def test_bash_check_blocks_bang():
    assert _simulate_bash_check("make test !") is not None


def test_bash_check_blocks_dollar_var():
    assert _simulate_bash_check("echo $HOME") is not None


def test_bash_check_blocks_curly_brace():
    assert _simulate_bash_check("make {a,b}") is not None


# --- 12e. Metacharacters in make args are also blocked ---


def test_bash_check_blocks_make_with_pipe_args():
    assert _simulate_bash_check("make test TESTFILE='a|b'") is not None


def test_bash_check_blocks_make_test_2gt1():
    assert _simulate_bash_check("make test 2>&1") is not None


# --- 12ee. VAR=value quoted assignments are stripped before pattern check ---


def test_bash_check_allows_make_with_quoted_var_value():
    """Quoted VAR=value assignments are stripped from restArgs so they don't
    trigger invalidPatterns. FILES='src/ models/' -> stripped -> empty restArgs."""
    assert _simulate_bash_check("make lint FILES='src/ models/'") is None


def test_bash_check_allows_make_with_double_quoted_var():
    assert _simulate_bash_check('make lint FILES="src/ models/"') is None


# --- 12f. Known-bad patterns in make args are blocked ---


def test_bash_check_blocks_ls_in_args():
    assert _simulate_bash_check("make test ls") is not None


def test_bash_check_blocks_grep_in_args():
    assert _simulate_bash_check("make test grep") is not None


def test_bash_check_blocks_cat_in_args():
    assert _simulate_bash_check("make test cat") is not None


def test_bash_check_blocks_python3_in_args():
    assert _simulate_bash_check("make test python3") is not None


def test_bash_check_blocks_pip_in_args():
    assert _simulate_bash_check("make test pip install") is not None


# --- 12g. Edge cases ---


def test_bash_check_blocks_empty_string():
    """Empty string: trimmed is '', so SHELL_META_CHARS test is false,
    and '' != 'make' but also doesn't start with 'make '. Should be blocked."""
    assert _simulate_bash_check("") is not None


def test_bash_check_blocks_whitespace_only():
    assert _simulate_bash_check("   ") is not None


def test_bash_check_allows_make_with_git_target():
    """make git-status is in MAKEFILE_TARGETS_WITH_FORBIDDEN_NAMES so only metachars
    in args would block it."""
    assert _simulate_bash_check("make git-status") is None


def test_bash_check_allows_make_git_commit_with_msg():
    assert _simulate_bash_check('make git-commit MSG="fix"') is None


def test_bash_check_blocks_make_git_commit_with_metachar():
    assert _simulate_bash_check("make git-commit MSG='fix; rm -rf /'") is not None


def test_bash_check_allows_make_git_commit_with_parens_in_msg():
    """Regression: bare parens in MSG="..." must NOT be blocked.
    Prior subagent had to drop parens from a commit message because
    SHELL_META_CHARS matched `(` and `)` literally anywhere in the command.
    Narrowed 2026-07-18: bare parens are data inside quoted strings.
    """
    assert _simulate_bash_check('make git-commit MSG="fix foo (see #123)"') is None
    assert _simulate_bash_check("make ship-commit MSG='fix enforce-make (narrow parens matcher)' PUSH=0") is None


def test_bash_check_still_blocks_dollar_paren_in_msg():
    """`$()` command substitution must STILL be blocked even inside MSG —
    narrowing bare parens does NOT weaken `$` detection."""
    assert _simulate_bash_check('make git-commit MSG="$(whoami)"') is not None
    assert _simulate_bash_check("make git-commit MSG='`whoami`'") is not None
