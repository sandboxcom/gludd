from __future__ import annotations

import copy
import io
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from scripts import branch_reconciliation_inventory as inventory
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).parent.parent.parent
TARGET_HEAD = "a" * 40
ANCESTOR_HEAD = "b" * 40
PATCH_HEAD = "c" * 40
UNIQUE_HEAD = "d" * 40
EMPTY_HEAD = "e" * 40
FINAL_TARGET_HEAD = "f" * 40
PAGE_HEADS = tuple(character * 40 for character in "1234")
CLI_ARGS = ["--target", "development", "--limit", "2", "--after", ""]
APPROVAL_KEY_ID = "release-reviewer-2026"
APPROVAL_REVIEWER_DIGEST = "9" * 64
APPROVAL_TIME = "2026-10-07T23:30:00Z"
APPROVAL_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"r" * 32)
APPROVAL_TRUST = {
    "keys": [{
        "key_id": APPROVAL_KEY_ID,
        "public_key": APPROVAL_PRIVATE_KEY.public_key().public_bytes_raw().hex(),
        "reviewer_identity_digest": APPROVAL_REVIEWER_DIGEST, "status": "active",
    }],
    "schema_version": 1,
}


class FakeGit:
    def __init__(
        self,
        *,
        refs: Sequence[tuple[str, str]] = (),
        ancestors: frozenset[str] = frozenset(),
        cherries: dict[str, str] | None = None,
        commit_counts: dict[str, int] | None = None,
        target_valid: bool = True,
        cursor_valid: bool = True,
        start_after_supported: bool = True,
        inclusive_start_after: bool = False,
        regressive_start_after: bool = False,
        subjects: dict[str, str] | None = None,
        changed_paths: dict[str, Sequence[str]] | None = None,
        malformed_path_heads: frozenset[str] = frozenset(),
        verification_refs: Sequence[tuple[str, str]] | None = None,
        verification_output: str | None = None,
        diff_results: dict[str, tuple[int, str, str]] | None = None,
        merge_trees: dict[str | tuple[str, str], tuple[int, str, str]] | None = None,
    ) -> None:
        self.refs = list(refs)
        self.ancestors = ancestors
        self.cherries = cherries or {}
        self.commit_counts = commit_counts or {}
        self.target_valid = target_valid
        self.cursor_valid = cursor_valid
        self.start_after_supported = start_after_supported
        self.inclusive_start_after = inclusive_start_after
        self.regressive_start_after = regressive_start_after
        self.subjects = subjects or {}
        self.changed_paths = changed_paths or {}
        self.malformed_path_heads = malformed_path_heads
        self.verification_refs = (
            None if verification_refs is None else list(verification_refs)
        )
        self.verification_output = verification_output
        self.diff_results = diff_results or {}
        self.merge_trees = merge_trees or {}
        self.calls: list[list[str]] = []

    def __call__(
        self, argv: Sequence[str], cwd: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        del cwd
        args = list(argv)
        self.calls.append(args)
        if args[1:4] == ["rev-parse", "--symbolic-full-name", "--verify"]:
            if self.target_valid:
                return self._result(args, 0, "refs/heads/development\n")
            return self._result(args, 1)
        if args[1:4] == ["rev-parse", "--verify", "--quiet"]:
            if self.target_valid:
                return self._result(args, 0, f"{TARGET_HEAD}\n")
            return self._result(args, 1)
        if args[1] == "check-ref-format":
            return self._result(args, 0) if self.cursor_valid else self._result(args, 1)
        if args[1:3] == ["show-ref", "--verify"]:
            if self.verification_output is not None:
                return self._result(args, 0, self.verification_output)
            current_refs = dict(self.refs)
            current_refs.setdefault("refs/heads/development", TARGET_HEAD)
            if self.verification_refs is not None:
                current_refs = dict(self.verification_refs)
            requested = args[4:]
            output = "".join(
                f"{current_refs[ref]} {ref}\n"
                for ref in requested
                if ref in current_refs
            )
            returncode = 0 if all(ref in current_refs for ref in requested) else 1
            return self._result(args, returncode, output)
        if args[1] == "for-each-ref":
            if (
                not self.start_after_supported
                and any(option.startswith("--start-after=") for option in args)
            ):
                return self._result(args, 129, stderr="error: unknown option 'start-after'")
            entries = sorted(self.refs)
            start_after = next(
                (
                    option.removeprefix("--start-after=")
                    for option in args
                    if option.startswith("--start-after=")
                ),
                "",
            )
            if start_after:
                if self.regressive_start_after:
                    entries = list(entries)
                elif self.inclusive_start_after:
                    entries = [entry for entry in entries if entry[0] >= start_after]
                else:
                    entries = [entry for entry in entries if entry[0] > start_after]
            count_option = next(
                option for option in args if option.startswith("--count=")
            )
            count = int(count_option.split("=", maxsplit=1)[1])
            output = "".join(f"{ref}\t{head}\n" for ref, head in entries[:count])
            return self._result(args, 0, output)
        if args[1:3] == ["merge-base", "--is-ancestor"]:
            return self._result(args, 0 if args[3] in self.ancestors else 1)
        if args[1] == "rev-list":
            head = args[-1].split("..", maxsplit=1)[1]
            default_count = max(1, len(self.cherries.get(head, "").splitlines()))
            return self._result(args, 0, f"{self.commit_counts.get(head, default_count)}\n")
        if args[1] == "cherry":
            return self._result(args, 0, self.cherries.get(args[3], ""))
        if args[1] == "show":
            head = args[-1]
            return self._result(args, 0, f"{self.subjects.get(head, 'subject')}\n")
        if args[1] == "diff-tree":
            head = args[-1]
            output = "\0".join(self.changed_paths.get(head, ()))
            if output and head not in self.malformed_path_heads:
                output += "\0"
            return self._result(args, 0, output)
        if args[1] == "diff":
            head = args[-2].split("...", maxsplit=1)[1]
            if head in self.diff_results:
                return self._result(args, *self.diff_results[head])
            output = "\0".join(self.changed_paths.get(head, ()))
            if output and head not in self.malformed_path_heads:
                output += "\0"
            return self._result(args, 0, output)
        if args[1] == "merge-tree":
            head = args[-1]
            returncode, stdout, stderr = self.merge_trees.get(
                (args[-2], head),
                self.merge_trees.get(head, (0, f"{EMPTY_HEAD}\0", "")),
            )
            return self._result(args, returncode, stdout, stderr)
        raise AssertionError(f"unexpected Git command: {args}")

    @staticmethod
    def _result(
        args: Sequence[str], returncode: int, stdout: str = "", stderr: str = ""
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def test_git_runner_turns_undecodable_evidence_into_bounded_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def undecodable(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    monkeypatch.setattr(subprocess, "run", undecodable)
    result = inventory._run(["git", "merge-tree"])
    assert result.returncode == 124
    assert "UnicodeDecodeError" not in result.stderr


@pytest.mark.parametrize(
    ("stdout", "stderr", "detail"),
    [
        ("", "", "git command failed"),
        ("stdout detail", "", "stdout detail"),
        ("", "stderr detail", "stderr detail"),
    ],
)
def test_checked_stdout_preserves_bounded_failure_detail(
    stdout: str, stderr: str, detail: str,
) -> None:
    def failed_run(
        argv: Sequence[str], cwd: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        del cwd
        return subprocess.CompletedProcess(argv, 2, stdout, stderr)
    with pytest.raises(inventory.InventoryError, match=detail):
        inventory._checked_stdout(["git", "status"], run=failed_run, cwd=None,
                                  label="probe failed")


def test_namespace_boundary_and_empty_head_name_fail_closed() -> None:
    tag_only = f"refs/tags/release\t{TARGET_HEAD}\n"
    assert inventory._parse_branch_entries(
        tag_only, allow_namespace_boundary=True) == []
    empty_head = f"refs/heads/\t{TARGET_HEAD}\n"
    with pytest.raises(inventory.InventoryError, match="malformed"):
        inventory._parse_branch_entries(empty_head, allow_namespace_boundary=True)


def test_ancestor_and_commit_count_failures_are_bounded() -> None:
    def unexpected_ancestor(
        argv: Sequence[str], cwd: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        del cwd
        return subprocess.CompletedProcess(argv, 128, "", "repository unavailable")
    with pytest.raises(inventory.InventoryError, match="repository unavailable"):
        inventory._ancestor(PATCH_HEAD, TARGET_HEAD, run=unexpected_ancestor, cwd=None)
    for output in ("not-a-count\n", "-1\n"):
        def invalid_count(
            argv: Sequence[str],
            cwd: str | None = None,
            *,
            value: str = output,
        ) -> subprocess.CompletedProcess[str]:
            del cwd
            return subprocess.CompletedProcess(argv, 0, value, "")
        with pytest.raises(inventory.InventoryError, match="malformed commit count"):
            inventory._commit_count(TARGET_HEAD, PATCH_HEAD, run=invalid_count, cwd=None)


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


@pytest.mark.parametrize(
    "verification_refs",
    [
        [
            ("refs/heads/development", PATCH_HEAD),
            ("refs/heads/feature/current", UNIQUE_HEAD),
        ],
        [
            ("refs/heads/development", TARGET_HEAD),
            ("refs/heads/feature/current", PATCH_HEAD),
        ],
        [("refs/heads/development", TARGET_HEAD)],
    ],
)
def test_page_snapshot_rejects_target_or_branch_movement(
    verification_refs: Sequence[tuple[str, str]],
) -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/current", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
        verification_refs=verification_refs,
    )

    with pytest.raises(inventory.InventoryError, match="page refs changed"):
        inventory.collect_inventory("development", 10, run=fake)


@pytest.mark.parametrize(
    "verification_output",
    [
        (
            f"{TARGET_HEAD} refs/heads/development\n"
            "not-an-object-id refs/heads/feature/current\n"
        ),
        (
            f"{TARGET_HEAD} refs/heads/development\n"
            f"{TARGET_HEAD} refs/heads/development\n"
            f"{UNIQUE_HEAD} refs/heads/feature/current\n"
        ),
        (
            f"{TARGET_HEAD} refs/heads/development\n"
            f"{UNIQUE_HEAD} refs/heads/feature/current\n"
            f"{PATCH_HEAD} refs/heads/feature/unrequested\n"
        ),
    ],
)
def test_page_snapshot_rejects_malformed_git_evidence(
    verification_output: str,
) -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/current", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
        verification_output=verification_output,
    )

    with pytest.raises(inventory.InventoryError, match="malformed page ref snapshot"):
        inventory.collect_inventory("development", 10, run=fake)


def test_page_snapshot_rejects_git_verification_failure() -> None:
    def failed_show_ref(
        argv: Sequence[str], cwd: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        del cwd
        return subprocess.CompletedProcess(argv, 128, "", "repository unavailable")

    with pytest.raises(
        inventory.InventoryError,
        match="page ref verification failed: repository unavailable",
    ):
        inventory._verify_page_snapshot(
            [],
            {
                "head": TARGET_HEAD,
                "input": "development",
                "ref": "refs/heads/development",
            },
            run=failed_show_ref,
            cwd=None,
        )


def test_page_snapshot_uses_one_exact_bounded_show_ref_command() -> None:
    branch_ref = "refs/heads/feature/current"
    fake = FakeGit(
        refs=[(branch_ref, UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
    )

    inventory.collect_inventory("development", 10, run=fake)

    verification_calls = [call for call in fake.calls if call[1] == "show-ref"]
    assert verification_calls == [
        [
            "git",
            "show-ref",
            "--verify",
            "--",
            "refs/heads/development",
            branch_ref,
        ]
    ]


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


def test_cursor_pages_are_strictly_greater_without_duplicates_or_gaps() -> None:
    refs = [("refs/heads/development", TARGET_HEAD), *[
        (f"refs/heads/feature/{name}", head)
        for name, head in zip("abcd", PAGE_HEADS, strict=True)
    ]]
    fake = FakeGit(refs=refs, ancestors=frozenset(PAGE_HEADS))
    first = inventory.collect_inventory("development", 2, after="", run=fake)
    second = inventory.collect_inventory(
        "development", 2, after=first["next_cursor"] or "", run=fake)
    combined = [
        branch["ref"] for page in (first, second) for branch in page["branches"]
    ]
    assert combined == [f"refs/heads/feature/{name}" for name in "abcd"]
    assert len(combined) == len(set(combined))
    assert first["after"] is None
    assert first["next_cursor"] == "refs/heads/feature/b"
    assert first["truncated"] is True
    assert second["after"] == first["next_cursor"]
    assert second["next_cursor"] is None
    assert second["truncated"] is False
    second_enumeration = [call for call in fake.calls
                          if call[1] == "for-each-ref"][1]
    assert "--start-after=refs/heads/feature/b" in second_enumeration
    assert not any(option.startswith("--sort=") for option in second_enumeration)
    assert "refs/heads" not in second_enumeration


def test_exhaustive_summary_handles_inclusive_cursor_without_duplicates() -> None:
    refs = [
        (f"refs/heads/feature/{name}", head)
        for name, head in zip("abcd", PAGE_HEADS, strict=True)
    ]
    fake = FakeGit(
        refs=refs,
        ancestors=frozenset(PAGE_HEADS),
        inclusive_start_after=True,
    )
    result = inventory.collect_summary("development", 2, run=fake)
    assert result["pages"] == 2
    assert [ref for group in result["groups"] for ref in group["refs"]] == [
        f"refs/heads/feature/{name}" for name in "abcd"
    ]
    assert result["counts"]["returned"] == 4


def test_inclusive_cursor_exhausts_after_the_last_unseen_ref() -> None:
    fake = FakeGit(
        refs=[
            (f"refs/heads/feature/{name}", PAGE_HEADS[index])
            for index, name in enumerate("abc")
        ],
        ancestors=frozenset(PAGE_HEADS),
        inclusive_start_after=True,
    )
    first = inventory.collect_inventory("development", 2, after="", run=fake)
    second = inventory.collect_inventory(
        "development", 2, after=first["next_cursor"] or "", run=fake)
    assert [branch["name"] for branch in second["branches"]] == ["feature/c"]
    assert second["truncated"] is False
    assert second["next_cursor"] is None


def test_cursor_backend_regression_uses_bounded_sorted_local_fallback() -> None:
    fake = FakeGit(
        refs=[
            ("refs/heads/fix-beta", PAGE_HEADS[0]),
            ("refs/heads/fix/dogfood", PAGE_HEADS[1]),
            ("refs/heads/fix/next", PAGE_HEADS[2]),
        ],
        ancestors=frozenset(PAGE_HEADS),
        regressive_start_after=True,
    )
    result = inventory.collect_inventory(
        "development", 2, after="refs/heads/fix/dogfood", run=fake)
    assert [branch["ref"] for branch in result["branches"]] == [
        "refs/heads/fix/next"
    ]
    enumerations = [call for call in fake.calls if call[1] == "for-each-ref"]
    assert len(enumerations) == 2
    assert "--sort=refname" in enumerations[1]
    assert "refs/heads" in enumerations[1]


def test_absent_cursor_is_a_strict_lexicographic_boundary() -> None:
    fake = FakeGit(
        refs=[
            ("refs/heads/feature/a", PAGE_HEADS[0]),
            ("refs/heads/feature/c", PAGE_HEADS[2]),
        ],
        ancestors=frozenset(PAGE_HEADS),
    )
    result = inventory.collect_inventory(
        "development", 2, after="refs/heads/feature/b", run=fake)
    assert [branch["ref"] for branch in result["branches"]] == [
        "refs/heads/feature/c"
    ]
    assert result["next_cursor"] is None


def test_legacy_git_cursor_fallback_is_bounded_and_gap_free() -> None:
    fake = FakeGit(
        refs=[
            (f"refs/heads/feature/{name}", head)
            for name, head in zip("abcd", PAGE_HEADS, strict=True)
        ],
        ancestors=frozenset(PAGE_HEADS),
        start_after_supported=False,
    )
    result = inventory.collect_inventory(
        "development", 2, after="refs/heads/feature/b", run=fake)
    assert [branch["ref"] for branch in result["branches"]] == [
        "refs/heads/feature/c",
        "refs/heads/feature/d",
    ]
    enumeration_calls = [call for call in fake.calls if call[1] == "for-each-ref"]
    assert len(enumeration_calls) == 2
    assert "--sort=refname" in enumeration_calls[1]
    assert f"--count={inventory.LOCAL_REF_SCAN_LIMIT + 1}" in enumeration_calls[1]
    assert "refs/heads" in enumeration_calls[1]


def test_legacy_git_cursor_fallback_fails_closed_at_scan_bound() -> None:
    fake = FakeGit(
        refs=[
            (f"refs/heads/feature/{index:04d}", PAGE_HEADS[0])
            for index in range(inventory.LOCAL_REF_SCAN_LIMIT + 1)
        ],
        start_after_supported=False,
    )
    with pytest.raises(inventory.InventoryError, match="scan exceeded"):
        inventory.collect_inventory(
            "development", 2, after="refs/heads/feature/0000", run=fake)


@pytest.mark.parametrize(
    ("cursor", "cursor_valid"),
    [
        ("feature/a", True),
        ("refs/heads/feature bad", True),
        ("refs/heads/-option", True),
        ("refs/heads/feature..bad", False),
        (" refs/heads/feature/a", True),
    ],
)
def test_invalid_cursor_fails_closed_before_enumeration(
    cursor: str,
    cursor_valid: bool,
) -> None:
    fake = FakeGit(cursor_valid=cursor_valid)
    with pytest.raises(inventory.InventoryError, match="cursor"):
        inventory.collect_inventory("development", 2, after=cursor, run=fake)
    assert not any(call[1] == "for-each-ref" for call in fake.calls)


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


def test_inventory_low_level_failures_remain_bounded() -> None:
    class InvalidCommitGit(FakeGit):
        def __call__(
            self, argv: Sequence[str], cwd: str | None = None
        ) -> subprocess.CompletedProcess[str]:
            args = list(argv)
            if args[1:4] == ["rev-parse", "--verify", "--quiet"]:
                return self._result(args, 0, "not-an-object\n")
            return super().__call__(args, cwd)
    with pytest.raises(inventory.InventoryError, match="invalid target ref"):
        inventory.collect_inventory("development", 1, run=InvalidCommitGit())
    with pytest.raises(inventory.InventoryError, match="malformed local branch"):
        inventory._parse_branch_entries(
            "refs/heads/feature/a\tnot-an-object\n",
            allow_namespace_boundary=False,
        )
    class FailedEnumerationGit(FakeGit):
        def __call__(
            self, argv: Sequence[str], cwd: str | None = None
        ) -> subprocess.CompletedProcess[str]:
            args = list(argv)
            if args[1] == "for-each-ref":
                return self._result(args, 2, stderr="enumeration unavailable")
            return super().__call__(args, cwd)
    with pytest.raises(inventory.InventoryError, match="enumeration unavailable"):
        inventory.collect_inventory("development", 1, run=FailedEnumerationGit())
    with pytest.raises(inventory.InventoryError, match="limit must be between"):
        inventory.collect_summary("development", 0, run=FakeGit())


def test_main_emits_machine_readable_error(capsys: pytest.CaptureFixture[str]) -> None:
    rc = inventory.main(
        ["--target", "missing", "--limit", "10", "--after", ""],
        run=FakeGit(target_valid=False),
    )
    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "error": "invalid target ref: missing",
        "ok": False,
        "schema_version": 2,
    }


def test_make_target_and_contract_are_tracked() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    contract = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text(encoding="utf-8")
    )
    assert "branch-reconciliation-inventory:" in makefile
    assert "scripts/branch_reconciliation_inventory.py" in makefile
    assert "RECONCILE_AFTER" in makefile
    assert '--after "$(RECONCILE_AFTER)"' in makefile
    entry = next(
        target
        for target in contract["targets"]
        if target["name"] == "branch-reconciliation-inventory"
    )
    assert entry["make_variables"] == [
        "RECONCILE_TARGET",
        "RECONCILE_LIMIT",
        "RECONCILE_AFTER",
    ]
    assert (
        entry["behavior"]
        == "make branch-reconciliation-inventory RECONCILE_TARGET=development "
        "RECONCILE_LIMIT=20 RECONCILE_AFTER="
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
        "check-ref-format",
        "for-each-ref",
        "merge-base",
        "cherry",
        "show-ref",
    }


def test_exhaustive_summary_pages_to_terminal_and_deduplicates_heads() -> None:
    refs = [
        ("refs/heads/development", TARGET_HEAD),
        ("refs/heads/feature/a", ANCESTOR_HEAD),
        ("refs/heads/feature/b", ANCESTOR_HEAD),
        ("refs/heads/feature/c", UNIQUE_HEAD),
    ]
    fake = FakeGit(
        refs=refs,
        ancestors=frozenset({ANCESTOR_HEAD}),
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
    )
    result = inventory.collect_summary("development", 2, run=fake)
    assert result["mode"] == "exhaustive-summary"
    assert result["pages"] == 2
    assert result["terminal"] is True
    assert result["truncated"] is False
    assert result["counts"]["returned"] == 3
    assert result["counts"]["deduplicated_heads"] == 2
    assert result["groups"][0]["refs"] == [
        "refs/heads/feature/a",
        "refs/heads/feature/b",
    ]
    assert result["groups"][0]["branch_count"] == 2
    assert result["groups"][1]["refs"] == ["refs/heads/feature/c"]


@pytest.mark.parametrize(
    "terminal_refs",
    [
        [
            ("refs/heads/feature/0-created", PAGE_HEADS[3]),
            ("refs/heads/feature/a", PAGE_HEADS[0]),
            ("refs/heads/feature/b", PAGE_HEADS[1]),
        ],
        [("refs/heads/feature/a", PAGE_HEADS[0])],
        [
            ("refs/heads/feature/a", PAGE_HEADS[2]),
            ("refs/heads/feature/b", PAGE_HEADS[1]),
        ],
    ],
    ids=("created-before-cursor", "deleted-after-page", "head-moved"),
)
def test_exhaustive_summary_rejects_stale_terminal_ref_snapshot(
    terminal_refs: Sequence[tuple[str, str]],
) -> None:
    class TerminalSnapshotGit(FakeGit):
        def __call__(
            self, argv: Sequence[str], cwd: str | None = None
        ) -> subprocess.CompletedProcess[str]:
            args = list(argv)
            if args[1] == "for-each-ref" and "--sort=refname" in args:
                original_refs = self.refs
                self.refs = list(terminal_refs)
                try:
                    return super().__call__(args, cwd)
                finally:
                    self.refs = original_refs
            return super().__call__(args, cwd)
    fake = TerminalSnapshotGit(
        refs=[
            ("refs/heads/feature/a", PAGE_HEADS[0]),
            ("refs/heads/feature/b", PAGE_HEADS[1]),
        ],
        ancestors=frozenset(PAGE_HEADS),
    )
    with pytest.raises(inventory.InventoryError, match="changed during exhaustive"):
        inventory.collect_summary("development", 1, run=fake)


def test_exhaustive_summary_rejects_target_move_after_single_page() -> None:
    class MovingTargetGit(FakeGit):
        target_head_resolutions = 0
        def __call__(
            self, argv: Sequence[str], cwd: str | None = None
        ) -> subprocess.CompletedProcess[str]:
            args = list(argv)
            result = super().__call__(args, cwd)
            if args[1:4] == ["rev-parse", "--verify", "--quiet"]:
                self.target_head_resolutions += 1
                if self.target_head_resolutions > 1:
                    return self._result(args, 0, f"{PAGE_HEADS[3]}\n")
            return result
    fake = MovingTargetGit(
        refs=[("refs/heads/feature/a", ANCESTOR_HEAD)],
        ancestors=frozenset({ANCESTOR_HEAD}),
    )
    with pytest.raises(inventory.InventoryError, match="target changed"):
        inventory.collect_summary("development", 2, run=fake)


def test_merge_queue_accounts_for_every_branch_and_queues_only_novel_heads() -> None:
    refs = [
        ("refs/heads/feature/ancestor-a", ANCESTOR_HEAD),
        ("refs/heads/feature/ancestor-b", ANCESTOR_HEAD),
        ("refs/heads/feature/novel-a", UNIQUE_HEAD),
        ("refs/heads/feature/novel-z", UNIQUE_HEAD),
        ("refs/heads/feature/other", PAGE_HEADS[0]),
        ("refs/heads/feature/patch-copy", PATCH_HEAD),
    ]
    fake = FakeGit(
        refs=refs,
        ancestors=frozenset({ANCESTOR_HEAD}),
        cherries={
            PATCH_HEAD: f"- {PATCH_HEAD}\n",
            UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n",
            PAGE_HEADS[0]: f"+ {PAGE_HEADS[0]}\n",
        },
    )
    result = inventory.collect_merge_queue("development", 2, run=fake)
    assert result["mode"] == "sequential-merge-queue"
    assert result["target"] == {
        "head": TARGET_HEAD,
        "input": "development",
        "ref": "refs/heads/development",
    }
    assert result["counts"] == {
        "ancestor_branches": 2,
        "ancestor_heads": 1,
        "collapsed_branches": 3,
        "collapsed_heads": 2,
        "observed_branches": 6,
        "observed_heads": 4,
        "patch_equivalent_branches": 1,
        "patch_equivalent_heads": 1,
        "queued_branches": 3,
        "queued_heads": 2,
    }
    assert [entry["source_ref"] for entry in result["queue"]] == [
        "refs/heads/feature/novel-a",
        "refs/heads/feature/other",
    ]
    assert result["queue"][0] == {
        "branch_count": 2,
        "expected_tip": UNIQUE_HEAD,
        "order": 1,
        "preflight": {
            "conflict_path_count": 0,
            "conflict_paths": [],
            "conflict_paths_truncated": False,
            "expected_source": UNIQUE_HEAD,
            "expected_target": TARGET_HEAD,
            "path_redactions": 0,
            "result_tree": EMPTY_HEAD,
            "status": "clean",
        },
        "refs": [
            "refs/heads/feature/novel-a",
            "refs/heads/feature/novel-z",
        ],
        "source_ref": "refs/heads/feature/novel-a",
        "unique_commits": 1,
    }
    assert [group["classification"] for group in result["collapsed"]] == [
        "ancestor",
        "patch-equivalent",
    ]
    assert sum(entry["branch_count"] for entry in result["queue"]) + sum(
        group["branch_count"] for group in result["collapsed"]
    ) == result["counts"]["observed_branches"]


def test_merge_queue_binds_bounded_conflict_preflight_into_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inventory, "CONFLICT_PATH_LIMIT", 2)
    monkeypatch.setattr(inventory, "CONFLICT_PATH_CHAR_LIMIT", 12)
    output = "\0".join(
        [EMPTY_HEAD, "safe.py", "line\nbreak.py", "third.py", ""]
    )
    fake = FakeGit(
        refs=[("refs/heads/feature/novel", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
        merge_trees={UNIQUE_HEAD: (1, output, "")},
    )
    result = inventory.collect_merge_queue("development", 2, run=fake)
    expected = {
        "conflict_path_count": 3,
        "conflict_paths": ["safe.py", "line�break.…"],
        "conflict_paths_truncated": True,
        "expected_source": UNIQUE_HEAD,
        "expected_target": TARGET_HEAD,
        "path_redactions": 1,
        "result_tree": EMPTY_HEAD,
        "status": "conflicted",
    }
    assert result["queue"][0]["preflight"] == expected
    assert result["receipt"]["body"]["queue"][0]["preflight"] == expected
    assert result["receipt"]["body"]["queue"][0]["preflight"] is not result[
        "queue"
    ][0]["preflight"]
    assert result["bounds"]["conflict_path_limit"] == 2
    assert result["bounds"]["conflict_path_char_limit"] == 12
    assert result["bounds"]["merge_tree_output_char_limit"] > 0
    assert [call for call in fake.calls if call[1] == "merge-tree"] == [
        [
            "git",
            "merge-tree",
            "--write-tree",
            "--name-only",
            "--no-messages",
            "-z",
            TARGET_HEAD,
            UNIQUE_HEAD,
        ]
    ]


def test_merge_queue_records_clean_conflict_preflight() -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/novel", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
    )
    result = inventory.collect_merge_queue("development", 2, run=fake)
    assert result["queue"][0]["preflight"] == {
        "conflict_path_count": 0,
        "conflict_paths": [],
        "conflict_paths_truncated": False,
        "expected_source": UNIQUE_HEAD,
        "expected_target": TARGET_HEAD,
        "path_redactions": 0,
        "result_tree": EMPTY_HEAD,
        "status": "clean",
    }


def test_merge_queue_preserves_conflicted_status_without_file_paths() -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/novel", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
        merge_trees={UNIQUE_HEAD: (1, f"{EMPTY_HEAD}\0", "")},
    )
    result = inventory.collect_merge_queue("development", 2, run=fake)
    assert result["queue"][0]["preflight"]["status"] == "conflicted"
    assert result["queue"][0]["preflight"]["conflict_path_count"] == 0
    assert result["queue"][0]["preflight"]["conflict_paths"] == []


def test_merge_queue_fails_closed_when_merge_tree_is_unsupported() -> None:
    secret = "/Users/operator/private.py"
    fake = FakeGit(
        refs=[("refs/heads/feature/novel", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
        merge_trees={UNIQUE_HEAD: (129, "", f"unknown option: {secret}")},
    )
    with pytest.raises(inventory.InventoryError) as error:
        inventory.collect_merge_queue("development", 2, run=fake)
    assert str(error.value) == "merge-tree conflict preflight unavailable"
    assert secret not in str(error.value)


@pytest.mark.parametrize(
    "result",
    [
        (1, f"{EMPTY_HEAD}\0unterminated.py", ""),
        (1, "not-an-object\0conflict.py\0", ""),
        (1, f"{EMPTY_HEAD}\0../escape.py\0", ""),
        (0, f"{EMPTY_HEAD}\0unexpected.py\0", ""),
        (1, f"{EMPTY_HEAD}\0conflict.py\0", "unexpected stderr"),
    ],
    ids=("unterminated", "tree", "unsafe-path", "clean-with-path", "stderr"),
)
def test_merge_queue_rejects_malformed_merge_tree_evidence(
    result: tuple[int, str, str],
) -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/novel", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
        merge_trees={UNIQUE_HEAD: result},
    )
    with pytest.raises(
        inventory.InventoryError,
        match="invalid merge-tree conflict preflight evidence",
    ):
        inventory.collect_merge_queue("development", 2, run=fake)


def test_merge_queue_rejects_oversized_merge_tree_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inventory, "MERGE_TREE_OUTPUT_CHAR_LIMIT", 10)
    fake = FakeGit(
        refs=[("refs/heads/feature/novel", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
        merge_trees={UNIQUE_HEAD: (0, f"{EMPTY_HEAD}\0", "")},
    )
    with pytest.raises(inventory.InventoryError, match="output exceeded"):
        inventory.collect_merge_queue("development", 2, run=fake)


def test_merge_queue_rejects_conflict_path_scan_above_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inventory, "CONFLICT_PATH_SCAN_LIMIT", 1)
    fake = FakeGit(
        refs=[("refs/heads/feature/novel", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
        merge_trees={
            UNIQUE_HEAD: (1, f"{EMPTY_HEAD}\0a.py\0b.py\0", ""),
        },
    )
    with pytest.raises(inventory.InventoryError, match="path bound exceeded"):
        inventory.collect_merge_queue("development", 2, run=fake)


def test_merge_queue_fails_closed_when_queued_tip_moves_before_emission() -> None:
    class MovingQueueTipGit(FakeGit):
        sorted_scans = 0
        def __call__(
            self, argv: Sequence[str], cwd: str | None = None
        ) -> subprocess.CompletedProcess[str]:
            args = list(argv)
            if args[1] == "for-each-ref" and "--sort=refname" in args:
                self.sorted_scans += 1
                if self.sorted_scans > 1:
                    original_refs = self.refs
                    self.refs = [("refs/heads/feature/novel", PAGE_HEADS[1])]
                    try:
                        return super().__call__(args, cwd)
                    finally:
                        self.refs = original_refs
            return super().__call__(args, cwd)
    fake = MovingQueueTipGit(
        refs=[("refs/heads/feature/novel", PAGE_HEADS[0])],
        cherries={PAGE_HEADS[0]: f"+ {PAGE_HEADS[0]}\n"},
    )
    with pytest.raises(inventory.InventoryError, match="reconciled refs changed"):
        inventory.collect_merge_queue("development", 2, run=fake)


def test_merge_queue_fails_closed_when_collapsed_tip_moves_before_emission() -> None:
    class MovingCollapsedTipGit(FakeGit):
        sorted_scans = 0
        def __call__(
            self, argv: Sequence[str], cwd: str | None = None
        ) -> subprocess.CompletedProcess[str]:
            args = list(argv)
            if args[1] == "for-each-ref" and "--sort=refname" in args:
                self.sorted_scans += 1
                if self.sorted_scans > 1:
                    original_refs = self.refs
                    self.refs = [("refs/heads/feature/patch-copy", PAGE_HEADS[1])]
                    try:
                        return super().__call__(args, cwd)
                    finally:
                        self.refs = original_refs
            return super().__call__(args, cwd)
    fake = MovingCollapsedTipGit(
        refs=[("refs/heads/feature/patch-copy", PATCH_HEAD)],
        cherries={PATCH_HEAD: f"- {PATCH_HEAD}\n"},
    )
    with pytest.raises(inventory.InventoryError, match="reconciled refs changed"):
        inventory.collect_merge_queue("development", 2, run=fake)


def test_merge_queue_receipt_rejects_new_ref_before_emission() -> None:
    class NewRefBeforeReceiptGit(FakeGit):
        sorted_scans = 0
        def __call__(
            self, argv: Sequence[str], cwd: str | None = None
        ) -> subprocess.CompletedProcess[str]:
            args = list(argv)
            if args[1] == "for-each-ref" and "--sort=refname" in args:
                self.sorted_scans += 1
                if self.sorted_scans > 1:
                    original_refs = self.refs
                    self.refs = [
                        *self.refs,
                        ("refs/heads/feature/new-during-handoff", PAGE_HEADS[1]),
                    ]
                    try:
                        return super().__call__(args, cwd)
                    finally:
                        self.refs = original_refs
            return super().__call__(args, cwd)
    fake = NewRefBeforeReceiptGit(
        refs=[("refs/heads/feature/novel", PAGE_HEADS[0])],
        cherries={PAGE_HEADS[0]: f"+ {PAGE_HEADS[0]}\n"},
    )
    with pytest.raises(inventory.InventoryError, match="reconciled refs changed"):
        inventory.collect_merge_queue("development", 2, run=fake)


def test_merge_queue_fails_closed_when_target_moves_before_emission() -> None:
    class MovingQueueTargetGit(FakeGit):
        target_head_resolutions = 0
        def __call__(
            self, argv: Sequence[str], cwd: str | None = None
        ) -> subprocess.CompletedProcess[str]:
            args = list(argv)
            result = super().__call__(args, cwd)
            if args[1:4] == ["rev-parse", "--verify", "--quiet"]:
                self.target_head_resolutions += 1
                if self.target_head_resolutions > 2:
                    return self._result(args, 0, f"{PAGE_HEADS[3]}\n")
            return result
    fake = MovingQueueTargetGit(
        refs=[("refs/heads/feature/novel", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
    )
    with pytest.raises(inventory.InventoryError, match="target changed"):
        inventory.collect_merge_queue("development", 2, run=fake)


def test_merge_queue_fails_closed_above_output_head_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inventory, "MERGE_QUEUE_HEAD_LIMIT", 1)
    fake = FakeGit(
        refs=[
            ("refs/heads/feature/a", PAGE_HEADS[0]),
            ("refs/heads/feature/b", PAGE_HEADS[1]),
        ],
        cherries={
            PAGE_HEADS[0]: f"+ {PAGE_HEADS[0]}\n",
            PAGE_HEADS[1]: f"+ {PAGE_HEADS[1]}\n",
        },
    )
    with pytest.raises(inventory.InventoryError, match="queue head bound"):
        inventory.collect_merge_queue("development", 2, run=fake)


def test_merge_queue_plan_groups_disjoint_current_heads_and_flags_collisions(
) -> None:
    fake = FakeGit(
        refs=[
            ("refs/heads/feature/a", PAGE_HEADS[0]),
            ("refs/heads/feature/ancestor", ANCESTOR_HEAD),
            ("refs/heads/feature/b", PAGE_HEADS[1]),
            ("refs/heads/feature/c", PAGE_HEADS[2]),
            ("refs/heads/feature/d", PAGE_HEADS[3]),
            ("refs/heads/feature/patch-copy", PATCH_HEAD),
        ],
        ancestors=frozenset({ANCESTOR_HEAD}),
        cherries={
            PAGE_HEADS[0]: f"+ {PAGE_HEADS[0]}\n",
            PAGE_HEADS[1]: f"+ {PAGE_HEADS[1]}\n",
            PAGE_HEADS[2]: f"+ {PAGE_HEADS[2]}\n",
            PAGE_HEADS[3]: f"+ {PAGE_HEADS[3]}\n",
            PATCH_HEAD: f"- {PATCH_HEAD}\n",
        },
        changed_paths={
            PAGE_HEADS[0]: ("config/alpha.yml", "src/a.py"),
            PAGE_HEADS[1]: ("src/shared.py",),
            PAGE_HEADS[2]: ("src/shared.py",),
            PAGE_HEADS[3]: ("config/beta.yml",),
        },
    )
    result = inventory.collect_merge_queue_plan("development", 2, run=fake)
    assert result["mode"] == "merge-queue-plan"
    assert result["counts"] == {
        "ancestor_branches": 1,
        "ancestor_heads": 1,
        "candidate_branches": 4,
        "candidate_heads": 4,
        "changed_path_collision_pairs": 1,
        "collision_pairs": 2,
        "observed_branches": 6,
        "observed_heads": 6,
        "patch_equivalent_branches": 1,
        "patch_equivalent_heads": 1,
        "planned_groups": 2,
        "shared_infrastructure_collision_pairs": 1,
    }
    assert [
        [entry["source_ref"] for entry in group["entries"]]
        for group in result["groups"]
    ] == [
        ["refs/heads/feature/a", "refs/heads/feature/b"],
        ["refs/heads/feature/c", "refs/heads/feature/d"],
    ]
    assert result["collisions"] == [
        {
            "changed_path_count": 0,
            "changed_paths": [],
            "changed_paths_truncated": False,
            "left_head": PAGE_HEADS[0],
            "left_ref": "refs/heads/feature/a",
            "path_redactions": 0,
            "reasons": ["shared-infrastructure"],
            "right_head": PAGE_HEADS[3],
            "right_ref": "refs/heads/feature/d",
            "shared_infrastructure_path_count": 2,
            "shared_infrastructure_paths": [
                "config/alpha.yml",
                "config/beta.yml",
            ],
            "shared_infrastructure_paths_truncated": False,
        },
        {
            "changed_path_count": 1,
            "changed_paths": ["src/shared.py"],
            "changed_paths_truncated": False,
            "left_head": PAGE_HEADS[1],
            "left_ref": "refs/heads/feature/b",
            "path_redactions": 0,
            "reasons": ["changed-path"],
            "right_head": PAGE_HEADS[2],
            "right_ref": "refs/heads/feature/c",
            "shared_infrastructure_path_count": 0,
            "shared_infrastructure_paths": [],
            "shared_infrastructure_paths_truncated": False,
        },
    ]
    assert result["collisions_truncated"] is False
    assert result["terminal"] is True
    assert result["truncated"] is False
    assert [call[1] for call in fake.calls].count("merge-tree") == 6
    assert [call[1] for call in fake.calls].count("diff") == 4


def test_merge_queue_plan_emits_deterministic_read_only_group_rehearsal() -> None:
    refs = [
        ("refs/heads/feature/a", PAGE_HEADS[0]),
        ("refs/heads/feature/b", PAGE_HEADS[1]),
    ]
    changed_paths = {PAGE_HEADS[0]: ("src/a.py",), PAGE_HEADS[1]: ("config/policy.yml", "src/b.py")}
    first_git = FakeGit(
        refs=refs, cherries={head: f"+ {head}\n" for head in PAGE_HEADS[:2]}, changed_paths=changed_paths
    )
    second_git = FakeGit(
        refs=list(reversed(refs)),
        cherries={head: f"+ {head}\n" for head in PAGE_HEADS[:2]}, changed_paths=changed_paths
    )
    first = inventory.collect_merge_queue_plan("development", 2, run=first_git)
    second = inventory.collect_merge_queue_plan("development", 2, run=second_git)
    rehearsal = first["groups"][0]["rehearsal"]
    assert rehearsal == second["groups"][0]["rehearsal"]
    assert rehearsal["candidate_id"].startswith("merge-rehearsal-sha256:")
    assert (rehearsal["base_head"], rehearsal["source_heads"]) == (TARGET_HEAD, list(PAGE_HEADS[:2]))
    preflight = rehearsal["preflight"]
    assert (preflight["changed_path_count"], preflight["pair_count"]) == (3, 1)
    assert preflight["shared_path_conflict_count"] == 0
    assert preflight["shared_infrastructure_paths"] == ["config/policy.yml"]
    assert preflight["freshness_checks"][0]["expected_object"] == TARGET_HEAD
    assert all(len(check["expected_path_sha256"]) == 64 for check in preflight["path_checks"])
    prediction = rehearsal["conflict_prediction"]
    assert prediction["status"] == "predicted-clean"
    assert prediction["blocked_reason"] is None
    assert prediction["engine"] == "git-merge-tree-write-tree"
    assert (prediction["required_check_count"], prediction["attempted_check_count"]) == (3, 3)
    assert [check["relation"] for check in prediction["checks"]] == ["target-source", "target-source", "source-source"]
    assert all(check["status"] == "clean" for check in prediction["checks"])
    assert "mergeable" not in prediction
    first_step, second_step = rehearsal["recipe"]
    assert (first_step["candidate_input"], second_step["candidate_input"]) == (TARGET_HEAD, "${candidate_commit_1}")
    assert first_step["commit_tree"]["environment"]["GIT_AUTHOR_DATE"] == "@0 +0000"
    assert rehearsal["final_candidate"] == "${candidate_commit_2}"
    assert "exact-candidate-gate" in rehearsal["admission"]["evidence_requirements"]
    assert "native-conflict-prediction-clean" in rehearsal["admission"]["evidence_requirements"]
    forbidden_calls = {"commit-tree", "merge", "update-ref", "worktree"}
    assert not {call[1] for call in [*first_git.calls, *second_git.calls]} & forbidden_calls


@pytest.mark.parametrize(
    ("native_result", "status", "reason"),
    [
        ((1, f"{EMPTY_HEAD}\0conflict.py\0", ""), "conflicted", None),
        ((129, "", "/secret/unsupported"), "blocked", "unsupported-git"),
        ((0, EMPTY_HEAD, ""), "blocked", "ambiguous-output"),
        ((124, "", "timed out"), "blocked", "runtime-bound-exceeded"),
    ],
)
def test_merge_queue_plan_native_prediction_classifies_conflict_or_block(
    native_result: tuple[int, str, str], status: str, reason: str | None
) -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/a", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
        changed_paths={UNIQUE_HEAD: ("src/a.py",)},
        merge_trees={UNIQUE_HEAD: native_result},
    )
    result = inventory.collect_merge_queue_plan("development", 2, run=fake)
    prediction = result["groups"][0]["rehearsal"]["conflict_prediction"]
    assert (prediction["status"], prediction["blocked_reason"]) == (status, reason)
    assert "/secret/unsupported" not in json.dumps(prediction)
    if status == "conflicted":
        assert prediction["checks"][0]["conflict_paths"] == ["conflict.py"]


def test_merge_queue_plan_native_prediction_caps_checks_and_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/a", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
        changed_paths={UNIQUE_HEAD: ("src/a.py",)},
    )
    monkeypatch.setattr(inventory, "MERGE_REHEARSAL_PREDICTION_OUTPUT_CHAR_LIMIT", 3)
    result = inventory.collect_merge_queue_plan("development", 2, run=fake)
    prediction = result["groups"][0]["rehearsal"]["conflict_prediction"]
    assert prediction["blocked_reason"] == "output-bound-exceeded"
    monkeypatch.setattr(inventory, "MERGE_REHEARSAL_PREDICTION_OUTPUT_CHAR_LIMIT", 262_144)
    monkeypatch.setattr(inventory, "MERGE_REHEARSAL_PREDICTION_CHECK_LIMIT", 0)
    result = inventory.collect_merge_queue_plan("development", 2, run=fake)
    prediction = result["groups"][0]["rehearsal"]["conflict_prediction"]
    assert prediction["blocked_reason"] == "prediction-check-bound-exceeded"
    assert prediction["bounds"]["timeout_seconds"] == inventory.GIT_TIMEOUT_SECONDS


def test_merge_queue_plan_rehearsal_fails_closed_on_group_branch_or_path_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGit(
        refs=[
            ("refs/heads/feature/a", PAGE_HEADS[0]),
            ("refs/heads/feature/alias-a", PAGE_HEADS[0]),
            ("refs/heads/feature/b", PAGE_HEADS[1]),
        ],
        cherries={head: f"+ {head}\n" for head in PAGE_HEADS[:2]},
        changed_paths={
            PAGE_HEADS[0]: ("src/a.py",),
            PAGE_HEADS[1]: ("src/b.py",),
        },
    )
    monkeypatch.setattr(inventory, "MERGE_REHEARSAL_GROUP_LIMIT", 0, raising=False)
    with pytest.raises(inventory.InventoryError, match="rehearsal group bound"):
        inventory.collect_merge_queue_plan("development", 2, run=fake)
    monkeypatch.setattr(inventory, "MERGE_REHEARSAL_GROUP_LIMIT", 2, raising=False)
    monkeypatch.setattr(inventory, "MERGE_REHEARSAL_BRANCH_LIMIT", 2, raising=False)
    with pytest.raises(inventory.InventoryError, match="rehearsal branch bound"):
        inventory.collect_merge_queue_plan("development", 2, run=fake)
    monkeypatch.setattr(inventory, "MERGE_REHEARSAL_BRANCH_LIMIT", 3, raising=False)
    monkeypatch.setattr(inventory, "MERGE_REHEARSAL_PATH_LIMIT", 1, raising=False)
    with pytest.raises(inventory.InventoryError, match="rehearsal path bound"):
        inventory.collect_merge_queue_plan("development", 2, run=fake)


def test_merge_queue_plan_bounds_displayed_paths_and_collision_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inventory, "MERGE_PLAN_PATH_LIMIT", 1)
    monkeypatch.setattr(inventory, "MERGE_PLAN_COLLISION_LIMIT", 1)
    fake = FakeGit(
        refs=[
            ("refs/heads/feature/a", PAGE_HEADS[0]),
            ("refs/heads/feature/b", PAGE_HEADS[1]),
            ("refs/heads/feature/c", PAGE_HEADS[2]),
        ],
        cherries={head: f"+ {head}\n" for head in PAGE_HEADS[:3]},
        changed_paths={
            head: ("shared.py", "second.py") for head in PAGE_HEADS[:3]
        },
    )
    result = inventory.collect_merge_queue_plan("development", 2, run=fake)
    assert result["counts"]["collision_pairs"] == 3
    assert len(result["collisions"]) == 1
    assert result["collisions_truncated"] is True
    assert result["candidates"][0]["changed_path_count"] == 2
    assert result["candidates"][0]["changed_paths"] == ["second.py"]
    assert result["candidates"][0]["changed_paths_truncated"] is True
    assert len(result["groups"]) == 3


def test_merge_queue_plan_fails_closed_on_path_scan_or_json_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/a", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
        changed_paths={UNIQUE_HEAD: ("one.py", "two.py")},
    )
    monkeypatch.setattr(inventory, "MERGE_PLAN_PATH_SCAN_LIMIT", 1)
    with pytest.raises(inventory.InventoryError, match="path scan bound"):
        inventory.collect_merge_queue_plan("development", 2, run=fake)
    monkeypatch.setattr(inventory, "MERGE_PLAN_PATH_SCAN_LIMIT", 10)
    monkeypatch.setattr(inventory, "MERGE_PLAN_JSON_CHAR_LIMIT", 10)
    with pytest.raises(inventory.InventoryError, match="JSON output exceeded"):
        inventory.collect_merge_queue_plan("development", 2, run=fake)


def test_merge_queue_plan_is_read_only_and_revalidates_refs() -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/a", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
        changed_paths={UNIQUE_HEAD: ("src/a.py",)},
    )
    inventory.collect_merge_queue_plan("development", 2, run=fake)
    mutating = {"branch", "checkout", "merge", "reset", "update-ref", "worktree"}
    assert not ({call[1] for call in fake.calls} & mutating)
    assert [call[1] for call in fake.calls].count("for-each-ref") == 3


@pytest.mark.parametrize(
    "output",
    [
        "unterminated.py",
        "\0",
        "/absolute.py\0",
        "../escape.py\0",
        "duplicate.py\0duplicate.py\0",
    ],
    ids=("unterminated", "empty", "absolute", "traversal", "duplicate"),
)
def test_merge_queue_plan_rejects_malformed_path_evidence(output: str) -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/a", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
        diff_results={UNIQUE_HEAD: (0, output, "")},
    )
    with pytest.raises(inventory.InventoryError, match="malformed merge queue plan"):
        inventory.collect_merge_queue_plan("development", 2, run=fake)


def test_merge_queue_plan_bounds_git_failure_without_leaking_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "/Users/operator/secret.py"
    fake = FakeGit(
        refs=[("refs/heads/feature/a", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
        diff_results={UNIQUE_HEAD: (128, "", secret)},
    )
    with pytest.raises(inventory.InventoryError) as error:
        inventory.collect_merge_queue_plan("development", 2, run=fake)
    assert str(error.value) == "merge queue plan path inspection failed"
    assert secret not in str(error.value)
    monkeypatch.setattr(inventory, "MERGE_PLAN_GIT_OUTPUT_CHAR_LIMIT", 4)
    fake.diff_results[UNIQUE_HEAD] = (0, "five\0", "")
    with pytest.raises(inventory.InventoryError, match="Git output exceeded"):
        inventory.collect_merge_queue_plan("development", 2, run=fake)


def test_merge_queue_plan_blocks_ref_movement_after_prediction() -> None:
    class MovingPlanTipGit(FakeGit):
        sorted_scans = 0

        def __call__(
            self, argv: Sequence[str], cwd: str | None = None
        ) -> subprocess.CompletedProcess[str]:
            args = list(argv)
            if args[1] == "for-each-ref" and "--sort=refname" in args:
                self.sorted_scans += 1
                if self.sorted_scans > 1:
                    original_refs = self.refs
                    self.refs = [("refs/heads/feature/a", PAGE_HEADS[1])]
                    try:
                        return super().__call__(args, cwd)
                    finally:
                        self.refs = original_refs
            return super().__call__(args, cwd)

    fake = MovingPlanTipGit(
        refs=[("refs/heads/feature/a", PAGE_HEADS[0])],
        cherries={PAGE_HEADS[0]: f"+ {PAGE_HEADS[0]}\n"},
        changed_paths={PAGE_HEADS[0]: ("src/a.py",)},
    )
    result = inventory.collect_merge_queue_plan("development", 2, run=fake)
    prediction = result["groups"][0]["rehearsal"]["conflict_prediction"]
    assert (result["ok"], result["terminal"]) == (False, False)
    assert (prediction["status"], prediction["blocked_reason"]) == (
        "blocked",
        "freshness-drift",
    )
    assert prediction["checks"] == []


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("Makefile", True),
        ("opencode.json", True),
        ("config/policy.yml", True),
        ("config/policy.yaml", False),
        ("config/nested/policy.yml", False),
        (".github/workflows/gate.yml", True),
        (".github/workflows/nested/gate.yml", False),
        ("src/service.py", False),
    ],
)
def test_merge_queue_plan_shared_infrastructure_policy(
    path: str,
    expected: bool,
) -> None:
    assert inventory._is_shared_infrastructure_path(path) is expected


def test_merge_queue_plan_reports_dual_reason_redaction_and_progress() -> None:
    events: list[str] = []
    fake = FakeGit(
        refs=[
            ("refs/heads/feature/a", PAGE_HEADS[0]),
            ("refs/heads/feature/b", PAGE_HEADS[1]),
        ],
        cherries={head: f"+ {head}\n" for head in PAGE_HEADS[:2]},
        changed_paths={
            PAGE_HEADS[0]: ("Makefile", "unsafe\nname.py"),
            PAGE_HEADS[1]: ("Makefile", "unsafe\nname.py"),
        },
    )
    result = inventory.collect_merge_queue_plan(
        "development",
        2,
        run=fake,
        progress=events.append,
    )
    assert result["collisions"][0]["reasons"] == [
        "changed-path",
        "shared-infrastructure",
    ]
    assert result["candidates"][0]["path_redactions"] == 1
    assert result["collisions"][0]["path_redactions"] == 1
    assert any(event.startswith("plan-paths=") for event in events)
    assert events[-1] == "verify=merge-queue-plan-preconditions"


def test_merge_queue_plan_handles_no_current_heads_without_path_git() -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/ancestor", ANCESTOR_HEAD)],
        ancestors=frozenset({ANCESTOR_HEAD}),
    )
    result = inventory.collect_merge_queue_plan("development", 2, run=fake)
    assert result["candidates"] == []
    assert result["groups"] == []
    assert result["collisions"] == []
    assert result["counts"]["ancestor_heads"] == 1
    assert not any(call[1] == "diff" for call in fake.calls)


