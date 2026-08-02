"""Runtime behavior for the extended OpenCode enforcement plugin set.

These tests execute the actual TypeScript factories.  They complement source
shape checks by proving that each mapped hook makes the expected allow/deny or
warning decision with hermetic state files.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"


def _run_ts(code: str, tmp_path: Path, env_override: dict[str, str]) -> object:
    script = tmp_path / "invoke.ts"
    script.write_text(code, encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "OPENCODE_SUBAGENT": "0",
            "GLUDD_DISENGAGE_PATH": str(tmp_path / "no-disengage.json"),
            "GLUDD_HOT_MODULE_PREFIX": str(tmp_path / "no-hot-"),
        }
    )
    env.update(env_override)
    result = subprocess.run(
        ["node", "--experimental-strip-types", str(script)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Node runtime probe failed ({result.returncode})\n"
        f"stdout:\n{result.stdout[-2000:]}\n"
        f"stderr:\n{result.stderr[-2000:]}"
    )
    for line in reversed(result.stdout.splitlines()):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise AssertionError(f"runtime probe emitted no JSON: {result.stdout[-2000:]}")


def _plugin(name: str) -> str:
    return str(PLUGIN_DIR / name)


def test_enforce_floor_v2_denies_non_dispatch_while_floor_is_deficient(tmp_path: Path):
    state = tmp_path / "dispatch-state.json"
    state.write_text(
        json.dumps({"dispatched": 0, "completed": 0, "last_updated": 0}),
        encoding="utf-8",
    )
    code = f"""
const mod = await import('{_plugin("enforce-floor-v2.ts")}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(
        code,
        tmp_path,
        {
            "GLUDD_PROJECT_ROOT": str(ROOT),
            "GLUDD_DISPATCH_STATE_FILE": str(state),
            "GLUDD_DISPATCH_FLOOR": "2",
            "GLUDD_FLOOR_V2_ENFORCE": "1",
        },
    )
    assert isinstance(result, dict)
    assert result["permissionDecision"] == "deny"
    assert "FLOOR DEFICIT: 2" in result["message"]


def test_enforce_directives_rejects_under_target_completion_claim(tmp_path: Path):
    state = tmp_path / "directives.json"
    code = f"""
const fs = await import('node:fs')
fs.writeFileSync(process.env.GLUDD_DIRECTIVE_STATE, JSON.stringify({{
  directives: [{{
    id: 'coverage-85', kind: 'numeric', subject: 'e2e coverage', target: 85,
    source: 'runtime-test', pattern: 'coverage', active: true,
    created_ts: 0, updated_ts: 0
  }}],
  last_dispatch_count: 0, last_dispatch_ts: 0, pid: process.pid
}}))
const mod = await import('{_plugin("enforce-directives.ts")}')
const plugin = await mod.default({{}})
const result = await plugin['experimental.text.complete'](
  {{}}, {{text: 'All e2e coverage work is complete at 72%.'}}
)
console.log(JSON.stringify(result))
"""
    result = _run_ts(
        code,
        tmp_path,
        {
            "GLUDD_DIRECTIVE_STATE": str(state),
            "GLUDD_DIRECTIVE_ENFORCE": "1",
        },
    )
    assert isinstance(result, dict)
    assert result["text"].startswith("DIRECTIVE VIOLATION:")
    assert "requires >85%" in result["text"]
    assert "claims 72%" in result["text"]


def test_enforce_deliverable_warns_for_check_only_and_oversized_prompt(tmp_path: Path):
    code = f"""
const warnings = []
console.warn = (...args) => warnings.push(args.join(' '))
const mod = await import('{_plugin("enforce-deliverable.ts")}')
const plugin = await mod.default({{}})
const prompt = ['check CI status', ...Array.from({{length: 20}}, (_, i) => `line ${{i}}`)].join('\\n')
await plugin['tool.execute.before']({{tool: 'task', args: {{prompt}}}}, undefined)
console.log(JSON.stringify(warnings))
"""
    result = _run_ts(
        code,
        tmp_path,
        {"GLUDD_DELIVERABLE_ENFORCE": "1"},
    )
    assert isinstance(result, list)
    assert any("DELIVERABLE WARNING" in warning for warning in result)
    assert any("TERSE PROMPT RULE" in warning for warning in result)


def test_enforce_no_ci_poll_denies_then_productive_work_resets(tmp_path: Path):
    poll_state = tmp_path / "ci-poll.json"
    stagnant_state = tmp_path / "stagnant.json"
    code = f"""
const mod = await import('{_plugin("enforce-no-ci-poll.ts")}')
const plugin = await mod.default({{}})
const poll = () => plugin['tool.execute.before'](
  {{tool: 'bash', args: {{command: 'make ci-status'}}}}, undefined
)
const first = await poll()
const second = await poll()
await plugin['tool.execute.before'](
  {{tool: 'bash', args: {{command: 'make git-commit MSG=test'}}}}, undefined
)
const afterReset = await poll()
console.log(JSON.stringify({{
  firstAllowed: first == null,
  secondDecision: second?.permissionDecision,
  secondMessage: second?.message,
  afterResetAllowed: afterReset == null
}}))
"""
    result = _run_ts(
        code,
        tmp_path,
        {
            "GLUDD_CI_POLL_STATE": str(poll_state),
            "GLUDD_STAGNANT_STATE": str(stagnant_state),
            "GLUDD_CI_POLL_MAX": "1",
            "GLUDD_STAGNANT_MAX": "10",
        },
    )
    assert result == {
        "firstAllowed": True,
        "secondDecision": "deny",
        "secondMessage": result["secondMessage"],
        "afterResetAllowed": True,
    }
    assert "CI POLLING IS NOT WORK" in result["secondMessage"]


def test_enforce_release_deadline_blocks_non_release_but_allows_release(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "TASKS.md").write_text(
        "- [x] RP.19 — Release cut | status: in_progress\n",
        encoding="utf-8",
    )
    state = tmp_path / "release-deadline.json"
    state.write_text(
        json.dumps(
            {
                "release_task": "RP.19",
                "start_ms": int(time.time() * 1000) - 10_000,
                "warned": False,
            }
        ),
        encoding="utf-8",
    )
    code = f"""
const mod = await import('{_plugin("enforce-release-deadline.ts")}')
const plugin = await mod.default({{}})
const blocked = await plugin['tool.execute.before'](
  {{tool: 'bash', args: {{command: 'make lint'}}}}, undefined
)
const allowed = await plugin['tool.execute.before'](
  {{tool: 'bash', args: {{command: 'make release-cut'}}}}, undefined
)
console.log(JSON.stringify({{
  blockedDecision: blocked?.permissionDecision,
  blockedMessage: blocked?.message,
  releaseAllowed: allowed == null
}}))
"""
    result = _run_ts(
        code,
        tmp_path,
        {
            "GLUDD_PROJECT_ROOT": str(project),
            "GLUDD_RELEASE_DEADLINE_STATE": str(state),
            "GLUDD_RELEASE_DEADLINE_WARN_MS": "10",
            "GLUDD_RELEASE_DEADLINE_BLOCK_MS": "20",
            "GLUDD_RELEASE_DEADLINE_ENFORCE": "1",
        },
    )
    assert isinstance(result, dict)
    assert result["blockedDecision"] == "deny"
    assert "RELEASE DEADLINE BLOCK" in result["blockedMessage"]
    assert result["releaseAllowed"] is True
