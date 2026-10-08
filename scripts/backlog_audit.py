"""Backlog audit CLI — system-wide bug-class sweep + guard-coverage report.

Runs the :mod:`general_ludd.quality.bug_class_registry` sweep over the repo and
prints a VERBOSE report:

* per-bug-class occurrence list (every instance, system-wide — never a point
  fix),
* guard-coverage gaps (bug classes whose ``guard_test_id`` is not in the set of
  currently-collected pytest node ids), and
* executable per-task backlog verdicts loaded from the canonical ``TASKS.md``
  parser and supported by one bounded, serial pytest evidence run.

Exit code is non-zero if there are occurrences, guard gaps, false/incomplete
task claims, or a task-source/evidence execution error.  Operators can disable
only task verdict execution with ``--no-backlog-verdicts`` while retaining the
pre-existing bug-class and guard reports.

Stdlib + project registry only. Invoke via the integrator-added
``make backlog-audit`` target, or directly:

    python scripts/backlog_audit.py [--repo-root PATH] [--no-collect]
                                    [--no-backlog-verdicts]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

# Make both the canonical ``scripts`` parser and application package importable
# when this file is run directly from outside the checkout (no editable install).
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
for _IMPORT_ROOT in (_REPO_ROOT, _SRC):
    if str(_IMPORT_ROOT) not in sys.path:
        sys.path.insert(0, str(_IMPORT_ROOT))

from general_ludd.quality.bug_class_registry import (  # noqa: E402
    DEFAULT_BUG_CLASSES,
    BugClass,
    Occurrence,
    sweep,
    verify_guards,
)
from general_ludd.validation.backlog_audit import (  # noqa: E402
    BacklogExecutionError,
    audit_task_ledger,
)
from general_ludd.validation.backlog_sources import BacklogSourceError  # noqa: E402


def collect_test_ids(repo_root: Path) -> set[str]:
    """Return the set of currently-collected pytest node ids.

    Used to decide which bug classes actually have an active guard. On any
    failure (pytest missing, collection error) returns an empty set so the
    report degrades to "all guards look like gaps" rather than crashing — a
    fail-LOUD default for a coverage report.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "--co", "-q"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()

    ids: set[str] = set()
    for raw in proc.stdout.splitlines():
        line = raw.strip()
        # pytest --co -q prints node ids like "tests/unit/test_x.py::test_y".
        if line.startswith("tests") and "::" in line and not line.endswith(")"):
            ids.add(line)
    return ids


def _print_occurrences(
    classes: list[BugClass],
    occurrences: dict[str, list[Occurrence]],
) -> int:
    """Print the per-bug-class occurrence list. Returns total occurrence count."""
    by_id = {c.id: c for c in classes}
    total = 0
    print("=" * 72)
    print("BUG-CLASS SWEEP — every occurrence, system-wide (a fix is never a point fix)")
    print("=" * 72)
    for class_id in sorted(occurrences):
        hits = occurrences[class_id]
        bug_class = by_id.get(class_id)
        desc = bug_class.description if bug_class else ""
        print(f"\n[{class_id}] {len(hits)} occurrence(s)")
        print(f"    {desc}")
        if bug_class is not None:
            print(f"    remediation: {bug_class.remediation}")
        if not hits:
            print("    (clean — no occurrences)")
            continue
        total += len(hits)
        for path, lineno, line in hits:
            where = f"{path}:{lineno}" if lineno else f"{path} (file-level)"
            print(f"    - {where}: {line}")
    return total


def _print_guard_gaps(classes: list[BugClass], known_ids: set[str]) -> list[BugClass]:
    """Print the guard-coverage gaps. Returns the list of gap classes."""
    gaps = verify_guards(classes, known_ids)
    print("\n" + "=" * 72)
    print("GUARD COVERAGE — bug classes with NO active regression test (prevention gaps)")
    print("=" * 72)
    if not gaps:
        print("\nAll bug classes have an active guard test. No prevention gaps.")
        return gaps
    for bug_class in gaps:
        guard = bug_class.guard_test_id or "(none declared)"
        print(f"\n[{bug_class.id}] GUARD GAP")
        print(f"    guard_test_id: {guard}")
        print(f"    {bug_class.description}")
        print(f"    fix: {bug_class.remediation}")
    return gaps


