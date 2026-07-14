"""Structural tests for infra/spot_validator.py — SpotConfigValidator."""

from __future__ import annotations

from pathlib import Path

import pytest

from general_ludd.infra.spot_validator import (
    SpotConfigValidator,
    SpotValidatorFinding,
)

VARIABLES_TF_SPOT_TRUE = '''variable "use_spot" {
  description = "Whether to use spot instances"
  type        = bool
  default     = true
}
'''

VARIABLES_TF_SPOT_FALSE = '''variable "use_spot" {
  description = "Whether to use spot instances"
  type        = bool
  default     = false
}
'''

VARIABLES_TF_NO_SPOT = '''variable "instance_type" {
  type = string
  default = "t3.medium"
}
'''


class TestSpotValidatorFinding:
    def test_ok_finding_construction(self):
        f = SpotValidatorFinding(
            stack_name="prod", severity="ok",
            message="use_spot=True matches expected=True",
            use_spot_configured=True, use_spot_expected=True,
        )
        assert f.severity == "ok"
        assert f.stack_name == "prod"

    def test_warning_finding_construction(self):
        f = SpotValidatorFinding(
            stack_name="dev", severity="warning",
            message="use_spot=False does not match expected=True",
            use_spot_configured=False, use_spot_expected=True,
        )
        assert f.severity == "warning"


class TestSpotConfigValidator:
    def test_default_spot_true(self):
        v = SpotConfigValidator(default_spot=True)
        assert v.default_spot is True

    def test_default_spot_false(self):
        v = SpotConfigValidator(default_spot=False)
        assert v.default_spot is False

    def test_missing_stack_dir(self):
        v = SpotConfigValidator()
        findings = v.validate("nonexistent_stack")
        assert len(findings) == 1
        assert findings[0].severity == "warning"
        assert "not found" in findings[0].message

    def test_stack_with_use_spot_true_matches(self, tmp_path: Path):
        stack_dir = tmp_path / "infra/terraform/stacks/prod"
        stack_dir.mkdir(parents=True)
        (stack_dir / "variables.tf").write_text(VARIABLES_TF_SPOT_TRUE)

        v = SpotConfigValidator(default_spot=True)
        findings = v.validate("prod", stacks_dir=str(tmp_path / "infra/terraform/stacks"))
        assert len(findings) == 1
        assert findings[0].severity == "ok"
        assert findings[0].use_spot_configured is True

    def test_stack_with_use_spot_false_mismatches(self, tmp_path: Path):
        stack_dir = tmp_path / "infra/terraform/stacks/dev"
        stack_dir.mkdir(parents=True)
        (stack_dir / "variables.tf").write_text(VARIABLES_TF_SPOT_FALSE)

        v = SpotConfigValidator(default_spot=True)
        findings = v.validate("dev", stacks_dir=str(tmp_path / "infra/terraform/stacks"))
        assert len(findings) == 1
        assert findings[0].severity == "warning"
        assert findings[0].use_spot_configured is False
        assert findings[0].use_spot_expected is True

    def test_stack_no_use_spot_variable_defaults_false(self, tmp_path: Path):
        stack_dir = tmp_path / "infra/terraform/stacks/legacy"
        stack_dir.mkdir(parents=True)
        (stack_dir / "variables.tf").write_text(VARIABLES_TF_NO_SPOT)

        v = SpotConfigValidator(default_spot=True)
        findings = v.validate("legacy", stacks_dir=str(tmp_path / "infra/terraform/stacks"))
        assert len(findings) == 1
        assert findings[0].use_spot_configured is False

    def test_read_use_spot_default_parses_true(self, tmp_path: Path):
        stack_dir = tmp_path
        (stack_dir / "variables.tf").write_text(VARIABLES_TF_SPOT_TRUE)
        result = SpotConfigValidator._read_use_spot_default(stack_dir)
        assert result is True

    def test_read_use_spot_default_parses_false(self, tmp_path: Path):
        stack_dir = tmp_path
        (stack_dir / "variables.tf").write_text(VARIABLES_TF_SPOT_FALSE)
        result = SpotConfigValidator._read_use_spot_default(stack_dir)
        assert result is False

    def test_read_use_spot_default_missing_file(self, tmp_path: Path):
        stack_dir = tmp_path / "nonexistent"
        stack_dir.mkdir(parents=True)
        result = SpotConfigValidator._read_use_spot_default(stack_dir)
        assert result is False
