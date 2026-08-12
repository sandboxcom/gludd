"""Pydantic contracts for end-to-end test generation pipeline.

Formalises the data contracts for the five-role pipeline:
  analyze_code_paths -> generate_scenarios -> validate_scenarios
  -> write_e2e_tests -> verify_coverage.

Defines GenerationSpec (what to test), GenerationHarness (execution environment),
TestReport (structured output), GeneratorConfig (pipeline configuration),
and PipelineStage (ordered pipeline stages).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# PipelineStage
# ---------------------------------------------------------------------------


class PipelineStage(StrEnum):
    """Ordered stages of the E2E test generation pipeline."""

    ANALYZE = "analyze_code_paths"
    GENERATE = "generate_scenarios"
    VALIDATE = "validate_scenarios"
    WRITE = "write_e2e_tests"
    VERIFY = "verify_coverage"


PIPELINE_STAGES: list[PipelineStage] = [
    PipelineStage.ANALYZE,
    PipelineStage.GENERATE,
    PipelineStage.VALIDATE,
    PipelineStage.WRITE,
    PipelineStage.VERIFY,
]
"""Canonical pipeline stage order."""


# ---------------------------------------------------------------------------
# TestSpec — what to test
# ---------------------------------------------------------------------------


class GenerationSpec(BaseModel):
    """Specification of what module to test and how."""

    model_config = ConfigDict(extra="forbid")

    target_module: str = Field(min_length=1)
    coverage_threshold: float = Field(default=85.0, ge=0.0, le=100.0)
    output_dir: str = "tests/e2e"
    scenario_catalog: str = "default"
    include_patterns: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# TestHarness — execution environment
# ---------------------------------------------------------------------------


class GenerationHarness(BaseModel):
    """Execution environment for running generated tests."""

    model_config = ConfigDict(extra="forbid")

    pytest_args: list[str] = Field(default_factory=lambda: ["-v"])
    fixtures: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=300, gt=0)
    coverage_config: dict[str, object] = Field(default_factory=lambda: {"branch": True, "source": []})


# ---------------------------------------------------------------------------
# TestReport — structured output
# ---------------------------------------------------------------------------


class TestReport(BaseModel):
    """Structured report from a test generation pipeline run."""

    # This public domain model is imported by test modules.  Pytest otherwise
    # mistakes its ``Test*`` name for a test class and emits a collection warning.
    __test__ = False
    model_config = ConfigDict(extra="forbid")

    verdict: str = Field(
        pattern=r"^(pass|fail|error|partial)$",
        description="Overall verdict: pass, fail, error, or partial",
    )
    coverage_percent: float = Field(default=0.0, ge=0.0, le=100.0)
    generated_files: list[str] = Field(default_factory=list)
    scenarios: list[str] = Field(default_factory=list)
    symbol_coverage: dict[str, float] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    duration_seconds: float = Field(default=0.0, ge=0.0)


# ---------------------------------------------------------------------------
# TestGenerator — pipeline configuration
# ---------------------------------------------------------------------------


class GeneratorConfig(BaseModel):
    """Configuration for an E2E test generation pipeline run.

    Binds a GenerationSpec (what to test) with a GenerationHarness (how to run)
    and an optional pipeline stage selection.
    """

    model_config = ConfigDict(extra="forbid")

    spec: GenerationSpec
    harness: GenerationHarness
    pipeline_stages: list[PipelineStage] = Field(default_factory=lambda: list(PIPELINE_STAGES))
