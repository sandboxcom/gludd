"""Behavioral contract for long-pipeline kickoff parallelism.

The plugin must launch the expensive pipeline against a frozen revision, expose
only genuinely independent work, and keep every writer out of that checkout.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MODULE = (ROOT / ".opencode" / "lib" / "pipeline_kickoff.ts").as_uri()
PLUGIN = (ROOT / ".opencode" / "plugin" / "enforce-pipeline-kickoff.ts").as_uri()


def _run_ts(code: str, env: dict[str, str] | None = None) -> dict[str, Any]:
    merged = os.environ.copy()
    merged.update(env or {})
    merged.setdefault("OPENCODE_SUBAGENT", "")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ts", dir="/tmp", prefix="pipeline_kickoff_", delete=False
    ) as handle:
        handle.write(code)
        script = Path(handle.name)
    try:
        proc = subprocess.run(
            ["node", "--experimental-strip-types", str(script)],
            cwd=ROOT,
            env=merged,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])
    finally:
        script.unlink(missing_ok=True)


def _runtime_env(tmp_path: Path, *, todos: object, in_flight: int = 0) -> dict[str, str]:
    todo_path = tmp_path / "todos.json"
    todo_path.write_text(json.dumps(todos), encoding="utf-8")
    multitask_path = tmp_path / "multitask.json"
    multitask_path.write_text(
        json.dumps({"estimatedInFlight": in_flight}), encoding="utf-8"
    )
    return {
        "GLUDD_PROJECT_ROOT": str(tmp_path),
        "GLUDD_PIPELINE_KICKOFF_STATE": str(tmp_path / "kickoff.json"),
        "GLUDD_TODOWRITE_STATE_PATH": str(todo_path),
        "GLUDD_MULTITASK_STATE_FILE": str(multitask_path),
        "GLUDD_FORCE_DISPATCH_PATH": str(tmp_path / "force.json"),
        "GLUDD_DISPATCH_PREFLIGHT_PATH": str(tmp_path / "preflight.json"),
        "GLUDD_PIPELINE_STATUS_PATH": str(tmp_path / ".gate-status"),
        "GLUDD_PIPELINE_TESTED_REF": "abc123def4567890abc123def4567890abc123de",
        "GLUDD_HOT_MODULE_PREFIX": str(tmp_path / "no-hot-"),
    }


def test_consolidation_deduplicates_filters_conflicts_and_caps_three() -> None:
    code = f"""
import {{ consolidateCandidateBatch }} from {MODULE!r}
const raw = [
  {{id: 'a', content: 'Implement parser tests', status: 'pending', files: ['src/a.py']}},
  {{id: 'a-copy', content: '  implement   parser tests ', status: 'pending', files: ['src/a.py']}},
  {{id: 'conflict', content: 'Refactor the same parser', status: 'pending', files: ['src/a.py']}},
  {{id: 'poll', content: 'Wait for CI and check pipeline status', status: 'pending'}},
  {{id: 'blocked', content: 'Publish package', status: 'pending', depends_on: ['missing']}},
  {{id: 'b', content: 'Add API contract tests', status: 'in_progress', files: ['tests/api.py']}},
  {{id: 'c', content: 'Document rollback operation', status: 'pending', files: ['docs/rollback.md']}},
  {{id: 'd', content: 'Add deployment receipt schema', status: 'pending', files: ['src/receipt.py']}},
]
const batch = consolidateCandidateBatch(raw, 0)
console.log(JSON.stringify({{ids: batch.map(item => item.id), count: batch.length}}))
"""
    result = _run_ts(code)
    assert result == {"ids": ["a", "b", "c"], "count": 3}


def test_consolidation_respects_existing_agents_and_zero_is_valid() -> None:
    code = f"""
import {{ consolidateCandidateBatch }} from {MODULE!r}
const raw = [
  {{id: 'a', content: 'Implement parser tests', status: 'pending', files: ['src/a.py']}},
  {{id: 'b', content: 'Add API contract tests', status: 'pending', files: ['tests/api.py']}},
  {{id: 'c', content: 'Document rollback operation', status: 'pending', files: ['docs/r.md']}},
]
console.log(JSON.stringify({{
  oneSlot: consolidateCandidateBatch(raw, 2).map(item => item.id),
  full: consolidateCandidateBatch(raw, 3),
  empty: consolidateCandidateBatch([], 0),
}}))
"""
    result = _run_ts(code)
    assert result == {"oneSlot": ["a"], "full": [], "empty": []}


def test_kickoff_receipt_path_is_namespaced_per_checkout() -> None:
    code = f"""
