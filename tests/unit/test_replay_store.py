"""Atomic, integrity-checked storage contracts for v1 replay bundles."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_KEY_ID = "replay-integrity-v1"
_KEY = b"replay-store-test-key-material-32b"


def _event_draft(index: int = 0) -> dict[str, object]:
    return {
        "event_id": f"event-{index}",
        "occurred_at": "2026-10-05T12:00:00Z",
        "recorded_at": "2026-10-05T12:00:00.010Z",
        "type": "tool.responded",
        "project_id": "project-alpha",
        "correlation": {
            "todo_id": "todo-7",
            "task_id": "task-9",
            "trace_id": f"trace-{index}",
        },
        "payload": {"worker": index, "exit_code": 0},
        "redaction": {"count": 0, "kinds": []},
    }


def _manifest_payload(
    run_id: str,
    *,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
    pinned: bool = False,
    hold_reason: str | None = None,
) -> dict[str, object]:
    created = created_at or datetime(2026, 10, 5, 12, tzinfo=UTC)
    return {
        "schema": "gludd.run-bundle/v1",
        "run_id": run_id,
        "parent_run_id": None,
        "operation": "record",
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "finalized_at": (created + timedelta(seconds=3)).isoformat().replace(
            "+00:00", "Z"
        ),
        "status": "completed",
        "project_id": "project-alpha",
        "source": {
            "repository_url_sha256": "sha256:" + "1" * 64,
            "commit_sha": "2" * 40,
            "tree_sha": "3" * 40,
            "branch": "feat/replay",
            "dirty": False,
        },
        "runtime": {
            "gludd_version": "0.1.1",
            "python_version": "3.14.0",
            "os": "darwin",
            "architecture": "arm64",
            "config_sha256": "sha256:" + "4" * 64,
            "feature_flags": {"replay_v1": True},
        },
        "model": {
            "provider": "openai",
            "profile": "default",
            "model": "gpt-6",
            "request_parameters": {"temperature": 0.25},
            "provider_revision": None,
        },
        "event_count": 0,
        "events_sha256": "sha256:" + "0" * 64,
        "attachments": [],
        "completeness": {
            "expected_stages": ["run.started", "run.completed"],
            "observed_stages": ["run.started", "run.completed"],
            "recorder_errors": [],
            "missing_ranges": [],
        },
        "retention": {
            "expires_at": (
                None
                if expires_at is None
                else expires_at.isoformat().replace("+00:00", "Z")
            ),
            "pinned": pinned,
            "hold_reason": hold_reason,
        },
        "integrity": "unsigned",
        "signing_key_id": None,
    }


def _store(
    root: Path,
    *,
    active_key_id: str | None = _KEY_ID,
    lock_timeout: float = 10.0,
) -> Any:
    from general_ludd.replay.store import RunBundleStore

    return RunBundleStore(
        root,
        verification_keys={_KEY_ID: _KEY},
        active_key_id=active_key_id,
        lock_timeout=lock_timeout,
    )


def _append_in_process(root: str, run_id: str, index: int) -> None:
    store = _store(Path(root))
    store.append_event(run_id, _event_draft(index))


def _finalized_run(
    root: Path,
    run_id: str,
    *,
    index: int = 0,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
    pinned: bool = False,
    hold_reason: str | None = None,
) -> Any:
    store = _store(root)
    store.append_event(run_id, _event_draft(index))
    store.finalize(
        run_id,
        _manifest_payload(
            run_id,
            created_at=created_at,
            expires_at=expires_at,
            pinned=pinned,
            hold_reason=hold_reason,
        ),
    )
    return store


def test_signed_round_trip_verifies_event_index_and_manifest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.append_event("run-atomic", _event_draft(1))
    second = store.append_event("run-atomic", _event_draft(2))

    manifest = store.finalize("run-atomic", _manifest_payload("run-atomic"))
    verdict = store.verify("run-atomic")
    bundle = store.read_verified("run-atomic")

    assert (first.sequence, second.sequence) == (0, 1)
    assert first.digest.startswith("sha256:")
    assert manifest.event_count == 2
    assert manifest.events_sha256.startswith("sha256:")
    assert manifest.integrity == "signed"
    assert manifest.signing_key_id == _KEY_ID
    assert verdict.valid is True
    assert verdict.complete is True
    assert verdict.event_count == 2
    assert [event.sequence for event in bundle.events] == [0, 1]


def test_public_replay_package_exports_store_contract() -> None:
    import general_ludd.replay as replay

    expected = {
        "BundleVerification",
        "ReplayIntegrityError",
        "ReplayPathError",
        "ReplayStateError",
        "RetentionResult",
        "RunBundleStore",
        "VerifiedBundle",
    }
    assert expected <= set(replay.__all__)


def test_unsigned_finalization_is_explicit_and_has_no_hmac(tmp_path: Path) -> None:
    store = _store(tmp_path, active_key_id=None)
    store.append_event("run-unsigned", _event_draft())
    manifest = store.finalize("run-unsigned", _manifest_payload("run-unsigned"))

    assert manifest.integrity == "unsigned"
    assert manifest.signing_key_id is None
    assert not (store.bundle_path("run-unsigned") / "manifest.hmac").exists()
    assert store.verify("run-unsigned").valid is True


@pytest.mark.parametrize(
    "run_id",
    ["../escape", "nested/run", "run\\escape", "run..escape", "NUL", ""],
)
def test_store_rejects_unsafe_run_ids_before_path_construction(
    tmp_path: Path, run_id: str
) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="run_id"):
        store.append_event(run_id, _event_draft())


def test_store_refuses_root_run_and_events_symlinks(tmp_path: Path) -> None:
    from general_ludd.replay.store import ReplayPathError, RunBundleStore

    outside = tmp_path / "outside"
    outside.mkdir()
    root_link = tmp_path / "root-link"
    root_link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ReplayPathError, match="symlink"):
        RunBundleStore(root_link)

    store = _store(tmp_path / "root")
    runs = tmp_path / "root" / "runs-v1"
    (runs / "linked-run").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ReplayPathError, match="symlink"):
        store.append_event("linked-run", _event_draft())

    real_run = store.bundle_path("events-link")
    real_run.mkdir(parents=True)
    (real_run / "events").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ReplayPathError, match="symlink"):
        store.append_event("events-link", _event_draft())


def test_threaded_append_is_unique_and_contiguous(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        events = list(
            pool.map(
                lambda index: store.append_event("thread-run", _event_draft(index)),
                range(24),
            )
        )

    assert sorted(event.sequence for event in events) == list(range(24))
    store.finalize("thread-run", _manifest_payload("thread-run"))
    assert store.verify("thread-run").valid is True


def test_spawned_process_append_is_unique_and_contiguous(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(
            target=_append_in_process,
            args=(str(tmp_path), "process-run", index),
        )
        for index in range(6)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    store = _store(tmp_path)
    store.finalize("process-run", _manifest_payload("process-run"))
    bundle = store.read_verified("process-run")
    assert [event.sequence for event in bundle.events] == list(range(6))


def test_unfinalized_and_failed_publish_never_verify_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    store.append_event("crash-before-finalize", _event_draft())
    incomplete = store.verify("crash-before-finalize")
    assert incomplete.valid is False
    assert incomplete.complete is False
    assert incomplete.status == "incomplete"
    assert "manifest" in incomplete.errors[0]

    original_replace = os.replace

    def fail_event_publish(source: Any, destination: Any) -> None:
        if Path(os.fsdecode(destination)).parent.name == "events":
            raise OSError("injected event publish crash")
        original_replace(source, destination)

    monkeypatch.setattr("general_ludd.replay.store.os.replace", fail_event_publish)
    with pytest.raises(OSError, match="publish crash"):
        store.append_event("crash-during-event", _event_draft())
    assert store.verify("crash-during-event").complete is False


@pytest.mark.parametrize("mutation", ["corrupt", "missing", "extra", "reordered"])
def test_verifier_rejects_event_corruption_missing_extra_and_reorder(
    tmp_path: Path, mutation: str
) -> None:
    run_id = f"run-{mutation}"
    store = _store(tmp_path)
    store.append_event(run_id, _event_draft(0))
    store.append_event(run_id, _event_draft(1))
    store.finalize(run_id, _manifest_payload(run_id))
    events = store.bundle_path(run_id) / "events"
    first = events / "000000000000.json"
    second = events / "000000000001.json"

    if mutation == "corrupt":
        first.write_text("{", encoding="utf-8")
    elif mutation == "missing":
        first.unlink()
    elif mutation == "extra":
        (events / "000000000002.json").write_text(
            second.read_text(encoding="utf-8"), encoding="utf-8"
        )
    else:
        first_bytes = first.read_bytes()
        first.write_bytes(second.read_bytes())
        second.write_bytes(first_bytes)

    verdict = store.verify(run_id)
    assert verdict.valid is False
    assert verdict.complete is False
    assert verdict.errors


@given(st.binary(min_size=1, max_size=128))
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_arbitrary_event_corruption_never_verifies(
    tmp_path: Path, corrupt_bytes: bytes
) -> None:
    run_id = "corrupt-" + hashlib.sha256(corrupt_bytes).hexdigest()[:24]
    root = tmp_path / run_id
    store = _finalized_run(root, run_id)
    event_path = store.bundle_path(run_id) / "events" / "000000000000.json"
    event_path.write_bytes(corrupt_bytes)
    assert store.verify(run_id).valid is False


@pytest.mark.parametrize("filename", ["manifest.json", "manifest.hmac"])
def test_verifier_rejects_manifest_or_hmac_tampering(
    tmp_path: Path, filename: str
) -> None:
    store = _finalized_run(tmp_path, "run-manifest-tamper")
    target = store.bundle_path("run-manifest-tamper") / filename
    if filename.endswith(".json"):
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["project_id"] = "tampered"
        target.write_text(json.dumps(payload), encoding="utf-8")
    else:
        target.write_text("0" * 64, encoding="utf-8")

    verdict = store.verify("run-manifest-tamper")
    assert verdict.valid is False
    assert any("HMAC" in error for error in verdict.errors)


def test_signing_key_rotation_reads_old_bundles_and_rejects_revoked_keys(
    tmp_path: Path,
) -> None:
    from general_ludd.replay.store import RunBundleStore

    old_key = b"old-replay-key-material-32-bytes!"
    new_key = b"new-replay-key-material-32-bytes!"
    old = RunBundleStore(
        tmp_path,
        verification_keys={"old-key": old_key},
        active_key_id="old-key",
    )
    old.append_event("old-run", _event_draft())
    old.finalize("old-run", _manifest_payload("old-run"))

    rotated = RunBundleStore(
        tmp_path,
        verification_keys={"old-key": old_key, "new-key": new_key},
        active_key_id="new-key",
    )
    rotated.append_event("new-run", _event_draft(1))
    rotated.finalize("new-run", _manifest_payload("new-run"))
    assert rotated.verify("old-run").valid is True
    assert rotated.verify("new-run").valid is True

    revoked = RunBundleStore(
        tmp_path,
        verification_keys={"new-key": new_key},
        active_key_id="new-key",
    )
    verdict = revoked.verify("old-run")
    assert verdict.valid is False
    assert any("old-key" in error for error in verdict.errors)


def test_append_rejects_reserved_fields_and_finalized_runs(tmp_path: Path) -> None:
    from general_ludd.replay.store import ReplayStateError

    store = _store(tmp_path)
    with pytest.raises(ValueError, match="store-managed"):
        store.append_event("run-fields", {**_event_draft(), "sequence": 99})
    store.append_event("run-fields", _event_draft())
    store.finalize("run-fields", _manifest_payload("run-fields"))
    with pytest.raises(ReplayStateError, match="finalized"):
        store.append_event("run-fields", _event_draft(2))
    with pytest.raises(ReplayStateError, match="finalized"):
        store.finalize("run-fields", _manifest_payload("run-fields"))


def test_retention_is_bounded_oldest_first_and_respects_pin_hold_and_lock(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 10, 10, tzinfo=UTC)
    old = now - timedelta(days=20)
    expired = now - timedelta(days=1)
    store = _finalized_run(
        tmp_path, "delete-oldest", created_at=old, expires_at=expired
    )
    _finalized_run(
        tmp_path,
        "keep-pinned",
        index=1,
        created_at=old + timedelta(days=1),
        expires_at=expired,
        pinned=True,
    )
    _finalized_run(
        tmp_path,
        "keep-held",
        index=2,
        created_at=old + timedelta(days=2),
        expires_at=expired,
        hold_reason="legal",
    )
    _finalized_run(
        tmp_path,
        "delete-second",
        index=3,
        created_at=old + timedelta(days=3),
        expires_at=expired,
    )
    _store(tmp_path).append_event("keep-incomplete", _event_draft(4))

    with store.run_lock("delete-oldest"):
        locked = store.enforce_retention(
            now=now,
            max_total_bytes=0,
            max_deletions=10,
            scan_limit=20,
        )
    assert "delete-oldest" in locked.skipped_locked
    assert locked.deleted == ("delete-second",)

    bounded = store.enforce_retention(
        now=now,
        max_total_bytes=0,
        max_deletions=1,
        scan_limit=20,
    )
    assert bounded.deleted == ("delete-oldest",)
    assert store.bundle_path("keep-pinned").exists()
    assert store.bundle_path("keep-held").exists()
    assert store.bundle_path("keep-incomplete").exists()
    assert bounded.quota_satisfied is False


def test_retention_dry_run_and_bounds_do_not_mutate(tmp_path: Path) -> None:
    now = datetime(2026, 10, 10, tzinfo=UTC)
    store = _finalized_run(
        tmp_path,
        "dry-run",
        created_at=now - timedelta(days=2),
        expires_at=now - timedelta(days=1),
    )
    result = store.enforce_retention(
        now=now,
        max_total_bytes=0,
        max_deletions=1,
        scan_limit=1,
        dry_run=True,
    )
    assert result.planned == ("dry-run",)
    assert result.deleted == ()
    assert store.bundle_path("dry-run").exists()

    for kwargs in (
        {"max_total_bytes": -1, "max_deletions": 1, "scan_limit": 1},
        {"max_total_bytes": 0, "max_deletions": 0, "scan_limit": 1},
        {"max_total_bytes": 0, "max_deletions": 1, "scan_limit": 0},
    ):
        with pytest.raises(ValueError):
            store.enforce_retention(now=now, **kwargs)


def test_constructor_and_internal_directory_validation_fail_closed(
    tmp_path: Path,
) -> None:
    from general_ludd.replay.store import ReplayPathError, RunBundleStore

    root_file = tmp_path / "root-file"
    root_file.write_text("not-a-directory", encoding="utf-8")
    with pytest.raises(ReplayPathError, match="directory"):
        RunBundleStore(root_file)
    with pytest.raises(ValueError, match="lock_timeout"):
        RunBundleStore(tmp_path / "bad-timeout", lock_timeout=0)
    with pytest.raises(ValueError, match="at least 16"):
        RunBundleStore(tmp_path / "short-key", verification_keys={"key": b"short"})
    with pytest.raises(ValueError, match="active_key_id"):
        RunBundleStore(
            tmp_path / "missing-active",
            verification_keys={"key": b"x" * 32},
            active_key_id="other",
        )
    with pytest.raises(ValueError, match="key ID"):
        RunBundleStore(
            tmp_path / "bad-key-id", verification_keys={"bad/key": b"x" * 32}
        )

    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / "runs-v1").write_text("blocked", encoding="utf-8")
    with pytest.raises(ReplayPathError, match="not a directory"):
        RunBundleStore(blocked)


def test_finalize_accepts_typed_manifest_and_rejects_wrong_lifecycle(
    tmp_path: Path,
) -> None:
    from general_ludd.replay.schema import parse_bundle_manifest
    from general_ludd.replay.store import ReplayStateError

    store = _store(tmp_path)
    store.append_event("typed-manifest", _event_draft())
    typed = parse_bundle_manifest(_manifest_payload("typed-manifest"))
    assert store.finalize("typed-manifest", typed).event_count == 1

    with pytest.raises(ReplayStateError, match="run_id"):
        store.finalize("right-run", _manifest_payload("wrong-run"))
    running = _manifest_payload("running-run")
    running["status"] = "running"
    running["finalized_at"] = None
    with pytest.raises(ReplayStateError, match="running"):
        store.finalize("running-run", running)


def test_stale_temporary_cleanup_and_manifest_publish_crash_are_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    events = store.bundle_path("stale-temp") / "events"
    events.mkdir(parents=True)
    stale = events / ".000000000000.json.crashed.tmp"
    stale.write_text("partial", encoding="utf-8")
    store.append_event("stale-temp", _event_draft())
    assert not stale.exists()

    invalid_temp = store.bundle_path("invalid-temp") / "events"
    invalid_temp.mkdir(parents=True)
    (invalid_temp / ".event.crashed.tmp").mkdir()
    with pytest.raises(ValueError, match="temporary"):
        store.append_event("invalid-temp", _event_draft())

    crash_store = _store(tmp_path / "manifest-crash")
    crash_store.append_event("run", _event_draft())
    original_replace = os.replace

    def fail_manifest_publish(source: Any, destination: Any) -> None:
        if Path(os.fsdecode(destination)).name == "manifest.json":
            raise OSError("injected manifest publish crash")
        original_replace(source, destination)

    monkeypatch.setattr("general_ludd.replay.store.os.replace", fail_manifest_publish)
    with pytest.raises(OSError, match="manifest publish crash"):
        crash_store.finalize("run", _manifest_payload("run"))
    verdict = crash_store.verify("run")
    assert verdict.complete is False
    assert "manifest" in verdict.errors[0]


@pytest.mark.parametrize(
    "mutation", ["noncanonical", "digest", "duplicate-id", "unexpected-entry", "events-file"]
)
def test_verifier_rejects_event_structure_failures(
    tmp_path: Path, mutation: str
) -> None:
    from general_ludd.replay.schema import canonical_replay_json

    run_id = f"event-structure-{mutation}"
    store = _store(tmp_path)
    store.append_event(run_id, _event_draft(0))
    store.append_event(run_id, _event_draft(1))
    store.finalize(run_id, _manifest_payload(run_id))
    events = store.bundle_path(run_id) / "events"
    first = events / "000000000000.json"
    second = events / "000000000001.json"

    if mutation == "noncanonical":
        first.write_text(" " + first.read_text(encoding="utf-8"), encoding="utf-8")
    elif mutation == "digest":
        payload = json.loads(first.read_text(encoding="utf-8"))
        payload["digest"] = "sha256:" + "f" * 64
        first.write_text(canonical_replay_json(payload), encoding="utf-8")
    elif mutation == "duplicate-id":
        first_payload = json.loads(first.read_text(encoding="utf-8"))
        second_payload = json.loads(second.read_text(encoding="utf-8"))
        second_payload["event_id"] = first_payload["event_id"]
        second.write_text(canonical_replay_json(second_payload), encoding="utf-8")
    elif mutation == "unexpected-entry":
        (events / "README").write_text("extra", encoding="utf-8")
    else:
        first.unlink()
        second.unlink()
        events.rmdir()
        events.write_text("not-a-directory", encoding="utf-8")

    assert store.verify(run_id).valid is False


def test_verifier_covers_attachment_digest_size_missing_extra_and_type(
    tmp_path: Path,
) -> None:
    content = b"bounded attachment"
    digest = hashlib.sha256(content).hexdigest()
    attachment = {
        "digest": f"sha256:{digest}",
        "original_bytes": len(content),
        "stored_bytes": len(content),
        "media_type": "text/plain",
        "encoding": "utf-8",
        "redaction_count": 0,
        "truncated": False,
    }

    store = _store(tmp_path)
    run_id = "attachment-valid"
    store.append_event(run_id, _event_draft())
    attachments = store.bundle_path(run_id) / "attachments"
    attachments.mkdir()
    attachment_path = attachments / f"sha256-{digest}"
    attachment_path.write_bytes(content)
    manifest = _manifest_payload(run_id)
    manifest["attachments"] = [attachment]
    store.finalize(run_id, manifest)
    assert store.verify(run_id).valid is True

    attachment_path.write_bytes(b"bad")
    mismatch = store.verify(run_id)
    assert mismatch.valid is False
    assert any("size mismatch" in error for error in mismatch.errors)
    assert any("digest mismatch" in error for error in mismatch.errors)

    missing_store = _store(tmp_path / "missing")
    missing_store.append_event("missing-attachment", _event_draft())
    missing_manifest = _manifest_payload("missing-attachment")
    missing_manifest["attachments"] = [attachment]
    missing_store.finalize("missing-attachment", missing_manifest)
    missing = missing_store.verify("missing-attachment")
    assert any("attachment missing" in error for error in missing.errors)
    assert any("directory missing" in error for error in missing.errors)

    extra_store = _finalized_run(tmp_path / "extra", "extra-attachment")
    extra_dir = extra_store.bundle_path("extra-attachment") / "attachments"
    extra_dir.mkdir()
    (extra_dir / "unexpected").write_bytes(b"extra")
    (extra_dir / "nested").mkdir()
    extra = extra_store.verify("extra-attachment")
    assert any("unexpected attachment" in error for error in extra.errors)
    assert any("unexpected attachment entry" in error for error in extra.errors)

    wrong_type_store = _finalized_run(tmp_path / "wrong-type", "wrong-type")
    wrong_type = wrong_type_store.bundle_path("wrong-type") / "attachments"
    wrong_type.write_text("file", encoding="utf-8")
    assert any(
        "not a directory" in error
        for error in wrong_type_store.verify("wrong-type").errors
    )


def test_verifier_rejects_invalid_manifest_missing_hmac_and_extra_bundle_file(
    tmp_path: Path,
) -> None:
    corrupt = _finalized_run(tmp_path / "corrupt", "corrupt-manifest")
    (corrupt.bundle_path("corrupt-manifest") / "manifest.json").write_text(
        "{", encoding="utf-8"
    )
    assert "corrupt" in corrupt.verify("corrupt-manifest").errors[0]

    missing_hmac = _finalized_run(tmp_path / "missing-hmac", "missing-hmac")
    (missing_hmac.bundle_path("missing-hmac") / "manifest.hmac").unlink()
    assert any("HMAC" in error for error in missing_hmac.verify("missing-hmac").errors)

    extra = _finalized_run(tmp_path / "extra-bundle", "extra-bundle")
    (extra.bundle_path("extra-bundle") / "unexpected.txt").write_text(
        "extra", encoding="utf-8"
    )
    assert any("unexpected bundle" in error for error in extra.verify("extra-bundle").errors)

    unsigned = _store(tmp_path / "unsigned", active_key_id=None)
    unsigned.append_event("unsigned-extra-hmac", _event_draft())
    unsigned.finalize("unsigned-extra-hmac", _manifest_payload("unsigned-extra-hmac"))
    (unsigned.bundle_path("unsigned-extra-hmac") / "manifest.hmac").write_text(
        "0" * 64, encoding="ascii"
    )
    assert any(
        "unexpected HMAC" in error
        for error in unsigned.verify("unsigned-extra-hmac").errors
    )


def test_locked_verify_and_unverified_read_fail_closed(tmp_path: Path) -> None:
    from general_ludd.replay.store import ReplayIntegrityError

    store = _store(tmp_path, lock_timeout=0.01)
    store.append_event("locked-verify", _event_draft())
    store.finalize("locked-verify", _manifest_payload("locked-verify"))
    with store.run_lock("locked-verify"):
        locked = store.verify("locked-verify")
    assert locked.valid is False
    assert locked.errors == ("replay bundle is locked",)

    incomplete = _store(tmp_path / "incomplete")
    incomplete.append_event("not-final", _event_draft())
    with pytest.raises(ReplayIntegrityError, match="did not verify"):
        incomplete.read_verified("not-final")


def test_retention_handles_scan_bounds_naive_time_and_corrupt_or_safe_entries(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 10, 10, tzinfo=UTC)
    store = _finalized_run(
        tmp_path,
        "a-old",
        created_at=now - timedelta(days=3),
        expires_at=now - timedelta(days=1),
    )
    _finalized_run(tmp_path, "b-new", created_at=now - timedelta(days=1))
    with pytest.raises(ValueError, match="timezone-aware"):
        store.enforce_retention(
            now=datetime(2026, 10, 10),
            max_total_bytes=10_000_000,
            max_deletions=1,
            scan_limit=1,
        )

    truncated = store.enforce_retention(
        now=now,
        max_total_bytes=10_000_000,
        max_deletions=1,
        scan_limit=1,
        dry_run=True,
    )
    assert truncated.scanned == 1
    assert truncated.quota_satisfied is False

    corrupt_dir = store.bundle_path("c-corrupt")
    corrupt_dir.mkdir()
    (corrupt_dir / "manifest.json").write_text("{", encoding="utf-8")
    result = store.enforce_retention(
        now=now,
        max_total_bytes=10_000_000,
        max_deletions=2,
        scan_limit=10,
        dry_run=True,
    )
    assert "c-corrupt" in result.skipped_incomplete


def test_retention_byte_pressure_is_oldest_first_and_never_follows_symlinks(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 10, 10, tzinfo=UTC)
    store = _finalized_run(
        tmp_path,
        "old-unexpired",
        created_at=now - timedelta(days=2),
    )
    _finalized_run(
        tmp_path,
        "new-unexpired",
        created_at=now - timedelta(days=1),
    )
    pressure = store.enforce_retention(
        now=now,
        max_total_bytes=0,
        max_deletions=1,
        scan_limit=10,
    )
    assert pressure.deleted == ("old-unexpired",)
    assert store.bundle_path("new-unexpired").exists()

    outside = tmp_path / "outside-evidence"
    outside.write_text("must-survive", encoding="utf-8")
    (store.bundle_path("new-unexpired") / "unsafe-link").symlink_to(outside)
    guarded = store.enforce_retention(
        now=now,
        max_total_bytes=0,
        max_deletions=1,
        scan_limit=10,
    )
    assert "new-unexpired" in guarded.skipped_incomplete
    assert guarded.quota_satisfied is False
    assert outside.read_text(encoding="utf-8") == "must-survive"
