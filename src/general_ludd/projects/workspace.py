"""Per-project filesystem workspace isolation."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class ProjectWorkspace:
    def __init__(
        self,
        project_id: str,
        base_dir: str = "/tmp/gludd-workspaces",
        workspace_path: str | None = None,
    ) -> None:
        self.project_id = project_id
        self._base_dir = base_dir
        if workspace_path:
            self.root = Path(workspace_path)
        else:
            self.root = Path(base_dir) / project_id

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

    def ensure_dirs(self) -> None:
        for d in (
            self.root,
            self.artifacts_dir,
            self.logs_dir,
            self.config_dir,
            self.repo_dir,
            self.private_data_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    def job_artifact_dir(self, job_id: str) -> Path:
        return self.artifacts_dir / job_id

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
