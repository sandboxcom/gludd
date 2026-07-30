"""Structural verification of enforce-make.ts command handling.

Validates:
  1. cleanCommand mechanism: inline $/VAR=val stripping (no named function yet)
  2. Command extraction order: input.args.command before output.args.command
  3. VAR=val stripping applied to toScan prevents false positive on
     'grep' in TESTFILE values (line 510)
  4. $ strip regex handles bash$ and sh$ prefix variants

These are structural tests only — parse the TypeScript source, no runtime execution.
"""

from __future__ import annotations

import os
import re

ENFORCE_MAKE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", ".opencode", "plugin", "enforce-make.ts"
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


def _read_source() -> str:
    with open(ENFORCE_MAKE_PATH) as wrapper, open(
        ENFORCE_MAKE_IMPL_PATH
    ) as implementation:
        return wrapper.read() + "\n" + implementation.read()


def _extract_regex(source: str, var_name: str) -> re.Pattern | None:
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
# 1. Command cleaning: $ prefix + VAR=val stripping (inline, no named function)
# ---------------------------------------------------------------------------


def test_dollar_prefix_strip_regex_exists():
    """Line 361: command.replace(/^\\S*\\$\\s*/, '') strips '$ ' and 'bash$ '
    prefixes from copied shell commands."""
    source = _read_source()
    # Search for the literal pattern \S*\$\s* which appears in the regex literal
    assert "\\S*\\$\\s*" in source, (
        "command.replace(/^\\S*\\$\\s*/, '') must exist at line 361. "
        "This strips leading '$ ' and 'bash$ ' prefixes from copied commands."
    )


def test_dollar_prefix_regex_strips_leading_dollar():
    """The regex /^\\S*\\$\\s*/ strips: '$ ', 'bash$ ', 'sh$ ', '$', '$make' etc."""
    strip_re = re.compile(r"^\S*\$\s*")

    # leading $ with space
    assert strip_re.sub("", "$ make test") == "make test"
    # leading $ without space
    assert strip_re.sub("", "$make test") == "make test"
    # bash$ variant
    assert strip_re.sub("", "bash$ make test") == "make test"
    # sh$ variant
    assert strip_re.sub("", "sh$ make test") == "make test"
    # no $ prefix — no substitution
    assert strip_re.sub("", "make test") == "make test"
    # only $ — strip to empty
    assert strip_re.sub("", "$") == ""


def test_varval_strip_applied_in_toscan_definition():
    """Line 510: toScan = restArgs.replace(VAR=val regex, '') strips VAR=val
    for ALL targets, not just forbidden-names ones."""
    source = _read_source()

    # toScan definition must include the replace call
    toscan_match = re.search(
        r"const\s+toScan\s*=\s*restArgs\s*\.\s*replace\s*\(.*?/g",
        source,
    )
    assert toscan_match is not None, (
        "const toScan = restArgs.replace(VAR=val regex, '') must exist at line 510. "
        "This applies VAR=val stripping BEFORE the invalidPatterns check for all targets."
    )

    # Verify the regex is present in the toScan definition
    has_varval_in_toscan = re.search(
        r"toScan\s*=\s*restArgs\s*\.\s*replace\s*\(\s*/\[A-Za-z_\]\[A-Za-z0-9_\]",
        source,
    )
    assert has_varval_in_toscan is not None, (
        "toScan definition must include the VAR=val strip regex"
    )


def test_varval_strip_regex_extracted_and_tested():
    """The VAR=val strip regex (on toScan line 510) correctly strips
    KEY=value, KEY='val', KEY=\"val\" assignments."""
    source = _read_source()

    # Extract the regex from the toScan definition
    m = re.search(
        r"toScan\s*=\s*restArgs\s*\.\s*replace\s*\(\s*/(\[A-Za-z_\]\[A-Za-z0-9_\]\*=.*?)/g",
        source,
    )
    assert m is not None
    regex_body = m.group(1)
    r = re.compile(regex_body)

    # Should match VAR=value patterns
    assert r.search("TESTFILE='tests/unit/test_foo.py'")
    assert r.search('MSG="hello world"')
    assert r.search("FILES=src/models")
    assert r.search("NO_XDIST=1")

    # After stripping all matches, only non-VAR arguments remain
    stripped = r.sub("", "TESTFILE='tests/unit/test_grep_tool.py' NO_XDIST=1 extra_arg")
    stripped = stripped.strip()
    assert "extra_arg" in stripped
    assert "test_grep_tool" not in stripped
    assert "NO_XDIST" not in stripped


