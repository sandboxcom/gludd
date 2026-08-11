"""Deep coverage for audit logging — edge cases, invariants, boundary conditions.

Extends test_ansible_audit.py (7 basic tests) with invariants the shallow
suite does not cover: event-type constants, cumulative buffering, copy-on-read
from flush, non-JSON-serializable detail values, empty-string playbook,
multiple-logger isolation, and the default sink / default timestamp factories.
"""

from __future__ import annotations

import json
import logging
import time
from unittest import mock

import pytest

from general_ludd.ansible.audit import (
    CREDENTIAL_ACCESS,
    NETWORK_DENY,
    PATH_BLOCKED,
    AuditEvent,
    PlaybookAuditLogger,
)


class TestEventTypeConstants:
    """The three exported constants are stable strings for downstream consumers."""

    def test_network_deny_constant(self) -> None:
        assert NETWORK_DENY == "network_deny"

    def test_credential_access_constant(self) -> None:
        assert CREDENTIAL_ACCESS == "credential_access"

    def test_path_blocked_constant(self) -> None:
        assert PATH_BLOCKED == "path_blocked"

    def test_constants_unique(self) -> None:
        vals = {NETWORK_DENY, CREDENTIAL_ACCESS, PATH_BLOCKED}
        assert len(vals) == 3


class TestAuditEventBoundary:
    """AuditEvent covers edge inputs without raising."""

    def test_empty_playbook(self) -> None:
        event = AuditEvent(
            event_type=NETWORK_DENY,
            module="uri",
            detail={"method": "GET", "url": "https://x", "policy": "p"},
            playbook="",
        )
        d = event.to_dict()
        assert d["playbook"] == ""

    def test_none_sandbox_id_serializes_as_null(self) -> None:
        event = AuditEvent(
            event_type=CREDENTIAL_ACCESS,
            module="vault",
            detail={"secret_name": "db/pw"},
            playbook="deploy.yml",
            sandbox_id=None,
        )
        line = event.to_json()
        parsed = json.loads(line)
        assert parsed["sandbox_id"] is None

    def test_to_json_sort_keys_produces_stable_output(self) -> None:
        event = AuditEvent(
            event_type=PATH_BLOCKED,
            module="copy",
            detail={"path": "/etc/shadow"},
            playbook="p.yml",
            timestamp=1000.0,
        )
        line1 = event.to_json()
        line2 = event.to_json()
        assert line1 == line2

    def test_to_dict_includes_all_fields(self) -> None:
        event = AuditEvent(
            event_type=NETWORK_DENY,
            module="uri",
            detail={"method": "GET", "url": "https://x", "policy": "p"},
            playbook="deploy.yml",
            timestamp=1234.5,
            sandbox_id="sbx-42",
        )
        d = event.to_dict()
        assert set(d.keys()) == {
            "event_type",
            "module",
            "detail",
            "playbook",
            "timestamp",
            "sandbox_id",
        }

    def test_to_json_handles_non_json_serializable_detail(self) -> None:
        event = AuditEvent(
            event_type=NETWORK_DENY,
            module="uri",
            detail={"bytes": b"\x00\xff", "float": 1.0, "int": 42, "str": "ok"},
            playbook="p.yml",
        )
        line = event.to_json()
        parsed = json.loads(line)
        assert parsed["detail"]["float"] == 1.0
        assert parsed["detail"]["int"] == 42
        assert parsed["detail"]["str"] == "ok"

    def test_detail_empty_dict(self) -> None:
        event = AuditEvent(
            event_type=NETWORK_DENY,
            module="uri",
            detail={},
            playbook="p.yml",
        )
        assert event.to_dict()["detail"] == {}

    def test_default_timestamp_is_current_time(self) -> None:
        before = time.time()
        event = AuditEvent(
            event_type=NETWORK_DENY,
            module="uri",
            detail={},
            playbook="p.yml",
        )
        after = time.time()
        assert before <= event.timestamp <= after + 0.1


