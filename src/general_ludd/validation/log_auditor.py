"""Log auditor — inspects log entries for anomalies and policy violations.

Secret detection is handled by detect-secrets in pre-commit/CI.
This module focuses on domain-specific audit checks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_MAX_RETRY_ATTEMPTS = 5

_SECRET_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"ghp_[a-zA-Z0-9]{30,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]


@dataclass
class AuditFinding:
    severity: str
    category: str
    description: str
    evidence: str


@dataclass
class AuditReport:
    findings: list[AuditFinding] = field(default_factory=list)
    total_findings: int = 0


class LogAuditor:
    def audit_logs(self, log_entries: list[dict[str, Any]]) -> AuditReport:
        findings: list[AuditFinding] = []
        for entry in log_entries:
            findings.extend(self._check_entry(entry))
        return AuditReport(findings=findings, total_findings=len(findings))

    def _check_entry(self, entry: dict[str, Any]) -> list[AuditFinding]:
        findings: list[AuditFinding] = []

        if "correlation_id" not in entry or not entry["correlation_id"]:
            findings.append(AuditFinding(
                severity="medium",
                category="missing_correlation_id",
                description=f"Log entry missing correlation_id: {entry.get('event', 'unknown')}",
                evidence=str(entry),
            ))

        attempt = entry.get("attempt", 0)
        if isinstance(attempt, int) and attempt >= _MAX_RETRY_ATTEMPTS:
            from_status = entry.get("from_status", "")
            to_status = entry.get("to_status", "")
            if from_status == to_status:
                findings.append(AuditFinding(
                    severity="high",
                    category="stuck_todo",
                    description=f"Todo {entry.get('todo_id', 'unknown')} appears stuck after {attempt} attempts",
                    evidence=f"from={from_status} to={to_status} attempt={attempt}",
                ))

        payload = entry.get("payload", {})
        for val in payload.values() if isinstance(payload, dict) else []:
            if isinstance(val, str):
                for pat in _SECRET_PATTERNS:
                    if pat.search(val):
                        findings.append(AuditFinding(
                            severity="critical",
                            category="secret_like_value",
                            description=f"Payload contains secret-like value matching {pat.pattern}",
                            evidence=str(entry),
                        ))

        return findings
