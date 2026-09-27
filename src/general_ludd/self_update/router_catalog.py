"""Static routing policy for the legacy self-update request router."""

from __future__ import annotations

from typing import Literal

TargetKind = Literal["config", "yaml", "role", "code"]
RiskLevel = Literal["low", "medium", "high"]
SubsystemSpec = dict[str, object]

CAP_CONFIG_WRITE = "config_write"
CAP_COLLECTIONS_SELF_MODIFY = "collections_self_modify"
CAP_CODE_SELF_MODIFY = "code_self_modify"

PRIORITY_CONFIG = 8
PRIORITY_ROLE = 5
PRIORITY_CODE = 3

ROLES_BASE = "collections/ansible_collections/general_ludd/agent/roles"

CODE_BEHAVIOUR_MARKERS = (
    "how the",
    "how it",
    "the way",
    "rewrite",
    "reimplement",
    "re-implement",
    "refactor",
    "change the logic",
    "change the algorithm",
    "implement",
    "new behaviour",
    "new behavior",
    "dispatch logic",
    "picks the next",
    "selection logic",
)

DEFAULT_SUBSYSTEM_MAP: dict[str, SubsystemSpec] = {
    "budget": {
        "kind": "config",
        "keywords": ["spend", "budget", "cost", "ceiling", "cap", "limit", "window"],
        "paths": ["config/ratchet.yml", "config"],
        "code_paths": ["src/general_ludd/controllers/spend_limiter.py"],
    },
    "model": {
        "kind": "config",
        "keywords": ["model", "profile", "provider", "api base", "api", "gateway", "llm"],
        "paths": ["config/model_profiles", "config"],
        "code_paths": ["src/general_ludd/models/gateway.py"],
    },
    "lint": {
        "kind": "config",
        "keywords": ["lint", "gate", "ratchet", "ci", "xdist", "mypy", "ruff"],
        "paths": ["config/ratchet.yml", "Makefile"],
        "code_paths": [],
    },
    "secret": {
        "kind": "config",
        "keywords": ["secret", "vault", "openbao", "credential", "alias"],
        "paths": ["src/general_ludd/secrets/config.py", "config"],
        "code_paths": ["src/general_ludd/secrets/config.py"],
    },
    "connector": {
        "kind": "config",
        "keywords": [
            "connector",
            "log source",
            "log sources",
            "observability",
            "tracing",
            "trace",
            "telemetry",
        ],
        "paths": ["src/general_ludd/observability"],
        "code_paths": ["src/general_ludd/observability"],
    },
    "scheduler": {
        "kind": "code",
        "keywords": ["scheduler", "schedule", "parallel", "dispatch", "concurrency", "next task"],
        "paths": ["src/general_ludd/event_loop/loop.py"],
        "code_paths": ["src/general_ludd/event_loop/loop.py"],
    },
    "role": {
        "kind": "role",
        "keywords": ["role", "playbook"],
        "paths": [ROLES_BASE],
        "code_paths": [],
    },
}


__all__ = (
    "CAP_CODE_SELF_MODIFY",
    "CAP_COLLECTIONS_SELF_MODIFY",
    "CAP_CONFIG_WRITE",
    "CODE_BEHAVIOUR_MARKERS",
    "DEFAULT_SUBSYSTEM_MAP",
    "PRIORITY_CODE",
    "PRIORITY_CONFIG",
    "PRIORITY_ROLE",
    "ROLES_BASE",
    "RiskLevel",
    "SubsystemSpec",
    "TargetKind",
)
