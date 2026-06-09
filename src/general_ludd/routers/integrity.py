from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI

from general_ludd.config.binary_paths import BinaryPathResolver
from general_ludd.integrity.scanner import FileIntegrityScanner, sign_change_openbao
from general_ludd.validation.gap_analyzer import GapAnalyzer
from general_ludd.validation.log_auditor import LogAuditor

_integrity_changes: list[dict[str, Any]] = []
_integrity_log: list[dict[str, Any]] = []


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:

    @app.post("/admin/integrity/scan")
    async def admin_integrity_scan(req: dict[str, Any] | None = None) -> dict[str, Any]:
        req = req or {}
        paths = req.get("paths", [])
        if not paths:
            import os as _os
            paths = [
                str(getattr(app.state, "_config_dir", "")),
                _os.path.expanduser("~/.config/gludd"),
                _os.path.expanduser("~/.local/share/general-ludd"),
            ]
            paths = [p for p in paths if p]
        scanner = FileIntegrityScanner()
        result = scanner.scan(paths, exclude_patterns=[r"\.pyc$", r"__pycache__", r"\.git/", r"\.db$"])
        _integrity_changes[:] = result["changes"]
        return result

    @app.get("/admin/integrity/report")
    async def admin_integrity_report() -> dict[str, Any]:
        return {"changes": _integrity_changes, "log_entries": len(_integrity_log)}

    @app.post("/admin/integrity/approve")
    async def admin_integrity_approve(req: dict[str, Any]) -> dict[str, Any]:
        result = sign_change_openbao(
            path=req.get("path", ""),
            signer=req.get("signer", "admin"),
            reason=req.get("reason", ""),
        )
        _integrity_log.append({
            "action": "approved",
            "path": req.get("path"),
            "reason": req.get("reason"),
            "signer": req.get("signer"),
            "timestamp": result.get("timestamp"),
            "signature": result.get("signature"),
        })
        return result

    @app.post("/admin/integrity/reject")
    async def admin_integrity_reject(req: dict[str, Any]) -> dict[str, Any]:
        _integrity_log.append({
            "action": "rejected",
            "path": req.get("path", ""),
            "reason": req.get("reason", ""),
            "signer": req.get("signer", "admin"),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        return {"path": req.get("path"), "status": "rejected"}

    @app.get("/admin/integrity/log")
    async def admin_integrity_log() -> dict[str, Any]:
        return {"entries": _integrity_log}

    @app.post("/admin/selftest")
    async def admin_selftest() -> dict[str, Any]:
        import subprocess

        resolver = BinaryPathResolver()
        podman_available = resolver.is_available("podman")

        results: list[dict[str, Any]] = []
        scenarios_run = 0
        scenarios_passed = 0
        errors: list[str] = []

        molecule_dir = "molecule/playbooks"
        import os

        if os.path.isdir(molecule_dir):
            for scenario in sorted(os.listdir(molecule_dir)):
                scenario_path = os.path.join(molecule_dir, scenario, "default")
                if not os.path.isdir(scenario_path):
                    continue
                if not podman_available and scenario in ("runtime_validate",):
                    results.append({
                        "scenario": scenario,
                        "passed": None,
                        "skipped": True,
                        "reason": "podman not available",
                    })
                    continue
                try:
                    result = subprocess.run(
                        ["uv", "run", "molecule", "test", "-s", scenario],
                        capture_output=True,
                        text=True,
                        timeout=300,
                        cwd=os.getcwd(),
                    )
                    scenarios_run += 1
                    passed = result.returncode == 0
                    if passed:
                        scenarios_passed += 1
                    results.append({
                        "scenario": scenario,
                        "passed": passed,
                        "returncode": result.returncode,
                    })
                    if not passed:
                        errors.append(f"{scenario}: {result.stderr[:200]}")
                except Exception as exc:
                    errors.append(f"{scenario}: {exc}")

        return {
            "success": len(errors) == 0,
            "podman_available": podman_available,
            "scenarios_run": scenarios_run,
            "scenarios_passed": scenarios_passed,
            "results": results,
            "errors": errors,
        }

    @app.post("/admin/gap-analysis")
    async def admin_gap_analysis(req: dict[str, Any] | None = None) -> dict[str, Any]:
        req = req or {}
        sprint_path = req.get("sprint_path", "")
        repo_root = req.get("repo_root", ".")
        analyzer = GapAnalyzer()
        report = analyzer.analyze(sprint_path=sprint_path, repo_root=repo_root)
        return {
            "total_gaps": report.total_gaps,
            "gaps": [
                {
                    "category": g.category,
                    "description": g.description,
                    "severity": g.severity,
                    "suggested_action": g.suggested_action,
                }
                for g in report.gaps
            ],
        }

    @app.post("/admin/log-audit")
    async def admin_log_audit(req: dict[str, Any] | None = None) -> dict[str, Any]:
        req = req or {}
        log_entries = req.get("log_entries", [])
        auditor = LogAuditor()
        report = auditor.audit_logs(log_entries)
        return {
            "total_findings": report.total_findings,
            "findings": [
                {
                    "severity": f.severity,
                    "category": f.category,
                    "description": f.description,
                    "evidence": f.evidence,
                }
                for f in report.findings
            ],
        }
