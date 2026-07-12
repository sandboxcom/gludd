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
    # CI/build surface — rewriting these could exfiltrate build-runner secrets
    # or silently disable guardrails. Path comparison is already lowercased in
    # _first_protected(), so these lowercase markers match case-insensitively.
    ".github",
    "/workflows/",
    "pyproject.toml",
    "makefile",
    "alembic",
    "/migrations/",
    "setup.cfg",
    "tox.ini",
    ".pre-commit",
    "dockerfile",
    "agents.md",
    "claude.md",
    "tasks.md",
    "bugs.md",
    "session.md",
)

# Bare-word markers that must match a whole PATH SEGMENT (or exact basename),
# not an arbitrary substring.  This prevents ``alembic`` from blocking
# ``src/alembic_runner.py``, ``makefile`` from blocking
# ``utils/makefile_parser.py``, and ``dockerfile`` from blocking
# ``src/dockerfile_parser.py``.
#
# A marker listed here is matched when:
#   • it equals any individual segment of the lowercased, normalised path, OR
#   • it equals the lowercased basename (covers ``Makefile`` at repo root).
#
# All other PROTECTED_PATH_MARKERS continue to be matched as substrings.
_SEGMENT_EXACT_MARKERS: frozenset[str] = frozenset(
    {"alembic", "makefile", "dockerfile"}
)

# Kinds whose change content is validated as YAML and then written in place.
_YAML_KINDS: frozenset[str] = frozenset({"config", "yaml", "role"})


@runtime_checkable
class CapabilityChecker(Protocol):
    """Decides whether a required capability is granted. Injected at integration."""

    def allows(self, capability: str) -> bool: ...


@runtime_checkable
class SafeWriter(Protocol):
    """Performs the actual filesystem write. Injected at integration.

    Implementations MAY return the resolved written path (``str``) for
    observability; callers that ignore the return value are unaffected. The
    ``str | None`` upper bound lets a concrete writer be a structural superset
    (e.g. ``AtomicSafeWriter.write -> str``) without tripping mypy's return-
    type covariance.
    """

    def write(self, path: str, content: str) -> str | None: ...


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


def _first_protected(
    target_paths: list[str], workspace_root: Path | None = None
) -> str | None:
    """Return the first target path matching the deny-list, else ``None``.

    Matching is **two-tier** to prevent over-blocking:

    * **Segment-exact markers** (``_SEGMENT_EXACT_MARKERS``): bare words like
      ``alembic``, ``makefile``, ``dockerfile`` that would falsely match
      ``alembic_runner.py`` or ``makefile_parser.py`` if treated as substrings.
      These are matched only when the marker equals an individual path segment or
      the exact basename (case-insensitive).

    * **Substring markers** (all other ``PROTECTED_PATH_MARKERS``): anchored by
      leading/trailing ``/`` or a leading ``.`` (e.g. ``/workflows/``,
      ``.github``, ``pyproject.toml``), so substring matching is safe and
      intentional for them.

    C9 fix (F2/F3): when ``workspace_root`` is provided, path resolution is
    anchored to ``workspace_root`` instead of CWD — closing the TOCTOU window
    where a cwd-shift could let a directory-traversal path evade the lexical
    check.  Resolution failure → fail-closed (return the path as protected).
    """
    from general_ludd.security.path_canonicalizer import is_denied_path

    for path in target_paths:
        normalised = os.path.normpath(urllib.parse.unquote(path))
        lowered = normalised.lower()

        # Lexical check via the canonical deny-list — catches substring and
        # segment-exact markers without needing a filesystem operation.
        if is_denied_path(lowered):
            return path

        # Resolve symlinks / .. traversal so `./secrets/../allowed` is caught.
        # C9 fix: resolve against workspace_root (not CWD) to prevent
        # cwd-shift evasion and to close the parent-dir TOCTOU window.
        # FAIL CLOSED (applier-D): if resolution raises we must NOT silently
        # fall back to the lexical path. Treat any resolve failure as
        # protected and return the path immediately.
        resolve_base: Path = workspace_root if workspace_root is not None else Path()
        try:
            resolved_lowered = (
                (resolve_base / path).resolve().as_posix().lower()
            )
        except Exception:
            return path

        if is_denied_path(resolved_lowered):
            return path

    return None


def _resolve_confined(
    target_paths: list[str], workspace_root: Path
) -> tuple[str | None, list[Path]]:
    """Resolve every target path and confine it to ``workspace_root``.

    Returns ``(escapee, resolved_paths)`` where:

    * ``escapee`` is the first raw target path resolving outside the root, or
      ``None`` if every path is confined.
    * ``resolved_paths`` holds the **resolved** :class:`Path` objects for the
      paths checked so far (complete only when ``escapee`` is ``None``).

    Returning the resolved paths is what closes the TOCTOU window (applier-C):
    the caller writes these resolved paths rather than the raw strings, so a
    symlink swapped after the check cannot redirect the write outside the root.

    Every path is anchored to ``workspace_root`` and resolved with symlinks
    followed, so ``../`` traversal, percent-encoded escapes, and absolute paths
    outside the root are all detected. A path is safe only if its resolved form
    is ``workspace_root`` itself or lives beneath it.
    """
    root = workspace_root.resolve()
    resolved_paths: list[Path] = []
    for path in target_paths:
        decoded = urllib.parse.unquote(path)
        # Joining against ``root`` anchors relative paths; for an absolute
        # ``decoded`` the join yields that absolute path, which the confinement
        # check then rejects unless it already lives under ``root``.
        resolved = (root / decoded).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            return path, resolved_paths
        resolved_paths.append(resolved)
    return None, resolved_paths


