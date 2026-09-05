"""Runtime integration tests for enforce-floor.ts anti-grinding enforcement.

Invokes enforce-floor.ts tool.execute.before hook multiple times within a single
Node process to verify:
1. 4+ consecutive non-dispatch calls trigger a floor-breach block.
2. A dispatch call resets the streak counter.
3. Read-only tools (read/grep/glob) do not increment the grind streak.
4. GLUDD_FLOOR_ENFORCE=0 disables all enforcement.

Uses a custom inline Node harness because the multi-call streak accumulation
requires a single process — the standard hook_plugin_harness.mjs spawns a fresh
process for each invocation, losing module-level streak state between calls.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-floor.ts"
SHARED_PATH = ROOT / ".opencode" / "lib" / "shared.ts"

_NODE_PATH: str | None = shutil.which("node")
_NODE_VER: tuple[int, int] | None = None
if _NODE_PATH is not None:
    try:
        proc = subprocess.run([_NODE_PATH, "--version"], capture_output=True, text=True, timeout=5)
        m = re.match(r"v(\d+)\.(\d+)", proc.stdout.strip())
        if m is not None:
            _NODE_VER = (int(m.group(1)), int(m.group(2)))
    except Exception:
        pass

NODE_OK = _NODE_VER is not None and _NODE_VER >= (22, 6)
SKIP_REASON = (
    f"node --experimental-strip-types needs node >= 22.6; "
    f"found {'.'.join(str(p) for p in _NODE_VER) if _NODE_VER else 'no node'}"
)

if not NODE_OK:
    pytest.skip(SKIP_REASON, allow_module_level=True)

_NODE_BIN: str = _NODE_PATH  # type: ignore[assignment]


def _clean_base_env() -> dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("GLUDD_") and not k.startswith("CLAUDE_") and not k.startswith("OPENCODE_")
    }


def _run_grind_test_harness(
    tmp_path: Path,
    calls: list[dict[str, Any]],
    extra_env: dict[str, str] | None = None,
    *,
    pending_items: int = 1,
) -> dict[str, Any]:
    # openWorkExists() checks TASKS.md for unchecked items. Without pending work,
    # the plugin resets _streakCount to 0. Provide an unchecked item so the floor
    # breach fires when the streak exceeds MAX_STREAK.
    (tmp_path / "TASKS.md").write_text(
        "".join(f"- [ ] Grind test pending work item {index}\n" for index in range(pending_items))
    )
    (tmp_path / "opencode.json").write_text("{}")
    (tmp_path / "Makefile").write_text("test:\n\t@true\n")

    tmp: dict[str, str] = {
        "streak": str(tmp_path / "streak.json"),
        "stop": str(tmp_path / "stop.json"),
        "session": str(tmp_path / "session.json"),
        "grind": str(tmp_path / "grind.json"),
        "project": str(tmp_path),
    }

    env_lines = "\n".join(
        f"process.env.{k} = {json.dumps(v)};"
        for k, v in {
            "GLUDD_PROJECT_ROOT": tmp["project"],
            "GLUDD_STREAK_FILE": tmp["streak"],
            "GLUDD_STOP_STATE_FILE": tmp["stop"],
            "GLUDD_SESSION_STATE": tmp["session"],
            "GLUDD_READ_GRIND_FILE": tmp["grind"],
            "GLUDD_MESSAGE_BOUNDARY_MS": "100",
        }.items()
    )

    if extra_env:
        env_lines += "\n" + "\n".join(f"process.env.{k} = {json.dumps(v)};" for k, v in extra_env.items())

    test_script = tmp_path / "grind_test_harness.mjs"
    script = f"""
{env_lines}