def test_merge_queue_plan_rejects_inconsistent_lifecycle_or_head_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/a", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
    )
    summary = inventory.collect_summary("development", 2, run=fake)
    summary["groups"][0]["lifecycle"] = "historical"
    with pytest.raises(inventory.InventoryError, match="lifecycle evidence"):
        inventory._build_merge_queue_plan(
            summary,
            run=fake,
            cwd=None,
            progress=None,
        )

    summary["groups"][0]["lifecycle"] = "current"
    monkeypatch.setattr(inventory, "MERGE_QUEUE_HEAD_LIMIT", 0)
    with pytest.raises(inventory.InventoryError, match="plan head bound"):
        inventory._build_merge_queue_plan(
            summary,
            run=fake,
            cwd=None,
            progress=None,
        )


def _receipt_fixture() -> tuple[inventory.MergeQueuePayload, FakeGit]:
    fake = FakeGit(
        refs=[
            ("refs/heads/feature/novel-a", PAGE_HEADS[0]),
            ("refs/heads/feature/novel-b", PAGE_HEADS[1]),
            ("refs/heads/feature/patch-copy", PATCH_HEAD),
        ],
        cherries={
            PAGE_HEADS[0]: f"+ {PAGE_HEADS[0]}\n",
            PAGE_HEADS[1]: f"+ {PAGE_HEADS[1]}\n",
            PATCH_HEAD: f"- {PATCH_HEAD}\n",
        },
    )
    return inventory.collect_merge_queue("development", 2, run=fake), fake


