from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from general_ludd.governance.spec_quality_contracts import (
    AuditFinding,
    AuditRule,
    RuleRegistry,
    SpecAuditor,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _make_default_registry() -> RuleRegistry:
    registry = RuleRegistry()
    registry.add_rule(
        AuditRule(
            rule_id="R001",
            name="Enforcement Present",
            description="Checks that Enforcement field is present",
            category="enforcement_present",
        )
    )
    registry.add_rule(
        AuditRule(
            rule_id="R002",
            name="Concrete Enforcement",
            description="Checks enforcement is concrete (not manual review)",
            category="enforcement_concrete",
        )
    )
    registry.add_rule(
        AuditRule(
            rule_id="R003",
            name="Body Non-Empty",
            description="Checks spec body is not empty",
            category="body_non_empty",
        )
    )
    registry.add_rule(
        AuditRule(
            rule_id="R004",
            name="No Placeholder Enforcement",
            description="Flags placeholder enforcement (TBD, planned, etc.)",
            category="no_placeholder_enforcement",
        )
    )
    registry.add_rule(
        AuditRule(
            rule_id="R005",
            name="Behavior Measurable",
            description="Checks behavior has measurable outcomes, not advisory language",
            category="behavior_measurable",
            severity="warning",
        )
    )
    return registry


def _finding_to_dict(f: AuditFinding) -> dict[str, object]:
    return {
        "rule_id": f.rule_id,
        "spec_id": f.spec_id,
        "severity": f.severity,
        "message": f.message,
        "evidence": f.evidence,
        "line": f.line,
    }


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:

    @app.post("/api/spec-quality/audit")
    async def spec_quality_audit(req: dict[str, object] | None = None) -> dict[str, object]:
        entries: list[dict[str, object]] = []
        if req is not None:
            raw = req.get("entries")
            if isinstance(raw, list):
                entries = [
                    {"spec_id": str(e.get("spec_id", "")), "body": str(e.get("body", ""))}
                    for e in raw
                    if isinstance(e, dict)
                ]

        registry = _make_default_registry()
        auditor = SpecAuditor(registry)
        report = auditor.audit(entries)

        return {
            "total_findings": report.total_findings,
            "error_count": report.error_count,
            "warning_count": report.warning_count,
            "info_count": report.info_count,
            "unique_specs_checked": report.unique_specs_checked,
            "unique_rules_fired": report.unique_rules_fired,
            "rules_applied": report.rules_applied,
            "has_errors": report.has_errors(),
            "findings": [_finding_to_dict(f) for f in report.findings],
        }

    @app.post("/api/spec-quality/scan")
    async def spec_quality_scan(req: dict[str, object] | None = None) -> dict[str, object]:
        entries: list[dict[str, object]] = []
        check_paths: list[str] | None = None
        if req is not None:
            raw_entries = req.get("entries")
            if isinstance(raw_entries, list):
                entries = [
                    {"spec_id": str(e.get("spec_id", "")), "body": str(e.get("body", ""))}
                    for e in raw_entries
                    if isinstance(e, dict)
                ]
            raw_paths = req.get("check_paths")
            if isinstance(raw_paths, list):
                check_paths = [str(p) for p in raw_paths if isinstance(p, str)]

        registry = _make_default_registry()
        auditor = SpecAuditor(registry, repo_root=str(_REPO_ROOT))
        report = auditor.scan_codebase(entries=entries if entries else None, check_paths=check_paths)

        return {
            "total_findings": report.total_findings,
            "error_count": report.error_count,
            "warning_count": report.warning_count,
            "info_count": report.info_count,
            "unique_specs_checked": report.unique_specs_checked,
            "unique_rules_fired": report.unique_rules_fired,
            "rules_applied": report.rules_applied,
            "has_errors": report.has_errors(),
            "findings": [_finding_to_dict(f) for f in report.findings],
        }

    @app.get("/api/spec-quality/rules")
    async def spec_quality_rules() -> dict[str, object]:
        registry = _make_default_registry()
        rules: list[dict[str, object]] = [
            {
                "rule_id": r.rule_id,
                "name": r.name,
                "description": r.description,
                "category": r.category,
                "severity": r.severity,
                "active": r.active,
            }
            for r in registry.list_rules()
        ]
        return {"count": len(rules), "rules": rules}
