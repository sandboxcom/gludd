"""Parse SAST tool JSON output into structured findings.

A ``project.yml`` can declare a security check (e.g. ``sast: semgrep --json …``
or ``sast-py: bandit -f json -r src``). :class:`~general_ludd.project_runner.runner.ProjectCommandRunner`
gates such a check on its exit code, but exit-code-only is coarse — semgrep/bandit
exit non-zero on ANY finding, with no severity or per-finding detail. These
best-effort parsers turn the tool's JSON into concise
``"SEVERITY file:line rule — message"`` strings that populate
``CheckResult.findings`` for severity-aware reporting.

Fail-soft by design: an unknown tool, non-JSON, or a schema surprise yields
``[]`` — never an exception (a SAST parse must never break a check run).
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["parse_findings", "summarize_findings"]

# argv[0] basenames whose JSON we know how to parse.
_SEMGREP = "semgrep"
_BANDIT = "bandit"
_ESLINT = "eslint"
_GOLANGCI_LINT = "golangci-lint"
_CARGO_AUDIT = "cargo-audit"
_TRIVY = "trivy"


def parse_findings(tool: str, stdout: str) -> list[str]:
    """Best-effort ``list[str]`` of findings from a SAST tool's JSON ``stdout``.

    ``tool`` is the argv[0] basename. Unknown tools, empty/non-JSON output,
    or a truncated document all return ``[]``.
    """
    text = (stdout or "").strip()
    if not text or not (text.startswith("{") or text.startswith("[")):
        return []
    try:
        doc = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # Truncated (the runner keeps only a stdout tail) or malformed → soft.
        return []
    name = (tool or "").lower()
    try:
        if name == _SEMGREP:
            return _parse_semgrep(doc) if isinstance(doc, dict) else []
        if name == _BANDIT:
            return _parse_bandit(doc) if isinstance(doc, dict) else []
        if name == _ESLINT:
            return _parse_eslint(doc)
        if name == _GOLANGCI_LINT:
            return _parse_golangci_lint(doc) if isinstance(doc, dict) else []
        if name == _CARGO_AUDIT:
            return _parse_cargo_audit(doc) if isinstance(doc, dict) else []
        if name == _TRIVY:
            return _parse_trivy(doc) if isinstance(doc, dict) else []
    except Exception:  # never let a schema surprise break a check run
        return []
    return []


def _parse_semgrep(doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for r in doc.get("results", []) or []:
        if not isinstance(r, dict):
            continue
        rule = r.get("check_id", "?")
        path = r.get("path", "?")
        line = ((r.get("start") or {}).get("line")) if isinstance(r.get("start"), dict) else None
        extra = r.get("extra") or {}
        sev = str(extra.get("severity", "INFO")).upper()
        msg = str(extra.get("message", "")).strip().replace("\n", " ")
        out.append(f"{sev} {path}:{line if line is not None else '?'} {rule} — {msg}")
    return out


def _parse_bandit(doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for r in doc.get("results", []) or []:
        if not isinstance(r, dict):
            continue
        rule = r.get("test_id", "?")
        path = r.get("filename", "?")
        line = r.get("line_number", "?")
        sev = str(r.get("issue_severity", "INFO")).upper()
        msg = str(r.get("issue_text", "")).strip().replace("\n", " ")
        out.append(f"{sev} {path}:{line} {rule} — {msg}")
    return out


def _parse_eslint(doc: list[Any]) -> list[str]:
    if not isinstance(doc, list):
        return []
    out: list[str] = []
    for file_entry in doc:
        if not isinstance(file_entry, dict):
            continue
        fp = file_entry.get("filePath", "?")
        for msg in file_entry.get("messages", []) or []:
            if not isinstance(msg, dict):
                continue
            rule = msg.get("ruleId", "?")
            sev_num = msg.get("severity", 1)
            sev = "ERROR" if sev_num >= 2 else "WARNING"
            line = msg.get("line", "?")
            text = str(msg.get("message", "")).strip().replace("\n", " ")
            out.append(f"{sev} {fp}:{line} {rule} — {text}")
    return out


def _parse_golangci_lint(doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    issues = doc.get("Issues", []) or []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        linter = issue.get("FromLinter", "?")
        pos = issue.get("Pos") or {}
        fp = pos.get("Filename", "?") if isinstance(pos, dict) else "?"
        line = pos.get("Line", "?") if isinstance(pos, dict) else "?"
        sev = str(issue.get("Severity", "warning")).upper()
        text = str(issue.get("Text", "")).strip().replace("\n", " ")
        out.append(f"{sev} {fp}:{line} {linter} — {text}")
    return out


def _parse_cargo_audit(doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    vulns = (doc.get("vulnerabilities") or {}).get("list", []) or []
    for v in vulns:
        if not isinstance(v, dict):
            continue
        advisory = v.get("advisory") or {}
        pkg = v.get("package") or {}
        aid = advisory.get("id", "?")
        pkg_name = pkg.get("name", "?")
        pkg_ver = pkg.get("version", "?")
        title = str(advisory.get("title", "")).strip().replace("\n", " ")
        cvss = advisory.get("cvss")
        sev = _cvss_severity(cvss) if isinstance(cvss, str) and cvss else "UNKNOWN"
        out.append(f"{sev} {pkg_name}@{pkg_ver} {aid} — {title}")
    return out


def _cvss_severity(cvss_str: str) -> str:
    if cvss_str.startswith("CVSS:"):
        return "UNKNOWN"
    m = re.search(r"(\d+\.\d+)", cvss_str)
    if m:
        score = float(m.group(1))
        if score >= 9.0:
            return "CRITICAL"
        if score >= 7.0:
            return "HIGH"
        if score >= 4.0:
            return "MEDIUM"
        return "LOW"
    return "UNKNOWN"


def _parse_trivy(doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for result in doc.get("Results", []) or []:
        if not isinstance(result, dict):
            continue
        target = result.get("Target", "?")
        for vuln in result.get("Vulnerabilities", []) or []:
            if not isinstance(vuln, dict):
                continue
            vid = vuln.get("VulnerabilityID", "?")
            sev = str(vuln.get("Severity", "UNKNOWN")).upper()
            pkg = vuln.get("PkgName", "?")
            title = str(vuln.get("Title", "")).strip().replace("\n", " ")
            out.append(f"{sev} {target}:{pkg} {vid} — {title}")
        for misc in result.get("Misconfigurations", []) or []:
            if not isinstance(misc, dict):
                continue
            mid = misc.get("ID", "?")
            sev = str(misc.get("Severity", "UNKNOWN")).upper()
            msg = str(misc.get("Message", "")).strip().replace("\n", " ")
            cause = misc.get("CauseMetadata") or {}
            line = cause.get("StartLine", "?") if isinstance(cause, dict) else "?"
            out.append(f"{sev} {target}:{line} {mid} — {msg}")
    return out


def summarize_findings(findings: list[str]) -> dict[str, int]:
    """Count findings by their leading SEVERITY token (e.g. ``{'HIGH': 2}``)."""
    counts: dict[str, int] = {}
    for f in findings:
        sev = f.split(" ", 1)[0] if f else "UNKNOWN"
        counts[sev] = counts.get(sev, 0) + 1
    return counts
