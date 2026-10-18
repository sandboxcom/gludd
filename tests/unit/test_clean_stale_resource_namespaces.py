"""Fail-closed tests for stale Gludd resource-namespace cleanup."""

from __future__ import annotations

import json
import marshal
import os
import py_compile
import shutil
import subprocess
import time
from base64 import urlsafe_b64encode
from collections.abc import Mapping
from hashlib import sha256
from importlib.util import cache_from_source
from pathlib import Path
from types import SimpleNamespace
from typing import IO, TYPE_CHECKING, Literal, Never

import pytest

if TYPE_CHECKING:
    import clean_stale_resource_namespaces as cleanup
    from clean_stale_resource_namespaces import (
        CleanupConfig,
        ProcessIdentity,
        apply_cleanup,
        plan_cleanup,
        protected_namespace_names,
    )
    from collection_lock import LeaseInspection, LeaseRecord
    from resource_arbiter import project_namespace
else:
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GLUDD_PROJECT_NAMESPACE", raising=False)
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
        lambda _path, **_options: LeaseInspection(state=state, record=LeaseRecord(123, 1)),
    )

    plan = plan_cleanup(_config(root, tmp_path / "receipts"), set(), {})

    assert plan.decisions[0].action == "preserve"
    assert plan.decisions[0].reason == f"lease-{state}"
    assert plan.decisions[0].paths == (base, toolchain)
    assert plan.decisions[0].blocking_marker == lock


def test_live_pid_requires_matching_start_identity_and_ambiguous_pid_fails_closed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    exact = _namespace(root, "exact")
    (exact / "worker.pid").write_text(json.dumps({"pid": 321, "pid_started_at": "Mon Oct 5 12:00:00 2026"}))
    ambiguous = _namespace(root, "ambiguous")
    (ambiguous / "worker.pid").write_text("321\n")
    reused = _namespace(root, "reused")
    (reused / "worker.pid").write_text(json.dumps({"pid": 321, "pid_started_at": "Sun Oct 4 12:00:00 2026"}))
    for marker in root.glob("*/worker.pid"):
        os.utime(marker, (OLD, OLD))
    processes = {321: ProcessIdentity(321, "Mon Oct 5 12:00:00 2026")}

    plan = plan_cleanup(_config(root, tmp_path / "receipts"), set(), processes)
    decisions = {decision.base_name: decision for decision in plan.decisions}

    assert decisions[exact.name].reason == "live-lease-owner"
    assert decisions[ambiguous.name].reason == "ambiguous-lease-owner"
    assert decisions[reused.name].action == "reclaim"
    assert decisions[exact.name].blocking_marker == exact / "worker.pid"
    assert decisions[ambiguous.name].blocking_marker == ambiguous / "worker.pid"
    assert decisions[reused.name].blocking_marker is None


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
    assert reasons[nested.name] == "stale-and-proven-inactive"
    assert candidate.is_symlink() and candidate_target.is_dir()


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
def test_all_numeric_bounds_fail_closed(tmp_path: Path, changes: dict[str, object], message: str) -> None:
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


def _recorded_package_file(
    candidate: Path,
    filename: str = "virtualenv/util/lock.py",
    content: bytes = b"import threading\n\nclass PackageLock:\n    pass\n",
) -> tuple[Path, Path]:
    site_packages = candidate / "ci-shards/python-3.11/lib/python3.11/site-packages"
    installed = site_packages / filename
    installed.parent.mkdir(parents=True)
    installed.write_bytes(content)
    metadata = site_packages / "fixture_distribution-1.0.dist-info"
    metadata.mkdir(exist_ok=True)
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: fixture-distribution\nVersion: 1.0\n",
        encoding="utf-8",
    )
    digest = urlsafe_b64encode(sha256(content).digest()).decode("ascii").rstrip("=")
    record = metadata / "RECORD"
    record.write_text(
        f"{filename},sha256={digest},{len(content)}\nfixture_distribution-1.0.dist-info/RECORD,,\n",
        encoding="utf-8",
    )
    for path in (candidate, *candidate.rglob("*")):
        os.utime(path, (OLD, OLD))
    return installed, record


@pytest.mark.parametrize("module_name", ("lock.py", "lease.py", "owner.py", "pid.py"))
def test_recorded_installed_module_is_not_a_runtime_lease_marker(
    tmp_path: Path,
    module_name: str,
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "installed", toolchain=True)
    module, record = _recorded_package_file(candidate, f"virtualenv/util/{module_name}")
    original_module = module.read_bytes()
    original_record = record.read_bytes()
    receipt = tmp_path / "receipts"

    decision = plan_cleanup(_config(root, receipt), set(), {}).decisions[0]

    assert decision.action == "reclaim"
    assert decision.reason == "stale-and-proven-inactive"
    assert decision.blocking_marker is None
    assert module.read_bytes() == original_module
    assert record.read_bytes() == original_record
    assert not receipt.exists()