import {{ projectKickoffStatePath }} from {MODULE!r}
const first = projectKickoffStatePath('/work/project-a')
const repeat = projectKickoffStatePath('/work/project-a')
const second = projectKickoffStatePath('/work/project-b')
console.log(JSON.stringify({{
  deterministic: first === repeat,
  isolated: first !== second,
  prefixed: first.startsWith('/tmp/gludd-pipeline-kickoff-'),
}}))
"""
    assert _run_ts(code) == {
        "deterministic": True,
        "isolated": True,
        "prefixed": True,
    }


def test_pipeline_launch_freezes_ref_and_exposes_isolated_batch(tmp_path: Path) -> None:
    env = _runtime_env(
        tmp_path,
        todos={
            "items": [
                {
                    "id": "api-tests",
                    "content": "Add API contract tests",
                    "status": "pending",
                    "files": ["tests/api.py"],
                },
                {
                    "id": "rollback-doc",
                    "content": "Document rollback operation",
                    "status": "pending",
                    "files": ["docs/rollback.md"],
                },
                {
                    "id": "receipt",
                    "content": "Add deployment receipt schema",
                    "status": "pending",
                    "files": ["src/receipt.py"],
                },
            ]
        },
        in_flight=1,
    )
    code = f"""
import pluginFactory from {PLUGIN!r}
const plugin = await pluginFactory({{}})
const input = {{tool: 'bash', args: {{command: 'make gate-async'}}}}
const beforeOutput = {{args: {{command: 'make gate-async'}}}}
const before = await plugin['tool.execute.before'](input, beforeOutput)
const afterOutput = {{
  args: beforeOutput.args,
  result: {{stdout: '[gate_async] started'}},
  metadata: {{exitCode: 0}},
}}
await plugin['tool.execute.after'](input, afterOutput)
const state = JSON.parse(await (await import('node:fs/promises')).readFile(
  process.env.GLUDD_PIPELINE_KICKOFF_STATE, 'utf8'))
console.log(JSON.stringify({{
  before: before ?? null,
  command: beforeOutput.args.command,
  status: state.status,
  testedRef: state.tested_ref,
  testedWorktree: state.tested_worktree,
  candidates: state.candidates.length,
  isolated: state.candidates.every(item => item.dispatch_prompt.includes('isolated git worktree')),
  metadataCount: afterOutput.metadata.pipelineKickoff.candidates.length,
  exposed: afterOutput.result.stdout.includes('PIPELINE PARALLEL KICKOFF'),
}}))
"""
    result = _run_ts(code, env)
    assert result == {
        "before": None,
        "command": "make gate-async REF=abc123def4567890abc123def4567890abc123de",
        "status": "running",
        "testedRef": "abc123def4567890abc123def4567890abc123de",
        "testedWorktree": str(tmp_path),
        "candidates": 2,
        "isolated": True,
        "metadataCount": 2,
        "exposed": True,
    }


def test_running_pipeline_blocks_checkout_writes_and_duplicate_or_filler_dispatches(
    tmp_path: Path,
) -> None:
    env = _runtime_env(
        tmp_path,
        todos=[
            {
                "id": "api-tests",
                "content": "Add API contract tests",
                "status": "pending",
                "files": ["tests/api.py"],
            }
        ],
    )
    code = f"""
import pluginFactory from {PLUGIN!r}
import * as fs from 'node:fs'
const plugin = await pluginFactory({{}})
const launch = {{tool: 'bash', args: {{command: 'make gate-async'}}}}
const launchOutput = {{args: {{command: 'make gate-async'}}}}
await plugin['tool.execute.before'](launch, launchOutput)
await plugin['tool.execute.after'](
  launch,
  {{args: launchOutput.args, result: '', metadata: {{exitCode: 0}}}},
)
const state = JSON.parse(fs.readFileSync(process.env.GLUDD_PIPELINE_KICKOFF_STATE, 'utf8'))
const prompt = state.candidates[0].dispatch_prompt
const edit = await plugin['tool.execute.before'](
  {{tool: 'edit', args: {{filePath: {str(tmp_path / 'src/a.py')!r}}}}}, {{}})
const filler = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Create an isolated git worktree, then wait for CI status'}}}}, {{}})
const unsafe = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Add API contract tests'}}}}, {{}})
const first = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt}}}}, {{}})
const duplicate = await plugin['tool.execute.before'](
  {{tool: 'agent', args: {{prompt}}}}, {{}})
console.log(JSON.stringify({{
  edit: edit?.permissionDecision,
  filler: filler?.permissionDecision,
  unsafe: unsafe?.permissionDecision,
  first: first ?? null,
  duplicate: duplicate?.permissionDecision,
  duplicateMessage: duplicate?.message ?? '',
}}))
"""
    result = _run_ts(code, env)
    assert result["edit"] == "deny"
    assert result["filler"] == "deny"
    assert result["unsafe"] == "deny"
    assert result["first"] is None
    assert result["duplicate"] == "deny"
    assert "duplicate" in result["duplicateMessage"].lower()


def test_zero_candidate_pipeline_does_not_require_filler_and_terminal_status_unfreezes(
    tmp_path: Path,
) -> None:
    env = _runtime_env(tmp_path, todos=[])
    code = f"""
