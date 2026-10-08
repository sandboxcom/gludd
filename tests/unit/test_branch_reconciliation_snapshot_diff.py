from __future__ import annotations

import copy
import io
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from typing import cast

import pytest
from scripts import branch_reconciliation_inventory as inventory

from tests.unit import test_branch_reconciliation_inventory as fixtures


def _refresh_snapshot_seal(snapshot: dict[str, object]) -> None:
    body = {
        key: snapshot[key]
        for key in (
            "approval_keyring_digest",
            "branches",
            "counts",
            "freshness_digest",
            "heads",
            "plan_digest",
            "target",
            "verification_time",
        )
    }
    snapshot["snapshot_digest"] = inventory.canonical_document_digest(body)


def _reseal_snapshot(
    source: inventory.ReconciliationSnapshotPayload,
    branches: list[dict[str, object]],
    *,
    marker: str,
) -> dict[str, object]:
    payload = cast(dict[str, object], copy.deepcopy(source))
    ordered = sorted(branches, key=lambda branch: cast(str, branch["ref"]))
    priority = {
        "explicitly-retired": 0,
        "merged": 1,
        "pending": 2,
        "stale": 3,
        "blocked": 4,
    }
    grouped: dict[str, list[dict[str, object]]] = {}
    for branch in ordered:
        grouped.setdefault(cast(str, branch["expected_tip"]), []).append(branch)
    heads = [
        {
            "expected_tip": head,
            "refs": sorted(cast(str, branch["ref"]) for branch in aliases),
            "status": max(
                (cast(str, branch["status"]) for branch in aliases),
                key=priority.__getitem__,
            ),
        }
        for head, aliases in sorted(grouped.items())
    ]
    counts = {
        "blocked": sum(branch["status"] == "blocked" for branch in ordered),
        "explicitly_retired": sum(
            branch["status"] == "explicitly-retired" for branch in ordered
        ),
        "merged": sum(branch["status"] == "merged" for branch in ordered),
        "pending": sum(branch["status"] == "pending" for branch in ordered),
        "stale": sum(branch["status"] == "stale" for branch in ordered),
        "total_branches": len(ordered),
        "total_heads": len(heads),
    }
    payload.update(
        {
            "approval_keyring_digest": None,
            "branches": ordered,
            "complete": not any(
                branch["status"] in {"blocked", "pending", "stale"}
                for branch in ordered
            ),
            "counts": counts,
            "freshness_digest": inventory.canonical_document_digest(
                {"freshness": marker}
            ),
            "heads": heads,
            "plan_digest": inventory.canonical_document_digest({"plan": marker}),
            "verification_time": None,
        }
    )
    _refresh_snapshot_seal(payload)
    return payload


def _snapshot_diff_fixture() -> dict[str, object]:
    before = inventory.build_reconciliation_snapshot(
        fixtures._snapshot_fixture("ancestor")
    )
    branches = [
        cast(dict[str, object], copy.deepcopy(branch))
        for branch in before["branches"]
        if branch["ref"] != "refs/heads/feature/blocked"
    ]
    pending = next(
        branch for branch in branches if branch["ref"] == "refs/heads/feature/pending"
    )
    pending.update({"current_head": fixtures.FINAL_TARGET_HEAD, "status": "stale"})
    added = cast(dict[str, object], copy.deepcopy(pending))
    added.update(
        {
            "current_head": fixtures.EMPTY_HEAD,
            "expected_tip": fixtures.EMPTY_HEAD,
            "ref": "refs/heads/feature/added",
            "status": "pending",
        }
    )
    branches.append(added)
    return {"after": _reseal_snapshot(before, branches, marker="after"), "before": before}


def _invalid_diff(value: object, **bounds: int) -> None:
    with pytest.raises(
        inventory.InventoryError, match="invalid reconciliation snapshot diff"
    ):
        inventory.build_reconciliation_snapshot_diff(value, **bounds)