def test_receipt_validation_primitives_reject_malformed_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for value in ([], {1: "value"}, {"wrong": "value"}):
        with pytest.raises(inventory.InventoryError, match="invalid reconciliation receipt"):
            inventory._receipt_dict(value, frozenset({"expected"}))
    for value in (True, "1", -1, 2):
        with pytest.raises(inventory.InventoryError, match="invalid reconciliation receipt"):
            inventory._receipt_integer(value, minimum=0, maximum=1)
    invalid_refs: tuple[object, ...] = (
        None,
        "refs/tags/release",
        "refs/heads/.hidden",
        "refs/heads/trailing.",
        "refs/heads/locked.lock",
        "refs/heads/a//b",
        " refs/heads/spaced",
        "refs/heads/white space",
        "refs/heads/bad~name",
        "refs/heads/a..b",
        f"refs/heads/{'x' * 1_025}",
    )
    for value in invalid_refs:
        with pytest.raises(inventory.InventoryError, match="invalid reconciliation receipt"):
            inventory._receipt_ref(value)
    invalid_ref_lists: tuple[object, ...] = (
        "refs/heads/a",
        [],
        ["refs/heads/b", "refs/heads/a"],
        ["refs/heads/a", "refs/heads/a"],
    )
    for value in invalid_ref_lists:
        with pytest.raises(inventory.InventoryError, match="invalid reconciliation receipt"):
            inventory._receipt_refs(value)
    with pytest.raises(inventory.InventoryError, match="invalid reconciliation receipt"):
        inventory._canonical_receipt_body({"unsupported": object()})
    monkeypatch.setattr(inventory, "RECEIPT_JSON_CHAR_LIMIT", 2)
    with pytest.raises(inventory.InventoryError, match="invalid reconciliation receipt"):
        inventory._canonical_receipt_body({"key": "value"})


