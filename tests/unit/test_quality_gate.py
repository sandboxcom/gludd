"""Structural tests for quality/gate.py — quality gate enforcement."""

from general_ludd.quality.gate import QualityGateChecker
from general_ludd.schemas.quality_gate import QualityGateConfig


class TestQualityGateChecker:
    def test_construct_default_config(self):
        checker = QualityGateChecker()
        assert checker.config is not None

    def test_construct_custom_config(self):
        config = QualityGateConfig()
        checker = QualityGateChecker(config)
        assert checker.config is config

    def test_check_python_coverage_returns_dict(self):
        checker = QualityGateChecker()
        result = checker.check_python_coverage(90.0)
        assert isinstance(result, dict)
        assert "gate" in result
        assert result["gate"] == "python_coverage"

    def test_check_python_coverage_passed(self):
        checker = QualityGateChecker()
        # Default threshold is 85% — 90% should pass
        result = checker.check_python_coverage(90.0)
        assert result["passed"] is True

    def test_check_python_coverage_failed(self):
        checker = QualityGateChecker()
        result = checker.check_python_coverage(10.0)
        assert result["passed"] is False

    def test_check_python_coverage_with_branch(self):
        checker = QualityGateChecker()
        result = checker.check_python_coverage(90.0, branch_percent=40.0)
        assert "checks" in result

    def test_check_molecule_coverage_returns_dict(self):
        checker = QualityGateChecker()
        result = checker.check_molecule_coverage(80, 100)
        assert isinstance(result, dict)
        assert "gate" in result

    def test_check_molecule_coverage_zero_required(self):
        checker = QualityGateChecker()
        result = checker.check_molecule_coverage(0, 0)
        assert result["passed"] is True

    def test_enforce_returns_dict(self):
        checker = QualityGateChecker()
        result = checker.enforce([{"passed": True}])
        assert isinstance(result, dict)
        assert "all_passed" in result
        assert result["all_passed"] is True

    def test_enforce_blocks_on_failure(self):
        checker = QualityGateChecker()
        result = checker.enforce([{"passed": False}])
        assert result["all_passed"] is False

    def test_enforce_has_block_flags(self):
        checker = QualityGateChecker()
        result = checker.enforce([{"passed": True}])
        assert "blocks_completion" in result
        assert "blocks_commit" in result
        assert "blocks_merge" in result
        assert "blocks_push" in result
        assert "blocks_reload" in result
