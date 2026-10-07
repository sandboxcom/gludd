"""Executable Node proofs for the v3 durable dispatch ledger."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.hook_runtime.fixtures import ROOT
from scripts.hook_runtime.runner import _run_ts

OWNER_MODULE = ROOT / ".opencode" / "lib" / "dispatch_dedup.ts"


def _env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return {
        "GLUDD_DISPATCH_DEDUP_STATE": str(tmp_path / "dispatch-ledger.json"),
        "GLUDD_PROJECT_ROOT": str(project),
        "GLUDD_DISPATCH_SCOPE": "project",
        **overrides,
    }


def _run(code: str, tmp_path: Path, **overrides: str) -> Any:
    source = f"""\
const fs = await import('node:fs')
const mod = await import('{OWNER_MODULE}')
{code}
"""
    return _run_ts(source, env_override=_env(tmp_path, **overrides), cwd=tmp_path)


def test_terminal_retry_is_explicit_and_completed_is_final(tmp_path: Path) -> None:
    result = _run(
        """
const failedPrompt = 'Build a durable failure receipt.'
const first = mod.registerDispatch('task', {prompt: failedPrompt})
mod.finishDispatch('task', {prompt: failedPrompt}, {error: 'worker failed'})
const implicit = mod.registerDispatch('task', {prompt: failedPrompt})
const retryPrompt = `${failedPrompt}\nRetry-Terminal: true`
const explicit = mod.registerDispatch('task', {prompt: retryPrompt})
mod.finishDispatch('task', {prompt: failedPrompt}, {})
const completedRetry = mod.registerDispatch('task', {prompt: retryPrompt})
console.log(JSON.stringify({first, implicit, explicit, completedRetry}))
""",
        tmp_path,
    )

    assert result["first"] is None
    assert "reason=explicit_retry_required" in result["implicit"]
    assert result["explicit"] is None
    assert "reason=completed_is_terminal" in result["completedRetry"]


def test_cancelled_dispatch_requires_explicit_retry(tmp_path: Path) -> None:
    result = _run(
        """
const prompt = 'Build a cancellation-safe handoff.'
mod.registerDispatch('workflow', {prompt})
mod.finishDispatch('workflow', {prompt}, {status: 'cancelled'})
const implicit = mod.registerDispatch('workflow', {prompt})
const explicit = mod.registerDispatch('workflow', {prompt, retry_terminal: true})
const ledger = JSON.parse(fs.readFileSync(process.env.GLUDD_DISPATCH_DEDUP_STATE, 'utf8'))
const entry = Object.values(ledger.entries)[0]
console.log(JSON.stringify({implicit, explicit, status: entry.status, attempts: entry.attempts}))
""",
        tmp_path,
    )

    assert "status=cancelled" in result["implicit"]
    assert result["explicit"] is None
    assert result["status"] == "in_progress"
    assert result["attempts"] == 2


def test_dead_stale_owner_recovery_is_bounded_and_explicit(tmp_path: Path) -> None:
    result = _run(
        """
const prompt = 'Recover a dead stale dispatch owner.'
mod.registerDispatch('agent', {prompt})
const stateFile = process.env.GLUDD_DISPATCH_DEDUP_STATE
const ledger = JSON.parse(fs.readFileSync(stateFile, 'utf8'))
const entry = Object.values(ledger.entries)[0]
entry.owner.claimed_at = 0
fs.writeFileSync(stateFile, JSON.stringify(ledger))
const liveRetry = mod.registerDispatch('agent', {prompt, retry_terminal: true})
const afterLiveRetry = JSON.parse(fs.readFileSync(stateFile, 'utf8'))
Object.values(afterLiveRetry.entries)[0].owner.pid = 999999999
fs.writeFileSync(stateFile, JSON.stringify(afterLiveRetry))
const implicit = mod.registerDispatch('agent', {prompt})
const explicit = mod.registerDispatch('agent', {prompt, retry_terminal: true})
const recovered = Object.values(
  JSON.parse(fs.readFileSync(stateFile, 'utf8')).entries,
)[0]
console.log(JSON.stringify({
  implicit,
  explicit,
  liveRetry,
  attempts: recovered.attempts,
  staleRecoveries: recovered.stale_recoveries,
  ownerPid: recovered.owner.pid,
}))
""",
        tmp_path,
        GLUDD_DISPATCH_STALE_MS="1",
    )

    assert "reason=owner_live_or_claim_fresh" in result["liveRetry"]
    assert "reason=existing_owner_active" in result["implicit"]
    assert result["explicit"] is None
    assert result["attempts"] == 2
    assert result["staleRecoveries"] == 1
    assert result["ownerPid"] > 0


def test_live_lock_blocks_and_dead_stale_lock_is_reclaimed(tmp_path: Path) -> None:
    result = _run(
        """
