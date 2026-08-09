"""Tests for e2e test generation wired through SandboxCapabilityRouter."""

from __future__ import annotations

from pathlib import Path

from general_ludd.agents.test_generation.contracts import (
    GenerationHarness,
    TestReport,
    GenerationSpec,
)
from general_ludd.agents.test_generation.test_generator import GeneratorImpl
from general_ludd.agents.test_generation.test_harness import HarnessRunner
from general_ludd.agents.test_generation.test_reporter import TestReporter
from general_ludd.sandbox.contracts import IsolationLevel, SandboxConfig


class TestE2EThroughRouter:
    def test_generator_accepts_sandbox_config(self) -> None:
        spec = GenerationSpec(target_module="general_ludd.foo")
        config = SandboxConfig(backend="process", isolation=IsolationLevel.NONE)
        gen = GeneratorImpl(spec=spec, harness=GenerationHarness(), sandbox_config=config)
        assert gen.sandbox_config is config

    def test_generator_defaults_to_none_isolation(self) -> None:
        spec = GenerationSpec(target_module="general_ludd.foo")
        gen = GeneratorImpl(spec=spec, harness=GenerationHarness())
        assert gen.sandbox_config is not None
        assert gen.sandbox_config.isolation == IsolationLevel.NONE
        assert gen.sandbox_config.backend == "process"

    def test_end_to_end_generate_and_run(self, tmp_path: Path) -> None:
        spec = GenerationSpec(
            target_module="general_ludd.e2e_demo",
            output_dir=str(tmp_path),
        )
        generator = GeneratorImpl(spec=spec, harness=GenerationHarness())
        output_files = generator.generate()
        assert len(output_files) > 0

        runner = HarnessRunner()
        result = runner.execute(test_dir=str(tmp_path))
        assert result is not None

        report = TestReporter.score(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            generated_files=output_files,
        )
        assert isinstance(report, TestReport)
        assert report.verdict in ("pass", "fail", "error", "partial")

    def test_report_contains_generated_files(self, tmp_path: Path) -> None:
        spec = GenerationSpec(
            target_module="general_ludd.e2e_demo",
            output_dir=str(tmp_path),
        )
        generator = GeneratorImpl(spec=spec, harness=GenerationHarness())
        output_files = generator.generate()

        runner = HarnessRunner()
        result = runner.execute(test_dir=str(tmp_path))

        report = TestReporter.score(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            generated_files=output_files,
        )
        assert len(report.generated_files) == len(output_files)