def test_reconciliation_snapshot_diff_is_deterministic_bounded_and_content_free() -> None:
    request = _snapshot_diff_fixture()

    result = inventory.build_reconciliation_snapshot_diff(request, detail_limit=2)

    assert result["counts"] == {
        "added": 1,
        "changed_head": 1,
        "removed": 1,
        "unchanged": 4,
    }
    assert result["details"] == {
        "added": [
            {"head": fixtures.EMPTY_HEAD, "ref": "refs/heads/feature/added"}
        ],
        "changed_head": [
            {
                "after_head": fixtures.FINAL_TARGET_HEAD,
                "before_head": fixtures.PAGE_HEADS[0],
                "ref": "refs/heads/feature/pending",
            }
        ],
        "removed": [
            {
                "head": fixtures.PAGE_HEADS[3],
                "ref": "refs/heads/feature/blocked",
            }
        ],
        "unchanged": [
            {
                "head": fixtures.PAGE_HEADS[1],
                "ref": "refs/heads/feature/merged",
            },
            {
                "head": fixtures.UNIQUE_HEAD,
                "ref": "refs/heads/feature/retired",
            },
        ],
    }
    assert result["details_truncated"] == {
        "added": False,
        "changed_head": False,
        "removed": False,
        "unchanged": True,
    }
    assert result == inventory.build_reconciliation_snapshot_diff(
        copy.deepcopy(request), detail_limit=2
    )
    encoded = json.dumps(result, sort_keys=True)
    assert all(
        sensitive not in encoded
        for sensitive in (
            "approval_key_id",
            "fresh_classification",
            "retirement_basis",
            "retirement_reason_code",
            "verification_time",
        )
    )


def test_reconciliation_snapshot_diff_rejects_unsealed_or_ambiguous_evidence() -> None:
    request = _snapshot_diff_fixture()
    after = cast(dict[str, object], request["after"])
    malformed = copy.deepcopy(request)
    cast(dict[str, object], malformed["after"])["extra"] = True
    tampered = copy.deepcopy(request)
    branches = cast(
        list[dict[str, object]], cast(dict[str, object], tampered["after"])["branches"]
    )
    branches[0]["current_head"] = fixtures.TARGET_HEAD
    digest_tampered = copy.deepcopy(request)
    cast(dict[str, object], digest_tampered["after"])["snapshot_digest"] = "0" * 64
    duplicate = copy.deepcopy(request)
    duplicate_after = cast(dict[str, object], duplicate["after"])
    duplicate_branches = cast(list[dict[str, object]], duplicate_after["branches"])
    duplicate["after"] = _reseal_snapshot(
        cast(inventory.ReconciliationSnapshotPayload, after),
        [*duplicate_branches, copy.deepcopy(duplicate_branches[0])],
        marker="duplicate",
    )
    mismatched = copy.deepcopy(request)
    mismatched_after = cast(dict[str, object], mismatched["after"])
    cast(dict[str, object], mismatched_after["target"])["ref"] = "refs/heads/other"
    mismatched["after"] = _reseal_snapshot(
        cast(inventory.ReconciliationSnapshotPayload, mismatched_after),
        cast(list[dict[str, object]], mismatched_after["branches"]),
        marker="mismatched",
    )
    for invalid in (
        None,
        malformed,
        tampered,
        digest_tampered,
        duplicate,
        mismatched,
    ):
        _invalid_diff(invalid)
    _invalid_diff(request, entry_limit=4)


Mutation = Callable[[dict[str, object]], None]


def _first_branch(snapshot: dict[str, object]) -> dict[str, object]:
    return cast(list[dict[str, object]], snapshot["branches"])[0]


def _retired_branch(snapshot: dict[str, object]) -> dict[str, object]:
    return next(
        branch
        for branch in cast(list[dict[str, object]], snapshot["branches"])
        if branch["status"] == "explicitly-retired"
    )


def _first_head(snapshot: dict[str, object]) -> dict[str, object]:
    return cast(list[dict[str, object]], snapshot["heads"])[0]


def _duplicate_first_head_ref(snapshot: dict[str, object]) -> None:
    head = _first_head(snapshot)
    ref = cast(list[str], head["refs"])[0]
    head["refs"] = [ref, ref]


