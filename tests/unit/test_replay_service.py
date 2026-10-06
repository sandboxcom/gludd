"""Read-only service contracts for v1 forensic replay bundles."""

from __future__ import annotations

import base64
import hashlib
import inspect
import io
import json
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

_KEY_ID = "replay-integrity-v1"
_KEY = b"replay-service-test-key-material32"


def _event(index: int = 0, *, payload: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "event_id": f"event-{index}",
        "occurred_at": "2026-10-05T12:00:00Z",
        "recorded_at": "2026-10-05T12:00:00.010Z",
        "type": "tool.responded",
        "project_id": "project-a",
        "correlation": {
            "todo_id": "todo-7",
            "task_id": "task-9",
            "trace_id": f"trace-{index}",
        },
        "payload": payload or {"exit_code": 0},
        "redaction": {"count": 0, "kinds": []},
    }


def _manifest(
    run_id: str,
    *,
    project_id: str | None = "project-a",
    created_at: datetime | None = None,
    attachments: list[dict[str, object]] | None = None,
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
        "project_id": project_id,
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
        "attachments": attachments or [],
        "completeness": {
            "expected_stages": ["run.started", "run.completed"],
            "observed_stages": ["run.started", "run.completed"],
            "recorder_errors": [],
            "missing_ranges": [],
        },
        "retention": {"expires_at": None, "pinned": False, "hold_reason": None},
        "integrity": "unsigned",
        "signing_key_id": None,
    }


def _store(root: Path) -> Any:
    from general_ludd.replay.store import RunBundleStore

    return RunBundleStore(
        root,
        verification_keys={_KEY_ID: _KEY},
        active_key_id=_KEY_ID,
    )


def _finalize(
    store: Any,
    run_id: str,
    *,
    project_id: str | None = "project-a",
    created_at: datetime | None = None,
    payload: dict[str, object] | None = None,
    attachment: bytes | None = None,
) -> None:
    attachments: list[dict[str, object]] = []
    if attachment is not None:
        digest = hashlib.sha256(attachment).hexdigest()
        attachments_dir = store.bundle_path(run_id) / "attachments"
        attachments_dir.mkdir(parents=True)
        (attachments_dir / f"sha256-{digest}").write_bytes(attachment)
        attachments.append(
            {
                "digest": f"sha256:{digest}",
                "original_bytes": len(attachment),
                "stored_bytes": len(attachment),
                "media_type": "application/octet-stream",
                "encoding": None,
                "redaction_count": 0,
                "truncated": False,
            }
        )
    store.append_event(run_id, _event(payload=payload))
    store.finalize(
        run_id,
        _manifest(
            run_id,
            project_id=project_id,
            created_at=created_at,
            attachments=attachments,
        ),
    )


class _Catalog:
    def __init__(self, entries: list[Any]) -> None:
        self.entries = entries
        self.page_calls: list[tuple[str | None, int, int]] = []
        self.get_calls: list[str] = []

    def page(
        self, *, project_id: str | None, offset: int, limit: int
    ) -> tuple[Any, ...]:
        self.page_calls.append((project_id, offset, limit))
        scoped = [entry for entry in self.entries if entry.project_id == project_id]
        return tuple(scoped[offset : offset + limit])

    def get(self, run_id: str) -> Any | None:
        self.get_calls.append(run_id)
        return next((entry for entry in self.entries if entry.run_id == run_id), None)


def _entry(
    run_id: str,
    *,
    project_id: str | None = "project-a",
    created_at: datetime | None = None,
) -> Any:
    from general_ludd.replay.service import ReplayCatalogEntry

    return ReplayCatalogEntry(
        run_id=run_id,
        project_id=project_id,
        created_at=created_at or datetime(2026, 10, 5, 12, tzinfo=UTC),
    )


def _service(
    store: Any,
    catalog: _Catalog,
    *,
    allowed_projects: set[str | None] | None = None,
    audits: list[Any] | None = None,
) -> Any:
    from general_ludd.replay.service import ReplayService

    allowed = {"project-a"} if allowed_projects is None else allowed_projects
    sink = [] if audits is None else audits
    return ReplayService(
        store,
        catalog=catalog,
        authorize=lambda capability, project_id: (
            project_id in allowed
            and capability in {"replay:read", "replay:export"}
        ),
        audit=sink.append,
        cursor_key=b"opaque-cursor-test-key-material-32b",
    )