@pytest.mark.parametrize(
    "content",
    [
        b"# no-same-owner\n\nPackaged rule documentation.\n",
        b"Owner reference\n===============\n\nPackaged rule documentation.\n",
    ],
)
def test_recorded_installed_documentation_is_not_a_runtime_owner(
    tmp_path: Path,
    content: bytes,
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "documentation", toolchain=True)
    document, _record = _recorded_package_file(candidate, "ansiblelint/rules/no_same_owner.md", content)
    original = document.read_bytes()

    decision = plan_cleanup(_config(root, tmp_path / "receipts"), set(), {}).decisions[0]

    assert decision.action == "reclaim"
    assert decision.blocking_marker is None
    assert document.read_bytes() == original


@pytest.mark.parametrize("content", [b'{"unknown_owner": 321}\n', b"opaque-owner-record-secret\n"])
def test_record_membership_does_not_exempt_opaque_markdown_owner_records(
    tmp_path: Path,
    content: bytes,
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "opaque_document", toolchain=True)
    document, _record = _recorded_package_file(candidate, "ansiblelint/rules/no_same_owner.md", content)

    decision = plan_cleanup(_config(root, tmp_path / "receipts"), set(), {}).decisions[0]

    assert decision.action == "preserve"
    assert decision.reason == "ambiguous-lease-owner"
    assert decision.blocking_marker == document


def test_modified_packaged_documentation_is_still_unknown_owner(tmp_path: Path) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "modified_document", toolchain=True)
    document, _record = _recorded_package_file(candidate, "ansiblelint/rules/no_same_owner.md", b"# Original\n")
    document.write_bytes(b"# Replaced documentation\n")
    os.utime(document, (OLD, OLD))

    decision = plan_cleanup(_config(root, tmp_path / "receipts"), set(), {}).decisions[0]

    assert decision.action == "preserve"
    assert decision.reason == "ambiguous-lease-owner"


@pytest.mark.parametrize(
    "invalid_record",
    ("missing", "hashless", "wrong-hash", "wrong-size", "duplicate"),
)
def test_unverified_installed_module_retains_unknown_owner_refusal(
    tmp_path: Path,
    invalid_record: str,
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "unverified", toolchain=True)
    module, record = _recorded_package_file(candidate)
    original = record.read_text(encoding="utf-8")
    if invalid_record == "missing":
        record.unlink()
    elif invalid_record == "hashless":
        record.write_text("virtualenv/util/lock.py,,\n", encoding="utf-8")
    elif invalid_record == "wrong-hash":
        module.write_bytes(b"import threading\n\nclass AlteredLock:\n    pass\n")
    elif invalid_record == "wrong-size":
        filename, digest, _size = original.splitlines()[0].split(",")
        record.write_text(f"{filename},{digest},1\n", encoding="utf-8")
    else:
        record.write_text(original + original.splitlines()[0] + "\n", encoding="utf-8")

    decision = plan_cleanup(_config(root, tmp_path / "receipts"), set(), {}).decisions[0]
    assert decision.action == "preserve"
    assert decision.reason == "ambiguous-lease-owner"
    assert decision.blocking_marker == module


@pytest.mark.parametrize(
    "payload",
    (
        b"321\n",
        b"pid=321\n",
        b'{"pid": 321, "pid_started_at": "live-start"}\n',
        b'{"unknown_owner": 321}\n',
        b"opaque-owner-record-secret\n",
    ),
)
def test_recorded_python_named_owner_payload_is_not_exempted_as_source(
    tmp_path: Path,
    payload: bytes,
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "owner-record", toolchain=True)
    marker, _record = _recorded_package_file(candidate, content=payload)

    decision = plan_cleanup(
        _config(root, tmp_path / "receipts"),
        set(),
        {321: ProcessIdentity(321, "live-start")},
    ).decisions[0]

    assert decision.action == "preserve"
    assert decision.reason in {"live-lease-owner", "ambiguous-lease-owner"}
    assert decision.blocking_marker == marker


@pytest.mark.parametrize(
    "marker_name",
    ("worker.pid", "worker.owner", "worker.lease", "gate-session-owner.json"),
)
def test_installed_source_identity_never_exempts_adjacent_runtime_owner_records(
    tmp_path: Path,
    marker_name: str,
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "adjacent-owner", toolchain=True)
    module, _record = _recorded_package_file(candidate)
    marker = module.parent / marker_name
    marker.write_text(
        json.dumps({"pid": 321, "pid_started_at": "live-start"}),
        encoding="utf-8",
    )

    decision = plan_cleanup(
        _config(root, tmp_path / "receipts"),
        set(),
        {321: ProcessIdentity(321, "live-start")},
    ).decisions[0]

    assert decision.action == "preserve"
    assert decision.reason == "live-lease-owner"
    assert decision.blocking_marker == marker


