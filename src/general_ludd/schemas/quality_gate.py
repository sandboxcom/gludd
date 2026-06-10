"""Quality gate configuration schema."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class PythonQualityGate(BaseModel):
    enabled: bool = True
    line_coverage_min_percent: float = 90.0
    branch_coverage_min_percent: float = 80.0
    coverage_config_path: str = "pyproject.toml"
    pytest_args: list[str] = Field(default_factory=lambda: [
        "--cov",
        "--cov-report=term-missing",
        "--cov-report=xml",
    ])

    @field_validator("line_coverage_min_percent", "branch_coverage_min_percent")
    @classmethod
    def _percent_range(cls, v: float) -> float:
        if not (0.0 <= v <= 100.0):
            raise ValueError("coverage percent must be between 0.0 and 100.0")
        return v


class MoleculeQualityGate(BaseModel):
    enabled: bool = True
    coverage_min_percent: float = 100.0
    require_for_registered_playbooks: bool = True
    require_for_internal_tool_calls: bool = True
    require_for_roles: bool = True
    require_for_collections: bool = True
    require_for_templates_used_by_playbooks: bool = True
    require_verbose_verify_tasks: bool = True
    allow_configured_exemptions: bool = True
    exemption_max_age_days: int = 14
    idempotence_required_by_default: bool = True

    @field_validator("coverage_min_percent")
    @classmethod
    def _percent_range(cls, v: float) -> float:
        if not (0.0 <= v <= 100.0):
            raise ValueError("coverage percent must be between 0.0 and 100.0")
        return v

    @field_validator("exemption_max_age_days")
    @classmethod
    def _positive_int(cls, v: int) -> int:
        if v < 1:
            raise ValueError("exemption_max_age_days must be at least 1")
        return v


class AnsibleTestGate(BaseModel):
    enabled_for_custom_collection_plugins: bool = True


class EnforcementGate(BaseModel):
    fail_completion_when_below_gate: bool = True
    fail_merge_tag_push_reload_when_below_gate: bool = True
    block_todo_complete: bool = True
    block_commit: bool = True
    block_merge: bool = True
    block_tag: bool = True
    block_push: bool = True
    block_reload: bool = True


class QualityGateConfig(BaseModel):
    enabled: bool = True
    python: PythonQualityGate = Field(default_factory=PythonQualityGate)
    molecule: MoleculeQualityGate = Field(default_factory=MoleculeQualityGate)
    ansible_test: AnsibleTestGate = Field(default_factory=AnsibleTestGate)
    enforcement: EnforcementGate = Field(default_factory=EnforcementGate)
