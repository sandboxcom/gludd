from __future__ import annotations

from typing import Any


class ModelRouter:
    def __init__(
        self,
        role_mapping: dict[str, str] | None = None,
        default_profile_id: str | None = None,
        weak_model_profile_id: str | None = None,
    ) -> None:
        self._mapping: dict[str, str] = dict(role_mapping) if role_mapping else {}
        self.default_profile_id = default_profile_id
        self.weak_model_profile_id = weak_model_profile_id
        self._quality_map: dict[str, str] = {}
        self._latency_map: dict[str, str] = {}
        self._pattern_map: dict[str, str] = {}

    def resolve_role(self, role_name: str) -> str | None:
        if role_name == "weak" and self.weak_model_profile_id:
            return self.weak_model_profile_id
        result = self._mapping.get(role_name)
        if result is not None:
            return result
        if self.default_profile_id is not None:
            return self.default_profile_id
        return None

    def add_role(self, role_name: str, profile_id: str) -> None:
        self._mapping[role_name] = profile_id

    def add_quality_mapping(self, class_name: str, profile_id: str) -> None:
        self._quality_map[class_name] = profile_id

    def add_latency_mapping(self, class_name: str, profile_id: str) -> None:
        self._latency_map[class_name] = profile_id

    def add_pattern_mapping(self, pattern_name: str, role_name: str) -> None:
        self._pattern_map[pattern_name] = role_name

    def resolve_pattern(self, pattern_name: str) -> str | None:
        role_name = self._pattern_map.get(pattern_name)
        if role_name is None:
            return None
        return self.resolve_role(role_name)

    def list_patterns(self) -> list[str]:
        return list(self._pattern_map.keys())

    def resolve_by_quality(self, class_name: str) -> str | None:
        return self._quality_map.get(class_name)

    def resolve_by_latency(self, class_name: str) -> str | None:
        return self._latency_map.get(class_name)

    def list_roles(self) -> list[str]:
        return list(self._mapping.keys())

    def list_profiles_by_role(self, profile_id: str) -> list[str]:
        return [role for role, pid in self._mapping.items() if pid == profile_id]

    @classmethod
    def build_from_profiles(cls, profiles: list[Any]) -> ModelRouter:
        role_mapping: dict[str, str] = {}
        quality_map: dict[str, str] = {}
        latency_map: dict[str, str] = {}
        for p in profiles:
            pid = p.model_profile_id
            for role_name in getattr(p, "role_names", []):
                role_mapping[role_name] = pid
            if getattr(p, "quality_class", None):
                quality_map[p.quality_class] = pid
            if getattr(p, "latency_class", None):
                latency_map[p.latency_class] = pid
        router = cls(role_mapping=role_mapping)
        router._quality_map = quality_map
        router._latency_map = latency_map
        return router
