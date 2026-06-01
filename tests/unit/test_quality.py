"""Unit tests for quality gates."""

from general_ludd.quality.gate import QualityGateChecker
from general_ludd.schemas.quality_gate import QualityGateConfig


class TestQualityGate:
    def test_quality_gate_reads_python_coverage_threshold_from_config(self):
        config = QualityGateConfig()
        checker = QualityGateChecker(config)
        result = checker.check_python_coverage(85.0)
        assert result["passed"] is False
        check = result["checks"][0]
        assert check["required"] == 90.0

    def test_quality_gate_passes_when_above_threshold(self):
        config = QualityGateConfig()
        checker = QualityGateChecker(config)
        result = checker.check_python_coverage(95.0)
        assert result["passed"] is True

    def test_quality_gate_reads_molecule_coverage_threshold_from_config(self):
        config = QualityGateConfig()
        checker = QualityGateChecker(config)
        result = checker.check_molecule_coverage(covered=3, required=5)
        assert result["passed"] is False
        assert result["percent"] == 60.0

    def test_quality_gate_blocks_completion_when_below_gate(self):
        checker = QualityGateChecker()
        gate_results = [
            checker.check_python_coverage(50.0),
            checker.check_molecule_coverage(0, 5),
        ]
        enforcement = checker.enforce(gate_results)
        assert enforcement["blocks_completion"] is True

    def test_quality_gate_blocks_commit_merge_tag_push_reload_on_failure(self):
        checker = QualityGateChecker()
        gate_results = [checker.check_python_coverage(50.0)]
        enforcement = checker.enforce(gate_results)
        assert enforcement["blocks_commit"] is True
        assert enforcement["blocks_merge"] is True
        assert enforcement["blocks_push"] is True
        assert enforcement["blocks_reload"] is True

    def test_quality_gate_all_pass_no_blocks(self):
        checker = QualityGateChecker()
        gate_results = [
            checker.check_python_coverage(95.0),
            checker.check_molecule_coverage(5, 5),
        ]
        enforcement = checker.enforce(gate_results)
        assert enforcement["all_passed"] is True
        assert enforcement["blocks_completion"] is False

    def test_branch_coverage_below_threshold(self):
        config = QualityGateConfig()
        checker = QualityGateChecker(config)
        result = checker.check_python_coverage(95.0, branch_percent=70.0)
        assert result["passed"] is False

    def test_molecule_coverage_zero_required_is_pass(self):
        checker = QualityGateChecker()
        result = checker.check_molecule_coverage(covered=0, required=0)
        assert result["passed"] is True
