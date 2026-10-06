"""Regression tests for the canonical three-agent concurrency contract."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / ".opencode" / "lib" / "multitask_config.ts"
FLOOR_PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-floor.ts"
DELEGATE_PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-delegate.ts"
MULTITASK_PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-multitask.ts"
CONSUMERS = {
    ROOT / ".opencode" / "plugin" / "enforce-floor.ts": "../lib/multitask_config.ts",
    ROOT / ".opencode" / "plugin" / "enforce-floor-v2.ts": "../lib/multitask_config.ts",
    ROOT / ".opencode" / "plugin" / "enforce-delegate.ts": "../lib/multitask_config.ts",
    ROOT / ".opencode" / "plugin" / "enforce-session-start.ts": "../lib/multitask_config.ts",
    ROOT / ".opencode" / "plugin" / "impl" / "enforce_stop_impl.ts": "../../lib/multitask_config.ts",
}
GATE_SAFE_GENERATOR = ROOT / "scripts" / "gen_gate_safe_hook.py"
MAKEFILE = ROOT / "Makefile"
CLAUDE_SETTINGS = ROOT / ".claude" / "settings.json"
FLOOR_HOOKS = tuple(
    ROOT / ".claude" / "hooks" / name
    for name in (
        "agent_floor_dec.sh",
        "agent_floor_enforce_pretool.sh",
        "agent_floor_posttool.sh",
        "agent_floor_pretool.sh",
        "agent_floor_stop.sh",
        "agent_floor_userprompt.sh",
        "force_delegate_pretool.sh",
        "session_start_orchestrate.sh",
    )
)
MAINTHREAD_BUDGET_HOOK = ROOT / ".claude" / "hooks" / "mainthread_budget.sh"
SESSION_START_HOOK = ROOT / ".claude" / "hooks" / "session_start_orchestrate.sh"
DIRECTIVES_PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-directives.ts"
DIRECTIVES_IMPL = ROOT / ".opencode" / "plugin" / "impl" / "enforce_directives_impl.ts"
ADDITIVE_PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-additive-task.ts"
SESSION_PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-session-start.ts"
DEEP_SPEC_SKILL = ROOT / ".opencode" / "skill" / "deep-spec" / "SKILL.md"
ENFORCE_BOOTSTRAP_SKILL = ROOT / ".opencode" / "skills" / "enforce-bootstrap" / "SKILL.md"
BACKGROUND_TEST_RUNNER_SKILL = ROOT / ".opencode" / "skills" / "background-test-runner" / "SKILL.md"
ACTIVE_SKILL_FILES = tuple(sorted((ROOT / ".opencode" / "skill").glob("*/SKILL.md"))) + tuple(
    sorted((ROOT / ".opencode" / "skills").glob("*/SKILL.md"))
)
ACTIVE_CLAUDE_AGENT_PROMPTS = tuple(sorted((ROOT / ".claude" / "agents").glob("*.md")))
ACTIVE_POLICY_FILES = (
    ROOT / "AGENTS.md",
    ROOT / "CLAUDE.md",
    ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md",
    ROOT / "docs" / "specs" / "OPERATIONAL_DISCIPLINE_SPECS.md",
    *ACTIVE_SKILL_FILES,
    *ACTIVE_CLAUDE_AGENT_PROMPTS,
)


def _load_config(env: dict[str, str] | None = None) -> dict[str, int]:
    script = """