import pluginFactory from {PLUGIN!r}
import * as fs from 'node:fs'
const plugin = await pluginFactory({{}})
const input = {{tool: 'bash', args: {{command: 'make gate-async'}}}}
const output = {{args: {{command: 'make gate-async'}}}}
await plugin['tool.execute.before'](input, output)
await plugin['tool.execute.after'](input, {{args: output.args, result: '', metadata: {{exitCode: 0}}}})
const started = JSON.parse(fs.readFileSync(process.env.GLUDD_PIPELINE_KICKOFF_STATE, 'utf8'))
fs.writeFileSync(
  process.env.GLUDD_PIPELINE_STATUS_PATH,
  `PASS ${{Math.floor(Date.now() / 1000)}}\\n`,
)
const editAfterPass = await plugin['tool.execute.before'](
  {{tool: 'edit', args: {{filePath: {str(tmp_path / 'src/a.py')!r}}}}}, {{}})
const finished = JSON.parse(fs.readFileSync(process.env.GLUDD_PIPELINE_KICKOFF_STATE, 'utf8'))
console.log(JSON.stringify({{
  candidates: started.candidates.length,
  editAfterPass: editAfterPass ?? null,
  status: finished.status,
}}))
"""
    result = _run_ts(code, env)
    assert result == {"candidates": 0, "editAfterPass": None, "status": "complete"}


def test_dispatch_cap_includes_agents_already_in_flight(tmp_path: Path) -> None:
    env = _runtime_env(
        tmp_path,
        todos=[
            {
                "id": f"task-{index}",
                "content": f"Implement useful change {index}",
                "status": "pending",
                "files": [f"src/{index}.py"],
            }
            for index in range(3)
        ],
        in_flight=0,
    )
    code = f"""
import pluginFactory from {PLUGIN!r}
import * as fs from 'node:fs'
const plugin = await pluginFactory({{}})
const input = {{tool: 'bash', args: {{command: 'make gate-async'}}}}
const output = {{args: {{command: 'make gate-async'}}}}
await plugin['tool.execute.before'](input, output)
await plugin['tool.execute.after'](input, {{args: output.args, result: '', metadata: {{exitCode: 0}}}})
const state = JSON.parse(fs.readFileSync(process.env.GLUDD_PIPELINE_KICKOFF_STATE, 'utf8'))
const multitaskPath = process.env.GLUDD_MULTITASK_STATE_FILE
const dispatch = async (index, inFlight) => {{
  fs.writeFileSync(multitaskPath, JSON.stringify({{estimatedInFlight: inFlight}}))
  return plugin['tool.execute.before'](
    {{tool: 'task', args: {{prompt: state.candidates[index].dispatch_prompt}}}},
    {{}},
  )
}}
// The multitask hook runs before pipeline-kickoff and has already counted the
// current attempt: one unrelated worker + this attempt + accepted kickoff work.
const first = await dispatch(0, 2)
const second = await dispatch(1, 3)
const third = await dispatch(2, 4)
console.log(JSON.stringify({{
  first: first ?? null,
  second: second ?? null,
  third: third?.permissionDecision ?? null,
  message: third?.message ?? '',
}}))
"""
    result = _run_ts(code, env)
    assert result["first"] is None
    assert result["second"] is None
    assert result["third"] == "deny"
    assert "cap" in result["message"].lower()


def test_stale_terminal_receipt_does_not_unfreeze_new_pipeline(tmp_path: Path) -> None:
    env = _runtime_env(tmp_path, todos=[])
    status_path = Path(env["GLUDD_PIPELINE_STATUS_PATH"])
    status_path.write_text("PASS 1\n", encoding="utf-8")
    os.utime(status_path, (1, 1))
    code = f"""
import pluginFactory from {PLUGIN!r}
const plugin = await pluginFactory({{}})
const input = {{tool: 'bash', args: {{command: 'make gate-async'}}}}
const output = {{args: {{command: 'make gate-async'}}}}
await plugin['tool.execute.before'](input, output)
await plugin['tool.execute.after'](input, {{args: output.args, result: '', metadata: {{exitCode: 0}}}})
const edit = await plugin['tool.execute.before'](
  {{tool: 'edit', args: {{filePath: {str(tmp_path / 'src/a.py')!r}}}}}, {{}})