def test_grep_false_positive_prevented_by_varval_strip():
    """When TESTFILE='tests/grep.py' is VAR=val stripped from toScan, the
    \\bgrep\\b invalidPattern no longer triggers a false positive.

    Uses 'tests/grep.py' (not 'test_grep_*.py') because \\b word boundary
    does not match around underscore-delimited words."""
    source = _read_source()

    # Extract the VAR=val strip regex from toScan
    m = re.search(
        r"toScan\s*=\s*restArgs\s*\.\s*replace\s*\(\s*/(\[A-Za-z_\]\[A-Za-z0-9_\]\*=.*?)/g",
        source,
    )
    assert m is not None
    r = re.compile(m.group(1))

    # Simulate: make test-specific TESTFILE='tests/grep.py'
    after_make = "test-specific TESTFILE='tests/grep.py'"
    words = after_make.split()
    rest_args = " ".join(words[1:]) if len(words) > 1 else ""

    # Raw restArgs contains 'grep' at a word boundary → would match \bgrep\b
    assert re.search(r"\bgrep\b", rest_args), (
        "Sanity check: 'grep' must appear in raw restArgs to demonstrate the bug"
    )

    # toScan = restArgs.replace(VAR=val regex, '') — strip VAR=val
    to_scan = r.sub("", rest_args).strip()

    # After stripping, 'grep' is gone — no false positive
    assert not re.search(r"\bgrep\b", to_scan), (
        f"After VAR=val stripping, 'grep' should NOT appear. "
        f"raw={rest_args!r} stripped={to_scan!r}"
    )


def test_clean_command_is_inline_not_named_function():
    """Document: there is no named 'cleanCommand' function. The $ strip and
    VAR=val strip are inline code (lines 361, 510). This is a structural
    observation about code organization, not a behavioral defect."""
    source = _read_source()
    has_named_function = "function cleanCommand" in source or "const cleanCommand" in source

    # The cleaning exists inline — document that no named function wraps it.
    # This test passes either way; it's informational.
    if not has_named_function:
        pass


# ---------------------------------------------------------------------------
# 2. Command extraction order: input.args.command before output.args.command
# ---------------------------------------------------------------------------


def test_command_extraction_input_before_output():
    """Line 350-354: input.args.command must be checked BEFORE output.args.command.
    In tool.execute.before, output is undefined — input is the correct source."""
    source = _read_source()

    bash_start = source.find('if (input.tool === "bash")')
    assert bash_start != -1

    extraction_block = source[bash_start : bash_start + 600]

    output_check = extraction_block.find("(output as any)?.args?.command")
    input_check = extraction_block.find("(input as any)?.args?.command")

    assert input_check != -1, "input?.args?.command extraction must exist"
    assert output_check != -1, "output?.args?.command fallback must exist"

    assert input_check < output_check, (
        f"input check at offset {input_check} is AFTER output check at {output_check}. "
        "input.args.command MUST be checked first — output is undefined in tool.execute.before."
    )


def test_input_args_command_extraction_exists():
    """Line 350: const ic = (input as any)?.args?.command."""
    source = _read_source()
    ic_pattern = re.search(r"const\s+ic\s*=\s*\(input as any\)\?\.args\?\.command", source)
    assert ic_pattern is not None, (
        "const ic = (input as any)?.args?.command must exist (line 350)"
    )


def test_output_args_command_fallback_exists():
    """Line 353: const oc = (output as any)?.args?.command as fallback."""
    source = _read_source()
    oc_pattern = re.search(r"const\s+oc\s*=\s*\(output as any\)\?\.args\?\.command", source)
    assert oc_pattern is not None, (
        "const oc = (output as any)?.args?.command must exist as fallback (line 353)"
    )