try {{
    const mod = await import({json.dumps(str(PLUGIN_PATH))});
    let plugin = mod.default;
    if (typeof plugin === "function") {{
        plugin = await plugin({{}});
    }}
    const hooks = plugin && plugin.hooks ? plugin.hooks : plugin;
    const hook = hooks["tool.execute.before"];

    const calls = {json.dumps(calls)};
    const results = [];
    const messages = [];
    const durations = [];
    for (const c of calls) {{
        const startedAt = performance.now();
        const r = await hook(c.input || {{}}, c.output || {{}});
        durations.push(performance.now() - startedAt);
        results.push(r ? r.permissionDecision : null);
        messages.push(r ? r.message : null);
    }}
    process.stdout.write(JSON.stringify({{ results, messages, durations }}));
}} catch (e) {{
    console.error("GRIND_TEST_ERROR " + (e && e.stack ? e.stack : String(e)));
    process.exit(1);
}}
"""
    test_script.write_text(script)

    env = _clean_base_env()
    proc = subprocess.run(
        [_NODE_BIN, "--experimental-strip-types", str(test_script)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        raise RuntimeError(f"Node test harness failed (exit {proc.returncode}): {stderr}")
    result: dict[str, Any] = json.loads(proc.stdout)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# STRUCTURAL TESTS — anti-grinding source code patterns
# ══════════════════════════════════════════════════════════════════════════════


class TestAntiGrindStructural:
    """Verify the anti-grinding enforcement logic exists in the plugin source."""

    @staticmethod
    def _src() -> str:
        return PLUGIN_PATH.read_text()

    def test_streak_count_incremented_for_non_dispatch(self) -> None:
        src = self._src()
        assert "_streakCount++" in src, "_streakCount MUST be incremented for non-dispatch calls"

    def test_read_tools_do_not_increment_streak_count(self) -> None:
        src = self._src()
        read_idx = src.find("if (isReadTool(tool))")
        assert read_idx > 0, "Read tools MUST be handled separately"
        read_section = src[read_idx : read_idx + 200]
        assert "_readStreak++" in read_section, "Read tools MUST increment _readStreak"
        assert "_streakCount++" not in read_section, "Read tools MUST NOT increment _streakCount"

    def test_dispatch_resets_streak_count_to_zero(self) -> None:
        src = self._src()
        dispatch_idx = src.find("if (isDispatchTool(tool))")
        assert dispatch_idx > 0
        after = src[dispatch_idx : dispatch_idx + 300]
        assert "_streakCount = 0" in after, "Dispatch MUST reset _streakCount to 0"

    def test_floor_breach_gated_on_streak_gt_max(self) -> None:
        src = self._src()
        assert "_streakCount <= effectiveMax" in src, "Floor breach MUST check _streakCount > effectiveMax"

    def test_floor_breach_returns_permission_deny(self) -> None:
        src = self._src()
        breach_idx = src.find("AGENT-FLOOR BREACH")
        assert breach_idx > 0
        # The deny return is in the hook body at line ~679; _buildFloorBreachBlock
        # (which contains the message text) is at line ~358. They are far apart.
        # Search for deny returns enclosing calls to _buildFloorBreachBlock.
        search_start = 0
        found_deny = False
        while True:
            deny_idx = src.find('permissionDecision: "deny"', search_start)
            if deny_idx < 0:
                break
            # Check if this deny is near a _buildFloorBreachBlock call
            nearby = src[max(0, deny_idx - 100) : deny_idx + 200]
            if "_buildFloorBreachBlock" in nearby:
                found_deny = True
                break
            search_start = deny_idx + 1
        assert found_deny, "Floor breach MUST return permissionDecision: deny"

    def test_max_streak_default_is_two(self) -> None:
        src = self._src()
        assert "const MAX_STREAK = 2" in src, (
            "MAX_STREAK default MUST be 2 (block fires on 3rd+ consecutive non-dispatch)"
        )

    def test_streak_reset_on_disengage(self) -> None:
        src = self._src()
        disengage_resets = src.count("_streakCount = 0")
        assert disengage_resets >= 2, f"_streakCount resets expected >=2, found {disengage_resets}"

    def test_block_message_includes_streak_count_and_floor(self) -> None:
        src = self._src()
        block_fn_idx = src.find("function _buildFloorBreachBlock")
        assert block_fn_idx > 0
        fn_body = src[block_fn_idx : block_fn_idx + 1000]
        assert "streakCount" in fn_body
        assert "FLOOR" in fn_body
        assert "TARGET" in fn_body

    def test_read_grinding_block_at_15_reads_60_seconds(self) -> None:
        src = self._src()
        assert "_readStreak > 15" in src, "Read-grind deny MUST trigger at >15 reads"
        grind_idx = src.find("_readStreak > 15")
        after = src[grind_idx : grind_idx + 80]
        assert "msSinceDispatch > 60_000" in after, "Read-grind deny MUST require >60s since last dispatch"

    def test_compulsive_check_commands_are_blocked(self) -> None:
        src = self._src()
        assert "COMPULSIVE_CHECK_RE" in src

    def test_session_start_read_grinding_block(self) -> None:
        src = self._src()
        assert "SESSION_START_READ_DENY = 6" in src
        assert "SESSION-START READ-GRINDING" in src

    def test_pressure_release_skips_floor_breach(self) -> None:
        src = self._src()
        pressure_idx = src.find("pressureRelief")
        assert pressure_idx > 0
        before_streak = src[src.find("_streakCount++") - 400 : src.find("_streakCount++")]
        assert "pressureRelief" in before_streak, "Pressure-release check MUST precede floor-breach logic"


# ══════════════════════════════════════════════════════════════════════════════
# RUNTIME INTEGRATION TESTS — actual hook invocation
# ══════════════════════════════════════════════════════════════════════════════


class TestAntiGrindRuntime:
    """Invoke enforce-floor.ts tool.execute.before with a custom multi-call
    Node harness to verify streak-based blocking behavior."""

    def test_first_non_dispatch_call_is_allowed(self, tmp_path: Path) -> None:
        """A single non-dispatch call from a fresh process should be allowed."""
        result = _run_grind_test_harness(
            tmp_path,
            calls=[{"tool": "edit", "input": {"tool": "edit"}, "output": {}}],
        )
        assert result["results"][0] is None, "First non-dispatch call MUST be allowed"

    def test_four_consecutive_non_dispatch_calls_triggers_block(self, tmp_path: Path) -> None:
        """After MAX_STREAK (2) non-dispatch calls, call 3 and 4 should be denied."""
        result = _run_grind_test_harness(
            tmp_path,
            calls=[
                {"tool": "edit", "input": {"tool": "edit"}, "output": {}},
                {"tool": "write", "input": {"tool": "write"}, "output": {}},
                {"tool": "edit", "input": {"tool": "edit"}, "output": {}},
                {"tool": "write", "input": {"tool": "write"}, "output": {}},
            ],
        )
        assert result["results"][0] is None, "Call 1 MUST be allowed (streak=1)"
        assert result["results"][1] is None, "Call 2 MUST be allowed (streak=2)"
        assert result["results"][2] == "deny", "Call 3 MUST be denied (streak=3 > MAX_STREAK=2)"
        assert result["results"][3] == "deny", "Call 4 MUST be denied (streak=4 > MAX_STREAK=2)"

    def test_dispatch_resets_streak(self, tmp_path: Path) -> None:
        """A dispatch call resets the streak counter to 0."""
        result = _run_grind_test_harness(
            tmp_path,
            calls=[
                {"tool": "edit", "input": {"tool": "edit"}, "output": {}},
                {"tool": "edit", "input": {"tool": "edit"}, "output": {}},
                {"tool": "task", "input": {"tool": "task"}, "output": {}},
                {"tool": "edit", "input": {"tool": "edit"}, "output": {}},
            ],
        )
        assert result["results"][0] is None, "Call 1 (edit) MUST be allowed"
        assert result["results"][1] is None, "Call 2 (edit) MUST be allowed"
        assert result["results"][2] is None, "Call 3 (dispatch) MUST be allowed"
        assert result["results"][3] is None, "Call 4 (edit after dispatch reset) MUST be allowed"

    def test_dispatch_runtime_is_not_an_inter_message_gap(self, tmp_path: Path) -> None:
        """Time spent inside dispatch bookkeeping must not create a message boundary."""
        result = _run_grind_test_harness(
            tmp_path,
            calls=[
                {"tool": "edit", "input": {"tool": "edit"}, "output": {}},
                {"tool": "task", "input": {"tool": "task"}, "output": {}},
                {"tool": "edit", "input": {"tool": "edit"}, "output": {}},
            ],
            extra_env={"GLUDD_MESSAGE_BOUNDARY_MS": "20"},
            # Keep the dispatch preflight decisively above the 20 ms boundary
            # even on fast runners; the assertion must exercise a real crossing.
            pending_items=250_000,
        )
        assert result["durations"][1] > 20, result
        assert result["results"] == [None, None, None], result

    def test_read_only_tools_do_not_increment_streak(self, tmp_path: Path) -> None:
        """Read-only tools (read/grep/glob) do NOT trigger the floor breach."""
        result = _run_grind_test_harness(
            tmp_path,
            calls=[
                {"tool": "read", "input": {"tool": "read"}, "output": {}},
                {"tool": "read", "input": {"tool": "read"}, "output": {}},
                {"tool": "grep", "input": {"tool": "grep"}, "output": {}},
                {"tool": "glob", "input": {"tool": "glob"}, "output": {}},
                {"tool": "read", "input": {"tool": "read"}, "output": {}},
            ],
        )
        for i, r in enumerate(result["results"], 1):
            assert r is None, f"Call {i} (read-only) MUST be allowed, got {r}"

    def test_mixed_read_then_edit_triggers_block(self, tmp_path: Path) -> None:
        """Reads interspersed with edits do not protect against the block."""
        result = _run_grind_test_harness(
            tmp_path,
            calls=[
                {"tool": "edit", "input": {"tool": "edit"}, "output": {}},
                {"tool": "read", "input": {"tool": "read"}, "output": {}},
                {"tool": "edit", "input": {"tool": "edit"}, "output": {}},
                {"tool": "edit", "input": {"tool": "edit"}, "output": {}},
            ],
        )
        assert result["results"][0] is None, "Call 1 (edit) MUST be allowed"
        assert result["results"][1] is None, "Call 2 (read) MUST be allowed"
        assert result["results"][2] is None, "Call 3 (edit, streak=2) MUST be allowed"
        assert result["results"][3] == "deny", "Call 4 (edit, streak=3) MUST be denied"

    def test_single_dispatch_resets_everything(self, tmp_path: Path) -> None:
        """After hitting floor breach, a dispatch resets and next call is clean."""
        result = _run_grind_test_harness(
            tmp_path,
            calls=[
                {"tool": "write", "input": {"tool": "write"}, "output": {}},
                {"tool": "edit", "input": {"tool": "edit"}, "output": {}},
                {
                    "tool": "bash",
                    "input": {"tool": "bash"},
                    "output": {"args": {"command": "make test"}},
                },
                {"tool": "task", "input": {"tool": "task"}, "output": {}},
                {"tool": "edit", "input": {"tool": "edit"}, "output": {}},
            ],
        )
        assert result["results"][0] is None, "Call 1 (write) MUST be allowed"
        assert result["results"][1] is None, "Call 2 (edit) MUST be allowed"
        assert result["results"][2] == "deny", "Call 3 (bash) MUST be denied (streak=3)"
        assert result["results"][3] is None, "Call 4 (dispatch) MUST be allowed"
        assert result["results"][4] is None, "Call 5 (edit after dispatch reset) MUST be allowed"

    def test_env_var_disables_all_floor_enforcement(self, tmp_path: Path) -> None:
        """GLUDD_FLOOR_ENFORCE=0 MUST allow all calls even after many consecutive
        non-dispatch calls."""
        result = _run_grind_test_harness(
            tmp_path,
            calls=[{"tool": "edit", "input": {"tool": "edit"}, "output": {}}] * 10,
            extra_env={"GLUDD_FLOOR_ENFORCE": "0"},
        )
        for i, r in enumerate(result["results"], 1):
            assert r is None, f"Call {i} MUST be allowed when FLOOR_ENFORCE=0, got {r}"
