"""Backlog auditor — evidence-based completeness re-adjudication of tasks/todos.

Re-adjudicates every task that *claims* to be done (status complete/done) against
real evidence, instead of trusting the status field.  Three verdicts:

  FALSE_CLAIM       status says done but the claim is unsupported — there are no
                    evidence tests, a referenced touched-file is absent, or an
                    evidence test fails when re-run via the injected runner.
  INCOMPLETE        evidence tests pass and files exist, but a touched file still
                    carries a stub marker (NotImplementedError / TODO / FIXME /
                    ``pass  # stub`` / ``@pytest.mark.xfail``) or a stated
                    acceptance criterion is unmet.
  VERIFIED_COMPLETE evidence tests pass, every referenced file exists, and no
                    stub markers or unmet criteria remain.

The class is pure and dependency-injected: a ``test_runner`` callable
(``run(node_ids) -> {node_id: passed_bool}``) and a ``file_reader`` callable
(``read(path) -> str | None``, ``None`` meaning absent) are supplied at
construction.  Defaults wire up the real filesystem; tests inject fakes so the
suite stays hermetic.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from general_ludd.security.sanitize import confine_path_multi, workspace_roots

# Statuses that assert the work is finished and therefore get re-adjudicated.
_COMPLETED_STATUSES = frozenset({"complete", "completed", "done"})

# Substrings whose presence in a touched file signals unfinished work.
_STUB_MARKERS: tuple[str, ...] = (
    "raise NotImplementedError",
    "NotImplementedError",
    "TODO",
    "FIXME",
    "pass  # stub",
    "@pytest.mark.xfail",
)

# Verdict label constants (kept as plain strings for easy serialisation).
FALSE_CLAIM = "FALSE_CLAIM"
INCOMPLETE = "INCOMPLETE"
VERIFIED_COMPLETE = "VERIFIED_COMPLETE"

TestRunner = Callable[[list[str]], dict[str, bool]]
FileReader = Callable[[str], "str | None"]


@dataclass
class TaskVerdict:
    """Per-task adjudication outcome."""

    id: str
    verdict: str
    reasons: list[str] = field(default_factory=list)


@dataclass
class BacklogAuditReport:
    """Aggregate report over a re-adjudicated backlog."""

    total_audited: int = 0
    verified_complete: int = 0
    false_claim: int = 0
    incomplete: int = 0
    verdicts: list[TaskVerdict] = field(default_factory=list)


def _read_file_confined(path: str, roots: Sequence[str]) -> str | None:
    """Read text only after resolving ``path`` inside one allowed root."""
    confined = confine_path_multi(path, list(roots))
    if confined is None:
        return None
    if not os.path.isfile(confined):
        return None
    try:
        with open(confined, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _real_file_reader(path: str) -> str | None:
    """Default reader for callers that do not provide a repository root."""
    return _read_file_confined(path, workspace_roots())


class BacklogAuditor:
    """Re-adjudicate claimed-complete tasks against real evidence.

    Parameters
    ----------
    repo_root:
        Absolute path used to resolve relative ``touched_files`` entries.
    test_runner:
        Callable ``run(node_ids) -> {node_id: passed_bool}``.  Injected so unit
        tests never spawn a real pytest.
    file_reader:
        Callable ``read(path) -> str | None`` returning a file's text or ``None``
        when absent.  Defaults to the real filesystem.
    """

    def __init__(
        self,
        repo_root: str,
        test_runner: TestRunner,
        file_reader: FileReader | None = None,
    ) -> None:
        self._root = repo_root
        self._run_tests = test_runner
        if file_reader is not None:
            self._read = file_reader
        else:
            roots = workspace_roots(repo_root)
            self._read = lambda path: _read_file_confined(path, roots)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def audit(self, tasks: Sequence[dict[str, Any]]) -> BacklogAuditReport:
        """Audit a backlog, re-adjudicating only completed/done tasks."""
        report = BacklogAuditReport()
        for task in tasks:
            status = str(task.get("status", "")).strip().lower()
            if status not in _COMPLETED_STATUSES:
                continue
            verdict = self._adjudicate(task)
            report.verdicts.append(verdict)
            report.total_audited += 1
            if verdict.verdict == VERIFIED_COMPLETE:
                report.verified_complete += 1
            elif verdict.verdict == FALSE_CLAIM:
                report.false_claim += 1
            elif verdict.verdict == INCOMPLETE:
                report.incomplete += 1
        return report

    # ------------------------------------------------------------------
    # Adjudication
    # ------------------------------------------------------------------

    def _adjudicate(self, task: dict[str, Any]) -> TaskVerdict:
        task_id = str(task.get("id", "<unknown>"))
        evidence_ids: list[str] = list(task.get("evidence_test_ids") or [])
        touched: list[str] = list(task.get("touched_files") or [])
        criteria: list[str] = list(task.get("acceptance_criteria") or [])

        false_reasons: list[str] = []

        # 1. No evidence tests → the doneness claim is unsupported.
        if not evidence_ids:
            false_reasons.append("status claims done but there are no evidence tests")

        # 2. Referenced touched files must exist; cache their contents for the
        #    stub-marker pass so each file is read at most once.
        file_text: dict[str, str] = {}
        for rel in touched:
            abs_path = rel if os.path.isabs(rel) else os.path.join(self._root, rel)
            text = self._read(abs_path)
            if text is None:
                false_reasons.append(f"referenced file is absent/missing: {rel}")
            else:
                file_text[rel] = text

        # 3. Re-run evidence tests via the injected runner.
        if evidence_ids:
            results = self._run_tests(evidence_ids)
            failing = [nid for nid in evidence_ids if not results.get(nid, False)]
            for nid in failing:
                false_reasons.append(f"evidence test fails on re-run: {nid}")

        if false_reasons:
            return TaskVerdict(id=task_id, verdict=FALSE_CLAIM, reasons=false_reasons)

        # Evidence passes and all files exist — check for unfinished work.
        incomplete_reasons: list[str] = []

        for rel, text in file_text.items():
            for marker in _STUB_MARKERS:
                if marker in text:
                    incomplete_reasons.append(
                        f"touched file {rel} contains stub marker: {marker!r}"
                    )
                    break  # one marker per file is enough to flag it

        for crit in criteria:
            if not self._criterion_met(crit, file_text):
                incomplete_reasons.append(f"acceptance criterion unmet: {crit}")

        if incomplete_reasons:
            return TaskVerdict(id=task_id, verdict=INCOMPLETE, reasons=incomplete_reasons)

        return TaskVerdict(
            id=task_id,
            verdict=VERIFIED_COMPLETE,
            reasons=["evidence tests pass, files exist, no stub markers"],
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _criterion_met(criterion: str, file_text: dict[str, str]) -> bool:
        """A criterion ``must export <symbol>`` is met iff some touched file
        contains ``<symbol>``.  Criteria without that grammar are treated as
        satisfied (we cannot disprove free-text criteria, so fail-open for them
        but fail-closed for the structured ``must export`` form).
        """
        marker = "must export "
        idx = criterion.lower().find(marker)
        if idx == -1:
            return True
        symbol = criterion[idx + len(marker):].strip()
        if not symbol:
            return True
        return any(symbol in text for text in file_text.values())
