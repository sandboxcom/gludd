"""Fail-closed tests for stale Gludd resource-namespace cleanup."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest
from scripts import clean_stale_resource_namespaces as cleanup
from scripts.clean_stale_resource_namespaces import (
    CleanupConfig,
    ProcessIdentity,
    apply_cleanup,
    plan_cleanup,
    protected_namespace_names,
)
from scripts.collection_lock import LeaseInspection, LeaseRecord
from scripts.resource_arbiter import project_namespace

NOW = 2_000_000_000.0
OLD = NOW - 86_400.0


def _namespace(root: Path, name: str, *, toolchain: bool = False) -> Path:
    suffix = "-toolchain" if toolchain else ""
    path = root / f"{name}-0123456789ab{suffix}"
    path.mkdir(parents=True)
    (path / "payload.bin").write_bytes(b"payload")
    for item in (path / "payload.bin", path):
        os.utime(item, (OLD, OLD))
    return path


def _config(root: Path, receipt: Path, *, validate_only: bool = True) -> CleanupConfig:
    return CleanupConfig(
        root=root,
        grace_seconds=3_600,
        now_epoch=NOW,
        validate_only=validate_only,
        receipt_path=receipt,
        max_candidates=50,
        max_entries_per_namespace=100,
        heartbeat_seconds=1.0,
    )


def test_stale_inactive_namespace_and_toolchain_are_one_reclaim_unit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    base = _namespace(root, "checkout")
    toolchain = _namespace(root, "checkout", toolchain=True)

    plan = plan_cleanup(_config(root, tmp_path / "receipts.jsonl"), set(), {})

    assert len(plan.decisions) == 1
    decision = plan.decisions[0]
    assert decision.action == "reclaim"
    assert decision.reason == "stale-and-proven-inactive"
    assert decision.paths == (base, toolchain)


def test_apply_revalidates_and_writes_one_receipt_per_deleted_path(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    base = _namespace(root, "checkout")
    toolchain = _namespace(root, "checkout", toolchain=True)
    receipt = tmp_path / "receipts.jsonl"
    config = _config(root, receipt, validate_only=False)
    plan = plan_cleanup(config, set(), {})

    result = apply_cleanup(plan, config, set(), {})

    assert result.deleted == (base, toolchain)
    assert not base.exists()
    assert not toolchain.exists()
    records = [json.loads(line) for line in receipt.read_text().splitlines()]
    assert [record["path"] for record in records] == [str(base), str(toolchain)]
    assert all(record["outcome"] == "deleted" for record in records)
    assert all(record["reason"] == "stale-and-proven-inactive" for record in records)


def test_validate_only_never_mutates_or_writes_receipts(tmp_path: Path) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "checkout")
    receipt = tmp_path / "receipts.jsonl"
    config = _config(root, receipt)
    plan = plan_cleanup(config, set(), {})

    result = apply_cleanup(plan, config, set(), {})

    assert result.deleted == ()
    assert candidate.is_dir()
    assert not receipt.exists()


def test_current_registered_and_git_worktree_namespaces_are_protected(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    registered = tmp_path / "registered"
    linked = tmp_path / "linked"
    for worktree in (checkout, registered, linked):
        worktree.mkdir()
    names = protected_namespace_names(checkout, (registered, linked))
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidates = []
    for name in names:
        path = root / name
        path.mkdir()
        os.utime(path, (OLD, OLD))
        candidates.append(path)

    plan = plan_cleanup(_config(root, tmp_path / "receipts"), names, {})

    assert len(plan.decisions) == 3
    assert all(decision.action == "preserve" for decision in plan.decisions)
    assert all(decision.reason == "protected-worktree-namespace" for decision in plan.decisions)
    assert {decision.paths[0] for decision in plan.decisions} == set(candidates)
    assert project_namespace(checkout) in names


def test_fresh_or_unrecognized_paths_are_preserved(tmp_path: Path) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    fresh = _namespace(root, "fresh")
    os.utime(fresh, (NOW, NOW))
    unknown = root / "operator-data"
    unknown.mkdir()
    os.utime(unknown, (OLD, OLD))

    plan = plan_cleanup(_config(root, tmp_path / "receipts"), set(), {})

    reasons = {decision.paths[0].name: decision.reason for decision in plan.decisions}
    assert reasons[fresh.name] == "within-grace-period"
    assert reasons[unknown.name] == "unrecognized-namespace"


@pytest.mark.parametrize("state", ["held", "unavailable"])
def test_held_or_uninspectable_kernel_lease_preserves_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: Literal["held", "unavailable"],
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    base = _namespace(root, "leased")
    toolchain = _namespace(root, "leased", toolchain=True)
    lock = base / "collection.lock"
    lock.write_text("pid=123\nacquired_unix_ns=1\n")
    os.utime(lock, (OLD, OLD))
    monkeypatch.setattr(
        "scripts.clean_stale_resource_namespaces.inspect_lease",
        lambda _path: LeaseInspection(state=state, record=LeaseRecord(123, 1)),
    )

    plan = plan_cleanup(_config(root, tmp_path / "receipts"), set(), {})

    assert plan.decisions[0].action == "preserve"
    assert plan.decisions[0].reason == f"lease-{state}"
    assert plan.decisions[0].paths == (base, toolchain)


def test_live_pid_requires_matching_start_identity_and_ambiguous_pid_fails_closed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    exact = _namespace(root, "exact")
    (exact / "worker.pid").write_text(
        json.dumps({"pid": 321, "pid_started_at": "Mon Oct 5 12:00:00 2026"})
    )
    ambiguous = _namespace(root, "ambiguous")
    (ambiguous / "worker.pid").write_text("321\n")
    reused = _namespace(root, "reused")
    (reused / "worker.pid").write_text(
        json.dumps({"pid": 321, "pid_started_at": "Sun Oct 4 12:00:00 2026"})
    )
    for marker in root.glob("*/worker.pid"):
        os.utime(marker, (OLD, OLD))
    processes = {321: ProcessIdentity(321, "Mon Oct 5 12:00:00 2026")}

    plan = plan_cleanup(_config(root, tmp_path / "receipts"), set(), processes)
    decisions = {decision.base_name: decision for decision in plan.decisions}

    assert decisions[exact.name].reason == "live-lease-owner"
    assert decisions[ambiguous.name].reason == "ambiguous-lease-owner"
    assert decisions[reused.name].action == "reclaim"


def test_missing_process_census_fails_closed_for_every_candidate(tmp_path: Path) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    _namespace(root, "checkout")

    plan = plan_cleanup(_config(root, tmp_path / "receipts"), set(), None)

    assert plan.decisions[0].action == "preserve"
    assert plan.decisions[0].reason == "process-census-unavailable"


def test_symlink_root_candidate_and_descendant_are_never_reclaimed(tmp_path: Path) -> None:
    real = tmp_path / "gludd-resources"
    real.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.mkdir()
    linked_root = linked_parent / "gludd-resources"
    linked_root.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="root must not be a symlink"):
        plan_cleanup(_config(linked_root, tmp_path / "receipts"), set(), {})

    candidate_target = tmp_path / "outside"
    candidate_target.mkdir()
    candidate = real / "linked-0123456789ab"
    candidate.symlink_to(candidate_target, target_is_directory=True)
    nested = _namespace(real, "nested")
    (nested / "escape").symlink_to(candidate_target, target_is_directory=True)

    plan = plan_cleanup(_config(real, tmp_path / "receipts"), set(), {})
    reasons = {decision.base_name: decision.reason for decision in plan.decisions}
    assert reasons[candidate.name] == "candidate-symlink"
    assert reasons[nested.name] == "descendant-symlink"


def test_apply_refuses_path_identity_change_between_plan_and_delete(tmp_path: Path) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "checkout")
    config = _config(root, tmp_path / "receipts", validate_only=False)
    plan = plan_cleanup(config, set(), {})
    original = candidate / "payload.bin"
    original.unlink()
    replacement = candidate / "replacement.bin"
    replacement.write_bytes(b"new")
    os.utime(replacement, (OLD, OLD))

    result = apply_cleanup(plan, config, set(), {})

    assert result.deleted == ()
    assert result.refused == (candidate,)
    assert candidate.exists()
    assert not config.receipt_path.exists()


def test_inventory_bound_and_invalid_grace_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    _namespace(root, "one")
    _namespace(root, "two")
    config = _config(root, tmp_path / "receipts")
    bounded = CleanupConfig(**{**config.__dict__, "max_candidates": 1})
    with pytest.raises(ValueError, match="candidate limit"):
        plan_cleanup(bounded, set(), {})
    invalid = CleanupConfig(**{**config.__dict__, "grace_seconds": -1})
    with pytest.raises(ValueError, match="grace"):
        plan_cleanup(invalid, set(), {})


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"max_candidates": 0}, "candidate limit"),
        ({"max_entries_per_namespace": 0}, "entry limit"),
        ({"heartbeat_seconds": 0.0}, "heartbeat"),
    ],
)
def test_all_numeric_bounds_fail_closed(
    tmp_path: Path, changes: dict[str, object], message: str
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    config = _config(root, tmp_path / "receipts")
    invalid = CleanupConfig(**{**config.__dict__, **changes})

    with pytest.raises(ValueError, match=message):
        plan_cleanup(invalid, set(), {})


def test_marker_parsing_accepts_bounded_formats_and_rejects_ambiguity(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "worker.pid"
    marker.write_text("pid=42\npid_started_at=process-start\n")
    assert cleanup._marker_owner(marker) == (42, "process-start")
    marker.write_text(json.dumps({"owner_pid": 43, "started_at": "other-start"}))
    assert cleanup._marker_owner(marker) == (43, "other-start")
    marker.write_text(json.dumps({"unrelated": 44}))
    assert cleanup._marker_owner(marker) is None
    marker.write_bytes(b"\xff")
    assert cleanup._marker_owner(marker) is None
    marker.write_bytes(b"x" * 4_097)
    assert cleanup._marker_owner(marker) is None
    marker.unlink()
    assert cleanup._marker_owner(marker) is None


def test_available_kernel_lock_uses_record_and_empty_lock_is_inactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "locked")
    lock = candidate / "collection.lock"
    lock.write_text("")
    os.utime(lock, (OLD, OLD))
    monkeypatch.setattr(
        cleanup,
        "inspect_lease",
        lambda _path: LeaseInspection("available", LeaseRecord(555, 1)),
    )
    plan = plan_cleanup(
        _config(root, tmp_path / "receipts"),
        set(),
        {555: ProcessIdentity(555, "live-start")},
    )
    assert plan.decisions[0].reason == "ambiguous-lease-owner"

    monkeypatch.setattr(
        cleanup,
        "inspect_lease",
        lambda _path: LeaseInspection("available", None),
    )
    assert plan_cleanup(_config(root, tmp_path / "receipts"), set(), {}).decisions[
        0
    ].action == "reclaim"


def test_directory_scan_refuses_file_hardlink_and_entry_overflow(tmp_path: Path) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    regular = root / "regular-0123456789ab"
    regular.write_text("not a directory")
    os.utime(regular, (OLD, OLD))
    linked = _namespace(root, "hardlink")
    os.link(linked / "payload.bin", linked / "second.bin")
    for item in linked.iterdir():
        os.utime(item, (OLD, OLD))
    overflow = _namespace(root, "overflow")
    (overflow / "second.bin").write_bytes(b"second")
    for item in overflow.iterdir():
        os.utime(item, (OLD, OLD))
    config = CleanupConfig(
        **{**_config(root, tmp_path / "receipts").__dict__, "max_entries_per_namespace": 1}
    )

    decisions = {item.base_name: item for item in plan_cleanup(config, set(), {}).decisions}

    assert decisions[regular.name].reason == "candidate-not-owned-directory"
    assert decisions[linked.name].reason == "descendant-hardlink"
    assert decisions[overflow.name].reason == "namespace-entry-limit"


def test_process_census_parses_stable_starts_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=(
                "1 Thu Oct  8 00:00:00 2026\n"
                "42 Thu Oct  8 01:02:03 2026\n"
            )
        ),
    )
    assert cleanup._process_identities() == {
        42: ProcessIdentity(42, "Thu Oct 8 01:02:03 2026")
    }
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="malformed\n"),
    )
    assert cleanup._process_identities() is None

    def unavailable(*_args: object, **_kwargs: object) -> object:
        raise subprocess.TimeoutExpired("ps", 10)

    monkeypatch.setattr(subprocess, "run", unavailable)
    assert cleanup._process_identities() is None


def test_git_and_registry_worktree_inventories_are_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    linked = tmp_path / "linked"
    missing = tmp_path / "registered-but-missing"
    project.mkdir()
    linked.mkdir()
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=(
                f"worktree {project}\nHEAD abc\n\nworktree {linked}\n\n"
                f"worktree {missing}\n"
            )
        ),
    )
    assert cleanup._git_worktrees(project) == (project, linked, missing)
    assert project_namespace(missing) in protected_namespace_names(project, (missing,))
    monkeypatch.setattr(
        subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(stdout="")
    )
    with pytest.raises(ValueError, match="inventory is empty"):
        cleanup._git_worktrees(project)

    registry = tmp_path / "registry.json"
    assert cleanup._registered_worktrees(registry) == ()
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "workstreams": {
                    "agent/demo": {
                        "branch": "agent/demo",
                        "status": "active",
                        "updated_epoch": 1,
                        "worktree": str(linked),
                    }
                },
            }
        )
    )
    assert cleanup._registered_worktrees(registry) == (linked,)
    registry.write_text(json.dumps({"version": 2, "workstreams": {}}))
    with pytest.raises(ValueError, match="schema"):
        cleanup._registered_worktrees(registry)


def test_registered_worktree_rejects_symlink_and_ambiguous_entries(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "registry.json"
    target = tmp_path / "target.json"
    target.write_text("{}")
    registry.symlink_to(target)
    with pytest.raises(ValueError, match="unsafe"):
        cleanup._registered_worktrees(registry)
    registry.unlink()
    registry.write_text("not-json")
    with pytest.raises(ValueError, match="unreadable"):
        cleanup._registered_worktrees(registry)
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "workstreams": {
                    "agent/demo": {"branch": "wrong", "status": "active"}
                },
            }
        )
    )
    with pytest.raises(ValueError, match="ambiguous"):
        cleanup._registered_worktrees(registry)


def test_main_validate_only_inventory_and_refusal_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    root = tmp_path / "gludd-resources"
    receipt = tmp_path / "receipt.jsonl"
    monkeypatch.setattr(cleanup, "_git_worktrees", lambda _root: (project,))
    monkeypatch.setattr(cleanup, "_registered_worktrees", lambda _path: ())
    monkeypatch.setattr(cleanup, "_process_identities", lambda: {})

    args = [
        "--root",
        str(root),
        "--project-root",
        str(project),
        "--registry",
        str(tmp_path / "registry"),
        "--receipt",
        str(receipt),
        "--grace-seconds",
        "3600",
        "--validate-only",
    ]
    assert cleanup.main(args) == 0
    output = capsys.readouterr().out
    assert "RESOURCE_CLEANUP_PHASE" in output
    assert "deleted=0" in output

    args[1] = str(tmp_path / "wrong-name")
    assert cleanup.main(args) == 2
    assert "RESOURCE_CLEANUP_REFUSED" in capsys.readouterr().out


def test_main_apply_deletes_stale_namespace_with_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "old")
    receipt = tmp_path / "receipt.jsonl"
    monkeypatch.setattr(cleanup, "_git_worktrees", lambda _root: (project,))
    monkeypatch.setattr(cleanup, "_registered_worktrees", lambda _path: ())
    monkeypatch.setattr(cleanup, "_process_identities", lambda: {})
    monkeypatch.setattr(time, "time", lambda: NOW)

    result = cleanup.main(
        [
            "--root",
            str(root),
            "--project-root",
            str(project),
            "--registry",
            str(tmp_path / "registry"),
            "--receipt",
            str(receipt),
            "--grace-seconds",
            "3600",
            "--apply",
        ]
    )

    assert result == 0
    assert not candidate.exists()
    assert json.loads(receipt.read_text())["outcome"] == "deleted"


def test_receipt_location_and_slow_delete_guardrails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    with pytest.raises(ValueError, match="outside"):
        cleanup._append_receipt(root / "receipt", {}, root)
    destination = tmp_path / "receipt"
    target = tmp_path / "target"
    target.write_text("target")
    destination.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        cleanup._append_receipt(destination, {}, root)

    candidate = _namespace(root, "slow")
    original = shutil.rmtree

    class SlowDelete:
        avoids_symlink_attacks = True

        def __call__(self, path: Path) -> None:
            time.sleep(0.02)
            original(path)

    monkeypatch.setattr(shutil, "rmtree", SlowDelete())
    cleanup._delete_with_heartbeats(candidate, 0.001)
    assert "RESOURCE_CLEANUP_HEARTBEAT" in capsys.readouterr().out
