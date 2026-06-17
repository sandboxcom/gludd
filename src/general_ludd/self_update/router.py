"""Natural-language -> :class:`UpdatePlan` routing for gludd self-update.

This module is the first stage of the self-update pipeline (issue #81). It
takes a free-text request like::

    update gludd: increase the spend window to 2h

and produces a deterministic :class:`UpdatePlan` that downstream stages can act
on: which subsystem is affected, which concrete repo paths to edit, the
capability a worker needs to perform the edit safely, a priority, and a risk
level.

Routing philosophy
------------------
* **Config-first.** The overwhelming majority of self-updates are config/yaml
  tweaks (budgets, model profiles, lint ratchet, secrets). Those are
  low-risk and fast-tracked to HIGH priority.
* **Code is the exception.** We only escalate to a behavioural ``code`` change
  when the request clearly asks to change *behaviour* that no config key can
  express (verbs like "change how / rewrite / reimplement the logic"). Code
  edits get a low auto-priority and HIGH risk so a human reviews before they
  land.
* **Fail safe, never guess.** If nothing matches, or all candidate paths are
  missing, we return a ``config`` / HIGH-risk plan whose rationale says it
  needs human routing — we never fabricate a code-write target.

The router is pure and deterministic. Filesystem access is injected via
``path_exists`` so tests can pin it without touching the real tree.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

__all__ = [
    "DEFAULT_SUBSYSTEM_MAP",
    "UpdatePlan",
    "UpdateRequest",
    "UpdateRequestRouter",
    "UpdateTarget",
]

TargetKind = Literal["config", "yaml", "role", "code"]
RiskLevel = Literal["low", "medium", "high"]

# Capability strings handed to the executor: each maps to a distinct write
# scope so the self-update can be gated/audited per capability.
CAP_CONFIG_WRITE = "config_write"
CAP_COLLECTIONS_SELF_MODIFY = "collections_self_modify"
CAP_CODE_SELF_MODIFY = "code_self_modify"

# Priority bands (higher = sooner). Config/yaml low-risk edits are fast-tracked;
# role edits sit in the middle; code edits get a low auto-priority because they
# require human review.
PRIORITY_CONFIG = 8
PRIORITY_ROLE = 5
PRIORITY_CODE = 3

# Base directory for self-modifiable Ansible roles (verified against the real
# repo layout: collections/ansible_collections/general_ludd/agent/roles/<name>).
ROLES_BASE = "collections/ansible_collections/general_ludd/agent/roles"

# Verbs/phrases that signal a BEHAVIOURAL change — something a config key cannot
# express, so the request must be a code edit (with review).
_CODE_BEHAVIOUR_MARKERS = (
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


# A SubsystemSpec describes one routable subsystem. ``kind`` is the *default*
# target kind for that subsystem; it can still be escalated to ``code`` when the
# request carries a behavioural marker AND the subsystem has a code path.
SubsystemSpec = dict[str, object]


# Curated map built from the REAL repo layout (paths verified to exist).
#   - spend/budget/cost            -> budget config + spend_limiter.py (code)
#   - model/profile/provider/api   -> model_profiles config + gateway.py (code)
#   - role/playbook                -> collections roles (resolved per request)
#   - connector/log source/observ. -> observability package (code)
#   - lint/gate/ci/xdist/ratchet   -> config/ratchet.yml + Makefile
#   - secret/vault                 -> secrets config + secrets package (code)
#   - scheduler/parallel/dispatch  -> event_loop scheduling (code)
DEFAULT_SUBSYSTEM_MAP: dict[str, SubsystemSpec] = {
    "budget": {
        "kind": "config",
        "keywords": [
            "spend",
            "budget",
            "cost",
            "ceiling",
            "cap",
            "limit",
            "window",
        ],
        # Config-first: the budget/spend knobs live in config; the limiter
        # module is the code path only used when behaviour itself must change.
        "paths": ["config/ratchet.yml", "config"],
        "code_paths": ["src/general_ludd/controllers/spend_limiter.py"],
    },
    "model": {
        "kind": "config",
        "keywords": [
            "model",
            "profile",
            "provider",
            "api base",
            "api",
            "gateway",
            "llm",
        ],
        "paths": ["config/model_profiles", "config"],
        "code_paths": ["src/general_ludd/models/gateway.py"],
    },
    "lint": {
        "kind": "config",
        "keywords": [
            "lint",
            "gate",
            "ratchet",
            "ci",
            "xdist",
            "mypy",
            "ruff",
        ],
        "paths": ["config/ratchet.yml", "Makefile"],
        "code_paths": [],
    },
    "secret": {
        "kind": "config",
        "keywords": [
            "secret",
            "vault",
            "openbao",
            "credential",
            "alias",
        ],
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
        "keywords": [
            "scheduler",
            "schedule",
            "parallel",
            "dispatch",
            "concurrency",
            "next task",
        ],
        "paths": ["src/general_ludd/event_loop/loop.py"],
        "code_paths": ["src/general_ludd/event_loop/loop.py"],
    },
    "role": {
        "kind": "role",
        "keywords": [
            "role",
            "playbook",
        ],
        # Resolved dynamically to the named role's directory at route time.
        "paths": [ROLES_BASE],
        "code_paths": [],
    },
}


@dataclass(frozen=True)
class UpdateRequest:
    """A raw natural-language self-update request."""

    text: str


@dataclass(frozen=True)
class UpdateTarget:
    """Where an update lands: a subsystem and its concrete repo paths."""

    kind: TargetKind
    paths: list[str] = field(default_factory=list)
    subsystem: str = ""


@dataclass(frozen=True)
class UpdatePlan:
    """A deterministic, actionable plan derived from one request."""

    target: UpdateTarget
    change_summary: str
    capability_required: str
    priority: int
    risk: RiskLevel
    rationale: str


def _capability_for(kind: TargetKind) -> str:
    if kind == "role":
        return CAP_COLLECTIONS_SELF_MODIFY
    if kind == "code":
        return CAP_CODE_SELF_MODIFY
    return CAP_CONFIG_WRITE


def _priority_for(kind: TargetKind) -> int:
    if kind == "role":
        return PRIORITY_ROLE
    if kind == "code":
        return PRIORITY_CODE
    return PRIORITY_CONFIG


def _risk_for(kind: TargetKind) -> RiskLevel:
    if kind == "role":
        return "medium"
    if kind == "code":
        return "high"
    return "low"


class UpdateRequestRouter:
    """Route ``update gludd: <text>`` requests into :class:`UpdatePlan`s.

    Args:
        subsystem_map: Optional override of :data:`DEFAULT_SUBSYSTEM_MAP`. Each
            value is a spec dict with ``kind``, ``keywords``, ``paths`` and an
            optional ``code_paths`` list.
        path_exists: Injected existence predicate (defaults to
            :func:`os.path.exists`) so the router can be tested without the real
            filesystem and so only paths that genuinely exist are kept.
    """

    def __init__(
        self,
        subsystem_map: dict[str, SubsystemSpec] | None = None,
        path_exists: Callable[[str], bool] = os.path.exists,
    ) -> None:
        self._map: dict[str, SubsystemSpec] = (
            subsystem_map if subsystem_map is not None else DEFAULT_SUBSYSTEM_MAP
        )
        self._path_exists = path_exists

    # -- public API ----------------------------------------------------------
    def route(self, text: str) -> UpdatePlan:
        """Classify ``text`` and return an actionable :class:`UpdatePlan`."""
        request = UpdateRequest(text=text)
        normalized = self._normalize(request.text)

        match = self._classify(normalized)
        if match is None:
            return self._fail_safe(request, reason="no subsystem keyword matched")

        subsystem, spec = match
        kind = self._decide_kind(normalized, spec)
        paths = self._resolve_paths(subsystem, spec, kind, normalized)
        existing = [p for p in paths if self._path_exists(p)]

        if not existing:
            return self._fail_safe(
                request,
                reason=(
                    f"matched subsystem '{subsystem}' but none of its candidate "
                    f"paths exist on disk"
                ),
            )

        target = UpdateTarget(kind=kind, paths=existing, subsystem=subsystem)
        return UpdatePlan(
            target=target,
            change_summary=self._summarize(request.text),
            capability_required=_capability_for(kind),
            priority=_priority_for(kind),
            risk=_risk_for(kind),
            rationale=(
                f"Request classified as subsystem '{subsystem}' -> {kind} edit; "
                f"capability '{_capability_for(kind)}'."
            ),
        )

    # -- internals -----------------------------------------------------------
    @staticmethod
    def _normalize(text: str) -> str:
        lowered = text.lower().strip()
        # Strip a leading "update gludd:" / "update gludd -" prefix if present.
        for prefix in ("update gludd:", "update gludd -", "update gludd"):
            if lowered.startswith(prefix):
                lowered = lowered[len(prefix) :].strip()
                break
        return lowered

    def _classify(self, normalized: str) -> tuple[str, SubsystemSpec] | None:
        """Return the best (subsystem, spec) match, or None.

        Deterministic tie-break: iterate the map in insertion order and pick the
        subsystem whose keyword has the longest match (most specific wins). An
        explicit "role"/"playbook" mention always wins so "the X role" routes to
        collections even when X also reads like another subsystem.
        """
        # Role mention is an unambiguous signal -> route to collections.
        role_spec = self._map.get("role")
        if role_spec is not None:
            for kw in self._keywords(role_spec):
                if kw in normalized:
                    return "role", role_spec

        best: tuple[str, SubsystemSpec] | None = None
        best_len = 0
        for subsystem, spec in self._map.items():
            if subsystem == "role":
                continue
            for kw in self._keywords(spec):
                if kw in normalized and len(kw) > best_len:
                    best = (subsystem, spec)
                    best_len = len(kw)
        return best

    def _decide_kind(self, normalized: str, spec: SubsystemSpec) -> TargetKind:
        """Choose the target kind for a matched subsystem.

        Defaults to the subsystem's declared kind (config/yaml/role). Escalates
        to ``code`` only when (a) the request carries a behavioural marker AND
        (b) the subsystem actually has a code path to edit. A subsystem whose
        declared kind is already ``code`` (e.g. scheduler) stays code.
        """
        declared = str(spec.get("kind", "config"))
        if declared == "code":
            return "code"
        if declared == "role":
            return "role"

        code_paths = self._code_paths(spec)
        if code_paths and self._is_behaviour_change(normalized):
            return "code"
        return declared  # type: ignore[return-value]

    @staticmethod
    def _is_behaviour_change(normalized: str) -> bool:
        return any(marker in normalized for marker in _CODE_BEHAVIOUR_MARKERS)

    def _resolve_paths(
        self,
        subsystem: str,
        spec: SubsystemSpec,
        kind: TargetKind,
        normalized: str,
    ) -> list[str]:
        if subsystem == "role":
            return self._resolve_role_paths(spec, normalized)
        if kind == "code":
            return list(self._code_paths(spec)) or self._config_paths(spec)
        return self._config_paths(spec)

    def _resolve_role_paths(self, spec: SubsystemSpec, normalized: str) -> list[str]:
        """Turn "... the <name> role" into that role's directory path."""
        name = self._extract_role_name(normalized)
        if name:
            return [f"{ROLES_BASE}/{name}"]
        return self._config_paths(spec)

    @staticmethod
    def _extract_role_name(normalized: str) -> str | None:
        """Pull the role/playbook identifier out of the request text.

        Handles both "the <name> role" and "role <name>" phrasings. The name is
        the snake_case-ish token adjacent to the role/playbook keyword.
        """
        tokens = normalized.replace(",", " ").split()
        for anchor in ("role", "playbook"):
            if anchor not in tokens:
                continue
            idx = tokens.index(anchor)
            # "the <name> role" -> token before the anchor.
            if idx > 0:
                candidate = tokens[idx - 1].strip("'\"")
                if candidate not in ("the", "a", "an", "to", "this", "that"):
                    return candidate
            # "role <name>" -> token after the anchor.
            if idx + 1 < len(tokens):
                candidate = tokens[idx + 1].strip("'\"")
                if candidate not in ("the", "a", "an", "to", "this", "that"):
                    return candidate
        return None

    # -- spec accessors (defensive about the loosely-typed spec dict) --------
    @staticmethod
    def _keywords(spec: SubsystemSpec) -> list[str]:
        kws = spec.get("keywords", [])
        return [str(k) for k in kws] if isinstance(kws, list) else []

    @staticmethod
    def _config_paths(spec: SubsystemSpec) -> list[str]:
        paths = spec.get("paths", [])
        return [str(p) for p in paths] if isinstance(paths, list) else []

    @staticmethod
    def _code_paths(spec: SubsystemSpec) -> list[str]:
        paths = spec.get("code_paths", [])
        return [str(p) for p in paths] if isinstance(paths, list) else []

    @staticmethod
    def _summarize(text: str) -> str:
        return text.strip()

    def _fail_safe(self, request: UpdateRequest, *, reason: str) -> UpdatePlan:
        """A safe, no-guess plan for unroutable requests.

        Never fabricates a code-write target: kind='config', no paths, HIGH risk,
        and a rationale that flags it for human routing.
        """
        target = UpdateTarget(kind="config", paths=[], subsystem="unknown")
        return UpdatePlan(
            target=target,
            change_summary=self._summarize(request.text),
            capability_required=CAP_CONFIG_WRITE,
            priority=PRIORITY_CODE,
            risk="high",
            rationale=(
                f"Needs human routing: {reason}. Not auto-applying any change "
                f"(fail-safe: no code write guessed)."
            ),
        )