def test_list_is_bounded_paginated_and_cursor_is_opaque_and_scope_bound(
    tmp_path: Path,
) -> None:
    from general_ludd.replay.service import ReplayCursorError

    store = _store(tmp_path)
    entries = []
    for index in range(3):
        run_id = f"run-{index}"
        created = datetime(2026, 10, 5, 12, index, tzinfo=UTC)
        _finalize(store, run_id, created_at=created)
        entries.append(_entry(run_id, created_at=created))
    catalog = _Catalog(entries)
    service = _service(store, catalog)

    first = service.list_runs(project_id="project-a", limit=2)
    assert [item.run_id for item in first.items] == ["run-0", "run-1"]
    assert first.next_cursor is not None
    assert "run-1" not in first.next_cursor
    assert "project-a" not in first.next_cursor
    assert catalog.page_calls == [("project-a", 0, 3)]

    second = service.list_runs(
        project_id="project-a", limit=2, cursor=first.next_cursor
    )
    assert [item.run_id for item in second.items] == ["run-2"]
    assert second.next_cursor is None
    assert catalog.page_calls[-1] == ("project-a", 2, 3)

    tampered = first.next_cursor[:-1] + ("A" if first.next_cursor[-1] != "A" else "B")
    with pytest.raises(ReplayCursorError, match="invalid replay cursor"):
        service.list_runs(project_id="project-a", limit=2, cursor=tampered)
    with pytest.raises(ReplayCursorError, match="invalid replay cursor"):
        _service(store, catalog, allowed_projects={"project-b"}).list_runs(
            project_id="project-b", limit=2, cursor=first.next_cursor
        )

    for limit in (0, 201, True):
        with pytest.raises(ValueError, match="limit"):
            service.list_runs(project_id="project-a", limit=limit)


def test_unauthorized_collection_is_content_free_and_does_not_query_catalog(
    tmp_path: Path,
) -> None:
    from general_ludd.replay.service import ReplayAccessDeniedError

    store = _store(tmp_path)
    catalog = _Catalog([])
    service = _service(store, catalog, allowed_projects=set())
    with pytest.raises(ReplayAccessDeniedError) as captured:
        service.list_runs(project_id="project-a", limit=10)
    assert str(captured.value) == "replay access denied"
    assert catalog.page_calls == []


def test_individual_cross_project_and_unauthorized_lookups_are_identical_not_found(
    tmp_path: Path,
) -> None:
    from general_ludd.replay.service import ReplayNotFoundError

    store = _store(tmp_path)
    _finalize(store, "run-b", project_id="project-b")
    catalog = _Catalog([_entry("run-b", project_id="project-b")])
    service = _service(store, catalog, allowed_projects={"project-a"})

    failures: list[str] = []
    operations: tuple[Callable[[], object], ...] = (
        lambda: service.show("run-b", project_id="project-a"),
        lambda: service.verify("run-b", project_id="project-a"),
        lambda: service.stream_export("run-b", project_id="project-a"),
        lambda: service.show("run-b", project_id="project-b"),
        lambda: service.show("missing", project_id="project-a"),
    )
    for operation in operations:
        with pytest.raises(ReplayNotFoundError) as captured:
            operation()
        failures.append(str(captured.value))
    assert failures == ["replay not found"] * len(failures)