def test_merge_queue_receipt_binds_all_reconciliation_identities() -> None:
    result, _fake = _receipt_fixture()
    body = result["receipt"]["body"]
    assert body == {
        "collapsed": result["collapsed"],
        "cursor": 0,
        "queue": result["queue"],
        "target": {
            "base_head": TARGET_HEAD,
            "checkpoint_head": TARGET_HEAD,
            "input": "development",
            "ref": "refs/heads/development",
        },
        "version": 2,
    }
    assert result["receipt"]["algorithm"] == "sha256"
    assert len(result["receipt"]["digest"]) == 64


def test_receipt_replay_marks_integrated_prefix_and_returns_next_item() -> None:
    class IntegratedPrefixGit(FakeGit):
        target_head = TARGET_HEAD
        integrated: frozenset[str] = frozenset()

        def __call__(
            self, argv: Sequence[str], cwd: str | None = None
        ) -> subprocess.CompletedProcess[str]:
            args = list(argv)
            if args[1:4] == ["rev-parse", "--verify", "--quiet"]:
                self.calls.append(args)
                return self._result(args, 0, f"{self.target_head}\n")
            if args[1:3] == ["merge-base", "--is-ancestor"] and (
                self.target_head != TARGET_HEAD
            ):
                self.calls.append(args)
                return self._result(args, 0 if args[3] in self.integrated else 1)
            return super().__call__(args, cwd)

    fake = IntegratedPrefixGit(
        refs=[
            ("refs/heads/feature/novel-a", PAGE_HEADS[0]),
            ("refs/heads/feature/novel-b", PAGE_HEADS[1]),
            ("refs/heads/feature/patch-copy", PATCH_HEAD),
        ],
        cherries={
            PAGE_HEADS[0]: f"+ {PAGE_HEADS[0]}\n",
            PAGE_HEADS[1]: f"+ {PAGE_HEADS[1]}\n",
            PATCH_HEAD: f"- {PATCH_HEAD}\n",
        },
    )
    queued = inventory.collect_merge_queue("development", 2, run=fake)
    fake.target_head = EMPTY_HEAD
    fake.integrated = frozenset({TARGET_HEAD, PAGE_HEADS[0]})
    replayed = inventory.replay_merge_receipt(queued["receipt"], run=fake)
    assert replayed["mode"] == "reconciliation-receipt-replay"
    assert replayed["bounds"] == {
        "conflict_path_char_limit": inventory.CONFLICT_PATH_CHAR_LIMIT,
        "conflict_path_limit": inventory.CONFLICT_PATH_LIMIT,
        "conflict_path_scan_limit": inventory.CONFLICT_PATH_SCAN_LIMIT,
        "local_ref_scan_limit": inventory.LOCAL_REF_SCAN_LIMIT,
        "merge_queue_head_limit": inventory.MERGE_QUEUE_HEAD_LIMIT,
        "merge_tree_output_char_limit": inventory.MERGE_TREE_OUTPUT_CHAR_LIMIT,
        "receipt_json_char_limit": inventory.RECEIPT_JSON_CHAR_LIMIT,
    }
    assert replayed["cursor"] == 1
    assert [entry["expected_tip"] for entry in replayed["integrated"]] == [
        PAGE_HEADS[0]
    ]
    assert [entry["expected_tip"] for entry in replayed["newly_integrated"]] == [
        PAGE_HEADS[0]
    ]
    assert replayed["next"] == queued["queue"][1]
    assert replayed["receipt"]["body"]["cursor"] == 1
    assert (
        replayed["receipt"]["body"]["target"]["checkpoint_head"] == EMPTY_HEAD
    )
    stable = inventory.replay_merge_receipt(replayed["receipt"], run=fake)
    assert stable["cursor"] == 1
    assert stable["next"] == replayed["next"]
    assert stable["newly_integrated"] == []
    assert stable["receipt"] == replayed["receipt"]
    fake.target_head = FINAL_TARGET_HEAD
    fake.integrated = frozenset({EMPTY_HEAD, PAGE_HEADS[0], PAGE_HEADS[1]})
    completed = inventory.replay_merge_receipt(replayed["receipt"], run=fake)
    assert completed["cursor"] == 2
    assert completed["complete"] is True
    assert completed["next"] is None
    assert [
        entry["expected_tip"] for entry in completed["newly_integrated"]
    ] == [PAGE_HEADS[1]]


