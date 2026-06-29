"""Unit tests for StsAuditModel (persistent STS audit log backing)."""

from __future__ import annotations

from general_ludd.db.models import StsAuditModel


class TestStsAuditModel:
    def test_sts_audit_model_creates_row(self):
        row = StsAuditModel(
            token_id="tok-abc",
            issuer_agent_id="issuer-1",
            subject_agent_id="subject-1",
            spec_yaml="capabilities: []\n",
            issued_at=1000.0,
            expires_at=4600.0,
        )
        assert row.token_id == "tok-abc"
        assert row.issuer_agent_id == "issuer-1"
        assert row.subject_agent_id == "subject-1"
        assert row.spec_yaml == "capabilities: []\n"
        assert row.issued_at == 1000.0
        assert row.expires_at == 4600.0
        assert row.last_used_at is None

    def test_sts_audit_use_count_default_is_zero(self):
        col = StsAuditModel.__table__.c.use_count
        assert col.default.arg == 0

    def test_sts_audit_events_default_empty_json(self):
        col = StsAuditModel.__table__.c.events
        assert col.default.arg == "[]"

    def test_sts_audit_tablename(self):
        assert StsAuditModel.__tablename__ == "sts_audit"