console.log(JSON.stringify({{decision: edit?.permissionDecision ?? null}}))
"""
    assert _run_ts(code, env) == {"decision": "deny"}


def test_frozen_checkout_allows_status_but_blocks_mutating_make(tmp_path: Path) -> None:
    env = _runtime_env(tmp_path, todos=[])
    state = {
        "version": 1,
        "status": "running",
        "pipeline_target": "gate-async",
        "command": "make gate-async",
        "tested_ref": env["GLUDD_PIPELINE_TESTED_REF"],
        "tested_worktree": str(tmp_path),
        "created_at": 1_800_000_000_000,
        "updated_at": 1_800_000_000_000,
        "candidates": [],
        "dispatched_keys": [],
    }
    Path(env["GLUDD_PIPELINE_KICKOFF_STATE"]).write_text(
        json.dumps(state), encoding="utf-8"
    )
    code = f"""
import pluginFactory from {PLUGIN!r}
const plugin = await pluginFactory({{}})
const status = await plugin['tool.execute.before'](
  {{tool: 'bash', args: {{command: 'make gate-status'}}}}, {{}})
const mutation = await plugin['tool.execute.before'](
  {{tool: 'bash', args: {{command: 'make git-add FILES=src/a.py'}}}}, {{}})
console.log(JSON.stringify({{
  status: status ?? null,
  mutation: mutation?.permissionDecision ?? null,
}}))
"""
    assert _run_ts(code, env) == {"status": None, "mutation": "deny"}


def test_subagent_and_explicit_disable_bypass_freeze(tmp_path: Path) -> None:
    env = _runtime_env(tmp_path, todos=[])
    state = {
        "version": 1,
        "status": "running",
        "pipeline_target": "gate-async",
        "command": "make gate-async",
        "tested_ref": env["GLUDD_PIPELINE_TESTED_REF"],
        "tested_worktree": str(tmp_path),
        "created_at": 1_800_000_000_000,
        "updated_at": 1_800_000_000_000,
        "candidates": [],
        "dispatched_keys": [],
    }
    Path(env["GLUDD_PIPELINE_KICKOFF_STATE"]).write_text(
        json.dumps(state), encoding="utf-8"
    )
    code = f"""
import pluginFactory from {PLUGIN!r}
const plugin = await pluginFactory({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'edit', args: {{filePath: {str(tmp_path / 'src/a.py')!r}}}}}, {{}})
console.log(JSON.stringify({{result: result ?? null}}))
"""
    assert _run_ts(code, {**env, "OPENCODE_SUBAGENT": "1"}) == {"result": None}
    assert _run_ts(code, {**env, "GLUDD_PIPELINE_KICKOFF_ENFORCE": "0"}) == {
        "result": None
    }


def test_failed_pipeline_launch_never_exposes_dispatch_candidates(tmp_path: Path) -> None:
    env = _runtime_env(
        tmp_path,
        todos=[{"id": "work", "content": "Implement useful parser tests", "status": "pending"}],
    )
    code = f"""
import pluginFactory from {PLUGIN!r}
import * as fs from 'node:fs'
const plugin = await pluginFactory({{}})
const input = {{tool: 'bash', args: {{command: 'make gate-async'}}}}
const beforeOutput = {{args: {{command: 'make gate-async'}}}}
await plugin['tool.execute.before'](input, beforeOutput)
const afterOutput = {{
  args: beforeOutput.args,
  result: {{stdout: '[gate_async] refused'}},
  metadata: {{exitCode: 2}},
}}
await plugin['tool.execute.after'](input, afterOutput)
const state = JSON.parse(fs.readFileSync(process.env.GLUDD_PIPELINE_KICKOFF_STATE, 'utf8'))
console.log(JSON.stringify({{
  status: state.status,
  exposed: afterOutput.result.stdout.includes('PIPELINE PARALLEL KICKOFF'),
  metadataExposed: Object.hasOwn(afterOutput.metadata, 'pipelineKickoff'),
}}))
"""
    assert _run_ts(code, env) == {
        "status": "failed",
        "exposed": False,
        "metadataExposed": False,
    }


def test_emitted_candidate_prompt_is_dispatchable(tmp_path: Path) -> None:
    env = _runtime_env(
        tmp_path,
        todos=[{"id": "work", "content": "Implement useful parser tests", "status": "pending"}],
    )
    code = f"""
import pluginFactory from {PLUGIN!r}
import * as fs from 'node:fs'
const plugin = await pluginFactory({{}})
const input = {{tool: 'bash', args: {{command: 'make gate-async'}}}}
const output = {{args: {{command: 'make gate-async'}}}}
await plugin['tool.execute.before'](input, output)
await plugin['tool.execute.after'](input, {{args: output.args, result: '', metadata: {{exitCode: 0}}}})
const state = JSON.parse(fs.readFileSync(process.env.GLUDD_PIPELINE_KICKOFF_STATE, 'utf8'))
const verdict = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: state.candidates[0].dispatch_prompt}}}}, {{}})
console.log(JSON.stringify({{verdict: verdict ?? null}}))
"""
    assert _run_ts(code, env) == {"verdict": None}


def test_dispatch_hash_deduplicates_alias_ids_and_binds_prompt_payload() -> None:
    code = f"""
