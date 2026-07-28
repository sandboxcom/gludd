"""E2E workflow tests for dogfood, ag15_benchmarks, and entity subsystems.

Covers orchestrator integration, runner seeding/validation, sprint parsing,
validator bypass detection, benchmark harness full-suite runs, GAIA/SWE-bench
loaders/scorers, entity graph construction/traversal/serialization, and
research-pattern extraction pipelines.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.ag15_benchmarks.benchmark_harness import (
    BenchmarkResult,
    BenchmarkSuite,
    BenchmarkSummary,
    BenchmarkTask,
)
from general_ludd.ag15_benchmarks.gaia import (
    load_tasks as load_gaia_tasks,
)
from general_ludd.ag15_benchmarks.gaia import (
    score_result as score_gaia,
)
from general_ludd.ag15_benchmarks.swe_bench import (
    load_tasks as load_swe_bench_tasks,
)
from general_ludd.ag15_benchmarks.swe_bench import (
    score_result as score_swe_bench,
)
from general_ludd.dogfood.orchestrator import run_smoke_and_validate
from general_ludd.dogfood.runner import (
    DogfoodConfig,
    DogfoodRunner,
    SmokeTaskResult,
    _validate_task_name,
)
from general_ludd.dogfood.sprint_parser import parse_sprint_markdown
from general_ludd.dogfood.validator import (
    DogfoodValidator,
)
from general_ludd.entity.graph import (
    Association,
    EntityGraph,
    EntityNode,
)
from general_ludd.entity.research_patterns import (
    EntityResearchResult,
    detect_acquisitions,
    detect_funding_rounds,
    extract_domains,
    extract_ip_addresses,
    parse_companies_house,
    parse_sec_filing,
    research_entity,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Dogfood — runner + config
# ═══════════════════════════════════════════════════════════════════════════════

class TestDogfoodConfigValidation:
    def test_config_valid_all_fields(self):
        cfg = DogfoodConfig(
            repo_root="/tmp/repo",
            target_repo="/tmp/target",
            runtime_profile="ansible",
            model_profile="sonnet",
        )
        assert cfg.repo_root == "/tmp/repo"
        assert cfg.target_repo == "/tmp/target"
        assert cfg.auto_commit is True

    def test_config_empty_repo_root_raises(self):
        with pytest.raises(ValueError):
            DogfoodConfig(repo_root="   ", target_repo="/tmp", runtime_profile="x", model_profile="x")

    def test_config_empty_target_repo_raises(self):
        with pytest.raises(ValueError):
            DogfoodConfig(repo_root="/tmp", target_repo="", runtime_profile="x", model_profile="x")


class TestTaskNameHardening:
    def test_valid_simple_name(self):
        assert _validate_task_name("noop") == "noop"

    def test_valid_with_dots_and_underscores(self):
        assert _validate_task_name("my_task.v2") == "my_task.v2"

    def test_rejects_path_separator_slash(self):
        with pytest.raises(ValueError, match="path separator"):
            _validate_task_name("foo/bar")

    def test_rejects_path_separator_backslash(self):
        with pytest.raises(ValueError, match="path separator"):
            _validate_task_name("foo\\bar")

    def test_rejects_traversal(self):
        with pytest.raises(ValueError, match="traversal"):
            _validate_task_name("../etc/passwd")

    def test_rejects_leading_dash(self):
        with pytest.raises(ValueError, match="dash"):
            _validate_task_name("--help")

    def test_rejects_shell_metachar(self):
        with pytest.raises(ValueError, match="shell metachar"):
            _validate_task_name("foo;rm -rf /")


class TestDogfoodRunnerSeeding:
    def test_seed_todos_from_sprint(self):
        sprint_content = (
            "## Objective 1: Ship beta\n"
            "Status : in_progress\n"
            "- [ ] write migration\n"
            "- [ ] add endpoint\n"
            "- AC1 : passes gate\n"
        )
        runner = DogfoodRunner(DogfoodConfig(
            repo_root="/tmp", target_repo="/tmp", runtime_profile="a", model_profile="m",
        ))
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(sprint_content)
            sprint_path = f.name
        try:
            todos = runner.seed_todos_from_sprint(sprint_path)
            assert len(todos) == 2
            assert todos[0]["source"] == "sprint"
            assert todos[0]["objective_number"] == 1
        finally:
            Path(sprint_path).unlink()

    def test_seed_todos_from_gap_analysis(self):
        gap = MagicMock()
        gap.description = "missing test"
        gap.category = "coverage"
        gap.severity = "high"
        gap.suggested_action = "write test"
        gap_report = MagicMock(gaps=[gap])
        runner = DogfoodRunner(DogfoodConfig(
            repo_root="/tmp", target_repo="/tmp", runtime_profile="a", model_profile="m",
        ))
        todos = runner.seed_todos_from_gap_analysis(gap_report)
        assert todos[0]["description"] == "missing test"
        assert todos[0]["source"] == "gap_analysis"

    def test_seed_todos_from_test_failures(self):
        runner = DogfoodRunner(DogfoodConfig(
            repo_root="/tmp", target_repo="/tmp", runtime_profile="a", model_profile="m",
        ))
        test_output = "FAILED tests/unit/test_foo.py::test_bar\nFAILED tests/unit/test_baz.py::test_qux"
        todos = runner.seed_todos_from_test_failures(test_output)
        assert len(todos) == 2
        assert todos[0]["test_id"] == "tests/unit/test_foo.py::test_bar"

    def test_seed_todos_from_test_failures_no_matches(self):
        runner = DogfoodRunner(DogfoodConfig(
            repo_root="/tmp", target_repo="/tmp", runtime_profile="a", model_profile="m",
        ))
        todos = runner.seed_todos_from_test_failures("all green")
        assert todos == []

    def test_smoke_task_result_on_failure(self):
        runner = DogfoodRunner(DogfoodConfig(
            repo_root="/tmp", target_repo="/tmp", runtime_profile="a", model_profile="m",
        ))
        with patch("subprocess.run", side_effect=OSError("no ansible")):
            result = runner.run_smoke_task("noop")
        assert isinstance(result, SmokeTaskResult)
        assert result.success is False
        assert result.duration_seconds >= 0

    def test_create_dogfood_profile(self):
        cfg = DogfoodConfig(
            repo_root="/tmp/a", target_repo="/tmp/b", runtime_profile="r", model_profile="m",
        )
        runner = DogfoodRunner(cfg)
        profile = runner.create_dogfood_profile()
        assert profile.runtime_mode == "r"
        assert profile.model_profiles == ["m"]
        assert profile.enabled is True


# ═══════════════════════════════════════════════════════════════════════════════
# Dogfood — sprint parser
# ═══════════════════════════════════════════════════════════════════════════════

class TestSprintParser:
    def test_parse_single_objective_with_tasks_and_ac(self):
        content = (
            "## Objective 1: Ship\n"
            "Status : in_progress\n"
            "- [ ] task one\n"
            "- [ ] task two\n"
            "- AC1 : gate passes\n"
            "- AC2 : coverage above 80\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            items = parse_sprint_markdown(path)
            assert len(items) == 1
            assert items[0].objective_number == 1
            assert items[0].title == "Ship"
            assert items[0].status == "in_progress"
            assert items[0].tasks == ["task one", "task two"]
            assert len(items[0].acceptance_criteria) == 2
        finally:
            Path(path).unlink()

    def test_parse_multiple_objectives(self):
        content = (
            "## Objective 1: Alpha\n"
            "Status : done\n"
            "- [ ] task-a\n"
            "## Objective 2: Beta\n"
            "Status : pending\n"
            "- [ ] task-b\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            items = parse_sprint_markdown(path)
            assert len(items) == 2
            assert items[0].status == "done"
            assert items[1].status == "pending"
        finally:
            Path(path).unlink()

    def test_parse_objective_without_title(self):
        content = "## Objective 3\nStatus : done\n- [ ] x"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            items = parse_sprint_markdown(path)
            assert items[0].title == ""
            assert items[0].objective_number == 3
        finally:
            Path(path).unlink()

    def test_parse_no_objectives_returns_empty(self):
        content = "just some markdown\nno objectives here\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            items = parse_sprint_markdown(path)
            assert items == []
        finally:
            Path(path).unlink()


# ═══════════════════════════════════════════════════════════════════════════════
# Dogfood — validator
# ═══════════════════════════════════════════════════════════════════════════════

class TestDogfoodValidator:
    def test_validate_successful_run(self):
        smoke = SmokeTaskResult(task_name="t", success=True, duration_seconds=1.0, output="ok")
        result = DogfoodValidator().validate_dogfood_run(smoke)
        assert result.valid is True

    def test_validate_failed_run(self):
        smoke = SmokeTaskResult(task_name="t", success=False, duration_seconds=0.5, output="err")
        result = DogfoodValidator().validate_dogfood_run(smoke)
        assert result.valid is False
        assert result.uses_configured_runtime is False

    def test_check_no_local_bypasses_empty(self):
        findings = DogfoodValidator().check_no_local_bypasses([])
        assert findings == []

    def test_check_local_bypass_detected(self):
        entries = [{"runtime": "local", "command": "bash -c echo"}]
        findings = DogfoodValidator().check_no_local_bypasses(entries)
        assert len(findings) == 1
        assert findings[0].category == "local_bypass"

    def test_check_bypass_command_indicator(self):
        entries = [{"runtime": "ansible", "command": "pip install requests"}]
        findings = DogfoodValidator().check_no_local_bypasses(entries)
        assert len(findings) == 1

    def test_check_artifacts_use_configured_runtime(self):
        artifacts = [{"runtime": "ansible"}, {"runtime": "ansible"}]
        assert DogfoodValidator().check_artifacts_use_configured_runtime(artifacts) is True

    def test_check_artifacts_mixed_runtime(self):
        artifacts = [{"runtime": "ansible"}, {"runtime": "local"}]
        assert DogfoodValidator().check_artifacts_use_configured_runtime(artifacts) is False

    def test_check_artifacts_empty(self):
        assert DogfoodValidator().check_artifacts_use_configured_runtime([]) is True


# ═══════════════════════════════════════════════════════════════════════════════
# Dogfood — orchestrator
# ═══════════════════════════════════════════════════════════════════════════════

class TestOrchestratorIntegration:
    def test_run_smoke_and_validate_success(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            report = run_smoke_and_validate(repo_root="/tmp", task_name="noop")
        assert report["smoke"]["success"] is True
        assert report["validation"]["valid"] is True

    def test_run_smoke_and_validate_failure(self):
        with patch("subprocess.run", side_effect=OSError("no ansible")):
            report = run_smoke_and_validate(repo_root="/tmp", task_name="noop")
        assert report["smoke"]["success"] is False
        assert report["validation"]["valid"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# ag15_benchmarks — harness
# ═══════════════════════════════════════════════════════════════════════════════

class TestBenchmarkHarness:
    def test_single_task_success(self):
        task = BenchmarkTask(task_id="t1", description="test")
        def runner(t):
            return "output"
        def scorer(t, output):
            return 1.0

        suite = BenchmarkSuite(agent_name="test-agent")
        summary = suite.run_benchmark("swe-bench", [task], scorer, runner)
        assert summary.total_tasks == 1
        assert summary.resolved_count == 1
        assert summary.mean_score == 1.0
        assert summary.resolution_rate() == 1.0

    def test_single_task_failure(self):
        task = BenchmarkTask(task_id="t2", description="fail")
        def runner(t):
            raise RuntimeError("boom")
        def scorer(t, output):
            return 0.0

        suite = BenchmarkSuite()
        summary = suite.run_benchmark("gaia", [task], scorer, runner)
        assert summary.resolved_count == 0
        assert summary.mean_score == 0.0
        assert suite.results[0].error == "boom"

    def test_partial_score_does_not_resolve(self):
        task = BenchmarkTask(task_id="t3", description="partial")
        def runner(t):
            return "half right"
        def scorer(t, output):
            return 0.5

        suite = BenchmarkSuite()
        summary = suite.run_benchmark("gaia", [task], scorer, runner)
        assert summary.resolved_count == 0
        assert summary.mean_score == 0.5

    def test_multiple_tasks_aggregate(self):
        tasks = [
            BenchmarkTask(task_id="a", description="pass"),
            BenchmarkTask(task_id="b", description="fail"),
        ]
        def runner(t):
            return "out"
        def scorer(t, output):
            return 1.0 if t.task_id == "a" else 0.0

        suite = BenchmarkSuite()
        summary = suite.run_benchmark("swe-bench", tasks, scorer, runner)
        assert summary.total_tasks == 2
        assert summary.resolved_count == 1
        assert summary.resolution_rate() == 0.5

    def test_report_json_serializable(self):
        suite = BenchmarkSuite(agent_name="reporter")
        BenchmarkTask(task_id="x", description="d")
        suite.results.append(BenchmarkResult(
            benchmark="swe-bench", task_id="x", score=1.0,
            agent_name="reporter", duration_ms=100.0, attempts=1,
            resolved=True,
        ))
        report = suite.report()
        assert report["agent"] == "reporter"
        assert report["benchmarks"]["swe-bench"]["resolved_count"] == 1

    def test_report_writes_to_file(self):
        suite = BenchmarkSuite()
        BenchmarkTask(task_id="f", description="d")
        suite.results.append(BenchmarkResult(
            benchmark="gaia", task_id="f", score=0.0,
            agent_name="a", duration_ms=50.0, attempts=1,
            resolved=False,
        ))
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            out_path = Path(tf.name)
        try:
            suite.report(output_path=out_path)
            assert out_path.exists()
            data = json.loads(out_path.read_text())
            assert data["agent"] == "a"
        finally:
            out_path.unlink(missing_ok=True)

    def test_resolution_rate_empty_summary(self):
        summary = BenchmarkSummary(
            benchmark="x", agent_name="a", total_tasks=0,
            resolved_count=0, mean_score=0.0, total_duration_ms=0.0,
        )
        assert summary.resolution_rate() == 0.0

    def test_benchmark_task_metadata_flow(self):
        task = BenchmarkTask(task_id="meta-1", description="with meta", metadata={"key": "val"})
        hits = []
        def runner(t):
            hits.append(t.metadata.get("key"))
            return "done"
        def scorer(t, output):
            return 1.0

        suite = BenchmarkSuite()
        suite.run_benchmark("custom", [task], scorer, runner)
        assert hits == ["val"]


# ═══════════════════════════════════════════════════════════════════════════════
# ag15_benchmarks — GAIA
# ═══════════════════════════════════════════════════════════════════════════════

class TestGAIAIntegration:
    def test_gaia_load_tasks_empty_when_no_dataset(self, tmp_path):
        tasks = load_gaia_tasks(cache_dir=tmp_path)
        assert tasks == []

    def test_gaia_load_tasks_from_file(self, tmp_path):
        gaia_dir = tmp_path / "gaia"
        gaia_dir.mkdir(parents=True)
        dataset = gaia_dir / "gaia_validation.jsonl"
        dataset.write_text(json.dumps({
            "task_id": "g1", "question": "What is 2+2?",
            "Level": "1", "Final answer": "4",
        }) + "\n" + json.dumps({
            "task_id": "g2", "question": "Capital of France?",
            "Level": "1", "Final answer": "Paris",
        }) + "\n")
        tasks = load_gaia_tasks(cache_dir=tmp_path)
        assert len(tasks) == 2
        assert tasks[0].task_id == "g1"
        assert tasks[0].metadata["level"] == "1"
        assert tasks[1].metadata["ground_truth"] == "Paris"

    def test_gaia_score_exact_match(self):
        task = BenchmarkTask(task_id="g", description="q", metadata={"ground_truth": "Hello"})
        assert score_gaia("Hello", task) == 1.0

    def test_gaia_score_case_insensitive(self):
        task = BenchmarkTask(task_id="g", description="q", metadata={"ground_truth": "HELLO"})
        assert score_gaia("hello", task) == 1.0

    def test_gaia_score_trailing_dot_normalized(self):
        task = BenchmarkTask(task_id="g", description="q", metadata={"ground_truth": "Hello."})
        assert score_gaia("hello", task) == 1.0

    def test_gaia_score_substring_partial(self):
        task = BenchmarkTask(task_id="g", description="q", metadata={"ground_truth": "paris"})
        assert score_gaia("Paris, France", task) == 0.5

    def test_gaia_score_no_match(self):
        task = BenchmarkTask(task_id="g", description="q", metadata={"ground_truth": "Paris"})
        assert score_gaia("London", task) == 0.0

    def test_gaia_score_no_ground_truth(self):
        task = BenchmarkTask(task_id="g", description="q", metadata={})
        assert score_gaia("anything", task) == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# ag15_benchmarks — SWE-bench
# ═══════════════════════════════════════════════════════════════════════════════

class TestSWEBenchIntegration:
    def test_swe_bench_load_tasks_empty_when_no_dataset(self, tmp_path):
        tasks = load_swe_bench_tasks(cache_dir=tmp_path)
        assert tasks == []

    def test_swe_bench_load_tasks_from_file(self, tmp_path):
        swe_dir = tmp_path / "swe-bench"
        swe_dir.mkdir(parents=True)
        dataset = swe_dir / "swe-bench_Verified.jsonl"
        dataset.write_text(json.dumps({
            "instance_id": "swe-1", "problem_statement": "fix bug",
            "repo": "org/repo", "base_commit": "abc123",
            "FAIL_TO_PASS": ["test_a", "test_b"],
            "PASS_TO_PASS": ["test_c"],
        }) + "\n")
        tasks = load_swe_bench_tasks(cache_dir=tmp_path)
        assert len(tasks) == 1
        assert tasks[0].task_id == "swe-1"
        assert tasks[0].metadata["repo"] == "org/repo"
        assert tasks[0].metadata["fail_to_pass"] == ["test_a", "test_b"]

    def test_swe_bench_score_full_pass(self):
        task = BenchmarkTask(task_id="s", description="d", metadata={"fail_to_pass": ["a", "b"]})
        result = "test_a passed\ntest_b passed\n"
        assert score_swe_bench(result, task) == 1.0

    def test_swe_bench_score_half_pass(self):
        task = BenchmarkTask(task_id="s", description="d", metadata={"fail_to_pass": ["a", "b"]})
        result = "test_a passed\n"
        assert score_swe_bench(result, task) == 0.5

    def test_swe_bench_score_no_fail_to_pass(self):
        task = BenchmarkTask(task_id="s", description="d", metadata={"fail_to_pass": []})
        assert score_swe_bench("anything", task) == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Entity — graph construction, traversal, serialization
# ═══════════════════════════════════════════════════════════════════════════════

class TestEntityGraphConstruction:
    def test_add_and_retrieve_node(self):
        g = EntityGraph()
        node = EntityNode(id="n1", name="Acme Corp", entity_type="company")
        g.add_node(node)
        assert g.node_count == 1
        assert g.has_node("n1") is True
        assert g.get_node("n1") is node

    def test_add_node_missing_returns_none(self):
        g = EntityGraph()
        assert g.get_node("nope") is None

    def test_add_edge_between_nodes(self):
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A"))
        g.add_node(EntityNode(id="b", name="B"))
        assoc = Association(source_id="a", target_id="b", assoc_type="supplier")
        g.add_edge(assoc)
        assert g.edge_count == 1

    def test_add_edge_missing_source_raises(self):
        g = EntityGraph()
        g.add_node(EntityNode(id="b", name="B"))
        with pytest.raises(ValueError, match="Source node"):
            g.add_edge(Association(source_id="a", target_id="b", assoc_type="x"))

    def test_add_edge_missing_target_raises(self):
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A"))
        with pytest.raises(ValueError, match="Target node"):
            g.add_edge(Association(source_id="a", target_id="b", assoc_type="x"))


class TestEntityGraphTraversal:
    def test_get_related_depth_1(self):
        g = EntityGraph()
        for nid in ("a", "b", "c"):
            g.add_node(EntityNode(id=nid, name=nid.upper()))
        g.add_edge(Association(source_id="a", target_id="b", assoc_type="x"))
        g.add_edge(Association(source_id="a", target_id="c", assoc_type="x"))
        related = g.get_related("a", max_depth=1)
        assert "depth_1" in related
        assert related["depth_1"] == ["b", "c"]

    def test_get_related_depth_2(self):
        g = EntityGraph()
        for nid in ("a", "b", "c"):
            g.add_node(EntityNode(id=nid, name=nid.upper()))
        g.add_edge(Association(source_id="a", target_id="b", assoc_type="x"))
        g.add_edge(Association(source_id="b", target_id="c", assoc_type="x"))
        related = g.get_related("a", max_depth=2)
        assert related["depth_1"] == ["b"]
        assert related["depth_2"] == ["c"]

    def test_get_related_missing_entity(self):
        g = EntityGraph()
        assert g.get_related("nope") == {}

    def test_find_path_direct(self):
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A"))
        g.add_node(EntityNode(id="b", name="B"))
        g.add_edge(Association(source_id="a", target_id="b", assoc_type="x"))
        path = g.find_path("a", "b")
        assert path == ["a", "b"]

    def test_find_path_through_intermediary(self):
        g = EntityGraph()
        for nid in ("a", "b", "c"):
            g.add_node(EntityNode(id=nid, name=nid.upper()))
        g.add_edge(Association(source_id="a", target_id="b", assoc_type="x"))
        g.add_edge(Association(source_id="b", target_id="c", assoc_type="x"))
        path = g.find_path("a", "c")
        assert path == ["a", "b", "c"]

    def test_find_path_self(self):
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A"))
        assert g.find_path("a", "a") == ["a"]

    def test_find_path_no_path(self):
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A"))
        g.add_node(EntityNode(id="b", name="B"))
        assert g.find_path("a", "b") is None

    def test_find_path_missing_nodes(self):
        g = EntityGraph()
        assert g.find_path("x", "y") is None

    def test_detect_clusters_single(self):
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A"))
        g.add_node(EntityNode(id="b", name="B"))
        g.add_edge(Association(source_id="a", target_id="b", assoc_type="x"))
        clusters = g.detect_clusters()
        assert len(clusters) == 1
        assert sorted(clusters[0]) == ["a", "b"]

    def test_detect_clusters_disjoint(self):
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A"))
        g.add_node(EntityNode(id="b", name="B"))
        g.add_edge(Association(source_id="a", target_id="b", assoc_type="x"))
        g.add_node(EntityNode(id="c", name="C"))
        g.add_node(EntityNode(id="d", name="D"))
        g.add_edge(Association(source_id="c", target_id="d", assoc_type="x"))
        clusters = g.detect_clusters()
        assert len(clusters) == 2

    def test_find_by_type(self):
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A", entity_type="company"))
        g.add_node(EntityNode(id="b", name="B", entity_type="person"))
        g.add_node(EntityNode(id="c", name="C", entity_type="company"))
        companies = g.find_by_type("company")
        assert len(companies) == 2

    def test_find_by_jurisdiction(self):
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A", jurisdiction="US"))
        g.add_node(EntityNode(id="b", name="B", jurisdiction="UK"))
        assert len(g.find_by_jurisdiction("US")) == 1

    def test_find_by_industry(self):
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A", industry="tech"))
        g.add_node(EntityNode(id="b", name="B", industry="finance"))
        assert len(g.find_by_industry("tech")) == 1


class TestAssociationClassifyType:
    def test_classify_personal(self):
        assert Association.classify_type("founder of company") == "personal"
        assert Association.classify_type("board member") == "personal"
        assert Association.classify_type("family relation") == "personal"

    def test_classify_competitive(self):
        assert Association.classify_type("competitor in market") == "competitive"

    def test_classify_financial(self):
        assert Association.classify_type("investor seed round") == "financial"
        assert Association.classify_type("acquisition completed") == "financial"
        assert Association.classify_type("debt financing") == "financial"

    def test_classify_contractual(self):
        assert Association.classify_type("vendor contract") == "contractual"
        assert Association.classify_type("partner agreement") == "contractual"

    def test_classify_other_fallback(self):
        assert Association.classify_type("met at conference") == "other"


class TestEntityGraphSerialization:
    def test_node_to_dict_and_json(self):
        node = EntityNode(id="n1", name="Acme", entity_type="company", jurisdiction="US")
        d = node.to_dict()
        assert d["id"] == "n1"
        assert d["jurisdiction"] == "US"
        j = node.to_json()
        assert "n1" in j

    def test_node_to_dict_excludes_none_fields(self):
        node = EntityNode(id="n1", name="A")
        d = node.to_dict()
        assert "jurisdiction" not in d

    def test_assoc_to_dict_and_json(self):
        assoc = Association(source_id="a", target_id="b", assoc_type="supplier", weight=0.8)
        d = assoc.to_dict()
        assert d["source_id"] == "a"
        assert d["weight"] == 0.8
        j = assoc.to_json()
        assert "a" in j

    def test_graph_to_dict_and_json(self):
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A"))
        g.add_node(EntityNode(id="b", name="B"))
        g.add_edge(Association(source_id="a", target_id="b", assoc_type="x"))
        d = g.to_dict()
        assert len(d["nodes"]) == 2
        assert len(d["edges"]) == 1
        j = g.to_json()
        assert "A" in j

    def test_graph_from_dict_roundtrip(self):
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A", entity_type="company"))
        g.add_node(EntityNode(id="b", name="B", entity_type="person"))
        g.add_edge(Association(source_id="a", target_id="b", assoc_type="employed_by"))
        d = g.to_dict()
        g2 = EntityGraph.from_dict(d)
        assert g2.node_count == 2
        assert g2.edge_count == 1
        assert g2.has_node("a")

    def test_graph_from_json_roundtrip(self):
        g = EntityGraph()
        g.add_node(EntityNode(id="x", name="X"))
        g.add_node(EntityNode(id="y", name="Y"))
        g.add_edge(Association(source_id="x", target_id="y", assoc_type="partnership"))
        j = g.to_json()
        g2 = EntityGraph.from_json(j)
        assert g2.node_count == 2
        assert g2.edge_count == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Entity — research patterns extraction
# ═══════════════════════════════════════════════════════════════════════════════

class TestResearchPatternsDomains:
    def test_extract_simple_domain(self):
        domains = extract_domains("visit example.com for info")
        assert len(domains) == 1
        assert domains[0].domain == "example.com"

    def test_extract_multiple_domains_deduplicated(self):
        domains = extract_domains("a.com and a.com and b.org")
        assert len(domains) == 2

    def test_extract_domains_with_www(self):
        domains = extract_domains("go to www.example.com")
        assert domains[0].domain == "example.com"

    def test_extract_no_domains(self):
        assert extract_domains("just text") == []


class TestResearchPatternsIPs:
    def test_extract_valid_ipv4(self):
        ips = extract_ip_addresses("server at 192.168.1.1")
        assert len(ips) == 1
        assert ips[0].address == "192.168.1.1"

    def test_extract_multiple_ips(self):
        ips = extract_ip_addresses("10.0.0.1 and 10.0.0.2")
        assert len(ips) == 2

    def test_extract_no_ips(self):
        assert extract_ip_addresses("no address") == []


class TestResearchPatternsSEC:
    def test_parse_sec_with_cik(self):
        filings = parse_sec_filing("CIK No: 0001234567")
        assert len(filings) == 1
        assert filings[0].cik == "0001234567"

    def test_parse_sec_with_file_number(self):
        filings = parse_sec_filing("Accession No. 1-12345")
        assert filings[0].file_number == "1-12345"

    def test_parse_sec_with_form_type(self):
        filings = parse_sec_filing("Form 10-K annual report")
        assert filings[0].form_type == "10-K"

    def test_parse_sec_complete_filing(self):
        filings = parse_sec_filing("CIK # 1234567890\nFile Number: 1-12345\nForm 8-K")
        assert len(filings) == 1
        f = filings[0]
        assert f.cik == "1234567890"
        assert f.file_number == "1-12345"
        assert f.form_type == "8-K"

    def test_parse_sec_nothing_found(self):
        assert parse_sec_filing("no sec data") == []


class TestResearchPatternsCompaniesHouse:
    def test_parse_companies_house_standard(self):
        records = parse_companies_house("Company No. SC123456 registered")
        assert len(records) == 1
        assert records[0].registration_number == "SC123456"

    def test_parse_companies_house_eight_digit(self):
        records = parse_companies_house("12345678")
        assert len(records) == 1
        assert records[0].registration_number == "12345678"

    def test_parse_companies_house_multiple(self):
        records = parse_companies_house("SC123456 and NI654321")
        assert len(records) == 2


class TestResearchPatternsFundingRounds:
    def test_detect_series_a(self):
        rounds = detect_funding_rounds("raised $5 million in Series A funding")
        assert len(rounds) == 1
        assert rounds[0].round_type == "Series A"

    def test_detect_seed_round(self):
        rounds = detect_funding_rounds("Seed round of $1M")
        assert len(rounds) == 1

    def test_detect_funding_with_amount(self):
        rounds = detect_funding_rounds("Series B round $10 million led by VC")
        assert len(rounds) >= 1
        if rounds:
            assert rounds[0].round_type == "Series B"

    def test_no_funding_rounds_in_plain_text(self):
        rounds = detect_funding_rounds("the company had a great quarter")
        assert rounds == []


class TestResearchPatternsAcquisitions:
    def test_detect_basic_acquisition(self):
        acqs = detect_acquisitions("acquired by BigCorp for cash")
        assert len(acqs) >= 1

    def test_detect_merger(self):
        acqs = detect_acquisitions("merged with OtherCo")
        assert len(acqs) >= 1

    def test_detect_active_acquisition(self):
        acqs = detect_acquisitions("AcmeCorp acquired WidgetCo in deal")
        assert len(acqs) >= 1

    def test_no_acquisitions_in_plain_text(self):
        acqs = detect_acquisitions("earnings report")
        assert acqs == []


class TestResearchEntityPipeline:
    def test_research_entity_empty_text(self):
        result = research_entity("")
        assert isinstance(result, EntityResearchResult)
        assert result.domains == []
        assert result.raw_text == ""

    def test_research_entity_with_domains_and_ips(self):
        result = research_entity("see example.com or 10.0.0.1 for details")
        assert len(result.domains) == 1
        assert len(result.ip_addresses) == 1
        assert result.companies_house_records == []
        assert result.funding_rounds == []

    def test_research_entity_full_pipeline(self):
        text = (
            "Company No. 12345678 filed Form 10-K at CIK # 9876543210.\n"
            "Visit example.com. Raised $3M Series B funding. acquired by GiantCorp."
        )
        result = research_entity(text)
        assert len(result.domains) == 1
        assert len(result.sec_filings) >= 1
        assert len(result.companies_house_records) == 1
        assert len(result.funding_rounds) >= 1
        assert len(result.acquisitions) >= 1
        assert result.raw_text == text

    def test_research_entity_raw_text_preserved(self):
        text = "sample text for preservation"
        result = research_entity(text)
        assert result.raw_text == text
