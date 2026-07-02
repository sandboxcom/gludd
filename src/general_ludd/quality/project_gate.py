"""Run an EXTERNAL target project's quality gate via its ``project.yml``.

This is a SEPARATE entrypoint from gludd's own self-hosting gate
(:mod:`general_ludd.quality.preflight`, which hardcodes gludd's REPO_ROOT +
``uv run ruff``/``mypy``/coverage). Nothing here touches that behavior.

Where slice 2's ``run_project_check`` runs *one* declared check, slice 3's
:func:`run_project_gate` runs *several* and aggregates their
:class:`~general_ludd.project_runner.runner.CheckResult`\\ s into a single
pass/fail gate verdict: the gate PASSES only when every *required* check is
declared in the project's ``project.yml`` AND exits successfully. Undeclared
required checks fail the gate (fail-closed — a missing test/lint command must
not silently "pass").
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from general_ludd.project_runner import (
    ProjectCommandRunner,
    ProjectProfile,
    ProjectProfileError,
    load_project_profile,
)

logger = logging.getLogger(__name__)

# Sensible default gate for a target project when the caller doesn't pin an
# explicit check set. Both are treated as required (see ``required`` below).
DEFAULT_CHECKS: tuple[str, ...] = ("lint", "test")


def run_project_gate(
    workspace: str | Path,
    checks: tuple[str, ...] | list[str] = DEFAULT_CHECKS,
    *,
    required: tuple[str, ...] | list[str] | None = None,
    timeout_s: int | None = None,
    profile: ProjectProfile | None = None,
) -> dict[str, Any]:
    """Run each check in ``checks`` for the target project and aggregate a verdict.

    Args:
        workspace: Target project root (where ``project.yml`` lives).
        checks: Logical checks to run (keys of the profile ``commands`` map).
        required: Subset of ``checks`` that MUST pass for the gate to pass.
            Defaults to *all* of ``checks`` (fail-closed). Checks that are run
            but not required are advisory — they appear in the report but a
            failure does not fail the gate.
        timeout_s: Per-check timeout override forwarded to the runner.
        profile: Pre-loaded profile (mainly for testing); when omitted the
            profile is loaded from ``workspace/project.yml``.

    Returns:
        A structured report dict. Top-level keys:
        ``passed`` (bool gate verdict), ``overall`` ("PASS"/"FAIL"),
        ``project``, ``workspace``, ``requested`` (checks run), ``required``,
        ``checks`` (per-check result dicts), ``missing`` (required-but-undeclared
        checks), ``passed_count``, ``failed_count``. On a config error loading
        the profile the report carries ``passed=False`` + a top-level ``error``.
    """
    requested = list(checks)
    required_set = set(requested) if required is None else set(required)

    ws = Path(workspace)

    # Load/validate the profile fail-closed: a missing/invalid project.yml is a
    # gate FAILURE with a surfaced error, not an exception that aborts the caller.
    if profile is None:
        try:
            profile = load_project_profile(ws)
        except ProjectProfileError as exc:
            return {
                "passed": False,
                "overall": "FAIL",
                "project": None,
                "workspace": str(ws),
                "requested": requested,
                "required": sorted(required_set),
                "checks": [],
                "missing": sorted(required_set),
                "passed_count": 0,
                "failed_count": len(requested),
                "error": str(exc),
            }

    try:
        runner = ProjectCommandRunner(ws, profile)
    except ProjectProfileError as exc:
        return {
            "passed": False,
            "overall": "FAIL",
            "project": profile.name,
            "workspace": str(ws),
            "requested": requested,
            "required": sorted(required_set),
            "checks": [],
            "missing": sorted(required_set),
            "passed_count": 0,
            "failed_count": len(requested),
            "error": str(exc),
        }

    check_reports: list[dict[str, Any]] = []
    missing: list[str] = []
    gate_passed = True

    for check in requested:
        is_required = check in required_set

        if not profile.has(check):
            # Undeclared: a required missing check fails the gate; an advisory
            # missing check is merely reported.
            missing.append(check)
            check_reports.append({
                "name": check,
                "declared": False,
                "required": is_required,
                "passed": False,
                "exit_code": None,
                "duration_s": 0.0,
                "timed_out": False,
                "error": f"no '{check}' command declared in project.yml",
                "summary": f"{check}: MISSING (not declared in project.yml)",
            })
            if is_required:
                gate_passed = False
            continue

        try:
            result = runner.run(check, timeout_s=timeout_s)
        except ProjectProfileError as exc:
            # Unsafe/unparsable command (metachars, not allow-listed): a config
            # error → treat as a failed check rather than aborting the gate.
            check_reports.append({
                "name": check,
                "declared": True,
                "required": is_required,
                "passed": False,
                "exit_code": None,
                "duration_s": 0.0,
                "timed_out": False,
                "error": str(exc),
                "summary": f"{check}: ERROR — {exc}",
            })
            if is_required:
                gate_passed = False
            continue

        check_reports.append({
            "name": result.name,
            "declared": True,
            "required": is_required,
            "passed": result.passed,
            "exit_code": result.exit_code,
            "duration_s": round(result.duration_s, 3),
            "timed_out": result.timed_out,
            "error": result.error,
            "summary": result.summary(),
            "stdout_tail": result.stdout_tail,
            "stderr_tail": result.stderr_tail,
        })
        if is_required and not result.passed:
            gate_passed = False

    # Fail-CLOSED for required checks that were never in the run set. The loop
    # above only iterates ``requested`` (the checks actually run), so a name in
    # ``required`` but absent from ``checks`` would otherwise never execute,
    # never land in ``missing``, and never flip ``gate_passed`` — the gate would
    # PASS even though a REQUIRED check never ran. Treat any such required check
    # as missing/failed so a caller cannot silently drop a required check by
    # omitting it from the run set.
    run_set = set(requested)
    for check in sorted(required_set - run_set):
        missing.append(check)
        check_reports.append({
            "name": check,
            "declared": False,
            "required": True,
            "passed": False,
            "exit_code": None,
            "duration_s": 0.0,
            "timed_out": False,
            "error": (
                f"required check '{check}' was not in the run set "
                f"{sorted(run_set)} — it never executed (fail-closed)"
            ),
            "summary": f"{check}: MISSING (required but not in the run set)",
        })
        gate_passed = False

    passed_count = sum(1 for c in check_reports if c["passed"])
    failed_count = len(check_reports) - passed_count

    return {
        "passed": gate_passed,
        "overall": "PASS" if gate_passed else "FAIL",
        "project": profile.name,
        "workspace": str(ws),
        "requested": requested,
        "required": sorted(required_set),
        "checks": check_reports,
        "missing": missing,
        "passed_count": passed_count,
        "failed_count": failed_count,
    }