def _mutate_after(request: dict[str, object], mutation: Mutation) -> dict[str, object]:
    changed = copy.deepcopy(request)
    after = cast(dict[str, object], changed["after"])
    mutation(after)
    _refresh_snapshot_seal(after)
    return changed


@pytest.mark.parametrize(
    "mutation",
    [
        lambda snapshot: cast(dict[str, object], snapshot["bounds"]).__setitem__(
            "approval_key_limit", 1
        ),
        lambda snapshot: cast(dict[str, object], snapshot["counts"]).__setitem__(
            "pending", 99
        ),
        lambda snapshot: snapshot.__setitem__("complete", True),
        lambda snapshot: cast(list[dict[str, object]], snapshot["heads"])[0].update(
            {"refs": []}
        ),
        lambda snapshot: cast(list[dict[str, object]], snapshot["branches"])[0].update(
            {"fresh_classification": "free-form"}
        ),
        lambda snapshot: cast(list[dict[str, object]], snapshot["branches"])[0].update(
            {"status": "merged"}
        ),
        lambda snapshot: _first_branch(snapshot).update({"ref": "invalid ref"}),
        lambda snapshot: _first_branch(snapshot).update({"status": "free-form"}),
        lambda snapshot: _first_branch(snapshot).update(
            {"retirement_basis": "free-form"}
        ),
        lambda snapshot: _first_branch(snapshot).update(
            {"retirement_reason_code": "free-form"}
        ),
        lambda snapshot: _retired_branch(snapshot).update(
            {"current_head": fixtures.EMPTY_HEAD}
        ),
        lambda snapshot: _retired_branch(snapshot).update(
            {"retirement_reason_code": "abandoned"}
        ),
        lambda snapshot: _first_branch(snapshot).update(
            {"approval_digest": "0" * 64}
        ),
        lambda snapshot: _first_head(snapshot).update({"refs": ["invalid ref"]}),
        _duplicate_first_head_ref,
        lambda snapshot: snapshot.__setitem__("mode", "free-form"),
        lambda snapshot: snapshot.__setitem__("branches", None),
        lambda snapshot: _first_head(snapshot).update({"status": "pending"}),
        lambda snapshot: snapshot.__setitem__(
            "approval_keyring_digest", "0" * 64
        ),
    ],
)
def test_reconciliation_snapshot_diff_rejects_resealed_schema_drift(
    mutation: Mutation,
) -> None:
    _invalid_diff(_mutate_after(_snapshot_diff_fixture(), mutation))


@pytest.mark.parametrize(
    "bounds",
    [
        {"detail_limit": -1},
        {"detail_limit": 101},
        {"entry_limit": 0},
        {"entry_limit": 10_001},
    ],
)
def test_reconciliation_snapshot_diff_rejects_invalid_requested_bounds(
    bounds: dict[str, int],
) -> None:
    _invalid_diff(_snapshot_diff_fixture(), **bounds)


def test_reconciliation_snapshot_diff_accepts_authenticated_snapshot_schema() -> None:
    snapshot = inventory.build_reconciliation_snapshot(
        fixtures._snapshot_fixture(),
        approval_trust=fixtures.APPROVAL_TRUST,
        verification_time=fixtures.APPROVAL_TIME,
    )

    result = inventory.build_reconciliation_snapshot_diff(
        {"after": snapshot, "before": copy.deepcopy(snapshot)}
    )

    assert result["counts"] == {
        "added": 0,
        "changed_head": 0,
        "removed": 0,
        "unchanged": 5,
    }


def test_reconciliation_snapshot_diff_cli_never_invokes_git(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_snapshot_diff_fixture())))

    def forbidden_git(
        _argv: Sequence[str], _cwd: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        raise AssertionError("snapshot diff must not invoke Git")

    rc = inventory.main(
        [*fixtures.CLI_ARGS, "--reconciliation-snapshot-diff"], run=forbidden_git
    )
    payload = json.loads(capsys.readouterr().out)
    assert (rc, payload["mode"], payload["counts"]["changed_head"]) == (
        0,
        "reconciliation-snapshot-diff",
        1,
    )
