"""Integration tests for NF.5 E2E Test Generation — full pipeline.

Exercises the four-stage pipeline end to end:

    code_path_analyzer → scenario_generator → write_e2e_tests → verify_coverage

A synthetic source module is written to ``tmp_path``, analyzed for public
symbols, mapped to E2E scenarios, rendered into pytest test files, and finally
measured for coverage via ``verify_coverage``.  Each stage's output feeds the
next, so a regression in any stage breaks the assertions downstream.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from general_ludd.agents.test_generation.code_path_analyzer import CodePathAnalyzer
from general_ludd.agents.test_generation.scenario_generator import ScenarioGenerator

ROOT = Path(__file__).resolve().parent.parent.parent
WRITE_SCRIPT = (
    ROOT
    / "collections/ansible_collections/general_ludd/e2e_test_gen"
    / "roles/write_e2e_tests/files/write_e2e_tests.py"
)
VERIFY_SCRIPT = (
    ROOT
    / "collections/ansible_collections/general_ludd/e2e_test_gen"
    / "roles/verify_coverage/files/verify_coverage.py"
)


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None, f"could not create spec for {path}"
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _has_tree_sitter() -> bool:
    try:
        import tree_sitter_python  # noqa: F401
        from tree_sitter import Language, Parser  # noqa: F401
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _has_tree_sitter(),
    reason="tree-sitter not installed; code_path_analyzer disabled",
)


# A source module whose public names hit multiple scenario keyword buckets so
# the ScenarioGenerator produces several scenarios (CRUD + auth + daemon).
SAMPLE_MODULE_SRC = '''\
"""Sample module exercising CRUD, auth, and daemon-restart code paths."""


def create_resource(name):
    return {"id": 1, "name": name}


def delete_resource(resource_id):
    return resource_id


def authenticate(token):
    return token == "ok"


def daemon_startup(config):
    return config


def _private_helper():
    return None
'''


@pytest.fixture
def sample_module(tmp_path: Path) -> Path:
    module_dir = tmp_path / "src"
    module_dir.mkdir()
    path = module_dir / "sample_module.py"
    path.write_text(SAMPLE_MODULE_SRC)
    return path


class TestStage1CodePathAnalyzer:
    """Stage 1 — symbol extraction from source."""

    def test_extracts_public_functions(self, sample_module: Path) -> None:
        symbols = CodePathAnalyzer().analyze(str(sample_module))
        public_names = {f.name for f in symbols.functions if f.is_public}
        assert {"create_resource", "delete_resource", "authenticate", "daemon_startup"} <= public_names

    def test_excludes_private_symbols(self, sample_module: Path) -> None:
        symbols = CodePathAnalyzer().analyze(str(sample_module))
        private_names = {f.name for f in symbols.functions if not f.is_public}
        assert "_private_helper" in private_names
        public_names = {f.name for f in symbols.functions if f.is_public}
        assert "_private_helper" not in public_names


class TestStage2ScenarioGenerator:
    """Stage 2 — symbol → scenario mapping."""

    def test_matches_multiple_scenarios(self, sample_module: Path) -> None:
        symbols = CodePathAnalyzer().analyze(str(sample_module))
        scenarios = ScenarioGenerator().generate(symbols)
        names = {s.name for s in scenarios}
        # create_resource/delete_resource → crud; authenticate → auth; daemon_startup → daemon_restart
        assert {"crud_lifecycle", "auth_flow", "daemon_restart"} <= names

    def test_coverage_targets_populated_from_symbols(self, sample_module: Path) -> None:
        symbols = CodePathAnalyzer().analyze(str(sample_module))
        scenarios = ScenarioGenerator().generate(symbols)
        crud = next(s for s in scenarios if s.name == "crud_lifecycle")
        assert "create_resource" in crud.coverage_targets
        assert "delete_resource" in crud.coverage_targets

    def test_steps_are_non_empty(self, sample_module: Path) -> None:
        symbols = CodePathAnalyzer().analyze(str(sample_module))
        scenarios = ScenarioGenerator().generate(symbols)
        for scen in scenarios:
            assert len(scen.steps) >= 3
            for step in scen.steps:
                assert step.action
                assert step.expected_result


class TestStage3WriteE2ETests:
    """Stage 3 — render validated scenarios into pytest test files."""

    def _scenarios_json(self, sample_module: Path) -> str:
        symbols = CodePathAnalyzer().analyze(str(sample_module))
        scenarios = ScenarioGenerator().generate(symbols)
        payload = {
            "module": str(sample_module),
            "scenarios": [
                {
                    "name": s.name,
                    "description": s.description,
                    "coverage_targets": s.coverage_targets,
                    "steps": [asdict(st) for st in s.steps],
                }
                for s in scenarios
            ],
        }
        return json.dumps(payload)

    def test_generates_one_file_per_scenario(self, sample_module: Path, tmp_path: Path) -> None:
        scenarios_file = tmp_path / "scenarios.json"
        scenarios_file.write_text(self._scenarios_json(sample_module))
        out_dir = tmp_path / "generated"
        result = subprocess.run(
            [sys.executable, str(WRITE_SCRIPT),
             "--scenarios-file", str(scenarios_file),
             "--output-dir", str(out_dir)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"write_e2e_tests failed: {result.stderr}"
        generated = sorted(out_dir.glob("test_e2e_generated_*.py"))
        assert len(generated) >= 3, f"expected >=3 test files, got {generated}"

    def test_generated_test_file_contains_assertions(self, sample_module: Path, tmp_path: Path) -> None:
        scenarios_file = tmp_path / "scenarios.json"
        scenarios_file.write_text(self._scenarios_json(sample_module))
        out_dir = tmp_path / "generated"
        subprocess.run(
            [sys.executable, str(WRITE_SCRIPT),
             "--scenarios-file", str(scenarios_file),
             "--output-dir", str(out_dir)],
            capture_output=True, text=True, timeout=30, check=True,
        )
        crud_files = [p for p in out_dir.glob("*.py") if "crud" in p.name]
        assert crud_files, "no crud test file generated"
        content = crud_files[0].read_text()
        assert "def test_" in content
        assert "assert" in content

    def test_manifest_written(self, sample_module: Path, tmp_path: Path) -> None:
        scenarios_file = tmp_path / "scenarios.json"
        scenarios_file.write_text(self._scenarios_json(sample_module))
        out_dir = tmp_path / "generated"
        manifest_path = tmp_path / "manifest.json"
        subprocess.run(
            [sys.executable, str(WRITE_SCRIPT),
             "--scenarios-file", str(scenarios_file),
             "--output-dir", str(out_dir),
             "--manifest", str(manifest_path)],
            capture_output=True, text=True, timeout=30, check=True,
        )
        manifest = json.loads(manifest_path.read_text())
        assert manifest["scenario_count"] >= 3
        assert len(manifest["test_files"]) == manifest["scenario_count"]


class TestStage4VerifyCoverage:
    """Stage 4 — run pytest-cov on generated tests, emit gap report."""

    def _generate_tests(self, sample_module: Path, tmp_path: Path) -> tuple[Path, Path]:
        """Run stages 1-3 and return (test_dir, scenarios_file)."""
        symbols = CodePathAnalyzer().analyze(str(sample_module))
        scenarios = ScenarioGenerator().generate(symbols)
        payload = {
            "module": str(sample_module),
            "scenarios": [
                {
                    "name": s.name,
                    "description": s.description,
                    "coverage_targets": s.coverage_targets,
                    "steps": [asdict(st) for st in s.steps],
                }
                for s in scenarios
            ],
        }
        scenarios_file = tmp_path / "scenarios.json"
        scenarios_file.write_text(json.dumps(payload))
        out_dir = tmp_path / "generated"
        subprocess.run(
            [sys.executable, str(WRITE_SCRIPT),
             "--scenarios-file", str(scenarios_file),
             "--output-dir", str(out_dir)],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return out_dir, scenarios_file

    def test_coverage_report_written(self, sample_module: Path, tmp_path: Path) -> None:
        test_dir, _ = self._generate_tests(sample_module, tmp_path)
        out = tmp_path / "coverage_report.json"
        result = subprocess.run(
            [sys.executable, str(VERIFY_SCRIPT),
             "--test-dir", str(test_dir),
             "--source-module", str(sample_module),
             "--output", str(out),
             "--threshold", "0"],
            capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, f"verify_coverage failed: {result.stderr[-800:]}"
        assert out.is_file()
        report = json.loads(out.read_text())
        assert report["status"] == "completed"
        assert "coverage_percent" in report
        assert "gap_report" in report

    def test_no_test_files_produces_skip_verdict(self, tmp_path: Path) -> None:
        empty_dir = tmp_path / "empty_tests"
        empty_dir.mkdir()
        out = tmp_path / "coverage_report.json"
        bogus_source = tmp_path / "no_module.py"
        bogus_source.write_text("x = 1\n")
        result = subprocess.run(
            [sys.executable, str(VERIFY_SCRIPT),
             "--test-dir", str(empty_dir),
             "--source-module", str(bogus_source),
             "--output", str(out)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        report = json.loads(out.read_text())
        assert report["verdict"] == "skip"
        assert report["status"] == "completed"


class TestFullPipelineEndToEnd:
    """End-to-end: analyze → generate → write → verify, asserting data flow."""

    def test_pipeline_produces_artifacts_at_each_stage(self, sample_module: Path, tmp_path: Path) -> None:
        # Stage 1
        symbols = CodePathAnalyzer().analyze(str(sample_module))
        assert any(f.is_public for f in symbols.functions), "analyzer found no public symbols"

        # Stage 2
        scenarios = ScenarioGenerator().generate(symbols)
        assert len(scenarios) >= 3, f"generator produced only {len(scenarios)} scenarios"

        # Stage 3
        scenarios_file = tmp_path / "scenarios.json"
        scenarios_file.write_text(json.dumps({
            "module": str(sample_module),
            "scenarios": [
                {
                    "name": s.name,
                    "description": s.description,
                    "coverage_targets": s.coverage_targets,
                    "steps": [asdict(st) for st in s.steps],
                }
                for s in scenarios
            ],
        }))
        test_dir = tmp_path / "generated"
        write_result = subprocess.run(
            [sys.executable, str(WRITE_SCRIPT),
             "--scenarios-file", str(scenarios_file),
             "--output-dir", str(test_dir)],
            capture_output=True, text=True, timeout=30,
        )
        assert write_result.returncode == 0, f"write stage failed: {write_result.stderr}"
        test_files = sorted(test_dir.glob("test_e2e_generated_*.py"))
        assert len(test_files) == len(scenarios)

        # Stage 4 — verify on the generated test files
        coverage_out = tmp_path / "coverage_report.json"
        verify_result = subprocess.run(
            [sys.executable, str(VERIFY_SCRIPT),
             "--test-dir", str(test_dir),
             "--source-module", str(sample_module),
             "--output", str(coverage_out),
             "--scenarios-file", str(scenarios_file),
             "--threshold", "0"],
            capture_output=True, text=True, timeout=120,
        )
        assert verify_result.returncode == 0, f"verify stage failed: {verify_result.stderr[-800:]}"
        report = json.loads(coverage_out.read_text())
        assert report["status"] == "completed"
        assert report["verdict"] in {"pass", "fail"}
        gap = report["gap_report"]
        # Cross-reference key exists and is structured
        assert "covered_targets" in gap
        assert "uncovered_targets" in gap
        assert "suggested_scenarios" in gap

    def test_pipeline_with_empty_module_produces_no_scenarios(self, tmp_path: Path) -> None:
        module_dir = tmp_path / "src"
        module_dir.mkdir()
        empty_module = module_dir / "empty.py"
        empty_module.write_text('"""No public symbols."""\n')

        symbols = CodePathAnalyzer().analyze(str(empty_module))
        scenarios = ScenarioGenerator().generate(symbols)
        assert scenarios == []

        # write_e2e_tests with zero scenarios should still succeed and emit a manifest
        scenarios_file = tmp_path / "scenarios.json"
        scenarios_file.write_text(json.dumps({"module": str(empty_module), "scenarios": []}))
        test_dir = tmp_path / "generated"
        result = subprocess.run(
            [sys.executable, str(WRITE_SCRIPT),
             "--scenarios-file", str(scenarios_file),
             "--output-dir", str(test_dir)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        manifest = json.loads((test_dir / "generated_tests.json").read_text())
        assert manifest["scenario_count"] == 0


class TestVerifyCoverageHelpersImportable:
    """Sanity: verify_coverage helpers can be imported and behave as documented."""

    def test_classify_range_covered(self) -> None:
        mod = _load_module(VERIFY_SCRIPT, "verify_coverage_helpers")
        state, missing = mod._classify_range(1, 5, executed={1, 2, 3, 4, 5}, missing=set())
        assert state == "covered"
        assert missing == []

    def test_classify_range_missing(self) -> None:
        mod = _load_module(VERIFY_SCRIPT, "verify_coverage_helpers")
        state, missing = mod._classify_range(1, 5, executed=set(), missing={1, 2, 3, 4, 5})
        assert state == "missing"
        assert missing == [1, 2, 3, 4, 5]
