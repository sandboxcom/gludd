from __future__ import annotations

import copy
import io
import json
import subprocess
import sys
from collections.abc import Sequence

import pytest
from scripts import branch_reconciliation_inventory as inventory

UNIQUE_HEAD = "d" * 40
PAGE_HEADS = tuple(character * 40 for character in "1234")
CLI_ARGS = ["--target", "development", "--limit", "2", "--after", ""]


def test_receipt_validation_primitives_reject_malformed_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_dicts: tuple[object, ...] = ([], {1: "value"}, {"wrong": "value"})
    for invalid_dict in invalid_dicts:
        with pytest.raises(
            inventory.InventoryError,
            match="invalid reconciliation receipt",
        ):
            inventory._receipt_dict(invalid_dict, frozenset({"expected"}))
    invalid_integers: tuple[object, ...] = (True, "1", -1, 2)
    for invalid_integer in invalid_integers:
        with pytest.raises(
            inventory.InventoryError,
            match="invalid reconciliation receipt",
        ):
            inventory._receipt_integer(invalid_integer, minimum=0, maximum=1)
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
    for invalid_ref in invalid_refs:
        with pytest.raises(
            inventory.InventoryError,
            match="invalid reconciliation receipt",
        ):
            inventory._receipt_ref(invalid_ref)
    invalid_ref_lists: tuple[object, ...] = (
        "refs/heads/a",
        [],
        ["refs/heads/b", "refs/heads/a"],
        ["refs/heads/a", "refs/heads/a"],
    )
    for invalid_ref_list in invalid_ref_lists:
        with pytest.raises(
            inventory.InventoryError,
            match="invalid reconciliation receipt",
        ):
            inventory._receipt_refs(invalid_ref_list)
    with pytest.raises(
        inventory.InventoryError,
        match="invalid reconciliation receipt",
    ):
        inventory._canonical_receipt_body({"unsupported": object()})
    monkeypatch.setattr(inventory, "RECEIPT_JSON_CHAR_LIMIT", 2)
    with pytest.raises(
        inventory.InventoryError,
        match="invalid reconciliation receipt",
    ):
        inventory._canonical_receipt_body({"key": "value"})


def _remote_evidence(
    rows: Sequence[tuple[str, str, str, str]],
    *,
    expires: str = "2026-10-08T01:00:00Z",
) -> dict[str, object]:
    remote_refs = [{"head": row[1], "ref": row[0]} for row in rows]
    body = {
        "expires_at": expires,
        "fetched_at": "2026-10-08T00:00:00Z",
        "refs": remote_refs,
        "remote": "origin",
        "version": 1,
    }
    return {
        "algorithm": "sha256",
        "body": body,
        "digest": inventory.canonical_document_digest(body),
    }