import {{ dispatchKey }} from {MODULE!r}
const first = dispatchKey(
  '[pipeline-task:first] Implement useful parser tests. ' +
  'Create and use an isolated git worktree. Never edit the frozen tested checkout.',
)
const alias = dispatchKey(
  '[pipeline-task:alias] Implement useful parser tests. ' +
  'Create and use an isolated git worktree. Never edit the frozen tested checkout.',
)
const mutated = dispatchKey(
  '[pipeline-task:alias] Delete production safeguards. ' +
  'Create and use an isolated git worktree. Never edit the frozen tested checkout.',
)
console.log(JSON.stringify({{sameTask: first === alias, mutationBound: first !== mutated}}))
"""
    assert _run_ts(code) == {"sameTask": True, "mutationBound": True}


def test_negated_worktree_language_is_not_an_isolated_dispatch_prompt() -> None:
    code = f"""
import {{ isIsolatedDispatchPrompt }} from {MODULE!r}
const valid = isIsolatedDispatchPrompt(
  'Create and use an isolated git worktree. Never edit the frozen tested checkout.',
)
const negated = isIsolatedDispatchPrompt(
  'Do not create an isolated git worktree. Never edit the frozen tested checkout.',
)
console.log(JSON.stringify({{valid, negated}}))
"""
    assert _run_ts(code) == {"valid": True, "negated": False}


def test_freshly_touched_stale_receipt_cannot_unfreeze_new_pipeline(
    tmp_path: Path,
) -> None:
    env = _runtime_env(tmp_path, todos=[])
    code = f"""
import pluginFactory from {PLUGIN!r}
import * as fs from 'node:fs'
const plugin = await pluginFactory({{}})
const launch = {{tool: 'bash', args: {{command: 'make gate-async'}}}}
const output = {{args: {{command: 'make gate-async'}}}}
await plugin['tool.execute.before'](launch, output)
await plugin['tool.execute.after'](
  launch,
  {{args: output.args, result: '', metadata: {{exitCode: 0}}}},
)
// A copied receipt can have a new filesystem mtime but still carry an epoch
// from an older pipeline. The receipt timestamp, not mtime alone, is binding.
fs.writeFileSync(process.env.GLUDD_PIPELINE_STATUS_PATH, 'PASS 1\\n')
const edit = await plugin['tool.execute.before'](
  {{tool: 'edit', args: {{filePath: {str(tmp_path / 'src/a.py')!r}}}}},
  {{}},
)
const state = JSON.parse(fs.readFileSync(process.env.GLUDD_PIPELINE_KICKOFF_STATE, 'utf8'))
console.log(JSON.stringify({{decision: edit?.permissionDecision ?? null, status: state.status}}))
"""
    assert _run_ts(code, env) == {"decision": "deny", "status": "running"}


def test_cross_checkout_terminal_receipt_cannot_unfreeze_pipeline(
    tmp_path: Path,
) -> None:
    foreign_checkout = tmp_path.parent / f"{tmp_path.name}-foreign"
    foreign_checkout.mkdir()
    env = {
        **_runtime_env(tmp_path, todos=[]),
        "GLUDD_PIPELINE_STATUS_PATH": str(foreign_checkout / ".gate-status"),
    }
    code = f"""
import pluginFactory from {PLUGIN!r}
import * as fs from 'node:fs'
const plugin = await pluginFactory({{}})
const launch = {{tool: 'bash', args: {{command: 'make gate-async'}}}}
const output = {{args: {{command: 'make gate-async'}}}}
await plugin['tool.execute.before'](launch, output)
await plugin['tool.execute.after'](
  launch,
  {{args: output.args, result: '', metadata: {{exitCode: 0}}}},
)
fs.writeFileSync(
  process.env.GLUDD_PIPELINE_STATUS_PATH,
  `PASS ${{Math.floor(Date.now() / 1000)}}\\n`,
)
const edit = await plugin['tool.execute.before'](
  {{tool: 'edit', args: {{filePath: {str(tmp_path / 'src/a.py')!r}}}}},
  {{}},
)
const state = JSON.parse(fs.readFileSync(process.env.GLUDD_PIPELINE_KICKOFF_STATE, 'utf8'))
console.log(JSON.stringify({{decision: edit?.permissionDecision ?? null, status: state.status}}))
"""
    assert _run_ts(code, env) == {"decision": "deny", "status": "running"}


def test_symlinked_cross_checkout_receipt_is_rejected(tmp_path: Path) -> None:
    foreign_checkout = tmp_path.parent / f"{tmp_path.name}-foreign-link"
    foreign_checkout.mkdir()
    foreign_status = foreign_checkout / ".gate-status"
    linked_status = tmp_path / "linked-gate-status"
    linked_status.symlink_to(foreign_status)
    env = {
        **_runtime_env(tmp_path, todos=[]),
        "GLUDD_PIPELINE_STATUS_PATH": str(linked_status),
    }
    code = f"""