def test_show_returns_only_safe_manifest_event_and_attachment_metadata(
    tmp_path: Path,
) -> None:
    secret_payload = "TOP-SECRET-PAYLOAD"
    attachment = b"TOP-SECRET-ATTACHMENT"
    store = _store(tmp_path)
    _finalize(
        store,
        "show-run",
        payload={"private": secret_payload},
        attachment=attachment,
    )
    catalog = _Catalog([_entry("show-run")])
    audits: list[Any] = []
    service = _service(store, catalog, audits=audits)
    bundle_path = store.bundle_path("show-run")
    before = {
        str(path.relative_to(bundle_path)): path.read_bytes()
        for path in bundle_path.rglob("*")
        if path.is_file()
    }

    detail = service.show("show-run", project_id="project-a")
    encoded = json.dumps(asdict(detail), default=str, sort_keys=True)

    assert detail.verification.reason == "verified"
    assert detail.manifest is not None
    assert detail.manifest.project_id == "project-a"
    assert detail.events[0].sequence == 0
    assert detail.events[0].event_type == "tool.responded"
    assert detail.attachments[0].stored_bytes == len(attachment)
    assert secret_payload not in encoded
    assert attachment.decode() not in encoded
    assert "payload" not in asdict(detail.events[0])
    assert "content" not in asdict(detail.attachments[0])
    after = {
        str(path.relative_to(bundle_path)): path.read_bytes()
        for path in bundle_path.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert audits[-1].action == "show"
    assert audits[-1].outcome == "success"


def test_verify_classifies_complete_corrupt_incomplete_and_unsupported(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _finalize(store, "complete")
    _finalize(store, "corrupt")
    corrupt_event = store.bundle_path("corrupt") / "events" / "000000000000.json"
    corrupt_event.write_text("{", encoding="utf-8")

    store.append_event("incomplete", _event())
    unsupported_dir = store.bundle_path("unsupported")
    unsupported_dir.mkdir(parents=True)
    (unsupported_dir / "manifest.json").write_text(
        '{"schema":"gludd.run-bundle/v2"}', encoding="utf-8"
    )
    entries = [_entry(name) for name in ("complete", "corrupt", "incomplete", "unsupported")]
    audits: list[Any] = []
    service = _service(store, _Catalog(entries), audits=audits)

    assert service.verify("complete", project_id="project-a").reason == "verified"
    assert service.verify("corrupt", project_id="project-a").reason == "integrity_failure"
    assert service.verify("incomplete", project_id="project-a").reason == "incomplete"
    unsupported = service.verify("unsupported", project_id="project-a")
    assert unsupported.reason == "unsupported_schema"
    assert unsupported.valid is False
    assert all(not hasattr(event, "errors") for event in audits)


def test_list_includes_bounded_unverified_summaries_without_raw_failures(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.append_event("partial", _event())
    catalog = _Catalog([_entry("partial")])
    page = _service(store, catalog).list_runs(project_id="project-a", limit=10)
    assert len(page.items) == 1
    assert page.items[0].verification.reason == "incomplete"
    assert not hasattr(page.items[0].verification, "errors")


def test_stream_export_is_verified_chunked_fixed_path_and_never_overwrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attachment = b"attachment-block-" * 10_000
    store = _store(tmp_path)
    _finalize(store, "export-run", attachment=attachment)
    catalog = _Catalog([_entry("export-run")])
    audits: list[Any] = []
    service = _service(store, catalog, audits=audits)
    sentinel = tmp_path / "existing.zip"
    sentinel.write_bytes(b"do-not-overwrite")
    temporary_calls = 0
    real_temporary_file = tempfile.TemporaryFile

    def tracked_temporary_file(*args: Any, **kwargs: Any) -> Any:
        nonlocal temporary_calls
        temporary_calls += 1
        return real_temporary_file(*args, **kwargs)

    monkeypatch.setattr(tempfile, "TemporaryFile", tracked_temporary_file)
    stream = service.stream_export(
        "export-run", project_id="project-a", chunk_size=4096
    )
    assert inspect.isgenerator(stream)
    chunks = list(stream)
    assert chunks
    assert max(map(len, chunks)) <= 4096
    assert temporary_calls == 1
    assert sentinel.read_bytes() == b"do-not-overwrite"

    with zipfile.ZipFile(io.BytesIO(b"".join(chunks))) as archive:
        names = archive.namelist()
        assert names == sorted(names)
        assert "manifest.json" in names
        assert "manifest.hmac" in names
        assert "events/000000000000.json" in names
        attachment_name = next(name for name in names if name.startswith("attachments/"))
        assert archive.read(attachment_name) == attachment
        assert all(not name.startswith(("/", "../")) and "/../" not in name for name in names)
    assert audits[-1].action == "export"
    assert audits[-1].outcome == "success"


def test_export_rejects_corruption_before_returning_a_stream(tmp_path: Path) -> None:
    from general_ludd.replay.service import ReplayExportError

    store = _store(tmp_path)
    _finalize(store, "bad-export")
    event = store.bundle_path("bad-export") / "events" / "000000000000.json"
    event.write_text("{", encoding="utf-8")
    service = _service(store, _Catalog([_entry("bad-export")]))
    with pytest.raises(ReplayExportError) as captured:
        service.stream_export("bad-export", project_id="project-a")
    assert str(captured.value) == "replay export unavailable"


def test_catalog_results_fail_closed_on_scope_duplicates_and_overflow(
    tmp_path: Path,
) -> None:
    from general_ludd.replay.service import ReplayCatalogError

    store = _store(tmp_path)
    _finalize(store, "run-a")

    class BrokenCatalog(_Catalog):
        def __init__(self, rows: tuple[Any, ...]) -> None:
            super().__init__([])
            self.rows = rows

        def page(
            self, *, project_id: str | None, offset: int, limit: int
        ) -> tuple[Any, ...]:
            del project_id, offset, limit
            return self.rows

    wrong_scope = BrokenCatalog((_entry("run-a", project_id="project-b"),))
    duplicate = _entry("run-a")
    duplicates = BrokenCatalog((duplicate, duplicate))
    overflow = BrokenCatalog(tuple(_entry(f"run-{index}") for index in range(4)))

    for catalog, limit in ((wrong_scope, 2), (duplicates, 2), (overflow, 2)):
        with pytest.raises(ReplayCatalogError, match="replay catalog unavailable"):
            _service(store, catalog).list_runs(project_id="project-a", limit=limit)


def test_invalid_service_inputs_and_audit_failure_are_content_free(tmp_path: Path) -> None:
    from general_ludd.replay.service import ReplayAuditError, ReplayService

    store = _store(tmp_path)
    _finalize(store, "run-a")
    catalog = _Catalog([_entry("run-a")])
    with pytest.raises(ValueError, match="cursor_key"):
        ReplayService(
            store,
            catalog=catalog,
            authorize=lambda capability, project_id: True,
            cursor_key=b"short",
        )

    def failed_audit(event: Any) -> None:
        del event
        raise RuntimeError("private audit backend detail")

    service = ReplayService(
        store,
        catalog=catalog,
        authorize=lambda capability, project_id: True,
        audit=failed_audit,
        cursor_key=b"opaque-cursor-test-key-material-32b",
    )
    with pytest.raises(ReplayAuditError) as captured:
        service.verify("run-a", project_id="project-a")
    assert str(captured.value) == "replay audit unavailable"
    assert "private" not in str(captured.value)

    for run_id in ("../escape", "run/escape", ""):
        with pytest.raises(ValueError, match="run_id"):
            service.show(run_id, project_id="project-a")


def test_cursor_and_project_validation_fail_closed_without_catalog_access(
    tmp_path: Path,
) -> None:
    from general_ludd.replay.service import ReplayCursorError

    store = _store(tmp_path)
    _finalize(store, "system-run", project_id=None)
    catalog = _Catalog([_entry("system-run", project_id=None)])
    service = _service(store, catalog, allowed_projects={None})

    page = service.list_runs(project_id=None, limit=1)
    assert [item.run_id for item in page.items] == ["system-run"]

    bad_cursors = (
        "",
        "x" * 129,
        "not-ascii-é",
        "!",
        base64.urlsafe_b64encode(b"too-short").decode("ascii"),
    )
    for cursor in bad_cursors:
        with pytest.raises(ReplayCursorError, match="invalid replay cursor"):
            service.list_runs(project_id=None, cursor=cursor)

    before = list(catalog.page_calls)
    for project_id in ("", "../escape", "space is unsafe"):
        with pytest.raises(ValueError, match="project_id"):
            service.list_runs(project_id=project_id)
    assert catalog.page_calls == before


def test_injected_authorizer_and_catalog_failures_are_content_free(
    tmp_path: Path,
) -> None:
    from general_ludd.replay.service import (
        ReplayAccessDeniedError,
        ReplayCatalogError,
        ReplayService,
    )

    store = _store(tmp_path)

    def failed_authorizer(capability: str, project_id: str | None) -> bool:
        del capability, project_id
        raise RuntimeError("private authorizer failure")

    denied = ReplayService(
        store,
        catalog=_Catalog([]),
        authorize=failed_authorizer,
        cursor_key=b"opaque-cursor-test-key-material-32b",
    )
    with pytest.raises(ReplayAccessDeniedError) as captured:
        denied.list_runs(project_id="project-a")
    assert str(captured.value) == "replay access denied"

    class FailedCatalog(_Catalog):
        def page(
            self, *, project_id: str | None, offset: int, limit: int
        ) -> tuple[Any, ...]:
            del project_id, offset, limit
            raise RuntimeError("private catalog failure")

        def get(self, run_id: str) -> Any | None:
            del run_id
            raise RuntimeError("private catalog failure")

    unavailable = _service(store, FailedCatalog([]))
    with pytest.raises(ReplayCatalogError) as list_failure:
        unavailable.list_runs(project_id="project-a")
    with pytest.raises(ReplayCatalogError) as show_failure:
        unavailable.show("missing", project_id="project-a")
    assert str(list_failure.value) == "replay catalog unavailable"
    assert str(show_failure.value) == "replay catalog unavailable"


def test_show_incomplete_and_manifest_scope_mismatch_disclose_no_content(
    tmp_path: Path,
) -> None:
    from general_ludd.replay.service import ReplayNotFoundError

    store = _store(tmp_path)
    store.append_event("partial", _event())
    partial_service = _service(store, _Catalog([_entry("partial")]))
    detail = partial_service.show("partial", project_id="project-a")
    assert detail.verification.reason == "incomplete"
    assert detail.manifest is None
    assert detail.events == ()
    assert detail.attachments == ()

    _finalize(store, "wrong-scope", project_id="project-b")
    mismatched = _service(store, _Catalog([_entry("wrong-scope")]))
    with pytest.raises(ReplayNotFoundError, match="replay not found"):
        mismatched.show("wrong-scope", project_id="project-a")


def test_unsigned_export_and_post_verification_corruption_fail_safely(
    tmp_path: Path,
) -> None:
    from general_ludd.replay.service import ReplayExportError
    from general_ludd.replay.store import RunBundleStore

    unsigned_store = RunBundleStore(tmp_path / "unsigned")
    _finalize(unsigned_store, "unsigned")
    unsigned_service = _service(unsigned_store, _Catalog([_entry("unsigned")]))
    unsigned_chunks = list(
        unsigned_service.stream_export("unsigned", project_id="project-a")
    )
    with zipfile.ZipFile(io.BytesIO(b"".join(unsigned_chunks))) as archive:
        assert "manifest.hmac" not in archive.namelist()

    signed_store = _store(tmp_path / "signed")
    _finalize(signed_store, "raced")
    audits: list[Any] = []
    signed_service = _service(
        signed_store,
        _Catalog([_entry("raced")]),
        audits=audits,
    )
    stream = signed_service.stream_export("raced", project_id="project-a")
    event_path = signed_store.bundle_path("raced") / "events" / "000000000000.json"
    event_path.write_text("{", encoding="utf-8")
    with pytest.raises(ReplayExportError, match="replay export unavailable"):
        list(stream)
    assert audits[-1].action == "export"
    assert audits[-1].outcome == "failure"


def test_export_rejects_catalog_manifest_scope_mismatch(tmp_path: Path) -> None:
    from general_ludd.replay.service import ReplayNotFoundError

    store = _store(tmp_path)
    _finalize(store, "wrong-export-scope", project_id="project-b")
    service = _service(store, _Catalog([_entry("wrong-export-scope")]))
    with pytest.raises(ReplayNotFoundError, match="replay not found"):
        service.stream_export("wrong-export-scope", project_id="project-a")