class RemoteGit:
    def __init__(
        self,
        local: Sequence[tuple[str, str, str, str]],
        remote: Sequence[tuple[str, str, str, str]],
    ) -> None:
        self.local = local
        self.remote = remote
        self.calls: list[list[str]] = []

    def __call__(
        self,
        argv: Sequence[str],
        _cwd: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        self.calls.append(args)
        rows = self.local if args[-1] == "refs/heads/" else self.remote
        output = "".join("\0".join(row) + "\n" for row in rows)
        return subprocess.CompletedProcess(args, 0, output, "")


def test_remote_tracking_inventory_accounts_for_every_local_and_remote_branch(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local = [
        ("refs/heads/deleted", PAGE_HEADS[0], "refs/remotes/origin/deleted", ""),
        ("refs/heads/diverged", PAGE_HEADS[1], "refs/remotes/origin/diverged", ""),
        ("refs/heads/equal", PAGE_HEADS[2], "refs/remotes/origin/equal", ""),
        ("refs/heads/local", PAGE_HEADS[3], "", ""),
    ]
    remote = [
        ("refs/remotes/origin/diverged", UNIQUE_HEAD, "", ""),
        ("refs/remotes/origin/equal", PAGE_HEADS[2], "", ""),
        ("refs/remotes/origin/remote", "e" * 40, "", ""),
    ]
    fake, evidence = RemoteGit(local, remote), _remote_evidence(remote)
    result = inventory.collect_remote_tracking_inventory(
        "origin",
        evidence,
        "2026-10-08T00:30:00Z",
        run=fake,
    )
    assert [entry["classification"] for entry in result["branches"]] == [
        "deleted-upstream",
        "diverged",
        "equal",
        "local-only",
        "remote-only",
    ]
    assert [entry["recommendation"] for entry in result["branches"]] == [
        "delete-candidate",
        "review",
        "retain",
        "retain",
        "review",
    ]
    assert result["counts"]["total"] == 5
    assert result["freshness_digest"] == evidence["digest"]
    assert {call[1] for call in fake.calls} == {"for-each-ref"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(evidence)))
    args = [
        *CLI_ARGS,
        "--remote-tracking",
        "--remote-name",
        "origin",
        "--remote-verification-time",
        "2026-10-08T00:30:00Z",
    ]
    assert inventory.main(args, run=RemoteGit(local, remote)) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == (
        "remote-tracking-reconciliation"
    )
    assert inventory.main(
        [*CLI_ARGS, "--remote-tracking"],
        run=RemoteGit(local, remote),
    ) == 2
    assert json.loads(capsys.readouterr().out)["error"] == (
        "invalid remote tracking inventory"
    )


def test_remote_tracking_inventory_rejects_stale_unsafe_or_ambiguous_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local = [("refs/heads/a", PAGE_HEADS[0], "refs/remotes/origin/a", "")]
    remote = [("refs/remotes/origin/a", PAGE_HEADS[0], "", "")]
    evidence = _remote_evidence(remote)
    tampered = copy.deepcopy(evidence)
    tampered["digest"] = "0" * 64
    cases = [
        (name, evidence, RemoteGit(local, remote))
        for name in ("-unsafe", "origin.", "origin..bad")
    ] + [
        ("origin", tampered, RemoteGit(local, remote)),
        (
            "origin",
            _remote_evidence(remote, expires="2026-10-08T00:30:00Z"),
            RemoteGit(local, remote),
        ),
        (
            "origin",
            evidence,
            RemoteGit(
                local,
                [(remote[0][0], remote[0][1], "", "refs/remotes/origin/a")],
            ),
        ),
        (
            "origin",
            _remote_evidence([remote[0], remote[0]]),
            RemoteGit(local, [remote[0], remote[0]]),
        ),
        (
            "origin",
            _remote_evidence(
                [("refs/remotes/origin/../a", PAGE_HEADS[0], "", "")]
            ),
            RemoteGit(
                local,
                [("refs/remotes/origin/../a", PAGE_HEADS[0], "", "")],
            ),
        ),
        (
            "origin",
            evidence,
            RemoteGit(
                [("refs/heads/a", PAGE_HEADS[0], "refs/remotes/origin/b", "")],
                remote,
            ),
        ),
    ]
    bad_rows = [
        [("refs/heads/a", PAGE_HEADS[0], "", "")],
        [("refs/remotes/origin/HEAD", PAGE_HEADS[0], "", "")],
        [(remote[0][0], remote[0][1], "refs/remotes/origin/upstream", "")],
    ]
    cases.extend(
        ("origin", _remote_evidence(rows), RemoteGit(local, rows))
        for rows in bad_rows
    )
    cases.append(("origin", _remote_evidence([]), RemoteGit(local, remote)))
    wrong_head = [(remote[0][0], UNIQUE_HEAD, "", "")]
    cases.append(("origin", _remote_evidence(wrong_head), RemoteGit(local, remote)))
    for remote_name, candidate, fake in cases:
        with pytest.raises(inventory.InventoryError) as error:
            inventory.collect_remote_tracking_inventory(
                remote_name,
                candidate,
                "2026-10-08T00:30:00Z",
                run=fake,
            )
        assert str(error.value) == "invalid remote tracking inventory"
    monkeypatch.setattr(inventory, "REMOTE_REF_SCAN_LIMIT", 0)
    with pytest.raises(inventory.InventoryError):
        inventory.collect_remote_tracking_inventory(
            "origin",
            evidence,
            "2026-10-08T00:30:00Z",
            run=RemoteGit(local, remote),
        )
    for bound in ("REMOTE_GIT_OUTPUT_CHAR_LIMIT", "REMOTE_JSON_CHAR_LIMIT"):
        with monkeypatch.context() as bounded:
            bounded.setattr(inventory, bound, 1)
            with pytest.raises(inventory.InventoryError):
                inventory.collect_remote_tracking_inventory(
                    "origin",
                    evidence,
                    "2026-10-08T00:30:00Z",
                    run=RemoteGit(local, remote),
                )
