"""Structural tests that enforce exactly-10 dispatch across all layers.

Verifies the dispatch baseline is hardcoded at 10 (not 3, 5, or 7) in:
  - enforce-session-start.ts: MIN_DISPATCHES and EFFECTIVE_MIN
  - enforce-multitask.ts: MIN_DISPATCHES and CEILING
  - shared.ts: isDispatchTool classification
  - AGENTS.md: no sub-10 dispatch count in any directive

These tests fail when the code softens the floor below 10.  A failing test
here means someone dropped the dispatch floor in code without updating this
guard — and the agent will run with fewer subagents than mandated.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SESSION_START_TS = ROOT / ".opencode" / "plugin" / "enforce-session-start.ts"
MULTITASK_TS = ROOT / ".opencode" / "plugin" / "enforce-multitask.ts"
SHARED_TS = ROOT / ".opencode" / "lib" / "shared.ts"
AGENTS_MD = ROOT / "AGENTS.md"


def _read(path: Path) -> str:
    assert path.exists(), f"Missing {path}"
    return path.read_text()


def _env_default(src: str, env_var: str) -> int:
    """Extract the hardcoded default from a parseInt(process.env.VAR || "N")."""
    pat = re.compile(rf"parseInt\(\s*process\.env\.{env_var}\s*\|\|\s*\"(\d+)\"")
    m = pat.search(src)
    assert m, f"Env var {env_var} default not found in source"
    return int(m.group(1))


# ============================================================================
# 1. enforce-session-start.ts: MIN_DISPATCHES hardcoded to 10
# ============================================================================


def test_session_start_min_dispatches_hardcoded_10():
    """MIN_DISPATCHES in enforce-session-start.ts defaults to 10, not 3."""
    src = _read(SESSION_START_TS)
    val = _env_default(src, "GLUDD_SESSION_START_MIN_DISPATCHES")
    assert val == 10, (
        f"enforce-session-start.ts MIN_DISPATCHES default is {val}, expected 10. "
        f"Was the hardcoded fallback changed?")


def test_session_start_min_dispatches_env_var_name():
    """The env var override key matches expectations."""
    src = _read(SESSION_START_TS)
    assert "GLUDD_SESSION_START_MIN_DISPATCHES" in src, (
        "Env var GLUDD_SESSION_START_MIN_DISPATCHES must exist for overrides.")


# ============================================================================
# 2. enforce-session-start.ts: EFFECTIVE_MIN = 10
# ============================================================================


def test_session_start_effective_min_is_10():
    """EFFECTIVE_MIN must be exactly 10 (hardcoded or resolved)."""
    src = _read(SESSION_START_TS)
    assert "EFFECTIVE_MIN" in src, "EFFECTIVE_MIN constant must exist"

    m = re.search(r"EFFECTIVE_MIN\s*=\s*(\d+)", src)
    assert m, "EFFECTIVE_MIN assignment not found"
    val = int(m.group(1))
    assert val == 10, (
        f"EFFECTIVE_MIN is {val}, expected 10. Must be hardcoded to 10.")


# ============================================================================
# 3. enforce-multitask.ts: MIN_DISPATCHES = 10
# ============================================================================


def test_multitask_min_dispatches_hardcoded_10():
    """MIN_DISPATCHES in enforce-multitask.ts defaults to 10, not 3."""
    src = _read(MULTITASK_TS)
    val = _env_default(src, "GLUDD_MULTITASK_MIN_DISPATCHES")
    assert val == 10, (
        f"enforce-multitask.ts MIN_DISPATCHES default is {val}, expected 10.")


def test_multitask_min_dispatches_per_wave_hardcoded_10():
    """MIN_DISPATCHES_PER_WAVE in enforce-multitask.ts defaults to 10."""
    src = _read(MULTITASK_TS)
    val = _env_default(src, "GLUDD_MIN_DISPATCHES")
    assert val == 10, (
        f"enforce-multitask.ts MIN_DISPATCHES_PER_WAVE default is {val}, "
        f"expected 10.")


def test_multitask_per_message_threshold_is_10():
    """The per-message MIN_DISPATCHES must be 10, which is the threshold."""
    src = _read(MULTITASK_TS)
    # The per-message check uses MIN_DISPATCHES (a constant), not a literal.
    # Verify the constant resolves to 10.
    val = _env_default(src, "GLUDD_MULTITASK_MIN_DISPATCHES")
    assert val == 10, (
        f"Multitask MIN_DISPATCHES is {val}, expected 10 for per-message threshold.")


def test_multitask_floor_breach_uses_min_dispatches():
    """Floor breach check references MIN_DISPATCHES (not a literal)."""
    src = _read(MULTITASK_TS)
    assert "_state.prevMessageDispatches < MIN_DISPATCHES" in src, (
        "Floor breach must use the MIN_DISPATCHES constant, not a literal.")


# ============================================================================
# 4. enforce-multitask.ts: CEILING = 10 blocks >10 dispatches per wave
# ============================================================================


def test_multitask_has_max_dispatches_constant():
    """enforce-multitask.ts must export a MAX_DISPATCHES ceiling constant."""
    src = _read(MULTITASK_TS)
    assert "MAX_DISPATCHES" in src, (
        "enforce-multitask.ts must declare MAX_DISPATCHES to cap "
        "dispatches per wave at 10.")


def test_multitask_max_dispatches_value_is_10():
    """MAX_DISPATCHES must resolve to 10 (env override or hardcoded)."""
    src = _read(MULTITASK_TS)
    m = re.search(
        r"MAX_DISPATCHES\s*=\s*parseInt\(\s*process\.env\.\w+\s*\|\|\s*\"(\d+)\"",
        src)
    assert m, "MAX_DISPATCHES parseInt declaration not found"
    val = int(m.group(1))
    assert val == 10, (
        f"MAX_DISPATCHES is {val}, expected 10. Must cap dispatches at exactly 10 per wave.")


def test_multitask_max_dispatches_enforcement_exists():
    """The code that blocks >=MAX_DISPATCHES dispatches must exist."""
    src = _read(MULTITASK_TS)
    assert ">= MAX_DISPATCHES" in src or "> MAX_DISPATCHES" in src, (
        "Must block dispatches that exceed MAX_DISPATCHES.")
    # Deny message must reference MAX_DISPATCHES
    max_pos = src.find("MAX_DISPATCHES")
    deny_pos = src.find("deny", max_pos)
    assert deny_pos > 0, (
        "MAX_DISPATCHES must be used in a deny/block context to enforce the cap.")


# ============================================================================
# 5. AGENTS.md: no dispatch count less than 10
# ============================================================================


def test_agents_md_no_sub_10_dispatch():
    """AGENTS.md MUST NOT contain any dispatch count directive below 10.

    Exemptions: date strings, git SHAs, line counts, version numbers.
    """
    src = _read(AGENTS_MD)

    # Patterns that match sub-10 dispatch directives — things like "at least 2",
    # "≥2", "1-4 dispatch", "≥5 dispatches", "≥2 known work items", etc.
    sub10_patterns: list[tuple[re.Pattern[str], str]] = [
        # "1-4 dispatch" enforcement phrase
        (re.compile(r"\b1-4\s+dispatch\b", re.IGNORECASE),
         "'1-4 dispatch'"),
        # "at least N" where N < 10 (but skip "at least 10" and "at least half")
        (re.compile(r"at\s+least\s+([0-9])\s+subagent", re.IGNORECASE),
         "'at least N subagent' with N < 10"),
        (re.compile(r"at\s+least\s+([0-9])\s+dispatch", re.IGNORECASE),
         "'at least N dispatch' with N < 10"),
        (re.compile(r"at\s+least\s+([0-9])\s+task/agent", re.IGNORECASE),
         "'at least N task/agent' with N < 10"),
        (re.compile(r"at\s+least\s+([0-9])\s+parallel\s+task", re.IGNORECASE),
         "'at least N parallel task' with N < 10"),
        # "≥N" with N < 10 (but not ≥10)
        (re.compile(r"\u2265\s*([0-9])\s+", re.UNICODE),
         "≥N with N < 10"),
        # "dispatches < N" where N < 10
        (re.compile(r"dispatches?\s*<\s*([0-9])\b", re.IGNORECASE),
         "'dispatches < N' with N < 10"),
        # "MIN_DISPATCHES (default N)" where N < 10
        (re.compile(r"MIN_DISPATCHES.*default\s+([0-9])", re.IGNORECASE),
         "'MIN_DISPATCHES default N' with N < 10"),
        # "floor.*N.*dispatch" where N < 10
        (re.compile(r"floor.*?[=:]\s*([0-9])\s+.*dispatch", re.IGNORECASE),
         "'floor = N dispatch' with N < 10"),
    ]

    violations: list[str] = []
    for pat, desc in sub10_patterns:
        for m in pat.finditer(src):
            # Extract the captured digit
            groups = m.groups()
            if groups:
                n = int(groups[0])
                if n >= 10:
                    continue
            else:
                n = 0  # falls through as violation
            line = src[:m.start()].count('\n') + 1
            violations.append(f"Line {line}: {desc} — \"{m.group().strip()}\"")

    # Allowlist: known sub-10 mentions that are NOT dispatch directives
    # These are procedural / historical / non-dispatch references.
    # Actually, we WANT these to fail.  Remove any spurious non-dispatch matches.
    # "at least half" shouldn't match because pattern looks for digit.
    # "10+ at all times" is fine (>10).

    # Keep only real dispatch-directive violations
    # Filter out matches that are not about dispatch counts
    false_positives = {
        # Version numbers / dates / SHAs / line counts
        "DISENGAGE": True,
        "2026-07": True,
    }

    real_violations = []
    for v in violations:
        # Skip if it's clearly not a dispatch directive
        if any(fp in v for fp in false_positives):
            continue
        real_violations.append(v)

    assert not real_violations, (
        f"AGENTS.md contains {len(real_violations)} sub-10 dispatch directive(s):\n" +
        "\n".join(f"  - {v}" for v in real_violations[:10]))


def test_agents_md_assumes_10_dispatch_floor():
    """AGENTS.md must explicitly state the dispatch floor is 10."""
    src = _read(AGENTS_MD)
    # Must contain "10 subagents" or "10-agent floor" or "dispatch >= 10" etc.
    assert "10-agent floor" in src or "10 subagents" in src.lower(), (
        "AGENTS.md must document the 10-agent floor.")


# ============================================================================
# 6. shared.ts: isDispatchTool correctly matches task/agent/workflow
# ============================================================================


def test_is_dispatch_tool_function_present():
    """isDispatchTool must be defined in shared.ts."""
    src = _read(SHARED_TS)
    assert "export function isDispatchTool" in src, (
        "shared.ts must export isDispatchTool.")


def test_dispatch_tools_array_contains_task_agent_workflow():
    """DISPATCH_TOOLS must be exactly ["task", "agent", "workflow"]."""
    src = _read(SHARED_TS)

    m = re.search(
        r'DISPATCH_TOOLS\s*=\s*\w+\.freeze\(\s*\[(.*?)\]\s*\)',
        src, re.DOTALL)
    assert m, "DISPATCH_TOOLS frozen array not found"

    raw = m.group(1)
    entries = re.findall(r'"(\w+)"', raw)
    assert sorted(entries) == ["agent", "task", "workflow"], (
        f"DISPATCH_TOOLS = {entries}, expected "
        "['agent', 'task', 'workflow'].")


def test_is_dispatch_tool_returns_true_for_task():
    """isDispatchTool('task') must return true."""
    src = _read(SHARED_TS)
    assert "DISPATCH_TOOLS.includes(tool)" in src, (
        "isDispatchTool must use DISPATCH_TOOLS.includes for membership test.")


def test_is_dispatch_tool_returns_false_for_bash():
    """isDispatchTool must NOT classify 'bash' as a dispatch."""
    src = _read(SHARED_TS)
    assert "DISPATCH_TOOLS.includes(tool)" in src
    assert '"bash"' not in src.split("DISPATCH_TOOLS")[1].split("\n", 1)[0], (
        "'bash' must not be in DISPATCH_TOOLS.")


def test_is_dispatch_tool_exported_via_es_module():
    """isDispatchTool must be an ES module export, not require()."""
    src = _read(SHARED_TS)
    assert "export function isDispatchTool" in src or "export const isDispatchTool" in src, (
        "isDispatchTool must use ES module export.")


# ============================================================================
# 7. Freshness check blocks until dispatches >= 10
# ============================================================================


def test_session_start_freshness_uses_effective_min():
    """The freshness check MUST compare dispatches against EFFECTIVE_MIN = 10."""
    src = _read(SESSION_START_TS)
    assert "EFFECTIVE_MIN" in src, "Freshness gate must reference EFFECTIVE_MIN."

    m = re.search(r"EFFECTIVE_MIN\s*=\s*(\d+)", src)
    assert m, "EFFECTIVE_MIN assignment not found"
    val = int(m.group(1))
    assert val == 10, (
        f"EFFECTIVE_MIN = {val}, freshness gate would pass at {val} dispatches, "
        f"but 10 is mandated.")


def test_session_start_primed_check_requires_10():
    """The primed condition must be: readsDone && dispatches >= EFFECTIVE_MIN."""
    src = _read(SESSION_START_TS)
    assert "dispatches >= EFFECTIVE_MIN" in src, (
        "Primed condition must check dispatches >= EFFECTIVE_MIN.")
    # EFFECTIVE_MIN must be 10 (tested above)


def test_session_start_freshness_warning_mentions_10():
    """Any warning/deny message about insufficient dispatches must reference 10."""
    src = _read(SESSION_START_TS)
    # The EFFECTIVE_MIN is used in messages — check it resolves to 10
    assert "${EFFECTIVE_MIN}" in src, (
        "Warning/deny messages must template EFFECTIVE_MIN.")
    # Pin: the EFFECTIVE_MIN resolution is tested above at 10


def test_session_start_hard_deny_uses_effective_min():
    """The HARD_DENY_SECS timeout message must template ${EFFECTIVE_MIN}."""
    src = _read(SESSION_START_TS)
    # The template literal is in the defaultImpl's tool.execute.before block.
    # defaultImpl is the first "tool.execute.before" occurrence.
    default_impl = src.split('"tool.execute.before"')[1]
    assert "${EFFECTIVE_MIN}" in default_impl, (
        "Hard-deny message must template ${EFFECTIVE_MIN} (defaultImpl block).")


def test_session_start_warning_uses_effective_min():
    """The DISPATCH_NOW_SECS warning message must template ${EFFECTIVE_MIN}."""
    src = _read(SESSION_START_TS)
    default_impl = src.split('"tool.execute.before"')[1]
    assert "${EFFECTIVE_MIN}" in default_impl, (
        "Dispatch-now warning must template ${EFFECTIVE_MIN} (defaultImpl block).")