def test_receipt_replay_fails_closed_on_unaccounted_target_movement() -> None:
    class MovingTargetGit(FakeGit):
        target_head = TARGET_HEAD

        def __call__(
            self, argv: Sequence[str], cwd: str | None = None
        ) -> subprocess.CompletedProcess[str]:
            args = list(argv)
            if args[1:4] == ["rev-parse", "--verify", "--quiet"]:
                self.calls.append(args)
                return self._result(args, 0, f"{self.target_head}\n")
            if args[1:3] == ["merge-base", "--is-ancestor"] and (
                self.target_head != TARGET_HEAD
            ):
                self.calls.append(args)
                return self._result(args, 0 if args[3] == TARGET_HEAD else 1)
            return super().__call__(args, cwd)

    queued, original = _receipt_fixture()
    fake = MovingTargetGit(refs=original.refs, cherries=original.cherries)
    fake.target_head = EMPTY_HEAD
    with pytest.raises(inventory.InventoryError, match="target movement"):
        inventory.replay_merge_receipt(queued["receipt"], run=fake)


@pytest.mark.parametrize("moved_ref_index", [0, 2], ids=("novel", "collapsed"))
def test_receipt_replay_fails_closed_on_source_movement(moved_ref_index: int) -> None:
    queued, fake = _receipt_fixture()
    ref, _head = fake.refs[moved_ref_index]
    fake.refs[moved_ref_index] = (ref, EMPTY_HEAD)
    with pytest.raises(inventory.InventoryError, match="source refs changed"):
        inventory.replay_merge_receipt(queued["receipt"], run=fake)


