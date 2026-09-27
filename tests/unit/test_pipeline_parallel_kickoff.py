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
    env = _runtime_env(tmp_path, todos=[])
    state = {
        "version": 1,
        "status": "running",
        "pipeline_target": "gate-async",
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
    prompt = (
        "[pipeline-task:api-tests] Add API contract tests. Create and use an "
        "isolated git worktree and feature branch. Never edit the frozen tested checkout."
    )
    code = f"""
import pluginFactory from {PLUGIN!r}
const plugin = await pluginFactory({{}})
const edit = await plugin['tool.execute.before'](
  {{tool: 'edit', args: {{filePath: {str(tmp_path / 'src/a.py')!r}}}}}, {{}})
const filler = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Create an isolated git worktree, then wait for CI status'}}}}, {{}})
const unsafe = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Add API contract tests'}}}}, {{}})
const first = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: {prompt!r}}}}}, {{}})
const duplicate = await plugin['tool.execute.before'](
  {{tool: 'agent', args: {{prompt: {prompt!r}}}}}, {{}})
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
fs.writeFileSync(process.env.GLUDD_PIPELINE_STATUS_PATH, 'PASS 1800000000\\n')
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
    env = _runtime_env(tmp_path, todos=[], in_flight=1)
    prompt = (
        "Create and use an isolated git worktree and feature branch. "
        "Never edit the frozen tested checkout."
    )
    code = f"""
import pluginFactory from {PLUGIN!r}
const plugin = await pluginFactory({{}})
const input = {{tool: 'bash', args: {{command: 'make gate-async'}}}}
const output = {{args: {{command: 'make gate-async'}}}}
await plugin['tool.execute.before'](input, output)
await plugin['tool.execute.after'](input, {{args: output.args, result: '', metadata: {{exitCode: 0}}}})
const dispatch = async (id) => plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: `[pipeline-task:${{id}}] Implement useful change ${{id}}. {prompt}`}}}}, {{}})
const first = await dispatch('one')
const second = await dispatch('two')
const third = await dispatch('three')
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
