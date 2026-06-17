"""Backlog audit CLI — system-wide bug-class sweep + guard-coverage report.

Runs the :mod:`general_ludd.quality.bug_class_registry` sweep over the repo and
prints a VERBOSE report:

* per-bug-class occurrence list (every instance, system-wide — never a point
  fix),
* guard-coverage gaps (bug classes whose ``guard_test_id`` is not in the set of
  currently-collected pytest node ids), and
* — *if* :class:`general_ludd.validation.backlog_auditor.BacklogAuditor` is
  importable — the per-task backlog verdicts.

The ``BacklogAuditor`` import is **lazy and guarded**: this script runs
standalone even when ``backlog_auditor.py`` does not exist yet (it is owned by
another agent). Exit code is non-zero if there are occurrences OR guard gaps,
so it can be wired into a gate later.

Stdlib + project registry only. Invoke via the integrator-added
``make backlog-audit`` target, or directly:

    python scripts/backlog_audit.py [--repo-root PATH] [--no-collect]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Make the registry importable when run as a bare script (no install). The repo
# root is two levels up from scripts/; src/ is the package root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from general_ludd.quality.bug_class_registry import (  # noqa: E402
    DEFAULT_BUG_CLASSES,
    BugClass,
    Occurrence,
    sweep,
    verify_guards,
)


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


def _print_backlog_verdicts(repo_root: Path) -> None:
    """Print BacklogAuditor task verdicts if the auditor is importable.

    Lazy, guarded import: another agent owns ``backlog_auditor.py`` and it may
    not exist yet. We never hard-depend on it.
    """
    print("\n" + "=" * 72)
    print("BACKLOG VERDICTS")
    print("=" * 72)
    try:
        from general_ludd.validation.backlog_auditor import (  # type: ignore  # noqa: PLC0415
            BacklogAuditor,
        )
    except ImportError:
        print(
            "\n(BacklogAuditor not importable yet — skipping task verdicts. "
            "The sweep + guard report above is standalone.)"
        )
        return

    try:
        auditor = BacklogAuditor(repo_root=repo_root)  # type: ignore[call-arg]
        verdicts = auditor.audit()  # type: ignore[attr-defined]
    except Exception as exc:  # pragma: no cover - depends on external module
        print(f"\n(BacklogAuditor present but failed to run: {exc!r})")
        return

    try:
        items = list(verdicts)
    except TypeError:  # pragma: no cover - unexpected shape
        print(f"\n{verdicts}")
        return

    if not items:
        print("\n(BacklogAuditor returned no verdicts.)")
        return
    for verdict in items:
        print(f"    - {verdict}")


def run_report(
    repo_root: Path,
    *,
    collect: bool = True,
    classes: list[BugClass] | None = None,
    known_test_ids: set[str] | None = None,
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
    """
    classes = list(DEFAULT_BUG_CLASSES) if classes is None else list(classes)
    occurrences = sweep(repo_root, classes)
    total_occurrences = _print_occurrences(classes, occurrences)

    if known_test_ids is not None:
        known_ids = known_test_ids
    else:
        known_ids = collect_test_ids(repo_root) if collect else set()
    if collect and known_test_ids is None and not known_ids:
        print(
            "\n(warning: could not collect pytest node ids — every guard will "
            "look like a gap. Run with a working test env for accurate guard "
            "coverage.)"
        )
    gaps = _print_guard_gaps(classes, known_ids)

    _print_backlog_verdicts(repo_root)

    print("\n" + "=" * 72)
    print(
        f"SUMMARY: {total_occurrences} occurrence(s) across "
        f"{sum(1 for v in occurrences.values() if v)} class(es); "
        f"{len(gaps)} guard gap(s)."
    )
    print("=" * 72)

    return 1 if (total_occurrences or gaps) else 0


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
    args = parser.parse_args(argv)

    return run_report(Path(args.repo_root), collect=not args.no_collect)


if __name__ == "__main__":
    raise SystemExit(main())
