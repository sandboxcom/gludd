"""Cloud IAM data contracts — provider-agnostic dataclasses for role
definitions, functions, persona maps, and validation results.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CloudRoleDefinition:
    provider: str
    name: str
    description: str
    actions: list[str] = field(default_factory=list)
    not_actions: list[str] = field(default_factory=list)
    data_actions: list[str] = field(default_factory=list)
    not_data_actions: list[str] = field(default_factory=list)
    assignable_scopes: list[str] = field(default_factory=list)


@dataclass
class CloudFunction:
    provider: str
    name: str
    category: str
    risk_level: str
    required_denial: str = ""


@dataclass
class PersonaRoleMap:
    persona: str
    provider: str
    assignments: list[tuple[str, str, bool]] = field(default_factory=list)

    def roles(self) -> list[str]:
        return [role for role, _scope, _builtin in self.assignments]

    def scopes(self) -> list[str]:
        return [scope for _role, scope, _builtin in self.assignments]


@dataclass
class ValidationResult:
    status: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    provider: str = ""


__all__ = [
    "CloudFunction",
    "CloudRoleDefinition",
    "PersonaRoleMap",
    "ValidationResult",
]
