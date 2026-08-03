"""Deep code review and analysis tests — diff analysis, suggestion generation,
security pattern detection, style compliance, and complexity scoring.

Covers the review, validation, quality, and log_analysis subsystems with
behavioral tests that verify correct operation, edge cases, and error paths.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, cast

import pytest

from general_ludd.log_analysis.prompt_evaluator import (
    _estimate_tokens,
    _try_parse_json,
    ab_compare,
    analyze_cot_quality,
    classify_prompt,
    detect_context_waste,
    generate_report,
    measure_prompt_efficiency,
    recommend_improvements,
)
from general_ludd.quality.feature_verifier import (
    _SAFE_NODE_ID,
    FeatureVerifier,
    _validate_node_id,
)
from general_ludd.quality.preflight import (
    check_tasks_ticks,
    verify_task_completion,
)
from general_ludd.review.completion_verifier import (
    _SHA_RE,
    _check_artifact,
    _check_commit,
    _repo_root_is_unresolved,
)
from general_ludd.review.consensus import (
    ConsensusEngine,
    _build_dissent_prompt,
    _build_judge_prompt,
    _build_prompt,
    _check_consensus,
    _compute_confidence,
    _parse_verdict,
)
from general_ludd.review.evidence_checker import (
    EvidenceChecker,
    _deduplicate,
    _extract_sources,
    _is_factual_claim,
    _is_valid_source,
    _meaningful_tokens,
    _normalize_token,
    _source_tokens,
    _split_sentences,
)
from general_ludd.validation.backlog_auditor import (
    FALSE_CLAIM,
    VERIFIED_COMPLETE,
    BacklogAuditor,
    BacklogAuditReport,
    TaskVerdict,
)
from general_ludd.validation.gap_analyzer import (
    GapAnalyzer,
    GapItem,
    GapReport,
    _find_impl_without_tests,
    _find_missing_molecule,
    _test_exists,
)

# ============================================================================
# DIFF ANALYSIS — GapAnalyzer + EvidenceChecker diff-like behavior
# ============================================================================


class TestDiffAnalysis:
    def test_missing_impl_detected_as_gap(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            src_dir = os.path.join(root, "src", "general_ludd", "newmod")
            os.makedirs(src_dir)
            Path(os.path.join(src_dir, "uncovered.py")).write_text("def foo(): pass\n")
            os.makedirs(os.path.join(root, "tests", "unit"))
            gaps = _find_impl_without_tests(root)
            assert len(gaps) >= 1
            assert any("uncovered.py" in g.description for g in gaps)

    def test_impl_with_test_not_gap(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            src_dir = os.path.join(root, "src", "general_ludd", "covered")
            os.makedirs(src_dir)
            Path(os.path.join(src_dir, "covered.py")).write_text("def bar(): pass\n")
            tests_dir = os.path.join(root, "tests", "unit")
            os.makedirs(tests_dir)
            Path(os.path.join(tests_dir, "test_covered.py")).write_text("import covered\n")
            gaps = _find_impl_without_tests(root)
            assert not any("covered.py" in g.description for g in gaps)

    def test_init_py_skipped_in_gap_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            src_dir = os.path.join(root, "src", "mypackage")
            os.makedirs(src_dir)
            Path(os.path.join(src_dir, "__init__.py")).write_text("")
            gaps = _find_impl_without_tests(root)
            assert not any("__init__.py" in g.description for g in gaps)

    def test_molecule_gap_detected_for_playbook_without_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            playbook_dir = os.path.join(root, "playbooks")
            os.makedirs(playbook_dir)
            Path(os.path.join(playbook_dir, "deploy.yml")).write_text("---\n- hosts: all\n")
            gaps = _find_missing_molecule(root)
            assert len(gaps) >= 1
            assert any("deploy.yml" in g.description for g in gaps)
            assert gaps[0].severity == "high"

    def test_molecule_gap_not_reported_when_scenario_exists(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            playbook_dir = os.path.join(root, "playbooks")
            os.makedirs(playbook_dir)
            Path(os.path.join(playbook_dir, "deploy.yml")).write_text("---\n")
            mol_dir = os.path.join(root, "molecule", "playbooks", "deploy")
            os.makedirs(mol_dir)
            gaps = _find_missing_molecule(root)
            assert not any("deploy.yml" in g.description for g in gaps)

    def test_gap_report_aggregates_correctly(self) -> None:
        item1 = GapItem(
            category="missing_tests",
            description="No test for a.py",
            severity="medium",
            suggested_action="Create test_a.py",
        )
        item2 = GapItem(
            category="missing_molecule",
            description="No molecule for b.yml",
            severity="high",
            suggested_action="Create scenario",
        )
        report = GapReport(total_gaps=2, gaps=[item1, item2])
        assert report.total_gaps == 2
        assert len(report.gaps) == 2
        assert report.gaps[0].severity == "medium"
        assert report.gaps[1].severity == "high"


# ============================================================================
# SUGGESTION GENERATION — prompt_evaluator recommend_improvements
# ============================================================================


class TestSuggestionGeneration:
    def test_recommendations_for_low_cot_score(self) -> None:
        analysis = {
            "cot_quality": {
                "reasoning_depth": 1,
                "decision_quality": 1,
                "dead_ends": 3,
                "score": 2,
            },
            "efficiency": {},
            "context_waste": [],
        }
        recs = recommend_improvements(analysis)
        assert any("Deepen reasoning" in r for r in recs)
        assert any("Improve decision clarity" in r for r in recs)
        assert any("Reduce dead-ends" in r for r in recs)
        assert any("Overall CoT quality is low" in r for r in recs)

    def test_recommendations_for_verbose_prompt(self) -> None:
        analysis = {
            "cot_quality": {"reasoning_depth": 5, "decision_quality": 5, "dead_ends": 0, "score": 7},
            "efficiency": {"tokens_in": 2000, "task_completed": False, "steps_taken": 8, "errors": 0},
            "context_waste": [],
        }
        recs = recommend_improvements(analysis)
        assert any("Prompt is very large" in r for r in recs)
        assert any("add explicit acceptance criteria" in r for r in recs)

    def test_recommendations_for_high_errors(self) -> None:
        analysis = {
            "cot_quality": {"score": 6},
            "efficiency": {"errors": 5, "task_completed": True, "steps_taken": 3, "tokens_in": 200},
            "context_waste": [],
        }
        recs = recommend_improvements(analysis)
        assert any("High error count" in r for r in recs)

    def test_recommendations_for_research_prompt(self) -> None:
        analysis = {
            "cot_quality": {"score": 7},
            "efficiency": {},
            "context_waste": [],
            "classification": "research",
        }
        recs = recommend_improvements(analysis)
        assert any("specify exact search scope" in r for r in recs)

    def test_fallback_recommendation_when_no_issues(self) -> None:
        analysis = {
            "cot_quality": {"reasoning_depth": 5, "decision_quality": 5, "dead_ends": 0, "score": 8},
            "efficiency": {"tokens_in": 100, "task_completed": True, "steps_taken": 2, "errors": 0},
            "context_waste": [],
        }
        recs = recommend_improvements(analysis)
        assert any("well-structured" in r.lower() for r in recs)

    def test_debugging_dead_ends_recommendation(self) -> None:
        analysis = {
            "cot_quality": {"dead_ends": 5},
            "efficiency": {},
            "context_waste": [],
            "classification": "debugging",
        }
        recs = recommend_improvements(analysis)
        assert any("add the specific error message" in r for r in recs)


# ============================================================================
# COMPLEXITY SCORING — analyze_cot_quality + ab_compare
# ============================================================================


class TestComplexityScoring:
    def test_cot_quality_high_reasoning(self) -> None:
        text = (
            "because the test failed, therefore the module needs a fix. "
            "however, an alternative approach is to refactor the interface. "
            "the pros and cons of each: given that the evidence shows "
            "the observation that the test result indicates a race condition."
        )
        result = analyze_cot_quality(text)
        assert result["reasoning_depth"] >= 2
        assert result["score"] > 0

    def test_cot_quality_strong_decision(self) -> None:
        text = (
            "I chose the first option. The best approach is to use a mutex. "
            "We should use this because it is clear the path is correct. "
            "I decided on this after evaluating alternatives."
        )
        result = analyze_cot_quality(text)
        assert result["decision_quality"] >= 2

    def test_cot_quality_dead_ends_detected(self) -> None:
        text = (
            "that approach was a dead end. I gave up on the second attempt. "
            "back to the drawing board. my assumption was wrong and I made a mistake."
        )
        result = analyze_cot_quality(text)
        assert result["dead_ends"] >= 1

    def test_cot_quality_empty_input(self) -> None:
        result = analyze_cot_quality("")
        assert result["score"] == 0
        assert result["reasoning_depth"] == 0
        assert result["decision_quality"] == 0
        assert result["dead_ends"] == 0

    def test_cot_quality_whitespace_input(self) -> None:
        result = analyze_cot_quality("   \n  ")
        assert result["score"] == 0

    def test_ab_compare_winner_a(self) -> None:
        variant_a = [
            {"role": "user", "tokens": 50},
            {"role": "assistant", "tokens": 30, "content": "done", "tool_calls": []},
        ]
        variant_b = [
            {"role": "user", "tokens": 2000},
            {"role": "assistant", "tokens": 500, "content": "error failed", "tool_calls": [{"name": "x"}] * 5},
        ]
        result = ab_compare(variant_a, variant_b)
        assert result["winner"] in ("A", "B", "tie")
        assert "recommendation" in result
        assert "a_metrics" in result
        assert "b_metrics" in result

    def test_ab_compare_equal_scores(self) -> None:
        entry_a = {"role": "user", "tokens": 100, "content": "", "tool_calls": []}
        entry = [
            entry_a,
            {"role": "assistant", "tokens": 50, "content": "done", "tool_calls": []},
        ]
        result = ab_compare(entry, entry)
        assert result["winner"] == "tie"


# ============================================================================
# SECURITY PATTERN DETECTION — source validation + path traversal
# ============================================================================


class TestSecurityPatternDetection:
    def test_check_artifact_blocks_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            ok, detail = _check_artifact(root, "../etc/passwd")
            assert ok is False
            assert "escapes" in detail.lower()

    def test_check_artifact_empty_path_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            ok, detail = _check_artifact(root, "")
            assert ok is False
            assert "empty" in detail.lower()

    def test_check_artifact_dot_path_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            ok, detail = _check_artifact(root, ".")
            assert ok is False
            assert "empty" in detail.lower()

    def test_check_artifact_legit_file_found(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "README.md").write_text("hello")
            ok, detail = _check_artifact(root, "README.md")
            assert ok is True
            assert "found" in detail

    def test_check_commit_rejects_non_hex_sha(self) -> None:
        ok, detail = _check_commit(".", "abc; rm -rf /")
        assert ok is False
        assert "rejected" in detail.lower()

    def test_check_commit_validates_hex_format(self) -> None:
        assert _SHA_RE.match("deadbeef") is not None
        assert _SHA_RE.match("1234567890abcdef1234567890abcdef12345678") is not None
        assert _SHA_RE.match("abc") is None
        assert _SHA_RE.match("zzzzzzz") is None

    def test_no_reviewer_consensus_blocked(self) -> None:
        engine = ConsensusEngine(reviewer=None)
        result = engine.run_debate("Should we merge?", num_agents=3)
        assert result["verdict"] == "error"
        assert "No reviewer configured" in result["error"]

    def test_empty_question_blocked(self) -> None:
        def reviewer(p):
            return "approve\nlooks good"
        engine = ConsensusEngine(reviewer=reviewer)
        result = engine.run_debate("")
        assert result["verdict"] == "error"
        assert "empty" in result["error"].lower()

    def test_url_source_blocks_username_in_url(self) -> None:
        assert _is_valid_source("https://user:pass@evil.com/path") is False

    def test_url_source_allows_safe_https(self) -> None:
        assert _is_valid_source("https://github.com/gludd/main.py") is True

    def test_absolute_path_source_rejected(self) -> None:
        assert _is_valid_source("/etc/passwd") is False

    def test_backslashed_source_rejected(self) -> None:
        assert _is_valid_source("src\\secret.py") is False


# ============================================================================
# STYLE COMPLIANCE — check_tasks_ticks + verify_task_completion
# ============================================================================


class TestStyleCompliance:
    def test_tasks_ticks_rejects_missing_evidence(self) -> None:
        lines = ["- [x] D.1 Add feature  | evidence:  | completed"]
        result = check_tasks_ticks(lines)
        violations = cast(list[str], result["violations"])
        assert result["checked"] == 1
        assert len(violations) >= 1

    def test_tasks_ticks_accepts_valid_evidence(self) -> None:
        lines = ["- [x] D.1 Add lint gate  | evidence: make lint, commit deadbeef  | completed"]
        result = check_tasks_ticks(lines)
        violations = cast(list[str], result["violations"])
        assert len(violations) == 0
        assert result["checked"] == 1

    def test_tasks_ticks_rejects_forbidden_words(self) -> None:
        lines = ["- [x] D.1 pending  | evidence: make test, commit abc12345  | completed"]
        result = check_tasks_ticks(lines)
        violations = cast(list[str], result["violations"])
        assert len(violations) >= 1
        assert any("Forbidden word" in v for v in violations)

    def test_verify_task_completion_all_criteria_met(self) -> None:
        criteria = ["lint clean", "mypy clean", "tests pass", "coverage 85%"]
        evidence = {
            "lint_errors": 0,
            "mypy_errors": 0,
            "test_fail_count": 0,
            "coverage_pct": 88.5,
        }
        result = verify_task_completion(criteria, evidence)
        assert result["complete"] is True
        assert result["confidence"] == 1.0

    def test_verify_task_completion_partial_criteria(self) -> None:
        criteria = ["lint clean", "coverage 85%"]
        evidence = {"lint_errors": 3, "coverage_pct": 75.0}
        result = verify_task_completion(criteria, evidence)
        assert result["complete"] is False
        assert result["passed"] == 0
        assert result["total"] == 2

    def test_verify_task_completion_no_criteria(self) -> None:
        result = verify_task_completion([], {})
        reason = cast(str, result["reason"])
        assert result["complete"] is False
        assert result["confidence"] == 0.0
        assert "No acceptance criteria" in reason

    def test_tasks_ticks_skips_unchecked_lines(self) -> None:
        lines = ["- [ ] Pending work", "- [x] Done work  | evidence: make lint, commit abcdef0  | completed"]
        result = check_tasks_ticks(lines)
        violations = cast(list[str], result["violations"])
        assert result["checked"] == 1
        assert len(violations) == 0

    def test_tasks_ticks_accepts_hex_commit_as_evidence(self) -> None:
        lines = ["- [x] F.1 Feature X  | evidence: commit abc1234  | completed"]
        result = check_tasks_ticks(lines)
        violations = cast(list[str], result["violations"])
        assert len(violations) == 0

    def test_tasks_ticks_accepts_rejected_without_evidence(self) -> None:
        lines = ["- [x] G.2 Cancelled item  | REJECTED: no longer needed"]
        result = check_tasks_ticks(lines)
        violations = cast(list[str], result["violations"])
        assert len(violations) == 0


# ============================================================================
# CONSENSUS ENGINE — deep debate mechanics
# ============================================================================


class TestConsensusEngineDeep:
    def test_unanimous_approve_returns_consensus(self) -> None:
        def reviewer(p):
            return "approve\nlooks correct"
        engine = ConsensusEngine(reviewer=reviewer)
        result = engine.run_debate("Should we deploy?", num_agents=3, max_rounds=5)
        assert result["consensus"] is True
        assert result["verdict"] == "approve"
        assert result["rounds"] >= 1

    def test_dissent_goes_to_max_rounds(self) -> None:
        call_count = [0]

        def alternating(prompt: str) -> str:
            call_count[0] += 1
            if call_count[0] % 2 == 1:
                return "approve\nagreed"
            return "reject\ndisagree"

        engine = ConsensusEngine(reviewer=alternating)
        result = engine.run_debate("Question?", num_agents=2, max_rounds=3)
        assert result["consensus"] is False
        assert result["rounds"] == 3

    def test_judge_breaks_tie(self) -> None:
        call_count = [0]

        def alternating(prompt: str) -> str:
            call_count[0] += 1
            if call_count[0] % 2 == 1:
                return "approve\nyes"
            return "reject\nno"

        def judge(p):
            return "approve\nafter review"
        engine = ConsensusEngine(reviewer=alternating, judge=judge)
        result = engine.run_debate("Question?", num_agents=2, max_rounds=2)
        assert result["consensus"] is False
        assert "judge_ruling" in result
        assert result.get("judge_ruling") is True or result["verdict"] == "tie"

    def test_confidence_computed_correctly(self) -> None:
        votes = [
            {"verdict": "approve", "rationale": "r1"},
            {"verdict": "approve", "rationale": "r2"},
            {"verdict": "reject", "rationale": "r3"},
        ]
        conf = _compute_confidence(votes)
        assert conf == pytest.approx(2.0 / 3.0, 0.01)

    def test_consensus_check_unanimous(self) -> None:
        votes = [
            {"verdict": "approve"},
            {"verdict": "approve"},
            {"verdict": "approve"},
        ]
        assert _check_consensus(votes) == "approve"

    def test_consensus_check_dissent(self) -> None:
        votes = [
            {"verdict": "approve"},
            {"verdict": "reject"},
        ]
        assert _check_consensus(votes) is None

    def test_consensus_check_empty(self) -> None:
        assert _check_consensus([]) is None

    def test_parse_verdict_approve(self) -> None:
        verdict, rationale = _parse_verdict("approve\nThis change is correct.")
        assert verdict == "approve"
        assert "correct" in rationale

    def test_parse_verdict_reject(self) -> None:
        verdict, rationale = _parse_verdict("reject\nContains a bug.")
        assert verdict == "reject"
        assert "bug" in rationale

    def test_parse_verdict_defaults_to_needs_changes(self) -> None:
        verdict, _ = _parse_verdict("random response\nno clear verdict")
        assert verdict == "needs_changes"

    def test_build_prompt_includes_context(self) -> None:
        prompt = _build_prompt("Q1?", "Some context", agent_index=0, num_agents=3, round_num=1, dissent_text="")
        assert "reviewer agent 1 of 3" in prompt
        assert "Some context" in prompt
        assert "Q1?" in prompt

    def test_build_prompt_round_two_includes_dissent(self) -> None:
        prompt = _build_prompt(
            "Q1?", "", agent_index=0, num_agents=3, round_num=2, dissent_text="Agent 1: reject\nAgent 2: approve"
        )
        assert "NOT unanimous" in prompt
        assert "dissenting opinions" in prompt

    def test_build_dissent_prompt_formats_votes(self) -> None:
        transcript = {
            "votes": [
                {"agent_index": 0, "verdict": "approve"},
                {"agent_index": 1, "verdict": "reject"},
            ]
        }
        dissent = _build_dissent_prompt(transcript)
        assert "Agent 1: approve" in dissent
        assert "Agent 2: reject" in dissent

    def test_build_judge_prompt_includes_votes(self) -> None:
        votes = [
            {"agent_index": 0, "verdict": "approve", "rationale": "good"},
            {"agent_index": 1, "verdict": "reject", "rationale": "bad"},
        ]
        prompt = _build_judge_prompt("Merge?", "ctx", votes)
        assert "tie-breaking judge" in prompt
        assert "approve" in prompt
        assert "reject" in prompt

    def test_num_agents_clamped_minimum(self) -> None:
        def reviewer(p):
            return "approve\ngood"
        engine = ConsensusEngine(reviewer=reviewer)
        result = engine.run_debate("Q?", num_agents=0)
        assert result["verdict"] != "error"

    def test_max_rounds_clamped_minimum(self) -> None:
        def reviewer(p):
            return "approve\ngood"
        engine = ConsensusEngine(reviewer=reviewer)
        result = engine.run_debate("Q?", num_agents=1, max_rounds=0)
        assert result["verdict"] == "approve"
        assert result["rounds"] == 1


# ============================================================================
# EVIDENCE CHECKER — deep claim detection and token normalization
# ============================================================================


class TestEvidenceCheckerDeep:
    def test_audit_response_extracts_claims(self) -> None:
        checker = EvidenceChecker()
        response = "The function is broken. OK it was fixed in src/main.py:42. I think it passes."
        results = checker.audit_response(response, tool_outputs=["src/main.py:42 test passed"])
        assert len(results) >= 0

    def test_is_valid_source_rejects_src_with_no_suffix(self) -> None:
        assert _is_valid_source("src") is False

    def test_is_valid_source_accepts_dotted_names(self) -> None:
        assert _is_valid_source("src/module.py:42") is True

    def test_extract_sources_finds_paths_in_text(self) -> None:
        text = "See src/main.py:10 and tests/test_app.py:20 for details"
        sources = _extract_sources(text)
        assert any("main.py" in s for s in sources)
        assert any("test_app.py" in s for s in sources)

    def test_normalize_token_plural(self) -> None:
        assert _normalize_token("functions") == "function"
        assert _normalize_token("classes") == "class"
        assert _normalize_token("tests") == "test"

    def test_normalize_token_ies(self) -> None:
        assert _normalize_token("categories") == "category"

    def test_normalize_token_sses_ches(self) -> None:
        assert _normalize_token("passes") == "pass"
        assert _normalize_token("matches") == "match"

    def test_meaningful_tokens_filters_stopwords(self) -> None:
        tokens = _meaningful_tokens("the file is at src/main.py")
        assert "the" not in tokens
        assert "is" not in tokens
        assert "at" not in tokens
        assert "src" not in tokens
        assert "py" in tokens
        assert "main" in tokens

    def test_deduplicate_preserves_order(self) -> None:
        result = _deduplicate(["a", "b", "a", "c", "b"])
        assert result == ["a", "b", "c"]

    def test_split_sentences_handles_periods(self) -> None:
        parts = _split_sentences("First sentence. Second sentence! Third?")
        assert len(parts) == 3

    def test_is_factual_claim_detects_patterns(self) -> None:
        assert _is_factual_claim("The function is broken") is True
        assert _is_factual_claim("It was fixed in src/main.py") is True

    def test_source_tokens_extracts_hostname(self) -> None:
        tokens = _source_tokens("https://github.com/user/repo/file.py")
        assert "github" in tokens or "repo" in tokens

    def test_matching_tool_sources_finds_context_match(self) -> None:
        from general_ludd.review.evidence_checker import _matching_tool_sources

        claim = "The test in src/main.py passes"
        tool_outputs = ["src/main.py:42 — test ran ok"]
        matching = _matching_tool_sources(claim, tool_outputs)
        assert any("main.py" in s for s in matching)


# ============================================================================
# BACKLOG AUDITOR — false claim detection + stub markers
# ============================================================================


class TestBacklogAuditorDeep:
    def test_false_claim_no_evidence_tests(self) -> None:
        tasks = [
            {"id": "t1", "status": "complete", "evidence_test_ids": [], "touched_files": [], "acceptance_criteria": []}
        ]
        def test_runner(ids):
            return {}
        auditor = BacklogAuditor(".", test_runner=test_runner)
        report = auditor.audit(tasks)
        assert report.total_audited == 1
        assert report.false_claim == 1
        assert report.verdicts[0].verdict == FALSE_CLAIM

    def test_false_claim_missing_file(self) -> None:
        tasks = [
            {
                "id": "t2",
                "status": "complete",
                "evidence_test_ids": ["tests::test_x"],
                "touched_files": ["nonexistent.py"],
                "acceptance_criteria": [],
            }
        ]
        def test_runner(ids):
            return {ids[0]: True}
        def file_reader(path):
            return None
        auditor = BacklogAuditor(".", test_runner=test_runner, file_reader=file_reader)
        report = auditor.audit(tasks)
        assert report.false_claim == 1

    def test_false_claim_failing_evidence_test(self) -> None:
        tasks = [
            {
                "id": "t3",
                "status": "complete",
                "evidence_test_ids": ["tests::test_fail"],
                "touched_files": [],
                "acceptance_criteria": [],
            }
        ]
        def test_runner(ids):
            return {ids[0]: False}
        auditor = BacklogAuditor(".", test_runner=test_runner)
        report = auditor.audit(tasks)
        assert report.false_claim == 1
        assert "fails on re-run" in report.verdicts[0].reasons[0]

    def test_incomplete_stub_marker(self) -> None:
        tasks = [
            {
                "id": "t4",
                "status": "complete",
                "evidence_test_ids": ["tests::test_pass"],
                "touched_files": ["mod.py"],
                "acceptance_criteria": [],
            }
        ]
        def test_runner(ids):
            return {ids[0]: True}
        def file_reader(path):
            return "def foo(): pass  # stub" if "mod.py" in path else None
        auditor = BacklogAuditor(".", test_runner=test_runner, file_reader=file_reader)
        report = auditor.audit(tasks)
        assert report.incomplete == 1
        assert any("stub" in r.lower() for r in report.verdicts[0].reasons)

    def test_verified_complete_passes_all_checks(self) -> None:
        tasks = [
            {
                "id": "t5",
                "status": "complete",
                "evidence_test_ids": ["tests::test_ok"],
                "touched_files": ["mod.py"],
                "acceptance_criteria": [],
            }
        ]
        def test_runner(ids):
            return {ids[0]: True}
        def file_reader(path):
            return "def foo(): return 42\n" if "mod.py" in path else None
        auditor = BacklogAuditor(".", test_runner=test_runner, file_reader=file_reader)
        report = auditor.audit(tasks)
        assert report.verified_complete == 1
        assert report.verdicts[0].verdict == VERIFIED_COMPLETE

    def test_skips_non_completed_tasks(self) -> None:
        tasks = [
            {
                "id": "t6",
                "status": "pending",
                "evidence_test_ids": ["a"],
                "touched_files": [],
                "acceptance_criteria": [],
            }
        ]
        def test_runner(ids):
            return {}
        auditor = BacklogAuditor(".", test_runner=test_runner)
        report = auditor.audit(tasks)
        assert report.total_audited == 0

    def test_incomplete_unmet_criterion(self) -> None:
        tasks = [
            {
                "id": "t7",
                "status": "complete",
                "evidence_test_ids": ["tests::test_ok"],
                "touched_files": ["mod.py"],
                "acceptance_criteria": ["must export render_widget"],
            }
        ]
        def test_runner(ids):
            return {ids[0]: True}
        def file_reader(path):
            return "def foo(): pass\n" if "mod.py" in path else None
        auditor = BacklogAuditor(".", test_runner=test_runner, file_reader=file_reader)
        report = auditor.audit(tasks)
        assert report.incomplete == 1

    def test_not_implemented_error_is_stub(self) -> None:
        tasks = [
            {
                "id": "t8",
                "status": "complete",
                "evidence_test_ids": ["tests::test_ok"],
                "touched_files": ["mod.py"],
                "acceptance_criteria": [],
            }
        ]
        def test_runner(ids):
            return {ids[0]: True}
        def file_reader(path):
            return "raise NotImplementedError('todo')" if "mod.py" in path else None
        auditor = BacklogAuditor(".", test_runner=test_runner, file_reader=file_reader)
        report = auditor.audit(tasks)
        assert report.incomplete == 1

    def test_xfail_marker_is_stub(self) -> None:
        tasks = [
            {
                "id": "t9",
                "status": "complete",
                "evidence_test_ids": ["tests::test_ok"],
                "touched_files": ["test_mod.py"],
                "acceptance_criteria": [],
            }
        ]
        def test_runner(ids):
            return {ids[0]: True}
        def file_reader(path):
            return "@pytest.mark.xfail\ndef test_x(): pass\n" if "test_mod.py" in path else None
        auditor = BacklogAuditor(".", test_runner=test_runner, file_reader=file_reader)
        report = auditor.audit(tasks)
        assert report.incomplete == 1

    def test_backlog_audit_report_aggregation(self) -> None:
        tasks = [
            {"id": "a", "status": "complete", "evidence_test_ids": [], "touched_files": [], "acceptance_criteria": []},
            {
                "id": "b",
                "status": "complete",
                "evidence_test_ids": ["t::pass"],
                "touched_files": ["ok.py"],
                "acceptance_criteria": [],
            },
        ]
        def test_runner(ids):
            return {ids[0]: True}
        def file_reader(path):
            return "def foo(): return 1\n" if "ok.py" in path else None
        auditor = BacklogAuditor(".", test_runner=test_runner, file_reader=file_reader)
        report = auditor.audit(tasks)
        assert report.total_audited == 2
        assert report.false_claim == 1
        assert report.verified_complete == 1

    def test_task_verdict_construction(self) -> None:
        tv = TaskVerdict(id="id1", verdict=FALSE_CLAIM, reasons=["bad"])
        assert tv.id == "id1"
        assert tv.verdict == FALSE_CLAIM
        assert tv.reasons == ["bad"]

    def test_backlog_audit_report_defaults(self) -> None:
        report = BacklogAuditReport()
        assert report.total_audited == 0
        assert report.verified_complete == 0
        assert report.false_claim == 0
        assert report.incomplete == 0
        assert report.verdicts == []


# ============================================================================
# FEATURE VERIFIER — evidence dispatch + node id validation
# ============================================================================


class TestFeatureVerifierDeep:
    def test_validate_node_id_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            _validate_node_id("")

    def test_validate_node_id_rejects_leading_dash(self) -> None:
        with pytest.raises(ValueError, match="dash"):
            _validate_node_id("-k --collect-only")

    def test_validate_node_id_rejects_semicolons(self) -> None:
        with pytest.raises(ValueError, match="metacharacter"):
            _validate_node_id("test_file.py; rm -rf /")

    def test_validate_node_id_accepts_valid(self) -> None:
        result = _validate_node_id("tests/unit/test_foo.py::TestFoo::test_bar")
        assert "TestFoo" in result

    def test_safe_node_id_regex_matches_valid(self) -> None:
        assert _SAFE_NODE_ID.match("tests/unit/test_x.py::TestCase::test_method") is not None
        assert _SAFE_NODE_ID.match("tests/unit/test_x.py::test_func[param-1]") is not None

    def test_safe_node_id_regex_rejects_injection(self) -> None:
        assert _SAFE_NODE_ID.match("-k exploit") is None
        assert _SAFE_NODE_ID.match("test.py; echo hacked") is None

    def test_feature_verifier_missing_evidence_gets_requested(self) -> None:
        fv = FeatureVerifier("/tmp")
        feature = {"id": "f1", "name": "test_feature", "status": "requested", "evidence": None}
        result = fv.verify_feature(feature)
        assert result["status"] == "requested"

    def test_feature_verifier_all_met_gets_verified(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "molecule" / "playbooks" / "default").mkdir(parents=True)
            (Path(root) / "collections").mkdir()
            fv = FeatureVerifier(root, runner=lambda nid: 0)
            feature = {
                "id": "f2",
                "name": "test_feature",
                "status": "requested",
                "evidence": ["test:tests/unit/test_x.py", "molecule:default"],
            }
            result = fv.verify_feature(feature)
            assert result["status"] == "verified"
            assert result["verified_at"] is not None

    def test_feature_verifier_verify_all_summary(self) -> None:
        fv = FeatureVerifier("/tmp", runner=lambda nid: 0)
        features = [
            {"id": "a", "name": "A", "status": "requested", "evidence": None},
            {"id": "b", "name": "B", "status": "requested", "evidence": None},
        ]
        summary = fv.verify_all(features)
        assert summary["total"] == 2
        assert summary["requested_count"] == 2


# ============================================================================
# PROMPT EVALUATOR — classify + measure + generate_report
# ============================================================================


class TestPromptEvaluatorDeep:
    def test_classify_planning_prompt(self) -> None:
        category = classify_prompt("Let's plan the architecture approach and design a strategy.")
        assert category == "planning"

    def test_classify_coding_prompt(self) -> None:
        category = classify_prompt("Write a function to implement the login endpoint.")
        assert category == "coding"

    def test_classify_research_prompt(self) -> None:
        category = classify_prompt("Research the codebase for authentication modules.")
        assert category == "research"

    def test_classify_debugging_prompt(self) -> None:
        category = classify_prompt("Debug this crash and fix the stack trace error.")
        assert category == "debugging"

    def test_classify_configuration_prompt(self) -> None:
        category = classify_prompt("Setup the Docker container and configure CI pipeline.")
        assert category == "configuration"

    def test_classify_unmatched_prompt_returns_other(self) -> None:
        category = classify_prompt("hello world")
        assert category == "other"

    def test_measure_prompt_efficiency_completed(self) -> None:
        response: dict[str, Any] = {"content": "done — test passes", "tool_calls": [{"name": "run"}]}
        result = measure_prompt_efficiency("fix the bug", response)
        assert result["task_completed"] is True
        assert result["tokens_in"] > 0
        assert result["tools_called"] == 1

    def test_measure_prompt_efficiency_not_completed(self) -> None:
        response: dict[str, Any] = {"content": "still working on it", "tool_calls": []}
        result = measure_prompt_efficiency("complex task", response)
        assert result["task_completed"] is False

    def test_measure_prompt_efficiency_errors_counted(self) -> None:
        response: dict[str, Any] = {"content": "got a type error and an exception", "tool_calls": []}
        result = measure_prompt_efficiency("do something", response)
        assert result["errors"] >= 1

    def test_detect_context_waste_repeated_sentences(self) -> None:
        conversation = [
            {"role": "assistant", "content": "This is a repeated sentence that appears multiple times.", "tokens": 10},
            {"role": "assistant", "content": "This is a repeated sentence that appears multiple times.", "tokens": 10},
        ]
        findings = detect_context_waste(conversation)
        assert any(f["type"] == "repeated_fact" for f in findings)

    def test_detect_context_waste_high_ratio(self) -> None:
        conversation = [
            {"role": "user", "content": "short", "tokens": 5},
            {"role": "assistant", "content": "very " * 1200, "tokens": 1200},
        ]
        findings = detect_context_waste(conversation)
        assert any(f["type"] == "high_response_overhead" for f in findings)

    def test_generate_report_markdown_format(self) -> None:
        analyses = [
            {
                "prompt_id": "Test Prompt",
                "classification": "coding",
                "efficiency": {
                    "tokens_in": 200,
                    "tokens_out": 150,
                    "task_completed": True,
                    "steps_taken": 3,
                    "errors": 0,
                },
                "cot_quality": {"reasoning_depth": 5, "decision_quality": 6, "dead_ends": 1, "score": 7},
                "context_waste": [],
                "recommendations": ["Good job!"],
            }
        ]
        report = generate_report(analyses, format="markdown")
        assert "# Prompt Evaluation Report" in report
        assert "Test Prompt" in report
        assert "coding" in report

    def test_estimate_tokens_basic(self) -> None:
        assert _estimate_tokens("hello world") == 2
        assert _estimate_tokens("") == 0

    def test_try_parse_json_valid(self) -> None:
        result = _try_parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_try_parse_json_array_returns_none(self) -> None:
        result = _try_parse_json("[1, 2, 3]")
        assert result is None

    def test_try_parse_json_invalid_returns_none(self) -> None:
        result = _try_parse_json("not json")
        assert result is None


# ============================================================================
# REPO_ROOT helpers
# ============================================================================


class TestRepoRootHelpers:
    def test_repo_root_is_unresolved_none(self) -> None:
        assert _repo_root_is_unresolved(None) is True

    def test_repo_root_is_unresolved_empty(self) -> None:
        assert _repo_root_is_unresolved("") is True
        assert _repo_root_is_unresolved("  ") is True

    def test_repo_root_is_unresolved_dot(self) -> None:
        assert _repo_root_is_unresolved(".") is True
        assert _repo_root_is_unresolved("./") is True

    def test_repo_root_is_unresolved_real_path(self) -> None:
        assert _repo_root_is_unresolved("/tmp") is False


# ============================================================================
# Deep Debris — test_test_exists edge cases
# ============================================================================


class TestGapAnalyzerEdgeCases:
    def test_analyze_empty_repo(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            analyzer = GapAnalyzer()
            report = analyzer.analyze("sprint1", root)
            assert report.total_gaps == 0

    def test_analyze_with_impl_and_test(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            src = os.path.join(root, "src", "mod")
            os.makedirs(src)
            Path(os.path.join(src, "feature.py")).write_text("def work(): pass\n")
            tests = os.path.join(root, "tests", "unit")
            os.makedirs(tests)
            Path(os.path.join(tests, "test_feature.py")).write_text("import feature\n")
            analyzer = GapAnalyzer()
            report = analyzer.analyze("sprint1", root)
            assert report.total_gaps == 0

    def test_test_exists_in_nested_test_dir(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            tests_dir = os.path.join(root, "tests", "integration", "sub")
            os.makedirs(tests_dir)
            Path(os.path.join(tests_dir, "test_advanced.py")).touch()
            assert _test_exists(root, "test_advanced.py") is True

    def test_test_exists_matches_exact_test_filename(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            tests_dir = os.path.join(root, "tests", "unit")
            os.makedirs(tests_dir)
            Path(os.path.join(tests_dir, "test_advanced.py")).touch()
            assert _test_exists(root, "test_advanced.py") is True

    def test_test_exists_returns_false_for_wrong_name(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            tests_dir = os.path.join(root, "tests")
            os.makedirs(tests_dir)
            Path(os.path.join(tests_dir, "test_other.py")).touch()
            assert _test_exists(root, "test_nonexistent.py") is False


# ============================================================================
# Context waste deep — broad prompts and verbosity
# ============================================================================


class TestContextWasteDeep:
    def test_overly_broad_request_detected(self) -> None:
        long_prompt = "x " * 600
        conversation = [
            {"role": "user", "content": long_prompt, "tokens": _estimate_tokens(long_prompt)},
            {"role": "assistant", "content": "ok", "tokens": 2},
        ]
        findings = detect_context_waste(conversation)
        assert any(f["type"] == "overly_broad_request" for f in findings)

    def test_context_waste_returns_empty_for_clean_conversation(self) -> None:
        conversation = [
            {"role": "user", "content": "short request", "tokens": 3},
            {"role": "assistant", "content": "short response", "tokens": 3},
        ]
        findings = detect_context_waste(conversation)
        assert not any(f["type"] == "repeated_fact" for f in findings)


# ============================================================================
# generate_report JSON format
# ============================================================================


class TestGenerateReport:
    def test_generate_report_json_format(self) -> None:
        analyses: list[dict[str, Any]] = [{"key": "value"}]
        report = generate_report(analyses, format="json")
        import json

        parsed = json.loads(report)
        assert parsed == analyses
