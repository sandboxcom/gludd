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
from typing import Any

__all__ = ["parse_findings", "summarize_findings"]

# argv[0] basenames whose JSON we know how to parse.
_SEMGREP = "semgrep"
_BANDIT = "bandit"


def parse_findings(tool: str, stdout: str) -> list[str]:
    """Best-effort ``list[str]`` of findings from a SAST tool's JSON ``stdout``.

    ``tool`` is the argv[0] basename (``semgrep``/``bandit``). Unknown tools,
    empty/non-JSON output, or a truncated document all return ``[]``.
    """
    text = (stdout or "").strip()
    if not text or not text.startswith("{"):
        return []
    try:
        doc = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # Truncated (the runner keeps only a stdout tail) or malformed → soft.
        return []
    if not isinstance(doc, dict):
        return []
    name = (tool or "").lower()
    try:
        if name == _SEMGREP:
            return _parse_semgrep(doc)
        if name == _BANDIT:
            return _parse_bandit(doc)
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


def summarize_findings(findings: list[str]) -> dict[str, int]:
    """Count findings by their leading SEVERITY token (e.g. ``{'HIGH': 2}``)."""
    counts: dict[str, int] = {}
    for f in findings:
        sev = f.split(" ", 1)[0] if f else "UNKNOWN"
        counts[sev] = counts.get(sev, 0) + 1
    return counts
