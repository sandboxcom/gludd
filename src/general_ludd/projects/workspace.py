"""Per-project filesystem workspace isolation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from general_ludd.security.sandboxes.state import safe_state_component
from general_ludd.security.state import project_state, secure_directory


def default_workspace_base() -> str:
    """Return the configured owner-only workspace root for this project."""
    return str(project_state().directory("workspaces"))


def confine_workspace_path(base_dir: str, workspace_path: str) -> str:
    """Resolve an untrusted ``workspace_path`` to a real path confined to ``base_dir``.

    The unauthenticated add-project path lets a caller supply ``workspace_path``;
    without this guard ``../../root/.ssh`` or an absolute ``/root/.ssh`` would let
    a project plant directories anywhere on disk. We:

      * refuse an absolute ``workspace_path`` (it must be a name under base_dir),
      * refuse any ``..`` component (the traversal primitive),
      * realpath-join it under base_dir and require the result stays under
        ``realpath(base_dir) + os.sep`` (or equals base_dir),

    returning the confined absolute path. Anything escaping base_dir raises
    ``ValueError`` BEFORE any directory is created. Mirrors
    ``git_automation.repo._reject_escaping_path``.
    """
    if os.path.isabs(workspace_path):
        raise ValueError(
            f"refusing absolute workspace_path (must be a name under base_dir): {workspace_path!r}"
        )
    norm = os.path.normpath(workspace_path)
    parts = norm.replace("\\", "/").split("/")
    if ".." in parts:
        raise ValueError(
            f"refusing workspace_path containing '..' traversal: {workspace_path!r}"
        )
    base_real = os.path.realpath(base_dir)
    target = os.path.realpath(os.path.join(base_real, norm))
    base_prefix = base_real.rstrip(os.sep) + os.sep
    if target != base_real and not target.startswith(base_prefix):
        raise ValueError(
            f"refusing workspace_path that escapes base_dir {base_dir!r}: {workspace_path!r}"
        )
    return target


class ProjectWorkspace:
    def __init__(
        self,
        project_id: str,
        base_dir: str | None = None,
        workspace_path: str | None = None,
    ) -> None:
        self.project_id = project_id
        if base_dir is None and workspace_path:
            candidate = Path(workspace_path).expanduser()
            if not candidate.is_absolute():
                candidate = Path.cwd() / candidate
            selected_base = candidate.parent
        elif base_dir is None:
            selected_base = Path(default_workspace_base())
        else:
            selected_base = Path(base_dir).expanduser()
            if not selected_base.is_absolute():
                selected_base = Path.cwd() / selected_base
        self._base_dir = str(selected_base)
        if workspace_path:
            candidate = Path(workspace_path).expanduser()
            if not candidate.is_absolute():
                candidate = selected_base / candidate
            resolved = candidate.resolve(strict=False)
            base_resolved = selected_base.resolve(strict=False)
            if not resolved.is_relative_to(base_resolved):
                raise ValueError(
                    f"workspace path escapes configured base: {workspace_path!r}",
                )
            self.root = candidate
        else:
            self.root = selected_base / safe_state_component(project_id)

    @property
    def artifacts_dir(self) -> Path:
        return self.root / "artifacts"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def repo_dir(self) -> Path:
        return self.root / "repo"

    @property
    def private_data_dir(self) -> Path:
        return self.root / "runner"

    @property
    def playbooks_dir(self) -> Path:
        return self.root / "playbooks"

    @property
    def templates_dir(self) -> Path:
        return self.root / "templates"

    @property
    def roles_dir(self) -> Path:
        return self.root / "roles"

    def ensure_dirs(self) -> None:
        for d in (
            self.root,
            self.artifacts_dir,
            self.logs_dir,
            self.config_dir,
            self.repo_dir,
            self.private_data_dir,
            self.playbooks_dir,
            self.templates_dir,
            self.roles_dir,
        ):
            secure_directory(d)

    def job_artifact_dir(self, job_id: str) -> Path:
        return self.artifacts_dir / safe_state_component(job_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "root": str(self.root),
            "artifacts_dir": str(self.artifacts_dir),
            "logs_dir": str(self.logs_dir),
            "config_dir": str(self.config_dir),
            "repo_dir": str(self.repo_dir),
            "private_data_dir": str(self.private_data_dir),
        }
