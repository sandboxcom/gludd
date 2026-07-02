"""Target-project toolchain runner.

The KEYSTONE capability that lets gludd work an EXTERNAL polyglot project
(Python/React/Postgres/Terraform/Redis/Mosquitto with its own lints, tests,
SAST/DAST) rather than only self-hosting on its own ``make test`` + ruff/mypy/
pytest.

A :class:`~general_ludd.project_runner.profile.ProjectProfile` (loaded from a
``project.yml`` in the target repo) maps logical checks — ``test``, ``lint``,
``build``, ``typecheck``, ``sast``, ``dast``, ``migrate`` … — to the concrete
shell commands that project uses. :class:`~general_ludd.project_runner.runner.ProjectCommandRunner`
runs a named check inside the jailed workspace and parses the outcome into a
structured :class:`~general_ludd.project_runner.runner.CheckResult`. This gives
the agent/gate a single, project-agnostic way to run + interpret a target
project's toolchain, replacing gludd's hardcoded self-gates for external work.
"""

from __future__ import annotations

from general_ludd.project_runner.profile import (
    ProjectProfile,
    ProjectProfileError,
    load_project_profile,
)
from general_ludd.project_runner.runner import (
    CheckResult,
    ProjectCommandRunner,
)

__all__ = [
    "CheckResult",
    "ProjectCommandRunner",
    "ProjectProfile",
    "ProjectProfileError",
    "load_project_profile",
]