@pytest.mark.parametrize("state", ("held", "unavailable"))
def test_installed_source_identity_never_exempts_a_kernel_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: Literal["held", "unavailable"],
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "adjacent-lock", toolchain=True)
    module, _record = _recorded_package_file(candidate)
    marker = module.parent / "worker.lock"
    marker.write_text("pid=321\nacquired_unix_ns=1\n", encoding="utf-8")
    monkeypatch.setattr(cleanup, "inspect_lease", lambda _path, **_options: LeaseInspection(state, None))

    decision = plan_cleanup(_config(root, tmp_path / "receipts"), set(), {}).decisions[0]
    assert decision.action == "preserve"
    assert decision.reason == f"lease-{state}"
    assert decision.blocking_marker == marker


def _cached_package_file(
    candidate: Path,
    invalidation_mode: py_compile.PycInvalidationMode = py_compile.PycInvalidationMode.TIMESTAMP,
    optimize: int = 0,
) -> tuple[Path, Path, Path]:
    source = b'"""Generated cache fixture."""\nimport threading\nassert threading is not None\n'
    module, record = _recorded_package_file(candidate, "filelock/_lease.py", source)
    cache = Path(cache_from_source(str(module), optimization="" if optimize == 0 else str(optimize)))
    py_compile.compile(
        str(module),
        cfile=str(cache),
        doraise=True,
        invalidation_mode=invalidation_mode,
        optimize=optimize,
    )
    for path in (candidate, *candidate.rglob("*")):
        os.utime(path, (OLD, OLD))
    return module, record, cache


@pytest.mark.parametrize("invalidation_mode", tuple(py_compile.PycInvalidationMode))
@pytest.mark.parametrize("optimize", (0, 1, 2))
def test_verified_generated_bytecode_is_not_a_runtime_lease_marker(
    tmp_path: Path,
    invalidation_mode: py_compile.PycInvalidationMode,
    optimize: int,
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "generated-cache", toolchain=True)
    module, record, cache = _cached_package_file(candidate, invalidation_mode, optimize)
    original = {path: path.read_bytes() for path in (module, record, cache)}
    receipt = tmp_path / "receipts"

    decision = plan_cleanup(_config(root, receipt), set(), {}).decisions[0]

    assert decision.action == "reclaim"
    assert decision.reason == "stale-and-proven-inactive"
    assert decision.blocking_marker is None
    assert all(path.read_bytes() == content for path, content in original.items())
    assert not receipt.exists()


@pytest.mark.parametrize(
    "damage",
    (
        "truncated",
        "header-only",
        "foreign-magic",
        "unknown-flags",
        "invalid-flags",
        "wrong-timestamp",
        "wrong-size",
        "wrong-source-hash",
        "opaque-payload",
        "unrelated-code",
        "filename-drift",
        "foreign-tag",
        "unsupported-opt",
        "noncanonical-path",
    ),
)
def test_unverified_bytecode_retains_unknown_owner_refusal(
    tmp_path: Path,
    damage: str,
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "unverified-cache", toolchain=True)
    module, _record, cache = _cached_package_file(candidate)
    content = bytearray(cache.read_bytes())
    if damage == "truncated":
        content = content[:12]
    elif damage == "header-only":
        content = content[:16]
    elif damage == "foreign-magic":
        content[:4] = b"\x00\x00\x00\x00"
    elif damage in {"unknown-flags", "invalid-flags"}:
        content[4:8] = (4 if damage == "unknown-flags" else 2).to_bytes(4, "little")
    elif damage == "wrong-timestamp":
        content[8:12] = (int(OLD) + 1).to_bytes(4, "little")
    elif damage == "wrong-size":
        content[12:16] = (1).to_bytes(4, "little")
    elif damage == "wrong-source-hash":
        content[4:8] = (3).to_bytes(4, "little")
        content[8:16] = b"bad-hash"
    elif damage == "opaque-payload":
        content[16:] = b"opaque-owner-record-secret"
    elif damage == "unrelated-code":
        content[16:] = marshal.dumps(compile("import os\n", str(module), "exec"))
    elif damage == "filename-drift":
        py_compile.compile(str(module), cfile=str(cache), dfile="foreign-build-source.py", doraise=True)
        content = bytearray(cache.read_bytes())
    elif damage == "foreign-tag":
        renamed = cache.with_name("_lease.cpython-foreign.pyc")
        cache.rename(renamed)
        cache = renamed
    elif damage == "unsupported-opt":
        renamed = cache.with_name(cache.name.removesuffix(".pyc") + ".opt-9.pyc")
        cache.rename(renamed)
        cache = renamed
    else:
        renamed = module.with_suffix(".pyc")
        cache.rename(renamed)
        cache = renamed
    cache.write_bytes(content)

    decision = plan_cleanup(_config(root, tmp_path / "receipts"), set(), {}).decisions[0]
    assert decision.action == "preserve"
    assert decision.reason == "ambiguous-lease-owner"
    assert decision.blocking_marker == cache


