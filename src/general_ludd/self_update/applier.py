"""UpdateApplier — safely apply a self-update plan (#81 part 2).

This module is intentionally DECOUPLED. It does not import the capability
lattice or the filesystem-write policy directly; both are injected via small
structural Protocols (``CapabilityChecker`` / ``SafeWriter``) and wired at
integration time. It also does not import ``self_update.router`` or
``self_update.__init__`` (owned elsewhere); the plan it consumes is described by
a local structural Protocol.

Safety contract (fail-closed throughout):

* A capability the checker does not allow -> ``denied``.
* Any target path matching the built-in PROTECTED_PATH deny-list -> ``denied``,
  regardless of capability. This list always wins.
* ``config`` / ``yaml`` / ``role`` kinds: the change must parse as YAML
  (``yaml.safe_load``); only then is it written via the injected ``SafeWriter``
  -> ``applied``. A parse error or a writer error -> ``denied``.
* ``code`` kind: never blind-written here. Returned as ``proposed`` with the
  change carried as the proposal payload, to be routed to the self-improve
  A/B + hot_reload path under ``code_self_modify``.
* Any other / unknown kind or any uncertainty -> ``denied``.
"""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

import yaml

ApplyStatus = Literal["applied", "proposed", "denied"]

# Substrings that, if present in any target path, force a hard ``denied`` —
# regardless of the granted capability. These cover the guardrail/secret/policy
# surface that self-update must NEVER rewrite. Matching is substring-based and
# case-insensitive so path-form variations (``./``, nested dirs) cannot smuggle
# a protected target past the check.
PROTECTED_PATH_MARKERS: tuple[str, ...] = (
    "guardrails",
    "secrets",
    ".opencode",
    ".claude",
    "capability_policy",
    "action_policy",
    "fs_write_policy",
    "enforce-",
    "permissions",
)

# Kinds whose change content is validated as YAML and then written in place.
_YAML_KINDS: frozenset[str] = frozenset({"config", "yaml", "role"})


@runtime_checkable
class CapabilityChecker(Protocol):
    """Decides whether a required capability is granted. Injected at integration."""

    def allows(self, capability: str) -> bool: ...


@runtime_checkable
class SafeWriter(Protocol):
    """Performs the actual filesystem write. Injected at integration."""

    def write(self, path: str, content: str) -> None: ...


@runtime_checkable
class UpdatePlan(Protocol):
    """Structural shape of the plan the applier consumes.

    Declared locally so this module never imports ``self_update.router`` /
    ``self_update.__init__`` (owned by a sibling component).
    """

    @property
    def kind(self) -> str: ...

    @property
    def capability_required(self) -> str: ...

    @property
    def target_paths(self) -> list[str]: ...


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of an :meth:`UpdateApplier.apply` call."""

    status: ApplyStatus
    target_paths: list[str]
    evidence: str


def _first_protected(target_paths: list[str]) -> str | None:
    """Return the first target path matching the deny-list, else ``None``."""
    for path in target_paths:
        normalised = os.path.normpath(urllib.parse.unquote(path))
        lowered = normalised.lower()
        # Also resolve symlinks / .. traversal so `./secrets/../allowed` is caught.
        try:
            resolved_lowered = Path(path).resolve().as_posix().lower()
        except Exception:
            resolved_lowered = lowered
        for marker in PROTECTED_PATH_MARKERS:
            if marker in lowered or marker in resolved_lowered:
                return path
    return None


class UpdateApplier:
    """Apply an update plan through an injected writer and capability checker."""

    def __init__(
        self,
        writer: SafeWriter,
        capability_checker: CapabilityChecker,
    ) -> None:
        self._writer = writer
        self._capability_checker = capability_checker

    def apply(self, plan: UpdatePlan, change_content: str) -> ApplyResult:
        target_paths = list(plan.target_paths)

        # 1. Capability gate. Fail closed on anything not explicitly allowed
        #    (including a checker that itself raises).
        try:
            allowed = bool(
                self._capability_checker.allows(plan.capability_required)
            )
        except Exception as exc:  # uncertainty must fail closed
            return ApplyResult(
                status="denied",
                target_paths=target_paths,
                evidence=f"capability check raised: {exc}",
            )
        if not allowed:
            return ApplyResult(
                status="denied",
                target_paths=target_paths,
                evidence=(
                    f"capability not allowed: {plan.capability_required!r}"
                ),
            )

        # 2. Protected-path deny-list. Always wins over capability.
        protected = _first_protected(target_paths)
        if protected is not None:
            return ApplyResult(
                status="denied",
                target_paths=target_paths,
                evidence=f"protected path refused: {protected}",
            )

        kind = plan.kind

        # 3. Code changes are NEVER blind-applied here — propose only.
        if kind == "code":
            return ApplyResult(
                status="proposed",
                target_paths=target_paths,
                evidence=change_content,
            )

        # 4. YAML-shaped kinds: validate then write.
        if kind in _YAML_KINDS:
            try:
                yaml.safe_load(change_content)
            except yaml.YAMLError as exc:
                return ApplyResult(
                    status="denied",
                    target_paths=target_paths,
                    evidence=f"invalid yaml: {exc}",
                )

            try:
                for path in target_paths:
                    self._writer.write(path, change_content)
            except Exception as exc:  # write failure fails closed
                return ApplyResult(
                    status="denied",
                    target_paths=target_paths,
                    evidence=f"write failed: {exc}",
                )

            return ApplyResult(
                status="applied",
                target_paths=target_paths,
                evidence=f"applied {kind} change to {len(target_paths)} path(s)",
            )

        # 5. Unknown kind -> fail closed.
        return ApplyResult(
            status="denied",
            target_paths=target_paths,
            evidence=f"unsupported plan kind: {kind!r}",
        )
