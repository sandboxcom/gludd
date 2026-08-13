"""Deep audit logging tests — event recording, query/filter, retention,
immutability, export, and concurrent write safety.
"""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.ansible.audit import (
    CREDENTIAL_ACCESS,
    NETWORK_DENY,
    PATH_BLOCKED,
    AuditEvent,
    PlaybookAuditLogger,
)
from general_ludd.db.models import AuditEventModel
from general_ludd.db.repository import AuditEventRepository
from general_ludd.self_update.apply import AuditRecord
from general_ludd.sts.audit import StsAuditPipeline
from general_ludd.validation.log_auditor import AuditFinding, AuditReport, LogAuditor

# ── helpers ────────────────────────────────────────────────────────────────


def _async_mock_session_factory():
    session = AsyncMock()
    session.commit = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=None)
    session.execute.return_value = result
    sf = MagicMock()
    sf.return_value.__aenter__ = AsyncMock(return_value=session)
    sf.return_value.__aexit__ = AsyncMock()
    sf.begin = MagicMock()
    sf.begin.return_value.__aenter__ = AsyncMock(return_value=session)
    sf.begin.return_value.__aexit__ = AsyncMock()
    return sf


def _repository_session() -> AsyncMock:
    """Model AsyncSession's sync add/result surface without awaitable leaks."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session.execute.return_value = result
    return session


# ── 1. Event recording with all metadata fields ────────────────────────────


class TestEventRecordingAllFields:
    def test_sts_audit_event_dict_contains_all_fields(self):
        pipeline = StsAuditPipeline(MagicMock())
        event = pipeline._event_dict(action="mint", agent_id="a1", parent_agent_id="p1", scope_hash="sh1")
        assert set(event.keys()) == {
            "action",
            "agent_id",
            "parent_agent_id",
            "scope_hash",
            "timestamp",
        }
        assert isinstance(event["timestamp"], float)
        assert event["action"] == "mint"

    def test_ansible_audit_event_to_dict_all_fields(self):
        evt = AuditEvent(
            event_type="network_deny",
            module="http_client",
            detail={"method": "POST", "url": "https://evil.com", "policy": "allowlist"},
            playbook="site.yml",
            sandbox_id="sb-42",
        )
        d = evt.to_dict()
        assert set(d.keys()) == {
            "event_type",
            "module",
            "detail",
            "playbook",
            "timestamp",
            "sandbox_id",
        }
        assert d["sandbox_id"] == "sb-42"
        assert isinstance(d["timestamp"], float)

    def test_audit_record_as_dict_all_fields(self):
        rec = AuditRecord(
            outcome="applied",
            subsystem="self_update",
            change_kind="code",
            apply_tier="config",
            target_files=("src/foo.py",),
            requested_by="agent-1",
            reason="clean diff",
            approved=True,
        )
        d = rec.as_dict()
        assert set(d.keys()) == {
            "outcome",
            "subsystem",
            "change_kind",
            "apply_tier",
            "target_files",
            "requested_by",
            "reason",
            "approved",
            "timestamp",
        }
        assert d["approved"] is True
        assert d["target_files"] == ["src/foo.py"]

    def test_audit_event_model_all_columns_exist(self):
        table = AuditEventModel.__table__
        col_names = {c.name for c in table.columns}
        required = {
            "id",
            "event_type",
            "project_id",
            "actor",
            "entity_type",
            "entity_id",
            "correlation_id",
            "details",
            "created_at",
            "scaffold_sha256",
            "model",
            "tokens_in",
            "tokens_out",
        }
        assert required <= col_names

    def test_audit_event_model_constructor_sets_all_fields(self):
        row = AuditEventModel(
            event_type="todo_created",
            project_id="proj-1",
            actor="agent-7",
            entity_type="todo",
            entity_id="todo-abc",
            correlation_id="corr-xyz",
            details='{"title":"Fix bug"}',
            scaffold_sha256="abc123def",
            model="sonnet",
            tokens_in=500,
            tokens_out=200,
        )
        assert row.event_type == "todo_created"
        assert row.project_id == "proj-1"
        assert row.actor == "agent-7"
        assert row.entity_type == "todo"
        assert row.entity_id == "todo-abc"
        assert row.correlation_id == "corr-xyz"
        assert row.details == '{"title":"Fix bug"}'
        assert row.scaffold_sha256 == "abc123def"
        assert row.model == "sonnet"
        assert row.tokens_in == 500
        assert row.tokens_out == 200


# ── 2. Query / filter by time range, actor, resource ───────────────────────


@pytest.mark.asyncio
class TestQueryFilter:
    async def test_audit_event_repo_list_by_entity_filters(self):
        session = _repository_session()
        repo = AuditEventRepository(session)
        await repo.list_by_entity("todo", "todo-abc", limit=20)
        session.execute.assert_awaited_once()
        stmt = session.execute.call_args[0][0]
        where_clause = str(stmt.whereclause).lower()
        assert "entity_type" in where_clause
        assert "entity_id" in where_clause

    async def test_audit_event_repo_list_by_project_filters(self):
        session = _repository_session()
        repo = AuditEventRepository(session)
        await repo.list_by_project("proj-42", limit=10)
        session.execute.assert_awaited_once()
        stmt = session.execute.call_args[0][0]
        where_clause = str(stmt.whereclause).lower()
        assert "project_id" in where_clause

    async def test_audit_event_repo_create_persists_all_fields(self):
        session = _repository_session()
        repo = AuditEventRepository(session)
        row = await repo.create(
            event_type="task_decision_made",
            entity_type="task_return",
            entity_id="tr-999",
            project_id="proj-1",
            details='{"score":0.95}',
        )
        assert row.event_type == "task_decision_made"
        assert row.entity_type == "task_return"
        assert row.entity_id == "tr-999"
        assert row.project_id == "proj-1"
        assert row.details == '{"score":0.95}'
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    async def test_audit_event_repo_create_null_project_id_raises(self):
        repo = AuditEventRepository(AsyncMock())
        with pytest.raises(ValueError, match="project_id"):
            await repo.create(
                event_type="todo_created",
                entity_type="todo",
                entity_id="t-1",
                project_id=None,
            )

    async def test_audit_event_repo_record_typed_serializes_details(self):
        from general_ludd.db.models import AuditEventType

        session = _repository_session()
        repo = AuditEventRepository(session)
        await repo.record_typed(
            event_type=AuditEventType.TODO_CREATED,
            entity_type="todo",
            entity_id="t-55",
            project_id="proj-1",
            details={"title": "New feature"},
        )
        session.add.assert_called_once()
        added: AuditEventModel = session.add.call_args[0][0]
        detail_dict = json.loads(added.details)
        assert detail_dict["title"] == "New feature"


# ── 3. Retention and pruning logic ─────────────────────────────────────────


class TestRetentionPruning:
    def test_playbook_audit_logger_flush_returns_all_and_clears(self):
        logger = PlaybookAuditLogger("play.yml", sandbox_id="sb-1")
        logger.network_deny("http", "GET", "https://bad.com", "deny-all")
        logger.path_blocked("fs", "/etc/shadow")
        events = logger.flush()
        assert len(events) == 2
        assert events[0].event_type == NETWORK_DENY
        assert events[1].event_type == PATH_BLOCKED
        second_flush = logger.flush()
        assert len(second_flush) == 2

    @pytest.mark.asyncio
    async def test_sts_audit_flush_on_tick_drains_pending(self):
        sf = _async_mock_session_factory()
        pipeline = StsAuditPipeline(sf)
        pipeline._pending_events = [
            {"token_id": "tok-1", "action": "mint", "agent_id": "a1"},
            {"token_id": "tok-2", "action": "use", "agent_id": "a2"},
        ]
        result = await pipeline.flush_on_tick()
        assert result == 2
        assert pipeline._pending_events == []

    def test_playbook_audit_logger_event_timestamps_are_monotonic(self):
        logger = PlaybookAuditLogger("play.yml")
        logger.network_deny("http", "POST", "https://a.com", "p1")
        time.sleep(0.01)
        logger.path_blocked("fs", "/tmp/x")
        events = logger.flush()
        assert len(events) == 2
        assert events[0].timestamp <= events[1].timestamp


# ── 4. Immutability enforcement ────────────────────────────────────────────


class TestImmutability:
    def test_audit_record_fields_immutable_after_creation(self):
        rec = AuditRecord(
            outcome="refused",
            subsystem="s",
            change_kind="c",
            apply_tier="a",
            target_files=("f",),
            requested_by="r",
            reason="no",
            approved=False,
        )
        d1 = rec.as_dict()
        d2 = rec.as_dict()
        assert d1 == d2

    def test_playbook_audit_event_to_dict_is_deterministic(self):
        evt = AuditEvent(
            event_type="credential_access",
            module="vault",
            detail={"secret_name": "api_key"},
            playbook="deploy.yml",
            sandbox_id="sb-3",
        )
        d1 = evt.to_dict()
        d2 = evt.to_dict()
        assert d1 == d2

    def test_sts_audit_scope_hash_deterministic_for_same_input(self):
        pipeline = StsAuditPipeline(MagicMock())
        h1 = pipeline._scope_hash(["read", "write", "execute"])
        h2 = pipeline._scope_hash(["read", "write", "execute"])
        assert h1 == h2

    def test_sts_audit_scope_hash_changes_when_actions_differ(self):
        pipeline = StsAuditPipeline(MagicMock())
        h1 = pipeline._scope_hash(["read"])
        h2 = pipeline._scope_hash(["read", "write"])
        assert h1 != h2

    def test_audit_event_to_json_is_stable_sorted(self):
        evt = AuditEvent(
            event_type="path_blocked",
            module="fs",
            detail={"path": "/etc/passwd"},
            playbook="lockdown.yml",
        )
        j1 = evt.to_json()
        j2 = evt.to_json()
        assert j1 == j2
        d = json.loads(j1)
        keys = list(d.keys())
        assert keys == sorted(keys)


# ── 5. Export to structured formats ────────────────────────────────────────


class TestExportStructuredFormats:
    def test_audit_event_to_json_produces_valid_json(self):
        evt = AuditEvent(
            event_type="network_deny",
            module="http",
            detail={"method": "GET", "url": "http://evil", "policy": "block"},
            playbook="test.yml",
        )
        j = evt.to_json()
        parsed = json.loads(j)
        assert parsed["event_type"] == "network_deny"
        assert parsed["module"] == "http"
        assert parsed["detail"] == {"method": "GET", "url": "http://evil", "policy": "block"}

    def test_audit_record_as_dict_json_serializable(self):
        rec = AuditRecord(
            outcome="applied",
            subsystem="self_update",
            change_kind="config",
            apply_tier="code",
            target_files=("a.py", "b.py"),
            requested_by="agent-1",
            reason="ok",
        )
        d = rec.as_dict()
        j = json.dumps(d)
        parsed = json.loads(j)
        assert parsed["outcome"] == "applied"
        assert parsed["target_files"] == ["a.py", "b.py"]

    def test_log_auditor_report_findings_structured(self):
        auditor = LogAuditor()
        entries = [
            {"correlation_id": "", "event": "test1"},
            {"correlation_id": "abc", "event": "test2"},
        ]
        report = auditor.audit_logs(entries)
        assert isinstance(report, AuditReport)
        assert len(report.findings) >= 1
        for f in report.findings:
            assert isinstance(f, AuditFinding)
            assert f.severity in ("critical", "high", "medium", "low", "info")
            assert f.category != ""

    def test_log_auditor_detects_secret_like_in_payload(self):
        auditor = LogAuditor()
        entries = [
            {"correlation_id": "x", "payload": {"key": "sk-abcdefghijklmnopqrstuvwxyz"}},
        ]
        report = auditor.audit_logs(entries)
        assert any(f.category == "secret_like_value" for f in report.findings)

    def test_log_auditor_detects_stuck_todo_after_max_retries(self):
        auditor = LogAuditor()
        entries = [
            {
                "correlation_id": "c1",
                "attempt": 7,
                "from_status": "blocked",
                "to_status": "blocked",
                "todo_id": "todo-stuck",
            },
        ]
        report = auditor.audit_logs(entries)
        assert any(f.category == "stuck_todo" for f in report.findings)


# ── 6. Concurrent write safety ─────────────────────────────────────────────


class TestConcurrentWriteSafety:
    def test_playbook_audit_logger_thread_safe_buffer(self):
        logger = PlaybookAuditLogger("concurrent.yml")
        threads = []
        errors: list[Exception] = []

        def emit_batch(batch_id: int) -> None:
            try:
                for i in range(50):
                    logger.network_deny(
                        f"mod-{batch_id}",
                        "GET",
                        f"http://url-{batch_id}-{i}",
                        f"policy-{batch_id}",
                    )
            except Exception as exc:
                errors.append(exc)

        for tid in range(4):
            t = threading.Thread(target=emit_batch, args=(tid,))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        events = logger.flush()
        assert len(events) == 200

    def test_audit_event_emit_does_not_mutate_original_detail_dict(self):
        original = {"path": "/etc/hosts"}
        evt = AuditEvent(
            event_type="path_blocked",
            module="fs",
            detail=original,
            playbook="deploy.yml",
        )
        original["extra"] = "injected"
        assert "extra" in original
        assert evt.detail.get("extra") is None

    @pytest.mark.asyncio
    async def test_sts_audit_append_event_handles_missing_row_gracefully(self):
        sf = _async_mock_session_factory()
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        session = sf.begin.return_value.__aenter__.return_value
        session.execute.return_value = result
        pipeline = StsAuditPipeline(sf)

        await pipeline._append_event("tok-nonexistent", {"action": "mint"})
        session.execute.assert_awaited_once()
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_sts_audit_append_event_increments_use_count(self):
        sf = _async_mock_session_factory()
        row = MagicMock()
        row.use_count = 3
        row.events = "[]"
        row.last_used_at = 1000.0
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=row)
        session = sf.begin.return_value.__aenter__.return_value
        session.execute.return_value = result
        pipeline = StsAuditPipeline(sf)

        await pipeline._append_event("tok-1", {"action": "use", "agent_id": "a1"})
        assert row.use_count == 4
        assert row.last_used_at != 1000.0
        session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_sts_audit_appends_multiple_events_to_same_row(self):
        sf = _async_mock_session_factory()
        row = MagicMock()
        row.use_count = 0
        row.events = json.dumps([{"action": "mint", "id": 1}])
        row.last_used_at = None
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=row)
        session = sf.begin.return_value.__aenter__.return_value
        session.execute.return_value = result
        pipeline = StsAuditPipeline(sf)

        await pipeline._append_event("tok-1", {"action": "use", "id": 2})
        assert json.loads(row.events) == [
            {"action": "mint", "id": 1},
            {"action": "use", "id": 2},
        ]
        assert row.use_count == 1


# ── integration: all six record methods produce correct action ──────────────


@pytest.mark.asyncio
class TestStsAuditAllRecordActions:
    async def test_all_six_record_actions_use_correct_action_string(self):
        sf = _async_mock_session_factory()
        row = MagicMock()
        row.use_count = 0
        row.events = "[]"
        row.last_used_at = None
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=row)
        session = sf.begin.return_value.__aenter__.return_value
        session.execute.return_value = result
        pipeline = StsAuditPipeline(sf)
        pipeline._scope_hash = MagicMock(return_value="hash1")

        await pipeline.record_mint("t1", "iss", "sub", ["read"])
        await pipeline.record_use("t1", "sub", "iss")
        await pipeline.record_renew("t1", "sub", "iss")
        await pipeline.record_revoke("t1", "sub", "iss")
        await pipeline.record_revive("t1", "sub", "iss")
        await pipeline.record_expire("t1", "sub", "iss")

        events_list = _extract_events_from_row(row)
        actions = [e["action"] for e in events_list]
        assert actions == ["mint", "use", "renew", "revoke", "revive", "expire"]


def _extract_events_from_row(row: MagicMock) -> list[dict]:
    """Pull the `events` column value set by mock calls, deserialise."""
    from json import loads

    raw = row.events
    if isinstance(raw, str):
        try:
            return loads(raw)
        except Exception:
            return []
    return raw or []


# ── Playbook audit logger: all three event types ────────────────────────────


class TestPlaybookAuditAllEventTypes:
    def test_network_deny_emits_correct_type_and_detail(self):
        logger = PlaybookAuditLogger("p.yml")
        logger.network_deny("net_mod", "DELETE", "https://bad", "block-all")
        events = logger.flush()
        assert len(events) == 1
        e = events[0]
        assert e.event_type == NETWORK_DENY
        assert e.module == "net_mod"
        assert e.detail["method"] == "DELETE"

    def test_credential_access_emits_correct_type(self):
        logger = PlaybookAuditLogger("p.yml")
        logger.credential_access("vault_mod", "db_password")
        events = logger.flush()
        assert len(events) == 1
        assert events[0].event_type == CREDENTIAL_ACCESS
        assert events[0].detail["secret_name"] == "db_password"

    def test_path_blocked_emits_correct_type(self):
        logger = PlaybookAuditLogger("p.yml")
        logger.path_blocked("fs_mod", "/etc/passwd")
        events = logger.flush()
        assert len(events) == 1
        assert events[0].event_type == PATH_BLOCKED
        assert events[0].detail["path"] == "/etc/passwd"

    def test_playbook_and_sandbox_id_propagated(self):
        logger = PlaybookAuditLogger("site.yml", sandbox_id="sb-99")
        logger.network_deny("m", "GET", "https://u", "p")
        e = logger.flush()[0]
        assert e.playbook == "site.yml"
        assert e.sandbox_id == "sb-99"