@pytest.mark.parametrize("missing_proof", ("source", "record", "modified-source"))
def test_generated_cache_requires_independent_source_record_proof(
    tmp_path: Path,
    missing_proof: str,
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "orphan-cache", toolchain=True)
    module, record, cache = _cached_package_file(candidate)
    if missing_proof == "source":
        module.unlink()
    elif missing_proof == "record":
        record.unlink()
    else:
        module.write_bytes(b"import threading\n")

    assert cleanup._owner_reason(cache, {}, None, candidate) == "ambiguous-lease-owner"


def test_cache_named_runtime_owner_record_keeps_owner_precedence(tmp_path: Path) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "cache-owner", toolchain=True)
    _module, _record, cache = _cached_package_file(candidate)
    cache.write_text(json.dumps({"pid": 321, "pid_started_at": "live-start"}), encoding="utf-8")

    decision = plan_cleanup(
        _config(root, tmp_path / "receipts"),
        set(),
        {321: ProcessIdentity(321, "live-start")},
    ).decisions[0]

    assert decision.action == "preserve"
    assert decision.reason == "live-lease-owner"
    assert decision.blocking_marker == cache


def test_verified_cache_classification_never_deserializes_or_executes_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "inert-cache", toolchain=True)
    trigger = tmp_path / "must-not-be-created"
    source = (f"from pathlib import Path\nPath({str(trigger)!r}).write_text('executed')\n").encode()
    module, _record = _recorded_package_file(candidate, "filelock/_lease.py", source)
    cache = Path(cache_from_source(str(module), optimization=""))
    py_compile.compile(str(module), cfile=str(cache), doraise=True, optimize=0)

    def forbidden_loads(*_args: object, **_kwargs: object) -> None:
        pytest.fail("cache payload must never be deserialized")

    monkeypatch.setattr(marshal, "loads", forbidden_loads)
    decision = plan_cleanup(_config(root, tmp_path / "receipts"), set(), {}).decisions[0]
    assert decision.action == "reclaim"
    assert not trigger.exists()


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
        lambda _path, **_options: LeaseInspection("available", LeaseRecord(555, 1)),
    )
    plan = plan_cleanup(
        _config(root, tmp_path / "receipts"),
        set(),
        {555: ProcessIdentity(555, "live-start")},
    )
    assert plan.decisions[0].reason == "ambiguous-lease-owner"
    assert plan.decisions[0].blocking_marker == lock

    monkeypatch.setattr(
        cleanup,
        "inspect_lease",
        lambda _path, **_options: LeaseInspection("available", None),
    )
    inactive = plan_cleanup(_config(root, tmp_path / "receipts"), set(), {}).decisions[0]
    assert inactive.action == "reclaim"
    assert inactive.blocking_marker is None


def test_first_nested_blocking_marker_is_reported_without_rescanning_or_mutating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    base = _namespace(root, "thread")
    toolchain = _namespace(root, "thread", toolchain=True)
    environment = toolchain / "ci-shards/python-3.11"
    environment.mkdir(parents=True)
    (environment / ".lock").write_text("")
    payload_directory = environment / "lib/fixture_package"
    payload_directory.mkdir(parents=True)
    first_marker = payload_directory / "a.owner"
    later_marker = payload_directory / "z.owner"
    for marker in (first_marker, later_marker):
        marker.write_text("opaque-marker-body-secret", encoding="utf-8")
    inspected: list[Path] = []
    original_scan = cleanup._scan_candidate

    def scan_once(
        path: Path,
        config: CleanupConfig,
        processes: Mapping[int, ProcessIdentity],
    ) -> cleanup._Scan:
        inspected.append(path)
        return original_scan(path, config, processes)

    monkeypatch.setattr(cleanup, "_scan_candidate", scan_once)
    receipt = tmp_path / "receipt.jsonl"
    config = _config(root, receipt)
    plan = plan_cleanup(config, set(), {})
    decision = plan.decisions[0]

    assert decision.action == "preserve"
    assert decision.reason == "ambiguous-lease-owner"
    assert decision.paths == (base, toolchain)
    assert decision.fingerprints == ()
    assert decision.blocking_marker == first_marker
    assert inspected == [base, toolchain]
    assert apply_cleanup(plan, config, set(), {}).deleted == ()
    assert inspected == [base, toolchain]
    assert first_marker.read_text(encoding="utf-8") == "opaque-marker-body-secret"
    assert later_marker.read_text(encoding="utf-8") == "opaque-marker-body-secret"
    assert not receipt.exists()


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
    config = CleanupConfig(**{**_config(root, tmp_path / "receipts").__dict__, "max_entries_per_namespace": 1})

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
        lambda *_args, **_kwargs: SimpleNamespace(stdout=("1 Thu Oct  8 00:00:00 2026\n42 Thu Oct  8 01:02:03 2026\n")),
    )
    assert cleanup._process_identities() == {42: ProcessIdentity(42, "Thu Oct 8 01:02:03 2026")}
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


