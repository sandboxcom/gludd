"""CLI subcommand: ``gludd spec-quality`` — behavioral spec quality audit.

Subcommands::

    gludd spec-quality audit [--json]                   Run full audit on spec entries
    gludd spec-quality check <spec_id> <body> [--json]  Check a single spec
    gludd spec-quality scan [--json]                    Scan codebase for enforcement refs
    gludd spec-quality rules [--json]                   List registered audit rules
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from general_ludd.governance.spec_quality_contracts import (
    AuditRule,
    RuleRegistry,
    SpecAuditor,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_registry() -> RuleRegistry:
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
            description="Checks enforcement is concrete",
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
            description="Flags placeholder enforcement",
            category="no_placeholder_enforcement",
        )
    )
    registry.add_rule(
        AuditRule(
            rule_id="R005",
            name="Behavior Measurable",
            description="Checks behavior has measurable outcomes",
            category="behavior_measurable",
            severity="warning",
        )
    )
    return registry


def _cmd_audit(args: argparse.Namespace) -> None:
    entries = _parse_entries_arg(args)
    registry = _make_registry()
    auditor = SpecAuditor(registry)
    report = auditor.audit(entries)

    result = {
        "total_findings": report.total_findings,
        "error_count": report.error_count,
        "warning_count": report.warning_count,
        "info_count": report.info_count,
        "has_errors": report.has_errors(),
        "unique_specs_checked": report.unique_specs_checked,
        "findings": [
            {
                "rule_id": f.rule_id,
                "spec_id": f.spec_id,
                "severity": f.severity,
                "message": f.message,
                "evidence": f.evidence,
            }
            for f in report.findings
        ],
    }

    if args.json:
        print(json.dumps(result, indent=2, default=str, sort_keys=True))
    else:
        print(f"Total findings: {report.total_findings}")
        print(f"  Errors:   {report.error_count}")
        print(f"  Warnings: {report.warning_count}")
        print(f"  Info:     {report.info_count}")
        print(f"  Specs checked: {report.unique_specs_checked}")
        if report.findings:
            print()
            for f in report.findings:
                print(f"  [{f.severity.upper()}] {f.spec_id} — {f.message}")

    if report.has_errors():
        sys.exit(1)


def _cmd_check(args: argparse.Namespace) -> None:
    spec_id = args.spec_id
    body = args.body
    registry = _make_registry()
    auditor = SpecAuditor(registry)
    report = auditor.audit([{"spec_id": spec_id, "body": body}])

    result = {
        "spec_id": spec_id,
        "total_findings": report.total_findings,
        "error_count": report.error_count,
        "warning_count": report.warning_count,
        "has_errors": report.has_errors(),
        "findings": [
            {
                "rule_id": f.rule_id,
                "severity": f.severity,
                "message": f.message,
            }
            for f in report.findings
        ],
    }

    if args.json:
        print(json.dumps(result, indent=2, default=str, sort_keys=True))
    else:
        if report.findings:
            print(f"FAIL: {spec_id} — {report.error_count} error(s), {report.warning_count} warning(s)")
            for f in report.findings:
                print(f"  [{f.severity.upper()}] {f.message}")
        else:
            print(f"PASS: {spec_id} — no findings")

    if report.has_errors():
        sys.exit(1)


def _cmd_scan(args: argparse.Namespace) -> None:
    registry = _make_registry()
    auditor = SpecAuditor(registry, repo_root=str(_REPO_ROOT))
    report = auditor.scan_codebase()

    result = {
        "total_findings": report.total_findings,
        "error_count": report.error_count,
        "warning_count": report.warning_count,
        "info_count": report.info_count,
        "has_errors": report.has_errors(),
        "unique_specs_checked": report.unique_specs_checked,
        "findings": [
            {
                "rule_id": f.rule_id,
                "spec_id": f.spec_id,
                "severity": f.severity,
                "message": f.message,
                "evidence": f.evidence,
            }
            for f in report.findings
        ],
    }

    if args.json:
        print(json.dumps(result, indent=2, default=str, sort_keys=True))
    else:
        print(f"Codebase scan: {report.total_findings} finding(s)")
        print(f"  Errors:   {report.error_count}")
        print(f"  Warnings: {report.warning_count}")
        print(f"  Info:     {report.info_count}")
        if report.findings:
            print()
            for f in report.findings:
                print(f"  [{f.severity.upper()}] {f.message}")

    if report.has_errors():
        sys.exit(1)


def _cmd_rules(args: argparse.Namespace) -> None:
    registry = _make_registry()
    rules = [
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

    if args.json:
        print(json.dumps({"count": len(rules), "rules": rules}, indent=2, default=str, sort_keys=True))
    else:
        print(f"Registered audit rules ({len(rules)}):")
        for r in rules:
            print(f"  [{r['rule_id']}] {r['name']} ({r['severity']}) — {r['description']}")


def _parse_entries_arg(args: argparse.Namespace) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    raw = getattr(args, "entries", None)
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        entries.append(
                            {
                                "spec_id": str(item.get("spec_id", "")),
                                "body": str(item.get("body", "")),
                            }
                        )
        except json.JSONDecodeError:
            print("ERROR: --entries must be valid JSON array of {spec_id, body} objects", file=sys.stderr)
            sys.exit(2)
    return entries


def add_spec_quality_subparser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    sq_parser = sub.add_parser(
        "spec-quality",
        help="Behavioral spec quality audit and enforcement reference checking",
    )
    sq_parser.set_defaults(func=None)
    sq_sub = sq_parser.add_subparsers(dest="spec_quality_command")

    audit_p = sq_sub.add_parser("audit", help="Run full spec quality audit")
    audit_p.add_argument("--entries", default=None, help="JSON array of {spec_id, body} objects")
    audit_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    audit_p.set_defaults(func=_cmd_audit)

    check_p = sq_sub.add_parser("check", help="Check a single spec entry")
    check_p.add_argument("spec_id", help="Spec identifier (e.g. AA001)")
    check_p.add_argument("body", help="Spec body text")
    check_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    check_p.set_defaults(func=_cmd_check)

    scan_p = sq_sub.add_parser("scan", help="Scan codebase for enforcement references")
    scan_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    scan_p.set_defaults(func=_cmd_scan)

    rules_p = sq_sub.add_parser("rules", help="List registered audit rules")
    rules_p.add_argument("--json", action="store_true", dest="json", help="Output as JSON")
    rules_p.set_defaults(func=_cmd_rules)


__all__ = [
    "add_spec_quality_subparser",
]