@pytest.mark.parametrize(
    "field",
    ["target", "queue", "preflight", "collapsed", "cursor"],
)
def test_receipt_replay_rejects_content_tampering(field: str) -> None:
    queued, fake = _receipt_fixture()
    tampered = copy.deepcopy(queued["receipt"])
    if field == "target":
        tampered["body"]["target"]["checkpoint_head"] = EMPTY_HEAD
    elif field == "queue":
        tampered["body"]["queue"][0]["expected_tip"] = EMPTY_HEAD
    elif field == "preflight":
        tampered["body"]["queue"][0]["preflight"]["status"] = "conflicted"
    elif field == "collapsed":
        tampered["body"]["collapsed"][0]["expected_tip"] = EMPTY_HEAD
    else:
        tampered["body"]["cursor"] = 1
    with pytest.raises(inventory.InventoryError) as error:
        inventory.replay_merge_receipt(tampered, run=fake)
    assert str(error.value) == "invalid reconciliation receipt"
    assert EMPTY_HEAD not in str(error.value)


def test_receipt_replay_rejects_resealed_preflight_identity_change() -> None:
    queued, fake = _receipt_fixture()
    body = copy.deepcopy(queued["receipt"]["body"])
    body["queue"][0]["preflight"]["expected_target"] = EMPTY_HEAD
    resealed = inventory._seal_receipt(body)
    with pytest.raises(inventory.InventoryError) as error:
        inventory.replay_merge_receipt(resealed, run=fake)
    assert str(error.value) == "invalid reconciliation receipt"


def test_receipt_replay_rejects_version_without_preflight_contract() -> None:
    queued, fake = _receipt_fixture()
    body = copy.deepcopy(queued["receipt"]["body"])
    body["version"] = 1
    resealed = inventory._seal_receipt(body)
    with pytest.raises(inventory.InventoryError) as error:
        inventory.replay_merge_receipt(resealed, run=fake)
    assert str(error.value) == "invalid reconciliation receipt"


def test_receipt_replay_rejects_out_of_order_integrated_head() -> None:
    class OutOfOrderGit(FakeGit):
        target_head = TARGET_HEAD

        def __call__(
            self, argv: Sequence[str], cwd: str | None = None
        ) -> subprocess.CompletedProcess[str]:
            args = list(argv)
            if args[1:4] == ["rev-parse", "--verify", "--quiet"]:
                self.calls.append(args)
                return self._result(args, 0, f"{self.target_head}\n")
            if args[1:3] == ["merge-base", "--is-ancestor"] and (
                self.target_head != TARGET_HEAD
            ):
                self.calls.append(args)
                integrated = {TARGET_HEAD, PAGE_HEADS[1]}
                return self._result(args, 0 if args[3] in integrated else 1)
            return super().__call__(args, cwd)

    queued, original = _receipt_fixture()
    fake = OutOfOrderGit(refs=original.refs, cherries=original.cherries)
    fake.target_head = EMPTY_HEAD
    with pytest.raises(inventory.InventoryError, match="out of order"):
        inventory.replay_merge_receipt(queued["receipt"], run=fake)


def test_main_replays_receipt_from_bounded_stdin(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    queued, fake = _receipt_fixture()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(queued["receipt"])))
    rc = inventory.main(
        [
            "--target",
            "development",
            "--limit",
            "2",
            "--after",
            "",
            "--replay-receipt",
            "--quiet-progress",
        ],
        run=fake,
    )
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["mode"] == "reconciliation-receipt-replay"
    assert payload["cursor"] == 0
    assert payload["next"]["expected_tip"] == PAGE_HEADS[0]
    assert captured.err == ""


def test_exhaustive_summary_fails_closed_above_scan_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inventory, "LOCAL_REF_SCAN_LIMIT", 2)
    fake = FakeGit(
        refs=[
            ("refs/heads/feature/a", PAGE_HEADS[0]),
            ("refs/heads/feature/b", PAGE_HEADS[1]),
            ("refs/heads/feature/c", PAGE_HEADS[2]),
        ],
        ancestors=frozenset(PAGE_HEADS),
    )

    with pytest.raises(inventory.InventoryError, match="scan exceeded"):
        inventory.collect_summary("development", 2, run=fake)