class TestPlaybookAuditLoggerDeep:
    """PlaybookAuditLogger invariants beyond single-event emission."""

    def test_multiple_event_types_buffered(self) -> None:
        logger = PlaybookAuditLogger(playbook="deploy.yml")
        logger.network_deny("uri", "POST", "https://x", "p")
        logger.credential_access("vault", "s3/api")
        logger.path_blocked("copy", "/etc/hosts")
        events = logger.flush()
        assert len(events) == 3
        types = {e.event_type for e in events}
        assert types == {NETWORK_DENY, CREDENTIAL_ACCESS, PATH_BLOCKED}

    def test_flush_returns_copy_not_reference(self) -> None:
        logger = PlaybookAuditLogger(playbook="deploy.yml")
        logger.network_deny("uri", "POST", "https://x", "p")
        flush1 = logger.flush()
        flush1.clear()
        flush2 = logger.flush()
        assert len(flush2) == 1

    def test_events_are_cumulative_across_flushes(self) -> None:
        logger = PlaybookAuditLogger(playbook="deploy.yml")
        logger.network_deny("uri", "POST", "https://x", "p")
        assert len(logger.flush()) == 1
        logger.credential_access("vault", "s")
        assert len(logger.flush()) == 2

    def test_default_sink_is_logger_info(self) -> None:
        """_default_sink calls logger.info with 'audit <line>'."""
        logger = PlaybookAuditLogger(playbook="deploy.yml")
        with mock.patch.object(logging.getLogger("general_ludd.ansible.audit"), "info") as mi:
            logger._default_sink("test-line")
        mi.assert_called_once_with("audit %s", "test-line")

    def test_custom_sink_receives_json_lines(self) -> None:
        lines: list[str] = []
        logger = PlaybookAuditLogger(playbook="p.yml", sink=lines.append)
        logger.network_deny("uri", "POST", "https://x", "p")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["event_type"] == NETWORK_DENY

    def test_separate_loggers_have_independent_buffers(self) -> None:
        a = PlaybookAuditLogger(playbook="a.yml")
        b = PlaybookAuditLogger(playbook="b.yml")
        a.network_deny("uri", "GET", "https://a", "p")
        b.credential_access("vault", "s")
        assert len(a.flush()) == 1
        assert len(b.flush()) == 1
        assert a.flush()[0].playbook == "a.yml"
        assert b.flush()[0].playbook == "b.yml"

    def test_sandbox_id_propagates_to_events(self) -> None:
        logger = PlaybookAuditLogger(playbook="p.yml", sandbox_id="sbx-99")
        logger.network_deny("uri", "GET", "https://x", "p")
        event = logger.flush()[0]
        assert event.sandbox_id == "sbx-99"

    def test_fail_open_sink_still_buffers_events(self) -> None:
        def broken(_line: str) -> None:
            raise RuntimeError("boom")

        logger = PlaybookAuditLogger(playbook="p.yml", sink=broken)
        logger.network_deny("uri", "POST", "https://x", "p")
        logger.credential_access("vault", "s")
        events = logger.flush()
        assert len(events) == 2

    def test_fail_open_sink_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        def broken(_line: str) -> None:
            raise OSError("disk full")

        caplog.set_level(logging.WARNING, logger="general_ludd.ansible.audit")
        logger = PlaybookAuditLogger(playbook="p.yml", sink=broken)
        logger.network_deny("uri", "POST", "https://x", "p")
        assert "audit sink failed" in caplog.text

    def test_path_blocked_detail_structure(self) -> None:
        logger = PlaybookAuditLogger(playbook="p.yml")
        logger.path_blocked("copy", "/etc/shadow")
        event = logger.flush()[0]
        assert event.detail["path"] == "/etc/shadow"
        assert event.module == "copy"
        assert event.event_type == PATH_BLOCKED

    def test_credential_access_detail_structure(self) -> None:
        logger = PlaybookAuditLogger(playbook="p.yml")
        logger.credential_access("community.hashi_vault.vault_read", "secret/prod/token")
        event = logger.flush()[0]
        assert event.detail["secret_name"] == "secret/prod/token"
        assert event.module == "community.hashi_vault.vault_read"
        assert event.event_type == CREDENTIAL_ACCESS

    def test_network_deny_detail_structure(self) -> None:
        logger = PlaybookAuditLogger(playbook="p.yml")
        logger.network_deny("get_url", "POST", "https://evil.com/dump", "block-egress")
        event = logger.flush()[0]
        assert event.detail["method"] == "POST"
        assert event.detail["url"] == "https://evil.com/dump"
        assert event.detail["policy"] == "block-egress"
        assert event.module == "get_url"
        assert event.event_type == NETWORK_DENY

    def test_emit_directly_with_auditevent_instance(self) -> None:
        logger = PlaybookAuditLogger(playbook="p.yml")
        event = AuditEvent(
            event_type=NETWORK_DENY,
            module="uri",
            detail={"method": "GET", "url": "https://x", "policy": "p"},
            playbook="p.yml",
        )
        logger.emit(event)
        assert len(logger.flush()) == 1
        assert logger.flush()[0] is event

    @pytest.mark.parametrize(
        "event_type,module,detail",
        [
            (NETWORK_DENY, "uri", {"method": "GET", "url": "https://x", "policy": "p"}),
            (CREDENTIAL_ACCESS, "vault", {"secret_name": "api/key"}),
            (PATH_BLOCKED, "lineinfile", {"path": "/etc/passwd"}),
        ],
    )
    def test_all_event_types_via_emit(self, event_type: str, module: str, detail: dict[str, str]) -> None:
        logger = PlaybookAuditLogger(playbook="p.yml")
        logger.emit(
            AuditEvent(
                event_type=event_type,
                module=module,
                detail=detail,
                playbook="p.yml",
                timestamp=1.0,
            )
        )
        event = logger.flush()[0]
        assert event.event_type == event_type
        assert event.module == module
        assert event.detail == detail

    def test_empty_flush_returns_empty_list(self) -> None:
        logger = PlaybookAuditLogger(playbook="p.yml")
        assert logger.flush() == []

    def test_many_events_large_count(self) -> None:
        logger = PlaybookAuditLogger(playbook="p.yml")
        for i in range(100):
            logger.network_deny("uri", "GET", f"https://host{i}/x", f"policy-{i}")
        assert len(logger.flush()) == 100

    def test_to_json_timestamp_float(self) -> None:
        event = AuditEvent(
            event_type=NETWORK_DENY,
            module="uri",
            detail={},
            playbook="p.yml",
            timestamp=1234.56789,
        )
        parsed = json.loads(event.to_json())
        assert parsed["timestamp"] == 1234.56789
