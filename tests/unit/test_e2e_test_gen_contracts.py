"""TDD contracts for end-to-end test generation.

Defines the expected shape of TestSpec, TestGenerator, TestHarness, and
TestReport before the implementation exists.  All tests should FAIL on first
run (red phase), then PASS after ``contracts.py`` is written.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

# ── TestSpec ─────────────────────────────────────────────────────────────────


class TestTestSpecContracts:
    def test_spec_requires_target_module(self):
        from general_ludd.agents.test_generation.contracts import TestSpec

        spec = TestSpec(target_module="src/example.py")
        assert spec.target_module == "src/example.py"

    def test_spec_defaults(self):
        from general_ludd.agents.test_generation.contracts import TestSpec

        spec = TestSpec(target_module="src/example.py")
        assert spec.coverage_threshold == 85.0
        assert spec.output_dir == "tests/e2e"
        assert spec.scenario_catalog == "default"
        assert spec.include_patterns == []

    def test_spec_rejects_empty_target_module(self):
        from general_ludd.agents.test_generation.contracts import TestSpec

        with pytest.raises(ValidationError):
            TestSpec(target_module="")

    def test_spec_coverage_threshold_bounded_0_100(self):
        from general_ludd.agents.test_generation.contracts import TestSpec

        TestSpec(target_module="src/x.py", coverage_threshold=0.0)
        TestSpec(target_module="src/x.py", coverage_threshold=100.0)
        with pytest.raises(ValidationError):
            TestSpec(target_module="src/x.py", coverage_threshold=-1.0)
        with pytest.raises(ValidationError):
            TestSpec(target_module="src/x.py", coverage_threshold=100.1)

    def test_spec_include_patterns_is_list(self):
        from general_ludd.agents.test_generation.contracts import TestSpec

        spec = TestSpec(target_module="src/x.py", include_patterns=["test_*.py"])
        assert spec.include_patterns == ["test_*.py"]

    def test_spec_extra_fields_forbidden(self):
        from general_ludd.agents.test_generation.contracts import TestSpec

        with pytest.raises(ValidationError):
            TestSpec(target_module="src/x.py", unknown_field=True)


# ── TestHarness ──────────────────────────────────────────────────────────────


class TestTestHarnessContracts:
    def test_harness_defaults(self):
        from general_ludd.agents.test_generation.contracts import TestHarness

        h = TestHarness()
        assert h.pytest_args == ["-v"]
        assert h.fixtures == []
        assert h.timeout_seconds == 300
        assert h.coverage_config == {"branch": True, "source": []}

    def test_harness_custom_pytest_args(self):
        from general_ludd.agents.test_generation.contracts import TestHarness

        h = TestHarness(pytest_args=["-v", "--tb=short", "-x"])
        assert h.pytest_args == ["-v", "--tb=short", "-x"]

    def test_harness_custom_fixtures(self):
        from general_ludd.agents.test_generation.contracts import TestHarness

        h = TestHarness(fixtures=["TestClient", "_run_cli"])
        assert h.fixtures == ["TestClient", "_run_cli"]

    def test_harness_timeout_positive(self):
        from general_ludd.agents.test_generation.contracts import TestHarness

        TestHarness(timeout_seconds=1)
        with pytest.raises(ValidationError):
            TestHarness(timeout_seconds=0)
        with pytest.raises(ValidationError):
            TestHarness(timeout_seconds=-5)

    def test_harness_extra_fields_forbidden(self):
        from general_ludd.agents.test_generation.contracts import TestHarness

        with pytest.raises(ValidationError):
            TestHarness(unknown=True)


# ── TestReport ───────────────────────────────────────────────────────────────


class TestTestReportContracts:
    def test_report_requires_verdict(self):
        from general_ludd.agents.test_generation.contracts import TestReport

        report = TestReport(verdict="pass")
        assert report.verdict == "pass"

    def test_report_verdict_only_valid_values(self):
        from general_ludd.agents.test_generation.contracts import TestReport

        TestReport(verdict="pass")
        TestReport(verdict="fail")
        TestReport(verdict="error")
        TestReport(verdict="partial")

    def test_report_defaults(self):
        from general_ludd.agents.test_generation.contracts import TestReport

        report = TestReport(verdict="pass")
        assert report.coverage_percent == 0.0
        assert report.generated_files == []
        assert report.scenarios == []
        assert report.symbol_coverage == {}
        assert report.errors == []
        assert report.duration_seconds == 0.0

    def test_report_coverage_percent_bounded(self):
        from general_ludd.agents.test_generation.contracts import TestReport

        TestReport(verdict="pass", coverage_percent=0.0)
        TestReport(verdict="pass", coverage_percent=100.0)
        with pytest.raises(ValidationError):
            TestReport(verdict="pass", coverage_percent=-0.1)
        with pytest.raises(ValidationError):
            TestReport(verdict="pass", coverage_percent=100.1)

    def test_report_full_structure(self):
        from general_ludd.agents.test_generation.contracts import TestReport

        report = TestReport(
            verdict="pass",
            coverage_percent=92.5,
            generated_files=["tests/e2e/test_generated_example.py"],
            scenarios=["crud_lifecycle", "auth_flow"],
            symbol_coverage={"func1": 100.0, "func2": 85.0},
            errors=[],
            duration_seconds=12.3,
        )
        assert report.coverage_percent == 92.5
        assert report.generated_files == ["tests/e2e/test_generated_example.py"]
        assert report.scenarios == ["crud_lifecycle", "auth_flow"]
        assert report.symbol_coverage == {"func1": 100.0, "func2": 85.0}
        assert report.duration_seconds == 12.3

    def test_report_errors_is_list_of_strings(self):
        from general_ludd.agents.test_generation.contracts import TestReport

        report = TestReport(verdict="error", errors=["Timeout"])
        assert report.errors == ["Timeout"]


# ── TestGenerator ────────────────────────────────────────────────────────────


class TestTestGeneratorContracts:
    def test_generator_requires_spec_and_harness(self):
        from general_ludd.agents.test_generation.contracts import (
            TestGenerator,
            TestHarness,
            TestSpec,
        )

        spec = TestSpec(target_module="src/example.py")
        harness = TestHarness()
        gen = TestGenerator(spec=spec, harness=harness)
        assert gen.spec == spec
        assert gen.harness == harness

    def test_generator_pipeline_stages_enum(self):
        from general_ludd.agents.test_generation.contracts import (
            PipelineStage,
            TestGenerator,
            TestHarness,
            TestSpec,
        )

        spec = TestSpec(target_module="src/example.py")
        harness = TestHarness()
        gen = TestGenerator(spec=spec, harness=harness)
        assert gen.pipeline_stages == [
            PipelineStage.ANALYZE,
            PipelineStage.GENERATE,
            PipelineStage.VALIDATE,
            PipelineStage.WRITE,
            PipelineStage.VERIFY,
        ]

    def test_generator_extra_fields_forbidden(self):
        from general_ludd.agents.test_generation.contracts import (
            TestGenerator,
            TestHarness,
            TestSpec,
        )

        spec = TestSpec(target_module="src/example.py")
        harness = TestHarness()
        with pytest.raises(ValidationError):
            TestGenerator(spec=spec, harness=harness, unknown=True)

    def test_generator_optional_pipeline_stages(self):
        from general_ludd.agents.test_generation.contracts import (
            PipelineStage,
            TestGenerator,
            TestHarness,
            TestSpec,
        )

        spec = TestSpec(target_module="src/example.py")
        harness = TestHarness()
        gen = TestGenerator(
            spec=spec,
            harness=harness,
            pipeline_stages=[PipelineStage.ANALYZE, PipelineStage.GENERATE],
        )
        assert len(gen.pipeline_stages) == 2
        assert PipelineStage.ANALYZE in gen.pipeline_stages
        assert PipelineStage.GENERATE in gen.pipeline_stages


# ── PipelineStage Enum ───────────────────────────────────────────────────────


class TestPipelineStageEnum:
    def test_all_five_stages_exist(self):
        from general_ludd.agents.test_generation.contracts import PipelineStage

        assert hasattr(PipelineStage, "ANALYZE")
        assert hasattr(PipelineStage, "GENERATE")
        assert hasattr(PipelineStage, "VALIDATE")
        assert hasattr(PipelineStage, "WRITE")
        assert hasattr(PipelineStage, "VERIFY")

    def test_stage_values_match_role_names(self):
        from general_ludd.agents.test_generation.contracts import PipelineStage

        assert PipelineStage.ANALYZE.value == "analyze_code_paths"
        assert PipelineStage.GENERATE.value == "generate_scenarios"
        assert PipelineStage.VALIDATE.value == "validate_scenarios"
        assert PipelineStage.WRITE.value == "write_e2e_tests"
        assert PipelineStage.VERIFY.value == "verify_coverage"


# ── SCHEMA_VERSION ───────────────────────────────────────────────────────────


class TestSchemaVersion:
    def test_schema_version_is_semver_string(self):
        from general_ludd.agents.test_generation.contracts import SCHEMA_VERSION

        assert isinstance(SCHEMA_VERSION, str)
        parts = SCHEMA_VERSION.split(".")
        assert len(parts) == 2
        assert all(p.isdigit() for p in parts)
