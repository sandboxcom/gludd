#!/usr/bin/env python3
"""Compliance report generator for the NF.6 OS Expert collection.

Consumes the structured findings produced by the ``*_audit.py`` backends
and the remediation metadata in :mod:`module_utils.hardening_guide` to emit
a multi-framework compliance report:

* **STIG** compliance status per STIG ID referenced in the knowledge base.
* **NIST SP 800-53** control mapping — findings grouped by control, each
  control annotated with its title and the highest-severity finding touching it.
* **CIS Benchmark** scores — per-benchmark pass/fail percentage computed
  against the set of CIS controls the knowledge base covers.
* **Remediation roadmap** — findings bucketed into three time-phased waves
  (immediate / short-term / long-term) by severity.

Pipeline::

    audit JSON  -->  generate_compliance_report(findings)  -->  ComplianceReport
                                                        |-> to_dict()                (JSON)
                                                        |-> format_compliance_markdown(...)  (human)

The report is deliberately scoped to the controls the OS Expert knowledge base
actually exercises. ``CIS-RHEL9`` "total controls" means "the subset of
CIS-RHEL9 controls this collection audits", not the full CIS benchmark
catalog. This is documented on :class:`CISScore`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from module_utils.hardening_guide import HARDENING_KB, SEVERITY_RANK

# Severity buckets for the remediation roadmap.
PHASE_BY_SEVERITY: dict[str, str] = {
    "critical": "immediate",
    "high": "immediate",
    "medium": "short_term",
    "low": "long_term",
    "info": "long_term",
}

PHASE_LABELS: dict[str, str] = {
    "immediate": "Immediate (0-7 days) — critical and high severity",
    "short_term": "Short term (1-4 weeks) — medium severity",
    "long_term": "Long term (1-3 months) — low and informational",
}

VALID_STIG_STATUSES = frozenset({"non_compliant", "not_assessed"})
VALID_CIS_STATUSES = frozenset({"pass", "fail"})

# NIST SP 800-53 control catalog excerpt — titles for the controls the KB
# references. Kept intentionally small; unknown controls fall back to a
# generic title rather than raising, so new references do not break the report.
NIST_800_53_TITLES: dict[str, str] = {
    "AC-3": "Access Enforcement",
    "AC-7": "Unsuccessful Logon Attempts",
    "AU-2": "Audit Events",
    "IA-5": "Authenticator Management",
    "IR-4": "Incident Handling",
    "SC-7": "Boundary Protection",
    "SC-30": "Concealment and Misdirection",
    "SI-2": "Flaw Remediation",
    "SI-3": "Malicious Code Protection",
}

NIST_FALLBACK_TITLE = "Security control (see NIST SP 800-53 for description)"

_STIG_RE = re.compile(r"STIG\s+([A-Z0-9][A-Z0-9-]+)")
_NIST_RE = re.compile(r"NIST SP 800-53\s+([A-Z]{2}-\d+(?:\(\d+\))?)")
_CIS_BENCHMARK_RE = re.compile(r"^(CIS-\S+)\s+(.+)$")


@dataclass
class StigStatus:
    """Compliance state of a single STIG control ID."""

    stig_id: str
    status: str
    finding_id: str | None
    severity: str


@dataclass
class NistControlMapping:
    """All findings that touch a single NIST SP 800-53 control."""

    control: str
    title: str
    finding_ids: list[str]
    severity: str


@dataclass
class CISControlStatus:
    """Pass/fail state of a single CIS benchmark control id."""

    benchmark: str
    control_id: str
    status: str
    finding_id: str | None


@dataclass
class CISScore:
    """Per-benchmark CIS compliance score.

    ``total_controls`` is the count of CIS controls the OS Expert knowledge
    base audits for this benchmark (not the full CIS catalog). ``score_percent``
    is therefore "fraction of audited controls that passed".
    """

    benchmark: str
    total_controls: int
    failed_controls: int
    passed_controls: int
    score_percent: float
    failed_control_ids: list[str]


@dataclass
class RemediationPhase:
    """One bucket of the remediation roadmap."""

    phase: str
    label: str
    finding_ids: list[str]
    total: int


@dataclass
class ComplianceReport:
    """The structured output of :func:`generate_compliance_report`."""

    stig: list[StigStatus]
    nist_800_53: list[NistControlMapping]
    cis_scores: list[CISScore]
    cis_controls: list[CISControlStatus]
    remediation_roadmap: list[RemediationPhase]
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return {
            "summary": self.summary,
            "stig": [asdict(s) for s in self.stig],
            "nist_800_53": [asdict(m) for m in self.nist_800_53],
            "cis_scores": [asdict(s) for s in self.cis_scores],
            "cis_controls": [asdict(c) for c in self.cis_controls],
            "remediation_roadmap": [asdict(p) for p in self.remediation_roadmap],
        }


def _extract_stig_ids(references: list[str]) -> list[str]:
    ids: list[str] = []
    for ref in references:
        m = _STIG_RE.search(ref)
        if m:
            ids.append(m.group(1))
    return ids


def _extract_nist_controls(references: list[str]) -> list[str]:
    controls: list[str] = []
    for ref in references:
        m = _NIST_RE.search(ref)
        if m:
            controls.append(m.group(1))
    return controls


def _parse_cis_control(cid: str) -> tuple[str, str] | None:
    m = _CIS_BENCHMARK_RE.match(cid)
    if not m:
        return None
    return m.group(1), cid  # (benchmark, full_control_id)


def _max_severity(severities: list[str]) -> str:
    if not severities:
        return "info"
    return min(severities, key=lambda s: SEVERITY_RANK.get(s, 99))


def generate_compliance_report(findings: list[dict[str, Any]]) -> ComplianceReport:
    """Translate audit findings into a multi-framework compliance report.

    Parameters
    ----------
    findings:
        List of finding dicts as emitted by ``assess_findings()`` in the
        ``*_audit.py`` modules. Each must carry an ``id`` to be matched
        against the hardening knowledge base.

    Returns
    -------
    ComplianceReport
        STIG status, NIST 800-53 mapping, per-benchmark CIS scores, and a
        phased remediation roadmap. The ``summary`` carries the overall CIS
        score and STIG compliance counts.
    """
    # Index findings by id (only those with an id and a KB entry count as matched)
    findings_by_id: dict[str, dict[str, Any]] = {}
    matched_ids: list[str] = []
    for finding in findings:
        fid = finding.get("id")
        if not fid:
            continue
        findings_by_id[fid] = finding
        if fid in HARDENING_KB:
            matched_ids.append(fid)

    triggered = set(matched_ids)

    # ---- STIG --------------------------------------------------------------
    # Walk every KB entry; a STIG id is non_compliant if its finding was
    # triggered, otherwise not_assessed (the control exists in scope but the
    # audit did not flag it).
    stig_rows: list[StigStatus] = []
    stig_seen: set[str] = set()
    for fid, tmpl in HARDENING_KB.items():
        refs = tmpl.get("references", [])
        for sid in _extract_stig_ids(refs):
            if sid in stig_seen:
                continue
            stig_seen.add(sid)
            if fid in triggered:
                f = findings_by_id[fid]
                stig_rows.append(StigStatus(
                    stig_id=sid,
                    status="non_compliant",
                    finding_id=fid,
                    severity=f.get("severity", "medium"),
                ))
            else:
                stig_rows.append(StigStatus(
                    stig_id=sid,
                    status="not_assessed",
                    finding_id=None,
                    severity="info",
                ))
    stig_rows.sort(key=lambda s: s.stig_id)

    # ---- NIST 800-53 -------------------------------------------------------
    control_to_findings: dict[str, list[str]] = {}
    control_to_severities: dict[str, list[str]] = {}
    for fid in matched_ids:
        tmpl = HARDENING_KB[fid]
        refs = tmpl.get("references", [])
        for ctrl in _extract_nist_controls(refs):
            control_to_findings.setdefault(ctrl, []).append(fid)
            control_to_findings[ctrl] = list(dict.fromkeys(control_to_findings[ctrl]))
            control_to_severities.setdefault(ctrl, []).append(
                findings_by_id[fid].get("severity", "medium")
            )
    nist_rows: list[NistControlMapping] = []
    for ctrl in sorted(control_to_findings):
        sev_list = control_to_severities[ctrl]
        nist_rows.append(NistControlMapping(
            control=ctrl,
            title=NIST_800_53_TITLES.get(ctrl, NIST_FALLBACK_TITLE),
            finding_ids=control_to_findings[ctrl],
            severity=_max_severity(sev_list),
        ))

    # ---- CIS ---------------------------------------------------------------
    # Build the universe of audited CIS controls per benchmark from the KB.
    benchmark_controls: dict[str, list[str]] = {}
    for fid, tmpl in HARDENING_KB.items():
        for cid in tmpl.get("cis_controls", []):
            parsed = _parse_cis_control(cid)
            if parsed is None:
                continue
            benchmark, _ = parsed
            bucket = benchmark_controls.setdefault(benchmark, [])
            if cid not in bucket:
                bucket.append(cid)

    cis_control_rows: list[CISControlStatus] = []
    failed_by_benchmark: dict[str, list[str]] = {}
    for benchmark in sorted(benchmark_controls):
        for cid in benchmark_controls[benchmark]:
            # Find the KB finding id(s) that carry this CIS control.
            owner_fid: str | None = None
            for fid, tmpl in HARDENING_KB.items():
                if cid in tmpl.get("cis_controls", []) and fid in triggered:
                    owner_fid = fid
                    break
            if owner_fid is not None:
                cis_control_rows.append(CISControlStatus(
                    benchmark=benchmark,
                    control_id=cid,
                    status="fail",
                    finding_id=owner_fid,
                ))
                failed_by_benchmark.setdefault(benchmark, []).append(cid)
            else:
                cis_control_rows.append(CISControlStatus(
                    benchmark=benchmark,
                    control_id=cid,
                    status="pass",
                    finding_id=None,
                ))
    cis_control_rows.sort(key=lambda c: (c.benchmark, c.control_id))

    cis_score_rows: list[CISScore] = []
    for benchmark in sorted(benchmark_controls):
        total = len(benchmark_controls[benchmark])
        failed = len(failed_by_benchmark.get(benchmark, []))
        passed = total - failed
        pct = round((passed / total * 100.0) if total else 100.0, 1)
        cis_score_rows.append(CISScore(
            benchmark=benchmark,
            total_controls=total,
            failed_controls=failed,
            passed_controls=passed,
            score_percent=pct,
            failed_control_ids=sorted(failed_by_benchmark.get(benchmark, [])),
        ))

    # ---- Remediation roadmap ----------------------------------------------
    phase_buckets: dict[str, list[str]] = {
        "immediate": [],
        "short_term": [],
        "long_term": [],
    }
    for fid in matched_ids:
        sev = findings_by_id[fid].get("severity", "medium")
        phase = PHASE_BY_SEVERITY.get(sev, "long_term")
        phase_buckets[phase].append(fid)
    roadmap = [
        RemediationPhase(
            phase=phase,
            label=PHASE_LABELS[phase],
            finding_ids=sorted(phase_buckets[phase]),
            total=len(phase_buckets[phase]),
        )
        for phase in ("immediate", "short_term", "long_term")
    ]

    # ---- Summary -----------------------------------------------------------
    stig_non = sum(1 for s in stig_rows if s.status == "non_compliant")
    stig_na = sum(1 for s in stig_rows if s.status == "not_assessed")
    if cis_score_rows:
        overall = round(sum(s.score_percent for s in cis_score_rows) / len(cis_score_rows), 1)
    else:
        overall = 100.0

    summary: dict[str, Any] = {
        "total_findings": sum(1 for f in findings if f.get("id")),
        "matched_findings": len(matched_ids),
        "stig_non_compliant": stig_non,
        "stig_not_assessed": stig_na,
        "nist_controls_touched": len(nist_rows),
        "overall_cis_score": overall,
    }

    return ComplianceReport(
        stig=stig_rows,
        nist_800_53=nist_rows,
        cis_scores=cis_score_rows,
        cis_controls=cis_control_rows,
        remediation_roadmap=roadmap,
        summary=summary,
    )


def format_compliance_markdown(report: ComplianceReport) -> str:
    """Render a :class:`ComplianceReport` as a human-readable Markdown report."""
    lines: list[str] = ["# Compliance Report", ""]

    if report.summary.get("total_findings", 0) == 0:
        lines.append("No audit findings — all in-scope controls passed.")
        lines.append("")
        return "\n".join(lines)

    s = report.summary
    lines.append(
        f"**Overall CIS score:** {s.get('overall_cis_score', 0.0)}%  "
        f"| **Findings:** {s.get('matched_findings', 0)} matched / "
        f"{s.get('total_findings', 0)} total"
    )
    lines.append("")

    # ---- STIG ----
    lines.append("## STIG Compliance Status")
    lines.append("")
    lines.append("| STIG ID | Status | Finding | Severity |")
    lines.append("|---------|--------|---------|----------|")
    for st in report.stig:
        lines.append(
            f"| {st.stig_id} | {st.status} | {st.finding_id or '—'} | {st.severity} |"
        )
    lines.append("")

    # ---- NIST 800-53 ----
    lines.append("## NIST SP 800-53 Mapping")
    lines.append("")
    if report.nist_800_53:
        lines.append("| Control | Title | Severity | Findings |")
        lines.append("|---------|-------|----------|----------|")
        for m in report.nist_800_53:
            lines.append(
                f"| {m.control} | {m.title} | {m.severity} | {', '.join(m.finding_ids)} |"
            )
    else:
        lines.append("_No NIST SP 800-53 controls touched by current findings._")
    lines.append("")

    # ---- CIS scores ----
    lines.append("## CIS Benchmark Scores")
    lines.append("")
    if report.cis_scores:
        lines.append("| Benchmark | Score | Passed | Failed | Total |")
        lines.append("|-----------|-------|--------|--------|-------|")
        for sc in report.cis_scores:
            lines.append(
                f"| {sc.benchmark} | {sc.score_percent}% | {sc.passed_controls} | "
                f"{sc.failed_controls} | {sc.total_controls} |"
            )
        lines.append("")
        lines.append("_Total reflects controls audited by the OS Expert collection, "
                     "not the full CIS benchmark catalog._")
    else:
        lines.append("_No CIS benchmark controls in scope._")
    lines.append("")

    # ---- Remediation roadmap ----
    lines.append("## Remediation Roadmap")
    lines.append("")
    for phase in report.remediation_roadmap:
        lines.append(f"### {phase.label}")
        lines.append("")
        if phase.finding_ids:
            for fid in phase.finding_ids:
                lines.append(f"- `{fid}`")
        else:
            lines.append("_No findings in this phase._")
        lines.append("")

    return "\n".join(lines)


__all__ = [
    "NIST_800_53_TITLES",
    "PHASE_BY_SEVERITY",
    "PHASE_LABELS",
    "VALID_CIS_STATUSES",
    "VALID_STIG_STATUSES",
    "CISControlStatus",
    "CISScore",
    "ComplianceReport",
    "NistControlMapping",
    "RemediationPhase",
    "StigStatus",
    "format_compliance_markdown",
    "generate_compliance_report",
]
