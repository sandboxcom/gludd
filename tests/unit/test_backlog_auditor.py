"""Unit tests for BacklogAuditor — evidence-based completeness re-adjudication.

Every verdict path is proven with a fake test_runner and temp files so the
suite stays hermetic and fast (no real pytest, no real FS coupling beyond
tmp_path).
"""

from __future__ import annotations

from pathlib import Path

from general_ludd.validation.backlog_auditor import (
    BacklogAuditor,
    BacklogAuditReport,
    TaskVerdict,
)


def _fake_runner(results: dict[str, bool]):
    """Build a test_runner(node_ids) -> {node_id: passed_bool} from a fixed map.

    Any node id not present in ``results`` defaults to failing (fail-closed).
    """

    def run(node_ids: list[str]) -> dict[str, bool]:
        return {nid: results.get(nid, False) for nid in node_ids}

    return run


# ---------------------------------------------------------------------------
# VERIFIED_COMPLETE
# ---------------------------------------------------------------------------


def test_verified_complete_when_evidence_passes_files_exist_no_stubs(tmp_path: Path) -> None:
    src = tmp_path / "src" / "feature.py"
    src.parent.mkdir(parents=True)
    src.write_text("def feature() -> int:\n    return 42\n", encoding="utf-8")

    auditor = BacklogAuditor(
        repo_root=str(tmp_path),
        test_runner=_fake_runner({"tests/unit/test_feature.py::test_ok": True}),
    )
    report = auditor.audit(
        [
            {
                "id": "T-1",
                "status": "complete",
                "evidence_test_ids": ["tests/unit/test_feature.py::test_ok"],
                "touched_files": ["src/feature.py"],
            }
        ]
    )

    assert isinstance(report, BacklogAuditReport)
    assert report.verified_complete == 1
    assert report.false_claim == 0
    assert report.incomplete == 0
    verdict = report.verdicts[0]
    assert isinstance(verdict, TaskVerdict)
    assert verdict.id == "T-1"
    assert verdict.verdict == "VERIFIED_COMPLETE"


# ---------------------------------------------------------------------------
# FALSE_CLAIM
# ---------------------------------------------------------------------------


def test_false_claim_when_no_evidence_tests(tmp_path: Path) -> None:
    auditor = BacklogAuditor(
        repo_root=str(tmp_path),
        test_runner=_fake_runner({}),
    )
    report = auditor.audit(
        [{"id": "T-2", "status": "done", "touched_files": []}]
    )

    assert report.false_claim == 1
    verdict = report.verdicts[0]
    assert verdict.verdict == "FALSE_CLAIM"
    assert any("no evidence" in r.lower() for r in verdict.reasons)


def test_false_claim_when_evidence_test_fails(tmp_path: Path) -> None:
    src = tmp_path / "ok.py"
    src.write_text("x = 1\n", encoding="utf-8")

    auditor = BacklogAuditor(
        repo_root=str(tmp_path),
        test_runner=_fake_runner({"tests/test_x.py::test_x": False}),
    )
    report = auditor.audit(
        [
            {
                "id": "T-3",
                "status": "complete",
                "evidence_test_ids": ["tests/test_x.py::test_x"],
                "touched_files": ["ok.py"],
            }
        ]
    )

    assert report.false_claim == 1
    verdict = report.verdicts[0]
    assert verdict.verdict == "FALSE_CLAIM"
    assert any("fail" in r.lower() for r in verdict.reasons)


def test_false_claim_when_referenced_file_absent(tmp_path: Path) -> None:
    auditor = BacklogAuditor(
        repo_root=str(tmp_path),
        test_runner=_fake_runner({"tests/test_y.py::test_y": True}),
    )
    report = auditor.audit(
        [
            {
                "id": "T-4",
                "status": "complete",
                "evidence_test_ids": ["tests/test_y.py::test_y"],
                "touched_files": ["does/not/exist.py"],
            }
        ]
    )

    assert report.false_claim == 1
    verdict = report.verdicts[0]
    assert verdict.verdict == "FALSE_CLAIM"
    assert any("absent" in r.lower() or "missing" in r.lower() for r in verdict.reasons)


# ---------------------------------------------------------------------------
# INCOMPLETE
# ---------------------------------------------------------------------------


def test_incomplete_when_touched_file_has_stub_marker(tmp_path: Path) -> None:
    src = tmp_path / "stub.py"
    src.write_text("def thing():\n    raise NotImplementedError\n", encoding="utf-8")

    auditor = BacklogAuditor(
        repo_root=str(tmp_path),
        test_runner=_fake_runner({"tests/test_s.py::test_s": True}),
    )
    report = auditor.audit(
        [
            {
                "id": "T-5",
                "status": "complete",
                "evidence_test_ids": ["tests/test_s.py::test_s"],
                "touched_files": ["stub.py"],
            }
        ]
    )

    assert report.incomplete == 1
    verdict = report.verdicts[0]
    assert verdict.verdict == "INCOMPLETE"
    assert any("stub" in r.lower() or "notimplemented" in r.lower() for r in verdict.reasons)


