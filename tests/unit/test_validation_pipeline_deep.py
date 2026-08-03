"""Deep validation pipeline tests — validator chaining, result aggregation,
skip conditions, error propagation, and report generation across the full
GapAnalyzer + BacklogAuditor + LogAuditor + ValidationRunner pipeline.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

from general_ludd.validation.backlog_auditor import (
    FALSE_CLAIM,
    INCOMPLETE,
    VERIFIED_COMPLETE,
    BacklogAuditor,
)
from general_ludd.validation.gap_analyzer import GapAnalyzer, GapReport
from general_ludd.validation.log_auditor import AuditReport, LogAuditor

# ---------------------------------------------------------------------------
# Pipeline result dataclass (tests exercise a conceptual pipeline that
# aggregates stage outputs into a single report)
# ---------------------------------------------------------------------------


@dataclass
class PipelineStageResult:
    name: str
    executed: bool = False
    skipped: bool = False
    skipped_reason: str = ""
    error: str | None = None
    output: Any = None


@dataclass
class PipelineReport:
    stages: list[PipelineStageResult] = field(default_factory=list)
    total_stages: int = 0
    executed_stages: int = 0
    skipped_stages: int = 0
    failed_stages: int = 0
    aggregated_findings: list[str] = field(default_factory=list)

    @property
    def all_stages_passed(self) -> bool:
        return self.failed_stages == 0 and self.executed_stages > 0


# ---------------------------------------------------------------------------
# Pipeline helper (test-local, not production code — exercises composition)
# ---------------------------------------------------------------------------


class _TestPipeline:
    """Orchestrates GapAnalyzer → BacklogAuditor → LogAuditor in sequence,
    with skip conditions and result aggregation, for deep pipeline testing."""

    def __init__(
        self,
        gap_analyzer: GapAnalyzer,
        backlog_auditor: BacklogAuditor | None,
        log_auditor: LogAuditor,
        *,
        skip_on_empty_gaps: bool = True,
        skip_on_empty_backlog: bool = True,
        stop_on_error: bool = False,
    ) -> None:
        self.gap_analyzer = gap_analyzer
        self.backlog_auditor = backlog_auditor
        self.log_auditor = log_auditor
        self.skip_on_empty_gaps = skip_on_empty_gaps
        self.skip_on_empty_backlog = skip_on_empty_backlog
        self.stop_on_error = stop_on_error

    def run(
        self,
        repo_root: str,
        tasks: list[dict[str, Any]],
        log_entries: list[dict[str, Any]],
    ) -> PipelineReport:
        report = PipelineReport()

        # --- Stage 1: Gap Analysis ---
        gap_stage = PipelineStageResult(name="gap_analysis")
        try:
            gap_result = self.gap_analyzer.analyze(sprint_path="sprint0", repo_root=repo_root)
            gap_stage.executed = True
            gap_stage.output = gap_result
            report.aggregated_findings.extend(f"gap:{g.category}:{g.description}" for g in gap_result.gaps)
        except Exception as exc:
            gap_stage.error = str(exc)
            report.failed_stages += 1
            report.stages.append(gap_stage)
            report.total_stages += 1
            if self.stop_on_error:
                return report

        report.stages.append(gap_stage)
        report.total_stages += 1
        if gap_stage.error:
            report.failed_stages += 1
        else:
            report.executed_stages += 1

        # --- Stage 2: Backlog Audit (skip if no completed tasks) ---
        backlog_stage = PipelineStageResult(name="backlog_audit")
        completed_tasks = [t for t in tasks if str(t.get("status", "")).lower() in {"complete", "completed", "done"}]

        if self.skip_on_empty_backlog and not completed_tasks:
            backlog_stage.skipped = True
            backlog_stage.skipped_reason = "no completed tasks in backlog"
            report.stages.append(backlog_stage)
            report.total_stages += 1
            report.skipped_stages += 1
        elif self.backlog_auditor is None:
            backlog_stage.skipped = True
            backlog_stage.skipped_reason = "backlog auditor unavailable"
            report.stages.append(backlog_stage)
            report.total_stages += 1
            report.skipped_stages += 1
        else:
            try:
                backlog_result = self.backlog_auditor.audit(tasks)
                backlog_stage.executed = True
                backlog_stage.output = backlog_result
                report.aggregated_findings.extend(f"backlog:{v.verdict}:{v.id}" for v in backlog_result.verdicts)
            except Exception as exc:
                backlog_stage.error = str(exc)

            report.stages.append(backlog_stage)
            report.total_stages += 1
            if backlog_stage.error:
                report.failed_stages += 1
                if self.stop_on_error:
                    return report
            else:
                report.executed_stages += 1

        # --- Stage 3: Log Audit (skip if prior gap analysis found nothing
        #     and skip_on_empty_gaps is set) ---
        log_stage = PipelineStageResult(name="log_audit")
        if self.skip_on_empty_gaps and isinstance(gap_stage.output, GapReport) and gap_stage.output.total_gaps == 0:
            log_stage.skipped = True
            log_stage.skipped_reason = "no gaps found — log audit skipped"
            report.stages.append(log_stage)
            report.total_stages += 1
            report.skipped_stages += 1
        else:
            try:
                log_result = self.log_auditor.audit_logs(log_entries)
                log_stage.executed = True
                log_stage.output = log_result
                report.aggregated_findings.extend(f"log:{f.category}" for f in log_result.findings)
            except Exception as exc:
                log_stage.error = str(exc)

            report.stages.append(log_stage)
            report.total_stages += 1
            if log_stage.error:
                report.failed_stages += 1
                if self.stop_on_error:
                    return report
            else:
                report.executed_stages += 1

        return report


# ---------------------------------------------------------------------------
# A. Validator chaining
# ---------------------------------------------------------------------------


class TestPipelineChainsAllThreeStages:
    def test_full_pipeline_runs_gap_backlog_log_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            src_dir = os.path.join(repo_root, "src", "pkg")
            os.makedirs(src_dir)
            with open(os.path.join(src_dir, "foo.py"), "w") as f:
                f.write("def bar(): pass\n")

            mock_runner = MagicMock(return_value={"test_x::test_one": True})

            pipeline = _TestPipeline(
                gap_analyzer=GapAnalyzer(),
                backlog_auditor=BacklogAuditor(
                    repo_root=repo_root,
                    test_runner=mock_runner,
                ),
                log_auditor=LogAuditor(),
            )
            report = pipeline.run(
                repo_root=repo_root,
                tasks=[{"id": "T1", "status": "complete", "evidence_test_ids": ["test_x::test_one"]}],
                log_entries=[{"event": "task", "correlation_id": "c1"}],
            )

            assert report.total_stages == 3
            names = [s.name for s in report.stages]
            assert names == ["gap_analysis", "backlog_audit", "log_audit"]
            assert report.executed_stages == 3
            assert report.skipped_stages == 0
            assert report.failed_stages == 0
            assert report.all_stages_passed


class TestPipelineStageOrderingIsPreserved:
    def test_stages_appear_in_registration_order_regardless_of_skip(self) -> None:
        pipeline = _TestPipeline(
            gap_analyzer=GapAnalyzer(),
            backlog_auditor=None,
            log_auditor=LogAuditor(),
            skip_on_empty_backlog=True,
            skip_on_empty_gaps=False,
        )
        with tempfile.TemporaryDirectory() as repo_root:
            report = pipeline.run(
                repo_root=repo_root,
                tasks=[{"id": "T1", "status": "pending"}],
                log_entries=[],
            )
            assert [s.name for s in report.stages] == ["gap_analysis", "backlog_audit", "log_audit"]
            assert report.skipped_stages == 1
            assert report.executed_stages == 2


# ---------------------------------------------------------------------------
# B. Result aggregation
# ---------------------------------------------------------------------------


class TestPipelineAggregatesFindingsAcrossStages:
    def test_gap_and_log_findings_are_aggregated(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            src_dir = os.path.join(repo_root, "src", "pkg")
            os.makedirs(src_dir)
            with open(os.path.join(src_dir, "unguarded.py"), "w") as f:
                f.write("def f(): pass\n")

            pipeline = _TestPipeline(
                gap_analyzer=GapAnalyzer(),
                backlog_auditor=None,
                log_auditor=LogAuditor(),
                skip_on_empty_backlog=True,
                skip_on_empty_gaps=False,
            )
            report = pipeline.run(
                repo_root=repo_root,
                tasks=[],
                log_entries=[
                    {"event": "x", "api_key": "sk-deadbeefdeadbeefdeadbeef12"},
                ],
            )

            gap_findings = [f for f in report.aggregated_findings if f.startswith("gap:")]
            log_findings = [f for f in report.aggregated_findings if f.startswith("log:")]
            assert len(gap_findings) >= 1
            assert len(log_findings) >= 1

    def test_backlog_verdicts_appear_in_aggregated_findings(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            runner = MagicMock(return_value={"t1": True, "t2": False})
            pipeline = _TestPipeline(
                gap_analyzer=GapAnalyzer(),
                backlog_auditor=BacklogAuditor(repo_root=repo_root, test_runner=runner),
                log_auditor=LogAuditor(),
            )
            report = pipeline.run(
                repo_root=repo_root,
                tasks=[
                    {"id": "A", "status": "complete", "evidence_test_ids": ["t1"]},
                    {"id": "B", "status": "complete", "evidence_test_ids": ["t2"]},
                ],
                log_entries=[],
            )
            backlog_findings = [f for f in report.aggregated_findings if f.startswith("backlog:")]
            verdicts = {f.split(":")[1] for f in backlog_findings}
            assert VERIFIED_COMPLETE in verdicts
            assert FALSE_CLAIM in verdicts


class TestPipelineReportCountersAreConsistent:
    def test_total_equals_executed_plus_skipped_plus_failed(self) -> None:
        pipeline = _TestPipeline(
            gap_analyzer=GapAnalyzer(),
            backlog_auditor=None,
            log_auditor=LogAuditor(),
            skip_on_empty_gaps=False,
        )
        with tempfile.TemporaryDirectory() as repo_root:
            report = pipeline.run(repo_root=repo_root, tasks=[], log_entries=[])
            assert report.total_stages == report.executed_stages + report.skipped_stages + report.failed_stages


# ---------------------------------------------------------------------------
# C. Skip conditions
# ---------------------------------------------------------------------------


class TestPipelineSkipsBacklogOnEmptyCompletedTasks:
    def test_no_completed_tasks_skips_backlog_audit(self) -> None:
        pipeline = _TestPipeline(
            gap_analyzer=GapAnalyzer(),
            backlog_auditor=BacklogAuditor(
                repo_root="/tmp",
                test_runner=MagicMock(),
            ),
            log_auditor=LogAuditor(),
        )
        with tempfile.TemporaryDirectory() as repo_root:
            report = pipeline.run(
                repo_root=repo_root,
                tasks=[
                    {"id": "T1", "status": "pending"},
                    {"id": "T2", "status": "in_progress"},
                    {"id": "T3", "status": "blocked"},
                ],
                log_entries=[],
            )
            backlog_stage = next(s for s in report.stages if s.name == "backlog_audit")
            assert backlog_stage.skipped is True
            assert "no completed tasks" in backlog_stage.skipped_reason

    def test_skip_on_empty_backlog_disabled_runs_anyway(self) -> None:
        runner = MagicMock(return_value={})
        pipeline = _TestPipeline(
            gap_analyzer=GapAnalyzer(),
            backlog_auditor=BacklogAuditor(repo_root="/tmp", test_runner=runner),
            log_auditor=LogAuditor(),
            skip_on_empty_backlog=False,
        )
        with tempfile.TemporaryDirectory() as repo_root:
            report = pipeline.run(
                repo_root=repo_root,
                tasks=[{"id": "T1", "status": "pending"}],
                log_entries=[],
            )
            backlog_stage = next(s for s in report.stages if s.name == "backlog_audit")
            assert backlog_stage.executed is True
            assert backlog_stage.skipped is False


class TestPipelineSkipsLogOnEmptyGaps:
    def test_zero_gaps_skips_log_audit_when_skip_enabled(self) -> None:
        pipeline = _TestPipeline(
            gap_analyzer=GapAnalyzer(),
            backlog_auditor=None,
            log_auditor=LogAuditor(),
            skip_on_empty_gaps=True,
        )
        with tempfile.TemporaryDirectory() as repo_root:
            report = pipeline.run(repo_root=repo_root, tasks=[], log_entries=[])
            log_stage = next(s for s in report.stages if s.name == "log_audit")
            assert log_stage.skipped is True
            assert "no gaps" in log_stage.skipped_reason

    def test_gaps_present_does_not_skip_log_audit(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            src_dir = os.path.join(repo_root, "src", "pkg")
            os.makedirs(src_dir)
            with open(os.path.join(src_dir, "bare.py"), "w") as f:
                f.write("def nope(): pass\n")

            pipeline = _TestPipeline(
                gap_analyzer=GapAnalyzer(),
                backlog_auditor=None,
                log_auditor=LogAuditor(),
                skip_on_empty_gaps=True,
            )
            report = pipeline.run(repo_root=repo_root, tasks=[], log_entries=[])
            log_stage = next(s for s in report.stages if s.name == "log_audit")
            assert log_stage.executed is True

    def test_skip_on_empty_gaps_disabled_runs_anyway(self) -> None:
        pipeline = _TestPipeline(
            gap_analyzer=GapAnalyzer(),
            backlog_auditor=None,
            log_auditor=LogAuditor(),
            skip_on_empty_gaps=False,
        )
        with tempfile.TemporaryDirectory() as repo_root:
            report = pipeline.run(repo_root=repo_root, tasks=[], log_entries=[])
            log_stage = next(s for s in report.stages if s.name == "log_audit")
            assert log_stage.executed is True


class TestPipelineSkipsMissingBacklogAuditor:
    def test_none_backlog_auditor_is_skipped(self) -> None:
        pipeline = _TestPipeline(
            gap_analyzer=GapAnalyzer(),
            backlog_auditor=None,
            log_auditor=LogAuditor(),
        )
        with tempfile.TemporaryDirectory() as repo_root:
            report = pipeline.run(
                repo_root=repo_root,
                tasks=[{"id": "T1", "status": "complete"}],
                log_entries=[],
            )
            backlog_stage = next(s for s in report.stages if s.name == "backlog_audit")
            assert backlog_stage.skipped is True
            assert "unavailable" in backlog_stage.skipped_reason


# ---------------------------------------------------------------------------
# D. Error propagation
# ---------------------------------------------------------------------------


class TestPipelineErrorPropagation:
    def test_gap_stage_error_sets_failed_stages(self) -> None:
        bad_analyzer = MagicMock(spec=GapAnalyzer)
        bad_analyzer.analyze.side_effect = RuntimeError("disk full")

        pipeline = _TestPipeline(
            gap_analyzer=bad_analyzer,
            backlog_auditor=None,
            log_auditor=LogAuditor(),
        )
        report = pipeline.run(repo_root="/tmp", tasks=[], log_entries=[])
        gap_stage = next(s for s in report.stages if s.name == "gap_analysis")
        assert gap_stage.error == "disk full"
        assert report.failed_stages >= 1
        assert not report.all_stages_passed

    def test_backlog_stage_error_does_not_block_log_audit(self) -> None:
        bad_runner = MagicMock(side_effect=RuntimeError("runner crashed"))

        pipeline = _TestPipeline(
            gap_analyzer=GapAnalyzer(),
            backlog_auditor=BacklogAuditor(repo_root="/tmp", test_runner=bad_runner),
            log_auditor=LogAuditor(),
            stop_on_error=False,
            skip_on_empty_gaps=False,
        )
        with tempfile.TemporaryDirectory() as repo_root:
            report = pipeline.run(
                repo_root=repo_root,
                tasks=[{"id": "T1", "status": "complete", "evidence_test_ids": ["t_x"]}],
                log_entries=[],
            )
            names = [s.name for s in report.stages]
            assert names == ["gap_analysis", "backlog_audit", "log_audit"]
            backlog_stage = next(s for s in report.stages if s.name == "backlog_audit")
            assert backlog_stage.error is not None
            assert "runner crashed" in backlog_stage.error
            log_stage = next(s for s in report.stages if s.name == "log_audit")
            assert log_stage.executed is True

    def test_stop_on_error_halts_pipeline_after_first_failure(self) -> None:
        bad_analyzer = MagicMock(spec=GapAnalyzer)
        bad_analyzer.analyze.side_effect = RuntimeError("first stage failed")

        pipeline = _TestPipeline(
            gap_analyzer=bad_analyzer,
            backlog_auditor=None,
            log_auditor=LogAuditor(),
            stop_on_error=True,
        )
        report = pipeline.run(repo_root="/tmp", tasks=[], log_entries=[])
        assert report.total_stages == 1
        assert report.failed_stages == 1

    def test_error_in_later_stage_captured_without_affecting_earlier(self) -> None:
        bad_logger = MagicMock(spec=LogAuditor)
        bad_logger.audit_logs.side_effect = RuntimeError("log parse error")

        with tempfile.TemporaryDirectory() as repo_root:
            pipeline = _TestPipeline(
                gap_analyzer=GapAnalyzer(),
                backlog_auditor=None,
                log_auditor=bad_logger,
                skip_on_empty_gaps=False,
                skip_on_empty_backlog=True,
            )
            report = pipeline.run(repo_root=repo_root, tasks=[], log_entries=[])
            gap_stage = next(s for s in report.stages if s.name == "gap_analysis")
            assert gap_stage.executed is True
            assert gap_stage.error is None
            log_stage = next(s for s in report.stages if s.name == "log_audit")
            assert log_stage.error == "log parse error"


# ---------------------------------------------------------------------------
# E. Report generation
# ---------------------------------------------------------------------------


class TestPipelineReportSummaries:
    def test_all_stages_passed_with_no_gaps_and_clean_logs(self) -> None:
        mock_runner = MagicMock(return_value={"t1": True})
        with tempfile.TemporaryDirectory() as repo_root:
            test_dir = os.path.join(repo_root, "tests")
            os.makedirs(test_dir)
            with open(os.path.join(test_dir, "test_foo.py"), "w") as f:
                f.write("def test_x(): pass\n")
            src_dir = os.path.join(repo_root, "src", "pkg")
            os.makedirs(src_dir)
            with open(os.path.join(src_dir, "foo.py"), "w") as f:
                f.write("def bar(): pass\n")

            pipeline = _TestPipeline(
                gap_analyzer=GapAnalyzer(),
                backlog_auditor=BacklogAuditor(repo_root=repo_root, test_runner=mock_runner),
                log_auditor=LogAuditor(),
                skip_on_empty_gaps=False,
            )
            report = pipeline.run(
                repo_root=repo_root,
                tasks=[{"id": "T1", "status": "complete", "evidence_test_ids": ["t1"]}],
                log_entries=[{"event": "ok", "correlation_id": "c1"}],
            )
            assert report.all_stages_passed
            assert report.executed_stages == 3

    def test_report_stages_include_skip_reason_when_applicable(self) -> None:
        pipeline = _TestPipeline(
            gap_analyzer=GapAnalyzer(),
            backlog_auditor=None,
            log_auditor=LogAuditor(),
        )
        with tempfile.TemporaryDirectory() as repo_root:
            report = pipeline.run(repo_root=repo_root, tasks=[], log_entries=[])
            for stage in report.stages:
                if stage.skipped:
                    assert stage.skipped_reason != ""

    def test_pipeline_report_stage_output_fields_are_typed(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            src_dir = os.path.join(repo_root, "src", "pkg")
            os.makedirs(src_dir)
            with open(os.path.join(src_dir, "bare.py"), "w") as f:
                f.write("def nope(): pass\n")

            pipeline = _TestPipeline(
                gap_analyzer=GapAnalyzer(),
                backlog_auditor=None,
                log_auditor=LogAuditor(),
            )
            report = pipeline.run(repo_root=repo_root, tasks=[], log_entries=[])
            gap_stage = next(s for s in report.stages if s.name == "gap_analysis")
            assert isinstance(gap_stage.output, GapReport)
            assert gap_stage.output.total_gaps >= 1
            log_stage = next(s for s in report.stages if s.name == "log_audit")
            assert isinstance(log_stage.output, AuditReport)


class TestPipelineReportIsImmuneToEmptyInputs:
    def test_empty_repo_empty_tasks_empty_logs(self) -> None:
        pipeline = _TestPipeline(
            gap_analyzer=GapAnalyzer(),
            backlog_auditor=None,
            log_auditor=LogAuditor(),
        )
        with tempfile.TemporaryDirectory() as repo_root:
            report = pipeline.run(repo_root=repo_root, tasks=[], log_entries=[])
            assert isinstance(report, PipelineReport)
            assert report.total_stages >= 1
            assert len(report.stages) == report.total_stages

    def test_multiple_skipped_stages_produce_valid_report(self) -> None:
        pipeline = _TestPipeline(
            gap_analyzer=GapAnalyzer(),
            backlog_auditor=None,
            log_auditor=LogAuditor(),
            skip_on_empty_gaps=True,
            skip_on_empty_backlog=True,
        )
        with tempfile.TemporaryDirectory() as repo_root:
            report = pipeline.run(repo_root=repo_root, tasks=[], log_entries=[])
            assert report.skipped_stages == 2
            assert report.executed_stages == 1
            assert report.all_stages_passed


# ---------------------------------------------------------------------------
# F. Cross-stage result dependency
# ---------------------------------------------------------------------------


class TestBacklogAuditDependsOnPriorStageOutput:
    def test_backlog_with_stub_marker_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            touched = os.path.join(repo_root, "src", "stub.py")
            os.makedirs(os.path.dirname(touched))
            with open(touched, "w") as f:
                f.write("def foo():\n    raise NotImplementedError\n")

            runner = MagicMock(return_value={"t1": True})
            auditor = BacklogAuditor(repo_root=repo_root, test_runner=runner)
            report = auditor.audit(
                [
                    {
                        "id": "S1",
                        "status": "complete",
                        "evidence_test_ids": ["t1"],
                        "touched_files": ["src/stub.py"],
                    }
                ]
            )
            assert report.total_audited == 1
            assert report.incomplete == 1
            assert report.verdicts[0].verdict == INCOMPLETE


class TestGapToBacklogTransitionWithRealFiles:
    def test_gap_found_then_backlog_verified(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            src_dir = os.path.join(repo_root, "src", "pkg")
            os.makedirs(src_dir)
            with open(os.path.join(src_dir, "solo.py"), "w") as f:
                f.write("def lonely(): pass\n")

            mk_runner = MagicMock(return_value={"t_x": True})
            pipeline = _TestPipeline(
                gap_analyzer=GapAnalyzer(),
                backlog_auditor=BacklogAuditor(repo_root=repo_root, test_runner=mk_runner),
                log_auditor=LogAuditor(),
            )
            report = pipeline.run(
                repo_root=repo_root,
                tasks=[
                    {
                        "id": "V1",
                        "status": "done",
                        "evidence_test_ids": ["t_x"],
                        "touched_files": ["src/pkg/solo.py"],
                    }
                ],
                log_entries=[],
            )
            gap_stage = next(s for s in report.stages if s.name == "gap_analysis")
            assert isinstance(gap_stage.output, GapReport)
            assert gap_stage.output.total_gaps >= 1

            backlog_findings = [f for f in report.aggregated_findings if f.startswith("backlog:")]
            assert any(VERIFIED_COMPLETE in f for f in backlog_findings)
