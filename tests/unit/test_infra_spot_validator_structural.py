"""Structural tests for infra/spot_validator.py — spot/preemptible config validator."""

from __future__ import annotations

from general_ludd.infra.spot_validator import (
    SpotConfigValidator,
    SpotValidatorFinding,
)


class TestSpotValidatorFinding:
    def test_constructor(self):
        f = SpotValidatorFinding(
            stack_name="test-stack",
            severity="warning",
            message="test",
            use_spot_configured=False,
            use_spot_expected=True,
        )
        assert f.stack_name == "test-stack"
        assert f.severity == "warning"
        assert f.use_spot_configured is False
        assert f.use_spot_expected is True


class TestSpotConfigValidator:
    def test_default_constructor(self):
        v = SpotConfigValidator()
        assert v.default_spot is True

    def test_explicit_constructor(self):
        v = SpotConfigValidator(default_spot=False)
        assert v.default_spot is False

    def test_validate_missing_stack_dir(self):
        v = SpotConfigValidator()
        findings = v.validate("nonexistent-stack-12345")
        assert len(findings) == 1
        assert findings[0].severity == "warning"
        assert "not found" in findings[0].message

    def test_read_use_spot_missing_file(self):
        from pathlib import Path

        result = SpotConfigValidator._read_use_spot_default(Path("/nonexistent/path"))
        assert isinstance(result, bool)
        assert result is False

    def test_read_use_spot_no_variable(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "variables.tf").write_text("variable \"other\" { default = true }")
            result = SpotConfigValidator._read_use_spot_default(d)
            assert result is False

    def test_read_use_spot_true(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "variables.tf").write_text(
                'variable "use_spot" {\n  description = "use spot instances"\n  default = true\n}'
            )
            result = SpotConfigValidator._read_use_spot_default(d)
            assert result is True
