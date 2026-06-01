from __future__ import annotations

from typing import Any


class ProjectSecretsManager:
    def __init__(
        self,
        base_manager: Any,
        project_id: str,
    ) -> None:
        self._base = base_manager
        self._project_id = project_id

    def _scoped_path(self, path: str) -> str:
        return f"projects/{self._project_id}/{path}"

    def write_secret(self, path: str, value: dict[str, Any]) -> None:
        self._base.write_secret(self._scoped_path(path), value)

    def read_secret(self, path: str) -> dict[str, Any] | None:
        return self._base.read_secret(self._scoped_path(path))

    def resolve(self, alias_name: str) -> str | None:
        return self._base.resolve(alias_name)