def test_git_and_registry_worktree_inventories_are_strict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    linked = tmp_path / "linked"
    missing = tmp_path / "registered-but-missing"
    project.mkdir()
    linked.mkdir()
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=(f"worktree {project}\nHEAD abc\n\nworktree {linked}\n\nworktree {missing}\n")
        ),
    )
    assert cleanup._git_worktrees(project) == (project, linked, missing)
    assert project_namespace(missing) in protected_namespace_names(project, (missing,))
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(stdout=""))
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
                "workstreams": {"agent/demo": {"branch": "wrong", "status": "active"}},
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


@pytest.mark.parametrize("marker_name", ["worker.owner", "owner\nrecord.pid"])
def test_read_only_json_reports_exact_blocking_marker_without_owner_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    marker_name: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "obsolete", toolchain=True)
    marker_directory = candidate / "ci-shards/python-3.11/lib/fixture_package"
    marker_directory.mkdir(parents=True)
    marker = marker_directory / marker_name
    marker.write_text("opaque-marker-body-secret", encoding="utf-8")
    stale = _namespace(root, "stale")
    protected = root / project_namespace(project)
    protected.mkdir()
    (protected / "worker.pid").write_text("opaque-protected-owner-secret", encoding="utf-8")
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
            "--max-candidates",
            "50",
            "--max-entries",
            "100",
            "--heartbeat-seconds",
            "1",
            "--validate-only",
        ]
    )
    output = capsys.readouterr().out
    rows = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    by_namespace = {row["namespace"]: row for row in rows}
    refused = by_namespace["obsolete-0123456789ab"]

    assert result == 0
    assert refused["action"] == "preserve"
    assert refused["reason"] == "ambiguous-lease-owner"
    assert refused["paths"] == [str(candidate)]
    assert refused["blocking_marker"] == str(marker)
    assert by_namespace[stale.name]["action"] == "reclaim"
    assert by_namespace[stale.name]["blocking_marker"] is None
    assert by_namespace[protected.name]["reason"] == "protected-worktree-namespace"
    assert by_namespace[protected.name]["blocking_marker"] is None
    assert "opaque-marker-body-secret" not in output
    assert "opaque-protected-owner-secret" not in output
    assert "deleted=0" in output
    assert candidate.exists() and stale.exists() and protected.exists()
    assert marker.read_text(encoding="utf-8") == "opaque-marker-body-secret"
    assert not receipt.exists()


def test_main_apply_deletes_stale_namespace_with_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_ordinary_descendant_symlink_unlinks_without_following_target(tmp_path: Path) -> None:
    """Interior links are entries; a protected target's data is not cleanup input."""
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "unlink", toolchain=True)
    protected = _namespace(root, "serving")
    sentinel = protected / "payload.bin"
    os.utime(sentinel, (NOW, NOW))
    before = sentinel.read_bytes(), sentinel.stat()
    links = {
        "outside-file": sentinel,
        "outside-directory": protected,
        "dangling": tmp_path / "never-created",
        "loop": candidate / "loop",
    }
    for name, target in links.items():
        link = candidate / name
        link.symlink_to(target)
        os.utime(link, (OLD, OLD), follow_symlinks=False)
    os.utime(candidate, (OLD, OLD))
    receipt = tmp_path / "receipts.jsonl"
    config = _config(root, receipt, validate_only=False)

    plan = plan_cleanup(config, {protected.name}, {})
    decisions = {item.base_name: item for item in plan.decisions}
    assert decisions[candidate.name.removesuffix("-toolchain")].action == "reclaim"
    assert decisions[protected.name].reason == "protected-worktree-namespace"
    assert apply_cleanup(plan, config, {protected.name}, {}).deleted == (candidate,)
    assert not candidate.exists()
    assert sentinel.read_bytes() == before[0]
    assert sentinel.stat() == before[1]
    assert protected.is_dir()
    assert not links["dangling"].exists()
    records = [json.loads(line) for line in receipt.read_text().splitlines()]
    assert [item["path"] for item in records] == [str(candidate)]
    assert records[0]["size_bytes"] < 4096