def _print_backlog_verdicts(repo_root: Path, *, enabled: bool) -> bool:
    """Print task verdicts and return whether every checked claim is verified."""
    print("\n" + "=" * 72)
    print("BACKLOG VERDICTS")
    print("=" * 72)
    if not enabled:
        print(
            "\n(disabled by --no-backlog-verdicts; bug-class sweep and guard "
            "coverage remain enabled.)"
        )
        return True

    try:
        report = audit_task_ledger(repo_root, pytest_main=pytest.main)
    except (BacklogSourceError, BacklogExecutionError) as exc:
        print(f"\nBACKLOG AUDIT ERROR: {exc}")
        return False
    except Exception as exc:  # fail closed on unanticipated API/runtime drift
        print(f"\nBACKLOG AUDIT ERROR: unexpected {type(exc).__name__}: {exc}")
        return False

    if not report.verdicts:
        print("\n(No checked completion claims found.)")
    for verdict in report.verdicts:
        print(f"\n[{verdict.id}] {verdict.verdict}")
        for reason in verdict.reasons:
            print(f"    - {reason}")
    print(
        "\nBACKLOG SUMMARY: "
        f"audited={report.total_audited} "
        f"verified={report.verified_complete} "
        f"false_claim={report.false_claim} incomplete={report.incomplete}"
    )
    return report.false_claim == 0 and report.incomplete == 0


def run_report(
    repo_root: Path,
    *,
    collect: bool = True,
    classes: list[BugClass] | None = None,
    known_test_ids: set[str] | None = None,
    backlog_verdicts: bool = True,
) -> int:
    """Run the full verbose audit. Returns a process exit code.

    Non-zero when there are occurrences OR guard gaps, so the script can later
    be wired into a blocking gate.

    Parameters
    ----------
    classes:
        Override the seed bug classes (used by tests). Defaults to
        :data:`DEFAULT_BUG_CLASSES`.
    known_test_ids:
        Override the collected pytest node ids (used by tests). When provided,
        pytest collection is skipped entirely regardless of ``collect``.
    backlog_verdicts:
        Execute checked-task evidence. ``False`` is the narrow operational
        rollback and does not disable either existing report section.
    """
    classes = list(DEFAULT_BUG_CLASSES) if classes is None else list(classes)
    occurrences = sweep(repo_root, classes)
    total_occurrences = _print_occurrences(classes, occurrences)

    known_ids = (
        known_test_ids
        if known_test_ids is not None
        else collect_test_ids(repo_root) if collect else set()
    )
    if collect and known_test_ids is None and not known_ids:
        print(
            "\n(warning: could not collect pytest node ids — every guard will "
            "look like a gap. Run with a working test env for accurate guard "
            "coverage.)"
        )
    gaps = _print_guard_gaps(classes, known_ids)

    backlog_clean = _print_backlog_verdicts(repo_root, enabled=backlog_verdicts)

    print("\n" + "=" * 72)
    print(
        f"SUMMARY: {total_occurrences} occurrence(s) across "
        f"{sum(1 for v in occurrences.values() if v)} class(es); "
        f"{len(gaps)} guard gap(s)."
    )
    print("=" * 72)

    return 1 if (total_occurrences or gaps or not backlog_clean) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "System-wide bug-class sweep + guard-coverage + backlog verdicts."
        ),
    )
    parser.add_argument(
        "--repo-root",
        default=str(_REPO_ROOT),
        help="Repo root to sweep (default: this checkout).",
    )
    parser.add_argument(
        "--no-collect",
        action="store_true",
        help="Skip pytest --co (faster; every guard then shows as a gap).",
    )
    parser.add_argument(
        "--no-backlog-verdicts",
        action="store_true",
        help=(
            "Disable only TASKS.md verdict execution; retain bug-class and "
            "guard reports."
        ),
    )
    args = parser.parse_args(argv)

    return run_report(
        Path(args.repo_root),
        collect=not args.no_collect,
        backlog_verdicts=not args.no_backlog_verdicts,
    )


if __name__ == "__main__":
    raise SystemExit(main())