const prompt = 'Claim work under an exclusive ledger writer lock.'
const stateFile = process.env.GLUDD_DISPATCH_DEDUP_STATE
const lockFile = `${stateFile}.lock`
fs.writeFileSync(lockFile, JSON.stringify({pid: process.pid, created_at_ms: 0, token: 'live'}))
const liveDenied = mod.registerDispatch('task', {prompt})
const wroteWhileLive = fs.existsSync(stateFile)
fs.writeFileSync(lockFile, JSON.stringify({pid: 999999999, created_at_ms: 0, token: 'dead'}))
const recovered = mod.registerDispatch('task', {prompt})
console.log(JSON.stringify({
  liveDenied,
  wroteWhileLive,
  recovered,
  stateExists: fs.existsSync(stateFile),
  lockExists: fs.existsSync(lockFile),
}))
""",
        tmp_path,
    )

    assert "reason=dispatch_ledger_lock_busy" in result["liveDenied"]
    assert result["wroteWhileLive"] is False
    assert result["recovered"] is None
    assert result["stateExists"] is True
    assert result["lockExists"] is False


def test_reworded_tracked_task_is_denied_without_content_echo(tmp_path: Path) -> None:
    result = _run(
        """
const first = mod.registerDispatch('task', {prompt: 'Implement S83.999 with tests.'})
const reworded = mod.registerDispatch(
  'task', {prompt: 'Take another approach to tracked item S83.999.'},
)
console.log(JSON.stringify({first, reworded}))
""",
        tmp_path,
    )

    assert result["first"] is None
    assert "reason=existing_task_owner_active" in result["reworded"]
    assert "S83.999" not in result["reworded"]


def test_filename_like_tokens_do_not_create_task_id_collisions(tmp_path: Path) -> None:
    result = _run(
        """
const first = mod.registerDispatch(
  'task', {prompt: 'Update the README.md navigation links.'},
)
const distinct = mod.registerDispatch(
  'task', {prompt: 'Document the deployment workflow in README.md.'},
)
const ledger = JSON.parse(fs.readFileSync(process.env.GLUDD_DISPATCH_DEDUP_STATE, 'utf8'))
console.log(JSON.stringify({first, distinct, entries: Object.keys(ledger.entries).length}))
""",
        tmp_path,
    )

    assert result["first"] is None
    assert result["distinct"] is None
    assert result["entries"] == 2


def test_same_spec_isolated_by_project_and_scope(tmp_path: Path) -> None:
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    result = _run(
        f"""
const prompt = 'Build the same untracked deliverable.'
process.env.GLUDD_PROJECT_ROOT = {json.dumps(str(project_a))}
process.env.GLUDD_DISPATCH_SCOPE = 'backend'
const first = mod.registerDispatch('task', {{prompt}})
process.env.GLUDD_DISPATCH_SCOPE = 'frontend'
const otherScope = mod.registerDispatch('task', {{prompt}})
process.env.GLUDD_PROJECT_ROOT = {json.dumps(str(project_b))}
process.env.GLUDD_DISPATCH_SCOPE = 'backend'
const otherProject = mod.registerDispatch('task', {{prompt}})
process.env.GLUDD_PROJECT_ROOT = {json.dumps(str(project_a))}
const duplicate = mod.registerDispatch('task', {{prompt}})
const ledger = JSON.parse(fs.readFileSync(process.env.GLUDD_DISPATCH_DEDUP_STATE, 'utf8'))
console.log(JSON.stringify({{first, otherScope, otherProject, duplicate, entries: Object.keys(ledger.entries).length}}))
""",
        tmp_path,
    )

    assert result["first"] is None
    assert result["otherScope"] is None
    assert result["otherProject"] is None
    assert "DUPLICATE DISPATCH DENIED" in result["duplicate"]
    assert result["entries"] == 3


def test_v3_state_and_diagnostics_omit_prompt_content(tmp_path: Path) -> None:
    result = _run(
        """