def test_input_command_dot_command_fallback_exists():
    """Line 357: input?.command fallback for flat input shape."""
    source = _read_source()
    assert "(input as any)?.command" in source, (
        "input?.command fallback must exist (for when args is absent)"
    )


# ---------------------------------------------------------------------------
# 3. VAR=val stripping prevents grep false positive (validated in toScan)
# ---------------------------------------------------------------------------


def test_toscan_not_raw_restargs():
    """Line 510: toScan is restArgs with VAR=val stripped, NOT raw restArgs.
    This prevents grep/cat/etc. in TESTFILE='...' values from triggering
    false positives in the invalidPatterns loop."""
    source = _read_source()

    # toScan must NOT be bare restArgs (without VAR=val stripping)
    toscan_raw = re.search(r"const\s+toScan\s*=\s*restArgs\s*[^.]", source)
    assert toscan_raw is None, (
        "BUG: toScan = restArgs (raw, no VAR=val stripping). "
        "Must be: toScan = restArgs.replace(VAR=val regex, '')"
    )

    toscan_stripped = re.search(
        r"const\s+toScan\s*=\s*restArgs\s*\.\s*replace\s*\(",
        source,
    )
    assert toscan_stripped is not None, (
        "toScan must call restArgs.replace(...) to strip VAR=val before checking patterns"
    )


def test_invalid_patterns_loop_uses_toscan():
    """The invalidPatterns loop (line 559-570) checks pattern.test(toScan).
    Combined with toScan definition at line 510 (VAR=val already stripped),
    grep/cat/etc. in TESTFILE values are NOT falsely blocked."""
    source = _read_source()

    # Search the full source for the pattern.test(toScan) pattern
    # Line 560: if (pattern.test(toScan)) {
    uses_pattern_test_toscan = re.search(r"pattern\s*\.\s*test\s*\(\s*toScan\s*\)", source)
    assert uses_pattern_test_toscan is not None, (
        "Line 560 must be: pattern.test(toScan) — the invalidPatterns loop "
        "must check against toScan (which has VAR=val already stripped)"
    )


# ---------------------------------------------------------------------------
# 4. $ strip regex /^\\S*\\$\\s*/ handles bash$ and sh$ variants
# ---------------------------------------------------------------------------


def test_dollar_strip_regex_handles_bash_prefix():
    """The regex /^\\S*\\$\\s*/ at line 361 strips 'bash$ ' because \\S* matches
    the 'bash' prefix before the '$'."""
    source = _read_source()
    assert "\\S*\\$\\s*" in source, (
        "The $ strip regex must exist in source (line 361)"
    )

    strip_re = re.compile(r"^\S*\$\s*")

    # bash$ make test -> strips to "make test"
    result = strip_re.sub("", "bash$ make test")
    assert result == "make test", f"Expected 'make test', got {result!r}"

    # sh$ make test -> strips to "make test"
    result2 = strip_re.sub("", "sh$ make test")
    assert result2 == "make test", f"Expected 'make test', got {result2!r}"

    # zsh$ make test -> strips to "make test"
    result3 = strip_re.sub("", "zsh$ make test")
    assert result3 == "make test", f"Expected 'make test', got {result3!r}"


def test_dollar_strip_regex_matches_dollar_space():
    """The regex /^\\S*\\$\\s*/ also handles bare '$ ' (no prefix)."""
    strip_re = re.compile(r"^\S*\$\s*")

    assert strip_re.sub("", "$ make test") == "make test"
    assert strip_re.sub("", "$  make test") == "make test"
    assert strip_re.sub("", "$make test") == "make test"


def test_dollar_strip_regex_does_not_alter_plain_make():
    """Plain 'make test' (no $ prefix) should NOT be altered."""
    strip_re = re.compile(r"^\S*\$\s*")
    assert strip_re.sub("", "make test") == "make test"


# ---------------------------------------------------------------------------
# 5. Integration: full cleaning pipeline structure
# ---------------------------------------------------------------------------


