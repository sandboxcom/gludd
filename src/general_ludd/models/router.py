from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class RouterProfileProtocol(Protocol):
    """Structural type for a model profile consumed by ``build_from_profiles``.

    Matches ``ModelProfile`` (pydantic model in gateway.py) and any
    duck-typed equivalent exposing these four routing attributes.
    """

    model_profile_id: str
    role_names: list[str]
    quality_class: str | None
    latency_class: str | None


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

    def resolve_role(self, role_name: str, strict: bool = False) -> str | None:
        """Resolve a role name to a model profile ID.

        Args:
            role_name: The role to look up.
            strict: When True, raise ValueError for any role that is not
                explicitly mapped (i.e. would fall through to the default
                or return None).  This prevents arbitrary role strings from
                silently gaining default-model access (D-19).
                The ``"weak"`` sentinel is always accepted when
                ``weak_model_profile_id`` is set, regardless of strict.
                The gateway call-site (``call_model_by_role``) should pass
                ``strict=True`` — that is a 1-line follow-up in gateway.py.
        """
        if role_name == "weak" and self.weak_model_profile_id:
            return self.weak_model_profile_id
        result = self._mapping.get(role_name)
        if result is not None:
            return result
        # Role is not in the explicit mapping.
        if strict:
            raise ValueError(
                f"Unrecognised role {role_name!r}: not present in role_mapping and "
                "strict=True was requested.  Add the role to the ModelRouter "
                "role_mapping or pass strict=False to fall through to the default."
            )
        if self.default_profile_id is not None:
            return self.default_profile_id
        return None

    def add_role(self, role_name: str, profile_id: str) -> None:
        self._mapping[role_name] = profile_id

    def set_role_routing(self, role_name: str, profile_id: str) -> None:
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
    def build_from_profiles(cls, profiles: Sequence[RouterProfileProtocol]) -> ModelRouter:
        role_mapping: dict[str, str] = {}
        quality_map: dict[str, str] = {}
        latency_map: dict[str, str] = {}
        for p in profiles:
            pid = p.model_profile_id
            for role_name in p.role_names:
                role_mapping[role_name] = pid
            if p.quality_class:
                quality_map[p.quality_class] = pid
            if p.latency_class:
                latency_map[p.latency_class] = pid
        router = cls(role_mapping=role_mapping)
        router._quality_map = quality_map
        router._latency_map = latency_map
        return router
