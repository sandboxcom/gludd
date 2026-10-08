"""Execute task-ledger evidence once and feed it to :class:`BacklogAuditor`.

Pytest is deliberately invoked once for the deduplicated evidence set.  The
result plugin records setup, call, and teardown outcomes per collected node so
collection errors, skipped tests, missing nodes, and incomplete phase reports
all fail closed.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Protocol

import pytest

from general_ludd.validation.backlog_auditor import BacklogAuditor, BacklogAuditReport
from general_ludd.validation.backlog_sources import (
    MAX_EVIDENCE_IDS,
    confined_file_reader,
    load_task_ledger,
)

EVIDENCE_TIMEOUT_SECONDS = 180


class BacklogExecutionError(ValueError):
    """Raised when evidence execution cannot be confined or bounded."""


class PytestMain(Protocol):
    """Callable shape used for the real and injected ``pytest.main`` entrypoint."""

    def __call__(
        self,
        args: list[str],
        *,
        plugins: list[object],
    ) -> int | pytest.ExitCode:
        """Invoke pytest with explicit arguments and result plugins."""
        ...


class EvidenceResultPlugin:
    """Record complete per-node pytest results without private pytest APIs."""

    def __init__(self) -> None:
        """Initialize bounded phase and collection-result stores."""
        self._phases: dict[str, dict[str, bool]] = {}
        self.results: dict[str, bool] = {}
        self.collection_errors: list[str] = []

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        """Retain bounded identifiers for any failed collection report."""
        if report.failed and len(self.collection_errors) < MAX_EVIDENCE_IDS:
            self.collection_errors.append(str(report.nodeid)[:1024])

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """Treat a node as passing only after all three phases pass."""
        if report.when not in {"setup", "call", "teardown"}:
            return
        phases = self._phases.setdefault(report.nodeid, {})
        phases[report.when] = bool(report.passed and not report.skipped)
        self.results[report.nodeid] = all(
            phases.get(phase, False) for phase in ("setup", "call", "teardown")
        )


def _resolved_repo_root(repo_root: str | Path) -> Path:
    try:
        root = Path(repo_root).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise BacklogExecutionError(
            f"repository root is unavailable: {repo_root}"
        ) from exc
    if not root.is_dir():
        raise BacklogExecutionError(f"repository root is not a directory: {root}")
    return root


def _normalize_node_id(root: Path, value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise BacklogExecutionError("evidence node ID is empty, non-text, or too long")
    if any(ord(char) < 32 for char in value):
        raise BacklogExecutionError("evidence node ID contains a control character")
    path_text, separator, selector = value.partition("::")
    path = PurePosixPath(path_text)
    if (
        not separator
        or not selector
        or path.is_absolute()
        or ".." in path.parts
        or path.suffix != ".py"
        or path_text.startswith("-")
    ):
        raise BacklogExecutionError(f"unsafe or malformed evidence node ID: {value}")
    candidate = root.joinpath(*path.parts)
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise BacklogExecutionError(
            f"evidence node ID escapes repository root: {value}"
        ) from exc
    return value


def _deduplicate_node_ids(root: Path, node_ids: Sequence[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in node_ids:
        node_id = _normalize_node_id(root, candidate)
        if node_id in seen:
            continue
        seen.add(node_id)
        unique.append(node_id)
    if len(unique) > MAX_EVIDENCE_IDS:
        raise BacklogExecutionError(
            f"evidence set exceeds {MAX_EVIDENCE_IDS} evidence ID limit"
        )
    return unique


def _requested_result(
    requested: str,
    recorded: dict[str, bool],
) -> bool:
    matching = [
        passed
        for node_id, passed in recorded.items()
        if node_id == requested
        or node_id.startswith(f"{requested}[")
        or node_id.startswith(f"{requested}::")
    ]
    return bool(matching) and all(matching)


def run_evidence_tests(
    repo_root: str | Path,
    node_ids: Sequence[str],
    *,
    pytest_main: PytestMain = pytest.main,
) -> dict[str, bool]:
    """Run a bounded, deduplicated evidence set in one serial pytest session."""
    root = _resolved_repo_root(repo_root)
    requested = _deduplicate_node_ids(root, node_ids)
    if not requested:
        return {}

    plugin = EvidenceResultPlugin()
    args = [
        "-q",
        "--tb=line",
        "--show-capture=no",
        f"--timeout={EVIDENCE_TIMEOUT_SECONDS}",
        "-o",
        "addopts=",
        "-p",
        "no:xdist",
        "--",
        *requested,
    ]
    print(
        "BACKLOG-EVIDENCE START "
        f"nodes={len(requested)} mode=serial timeout={EVIDENCE_TIMEOUT_SECONDS}s"
    )
    previous = Path.cwd()
    try:
        os.chdir(root)
        raw_exit_code = pytest_main(args, plugins=[plugin])
    except Exception as exc:  # fail closed at the pytest process boundary
        print(f"BACKLOG-EVIDENCE ERROR type={type(exc).__name__}")
        return dict.fromkeys(requested, False)
    finally:
        os.chdir(previous)

    try:
        exit_code = pytest.ExitCode(int(raw_exit_code))
    except (TypeError, ValueError):
        exit_code = pytest.ExitCode.INTERNAL_ERROR
    fatal_exit_codes = {
        pytest.ExitCode.INTERRUPTED,
        pytest.ExitCode.INTERNAL_ERROR,
        pytest.ExitCode.USAGE_ERROR,
        pytest.ExitCode.NO_TESTS_COLLECTED,
    }
    if plugin.collection_errors or exit_code in fatal_exit_codes:
        results = dict.fromkeys(requested, False)
    elif exit_code in {pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED}:
        results = {
            node_id: _requested_result(node_id, plugin.results)
            for node_id in requested
        }
    else:  # pragma: no cover - protects against a future pytest exit member
        results = dict.fromkeys(requested, False)

    for node_id, passed in results.items():
        state = "PASS" if passed else "FAIL"
        print(f"BACKLOG-EVIDENCE {state} {node_id}")
    print(
        "BACKLOG-EVIDENCE END "
        f"exit={int(exit_code)} collection_errors={len(plugin.collection_errors)}"
    )
    return results


def audit_task_ledger(
    repo_root: str | Path,
    *,
    tasks_path: str | Path | None = None,
    pytest_main: PytestMain = pytest.main,
) -> BacklogAuditReport:
    """Load checked tasks, execute all evidence once, and adjudicate claims."""
    root = _resolved_repo_root(repo_root)
    tasks = load_task_ledger(root, tasks_path=tasks_path)
    all_node_ids: list[str] = []
    for task in tasks:
        evidence = task.get("evidence_test_ids", [])
        if not isinstance(evidence, list) or not all(
            isinstance(node_id, str) for node_id in evidence
        ):
            raise BacklogExecutionError("task source returned malformed evidence IDs")
        all_node_ids.extend(evidence)
    results = run_evidence_tests(root, all_node_ids, pytest_main=pytest_main)

    def cached_runner(requested: list[str]) -> dict[str, bool]:
        return {node_id: results.get(node_id, False) for node_id in requested}

    auditor = BacklogAuditor(
        repo_root=str(root),
        test_runner=cached_runner,
        file_reader=confined_file_reader(root),
    )
    return auditor.audit(tasks)


__all__ = (
    "EVIDENCE_TIMEOUT_SECONDS",
    "BacklogExecutionError",
    "EvidenceResultPlugin",
    "PytestMain",
    "audit_task_ledger",
    "run_evidence_tests",
)
