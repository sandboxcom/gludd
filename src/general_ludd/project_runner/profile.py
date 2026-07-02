"""``project.yml`` profile: the per-target-project command map + exec allowlist.

A target repo declares, in a ``project.yml`` at its root, how gludd should run
its checks::

    name: my-app
    allowed_exec: [npm, pytest, ruff, terraform, psql, semgrep]
    env_passthrough: [NODE_ENV, CI]   # extra non-secret vars to forward
    commands:
      test: npm test
      lint: ruff check .
      typecheck: npm run typecheck
      build: npm run build
      sast: semgrep --config auto --error .
      migrate: alembic upgrade head

Security model (mirrors the MCP launcher allowlist in mcp/transport.py):
- Each command is parsed with ``shlex`` (never ``shell=True``).
- Shell metacharacters are REJECTED so a command can't chain/redirect/subshell.
- The basename of argv[0] must be in ``allowed_exec`` — fail-closed. The
  operator can opt out with ``GLUDD_PROJECT_ALLOW_ANY_EXEC=1`` (parallel to
  ``GLUDD_MCP_ALLOW_ANY_EXEC``) for a fully-trusted target.

This module only VALIDATES + resolves commands; execution + jailing live in
:mod:`general_ludd.project_runner.runner`.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

# Reject any command carrying shell metacharacters — no chaining, piping,
# redirection, subshells, globbing-to-shell, or env-expansion. Parity with the
# make-only Bash policy + mcp/transport.py's injection guard.
_SHELL_META_RE = re.compile(r"[;&|<>$`\\!(){}\[\]*?~\n\r]")

_ALLOW_ANY_EXEC_ENV = "GLUDD_PROJECT_ALLOW_ANY_EXEC"


class ProjectProfileError(ValueError):
    """Raised when a project.yml is missing, malformed, or a command is unsafe."""


class ProjectProfile(BaseModel):
    """Validated ``project.yml`` — the logical-check → shell-command map."""

    name: str = Field(default="target-project")
    # Logical check name (test/lint/build/typecheck/sast/dast/migrate/...) → the
    # exact command string to run in the target workspace.
    commands: dict[str, str] = Field(default_factory=dict)
    # argv[0] basenames permitted to run. Empty ⇒ nothing runs unless the
    # GLUDD_PROJECT_ALLOW_ANY_EXEC opt-out is set.
    allowed_exec: list[str] = Field(default_factory=list)
    # Extra environment variable NAMES to forward from gludd's own environment
    # into target-project commands, on top of the always-present sanitized base
    # (PATH/HOME/locale/temp — see runner._build_env). This is an allowlist:
    # gludd's secrets (ZAI_API_KEY, tokens, …) are NEVER inherited unless a name
    # is listed here. Default is a small set of standard, non-secret build-context
    # vars so common toolchains behave; keep additions minimal.
    env_passthrough: list[str] = Field(default_factory=lambda: ["NODE_ENV", "CI"])

    @field_validator("commands")
    @classmethod
    def _no_empty_commands(cls, v: dict[str, str]) -> dict[str, str]:
        for name, cmd in v.items():
            if not isinstance(cmd, str) or not cmd.strip():
                raise ProjectProfileError(f"command '{name}' is empty")
        return v

    def has(self, check: str) -> bool:
        """True when the profile declares a command for ``check``."""
        return check in self.commands and bool(self.commands[check].strip())

    def resolve_argv(self, check: str) -> list[str]:
        """Parse + safety-check the command for ``check`` into an argv list.

        Raises :class:`ProjectProfileError` when the check is undeclared, the
        command carries shell metacharacters, or argv[0] is not allow-listed
        (unless GLUDD_PROJECT_ALLOW_ANY_EXEC is set).
        """
        if not self.has(check):
            raise ProjectProfileError(f"no '{check}' command in project.yml")
        raw = self.commands[check].strip()
        if _SHELL_META_RE.search(raw):
            raise ProjectProfileError(
                f"command '{check}' contains shell metacharacters (not allowed): {raw!r}"
            )
        try:
            argv = shlex.split(raw)
        except ValueError as exc:  # unbalanced quotes, etc.
            raise ProjectProfileError(f"command '{check}' is unparsable: {exc}") from exc
        if not argv:
            raise ProjectProfileError(f"command '{check}' is empty after parsing")
        exe = os.path.basename(argv[0])
        allow_any = os.getenv(_ALLOW_ANY_EXEC_ENV, "").strip().lower() in {"1", "true", "yes", "on"}
        if not allow_any and exe not in self.allowed_exec:
            raise ProjectProfileError(
                f"executable '{exe}' for check '{check}' is not in allowed_exec "
                f"{self.allowed_exec} (set {_ALLOW_ANY_EXEC_ENV}=1 to override)"
            )
        return argv


def load_project_profile(workspace: str | Path, filename: str = "project.yml") -> ProjectProfile:
    """Load + validate ``project.yml`` from ``workspace``.

    Raises :class:`ProjectProfileError` if the file is absent or malformed so
    callers fail closed (no silent "run nothing").
    """
    import yaml

    root = Path(workspace).resolve()
    path = root / filename
    if not path.is_file():
        raise ProjectProfileError(f"no {filename} in {root}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ProjectProfileError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ProjectProfileError(f"{path} must be a mapping, got {type(data).__name__}")
    try:
        return ProjectProfile(**data)
    except ProjectProfileError:
        raise
    except Exception as exc:  # pydantic ValidationError → our error type
        raise ProjectProfileError(f"{path} is invalid: {exc}") from exc