def _restore_snapshots(snapshots: list[tuple[Path, bytes | None]]) -> None:
    """Roll every target back to the bytes captured before the write.

    Used when a post-write validation fails so a broken config is never left on
    disk. For each ``(resolved, prior)`` pair:

    * ``prior is None`` -> the file did not exist before the write, so remove the
      newly-created file (best-effort).
    * ``prior`` is bytes -> restore that exact prior content.

    Restore is best-effort per target (an :class:`OSError` on one path does not
    abort the rest) — the goal is maximal recovery, never a second failure.
    """
    for resolved, prior in snapshots:
        try:
            if prior is None:
                if resolved.exists():
                    resolved.unlink()
            else:
                resolved.write_bytes(prior)
        except OSError:
            # Best-effort: continue restoring the remaining snapshots.
            continue


class UpdateApplier:
    """Apply an update plan through an injected writer and capability checker."""

    def __init__(
        self,
        writer: SafeWriter,
        capability_checker: CapabilityChecker,
        workspace_root: Path,
    ) -> None:
        self._writer = writer
        self._capability_checker = capability_checker
        self._workspace_root = workspace_root

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

        # 2. Workspace-confinement gate. Every target path must resolve INSIDE
        #    the workspace root. ``../`` traversal, percent-encoded escapes, and
        #    absolute paths outside the root are denied regardless of capability
        #    or kind. This runs before the protected-path check and before any
        #    write so an escaping path can never reach the SafeWriter.
        escapee, resolved_paths = _resolve_confined(
            target_paths, self._workspace_root
        )
        if escapee is not None:
            return ApplyResult(
                status="denied",
                target_paths=target_paths,
                evidence=f"path escapes workspace root: {escapee}",
            )

        # 3. Protected-path deny-list. Always wins over capability.
        protected = _first_protected(target_paths, self._workspace_root)
        if protected is not None:
            return ApplyResult(
                status="denied",
                target_paths=target_paths,
                evidence=f"protected path refused: {protected}",
            )

        kind = plan.kind

        # 4. Code changes are NEVER blind-applied here — propose only.
        if kind == "code":
            return ApplyResult(
                status="proposed",
                target_paths=target_paths,
                evidence=change_content,
            )

        # 4a. Empty-targets guard (C9 F4).  A non-code change with zero
        #     target paths must never report "applied" — nothing would be
        #     written.  Code changes exit above, so they are unaffected.
        if not target_paths:
            return ApplyResult(
                status="denied",
                target_paths=target_paths,
                evidence="no target paths specified — refusing to apply empty change",
            )

        # 5. YAML-shaped kinds: validate then write.
        if kind in _YAML_KINDS:
            try:
                yaml.safe_load(change_content)
            except yaml.YAMLError as exc:
                return ApplyResult(
                    status="denied",
                    target_paths=target_paths,
                    evidence=f"invalid yaml: {exc}",
                )

            # Snapshot each resolved target's PRIOR bytes before writing so a
            # post-write validation failure can be rolled back — a broken config
            # is never left on disk. ``None`` marks a file that did not exist.
            snapshots: list[tuple[Path, bytes | None]] = []
            for resolved in resolved_paths:
                try:
                    prior = resolved.read_bytes() if resolved.exists() else None
                except OSError:
                    prior = None
                snapshots.append((resolved, prior))

            try:
                # TOCTOU fix (applier-C): write the RESOLVED paths computed by
                # the confinement gate, NOT the raw target strings. A symlink
                # swapped between the check and this write cannot redirect the
                # write outside the confined, already-resolved location.
                for resolved in resolved_paths:
                    self._writer.write(str(resolved), change_content)
            except Exception as exc:  # write failure fails closed
                # Roll back anything already swapped before failing closed.
                _restore_snapshots(snapshots)
                return ApplyResult(
                    status="denied",
                    target_paths=target_paths,
                    evidence=f"write failed: {exc}",
                )

            # Post-write validation (recoverability): re-read the on-disk result
            # and confirm it still parses as YAML. If a target materialised on
            # disk but no longer parses (or cannot be read), restore EVERY
            # snapshot and deny — the config is rolled back to its prior state.
            # A writer that does not materialise a file leaves nothing on disk to
            # validate, so its absence is not treated as a failure.
            try:
                for resolved in resolved_paths:
                    if not resolved.exists():
                        continue
                    yaml.safe_load(resolved.read_text(encoding="utf-8"))
            except Exception as exc:
                _restore_snapshots(snapshots)
                return ApplyResult(
                    status="denied",
                    target_paths=target_paths,
                    evidence=f"post-write validation failed, rolled back: {exc}",
                )

            return ApplyResult(
                status="applied",
                target_paths=target_paths,
                evidence=f"applied {kind} change to {len(target_paths)} path(s)",
            )

        # 6. Unknown kind -> fail closed.
        return ApplyResult(
            status="denied",
            target_paths=target_paths,
            evidence=f"unsupported plan kind: {kind!r}",
        )
