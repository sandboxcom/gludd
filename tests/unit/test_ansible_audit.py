"""TDD: structured audit logging for playbook execution (OpenShell P1 transfer).

Mirrors OpenShell's audit trail: every policy-denied outbound request,
credential access, and protected-path write is logged as a structured JSON
event so operators can detect exfiltration attempts from audit logs.
"""
from __future__ import annotations

import json

from general_ludd.ansible.audit import AuditEvent, PlaybookAuditLogger


class TestNetworkDenyAudit:
    """A network policy denial emits a structured event with full detail."""

    def test_audit_network_deny_emits_structured_event(self) -> None:
        """deny -> event with method/url/policy/timestamp."""
        logger = PlaybookAuditLogger(playbook="deploy.yml")
        logger.network_deny(
            module="uri",
            method="POST",
            url="https://evil.example.com/exfil",
            policy="deny-egress-default",
        )
        events = logger.flush()
        assert len(events) == 1
        event = events[0]
        assert event.event_type == "network_deny"
        assert event.module == "uri"
        assert event.detail["method"] == "POST"
        assert event.detail["url"] == "https://evil.example.com/exfil"
        assert event.detail["policy"] == "deny-egress-default"
        assert isinstance(event.timestamp, float)
        assert event.timestamp > 0


class TestCredentialAccessAudit:
    """Any task that reads OpenBao / env secrets is logged."""

    def test_audit_credential_access_logged(self) -> None:
        """task reads secret -> event with task/module."""
        logger = PlaybookAuditLogger(playbook="deploy.yml")
        logger.credential_access(
            module="community.hashi_vault.vault_read",
            secret_name="secret/data/aws",  # pragma: allowlist secret
        )
        events = logger.flush()
        assert len(events) == 1
        event = events[0]
        assert event.event_type == "credential_access"
        assert event.module == "community.hashi_vault.vault_read"
        assert event.detail["secret_name"] == "secret/data/aws"


class TestPathWriteBlockedAudit:
    """A task that writes to a protected path is logged."""

    def test_audit_path_write_blocked_logged(self) -> None:
        """task writes to protected path -> event with path."""
        logger = PlaybookAuditLogger(playbook="deploy.yml")
        logger.path_blocked(
            module="copy",
            path="/etc/shadow",
        )
        events = logger.flush()
        assert len(events) == 1
        event = events[0]
        assert event.event_type == "path_blocked"
        assert event.module == "copy"
        assert event.detail["path"] == "/etc/shadow"


class TestPlaybookContext:
    """Every event carries the playbook context it fired within."""

    def test_audit_event_includes_playbook_name(self) -> None:
        logger = PlaybookAuditLogger(playbook="provision-db.yml", sandbox_id="sbx-42")
        logger.network_deny(
            module="get_url",
            method="GET",
            url="http://169.254.169.254/latest/meta-data/",
            policy="block-metadata",
        )
        event = logger.flush()[0]
        assert event.playbook == "provision-db.yml"
        assert event.sandbox_id == "sbx-42"


class TestJsonSerialization:
    """Emitted events serialize to valid JSON."""

    def test_audit_event_json_valid(self) -> None:
        event = AuditEvent(
            event_type="network_deny",
            module="uri",
            detail={"method": "POST", "url": "https://x/y", "policy": "p"},
            playbook="deploy.yml",
            timestamp=1234.5,
            sandbox_id="sbx-1",
        )
        line = event.to_json()
        parsed = json.loads(line)
        assert parsed["event_type"] == "network_deny"
        assert parsed["module"] == "uri"
        assert parsed["detail"]["url"] == "https://x/y"
        assert parsed["playbook"] == "deploy.yml"
        assert parsed["timestamp"] == 1234.5
        assert parsed["sandbox_id"] == "sbx-1"

    def test_logger_emits_valid_json_to_sink(self) -> None:
        """The logger's write sink receives valid JSON lines."""
        lines: list[str] = []
        logger = PlaybookAuditLogger(playbook="deploy.yml", sink=lines.append)
        logger.network_deny(module="uri", method="POST", url="https://x/y", policy="p")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["event_type"] == "network_deny"


class TestFailOpen:
    """A failing log write must never block the playbook."""

    def test_audit_logger_fail_open(self) -> None:
        """if log write fails, does NOT block the playbook (no exception raised)."""
        def broken_sink(_line: str) -> None:
            raise OSError("disk full")

        logger = PlaybookAuditLogger(playbook="deploy.yml", sink=broken_sink)
        logger.network_deny(module="uri", method="POST", url="https://x/y", policy="p")
        logger.credential_access(module="vault", secret_name="s")
        logger.path_blocked(module="copy", path="/etc/shadow")
        events = logger.flush()
        assert len(events) == 3
