"""Structural tests for agents/test_generation/contracts.py."""

from general_ludd.agents.test_generation.contracts import (
    PIPELINE_STAGES,
    PipelineStage,
    TestGenerator,
    TestHarness,
    TestReport,
    TestSpec,
)


class TestGenerationContracts:
    def test_imports(self):
        pass

    def test_pipeline_stages(self):
        assert PipelineStage.ANALYZE == "analyze_code_paths"
        assert PipelineStage.GENERATE == "generate_scenarios"
        assert PipelineStage.VALIDATE == "validate_scenarios"
        assert PipelineStage.WRITE == "write_e2e_tests"
        assert PipelineStage.VERIFY == "verify_coverage"

    def test_pipeline_stages_list(self):
        assert len(PIPELINE_STAGES) == 5
        assert PIPELINE_STAGES[0] == PipelineStage.ANALYZE

    def test_test_spec(self):
        spec = TestSpec(target_module="general_ludd.foo")
        assert spec.target_module == "general_ludd.foo"
        assert spec.coverage_threshold == 85.0
        assert spec.output_dir == "tests/e2e"

    def test_test_harness(self):
        harness = TestHarness()
        assert harness.pytest_args == ["-v"]
        assert harness.timeout_seconds == 300

    def test_test_report(self):
        report = TestReport(verdict="pass", coverage_percent=92.5)
        assert report.verdict == "pass"
        assert report.coverage_percent == 92.5
        assert report.generated_files == []

    def test_test_generator(self):
        spec = TestSpec(target_module="general_ludd.bar")
        harness = TestHarness(timeout_seconds=120)
        gen = TestGenerator(spec=spec, harness=harness)
        assert gen.harness.timeout_seconds == 120
        assert len(gen.pipeline_stages) == 5
