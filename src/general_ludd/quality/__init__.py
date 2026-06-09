"""Quality gate module — preflight checks and task completion verification."""

from __future__ import annotations

__all__ = (
    "CoverageResult",
    "MoleculeCoverageChecker",
    "MoleculeCoverageReport",
    "QualityGateChecker",
    "QualityGateResult",
    "check_coverage",
    "check_filestore",
    "check_lint",
    "check_molecule_coverage",
    "check_molecule_scenarios",
    "check_mypy",
    "check_playbooks",
    "check_python_coverage",
    "check_quality_gates",
    "check_sprint_boxes",
    "check_templates",
    "generate_backlog_from_audit",
    "run_completion_audit",
    "run_preflight",
    "verify_task_completion",
)

from general_ludd.quality.gate import QualityGateChecker
from general_ludd.quality.molecule_coverage import (
    MoleculeCoverageChecker,
    MoleculeCoverageReport,
)
from general_ludd.quality.preflight import (
    check_coverage,
    check_filestore,
    check_lint,
    check_molecule_scenarios,
    check_mypy,
    check_playbooks,
    check_sprint_boxes,
    check_templates,
    generate_backlog_from_audit,
    run_completion_audit,
    run_preflight,
    verify_task_completion,
)
from general_ludd.quality.tools import (
    CoverageResult,
    QualityGateResult,
    check_molecule_coverage,
    check_python_coverage,
    check_quality_gates,
)