@pytest.mark.parametrize(
    "name", ("worker.pid", "collection.lock", "worker.owner", "lease-data", "lock.py", "_lease.pyc"),
)
@pytest.mark.parametrize("target_kind", ("live", "dangling", "loop"))
def test_marker_symlink_refuses_pair_without_reading_target(
    tmp_path: Path, name: str, target_kind: str,
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    base = _namespace(root, "marked")
    toolchain = _namespace(root, "marked", toolchain=True)
    target = tmp_path / "outside-owner"
    target.write_text('{"pid":321,"pid_started_at":"live-start"}')
    before = target.read_bytes(), target.stat()
    marker = toolchain / name
    link_target = target if target_kind == "live" else tmp_path / "missing" if target_kind == "dangling" else marker
    marker.symlink_to(link_target)
    os.utime(marker, (OLD, OLD), follow_symlinks=False)
    os.utime(toolchain, (OLD, OLD))
    config = _config(root, tmp_path / "receipts", validate_only=False)

    decision = plan_cleanup(config, set(), {321: ProcessIdentity(321, "live-start")}).decisions[0]
    assert decision.reason == "marker-symlink"
    assert decision.blocking_marker == marker
    assert decision.paths == (base, toolchain)
    assert decision.fingerprints == ()
    assert apply_cleanup(plan_cleanup(config, set(), {}), config, set(), {}).deleted == ()
    assert base.exists() and toolchain.exists() and marker.is_symlink()
    assert target.read_bytes() == before[0] and target.stat() == before[1]
    assert not config.receipt_path.exists()


@pytest.mark.parametrize("change", ("text", "file", "directory"))
def test_changed_ordinary_link_refuses_fresh_apply(tmp_path: Path, change: str) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "changed")
    link = candidate / "ordinary"
    link.symlink_to("first")
    os.utime(link, (OLD, OLD), follow_symlinks=False)
    os.utime(candidate, (OLD, OLD))
    config = _config(root, tmp_path / "receipts", validate_only=False)
    plan = plan_cleanup(config, set(), {})
    assert plan.decisions[0].action == "reclaim"
    link.unlink()
    if change == "text":
        link.symlink_to("other")
    elif change == "file":
        link.write_bytes(b"first")
    else:
        link.mkdir()
    os.utime(link, (OLD, OLD), follow_symlinks=False)
    os.utime(candidate, (OLD, OLD))

    result = apply_cleanup(plan, config, set(), {})
    assert result.deleted == () and result.refused == (candidate,)
    assert candidate.is_dir()
    assert not config.receipt_path.exists()


def test_fresh_link_age_uses_entry_not_target(tmp_path: Path) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "fresh-link")
    outside = tmp_path / "old-target"
    outside.write_bytes(b"outside")
    os.utime(outside, (OLD, OLD))
    link = candidate / "ordinary"
    link.symlink_to(outside)
    os.utime(link, (NOW, NOW), follow_symlinks=False)
    os.utime(candidate, (OLD, OLD))

    decision = plan_cleanup(_config(root, tmp_path / "receipts"), set(), {}).decisions[0]
    assert decision.reason == "within-grace-period"
    assert outside.stat().st_mtime == OLD
    assert link.is_symlink()


def test_directory_to_link_scan_race_never_traverses_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "directory-race")
    directory = candidate / "nested"
    directory.mkdir()
    (directory / "original").write_bytes(b"owned")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_bytes(b"untouched")
    original_stat = os.stat
    original_listdir = os.listdir
    changed = False
    traversed: list[object] = []

    def replace_after_stat(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes] | int,
        *, dir_fd: int | None = None, follow_symlinks: bool = True,
    ) -> os.stat_result:
        nonlocal changed
        metadata = original_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        if not changed and Path(str(path)).name == "nested" and follow_symlinks is False:
            changed = True
            directory.rename(candidate / "saved")
            directory.symlink_to(outside, target_is_directory=True)
        return metadata

    def guarded_listdir(path: str | os.PathLike[str] | int = ".") -> list[str]:
        if str(path) == str(directory):
            traversed.append(path)
        return original_listdir(path)

    with monkeypatch.context() as patch:
        patch.setattr(os, "stat", replace_after_stat)
        patch.setattr(os, "listdir", guarded_listdir)
        decision = plan_cleanup(_config(root, tmp_path / "receipts"), set(), {}).decisions[0]
    assert changed, "the real no-follow metadata boundary must be exercised"
    assert traversed == []
    assert decision.action == "preserve" and decision.fingerprints == ()
    assert (outside / "sentinel").read_bytes() == b"untouched"


