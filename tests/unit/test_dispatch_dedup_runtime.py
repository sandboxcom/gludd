"""Runtime proofs for content-addressed subagent dispatch deduplication."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from scripts import check_dispatch_dedup
from scripts.makefile_layout import compose_makefile

from tests.unit._hook_fixtures import HookEnv, hook_plugin_env_impl


@pytest.fixture
def hook_plugin_env(tmp_path: Path) -> Iterator[HookEnv]:
    yield from hook_plugin_env_impl(tmp_path)


def _dispatch_args(prompt: str, **options: object) -> dict[str, dict[str, object]]:
    return {"args": {"prompt": prompt, **options}}


def test_exact_in_progress_dispatch_is_denied(
    hook_plugin_env: HookEnv,
) -> None:
    prompt = "Fix S83.157 in src/general_ludd/example.py and add its regression test."
    first = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "task"},
        output=_dispatch_args(prompt),
    )
    duplicate = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "task"},
        output=_dispatch_args(prompt),
    )

    assert first.returncode == 0, first.stderr
    assert duplicate.returncode != 0
    assert "DUPLICATE DISPATCH DENIED" in duplicate.stderr


def test_same_tracked_task_id_is_denied_even_when_prompt_wording_changes(
    hook_plugin_env: HookEnv,
) -> None:
    first = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "task"},
        output=_dispatch_args("Fix S83.157 in the Azure live proof."),
    )
    reworded = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "task"},
        output=_dispatch_args(
            "Investigate and repair tracked item S83.157 using another approach."
        ),
    )

    assert first.returncode == 0, first.stderr
    assert reworded.returncode != 0
    assert "reason=existing_task_owner_active" in reworded.stderr
    assert "S83.157" not in reworded.stderr
    assert "content_omitted=true" in reworded.stderr


def test_failed_dispatch_requires_explicit_retry_and_completed_dispatch_cannot_retry(
    hook_plugin_env: HookEnv,
) -> None:
    prompt = "Implement the pipeline failure receipt and its tests."
    first = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "workflow"},
        output=_dispatch_args(prompt),
    )
    failed = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.after",
        input={"tool": "workflow"},
        output={**_dispatch_args(prompt), "error": "worker failed"},
    )
    implicit_retry = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "workflow"},
        output=_dispatch_args(prompt),
    )
    retry = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "workflow"},
        output=_dispatch_args(prompt, retry_terminal=True),
    )
    completed = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.after",
        input={"tool": "workflow"},
        output=_dispatch_args(prompt),
    )
    duplicate = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "workflow"},
        output=_dispatch_args(prompt),
    )

    assert first.returncode == 0, first.stderr
    assert failed.returncode == 0, failed.stderr
    assert implicit_retry.returncode != 0
    assert "explicit_retry_required" in implicit_retry.stderr
    assert retry.returncode == 0, retry.stderr
    assert completed.returncode == 0, completed.stderr
    assert duplicate.returncode != 0
    assert "status=completed" in duplicate.stderr


def test_cancelled_dispatch_requires_explicit_retry(
    hook_plugin_env: HookEnv,
) -> None:
    prompt = "Generate a cancellation-safe release summary."
    first = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "task"},
        output=_dispatch_args(prompt),
    )
    cancelled = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.after",
        input={"tool": "task"},
        output={**_dispatch_args(prompt), "status": "cancelled"},
    )
    implicit_retry = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "task"},
        output=_dispatch_args(prompt),
    )
    explicit_retry = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "task"},
        output=_dispatch_args(prompt, retry_terminal=True),
    )

    assert first.returncode == 0, first.stderr
    assert cancelled.returncode == 0, cancelled.stderr
    assert implicit_retry.returncode != 0
    assert "status=cancelled" in implicit_retry.stderr
    assert explicit_retry.returncode == 0, explicit_retry.stderr


def test_dead_stale_owner_is_recovered_only_by_explicit_retry(
    hook_plugin_env: HookEnv,
) -> None:
    prompt = "Recover the stale owner of the durable dispatch claim."
    stale_env = {"GLUDD_DISPATCH_STALE_MS": "1"}
    first = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "agent"},
        output=_dispatch_args(prompt),
        env_overrides=stale_env,
    )
    time.sleep(0.02)
    implicit_retry = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "agent"},
        output=_dispatch_args(prompt),
        env_overrides=stale_env,
    )
    explicit_retry = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "agent"},
        output=_dispatch_args(prompt, retry_terminal=True),
        env_overrides=stale_env,
    )

    assert first.returncode == 0, first.stderr
    assert implicit_retry.returncode != 0
    assert "status=in_progress" in implicit_retry.stderr
    assert explicit_retry.returncode == 0, explicit_retry.stderr
    ledger = hook_plugin_env.read_json("GLUDD_DISPATCH_DEDUP_STATE")
    assert isinstance(ledger, dict)
    entries = ledger["entries"]
    assert isinstance(entries, dict)
    entry = next(iter(entries.values()))
    assert isinstance(entry, dict)
    assert entry["attempts"] == 2
    assert entry["stale_recoveries"] == 1
    assert entry["owner"]["pid"] > 0
    assert len(entry["owner"]["owner_id"]) == 64


def test_fingerprint_is_project_and_scope_specific(
    hook_plugin_env: HookEnv,
) -> None:
    prompt = "Build the same named deliverable without a tracked task ID."
    project_a = hook_plugin_env.cwd / "project-a"
    project_b = hook_plugin_env.cwd / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    project_a_backend = {
        "GLUDD_PROJECT_ROOT": str(project_a),
        "GLUDD_DISPATCH_SCOPE": "backend",
    }
    project_a_frontend = {
        "GLUDD_PROJECT_ROOT": str(project_a),
        "GLUDD_DISPATCH_SCOPE": "frontend",
    }
    project_b_backend = {
        "GLUDD_PROJECT_ROOT": str(project_b),
        "GLUDD_DISPATCH_SCOPE": "backend",
    }

    first = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "task"},
        output=_dispatch_args(prompt),
        env_overrides=project_a_backend,
    )
    other_scope = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "task"},
        output=_dispatch_args(prompt),
        env_overrides=project_a_frontend,
    )
    other_project = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "task"},
        output=_dispatch_args(prompt),
        env_overrides=project_b_backend,
    )
    duplicate = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "task"},
        output=_dispatch_args(prompt),
        env_overrides=project_a_backend,
    )

    assert first.returncode == 0, first.stderr
    assert other_scope.returncode == 0, other_scope.stderr
    assert other_project.returncode == 0, other_project.stderr
    assert duplicate.returncode != 0
    ledger = hook_plugin_env.read_json("GLUDD_DISPATCH_DEDUP_STATE")
    assert isinstance(ledger, dict)
    entries = ledger["entries"]
    assert isinstance(entries, dict)
    assert len(entries) == 3


def test_v2_ledger_migrates_atomically_without_retaining_prompt_content(
    hook_plugin_env: HookEnv,
) -> None:
    prompt = "Migrate private marker ZEUS-ORCHID-441 without redispatching it."
    normalized = f"task\n{prompt.lower()}"
    legacy_fingerprint = hashlib.sha256(normalized.encode()).hexdigest()
    ledger_path = hook_plugin_env.state_path("GLUDD_DISPATCH_DEDUP_STATE")
    ledger_path.write_text(
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

    duplicate = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "task"},
        output=_dispatch_args(prompt),
    )

    assert duplicate.returncode != 0
    migrated_text = ledger_path.read_text(encoding="utf-8")
    migrated = json.loads(migrated_text)
    assert migrated["version"] == 3
    assert "zeus-orchid-441" not in migrated_text.lower()
    entry = next(iter(migrated["entries"].values()))
    assert "normalized_spec" not in entry
    assert "prompt_head" not in entry
    assert entry["status"] == "completed"


def test_duplicate_diagnostics_and_v3_state_do_not_echo_prompt_content(
    hook_plugin_env: HookEnv,
) -> None:
    prompt = "Handle confidential marker NEBULA-CEDAR-884."
    first = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "task"},
        output=_dispatch_args(prompt),
    )
    duplicate = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "task"},
        output=_dispatch_args(prompt),
    )

    assert first.returncode == 0, first.stderr
    assert duplicate.returncode != 0
    assert "nebula-cedar-884" not in duplicate.stderr.lower()
    ledger_text = hook_plugin_env.state_path(
        "GLUDD_DISPATCH_DEDUP_STATE"
    ).read_text(encoding="utf-8")
    assert "nebula-cedar-884" not in ledger_text.lower()


def test_live_dispatch_ledger_writer_blocks_instead_of_losing_an_owner(
    hook_plugin_env: HookEnv,
) -> None:
    ledger = hook_plugin_env.state_path("GLUDD_DISPATCH_DEDUP_STATE")
    lock = Path(f"{ledger}.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        json.dumps({"pid": os.getpid(), "created_at_ms": int(time.time() * 1000)}),
        encoding="utf-8",
    )

    result = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "task"},
        output=_dispatch_args("Own S83.166 without losing a concurrent writer."),
    )

    assert result.returncode != 0
    assert "ledger lock is busy" in result.stderr
    assert not ledger.exists()


def test_dead_dispatch_ledger_writer_is_reclaimed_and_state_is_private(
    hook_plugin_env: HookEnv,
) -> None:
    ledger = hook_plugin_env.state_path("GLUDD_DISPATCH_DEDUP_STATE")
    lock = Path(f"{ledger}.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        json.dumps({"pid": 999_999_999, "created_at_ms": 0}),
        encoding="utf-8",
    )

    result = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "task"},
        output=_dispatch_args("Own S83.166 after a crashed prior writer."),
    )

    assert result.returncode == 0, result.stderr
    assert ledger.exists()
    assert stat.S_IMODE(ledger.stat().st_mode) == 0o600
    assert not lock.exists()


def test_dispatch_dedup_contract_is_not_a_placeholder() -> None:
    root = Path(__file__).resolve().parents[2]
    plugin = (root / ".opencode/plugin/enforce-delegate.ts").read_text(
        encoding="utf-8"
    )
    owner = (root / ".opencode/lib/dispatch_dedup.ts").read_text(encoding="utf-8")
    makefile = compose_makefile(root / "Makefile")
    checker = (root / "scripts/check_dispatch_dedup.py").read_text(encoding="utf-8")
    documentation = (root / "docs/WAVE_ENFORCEMENT.md").read_text(encoding="utf-8")

    assert "registerDispatch" in plugin
    assert "GLUDD_DISPATCH_DEDUP_STATE" in owner
    assert "DUPLICATE DISPATCH DENIED" in owner
    assert "ledger lock is busy" in owner
    assert "_subagent-dedup-guard:\n\t@true" not in makefile
    assert "assuming clean" not in checker
    assert "Content-addressed ownership ledger" in documentation
    assert "langchain-ai/langgraph/issues/7417" in documentation


def test_checker_reports_absent_ledger_as_inactive_not_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        check_dispatch_dedup,
        "DISPATCH_STATE_FILE",
        str(tmp_path / "absent.json"),
    )

    assert check_dispatch_dedup.main() == 0
    output = capsys.readouterr().out
    assert "INACTIVE" in output
    assert "clean" not in output.lower()


def test_checker_fails_closed_on_corrupt_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = tmp_path / "ledger.json"
    ledger.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(check_dispatch_dedup, "DISPATCH_STATE_FILE", str(ledger))

    assert check_dispatch_dedup.main() == 2


def test_checker_accepts_plugin_ledger_and_counts_denied_duplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    normalized_spec = "task\\nfix s83.157"
    fingerprint = hashlib.sha256(normalized_spec.encode()).hexdigest()
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps(
            {
                "version": 2,
                "entries": {
                    fingerprint: {
                        "fingerprint": fingerprint,
                        "normalized_spec": normalized_spec,
                        "prompt_head": "Fix S83.157",
                        "task_ids": ["S83.157"],
                        "tool": "task",
                        "status": "completed",
                        "attempts": 1,
                        "denied_duplicates": 2,
                        "first_dispatched_at": 1,
                        "updated_at": 2,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(check_dispatch_dedup, "DISPATCH_STATE_FILE", str(ledger))

    assert check_dispatch_dedup.main() == 0
    assert "duplicates_blocked=2" in capsys.readouterr().out


def _v3_entry(status: str = "cancelled") -> tuple[str, dict[str, object]]:
    project_digest = "b" * 64
    scope_digest = "c" * 64
    spec_digest = "d" * 64
    identity = "\n".join(
        [
            "dispatch-ledger-v3",
            "task",
            project_digest,
            scope_digest,
            spec_digest,
        ]
    )
    fingerprint = hashlib.sha256(identity.encode()).hexdigest()
    return fingerprint, {
        "fingerprint": fingerprint,
        "project_digest": project_digest,
        "scope_digest": scope_digest,
        "spec_digest": spec_digest,
        "task_ids": ["S83.157"],
        "tool": "task",
        "status": status,
        "attempts": 2,
        "denied_duplicates": 3,
        "stale_recoveries": 1,
        "first_dispatched_at": 1,
        "updated_at": 2,
        "owner": {
            "owner_id": "e" * 64,
            "pid": 123,
            "process_started_at_ms": 1,
            "claimed_at": 1,
        },
        "terminal_reason": "cancelled" if status == "cancelled" else "success",
    }


def test_checker_accepts_content_free_v3_ledger_and_cancelled_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fingerprint, entry = _v3_entry()
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps({"version": 3, "entries": {fingerprint: entry}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(check_dispatch_dedup, "DISPATCH_STATE_FILE", str(ledger))

    assert check_dispatch_dedup.main() == 0
    output = capsys.readouterr().out
    assert "cancelled=1" in output
    assert "duplicates_blocked=3" in output


def test_checker_rejects_v3_entry_with_ambiguous_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fingerprint, entry = _v3_entry(status="in_progress")
    entry["owner"] = {"pid": 123}
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps({"version": 3, "entries": {fingerprint: entry}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(check_dispatch_dedup, "DISPATCH_STATE_FILE", str(ledger))

    assert check_dispatch_dedup.main() == 2


@pytest.mark.parametrize(
    "case",
    [
        "fingerprint_mismatch",
        "status",
        "attempts",
        "duplicates",
        "task_ids_type",
        "task_ids_order",
        "digests",
        "tool",
        "scoped_digest",
        "stale_recoveries",
        "timestamp",
        "owner_type",
        "active_terminal_reason",
        "missing_terminal_reason",
    ],
)
def test_checker_rejects_each_malformed_v3_transition_field(
    case: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fingerprint, entry = _v3_entry()
    if case == "fingerprint_mismatch":
        entry["fingerprint"] = "f" * 64
    elif case == "status":
        entry["status"] = "unknown"
    elif case == "attempts":
        entry["attempts"] = True
    elif case == "duplicates":
        entry["denied_duplicates"] = -1
    elif case == "task_ids_type":
        entry["task_ids"] = "S83.157"
    elif case == "task_ids_order":
        entry["task_ids"] = ["S83.158", "S83.157"]
    elif case == "digests":
        entry["project_digest"] = "not-a-digest"
    elif case == "tool":
        entry["tool"] = "unknown"
    elif case == "scoped_digest":
        entry["spec_digest"] = "f" * 64
    elif case == "stale_recoveries":
        entry["stale_recoveries"] = -1
    elif case == "timestamp":
        entry["updated_at"] = -1
    elif case == "owner_type":
        entry["owner"] = "ambiguous"
    elif case == "active_terminal_reason":
        entry["status"] = "in_progress"
    elif case == "missing_terminal_reason":
        entry.pop("terminal_reason")
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps({"version": 3, "entries": {fingerprint: entry}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(check_dispatch_dedup, "DISPATCH_STATE_FILE", str(ledger))

    assert check_dispatch_dedup.main() == 2


@pytest.mark.parametrize(
    "state",
    [
        [],
        {"version": 99, "entries": {}},
        {"version": 3, "entries": []},
        {"version": 3, "entries": {"invalid": {}}},
        {"version": 3, "entries": {"a" * 64: "invalid"}},
    ],
)
def test_checker_rejects_malformed_ledger_envelopes(
    state: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(check_dispatch_dedup, "DISPATCH_STATE_FILE", str(ledger))

    assert check_dispatch_dedup.main() == 2


def test_checker_rejects_v2_entry_without_normalized_spec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fingerprint = "a" * 64
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps(
            {
                "version": 2,
                "entries": {
                    fingerprint: {
                        "fingerprint": fingerprint,
                        "task_ids": [],
                        "tool": "task",
                        "status": "failed",
                        "attempts": 1,
                        "denied_duplicates": 0,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(check_dispatch_dedup, "DISPATCH_STATE_FILE", str(ledger))

    assert check_dispatch_dedup.main() == 2
