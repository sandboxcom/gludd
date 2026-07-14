"""Structural tests for dogfood/validator.py — dogfood run validation."""

from __future__ import annotations

from unittest.mock import MagicMock

from general_ludd.dogfood.validator import (
    BypassFinding,
    DogfoodValidationResult,
    DogfoodValidator,
    _looks_like_local_bypass,
)


class TestDogfoodValidationResult:
    def test_minimal_construction(self):
        result = DogfoodValidationResult(
            valid=True,
            uses_configured_runtime=True,
            uses_configured_models=True,
            has_molecule_evidence=True,
            has_quality_gate_evidence=True,
        )
        assert result.valid is True
        assert result.uses_configured_runtime is True
        assert result.uses_configured_models is True
        assert result.has_molecule_evidence is True
        assert result.has_quality_gate_evidence is True

    def test_false_by_default(self):
        result = DogfoodValidationResult(
            valid=False,
            uses_configured_runtime=False,
            uses_configured_models=False,
            has_molecule_evidence=False,
            has_quality_gate_evidence=False,
        )
        assert result.valid is False


class TestBypassFinding:
    def test_construction(self):
        finding = BypassFinding(
            category="local_bypass",
            description="bypassed runtime",
            evidence='{"command": "bash -c ls"}',
        )
        assert finding.category == "local_bypass"
        assert finding.description == "bypassed runtime"


class TestLookLikeLocalBypass:
    def test_bash_c_detected(self):
        assert _looks_like_local_bypass("bash -c 'rm -rf /'") is True

    def test_pip_install_detected(self):
        assert _looks_like_local_bypass("pip install requests") is True

    def test_python_c_detected(self):
        assert _looks_like_local_bypass("python -c 'print(1)'") is True

    def test_sh_c_detected(self):
        assert _looks_like_local_bypass("sh -c './script.sh'") is True

    def test_npm_run_detected(self):
        assert _looks_like_local_bypass("npm run build") is True

    def test_normal_command_not_bypass(self):
        assert _looks_like_local_bypass("ls -la") is False

    def test_empty_string_not_bypass(self):
        assert _looks_like_local_bypass("") is False

    def test_ansible_command_not_bypass(self):
        assert _looks_like_local_bypass("ansible-playbook deploy.yml") is False


class TestDogfoodValidator:
    def test_validate_dogfood_run_success(self):
        validator = DogfoodValidator()
        mock_result = MagicMock()
        mock_result.success = True

        result = validator.validate_dogfood_run(mock_result)
        assert result.valid is True
        assert result.uses_configured_runtime is True
        assert result.uses_configured_models is True
        assert result.has_molecule_evidence is False
        assert result.has_quality_gate_evidence is False

    def test_validate_dogfood_run_failure(self):
        validator = DogfoodValidator()
        mock_result = MagicMock()
        mock_result.success = False

        result = validator.validate_dogfood_run(mock_result)
        assert result.valid is False
        assert result.uses_configured_runtime is False

    def test_check_no_local_bypasses_empty(self):
        validator = DogfoodValidator()
        findings = validator.check_no_local_bypasses([])
        assert findings == []

    def test_check_no_local_bypasses_no_matches(self):
        validator = DogfoodValidator()
        findings = validator.check_no_local_bypasses([
            {"command": "ansible-playbook deploy.yml", "runtime": "ansible"},
        ])
        assert findings == []

    def test_check_no_local_bypasses_detects_local_runtime(self):
        validator = DogfoodValidator()
        findings = validator.check_no_local_bypasses([
            {"command": "some command", "runtime": "local"},
        ])
        assert len(findings) == 1
        assert findings[0].category == "local_bypass"

    def test_check_no_local_bypasses_detects_command_pattern(self):
        validator = DogfoodValidator()
        findings = validator.check_no_local_bypasses([
            {"command": "bash -c 'echo hello'", "runtime": "ansible"},
        ])
        assert len(findings) == 1
        assert findings[0].category == "local_bypass"

    def test_check_artifacts_empty(self):
        validator = DogfoodValidator()
        assert validator.check_artifacts_use_configured_runtime([]) is True

    def test_check_artifacts_all_ansible(self):
        validator = DogfoodValidator()
        assert validator.check_artifacts_use_configured_runtime([
            {"runtime": "ansible"}, {"runtime": "ansible"},
        ]) is True

    def test_check_artifacts_mixed_runtime(self):
        validator = DogfoodValidator()
        assert validator.check_artifacts_use_configured_runtime([
            {"runtime": "ansible"}, {"runtime": "local"},
        ]) is False
