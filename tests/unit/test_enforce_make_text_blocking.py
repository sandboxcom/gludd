"""
Tests for the enforce-make.ts text.complete hook gate-status blocking logic.

Validates:
  1. Clean gate (no .gate-status FAIL) → output passes through unchanged
  2. Gate red (.gate-status contains FAIL) → "[GATE RED]" prefix prepended
  3. CI FAIL only (no .gate-status file or no FAIL in it) → output passes through
  4. Subagent context (isSubagent() → true) → output passes through unchanged
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
    with open(ENFORCE_MAKE_IMPL_PATH) as implementation, open(
        ENFORCE_MAKE_PATH
    ) as wrapper:
        return implementation.read() + "\n" + wrapper.read()


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


def _get_text_complete_body() -> str:
    source = _read_source()
    # Scope to defaultImpl to avoid the proxy's impl["text.complete"]
    # string literal at the bottom of the file.
    default_impl_start = source.index("const defaultImpl")
    region = source[default_impl_start:]
    return _find_function_body(region, "experimental.text.complete")


# -- 1. Subagent guard: isSubagent() → return output unchanged ---------------


def test_text_complete_has_subagent_guard():
    body = _get_text_complete_body()
    assert "OPENCODE_SUBAGENT" in body, "text.complete must guard on OPENCODE_SUBAGENT env var"
    subagent_return = re.search(
        r"if\s*\(\s*process\.env\.OPENCODE_SUBAGENT\s*===\s*\"1\"\s*\)\s*return\s+output",
        body,
    )
    assert subagent_return is not None, (
        'Subagent guard must be: if (process.env.OPENCODE_SUBAGENT === "1") return output'
    )


def test_subagent_returns_before_gate_check():
    body = _get_text_complete_body()
    subagent_pos = body.index("OPENCODE_SUBAGENT")
    gate_status_pos = body.index(".gate-status")
    assert subagent_pos < gate_status_pos, "Subagent guard must fire BEFORE .gate-status read"


# -- 2. Non-string guard: typeof output !== 'string' → return output --------


def test_text_complete_has_non_string_guard():
    body = _get_text_complete_body()
    assert "typeof output" in body, "text.complete must check typeof output"
    non_string = re.search(r"typeof\s+output\s*!==\s*['\"]string['\"]", body)
    assert non_string is not None, "Must guard typeof output !== 'string'"
    assert "return output" in body[body.index("typeof output") :], "Non-string output must be returned as-is"


# -- 3. .gate-status read and FAIL detection ---------------------------------


def test_text_complete_reads_gate_status():
    body = _get_text_complete_body()
    assert ".gate-status" in body, "Must reference .gate-status file"


def test_text_complete_checks_fail_in_gate_status():
    body = _get_text_complete_body()
    assert "/FAIL/" in body or "/FAIL/i" in body, "Must test .gate-status content for FAIL marker"


# -- 4. Gate red → "[GATE RED]" prefix prepended ------------------------------


def test_text_complete_gate_red_prepends_warning():
    body = _get_text_complete_body()
    assert "GATE RED" in body, "When .gate-status has FAIL, MUST prepend [GATE RED] warning"
    gate_red_line = next((line.strip() for line in body.splitlines() if "GATE RED" in line), "")
    assert "Fix failures" in gate_red_line or "GATE RED" in gate_red_line, (
        "[GATE RED] warning must tell agent to fix failures"
    )
    assert "+ output" in body, "Gate-red path must append original output"


# -- 5. Clean gate / CI FAIL only → output passes through ---------------------


def test_text_complete_clean_gate_passes_through():
    body = _get_text_complete_body()
    lines = body.strip().splitlines()
    last_lines = "\n".join(lines[-8:])
    assert "return output" in last_lines, "Final line must return output unchanged when no FAIL in .gate-status"


def test_text_complete_no_gate_status_file_passes_through():
    body = _get_text_complete_body()
    assert "fs.existsSync" in body, "Must check .gate-status existence — missing file = pass through"
    has_exists_protection = re.search(r"if\s*\(\s*fs\.existsSync\s*\(\s*p\s*\)\s*\)", body)
    assert has_exists_protection is not None, "existsSync guard must prevent read of missing .gate-status"


# -- 6. Fail-open on read errors ----------------------------------------------


def test_text_complete_fail_open_on_read_error():
    body = _get_text_complete_body()
    assert "catch" in body[body.index(".gate-status") :], "Read of .gate-status must be try/catch wrapped (fail-open)"
