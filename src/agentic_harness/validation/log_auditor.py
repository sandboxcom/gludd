"""Log auditor — inspects log entries for anomalies and policy violations."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SECRET_PATTERNS = [
    re.compile(r"(?i)(?:api_key|secret|token|password|private_key)\s*[:=]\s*['\"]?[\w\-]{16,}['\"]?"),
    re.compile(r"(?i)sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"(?i)ghp_[a-zA-Z0-9]{20,}"),
    re.compile(r"(?i)AKIA[0-9A-Z]{16}"),
]

_MAX_RETRY_ATTEMPTS = 5


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
    def audit_logs(self, log_entries: list[dict]) -> AuditReport:
        findings: list[AuditFinding] = []
        for entry in log_entries:
            findings.extend(self._check_entry(entry))
        return AuditReport(findings=findings, total_findings=len(findings))

    def _check_entry(self, entry: dict) -> list[AuditFinding]:
        findings: list[AuditFinding] = []

        if "correlation_id" not in entry or not entry["correlation_id"]:
            findings.append(AuditFinding(
                severity="medium",
                category="missing_correlation_id",
                description=f"Log entry missing correlation_id: {entry.get('event', 'unknown')}",
                evidence=str(entry),
            ))

        entry_str = str(entry)
        for pattern in _SECRET_PATTERNS:
            match = pattern.search(entry_str)
            if match:
                findings.append(AuditFinding(
                    severity="critical",
                    category="secret_like_value",
                    description="Potential secret or credential detected in log entry",
                    evidence=match.group(0)[:20] + "...",
                ))
                break

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

        return findings
