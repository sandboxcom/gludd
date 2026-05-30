"""Role-to-model routing."""

from __future__ import annotations


class ModelRouter:
    def __init__(self, role_mapping: dict[str, str] | None = None) -> None:
        self._mapping: dict[str, str] = dict(role_mapping) if role_mapping else {}

    def resolve_role(self, role_name: str) -> str | None:
        return self._mapping.get(role_name)

    def add_role(self, role_name: str, profile_id: str) -> None:
        self._mapping[role_name] = profile_id

    def list_roles(self) -> list[str]:
        return list(self._mapping.keys())
