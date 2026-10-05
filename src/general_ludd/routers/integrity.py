"""Admin router for file-integrity scanning, sign-change, and log auditing."""

from __future__ import annotations

import os
import time
from typing import cast

from fastapi import FastAPI, HTTPException

from general_ludd.config.binary_paths import BinaryPathResolver
from general_ludd.integrity.fim_excludes import FIM_EXCLUDE_PATTERNS
from general_ludd.integrity.scanner import (
    FileIntegrityScanner,
    IntegrityKeyError,
    sign_change_openbao,
)
from general_ludd.security.sanitize import confine_path_multi
from general_ludd.security.state import project_state
from general_ludd.validation.gap_analyzer import GapAnalyzer
from general_ludd.validation.log_auditor import LogAuditor

# DoS cap: /admin/log-audit iterates a caller-supplied log_entries list with no
# size limit. Reject oversized input early with HTTP 413.
_MAX_LOG_AUDIT_ENTRIES = 10_000

_integrity_changes: list[dict[str, object]] = []
_integrity_log: list[dict[str, object]] = []


def _scan_roots(app: FastAPI) -> list[str]:
    """The directories an integrity scan is allowed to touch.

    A caller-supplied scan path must resolve inside one of these roots; anything
    else (e.g. ``/etc``, ``/``, ``~/.ssh``) is refused. Pure env/attr reads — no
    blocking I/O.

    The owner-only project state namespace is included for generated artifacts;
    the process-wide temporary directory is intentionally excluded.
    """
    state_root = project_state().project_dir
    roots = [
        os.getcwd(),
        os.environ.get("GLUDD_WORKSPACE_ROOT", ""),
        str(getattr(app.state, "_config_dir", "") or ""),
        os.path.expanduser("~/.config/gludd"),
        os.path.expanduser("~/.local/share/general-ludd"),
        str(state_root),
        os.path.realpath(state_root),
    ]
    return [r for r in roots if r]