def test_marker_leaf_swap_never_opens_link_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "marker-race")
    marker = candidate / "worker.pid"
    marker.write_text("321")
    outside = tmp_path / "outside"
    outside.write_text('{"pid":321,"pid_started_at":"live-start"}')
    original_stat = os.stat
    original_open = Path.open
    changed = False
    followed: list[Path] = []

    def replace_after_stat(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes] | int,
        *, dir_fd: int | None = None, follow_symlinks: bool = True,
    ) -> os.stat_result:
        nonlocal changed
        metadata = original_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        if not changed and Path(str(path)).name == marker.name and follow_symlinks is False:
            changed = True
            marker.unlink()
            marker.symlink_to(outside)
        return metadata

    def guarded_open(
        path: Path, mode: str = "r", buffering: int = -1, encoding: str | None = None,
        errors: str | None = None, newline: str | None = None,
    ) -> IO[str] | IO[bytes]:
        if path == marker and changed:
            followed.append(path)
        return original_open(path, mode, buffering, encoding, errors, newline)

    with monkeypatch.context() as patch:
        patch.setattr(os, "stat", replace_after_stat)
        patch.setattr(Path, "open", guarded_open)
        decision = plan_cleanup(_config(root, tmp_path / "receipts"), set(), {}).decisions[0]
    assert changed
    assert followed == []
    assert decision.action == "preserve"
    assert outside.read_text() == '{"pid":321,"pid_started_at":"live-start"}'


@pytest.mark.parametrize("failure", ("none", "readlink", "read", "scandir", "overflow", "unsupported"))
def test_cleanup_scan_descriptors_close_on_every_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "close")
    link = candidate / "ordinary"
    link.symlink_to("missing")
    os.utime(link, (OLD, OLD), follow_symlinks=False)
    os.utime(candidate, (OLD, OLD))
    opened: list[int] = []
    remaining: set[int] = set()
    original_open = os.open
    original_close = os.close

    def tracked_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes], flags: int, mode: int = 0o777,
        *, dir_fd: int | None = None,
    ) -> int:
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        opened.append(descriptor)
        remaining.add(descriptor)
        return descriptor

    def tracked_close(descriptor: int) -> None:
        remaining.discard(descriptor)
        original_close(descriptor)

    def unavailable_readlink(*_args: object, **_kwargs: object) -> str:
        raise OSError("fixture readlink unavailable")

    def unavailable_read(*_args: object, **_kwargs: object) -> Never:
        raise OSError("fixture read unavailable")

    if failure == "read":
        (candidate / "worker.pid").write_text("321")
    config = _config(root, tmp_path / "receipts")
    if failure == "overflow":
        config = CleanupConfig(**{**config.__dict__, "max_entries_per_namespace": 1})
    with monkeypatch.context() as patch:
        patch.setattr(os, "open", tracked_open)
        patch.setattr(os, "close", tracked_close)
        if failure == "readlink":
            patch.setattr(os, "readlink", unavailable_readlink)
        if failure == "read":
            patch.setattr(os, "read", unavailable_read)
        if failure == "scandir":
            patch.setattr(os, "scandir", unavailable_read)
        if failure == "unsupported":
            patch.delattr(os, "O_NOFOLLOW")
        decision = plan_cleanup(config, set(), {}).decisions[0]
    assert remaining == set()
    if failure == "unsupported":
        assert decision.action == "preserve"
    else:
        assert opened, "the scanner must use its native descriptor boundary"
        assert (decision.action == "reclaim") is (failure == "none")
    assert link.is_symlink() and not config.receipt_path.exists()


def test_candidate_metadata_change_refuses_apply(tmp_path: Path) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "metadata")
    config = _config(root, tmp_path / "receipts", validate_only=False)
    plan = plan_cleanup(config, set(), {})
    candidate.chmod(candidate.stat().st_mode ^ 0o010)
    os.utime(candidate, (OLD, OLD))
    result = apply_cleanup(plan, config, set(), {})
    assert result.deleted == () and result.refused == (candidate,)
    assert candidate.is_dir() and not config.receipt_path.exists()


def test_scanner_never_stats_or_opens_ordinary_link_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "no-follow")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_bytes(b"outside")
    link = candidate / "ordinary"
    link.symlink_to(outside, target_is_directory=True)
    os.utime(link, (OLD, OLD), follow_symlinks=False)
    os.utime(candidate, (OLD, OLD))
    original_stat, original_open = os.stat, os.open
    nofollow_stamps: list[str] = []

    def guard_stat(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes] | int,
        *, dir_fd: int | None = None, follow_symlinks: bool = True,
    ) -> os.stat_result:
        if not isinstance(path, int):
            name = os.fsdecode(path)
            assert name != str(outside), "a target is not namespace inventory"
            if Path(name).name == link.name:
                assert follow_symlinks is False
                nofollow_stamps.append(name)
        return original_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    def guard_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes], flags: int, mode: int = 0o777,
        *, dir_fd: int | None = None,
    ) -> int:
        assert os.fsdecode(path) not in {link.name, str(link), str(outside)}
        return original_open(path, flags, mode, dir_fd=dir_fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "stat", guard_stat)
        patch.setattr(os, "open", guard_open)
        decision = plan_cleanup(_config(root, tmp_path / "receipts"), set(), {}).decisions[0]
    assert decision.action == "reclaim"
    assert nofollow_stamps
    assert (outside / "sentinel").read_bytes() == b"outside"


