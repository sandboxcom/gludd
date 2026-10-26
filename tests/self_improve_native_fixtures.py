"""Strict controlled retirement inputs for self-improvement consumer unit tests.

These adapters provide no native gate, ownership or retirement proof. The real
native worktree-retirement fixture separately measures those production owners.
"""

from __future__ import annotations

import json
from pathlib import Path

from general_ludd.self_improve.runtime import MakeResult


def controlled_retirement_result(
    target: str, values: dict[str, str], *, branch: str, worktree: Path, head: str,
) -> MakeResult:
    """Require exact fixture context and make removal actually observable."""
    assert target in {"agent-cleanup", "workstream-unregister"}
    assert values["BRANCH"] == branch and branch
    assert values["WORKTREE"] == str(worktree)
    key = "WORKSTREAM_EXPECTED_HEAD" if target == "workstream-unregister" else "AGENT_CLEANUP_EXPECTED_HEAD"
    assert values[key] == head and len(head) == 40
    payload: dict[str, object] = {
        "branch": branch, "worktree": str(worktree), "head": head, "native_ownership_verified": 1,
    }
    progress = ""
    if target == "workstream-unregister":
        payload["status"] = "released"
    else:
        assert values["AGENT_CLEANUP_PURPOSE"] in {"merged", "self-improve-evaluation"}
        assert values["AGENT_CLEANUP_VALIDATE_ONLY"] in {"0", "1"}
        plan = values["AGENT_CLEANUP_VALIDATE_ONLY"] == "1"
        payload.update(status="planned" if plan else "removed", validate_only=plan)
        if not plan:
            for number in range(16):
                archive = worktree.with_name(f"{worktree.name}-retired-{number}")
                if not archive.exists():
                    worktree.rename(archive)
                    break
            else:
                raise AssertionError("owned fixture retirement archive bound exceeded")
            progress = "owned fixture archive progress\n"
    return MakeResult(
        ("make", target, *(f"{key}={value}" for key, value in values.items())),
        0, progress + json.dumps(payload), "", 0.01,
    )


def controlled_revision_result(reference: str, head: str) -> MakeResult:
    """Provide the actual owner's structured boundary without native proof."""
    return MakeResult(
        ("make", "git-show-commit", f"C={reference}"), 0,
        f"--- {reference} summary ---\n{head}\nparent: fixture\n--- files touched ---\n",
        "", 0.01,
    )
