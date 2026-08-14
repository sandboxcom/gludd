"""Tests for the bounded branch-reconciliation inventory."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
from scripts import branch_reconciliation_inventory as inventory

ROOT = Path(__file__).parent.parent.parent
TARGET_HEAD = "a" * 40
ANCESTOR_HEAD = "b" * 40
PATCH_HEAD = "c" * 40
UNIQUE_HEAD = "d" * 40
EMPTY_HEAD = "e" * 40


class FakeGit:
    """Return deterministic results for the read-only Git command set."""

    def __init__(
        self,
        *,
        refs: Sequence[tuple[str, str]] = (),
        ancestors: frozenset[str] = frozenset(),
        cherries: dict[str, str] | None = None,
        commit_counts: dict[str, int] | None = None,
        target_valid: bool = True,
    ) -> None:
        self.refs = list(refs)
        self.ancestors = ancestors
        self.cherries = cherries or {}
        self.commit_counts = commit_counts or {}
        self.target_valid = target_valid
        self.calls: list[list[str]] = []

    def __call__(
        self, argv: Sequence[str], cwd: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        del cwd
        args = list(argv)
        self.calls.append(args)
        if args[1:4] == ["rev-parse", "--symbolic-full-name", "--verify"]:
            return self._result(args, 0, "refs/heads/development\n") if self.target_valid else self._result(args, 1)
        if args[1:4] == ["rev-parse", "--verify", "--quiet"]:
            return self._result(args, 0, f"{TARGET_HEAD}\n") if self.target_valid else self._result(args, 1)
        if args[1] == "for-each-ref":
            output = "".join(f"{ref}\t{head}\n" for ref, head in self.refs)
            return self._result(args, 0, output)
        if args[1:3] == ["merge-base", "--is-ancestor"]:
            return self._result(args, 0 if args[3] in self.ancestors else 1)
        if args[1] == "rev-list":
            head = args[-1].split("..", maxsplit=1)[1]
            default_count = max(1, len(self.cherries.get(head, "").splitlines()))
            return self._result(args, 0, f"{self.commit_counts.get(head, default_count)}\n")
        if args[1] == "cherry":
            return self._result(args, 0, self.cherries.get(args[3], ""))
        raise AssertionError(f"unexpected Git command: {args}")

    @staticmethod
    def _result(
        args: Sequence[str], returncode: int, stdout: str = "", stderr: str = ""
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def test_ancestor_is_historical_without_patch_scan() -> None:
    fake = FakeGit(
        refs=[
            ("refs/heads/development", TARGET_HEAD),
            ("refs/heads/feature/merged", ANCESTOR_HEAD),
        ],
        ancestors=frozenset({ANCESTOR_HEAD}),
    )

    result = inventory.collect_inventory("development", 10, run=fake)

    assert result["branches"] == [
        {
            "classification": "ancestor",
            "head": ANCESTOR_HEAD,
            "lifecycle": "historical",
            "name": "feature/merged",
            "patch_equivalent_commits": 0,
            "ref": "refs/heads/feature/merged",
            "unique_commits": 0,
        }
    ]
    assert not any(call[1] == "cherry" for call in fake.calls)


def test_all_minus_cherry_rows_are_patch_equivalent_and_historical() -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/replayed", PATCH_HEAD)],
        cherries={PATCH_HEAD: f"- {PATCH_HEAD}\n- {'f' * 40}\n"},
    )

    result = inventory.collect_inventory("development", 10, run=fake)

    branch = result["branches"][0]
    assert branch["classification"] == "patch-equivalent"
    assert branch["lifecycle"] == "historical"
    assert branch["patch_equivalent_commits"] == 2
    assert branch["unique_commits"] == 0


def test_any_plus_cherry_row_is_unique_and_current() -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/current", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"- {PATCH_HEAD}\n+ {UNIQUE_HEAD}\n"},
    )

    result = inventory.collect_inventory("development", 10, run=fake)

    branch = result["branches"][0]
    assert branch["classification"] == "unique"
    assert branch["lifecycle"] == "current"
    assert branch["patch_equivalent_commits"] == 1
    assert branch["unique_commits"] == 1


def test_nonancestor_without_comparable_patch_rows_fails_closed_as_unique() -> None:
    fake = FakeGit(refs=[("refs/heads/feature/merge-only", EMPTY_HEAD)])

    result = inventory.collect_inventory("development", 10, run=fake)

    branch = result["branches"][0]
    assert branch["classification"] == "unique"
    assert branch["lifecycle"] == "current"
    assert branch["patch_equivalent_commits"] == 0
    assert branch["unique_commits"] == 0


def test_branch_over_commit_scan_bound_fails_closed_without_cherry() -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/large", UNIQUE_HEAD)],
        commit_counts={UNIQUE_HEAD: inventory.COMMIT_SCAN_LIMIT + 1},
    )

    result = inventory.collect_inventory("development", 10, run=fake)

    branch = result["branches"][0]
    assert branch["classification"] == "unique"
    assert branch["lifecycle"] == "current"
    assert not any(call[1] == "cherry" for call in fake.calls)


def test_limit_bounds_classification_and_reports_truncation() -> None:
    fake = FakeGit(
        refs=[
            ("refs/heads/feature/z", UNIQUE_HEAD),
            ("refs/heads/development", TARGET_HEAD),
            ("refs/heads/feature/a", ANCESTOR_HEAD),
            ("refs/heads/feature/b", PATCH_HEAD),
        ],
        ancestors=frozenset({ANCESTOR_HEAD}),
        cherries={
            PATCH_HEAD: f"- {PATCH_HEAD}\n",
            UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n",
        },
    )

    result = inventory.collect_inventory("development", 2, run=fake)

    assert [branch["name"] for branch in result["branches"]] == [
        "feature/a",
        "feature/b",
    ]
    assert result["limit"] == 2
    assert result["truncated"] is True
    assert result["counts"]["returned"] == 2
    assert sum(call[1] == "merge-base" for call in fake.calls) == 2


@pytest.mark.parametrize("limit", [0, -1, inventory.MAX_LIMIT + 1])
def test_limit_outside_bounded_contract_is_rejected(limit: int) -> None:
    with pytest.raises(inventory.InventoryError, match="limit"):
        inventory.collect_inventory("development", limit, run=FakeGit())


def test_invalid_target_ref_fails_before_enumeration() -> None:
    fake = FakeGit(target_valid=False)

    with pytest.raises(inventory.InventoryError, match="invalid target ref"):
        inventory.collect_inventory("missing", 10, run=fake)

    assert not any(call[1] == "for-each-ref" for call in fake.calls)


def test_option_shaped_target_is_rejected_without_git_call() -> None:
    fake = FakeGit()

    with pytest.raises(inventory.InventoryError, match="invalid target ref"):
        inventory.collect_inventory("--help", 10, run=fake)

    assert fake.calls == []


def test_malformed_ref_inventory_fails_closed() -> None:
    fake = FakeGit(refs=[("refs/tags/not-a-head", ANCESTOR_HEAD)])

    with pytest.raises(inventory.InventoryError, match="malformed local branch"):
        inventory.collect_inventory("development", 10, run=fake)


def test_main_emits_machine_readable_error(capsys: pytest.CaptureFixture[str]) -> None:
    rc = inventory.main(["--target", "missing", "--limit", "10"], run=FakeGit(target_valid=False))

    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "error": "invalid target ref: missing",
        "ok": False,
        "schema_version": 1,
    }


def test_make_target_and_contract_are_tracked() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    contract = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text(encoding="utf-8")
    )

    assert "branch-reconciliation-inventory:" in makefile
    assert "scripts/branch_reconciliation_inventory.py" in makefile
    entry = next(
        target
        for target in contract["targets"]
        if target["name"] == "branch-reconciliation-inventory"
    )
    assert entry["make_variables"] == ["RECONCILE_TARGET", "RECONCILE_LIMIT"]
    assert (
        entry["behavior"]
        == "make branch-reconciliation-inventory RECONCILE_TARGET=development RECONCILE_LIMIT=20"
    )


def test_git_command_set_is_read_only() -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/current", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
    )

    inventory.collect_inventory("development", 10, run=fake)

    assert {call[1] for call in fake.calls} <= {
        "rev-parse",
        "rev-list",
        "for-each-ref",
        "merge-base",
        "cherry",
    }