def test_main_all_pages_emits_terminal_summary(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/merged", ANCESTOR_HEAD)],
        ancestors=frozenset({ANCESTOR_HEAD}),
    )

    rc = inventory.main(
        ["--target", "development", "--limit", "2", "--after", "", "--all-pages"],
        run=fake,
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "exhaustive-summary"
    assert payload["terminal"] is True


def test_main_emits_machine_readable_sequential_merge_queue(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/novel", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
    )

    rc = inventory.main(
        [
            "--target",
            "development",
            "--limit",
            "2",
            "--after",
            "",
            "--all-pages",
            "--merge-queue",
            "--quiet-progress",
        ],
        run=fake,
    )

    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["mode"] == "sequential-merge-queue"
    assert payload["queue"][0]["expected_tip"] == UNIQUE_HEAD
    assert captured.err == ""


def test_main_emits_machine_readable_merge_queue_plan(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/novel", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
        changed_paths={UNIQUE_HEAD: ("src/novel.py",)},
    )

    rc = inventory.main(
        [
            "--target",
            "development",
            "--limit",
            "2",
            "--after",
            "",
            "--all-pages",
            "--merge-queue-plan",
            "--quiet-progress",
        ],
        run=fake,
    )

    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["mode"] == "merge-queue-plan"
    assert payload["groups"][0]["entries"][0]["expected_tip"] == UNIQUE_HEAD
    assert captured.err == ""


@pytest.mark.parametrize(
    "conflict",
    ["--counts-only", "--current-only", "--head-semantics", "--merge-queue"],
)
def test_main_merge_queue_plan_rejects_other_summary_modes(
    conflict: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = inventory.main(
        [
            "--target",
            "development",
            "--limit",
            "2",
            "--after",
            "",
            "--all-pages",
            "--merge-queue-plan",
            conflict,
            "--quiet-progress",
        ],
        run=FakeGit(),
    )

    assert rc == 2
    assert "merge queue plan cannot be combined" in json.loads(
        capsys.readouterr().out
    )["error"]


def test_main_merge_queue_plan_requires_terminal_exhaustive_mode(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = inventory.main(
        [
            "--target",
            "development",
            "--limit",
            "2",
            "--after",
            "",
            "--merge-queue-plan",
            "--quiet-progress",
        ],
        run=FakeGit(),
    )

    assert rc == 2
    assert "merge queue plan requires an all-pages summary" in json.loads(
        capsys.readouterr().out
    )["error"]


@pytest.mark.parametrize(
    "conflict",
    ["--counts-only", "--current-only", "--head-semantics"],
)
def test_main_merge_queue_rejects_lossy_or_semantic_summary_modes(
    conflict: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeGit()

    rc = inventory.main(
        [
            "--target",
            "development",
            "--limit",
            "2",
            "--after",
            "",
            "--all-pages",
            "--merge-queue",
            conflict,
            "--quiet-progress",
        ],
        run=fake,
    )

    assert rc == 2
    assert "merge queue cannot be combined" in json.loads(
        capsys.readouterr().out
    )["error"]


def test_main_counts_only_omits_large_groups(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/merged", ANCESTOR_HEAD)],
        ancestors=frozenset({ANCESTOR_HEAD}),
    )

    rc = inventory.main([*CLI_ARGS, "--all-pages", "--counts-only"], run=fake)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "exhaustive-counts"
    assert payload["counts"]["returned"] == 1
    assert "groups" not in payload


def test_main_current_only_emits_unique_deduplicated_groups(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeGit(
        refs=[
            ("refs/heads/feature/merged", ANCESTOR_HEAD),
            ("refs/heads/feature/current", UNIQUE_HEAD),
        ],
        ancestors=frozenset({ANCESTOR_HEAD}),
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
    )

    rc = inventory.main([*CLI_ARGS, "--all-pages", "--current-only"], run=fake)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "exhaustive-current"
    assert payload["selected_branches"] == 1
    assert payload["selected_heads"] == 1
    assert [group["names"] for group in payload["groups"]] == [["feature/current"]]


def test_main_default_progress_stays_observable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/current", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
    )

    rc = inventory.main([*CLI_ARGS, "--all-pages"], run=fake)

    captured = capsys.readouterr()
    assert rc == 0
    assert json.loads(captured.out)["mode"] == "exhaustive-summary"
    assert "BRANCH-RECONCILIATION target=development" in captured.err
    assert "classify=1/1 ref=refs/heads/feature/current" in captured.err
    assert "verify=terminal-ref-snapshot" in captured.err


def test_main_quiet_progress_keeps_current_json_machine_consumable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/current", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
    )

    rc = inventory.main(
        [*CLI_ARGS, "--all-pages", "--current-only", "--quiet-progress"], run=fake
    )

    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["mode"] == "exhaustive-current"
    assert payload["selected_heads"] == 1
    assert captured.err == ""


def test_main_quiet_progress_preserves_structured_nonzero_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeGit(target_valid=False)

    rc = inventory.main(
        [*CLI_ARGS, "--all-pages", "--current-only", "--quiet-progress"], run=fake
    )

    captured = capsys.readouterr()
    assert rc == 2
    assert json.loads(captured.out) == {
        "error": "invalid target ref: development",
        "ok": False,
        "schema_version": inventory.SCHEMA_VERSION,
    }
    assert captured.err == ""


def test_exhaustive_make_target_and_contract_are_tracked() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    contract = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text(encoding="utf-8")
    )

    assert "branch-reconciliation-summary:" in makefile
    assert "--all-pages" in makefile
    assert "--counts-only" in makefile
    assert "--current-only" in makefile
    assert "--quiet-progress" in makefile
    entry = next(
        target
        for target in contract["targets"]
        if target["name"] == "branch-reconciliation-summary"
    )
    assert "RECONCILE_DETAILS" in makefile
    assert "RECONCILE_CURRENT_ONLY" in makefile
    assert "RECONCILE_QUIET_PROGRESS" in makefile
    assert entry["make_variables"] == [
        "RECONCILE_TARGET",
        "RECONCILE_LIMIT",
        "RECONCILE_DETAILS",
        "RECONCILE_CURRENT_ONLY",
        "RECONCILE_QUIET_PROGRESS",
        "RECONCILE_HEAD_SEMANTICS",
    ]
    assert entry["behavior"].endswith(
        "RECONCILE_TARGET=development RECONCILE_LIMIT=100 "
        "RECONCILE_DETAILS=0 RECONCILE_CURRENT_ONLY=0 "
        "RECONCILE_QUIET_PROGRESS=0 RECONCILE_HEAD_SEMANTICS=0"
    )
    assert "RECONCILE_HEAD_SEMANTICS" in makefile
    assert "--head-semantics" in makefile


def test_head_semantics_are_opt_in_and_default_payload_is_compatible(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/current", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
        subjects={UNIQUE_HEAD: "add bounded reconciliation"},
        changed_paths={UNIQUE_HEAD: ("scripts/inventory.py",)},
    )

    rc = inventory.main([*CLI_ARGS, "--all-pages"], run=fake)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "head_summaries" not in payload
    assert not any(call[1] in {"show", "diff", "diff-tree"} for call in fake.calls)


def test_head_semantics_expose_one_bounded_summary_per_deduplicated_head(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeGit(
        refs=[
            ("refs/heads/feature/a", UNIQUE_HEAD),
            ("refs/heads/feature/alias", UNIQUE_HEAD),
        ],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
        subjects={UNIQUE_HEAD: "add bounded reconciliation"},
        changed_paths={
            UNIQUE_HEAD: ("scripts/inventory.py", "tests/unit/test_inventory.py")
        },
    )

    rc = inventory.main(
        [*CLI_ARGS, "--all-pages", "--current-only", "--head-semantics"], run=fake
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["head_summaries"] == [
        {
            "changed_path_count": 2,
            "changed_paths": [
                "scripts/inventory.py",
                "tests/unit/test_inventory.py",
            ],
            "changed_paths_truncated": False,
            "head": UNIQUE_HEAD,
            "path_redactions": 0,
            "subject": "add bounded reconciliation",
            "subject_truncated": False,
        }
    ]
    semantic_calls = [
        call for call in fake.calls if call[1] in {"show", "diff-tree"}
    ]
    assert [call[1] for call in semantic_calls] == ["show", "diff-tree"]
    assert "--format=%s" in semantic_calls[0]
    assert {"--name-only", "-z", "--first-parent"} <= set(semantic_calls[1])


def test_head_semantics_redact_controls_and_bound_subjects_and_paths(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inventory, "SEMANTIC_SUBJECT_CHAR_LIMIT", 10)
    monkeypatch.setattr(inventory, "SEMANTIC_PATH_CHAR_LIMIT", 12)
    monkeypatch.setattr(inventory, "SEMANTIC_PATH_LIMIT", 2)
    fake = FakeGit(
        refs=[("refs/heads/feature/current", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
        subjects={UNIQUE_HEAD: "safe\tbut very long"},
        changed_paths={
            UNIQUE_HEAD: (
                "safe.py",
                "line\nbreak.py",
                "very/long/component/name.py",
            )
        },
    )

    rc = inventory.main([*CLI_ARGS, "--all-pages", "--head-semantics"], run=fake)

    assert rc == 0
    summary = json.loads(capsys.readouterr().out)["head_summaries"][0]
    assert summary["subject"] == "safe�but …"
    assert summary["subject_truncated"] is True
    assert summary["changed_path_count"] == 3
    assert summary["changed_paths"] == ["safe.py", "line�break.…"]
    assert summary["changed_paths_truncated"] is True
    assert summary["path_redactions"] == 1


def test_head_semantics_fail_closed_above_head_bound_before_semantic_git(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inventory, "SEMANTIC_HEAD_LIMIT", 1)
    fake = FakeGit(
        refs=[
            ("refs/heads/feature/a", UNIQUE_HEAD),
            ("refs/heads/feature/b", PATCH_HEAD),
        ],
        cherries={
            UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n",
            PATCH_HEAD: f"+ {PATCH_HEAD}\n",
        },
    )

    rc = inventory.main([*CLI_ARGS, "--all-pages", "--head-semantics"], run=fake)

    assert rc == 2
    assert "semantic head bound" in json.loads(capsys.readouterr().out)["error"]
    assert not any(call[1] in {"show", "diff-tree"} for call in fake.calls)


def test_head_semantics_cover_more_than_legacy_256_head_cap() -> None:
    heads = [f"{index + 1:040x}" for index in range(257)]
    groups: list[inventory.SummaryGroup] = [
        {
            "branch_count": 1,
            "classification": "unique",
            "head": head,
            "lifecycle": "current",
            "names": [f"feature/{index}"],
            "patch_equivalent_commits": 0,
            "refs": [f"refs/heads/feature/{index}"],
            "unique_commits": 1,
        }
        for index, head in enumerate(heads)
    ]

    summaries = inventory.collect_head_summaries(groups, run=FakeGit())

    assert [summary["head"] for summary in summaries] == heads


def test_head_semantics_reject_nonterminal_and_counts_only_modes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for extra in ([], ["--all-pages", "--counts-only"]):
        rc = inventory.main([*CLI_ARGS, "--head-semantics", *extra], run=FakeGit())
        assert rc == 2
        assert "head semantics" in json.loads(capsys.readouterr().out)["error"]


def test_head_semantics_reject_malformed_or_oversized_git_evidence(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGit(
        refs=[("refs/heads/feature/current", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
        changed_paths={UNIQUE_HEAD: ("unterminated.py",)},
        malformed_path_heads=frozenset({UNIQUE_HEAD}),
    )
    args = [*CLI_ARGS, "--all-pages", "--head-semantics"]

    assert inventory.main(args, run=fake) == 2
    assert "malformed semantic path" in json.loads(capsys.readouterr().out)["error"]

    monkeypatch.setattr(inventory, "SEMANTIC_GIT_OUTPUT_CHAR_LIMIT", 3)
    oversized = FakeGit(
        refs=[("refs/heads/feature/current", UNIQUE_HEAD)],
        cherries={UNIQUE_HEAD: f"+ {UNIQUE_HEAD}\n"},
        subjects={UNIQUE_HEAD: "long subject"},
    )
    assert inventory.main(args, run=oversized) == 2
    assert "semantic Git output exceeded" in json.loads(capsys.readouterr().out)["error"]


def _retirement_approval(bound_plan_digest: str, **changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "expected_tip": UNIQUE_HEAD, "plan_digest": bound_plan_digest,
        "reason_code": "superseded", "ref": "refs/heads/feature/retired",
        "reviewer_identity_digest": APPROVAL_REVIEWER_DIGEST,
        "issued_at": "2026-10-07T23:00:00Z", "expires_at": "2026-10-08T23:00:00Z",
        "key_id": APPROVAL_KEY_ID, "version": 2,
    }
    body.update(changes)
    canonical = json.dumps(body, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    message = f"gludd.reconciliation-retirement-approval/v1\0{canonical}"
    return {
        "algorithm": "ed25519", "body": body,
        "signature": APPROVAL_PRIVATE_KEY.sign(message.encode()).hex(),
    }


def _snapshot_fixture(
    equivalence: str | None = None,
) -> dict[str, object]:
    refs = [
        ("refs/heads/feature/blocked", PAGE_HEADS[3]), ("refs/heads/feature/merged", PAGE_HEADS[1]),
        ("refs/heads/feature/pending", PAGE_HEADS[0]), ("refs/heads/feature/retired", UNIQUE_HEAD),
        ("refs/heads/feature/stale", PAGE_HEADS[2]),
    ]
    alias = ("refs/heads/feature/retired-alias", UNIQUE_HEAD)
    if equivalence is not None:
        refs.append(alias)
    heads = [head for _ref, head in refs]
    paths: dict[str, Sequence[str]] = {
        head: (f"src/{index}.py",) for index, head in enumerate(heads)
    }
    paths[PAGE_HEADS[3]] = paths[PAGE_HEADS[0]]
    conflict = (1, f"{EMPTY_HEAD}\0conflict.py\0", "")
    planned = inventory.collect_merge_queue_plan(
        "development", len(refs),
        run=FakeGit(
            refs=refs,
            cherries={head: f"+ {head}\n" for head in heads},
            changed_paths=paths, merge_trees={(TARGET_HEAD, PAGE_HEADS[3]): conflict},
        ),
    )
    fresh_refs = [*refs[:3], ("refs/heads/feature/stale", FINAL_TARGET_HEAD)]
    ancestors = {PAGE_HEADS[1]}
    cherries = {head: f"+ {head}\n" for head in (PAGE_HEADS[0], PAGE_HEADS[3], FINAL_TARGET_HEAD)}
    if equivalence is not None:
        fresh_refs.append(alias)
        if equivalence == "ancestor":
            ancestors.add(UNIQUE_HEAD)
        elif equivalence == "patch-equivalent":
            cherries[UNIQUE_HEAD] = f"- {UNIQUE_HEAD}\n"
        else:
            raise AssertionError("unsupported test equivalence")
    fresh = inventory.collect_summary(
        "development", len(refs),
        run=FakeGit(refs=fresh_refs, ancestors=frozenset(ancestors), cherries=cherries),
    )
    plan_digest = planned["snapshot_basis"]["digest"]
    request: dict[str, object] = {
        "fresh_inventory": fresh, "freshness_digest": inventory.canonical_document_digest(fresh),
        "plan": planned, "plan_digest": plan_digest,
        "retired": [{
            "approval": None if equivalence else _retirement_approval(plan_digest),
            "expected_tip": UNIQUE_HEAD, "ref": "refs/heads/feature/retired",
        }],
    }
    return request


def test_reconciliation_snapshot_classifies_every_branch_and_head_deterministically() -> None:
    request = _snapshot_fixture()
    result = inventory.build_reconciliation_snapshot(
        request, approval_trust=APPROVAL_TRUST, verification_time=APPROVAL_TIME
    )
    assert [entry["status"] for entry in result["branches"]] == [
        "blocked", "merged", "pending", "explicitly-retired", "stale"
    ]
    assert tuple(result["counts"].values()) == (1, 1, 1, 1, 1, 5, 5)
    assert result["complete"] is False and len(result["snapshot_digest"]) == 64
    retired = result["branches"][3]
    assert (retired["retirement_basis"], retired["retirement_reason_code"]) == (
        "operator-approval", "superseded")
    assert len(cast(str, retired["approval_digest"])) == len(
        cast(str, result["approval_keyring_digest"])) == 64
    assert (retired["approval_key_id"], result["verification_time"]) == (
        APPROVAL_KEY_ID, APPROVAL_TIME)
    assert "reviewer_identity_digest" not in retired
    assert result == inventory.build_reconciliation_snapshot(
        copy.deepcopy(request),
        approval_trust=copy.deepcopy(APPROVAL_TRUST),
        verification_time=APPROVAL_TIME,
    )


@pytest.mark.parametrize(
    ("classification", "basis"),
    (("ancestor", "fresh-ancestor"), ("patch-equivalent", "fresh-patch-equivalent")),
)
def test_reconciliation_snapshot_accepts_exact_fresh_retirement_evidence(
    classification: str, basis: str
) -> None:
    request = _snapshot_fixture(classification)
    branches = inventory.build_reconciliation_snapshot(request)["branches"]
    retired = next(entry for entry in branches if entry["ref"].endswith("/retired"))
    actual = (retired["status"], retired["retirement_basis"], retired["approval_digest"])
    assert actual == ("explicitly-retired", basis, None)


def test_reconciliation_snapshot_rejects_digest_and_accounting_ambiguity() -> None:
    request = _snapshot_fixture()
    def variant(path: tuple[str, ...], value: object) -> dict[str, object]:
        changed = copy.deepcopy(request)
        cursor = changed
        for key in path[:-1]:
            cursor = cast(dict[str, object], cursor[key])
        cursor[path[-1]] = value
        if path[0] == "fresh_inventory":
            changed["freshness_digest"] = inventory.canonical_document_digest(changed["fresh_inventory"])
        return changed
    def approval_variant(
        *, outer_tip: str = UNIQUE_HEAD, signature: str | None = None,
        **changes: object,
    ) -> dict[str, object]:
        changed = copy.deepcopy(request)
        entry = cast(list[dict[str, object]], changed["retired"])[0]
        approval = _retirement_approval(cast(str, request["plan_digest"]), **changes)
        if signature is not None:
            approval["signature"] = signature
        entry.update({"approval": approval, "expected_tip": outer_tip})
        return changed
    duplicate = copy.deepcopy(request)
    duplicate["retired"] = cast(list[object], duplicate["retired"]) * 2
    fresh_unknown = copy.deepcopy(request)
    fresh = cast(dict[str, object], fresh_unknown["fresh_inventory"])
    group = cast(list[dict[str, object]], fresh["groups"])[0]
    group["refs"], group["names"] = ["refs/heads/feature/unknown"], ["feature/unknown"]
    fresh_unknown["freshness_digest"] = inventory.canonical_document_digest(fresh)
    bare = {"approval": None, "expected_tip": UNIQUE_HEAD, "ref": "refs/heads/feature/retired"}
    unknown = {**bare, "expected_tip": EMPTY_HEAD, "ref": "refs/heads/feature/unknown"}
    legacy = copy.deepcopy(request)
    legacy_entry = cast(list[dict[str, object]], legacy["retired"])[0]
    signed = cast(dict[str, object], legacy_entry["approval"])
    legacy_entry["approval"] = {"algorithm": "sha256", "body": signed["body"], "digest": "0" * 64}
    invalid_values = (
        None, {**request, "extra": True}, variant(("freshness_digest",), ""),
        variant(("plan", "ok"), False), variant(("plan", "snapshot_basis", "algorithm"), "sha1"),
        variant(("plan", "target", "head"), EMPTY_HEAD),
        variant(("plan", "snapshot_basis", "body", "entries"), None),
        variant(("fresh_inventory", "mode"), "wrong"), variant(("fresh_inventory", "groups"), None),
        variant(("fresh_inventory", "counts"), {}), variant(("retired",), None),
        variant(("freshness_digest",), "0" * 64), variant(("retired",), []),
        duplicate, variant(("retired",), [unknown]), variant(("retired",), [bare]),
        approval_variant(outer_tip=EMPTY_HEAD, expected_tip=EMPTY_HEAD),
        approval_variant(signature="0" * 128),
        approval_variant(reason_code="operator explanation"),
        approval_variant(plan_digest="0" * 64),
        approval_variant(key_id="-bad"),
        approval_variant(key_id="unknown-signer"),
        approval_variant(reviewer_identity_digest="reviewer@example.test"),
        approval_variant(issued_at="2026-10-07 23:00:00Z"),
        approval_variant(issued_at="2026-10-07T23:31:00Z"),
        approval_variant(expires_at=APPROVAL_TIME),
        approval_variant(signature="0" * 130), legacy, fresh_unknown,
    )
    for value in invalid_values:
        with pytest.raises(inventory.InventoryError) as error:
            inventory.build_reconciliation_snapshot(
                value, approval_trust=APPROVAL_TRUST, verification_time=APPROVAL_TIME
            )
        assert str(error.value) == "invalid reconciliation snapshot"
    revoked = copy.deepcopy(APPROVAL_TRUST)
    cast(list[dict[str, object]], revoked["keys"])[0]["status"] = "revoked"
    key = cast(dict[str, object], cast(list[dict[str, object]], APPROVAL_TRUST["keys"])[0])
    oversized = {"keys": [{**key, "key_id": f"key-{index:03d}"} for index in range(129)],
                 "schema_version": 1}
    bad_status = {"keys": [{**key, "status": "disabled"}], "schema_version": 1}
    duplicate_keys = {"keys": [key, key], "schema_version": 1}
    unsorted = {"keys": [key, {**key, "key_id": "aaa"}], "schema_version": 1}
    for trust in (revoked, oversized, bad_status, duplicate_keys, unsorted):
        with pytest.raises(inventory.InventoryError, match="invalid reconciliation snapshot"):
            inventory.build_reconciliation_snapshot(
                request, approval_trust=trust, verification_time=APPROVAL_TIME)
    with pytest.raises(inventory.InventoryError, match="invalid reconciliation snapshot"):
        inventory.build_reconciliation_snapshot(request)
    with pytest.raises(inventory.InventoryError, match="invalid reconciliation snapshot"):
        inventory.build_reconciliation_snapshot(
            _snapshot_fixture("ancestor"), approval_trust=APPROVAL_TRUST,
            verification_time=APPROVAL_TIME)
    def broken_verifier(_message: str, _signature: str, _key: str) -> bool:
        raise ValueError("bounded verifier failure")
    with pytest.raises(inventory.InventoryError, match="invalid reconciliation snapshot"):
        inventory.build_reconciliation_snapshot(
            request, approval_trust=APPROVAL_TRUST,
            verification_time=APPROVAL_TIME, verify_signature=broken_verifier)


def test_reconciliation_snapshot_cli_is_bounded_and_never_invokes_git(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    request = _snapshot_fixture()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    keyring = tmp_path / "retirement-keyring.json"
    keyring.write_text(json.dumps(APPROVAL_TRUST), encoding="utf-8")
    def forbidden_git(_argv: Sequence[str], _cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        raise AssertionError("snapshot reconciliation must not invoke Git")
    args = [*CLI_ARGS, "--reconciliation-snapshot", "--retirement-keyring", str(keyring),
            "--retirement-verification-time", APPROVAL_TIME]
    rc = inventory.main(args, run=forbidden_git)
    payload = json.loads(capsys.readouterr().out)
    assert (rc, payload["mode"], payload["ok"]) == (0, "reconciliation-snapshot", True)


def _remote_evidence(rows: Sequence[tuple[str, str, str, str]], *,
                     expires: str = "2026-10-08T01:00:00Z") -> dict[str, object]:
    remote_refs = [{"head": row[1], "ref": row[0]} for row in rows]
    body = {"expires_at": expires, "fetched_at": "2026-10-08T00:00:00Z",
            "refs": remote_refs, "remote": "origin", "version": 1}
    return {"algorithm": "sha256", "body": body,
            "digest": inventory.canonical_document_digest(body)}


class RemoteGit:
    def __init__(self, local: Sequence[tuple[str, str, str, str]],
                 remote: Sequence[tuple[str, str, str, str]]) -> None:
        self.local, self.remote, self.calls = local, remote, []
    def __call__(self, argv: Sequence[str], _cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        self.calls.append(args)
        rows = self.local if args[-1] == "refs/heads/" else self.remote
        output = "".join("\0".join(row) + "\n" for row in rows)
        return subprocess.CompletedProcess(args, 0, output, "")


def test_remote_tracking_inventory_accounts_for_every_local_and_remote_branch(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    local = [("refs/heads/deleted", PAGE_HEADS[0], "refs/remotes/origin/deleted", ""),
             ("refs/heads/diverged", PAGE_HEADS[1], "refs/remotes/origin/diverged", ""),
             ("refs/heads/equal", PAGE_HEADS[2], "refs/remotes/origin/equal", ""),
             ("refs/heads/local", PAGE_HEADS[3], "", "")]
    remote = [("refs/remotes/origin/diverged", UNIQUE_HEAD, "", ""),
              ("refs/remotes/origin/equal", PAGE_HEADS[2], "", ""),
              ("refs/remotes/origin/remote", EMPTY_HEAD, "", "")]
    fake, evidence = RemoteGit(local, remote), _remote_evidence(remote)
    result = inventory.collect_remote_tracking_inventory(
        "origin", evidence, "2026-10-08T00:30:00Z", run=fake)
    assert [entry["classification"] for entry in result["branches"]] == [
        "deleted-upstream", "diverged", "equal", "local-only", "remote-only"]
    assert [entry["recommendation"] for entry in result["branches"]] == [
        "delete-candidate", "review", "retain", "retain", "review"]
    assert result["counts"]["total"] == 5 and result["freshness_digest"] == evidence["digest"]
    assert {call[1] for call in fake.calls} == {"for-each-ref"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(evidence)))
    args = [*CLI_ARGS, "--remote-tracking", "--remote-name", "origin",
            "--remote-verification-time", "2026-10-08T00:30:00Z"]
    assert inventory.main(args, run=RemoteGit(local, remote)) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "remote-tracking-reconciliation"
    assert inventory.main([*CLI_ARGS, "--remote-tracking"], run=RemoteGit(local, remote)) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "invalid remote tracking inventory"


def test_remote_tracking_inventory_rejects_stale_unsafe_or_ambiguous_evidence(
    monkeypatch: pytest.MonkeyPatch) -> None:
    local = [("refs/heads/a", PAGE_HEADS[0], "refs/remotes/origin/a", "")]
    remote = [("refs/remotes/origin/a", PAGE_HEADS[0], "", "")]
    evidence = _remote_evidence(remote)
    tampered = copy.deepcopy(evidence)
    tampered["digest"] = "0" * 64
    cases = [(name, evidence, RemoteGit(local, remote))
             for name in ("-unsafe", "origin.", "origin..bad")] + [
        ("origin", tampered, RemoteGit(local, remote)),
        ("origin", _remote_evidence(remote, expires="2026-10-08T00:30:00Z"), RemoteGit(local, remote)),
        ("origin", evidence, RemoteGit(local, [(remote[0][0], remote[0][1], "", "refs/remotes/origin/a")])),
        ("origin", _remote_evidence([remote[0], remote[0]]), RemoteGit(local, [remote[0], remote[0]])),
        ("origin", _remote_evidence([("refs/remotes/origin/../a", PAGE_HEADS[0], "", "")]),
         RemoteGit(local, [("refs/remotes/origin/../a", PAGE_HEADS[0], "", "")])),
        ("origin", evidence,
         RemoteGit([("refs/heads/a", PAGE_HEADS[0], "refs/remotes/origin/b", "")], remote)),
    ]
    bad_rows = [
        [("refs/heads/a", PAGE_HEADS[0], "", "")],
        [("refs/remotes/origin/HEAD", PAGE_HEADS[0], "", "")],
        [(remote[0][0], remote[0][1], "refs/remotes/origin/upstream", "")],
    ]
    cases.extend(("origin", _remote_evidence(rows), RemoteGit(local, rows))
                 for rows in bad_rows)
    cases.append(("origin", _remote_evidence([]), RemoteGit(local, remote)))
    wrong_head = [(remote[0][0], UNIQUE_HEAD, "", "")]
    cases.append(("origin", _remote_evidence(wrong_head), RemoteGit(local, remote)))
    for remote_name, candidate, fake in cases:
        with pytest.raises(inventory.InventoryError) as error:
            inventory.collect_remote_tracking_inventory(
                remote_name, candidate, "2026-10-08T00:30:00Z", run=fake)
        assert str(error.value) == "invalid remote tracking inventory"
    monkeypatch.setattr(inventory, "REMOTE_REF_SCAN_LIMIT", 0)
    with pytest.raises(inventory.InventoryError):
        inventory.collect_remote_tracking_inventory(
            "origin", evidence, "2026-10-08T00:30:00Z", run=RemoteGit(local, remote))
    for bound in ("REMOTE_GIT_OUTPUT_CHAR_LIMIT", "REMOTE_JSON_CHAR_LIMIT"):
        with monkeypatch.context() as bounded:
            bounded.setattr(inventory, bound, 1)
            with pytest.raises(inventory.InventoryError):
                inventory.collect_remote_tracking_inventory(
                    "origin", evidence, "2026-10-08T00:30:00Z", run=RemoteGit(local, remote))