def test_incomplete_detects_each_stub_marker_variant(tmp_path: Path) -> None:
    markers = [
        "raise NotImplementedError\n",
        "# TODO: finish\n",
        "# FIXME later\n",
        "pass  # stub\n",
        "@pytest.mark.xfail\n",
    ]
    tasks = []
    runner_map = {}
    for i, marker in enumerate(markers):
        fname = f"m{i}.py"
        (tmp_path / fname).write_text(f"x = 1\n{marker}", encoding="utf-8")
        node = f"tests/test_m{i}.py::test_m{i}"
        runner_map[node] = True
        tasks.append(
            {
                "id": f"M-{i}",
                "status": "complete",
                "evidence_test_ids": [node],
                "touched_files": [fname],
            }
        )

    auditor = BacklogAuditor(repo_root=str(tmp_path), test_runner=_fake_runner(runner_map))
    report = auditor.audit(tasks)

    assert report.incomplete == len(markers)
    assert all(v.verdict == "INCOMPLETE" for v in report.verdicts)


# ---------------------------------------------------------------------------
# Non-completed statuses are skipped
# ---------------------------------------------------------------------------


def test_non_completed_tasks_are_skipped(tmp_path: Path) -> None:
    auditor = BacklogAuditor(repo_root=str(tmp_path), test_runner=_fake_runner({}))
    report = auditor.audit(
        [
            {"id": "B-1", "status": "backlog"},
            {"id": "A-1", "status": "active"},
        ]
    )

    assert report.verdicts == []
    assert report.total_audited == 0


def test_complete_and_done_statuses_both_audited(tmp_path: Path) -> None:
    auditor = BacklogAuditor(repo_root=str(tmp_path), test_runner=_fake_runner({}))
    report = auditor.audit(
        [
            {"id": "C-1", "status": "complete"},
            {"id": "D-1", "status": "done"},
            {"id": "X-1", "status": "Done"},  # case-insensitive
        ]
    )

    assert report.total_audited == 3


# ---------------------------------------------------------------------------
# Injectable file reader (purity)
# ---------------------------------------------------------------------------


def test_injected_file_reader_is_used_instead_of_real_fs(tmp_path: Path) -> None:
    reads: list[str] = []

    def fake_reader(path: str) -> str | None:
        reads.append(path)
        # Pretend the file exists and is clean
        return "clean = True\n"

    auditor = BacklogAuditor(
        repo_root=str(tmp_path),
        test_runner=_fake_runner({"n::t": True}),
        file_reader=fake_reader,
    )
    report = auditor.audit(
        [
            {
                "id": "I-1",
                "status": "complete",
                "evidence_test_ids": ["n::t"],
                "touched_files": ["virtual/only.py"],
            }
        ]
    )

    assert reads, "injected reader must be consulted"
    assert report.verified_complete == 1


# ---------------------------------------------------------------------------
# Acceptance criteria unmet → INCOMPLETE
# ---------------------------------------------------------------------------


def test_unmet_acceptance_criteria_marks_incomplete(tmp_path: Path) -> None:
    src = tmp_path / "ac.py"
    src.write_text("def ac():\n    return 1\n", encoding="utf-8")

    auditor = BacklogAuditor(
        repo_root=str(tmp_path),
        test_runner=_fake_runner({"tests/test_ac.py::test_ac": True}),
    )
    report = auditor.audit(
        [
            {
                "id": "AC-1",
                "status": "complete",
                "acceptance_criteria": ["must export ac", "must export MISSING_SYMBOL"],
                "evidence_test_ids": ["tests/test_ac.py::test_ac"],
                "touched_files": ["ac.py"],
            }
        ]
    )

    assert report.incomplete == 1
    verdict = report.verdicts[0]
    assert verdict.verdict == "INCOMPLETE"
    assert any("criteri" in r.lower() for r in verdict.reasons)


# ---------------------------------------------------------------------------
# Report aggregation across mixed verdicts
# ---------------------------------------------------------------------------


def test_report_aggregates_mixed_verdicts(tmp_path: Path) -> None:
    good = tmp_path / "good.py"
    good.write_text("ok = 1\n", encoding="utf-8")
    stub = tmp_path / "bad.py"
    stub.write_text("raise NotImplementedError\n", encoding="utf-8")

    auditor = BacklogAuditor(
        repo_root=str(tmp_path),
        test_runner=_fake_runner(
            {"t::good": True, "t::fail": False, "t::stub": True}
        ),
    )
    report = auditor.audit(
        [
            {
                "id": "G",
                "status": "done",
                "evidence_test_ids": ["t::good"],
                "touched_files": ["good.py"],
            },
            {
                "id": "F",
                "status": "done",
                "evidence_test_ids": ["t::fail"],
                "touched_files": ["good.py"],
            },
            {
                "id": "S",
                "status": "done",
                "evidence_test_ids": ["t::stub"],
                "touched_files": ["bad.py"],
            },
        ]
    )

    assert report.total_audited == 3
    assert report.verified_complete == 1
    assert report.false_claim == 1
    assert report.incomplete == 1
    by_id = {v.id: v.verdict for v in report.verdicts}
    assert by_id == {
        "G": "VERIFIED_COMPLETE",
        "F": "FALSE_CLAIM",
        "S": "INCOMPLETE",
    }
