"""Structural tests for quality/config.py — EnforcementGate, MoleculeQualityGate, PythonQualityGate, QualityGateConfig."""

from __future__ import annotations

from general_ludd.quality.config import (
    EnforcementGate,
    MoleculeQualityGate,
    PythonQualityGate,
    QualityGateConfig,
)


class TestEnforcementGate:
    def test_defaults(self):
        g = EnforcementGate()
        assert g.fail_completion_when_below_gate is True
        assert g.fail_merge_tag_push_reload_when_below_gate is True
        assert g.block_todo_complete is True
        assert g.block_commit is True
        assert g.block_merge is True
        assert g.block_tag is True
        assert g.block_push is True
        assert g.block_reload is True

    def test_override(self):
        g = EnforcementGate(block_commit=False, block_push=False)
        assert g.block_commit is False
        assert g.block_push is False
        assert g.block_merge is True


class TestMoleculeQualityGate:
    def test_defaults(self):
        g = MoleculeQualityGate()
        assert g.enabled is True
        assert g.coverage_min_percent == 100.0
        assert g.require_for_registered_playbooks is True
        assert g.require_for_internal_tool_calls is True
        assert g.require_for_roles is True
        assert g.require_for_collections is True
        assert g.require_for_templates_used_by_playbooks is True
        assert g.require_verbose_verify_tasks is True
        assert g.allow_configured_exemptions is True
        assert g.exemption_max_age_days == 14
        assert g.idempotence_required_by_default is True

    def test_validator_coverage_range(self):
        MoleculeQualityGate(coverage_min_percent=0.0)
        MoleculeQualityGate(coverage_min_percent=100.0)

    def test_validator_exemption_age(self):
        MoleculeQualityGate(exemption_max_age_days=1)


class TestPythonQualityGate:
    def test_defaults(self):
        g = PythonQualityGate()
        assert g.enabled is True
        assert g.line_coverage_min_percent == 90.0
        assert g.branch_coverage_min_percent == 80.0
        assert g.coverage_config_path == "pyproject.toml"
        assert isinstance(g.pytest_args, list)
        assert "--cov" in g.pytest_args
        assert "--cov-report=term-missing" in g.pytest_args
        assert "--cov-report=xml" in g.pytest_args

    def test_validator_coverage_range(self):
        PythonQualityGate(line_coverage_min_percent=0.0)
        PythonQualityGate(branch_coverage_min_percent=100.0)


class TestQualityGateConfig:
    def test_defaults(self):
        c = QualityGateConfig()
        assert c.enabled is True
        assert isinstance(c.python, PythonQualityGate)
        assert isinstance(c.molecule, MoleculeQualityGate)
        assert isinstance(c.enforcement, EnforcementGate)

    def test_sub_gate_defaults(self):
        c = QualityGateConfig()
        assert c.python.enabled is True
        assert c.molecule.enabled is True
        assert c.enforcement.block_commit is True