import pluginFactory from {PLUGIN!r}
import * as fs from 'node:fs'
const plugin = await pluginFactory({{}})
const launch = {{tool: 'bash', args: {{command: 'make gate-async'}}}}
const output = {{args: {{command: 'make gate-async'}}}}
await plugin['tool.execute.before'](launch, output)
await plugin['tool.execute.after'](
  launch,
  {{args: output.args, result: '', metadata: {{exitCode: 0}}}},
)
fs.writeFileSync(
  process.env.GLUDD_PIPELINE_STATUS_PATH,
  `PASS ${{Math.floor(Date.now() / 1000)}}\\n`,
)
const edit = await plugin['tool.execute.before'](
  {{tool: 'edit', args: {{filePath: {str(tmp_path / 'src/a.py')!r}}}}},
  {{}},
)
const state = JSON.parse(fs.readFileSync(process.env.GLUDD_PIPELINE_KICKOFF_STATE, 'utf8'))
console.log(JSON.stringify({{decision: edit?.permissionDecision ?? null, status: state.status}}))
"""
    assert _run_ts(code, env) == {"decision": "deny", "status": "running"}


def test_terminal_receipt_path_cannot_be_mutated_after_launch(tmp_path: Path) -> None:
    env = _runtime_env(tmp_path, todos=[])
    code = f"""
import pluginFactory from {PLUGIN!r}
import * as fs from 'node:fs'
import * as path from 'node:path'
const plugin = await pluginFactory({{}})
const launch = {{tool: 'bash', args: {{command: 'make gate-async'}}}}
const output = {{args: {{command: 'make gate-async'}}}}
await plugin['tool.execute.before'](launch, output)
await plugin['tool.execute.after'](
  launch,
  {{args: output.args, result: '', metadata: {{exitCode: 0}}}},
)
const state = JSON.parse(fs.readFileSync(process.env.GLUDD_PIPELINE_KICKOFF_STATE, 'utf8'))
const swappedPath = path.join(process.env.GLUDD_PROJECT_ROOT, 'swapped-gate-status')
state.status_path = swappedPath
fs.writeFileSync(process.env.GLUDD_PIPELINE_KICKOFF_STATE, JSON.stringify(state))
fs.writeFileSync(swappedPath, `PASS ${{Math.floor(Date.now() / 1000)}}\\n`)
const edit = await plugin['tool.execute.before'](
  {{tool: 'edit', args: {{filePath: {str(tmp_path / 'src/a.py')!r}}}}},
  {{}},
)
const finished = JSON.parse(fs.readFileSync(process.env.GLUDD_PIPELINE_KICKOFF_STATE, 'utf8'))
console.log(JSON.stringify({{
  decision: edit?.permissionDecision ?? null,
  status: finished.status,
}}))
"""
    assert _run_ts(code, env) == {"decision": "deny", "status": "running"}


def test_ship_receipt_rejects_stale_same_ref_content(tmp_path: Path) -> None:
    env = {
        **_runtime_env(tmp_path, todos=[]),
        "GLUDD_PIPELINE_STATUS_PATH": str(tmp_path / ".ship-status"),
    }
    tested_ref = env["GLUDD_PIPELINE_TESTED_REF"]
    Path(env["GLUDD_PIPELINE_STATUS_PATH"]).write_text(
        f"SHIP PASS {tested_ref}\n", encoding="utf-8"
    )
    code = f"""
import pluginFactory from {PLUGIN!r}
import * as fs from 'node:fs'
const plugin = await pluginFactory({{}})
const launch = {{tool: 'bash', args: {{command: 'make ship-async'}}}}
const output = {{args: {{command: 'make ship-async'}}}}
await plugin['tool.execute.before'](launch, output)
await plugin['tool.execute.after'](
  launch,
  {{args: output.args, result: '', metadata: {{exitCode: 0}}}},
)
const stale = fs.readFileSync(process.env.GLUDD_PIPELINE_STATUS_PATH, 'utf8')
fs.writeFileSync(process.env.GLUDD_PIPELINE_STATUS_PATH, stale)
const edit = await plugin['tool.execute.before'](
  {{tool: 'edit', args: {{filePath: {str(tmp_path / 'src/a.py')!r}}}}},
  {{}},
)
const state = JSON.parse(fs.readFileSync(process.env.GLUDD_PIPELINE_KICKOFF_STATE, 'utf8'))
console.log(JSON.stringify({{decision: edit?.permissionDecision ?? null, status: state.status}}))
"""
    assert _run_ts(code, env) == {"decision": "deny", "status": "running"}


def test_ship_receipt_uses_exact_ref_and_last_terminal_line(tmp_path: Path) -> None:
    env = {
        **_runtime_env(tmp_path, todos=[]),
        "GLUDD_PIPELINE_STATUS_PATH": str(tmp_path / ".ship-status"),
    }
    code = f"""