def _confine_scan_paths(app: FastAPI, paths: list[object]) -> list[str]:
    """Validate each requested scan path lies inside an allowed root, else 422.

    Returns the real (resolved) canonical path for each accepted candidate so
    callers cannot bypass confinement via symlink swaps (TOCTOU).
    """
    roots = _scan_roots(app)
    confined: list[str] = []
    for raw in paths:
        p = str(raw)
        resolved = confine_path_multi(p, roots)
        if resolved is None:
            raise HTTPException(
                status_code=422,
                detail=f"scan path escapes the allowed roots: {p!r}",
            )
        confined.append(resolved)
    return confined


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    """Register the admin integrity-scan and log-audit endpoints."""

    @app.post("/admin/integrity/scan")
    async def admin_integrity_scan(req: dict[str, object] | None = None) -> dict[str, object]:
        req = req or {}
        raw_paths = req.get("paths", [])
        paths: list[str]
        if not raw_paths:
            # Trusted defaults — already the allowed roots, no confinement needed.
            paths = [
                str(getattr(app.state, "_config_dir", "")),
                os.path.expanduser("~/.config/gludd"),
                os.path.expanduser("~/.local/share/general-ludd"),
            ]
            paths = [p for p in paths if p]
        else:
            # AUTH-5: caller-supplied scan paths must stay inside an allowed root
            # so the endpoint can't be used to hash/exfiltrate arbitrary files.
            paths = _confine_scan_paths(app, cast(list[object], raw_paths))
        scanner = FileIntegrityScanner()
        # Shared canonical exclude set (see integrity.fim_excludes); one source
        # of truth so the scan sites cannot drift apart.
        exclude_patterns = list(FIM_EXCLUDE_PATTERNS)

        # Safety: if self-improve is enabled but a config OVERLAY (project
        # .gludd/ or user ~/.config/gludd) falls outside the scan scope,
        # agent-authored changes land there untracked — warn the operator.
        # Self-improve state comes from the loaded startup config on app.state.
        from general_ludd.integrity.overlay_guard import (
            resolve_self_improve_enabled,
            warn_if_overlay_unmonitored,
        )

        startup_config = getattr(app.state, "_startup_config", {}) or {}
        _uc = startup_config.get("user_config")
        _si_cfg = getattr(_uc, "self_improve", None)
        warn_if_overlay_unmonitored(paths, exclude_patterns, resolve_self_improve_enabled(_si_cfg))

        result = scanner.scan(paths, exclude_patterns=exclude_patterns)
        _integrity_changes[:] = cast(list[dict[str, object]], result.get("changes", []))
        return result

    @app.get("/admin/integrity/report")
    async def admin_integrity_report() -> dict[str, object]:
        return {"changes": _integrity_changes, "log_entries": len(_integrity_log)}

    @app.post("/admin/integrity/approve")
    async def admin_integrity_approve(req: dict[str, object]) -> dict[str, object]:
        # AUTH: the signed path must resolve inside an allowed root, else a
        # caller can sign/exfiltrate arbitrary files (e.g. /etc/passwd).
        raw_path = req.get("path", "")
        (path,) = _confine_scan_paths(app, [raw_path]) if raw_path else ("",)

        # HASH-BIND: look up the pending change for this path and verify that
        # the caller-supplied hashes match what was scanned.  An approval whose
        # hashes don't match a real scanned change is rejected so that an
        # approval cannot be replayed against a tampered version of the file.
        req_old_hash = cast(str | None, req.get("old_hash"))
        req_new_hash = cast(str | None, req.get("new_hash"))

        # Find the pending change record for this path (unapproved).
        matched_change = next(
            (c for c in _integrity_changes if c.get("file") == path and not c.get("approved", False)),
            None,
        )
        if matched_change is not None:
            # If hashes are provided in the request, they must match the scan.
            # If the caller omits them, pull from the scanned record so the
            # signature still covers the real hashes.
            if req_old_hash is not None and req_old_hash != matched_change.get("old_hash"):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"old_hash mismatch for {path!r}: "
                        f"request={req_old_hash!r} scan={matched_change.get('old_hash')!r}"
                    ),
                )
            if req_new_hash is not None and req_new_hash != matched_change.get("new_hash"):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"new_hash mismatch for {path!r}: "
                        f"request={req_new_hash!r} scan={matched_change.get('new_hash')!r}"
                    ),
                )
            sign_old_hash = cast(str | None, matched_change.get("old_hash"))
            sign_new_hash = cast(str | None, matched_change.get("new_hash"))
        else:
            # No pending scanned change found — use caller-supplied hashes (may
            # be None if caller doesn't know them; the signature will cover
            # "None" strings, which is intentional and detectable on verify).
            sign_old_hash = req_old_hash
            sign_new_hash = req_new_hash

        try:
            result = sign_change_openbao(
                path=path,
                signer=cast(str, req.get("signer", "admin")),
                reason=cast(str, req.get("reason", "")),
                old_hash=sign_old_hash,
                new_hash=sign_new_hash,
            )
        except IntegrityKeyError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"integrity signing unavailable: {exc}",
            ) from exc

        # Mark the change as approved in the pending-changes list.
        if matched_change is not None:
            matched_change["approved"] = True
            matched_change["signature"] = result.get("signature")

        _integrity_log.append(
            {
                "action": "approved",
                "path": path,
                "reason": req.get("reason"),
                "signer": req.get("signer"),
                "old_hash": sign_old_hash,
                "new_hash": sign_new_hash,
                "timestamp": result.get("timestamp"),
                "signature": result.get("signature"),
            }
        )
        return result

    @app.post("/admin/integrity/reject")
    async def admin_integrity_reject(req: dict[str, object]) -> dict[str, object]:
        # AUTH: confine the path the same way approve does so the integrity log
        # cannot be polluted with / referenced against out-of-root paths.
        raw_path = req.get("path", "")
        (path,) = _confine_scan_paths(app, [raw_path]) if raw_path else ("",)
        _integrity_log.append(
            {
                "action": "rejected",
                "path": path,
                "reason": req.get("reason", ""),
                "signer": req.get("signer", "admin"),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        )
        return {"path": path, "status": "rejected"}

    @app.get("/admin/integrity/log")
    async def admin_integrity_log() -> dict[str, object]:
        return {"entries": _integrity_log}

    @app.post("/admin/selftest")
    async def admin_selftest() -> dict[str, object]:
        import asyncio
        import subprocess

        resolver = BinaryPathResolver()
        podman_available = resolver.is_available("podman")

        results: list[dict[str, object]] = []
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
                    results.append(
                        {
                            "scenario": scenario,
                            "passed": None,
                            "skipped": True,
                            "reason": "podman not available",
                        }
                    )
                    continue
                try:
                    result = await asyncio.to_thread(
                        subprocess.run,
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
                    results.append(
                        {
                            "scenario": scenario,
                            "passed": passed,
                            "returncode": result.returncode,
                        }
                    )
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
    async def admin_gap_analysis(req: dict[str, object] | None = None) -> dict[str, object]:
        req = req or {}
        sprint_path = cast(str, req.get("sprint_path", ""))
        # AUTH: confine sprint_path the same way as repo_root so the analyzer
        # can't be pointed at arbitrary out-of-root files.
        if sprint_path:
            (sprint_path,) = _confine_scan_paths(app, [sprint_path])
        raw_root = cast(str, req.get("repo_root", ""))
        if raw_root and raw_root != ".":
            (repo_root,) = _confine_scan_paths(app, [raw_root])
        else:
            roots = _scan_roots(app)
            repo_root = roots[0] if roots else os.getcwd()
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
    async def admin_log_audit(req: dict[str, object] | None = None) -> dict[str, object]:
        req = req or {}
        log_entries = cast(list[dict[str, object]], req.get("log_entries", []))
        if len(log_entries) > _MAX_LOG_AUDIT_ENTRIES:
            raise HTTPException(
                status_code=413,
                detail="log_entries exceeds maximum allowed count",
            )
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