def test_full_cleaning_pipeline_steps_in_order():
    """Verify the cleaning pipeline steps exist in order:
    1. Command extraction from input (line 350)
    2. $ prefix strip (line 361)
    3. Make target extraction (line 364)
    4. toScan = restArgs.replace(VAR=val regex) (line 510)
    5. invalidPatterns check against toScan (line 560)
    """
    source = _read_source()

    # Step 1: input extraction
    pos_ic = source.find("(input as any)?.args?.command")
    assert pos_ic != -1, "Step 1: input extraction must exist"

    # Step 2: $ strip — search for \S*\$\s* pattern
    pos_dollar = source.find("\\S*\\$\\s*")
    assert pos_dollar != -1, "Step 2: $ strip must exist"

    # Step 3: make extraction
    pos_make = source.find("trimmed.match(/^(make\\s+\\S+)/)")
    assert pos_make != -1, "Step 3: make extraction must exist"

    # Step 4: toScan VAR=val stripping
    pos_toscan = source.find("const toScan = restArgs.replace(")
    assert pos_toscan != -1, "Step 4: toScan VAR=val stripping must exist"

    # Step 5: invalidPatterns
    pos_inval = source.find("const invalidPatterns = [")
    assert pos_inval != -1, "Step 5: invalidPatterns array must exist"

    # Verify order: all steps are in sequence
    assert pos_ic < pos_dollar < pos_make < pos_toscan < pos_inval, (
        f"Steps must be in order. Got: ic={pos_ic}, $={pos_dollar}, "
        f"make={pos_make}, toScan={pos_toscan}, invalid={pos_inval}"
    )


def test_invalid_patterns_contains_all_forbidden_terms():
    """The invalidPatterns array must contain all forbidden command patterns."""
    source = _read_source()
    required = [
        "\\b2>&1\\b", "\\brg\\b", "\\btail\\b", "\\bhead\\b",
        "\\bgrep\\b", "\\bcat\\b", "\\bfind\\b", "\\bls\\b",
        "\\bcd\\b", "\\bpython\\b", "\\bpython3\\b", "\\buv\\b",
        "\\bpip\\b", "\\bgit\\b", "\\brm\\b", "\\bcp\\b",
        "\\bmv\\b", "\\bwhich\\b", "\\bcommand\\b", "\\bexport\\b",
        "\\bsource\\b",
    ]
    for pat in required:
        assert pat in source, f"Forbidden pattern {pat} must exist in invalidPatterns"


# ---------------------------------------------------------------------------
# 6. argsStripped (forbidden-names path) also strips VAR=val (line 523)
# ---------------------------------------------------------------------------


def test_argsstripped_variable_still_exists_for_forbidden_names():
    """Line 523: argsStripped strips VAR=val in the forbidden-names block.
    This is redundant with toScan but serves as an explicit check against
    metacharacters in args to targets like git-commit, git-status, etc."""
    source = _read_source()
    assert "argsStripped" in source, (
        "argsStripped must exist for the forbidden-names metachar check (line 523)"
    )


def test_both_toscan_and_argsstripped_strip_varval():
    """Both toScan and argsStripped use the same VAR=val strip regex.
    toScan (line 510) applies to ALL targets; argsStripped (line 523)
    applies an additional metachar check for forbidden-names targets only."""
    source = _read_source()

    # Extract both regexes and verify they're the same
    toscan_re = re.search(
        r"toScan\s*=\s*restArgs\s*\.\s*replace\s*\(\s*/(\[A-Za-z_\]\[A-Za-z0-9_\]\*=.*?)/g",
        source,
    )
    argsstripped_re = re.search(
        r"argsStripped\s*=\s*restArgs\s*\.\s*replace\s*\(\s*/(\[A-Za-z_\]\[A-Za-z0-9_\]\*=.*?)/g",
        source,
    )

    assert toscan_re is not None, "toScan VAR=val strip must exist"
    assert argsstripped_re is not None, "argsStripped VAR=val strip must exist"

    assert toscan_re.group(1) == argsstripped_re.group(1), (
        "toScan and argsStripped must use the SAME VAR=val strip regex"
    )
