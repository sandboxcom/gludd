"""Runtime proofs for content-addressed subagent dispatch deduplication."""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

import pytest
from scripts import check_dispatch_dedup

from tests.unit._hook_fixtures import HookEnv, hook_plugin_env_impl


@pytest.fixture
def hook_plugin_env(tmp_path: Path):
    yield from hook_plugin_env_impl(tmp_path)


def _dispatch_args(prompt: str) -> dict[str, dict[str, str]]:
    return {"args": {"prompt": prompt}}


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
    assert "task_id=S83.157" in reworded.stderr


def test_failed_dispatch_can_retry_but_completed_dispatch_cannot(
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
    retry = hook_plugin_env.invoke(
        "enforce-delegate.ts",
        "tool.execute.before",
        input={"tool": "workflow"},
        output=_dispatch_args(prompt),
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
    assert retry.returncode == 0, retry.stderr
    assert completed.returncode == 0, completed.stderr
    assert duplicate.returncode != 0
    assert "status=completed" in duplicate.stderr


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
    makefile = (root / "Makefile").read_text(encoding="utf-8")
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
    fingerprint = "a" * 64
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps(
            {
                "version": 2,
                "entries": {
                    fingerprint: {
                        "fingerprint": fingerprint,
                        "normalized_spec": "task\\nfix s83.157",
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