const cfg = await import(process.env.GLUDD_CONFIG_URL);
console.log(JSON.stringify({
  hardMax: cfg.HARD_MAX_DISPATCHES,
  minimum: cfg.MIN_DISPATCHES,
  maximum: cfg.MAX_DISPATCHES,
}));
"""
    runtime_env = os.environ.copy()
    runtime_env.update(env or {})
    runtime_env["GLUDD_CONFIG_URL"] = CONFIG.as_uri()
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        cwd=ROOT,
        env=runtime_env,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return json.loads(result.stdout)


def test_canonical_default_is_opt_in_with_three_as_the_ceiling() -> None:
    values = _load_config()
    assert values == {"hardMax": 3, "minimum": 0, "maximum": 3}


def test_reload_enforcement_uses_the_opt_in_bounded_floor() -> None:
    source = compose_makefile(MAKEFILE)
    recipe = source.split("reload-enforcement:", 1)[1].split("rearm-enforcement:", 1)[0]
    assert 'CLAUDE_AGENT_FLOOR:-0' in recipe
    assert 'CLAUDE_AGENT_CEILING:-3' in recipe
    assert 'if [ "$$CEILING" -gt 3 ]; then CEILING=3; fi' in recipe
    assert 'if [ "$$FLOOR" -gt "$$CEILING" ]; then FLOOR="$$CEILING"; fi' in recipe


def test_operator_overrides_cannot_exceed_three() -> None:
    values = _load_config(
        {
            "GLUDD_MIN_DISPATCHES": "99",
            "GLUDD_MULTITASK_MAX_DISPATCHES": "99",
        }
    )
    assert values == {"hardMax": 3, "minimum": 3, "maximum": 3}


def test_operator_overrides_keep_valid_lower_bounds() -> None:
    values = _load_config(
        {
            "GLUDD_MIN_DISPATCHES": "-9",
            "GLUDD_MULTITASK_MAX_DISPATCHES": "0",
        }
    )
    assert values == {"hardMax": 3, "minimum": 0, "maximum": 1}


def test_minimum_cannot_exceed_a_lower_operator_ceiling() -> None:
    values = _load_config(
        {
            "GLUDD_MIN_DISPATCHES": "3",
            "GLUDD_MULTITASK_MAX_DISPATCHES": "1",
        }
    )
    assert values == {"hardMax": 3, "minimum": 1, "maximum": 1}


def test_every_agent_count_consumer_uses_the_canonical_cap() -> None:
    for path, import_path in CONSUMERS.items():
        source = path.read_text()
        assert import_path in source, f"{path.name} must import the canonical config"
        assert "HARD_MAX_DISPATCHES" in source, f"{path.name} must consume the hard cap"
        assert "clampDispatchCount" in source, f"{path.name} must clamp runtime overrides"
        assert "const HARD_MAX_DISPATCHES =" not in source, (
            f"{path.name} must not define a second hard cap"
        )


def test_active_agent_instructions_do_not_advertise_a_ten_agent_floor() -> None:
    source = (ROOT / "AGENTS.md").read_text()
    forbidden = (
        "floor=10, ceiling=10, target=10",
        "sets the ceiling at 10",
        "floor at 10",
        "10-agent floor",
        "10 agents",
        "exactly 10",
        "9 other subagents",
        "≤5 agents",
    )
    for phrase in forbidden:
        assert phrase not in source, f"AGENTS.md still advertises stale concurrency: {phrase}"


def test_active_policy_specs_share_the_canonical_three_agent_contract() -> None:
    forbidden = (
        r"\b10-agent(?: dispatch)? floor\b",
        r"\bdispatch ceiling at 10\b",
        r"\bno more than 10 concurrent subagents\b",
        r"\bfloor\s*\(10\)",
        r"\bfloor must stay at 10\b",
        r"\b(?:all|the) 10 dispatch slots\b",
        r"\bfewer than 10 edit tasks\b",
        r"\bexactly 10 (?:task|agent|workflow|dispatch)",
        r"\b10 dispatches\b",
        r"\b10 subagents\b",
        r"\b10-wide wave\b",
        r"\bprimed at 10\b",
        r"\b0 subagents running = pipeline collapse\b",
        r"\bimmediate dispatch wave\b",
        r"\bno prose before dispatch\b",
        r"\bpoll from a subagent\b",
        r"\bpoll from subagents, not the main thread\b",
        r"\bkeep the subagent pool full\b",
        r"\bnever wait for a test result without dispatching other work\b",
        r"\bnever run long ops in the foreground\b",
        r"\b≤3 read calls\b",
        r"\bprocess results and re-dispatch\b",
        r"\bci should be checked by subagents\b",
        r"\bmust dispatch ci monitoring to a subagent\b",
        r"\bmust dispatch a replacement immediately\b",
        r"\bnext tool call must be a dispatch wave\b",
        r"\bsession-start dispatch requirement\b",
        r"\bre-dispatch replacement immediately\b",
        r"\bdispatch the next wave immediately\b",
        r"\bdispatch replacement subagents immediately\b",
        r"\bmust dispatch other subagents while a background gate is running\b",
        r"\bmaintain 10 agents at all times\b",
        r"\bdispatch floor enforcement must default to on\b",
        r"\bmust dispatch other agents\b",
        r"\bmust dispatch any main-thread operation\b",
        r"\bmust dispatch commit operations as subagent tasks\b",
        r"\bon the main thread is blocked\b",
        r"\bon the main thread are denied\b",
        r"\ball subagents in a wave must be dispatched in one message\b",
        r"\bvery next tool-call message.*must include at least 2\b",
        r"\bfirst dispatch wave.*session is in violation\b",
        r"\bonly valid response.*dispatch wave\b",
        r"\bpipeline must stay primed at 3 agents at all times\b",
        r"\bmust dispatch ≥3 subagents\b",
        r"\bunder-floor dispatch waves \(<3 dispatches\b",
        r"\bmax 3 file reads between results and dispatch\b",
        r"\bat most 2 consecutive zero-dispatch responses\b",
        r"\bexactly 1 task dispatch is a policy violation\b",
        r"\bmain thread blocks all subagent dispatch\b",
        r"\b0 subagents running = the entire pipeline drains\b",
        r"\bsession start second action is dispatch wave\b",
        r"\bmust contain ≥10 task/agent/workflow dispatches\b",
        r"\bin-flight (?:count )?drops below 10\b",
        r"\bstatus summary.*before first dispatch wave\b",
        r"\bprose summary before dispatch wave\b",
        r"\bprose analysis before dispatch wave\b",
        r"\bdispatch wave must be next action after backlog reads\b",
        r"\byield to the next dispatch wave\b",
        r"\bre-dispatch a replacement\b",
        r"\btime-to-dispatch timer\b",
        r"\bmessage with 0 dispatches after max_zero_streak.*policy violation\b",
        r"\bafter max_zero_streak \(2\) consecutive zero-dispatch responses\b",
        r"\bzero-dispatch streak counter blocks at max_zero_streak=2\b",
        r"\b(?:max(?:imum)?|cap) (?:of )?6 concurrent worktree agents\b",
        r"\bat most 5-6 worktree-isolated agents\b",
    )
    violations: list[str] = []
    for path in ACTIVE_POLICY_FILES:
        source = path.read_text().lower()
        for pattern in forbidden:
            if re.search(pattern, source):
                violations.append(f"{path.name}: {pattern}")
    assert not violations, "active policy still advertises stale concurrency:\n" + "\n".join(
        violations
    )


def test_generated_shell_hook_cannot_exceed_canonical_ceiling() -> None:
    source = GATE_SAFE_GENERATOR.read_text()
    assert 'HARD_CEILING=3' in source
    assert 'FLOOR="${CLAUDE_AGENT_FLOOR:-0}"' in source
    assert 'TARGET="${CLAUDE_AGENT_TARGET:-3}"' in source
    assert 'CEILING="${CLAUDE_AGENT_CEILING:-3}"' in source
    assert '"$FLOOR" -gt "$HARD_CEILING"' in source
    assert '"$TARGET" -gt "$HARD_CEILING"' in source
    assert '"$CEILING" -gt "$HARD_CEILING"' in source
    assert "*[!0-9]*) FLOOR=\"0\"" in source
    assert '"$FLOOR" -gt "$CEILING"' in source
    assert '"$TARGET" -gt "$CEILING"' in source


def test_active_harness_does_not_force_delegation_or_a_nonzero_floor() -> None:
    settings = json.loads(CLAUDE_SETTINGS.read_text())["env"]
    assert settings["CLAUDE_AGENT_FLOOR"] == "0"
    assert settings["GLUDD_MIN_DISPATCHES"] == "0"
    assert settings["GLUDD_FORCE_DELEGATE"] == "0"


def test_zero_floor_keeps_only_the_hard_dispatch_ceiling_active() -> None:
    source = FLOOR_PLUGIN.read_text()
    adaptive_return = source.index("if (eff.floor === 0)")
    minimum_enforcement = source.index("A completed assistant message containing dispatches")
    assert adaptive_return < minimum_enforcement
    assert "const dispatchCeiling = eff.waveWidth > 0" in source
    assert "_thisMessageDispatchCount >= dispatchCeiling" in source
    assert "const FLOOR = Math.min(" in source


def test_mainthread_delegation_block_requires_an_explicit_floor() -> None:
    source = DELEGATE_PLUGIN.read_text()
    budget = source[source.index("function mainthreadBudgetBefore") :]
    assert "if (FLOOR === 0) return null" in budget[:500]


def test_multitask_mutation_streak_requires_an_explicit_floor() -> None:
    source = MULTITASK_PLUGIN.read_text()
    streak = source.index("// --- Consecutive non-dispatch counter")
    prefix = source[max(0, streak - 500) : streak]
    assert "if (REQUIRED_DISPATCHES === 0)" in prefix


def test_every_floor_hook_defaults_to_opt_in() -> None:
    for path in FLOOR_HOOKS:
        source = path.read_text()
        assert 'CLAUDE_AGENT_FLOOR:-0' in source, f"{path.name} still defaults floor on"
        assert 'CLAUDE_AGENT_FLOOR:-3' not in source


def test_mainthread_budget_is_disabled_without_an_explicit_floor() -> None:
    source = MAINTHREAD_BUDGET_HOOK.read_text()
    assert 'FLOOR="${CLAUDE_AGENT_FLOOR:-0}"' in source
    assert '[ "$FLOOR" -gt 0 ] || exit 0' in source
    assert '[ "$live" -lt "$FLOOR" ] || exit 0' in source


def test_session_start_does_not_invent_filler_work() -> None:
    source = SESSION_START_HOOK.read_text()
    assert "THERE IS ALWAYS REAL WORK" not in source
    assert "dispatch read-only auditors/reviewers/proposers" not in source
    assert '[ "$FLOOR" -eq 0 ]' in source


def test_floor_plugin_has_no_stale_or_makework_prompts() -> None:
    source = FLOOR_PLUGIN.read_text()
    assert "keeps 9 productive tasks running" not in source
    assert "dispatch RESEARCH tasks" not in source
    assert "_dispatchPeak >= 5" not in source


def test_session_floor_tracker_uses_opt_in_canonical_floor() -> None:
    source = (ROOT / ".opencode" / "plugin" / "enforce-floor-v2.ts").read_text()
    assert 'process.env.GLUDD_DISPATCH_FLOOR ?? String(MIN_DISPATCHES)' in source
    assert "clampDispatchCount(parsedFloor)" in source
    assert '|| "10"' not in source
    assert "10-agent floor" not in source


def test_registered_directives_plugin_has_no_always_on_legacy_floor() -> None:
    wrapper = DIRECTIVES_PLUGIN.read_text()
    source = DIRECTIVES_IMPL.read_text()
    manifest = (ROOT / "opencode.json").read_text()
    assert '"./.opencode/plugin/enforce-directives.ts"' in manifest
    assert 'impl from "./impl/enforce_directives_impl.ts"' in wrapper
    assert 'id: "floor-10"' not in source
    assert "target: 10" not in source
    assert "Dormant directive-enforcement implementation" not in source
    assert "clampDispatchCount(parseInt(floorMatch[1], 10))" in source
    assert 'd.id !== "floor-10"' in source


def test_additive_guard_uses_reachable_canonical_wave_size() -> None:
    source = ADDITIVE_PLUGIN.read_text()
    assert 'HARD_MAX_DISPATCHES' in source
    assert "const COMPLETE_WAVE_SIZE = HARD_MAX_DISPATCHES" in source
    assert "total >= COMPLETE_WAVE_SIZE" in source
    assert "total >= 10" not in source


def test_worktree_agent_cap_is_two_and_clamped() -> None:
    source = DELEGATE_PLUGIN.read_text()
    assert "const HARD_WORKTREE_CAP = 2" in source
    assert "Math.min(HARD_WORKTREE_CAP" in source
    assert 'GLUDD_WORKTREE_CAP || "6"' not in source


def test_session_banner_does_not_demand_a_wave_when_floor_is_zero() -> None:
    source = SESSION_PLUGIN.read_text()
    assert 'EFFECTIVE_MIN > 0 ? "DO NOT WRITE any prose' in source
    assert ': "After locating work, proceed inline or delegate based on task shape."' in source


def test_active_deep_spec_skill_uses_current_agent_limits() -> None:
    source = DEEP_SPEC_SKILL.read_text()
    assert "10 agents retry simultaneously" not in source
    assert "Currently 10 agents shared" not in source
    assert "three-agent hard ceiling" in source