const prompt = 'Handle confidential marker NEBULA-CEDAR-884.'
mod.registerDispatch('task', {prompt})
const duplicate = mod.registerDispatch('task', {prompt})
const ledgerText = fs.readFileSync(process.env.GLUDD_DISPATCH_DEDUP_STATE, 'utf8')
const ledger = JSON.parse(ledgerText)
const entry = Object.values(ledger.entries)[0]
console.log(JSON.stringify({
  duplicate,
  ledgerText,
  version: ledger.version,
  hasOwner: typeof entry.owner.owner_id === 'string',
}))
""",
        tmp_path,
    )

    assert result["version"] == 3
    assert result["hasOwner"] is True
    assert "nebula-cedar-884" not in result["duplicate"].lower()
    assert "nebula-cedar-884" not in result["ledgerText"].lower()


def test_v2_state_migrates_without_redispatch_or_plaintext(tmp_path: Path) -> None:
    prompt = "Migrate private marker ZEUS-ORCHID-441 without redispatching it."
    normalized = f"task\n{prompt.lower()}"
    legacy_fingerprint = hashlib.sha256(normalized.encode()).hexdigest()
    state_file = tmp_path / "dispatch-ledger.json"
    state_file.write_text(
        json.dumps(
            {
                "version": 2,
                "entries": {
                    legacy_fingerprint: {
                        "fingerprint": legacy_fingerprint,
                        "normalized_spec": normalized,
                        "prompt_head": prompt,
                        "task_ids": [],
                        "tool": "task",
                        "status": "completed",
                        "attempts": 1,
                        "denied_duplicates": 0,
                        "first_dispatched_at": 1,
                        "updated_at": 2,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = _run(
        f"""
const prompt = {json.dumps(prompt)}
const denied = mod.registerDispatch('task', {{prompt}})
const ledgerText = fs.readFileSync(process.env.GLUDD_DISPATCH_DEDUP_STATE, 'utf8')
const ledger = JSON.parse(ledgerText)
const entry = Object.values(ledger.entries)[0]
console.log(JSON.stringify({{
  denied,
  ledgerText,
  version: ledger.version,
  status: entry.status,
  hasLegacySpec: Object.hasOwn(entry, 'normalized_spec'),
}}))
""",
        tmp_path,
    )

    assert "status=completed" in result["denied"]
    assert result["version"] == 3
    assert result["status"] == "completed"
    assert result["hasLegacySpec"] is False
    assert "zeus-orchid-441" not in result["ledgerText"].lower()


def test_v2_migration_drops_filename_tokens_previously_treated_as_ids(
    tmp_path: Path,
) -> None:
    legacy_prompt = "Update the README.md navigation links."
    normalized = f"task\n{legacy_prompt.lower()}"
    legacy_fingerprint = hashlib.sha256(normalized.encode()).hexdigest()
    state_file = tmp_path / "dispatch-ledger.json"
    state_file.write_text(
        json.dumps(
            {
                "version": 2,
                "entries": {
                    legacy_fingerprint: {
                        "fingerprint": legacy_fingerprint,
                        "normalized_spec": normalized,
                        "prompt_head": legacy_prompt,
                        "task_ids": ["README.MD"],
                        "tool": "task",
                        "status": "completed",
                        "attempts": 1,
                        "denied_duplicates": 0,
                        "first_dispatched_at": 1,
                        "updated_at": 2,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = _run(
        """
const distinct = mod.registerDispatch(
  'task', {prompt: 'Document the deployment workflow in README.md.'},
)
const ledger = JSON.parse(fs.readFileSync(process.env.GLUDD_DISPATCH_DEDUP_STATE, 'utf8'))
const completed = Object.values(ledger.entries).find(entry => entry.status === 'completed')
console.log(JSON.stringify({distinct, version: ledger.version, taskIds: completed.task_ids}))
""",
        tmp_path,
    )

    assert result["distinct"] is None
    assert result["version"] == 3
    assert result["taskIds"] == []


def test_corrupt_state_fails_closed_without_echoing_prompt(tmp_path: Path) -> None:
    result = _run(
        """
fs.writeFileSync(
  process.env.GLUDD_DISPATCH_DEDUP_STATE,
  JSON.stringify({version: 3, entries: {not_a_digest: {status: 'completed'}}}),
)
const prompt = 'Do not echo SECRET-PROMPT-991.'
const denied = mod.registerDispatch('task', {prompt})
console.log(JSON.stringify({denied}))
""",
        tmp_path,
    )

    assert "reason=dispatch_ledger_invalid" in result["denied"]
    assert "secret-prompt-991" not in result["denied"].lower()