@pytest.mark.parametrize("damage", ("foreign", "hardlink"))
def test_ordinary_link_object_requires_owned_single_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str,
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "link-ownership")
    link = candidate / "ordinary"
    link.symlink_to("missing")
    original_stat = os.stat

    def changed_stat(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes] | int,
        *, dir_fd: int | None = None, follow_symlinks: bool = True,
    ) -> os.stat_result:
        result = original_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        if not isinstance(path, int) and Path(os.fsdecode(path)).name == link.name:
            values = list(result)
            values[4 if damage == "foreign" else 3] += 1
            return os.stat_result(values)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(os, "stat", changed_stat)
        decision = plan_cleanup(_config(root, tmp_path / "receipts"), set(), {}).decisions[0]
    assert decision.reason == ("foreign-owner" if damage == "foreign" else "descendant-hardlink")
    assert link.is_symlink()


def test_record_metadata_ancestor_swap_never_reads_outside_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "record-race", toolchain=True)
    _module, record = _recorded_package_file(candidate)
    metadata = record.parent
    outside = tmp_path / "outside-metadata"
    outside.mkdir()
    original_record = record.read_bytes()
    (outside / "RECORD").write_bytes(original_record)
    original_stat, original_open = os.stat, os.open
    changed = False
    outside_opens: list[str] = []

    def replace_metadata(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes] | int,
        *, dir_fd: int | None = None, follow_symlinks: bool = True,
    ) -> os.stat_result:
        nonlocal changed
        result = original_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        if not changed and not isinstance(path, int) and Path(os.fsdecode(path)).name == metadata.name:
            changed = True
            metadata.rename(metadata.with_name("saved-metadata"))
            metadata.symlink_to(outside, target_is_directory=True)
        return result

    def guard_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes], flags: int, mode: int = 0o777,
        *, dir_fd: int | None = None,
    ) -> int:
        if os.fsdecode(path) == str(outside):
            outside_opens.append(str(path))
        if changed and Path(os.fsdecode(path)).name == metadata.name:
            assert flags & os.O_NOFOLLOW
        return original_open(path, flags, mode, dir_fd=dir_fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "stat", replace_metadata)
        patch.setattr(os, "open", guard_open)
        decision = plan_cleanup(_config(root, tmp_path / "receipts"), set(), {}).decisions[0]
    assert changed
    assert outside_opens == []
    assert decision.action == "preserve"
    assert (outside / "RECORD").read_bytes() == original_record


def test_link_replacement_during_readlink_refuses_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "readlink-race")
    link = candidate / "ordinary"
    link.symlink_to("first")
    original_readlink = os.readlink
    changed = False

    def replace_link(
        path: str | os.PathLike[str], *, dir_fd: int | None = None,
    ) -> str:
        nonlocal changed
        target = original_readlink(path, dir_fd=dir_fd)
        if not changed:
            changed = True
            link.unlink()
            link.symlink_to("other")
        return target

    with monkeypatch.context() as patch:
        patch.setattr(os, "readlink", replace_link)
        decision = plan_cleanup(_config(root, tmp_path / "receipts"), set(), {}).decisions[0]
    assert changed
    assert decision.action == "preserve" and decision.fingerprints == ()
    assert link.readlink() == Path("other")


def test_native_deleter_late_directory_link_swap_preserves_outside_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "gludd-resources"
    root.mkdir()
    candidate = _namespace(root, "delete-race")
    directory = candidate / "nested"
    directory.mkdir()
    (directory / "owned").write_bytes(b"owned")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_bytes(b"outside")
    for path in (directory / "owned", directory, candidate):
        os.utime(path, (OLD, OLD))
    config = _config(root, tmp_path / "receipts", validate_only=False)
    plan = plan_cleanup(config, set(), {})
    assert plan.decisions[0].action == "reclaim"
    original_delete = cleanup._delete_with_heartbeats
    original_open = os.open
    changed = False

    def replace_before_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes], flags: int, mode: int = 0o777,
        *, dir_fd: int | None = None,
    ) -> int:
        nonlocal changed
        if not changed and os.fsdecode(path) == directory.name:
            changed = True
            directory.rename(candidate / "saved")
            directory.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    def inject_late_swap(path: Path, heartbeat_seconds: float) -> None:
        with monkeypatch.context() as patch:
            patch.setattr(os, "open", replace_before_open)
            original_delete(path, heartbeat_seconds)

    monkeypatch.setattr(cleanup, "_delete_with_heartbeats", inject_late_swap)
    with pytest.raises(OSError):
        apply_cleanup(plan, config, set(), {})
    assert changed
    assert (outside / "sentinel").read_bytes() == b"outside"
    assert candidate.is_dir() and directory.is_symlink()
    assert not config.receipt_path.exists()