import pluginFactory from {PLUGIN!r}
import * as fs from 'node:fs'
const plugin = await pluginFactory({{}})
const launch = {{tool: 'bash', args: {{command: 'make ship-async'}}}}
const output = {{args: {{command: 'make ship-async'}}}}
await plugin['tool.execute.before'](launch, output)
await plugin['tool.execute.after'](
  launch,
  {{args: output.args, result: '', metadata: {{exitCode: 0}}}},
)
fs.writeFileSync(process.env.GLUDD_PIPELINE_STATUS_PATH, 'SHIP PASS wrong-ref\\n')
const swapped = await plugin['tool.execute.before'](
  {{tool: 'edit', args: {{filePath: {str(tmp_path / 'src/a.py')!r}}}}},
  {{}},
)
fs.writeFileSync(
  process.env.GLUDD_PIPELINE_STATUS_PATH,
  `SHIP PASS ${{process.env.GLUDD_PIPELINE_TESTED_REF}}\\nSHIP FAIL gate\\n`,
)
const afterFailure = await plugin['tool.execute.before'](
  {{tool: 'edit', args: {{filePath: {str(tmp_path / 'src/a.py')!r}}}}},
  {{}},
)
const state = JSON.parse(fs.readFileSync(process.env.GLUDD_PIPELINE_KICKOFF_STATE, 'utf8'))
console.log(JSON.stringify({{
  swapped: swapped?.permissionDecision ?? null,
  afterFailure: afterFailure ?? null,
  status: state.status,
}}))
"""
    assert _run_ts(code, env) == {
        "swapped": "deny",
        "afterFailure": None,
        "status": "failed",
    }


def test_zero_useful_candidates_rejects_ad_hoc_dispatch(tmp_path: Path) -> None:
    env = _runtime_env(
        tmp_path,
        todos=[
            {"id": "poll", "content": "Wait for CI status", "status": "pending"},
            {
                "id": "blocked",
                "content": "Implement blocked package release",
                "status": "pending",
                "depends_on": ["not-complete"],
            },
        ],
    )
    prompt = (
        "[pipeline-task:invented] Implement an unrelated useful change. "
        "Create and use an isolated git worktree. "
        "Never edit the frozen tested checkout."
    )
    code = f"""
import pluginFactory from {PLUGIN!r}
const plugin = await pluginFactory({{}})
const launch = {{tool: 'bash', args: {{command: 'make gate-async'}}}}
const output = {{args: {{command: 'make gate-async'}}}}
await plugin['tool.execute.before'](launch, output)
await plugin['tool.execute.after'](
  launch,
  {{args: output.args, result: '', metadata: {{exitCode: 0}}}},
)
const verdict = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: {prompt!r}}}}},
  {{}},
)
console.log(JSON.stringify({{
  decision: verdict?.permissionDecision ?? null,
  message: verdict?.message ?? '',
}}))
"""
    result = _run_ts(code, env)
    assert result["decision"] == "deny"
    assert "candidate" in result["message"].lower()


def test_candidate_source_mutation_after_launch_is_not_dispatchable(
    tmp_path: Path,
) -> None:
    env = _runtime_env(
        tmp_path,
        todos=[
            {
                "id": "parser",
                "content": "Implement useful parser tests",
                "status": "pending",
                "files": ["tests/parser.py"],
            }
        ],
    )
    code = f"""
import pluginFactory from {PLUGIN!r}
import * as fs from 'node:fs'
const plugin = await pluginFactory({{}})
const launch = {{tool: 'bash', args: {{command: 'make gate-async'}}}}
const output = {{args: {{command: 'make gate-async'}}}}
await plugin['tool.execute.before'](launch, output)
await plugin['tool.execute.after'](
  launch,
  {{args: output.args, result: '', metadata: {{exitCode: 0}}}},
)
const frozen = JSON.parse(fs.readFileSync(process.env.GLUDD_PIPELINE_KICKOFF_STATE, 'utf8'))
fs.writeFileSync(process.env.GLUDD_TODOWRITE_STATE_PATH, JSON.stringify([{{
  id: 'parser',
  content: 'Delete production safeguards',
  status: 'pending',
  files: ['src/security.py'],
}}]))
const changedPrompt = frozen.candidates[0].dispatch_prompt
  .replace('Implement useful parser tests', 'Delete production safeguards')
  .replace('tests/parser.py', 'src/security.py')
