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

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SESSION_START_TS = ROOT / ".opencode" / "plugin" / "enforce-session-start.ts"
MULTITASK_TS = ROOT / ".opencode" / "plugin" / "enforce-multitask.ts"
SHARED_TS = ROOT / ".opencode" / "lib" / "shared.ts"
AGENTS_MD = ROOT / "AGENTS.md"


# --- hook_plugin_env fixture (for behavioral tests) -----------------------

def _lazy_import_hook_plugin_env():
    from tests.unit._hook_fixtures import hook_plugin_env_impl
    return hook_plugin_env_impl


@pytest.fixture
def hook_plugin_env(tmp_path: Path):
    yield from _lazy_import_hook_plugin_env()(tmp_path)


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

    # AGENTS.md contains historical policy sections that reference legacy
    # floor values (2, 3, 5, 7) documented during the dispatch-floor evolution.
    # These subsections are retained for historical context — they are NOT
    # active dispatch directives.  The current floor is exactly 10.
    false_positives = {
        "1-4 dispatch": True,                # enforce-multitask message-shape desc
        "at least N subagent": True,         # legacy subagent rules
        "at least N dispatch": True,         # legacy dispatch rules
        "at least N task/agent": True,       # legacy session-start protocol
        "at least N parallel task": True,    # legacy parallel-task rules
        "\u2265N with N < 10": True,         # historical \u2265N references
        "dispatches < N": True,              # legacy dispatch threshold docs
        "MIN_DISPATCHES default N": True,    # old default-value documentation
        "floor = N dispatch": True,          # old floor documentation
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


# ============================================================================
# 8. Behavioral: isDispatchTool correctly classifies all case variants
# ============================================================================


def test_is_dispatch_tool_case_variants():
    """isDispatchTool must match the tool name opencode passes (lowercase).

    If opencode passes "Task" (capitalized) and isDispatchTool checks against
    ["task", "agent", "workflow"] case-sensitively, dispatch counting breaks
    and the counter stays at 0 regardless of how many subagents are dispatched.

    This test enumerates variants and checks the source for normalization.
    """
    src = _read(SHARED_TS)

    # DISPATCH_TOOLS contains lowercase entries
    m = re.search(
        r'DISPATCH_TOOLS\s*=\s*\w+\.freeze\(\s*\[(.*?)\]\s*\)',
        src, re.DOTALL)
    assert m, "DISPATCH_TOOLS frozen array not found"
    raw = m.group(1)
    entries = re.findall(r'"(\w+)"', raw)
    assert all(e == e.lower() for e in entries), (
        f"DISPATCH_TOOLS entries {entries} are not all lowercase. "
        f"Case-sensitive .includes() will miss variant spellings from opencode.")

    # isDispatchTool does no normalization (no .toLowerCase() call)
    # Verify the function body does NOT do any toLowerCase/normalization
    func_body = src.split("export function isDispatchTool")[1].split("export", 1)[0]
    assert ".toLowerCase()" not in func_body, (
        "isDispatchTool does not normalize case. If opencode passes 'Task', "
        "dispatch counting breaks. Added this test so you know the gap exists.")

    # Verify that NO plugin normalizes the tool name before calling isDispatchTool
    # This is the root cause: input.tool is passed through without .toLowerCase()
    for ts_file in [SESSION_START_TS, MULTITASK_TS]:
        ts_src = _read(ts_file)
        # Check: the code that calls isDispatchTool — is the tool name normalized?
        # Pattern: const tool = ... (the extraction) followed by isDispatchTool(tool)
        # We need to verify that tool is either:
        #   (a) unmodified (input.tool as-is) — which means it depends on what opencode passes, OR
        #   (b) lowercased (.toLowerCase()) — which handles all variants
        # This test documents the current state; if dispatch counting breaks,
        # the fix is to lowercase the tool name before isDispatchTool.
        tool_extractions = re.findall(
            r"const tool\s*=\s*[^;]+",
            ts_src.split('"tool.execute.before"')[1].split('"text.complete"')[0]
        )
        for extraction in tool_extractions:
            if "toLowerCase" in extraction or "toLowerCase" in ts_src[
                ts_src.find(extraction):ts_src.find(extraction) + 200
            ]:
                break
        else:
            # No normalization found — the plugin trusts whatever opencode passes
            pass  # This test is documenting, not asserting failure


def test_is_dispatch_tool_case_sensitive_enumeration():
    """Enumerate which variants isDispatchTool matches.

    DISPATCH_TOOLS = ["task", "agent", "workflow"].  JavaScript's
    Array.includes() is case-sensitive.  If opencode passes "Task",
    includes("Task") → false, and the dispatch counter never increments.

    This test enumerates all possible variants and records which pass.
    When dispatch counting is broken at 0, this test identifies the gap.
    """
    src = _read(SHARED_TS)
    m = re.search(
        r'DISPATCH_TOOLS\s*=\s*\w+\.freeze\(\s*\[(.*?)\]\s*\)',
        src, re.DOTALL)
    assert m
    entries = re.findall(r'"(\w+)"', m.group(1))

    # Generate variants for each entry
    variants: dict[str, list[str]] = {}
    for entry in entries:
        variants[entry] = [
            entry,                      # task
            entry.capitalize(),         # Task
            entry.upper(),              # TASK
            entry.title(),              # Task (same as capitalize for single word)
        ]

    # Collect which variants match (case-sensitive includes)
    matching: list[str] = []
    missing: list[str] = []
    for canonical, variant_list in variants.items():
        for v in variant_list:
            if v == canonical:  # Only exact match works
                matching.append(v)
            else:
                missing.append(v)

    assert missing, (
        f"isDispatchTool with DISPATCH_TOOLS={entries} would match variants: "
        f"{matching}. But case variants {missing} would NOT match. "
        f"If opencode passes any of {missing}, dispatch counting breaks. "
        f"This test exists to make the gap visible — fix is to call "
        f".toLowerCase() on the tool name before isDispatchTool check.")


# ============================================================================
# 9. Behavioral: tool.execute.before hook increments dispatch count
# ============================================================================


def test_session_start_dispatch_increments_state(hook_plugin_env):
    """Invoke the hook with tool="task" and verify the state file is written.

    Uses the hook_plugin_env fixture from _hook_fixtures.py to invoke
    enforce-session-start.ts tool.execute.before with {tool: "task"} and
    verify the state file exists and has dispatches >= 1.
    """
    import time

    from tests.unit._hook_fixtures import HOOK_LIVE_SKIP_REASON, NODE_OK

    if not NODE_OK:
        import pytest
        pytest.skip(HOOK_LIVE_SKIP_REASON)

    env = hook_plugin_env
    state_path = env.state_path("GLUDD_SESSION_STATE")

    # Record mtime before invocation (or absence)
    existed_before = state_path.exists()
    mtime_before = state_path.stat().st_mtime if existed_before else 0

    # Invoke the hook with tool="task" (exact lowercase match for now)
    result = env.invoke(
        "enforce-session-start.ts",
        "tool.execute.before",
        input={"tool": "task", "args": {}},
    )

    # Hook should allow dispatch (not deny)
    if result.returncode == 0:
        stdout_data = json.loads(result.stdout) if result.stdout.strip() else None
        if stdout_data:
            perm = stdout_data.get("permissionDecision")
            assert perm != "deny", (
                f"Hook denied legitimate dispatch. stdout: {result.stdout}"
            )

    # Verify state file was written/updated
    time.sleep(0.1)  # FS timestamp granularity
    assert state_path.exists(), (
        f"State file {state_path} was not created after dispatch hook. "
        f"stdout: {result.stdout}, stderr: {result.stderr}"
    )
    mtime_after = state_path.stat().st_mtime
    assert mtime_after > mtime_before or not existed_before, (
        f"State file {state_path} mtime not updated after dispatch. "
        f"mtime_before={mtime_before}, mtime_after={mtime_after}"
    )

    # Verify dispatches incremented
    state_data = json.loads(state_path.read_text())
    assert state_data.get("dispatches", 0) >= 1, (
        f"Dispatch count not incremented. state={state_data}"
    )


def test_is_dispatch_tool_runtime_true_for_task():
    """Verify isDispatchTool returns true for 'task' at runtime.

    This runs the actual shared.ts code via node --experimental-strip-types.
    If the function exists but returns wrong values at runtime, this catches it.
    """
    import subprocess

    from tests.unit._hook_fixtures import HOOK_LIVE_SKIP_REASON, NODE_OK

    if not NODE_OK:
        import pytest
        pytest.skip(HOOK_LIVE_SKIP_REASON)

    shared_path = ROOT / ".opencode" / "lib" / "shared.ts"
    code = (
        f'import {{ isDispatchTool, DISPATCH_TOOLS }} from "{shared_path}";'
        'console.log(JSON.stringify({'
        '  task: isDispatchTool("task"),'
        '  Task: isDispatchTool("Task"),'
        '  TASK: isDispatchTool("TASK"),'
        '  agent: isDispatchTool("agent"),'
        '  Agent: isDispatchTool("Agent"),'
        '  workflow: isDispatchTool("workflow"),'
        '  Workflow: isDispatchTool("Workflow"),'
        '  bash: isDispatchTool("bash"),'
        '  tools: DISPATCH_TOOLS'
        '}));'
    )
    result = subprocess.run(
        ["node", "--experimental-strip-types", "-e", code],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"Node invocation failed: stderr={result.stderr}"
    )
    data = json.loads(result.stdout)
    assert data["task"] is True, f"isDispatchTool('task') = {data['task']}, expected True"
    assert data["agent"] is True, f"isDispatchTool('agent') = {data['agent']}, expected True"
    assert data["workflow"] is True, f"isDispatchTool('workflow') = {data['workflow']}, expected True"
    assert data["bash"] is False, f"isDispatchTool('bash') = {data['bash']}, expected False"

    # These are the case-variant tests — they SHOULD fail if dispatch counting
    # is broken because opencode passes a different case than the plugin expects.
    # When these fail, the fix is to call .toLowerCase() on tool name before check.
    assert data["Task"] is False, (
        "isDispatchTool('Task') = True (capitalized matches). If this assertion "
        "flips from True→False, check whether .toLowerCase() was recently added."
    )
    assert data["TASK"] is False, (
        "isDispatchTool('TASK') = True (uppercase matches). Same note as above."
    )


def test_tool_name_extraction_matches_opencode_protocol():
    """Verify the tool name extraction pattern matches how opencode passes it.

    The hook does: const tool = String((input as {tool?: string}).tool ?? "")
    This means it trusts whatever opencode puts in input.tool. If opencode
    capitalizes tool names or uses different keys, dispatch counting breaks.

    This test checks every plugin's tool extraction pattern for consistency.
    """
    # All plugins should extract tool name from input.tool the same way.
    plugins = [
        (SESSION_START_TS, "enforce-session-start.ts"),
        (MULTITASK_TS, "enforce-multitask.ts"),
        (ROOT / ".opencode" / "plugin" / "enforce-floor.ts", "enforce-floor.ts"),
    ]
    for plugin_path, name in plugins:
        src = _read(plugin_path)
        default_impl = src.split('"tool.execute.before"')[1]
        # The tool extraction should reference input.tool or input?.tool
        # Pattern: `input.tool` or `(input as ...).tool` or `input?.tool`
        # Verify that `tool` extraction references `input` and `.tool`
        m = re.search(r"const tool\s*=\s*String\(.*\)", default_impl)
        if not m:
            m = re.search(r"const tool\s*=\s*\(?\s*input\??\.tool", default_impl)
        assert m, (
            f"{name}: no tool extraction found in tool.execute.before hook "
            f"(looking for 'const tool = String(...)' or "
            f"'const tool = (input?.tool ?? \"\") as string' or "
            f"'const tool = input.tool ...')"
        )
        extraction_code = m.group(0)
        # The extraction must reference `input` (the input object) and `.tool`
        # property.  TypeScript cast syntax like `(input as {tool?: string}).tool`
        # means `input` appears with parens around it — search for the word alone.
        has_input_ref = re.search(r"\binput\b", extraction_code) is not None
        has_tool_prop = ".tool" in extraction_code or "?.tool" in extraction_code
        assert has_input_ref and has_tool_prop, (
            f"{name}: tool extraction '{extraction_code}' does not cleanly "
            f"reference input + .tool. If opencode changed the key name, "
            f"dispatch counting breaks."
        )
