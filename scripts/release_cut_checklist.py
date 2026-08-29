#!/usr/bin/env python3
"""Observable, fail-closed preflight for the v0.1.0-beta.4 release cut.

The checklist composes existing Make release guards.  It deliberately owns no
second parser for Git status, gate state, release recipes, versions, or tags.

Exit codes:
  0 — every release precondition passed
  1 — one or more release blockers were found
  2 — required evidence could not be collected
  130 — the operator cancelled the checklist
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from security_audit_observability import PhaseResult, run_phase

EXIT_READY = 0
EXIT_BLOCKED = 1
EXIT_EVIDENCE_ERROR = 2
EXIT_CANCELLED = 130


class PhaseRunner(Protocol):
    """Callable boundary shared with the mature observable phase runner."""

    def __call__(
        self,
        *,
        name: str,
        command: list[str],
        heartbeat_seconds: float,
        timeout_seconds: float,
        sensitive: bool,
    ) -> PhaseResult: ...


@dataclass(frozen=True)
class PhaseSpec:
    """One bounded, read-only beta4 preflight phase."""

    title: str
    name: str
    command: tuple[str, ...]
    timeout_seconds: float
    heartbeat_seconds: float = 15.0


@dataclass
class Check:
    """Terminal result presented to the release operator."""

    name: str
    passed: bool = False
    detail: str = ""


@dataclass
class Checklist:
    """Aggregate beta4 release evidence."""

    tag: str
    checks: list[Check] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks) and not self.errors

    @property
    def blockers(self) -> list[str]:
        failed = [
            f"{check.name}: {check.detail}"
            for check in self.checks
            if not check.passed
        ]
        return [*failed, *self.errors]


def _make_phase(
    title: str,
    name: str,
    target: str,
    *variables: str,
    timeout_seconds: float,
) -> PhaseSpec:
    """Build a shell-free Make invocation with explicit bounds."""
    return PhaseSpec(
        title=title,
        name=name,
        command=("make", "--no-print-directory", target, *variables),
        timeout_seconds=timeout_seconds,
    )


def beta4_check_plan(tag: str) -> list[PhaseSpec]:
    """Return the canonical beta4 guard sequence without duplicating a parser."""
    tag_variable = f"TAG={tag}"
    return [
        _make_phase(
            "Release worktrees clean",
            "release-worktree",
            "release-worktree-guard",
            timeout_seconds=60,
        ),
        _make_phase(
            "Fresh immutable gate",
            "gate-fresh",
            "check-gate-fresh",
            timeout_seconds=60,
        ),
        _make_phase("Lint (ruff)", "lint", "lint", timeout_seconds=300),
        _make_phase(
            "Typecheck (mypy)",
            "typecheck",
            "typecheck",
            timeout_seconds=600,
        ),
        _make_phase(
            "Test collection",
            "test-collection",
            "collect-check",
            timeout_seconds=300,
        ),
        _make_phase(
            "README current",
            "readme-current",
            "check-readme-status",
            tag_variable,
            timeout_seconds=60,
        ),
        _make_phase(
            "Release dry run",
            "release-dry-run",
            "release-dry-run",
            tag_variable,
            timeout_seconds=1200,
        ),
        _make_phase(
            "Tag immutability",
            "tag-immutability",
            "check-tag-immutability",
            tag_variable,
            timeout_seconds=120,
        ),
    ]


def _evidence_error(spec: PhaseSpec, result: PhaseResult) -> str | None:
    if result.status == "timed_out" or result.exit_code == 124:
        return f"{spec.title}: timed out after {spec.timeout_seconds:g}s"
    if result.exit_code == 127:
        return f"{spec.title}: could not start required evidence command"
    return None


def run_checklist(
    tag: str,
    *,
    phase_runner: PhaseRunner = run_phase,
) -> Checklist:
    """Run every read-only phase with live output and bounded ownership."""
    checklist = Checklist(tag=tag)
    plan = beta4_check_plan(tag)
    if not plan:
        checklist.errors.append("beta4 checklist contains no checks")
        return checklist

    for spec in plan:
        try:
            result = phase_runner(
                name=spec.name,
                command=list(spec.command),
                heartbeat_seconds=spec.heartbeat_seconds,
                timeout_seconds=spec.timeout_seconds,
                sensitive=False,
            )
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            detail = f"{spec.title}: evidence collection failed: {exc}"
            checklist.errors.append(detail)
            checklist.checks.append(Check(spec.title, False, "evidence collection failed"))
            continue

        evidence_error = _evidence_error(spec, result)
        if evidence_error is not None:
            checklist.errors.append(evidence_error)
            checklist.checks.append(Check(spec.title, False, evidence_error.split(": ", 1)[1]))
            continue

        passed = result.status == "passed" and result.exit_code == 0
        detail = (
            f"passed in {result.elapsed_seconds:.3f}s"
            if passed
            else f"failed (exit {result.exit_code})"
        )
        checklist.checks.append(Check(spec.title, passed, detail))

    return checklist


def exit_code(checklist: Checklist) -> int:
    """Return the stable fail-closed terminal code."""
    if checklist.errors:
        return EXIT_EVIDENCE_ERROR
    return EXIT_READY if checklist.all_passed else EXIT_BLOCKED


def print_report(checklist: Checklist, human: bool = False) -> str:
    """Render the compact operator report retained for backward compatibility."""
    del human
    lines = [f"=== Beta4 Release-Cut Checklist for {checklist.tag} ===", ""]
    for check in checklist.checks:
        status = "PASS" if check.passed else "FAIL"
        lines.append(f"  [{status}] {check.name}: {check.detail}")
    for error in checklist.errors:
        lines.append(f"  [ERR]  {error}")
    lines.append("")
    lines.append("=" * 60)
    if checklist.all_passed:
        lines.append(
            f"READY FOR RELEASE-CUT: make release-cut TAG={checklist.tag} MSG='...'"
        )
    else:
        lines.append("BLOCKERS — fix these before release-cut:")
        lines.extend(f"  * {blocker}" for blocker in checklist.blockers)
    lines.append("=" * 60)
    return "\n".join(lines)


def _terminal_event(checklist: Checklist, code: int) -> str:
    status = "ready" if code == EXIT_READY else (
        "evidence_error" if code == EXIT_EVIDENCE_ERROR else "blocked"
    )
    return json.dumps(
        {
            "schema_version": 1,
            "event": "release_checklist_complete",
            "tag": checklist.tag,
            "status": status,
            "exit_code": code,
            "passed": sum(check.passed for check in checklist.checks),
            "failed": sum(not check.passed for check in checklist.checks),
            "evidence_errors": len(checklist.errors),
        },
        sort_keys=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("TAG", help="Target release tag, e.g. v0.1.0-beta.4")
    parser.add_argument(
        "--human",
        action="store_true",
        help="retain the human-readable operator report",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        checklist = run_checklist(args.TAG)
    except KeyboardInterrupt:
        print("RELEASE-CHECKLIST-CANCELLED", file=sys.stderr, flush=True)
        return EXIT_CANCELLED

    code = exit_code(checklist)
    print(_terminal_event(checklist, code), flush=True)
    print(print_report(checklist, human=args.human), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