const changed = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: changedPrompt}}}},
  {{}},
)
const exact = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: frozen.candidates[0].dispatch_prompt}}}},
  {{}},
)
console.log(JSON.stringify({{
  changed: changed?.permissionDecision ?? null,
  exact: exact ?? null,
}}))
"""
    assert _run_ts(code, env) == {"changed": "deny", "exact": None}


def test_candidate_receipt_mutation_after_launch_fails_integrity(
    tmp_path: Path,
) -> None:
    env = _runtime_env(
        tmp_path,
        todos=[
            {
                "id": "parser",
                "content": "Implement useful parser tests",
                "status": "pending",
                "files": ["tests/parser.py"],
            }
        ],
    )
    code = f"""
import pluginFactory from {PLUGIN!r}
import * as fs from 'node:fs'
const plugin = await pluginFactory({{}})
const launch = {{tool: 'bash', args: {{command: 'make gate-async'}}}}
const output = {{args: {{command: 'make gate-async'}}}}
await plugin['tool.execute.before'](launch, output)
await plugin['tool.execute.after'](
  launch,
  {{args: output.args, result: '', metadata: {{exitCode: 0}}}},
)
const state = JSON.parse(fs.readFileSync(process.env.GLUDD_PIPELINE_KICKOFF_STATE, 'utf8'))
const prompt = state.candidates[0].dispatch_prompt
state.candidates[0].objective = 'Mutated after launch'
fs.writeFileSync(process.env.GLUDD_PIPELINE_KICKOFF_STATE, JSON.stringify(state))
const verdict = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt}}}},
  {{}},
)
console.log(JSON.stringify({{
  decision: verdict?.permissionDecision ?? null,
  message: verdict?.message ?? '',
}}))
"""
    result = _run_ts(code, env)
    assert result["decision"] == "deny"
    assert "integrity" in result["message"].lower()


def test_alias_task_id_cannot_bypass_dispatched_content_hash(tmp_path: Path) -> None:
    env = _runtime_env(
        tmp_path,
        todos=[
            {
                "id": "parser",
                "content": "Implement useful parser tests",
                "status": "pending",
                "files": ["tests/parser.py"],
            }
        ],
    )
    code = f"""
import pluginFactory from {PLUGIN!r}
import * as fs from 'node:fs'
const plugin = await pluginFactory({{}})
const launch = {{tool: 'bash', args: {{command: 'make gate-async'}}}}
const output = {{args: {{command: 'make gate-async'}}}}
await plugin['tool.execute.before'](launch, output)
await plugin['tool.execute.after'](
  launch,
  {{args: output.args, result: '', metadata: {{exitCode: 0}}}},
)
const state = JSON.parse(fs.readFileSync(process.env.GLUDD_PIPELINE_KICKOFF_STATE, 'utf8'))
const prompt = state.candidates[0].dispatch_prompt
const first = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt}}}},
  {{}},
)
const alias = await plugin['tool.execute.before'](
  {{tool: 'agent', args: {{prompt: prompt.replace('[pipeline-task:parser]', '[pipeline-task:alias]')}}}},
  {{}},
)
console.log(JSON.stringify({{
  first: first ?? null,
  alias: alias?.permissionDecision ?? null,
}}))
"""
    assert _run_ts(code, env) == {"first": None, "alias": "deny"}


def test_pipeline_plugin_live_activation_is_built_and_documented() -> None:
    builder = (ROOT / "scripts" / "build_hot_modules.js").read_text(encoding="utf-8")
    assert '"enforce-pipeline-kickoff"' in builder

    doc = (ROOT / "docs" / "features" / "PIPELINE_PARALLEL_KICKOFF.md").read_text(
        encoding="utf-8"
    )
    assert "github.com/anomalyco/opencode/issues/39987" in doc
    assert "github.com/anomalyco/opencode/issues/42898" in doc
    assert "make hot-reload-plugins" in doc
    assert "make hot-reload-status" in doc
    assert "Restart OpenCode" in doc


def test_plugin_registered_and_feature_doc_records_forum_evidence() -> None:
    config = json.loads((ROOT / "opencode.json").read_text(encoding="utf-8"))
    assert "./.opencode/plugin/enforce-pipeline-kickoff.ts" in config["plugin"]

    doc = (ROOT / "docs" / "features" / "PIPELINE_PARALLEL_KICKOFF.md").read_text(
        encoding="utf-8"
    )
    assert "github.com/openai/codex/issues/41007" in doc
    assert "github.com/openai/codex/discussions/3898" in doc
    assert "github.com/openai/codex/issues/38989" in doc

    specs = (ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md").read_text(
        encoding="utf-8"
    )
    assert specs.count("enforce-pipeline-kickoff.ts") >= 5
