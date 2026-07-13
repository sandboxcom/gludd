"""Structural tests for schemas/quality_gate.py — quality gate configuration models."""

from __future__ import annotations

import pytest

from general_ludd.schemas.quality_gate import (
    AnsibleTestGate,
    EnforcementGate,
    MoleculeQualityGate,
    PythonQualityGate,
    QualityGateConfig,
)


class TestPythonQualityGate:
    def test_defaults(self):
        qg = PythonQualityGate()
        assert qg.enabled is True
        assert qg.line_coverage_min_percent == 90.0
        assert qg.branch_coverage_min_percent == 80.0
        assert qg.coverage_config_path == "pyproject.toml"
        assert isinstance(qg.pytest_args, list)
        assert "--cov" in qg.pytest_args

    def test_custom_coverage(self):
        qg = PythonQualityGate(line_coverage_min_percent=95.0, branch_coverage_min_percent=85.0)
        assert qg.line_coverage_min_percent == 95.0
        assert qg.branch_coverage_min_percent == 85.0

    def test_line_coverage_rejects_out_of_range(self):
        with pytest.raises(ValueError, match="between 0.0 and 100.0"):
            PythonQualityGate(line_coverage_min_percent=-1.0)
        with pytest.raises(ValueError, match="between 0.0 and 100.0"):
            PythonQualityGate(line_coverage_min_percent=101.0)

    def test_branch_coverage_rejects_out_of_range(self):
        with pytest.raises(ValueError, match="between 0.0 and 100.0"):
            PythonQualityGate(branch_coverage_min_percent=101.0)

    def test_coverage_edge_zero(self):
        qg = PythonQualityGate(line_coverage_min_percent=0.0, branch_coverage_min_percent=0.0)
        assert qg.line_coverage_min_percent == 0.0
        assert qg.branch_coverage_min_percent == 0.0

    def test_coverage_edge_hundred(self):
        qg = PythonQualityGate(line_coverage_min_percent=100.0, branch_coverage_min_percent=100.0)
        assert qg.line_coverage_min_percent == 100.0
        assert qg.branch_coverage_min_percent == 100.0


class TestMoleculeQualityGate:
    def test_defaults(self):
        mq = MoleculeQualityGate()
        assert mq.enabled is True
        assert mq.coverage_min_percent == 100.0
        assert mq.require_for_registered_playbooks is True
        assert mq.require_for_internal_tool_calls is True
        assert mq.require_for_roles is True
        assert mq.require_for_collections is True
        assert mq.require_for_templates_used_by_playbooks is True
        assert mq.require_verbose_verify_tasks is True
        assert mq.allow_configured_exemptions is True
        assert mq.exemption_max_age_days == 14
        assert mq.idempotence_required_by_default is True

    def test_coverage_rejects_out_of_range(self):
        with pytest.raises(ValueError, match="between 0.0 and 100.0"):
            MoleculeQualityGate(coverage_min_percent=-0.1)
        with pytest.raises(ValueError, match="between 0.0 and 100.0"):
            MoleculeQualityGate(coverage_min_percent=100.1)

    def test_exemption_age_rejects_zero(self):
        with pytest.raises(ValueError, match="at least 1"):
            MoleculeQualityGate(exemption_max_age_days=0)

    def test_exemption_age_rejects_negative(self):
        with pytest.raises(ValueError, match="at least 1"):
            MoleculeQualityGate(exemption_max_age_days=-5)

    def test_exemption_age_edge_one(self):
        mq = MoleculeQualityGate(exemption_max_age_days=1)
        assert mq.exemption_max_age_days == 1


class TestAnsibleTestGate:
    def test_defaults(self):
        atg = AnsibleTestGate()
        assert atg.enabled_for_custom_collection_plugins is True


class TestEnforcementGate:
    def test_defaults_all_true(self):
        eg = EnforcementGate()
        assert eg.fail_completion_when_below_gate is True
        assert eg.fail_merge_tag_push_reload_when_below_gate is True
        assert eg.block_todo_complete is True
        assert eg.block_commit is True
        assert eg.block_merge is True
        assert eg.block_tag is True
        assert eg.block_push is True
        assert eg.block_reload is True


class TestQualityGateConfig:
    def test_defaults(self):
        qgc = QualityGateConfig()
        assert qgc.enabled is True
        assert isinstance(qgc.python, PythonQualityGate)
        assert isinstance(qgc.molecule, MoleculeQualityGate)
        assert isinstance(qgc.ansible_test, AnsibleTestGate)
        assert isinstance(qgc.enforcement, EnforcementGate)

    def test_disabled_config(self):
        qgc = QualityGateConfig(enabled=False)
        assert qgc.enabled is False

    def test_nested_config_custom(self):
        qgc = QualityGateConfig(
            python=PythonQualityGate(line_coverage_min_percent=85.0),
            enforcement=EnforcementGate(block_merge=False),
        )
        assert qgc.python.line_coverage_min_percent == 85.0
        assert qgc.enforcement.block_merge is False
