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
* **H.17 signature verification.** If ``verify_signature`` is supplied, the
  caller MUST provide a ``content_signature`` and a ``public_key``; the applier
  verifies the Ed25519 signature BEFORE any write. Missing/invalid → ``denied``.
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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

import yaml

from general_ludd.security.path_canonicalizer import (
    PROTECTED_PATH_MARKERS as _CANONICAL_PROTECTED_PATH_MARKERS,
)
from general_ludd.security.path_canonicalizer import (
    is_denied_path,
)

ApplyStatus = Literal["applied", "proposed", "denied"]
PROTECTED_PATH_MARKERS = _CANONICAL_PROTECTED_PATH_MARKERS

#: Kinds whose change content is validated as YAML and then written in place.
_YAML_KINDS: frozenset[str] = frozenset({"config", "yaml", "role"})


@runtime_checkable
class CapabilityChecker(Protocol):
    """Decides whether a required capability is granted. Injected at integration."""

    def allows(self, capability: str) -> bool:
        """Return whether the named capability is explicitly granted."""
        ...


@runtime_checkable
class SafeWriter(Protocol):
    """Performs the actual filesystem write. Injected at integration.

    Implementations MAY return the resolved written path (``str``) for
    observability; callers that ignore the return value are unaffected. The
    ``str | None`` upper bound lets a concrete writer be a structural superset
    (e.g. ``AtomicSafeWriter.write -> str``) without tripping mypy's return-
    type covariance.
    """

    def write(self, path: str, content: str) -> str | None:
        """Write content through the implementation's safety boundary."""
        ...


@runtime_checkable
class UpdatePlan(Protocol):
    """Structural shape of the plan the applier consumes.

    Declared locally so this module never imports ``self_update.router`` /
    ``self_update.__init__`` (owned by a sibling component).
    """

    @property
    def kind(self) -> str:
        """Return the update kind used to select the apply path."""
        ...

    @property
    def capability_required(self) -> str:
        """Return the capability that must be granted before applying."""
        ...

    @property
    def target_paths(self) -> list[str]:
        """Return repository-relative paths targeted by the update."""
        ...


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of an :meth:`UpdateApplier.apply` call."""

    status: ApplyStatus
    target_paths: list[str]
    evidence: str


def _denied_result(target_paths: list[str], evidence: str) -> ApplyResult:
    """Build the repeated fail-closed result shape in one place."""
    return ApplyResult(status="denied", target_paths=target_paths, evidence=evidence)


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
    for path in target_paths:
        decoded = urllib.parse.unquote(path)

        # S.9 fix: check the raw (pre-normpath) path BEFORE os.path.normpath
        # collapses ``..`` traversal.  Without this, ``guardrails/../../etc``
        # becomes ``../../etc`` — the ``guardrails`` marker is stripped and
        # the protected-path check bypasses entirely.
        if is_denied_path(decoded):
            return path

        normalised = os.path.normpath(decoded)
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
        """Bind explicit write, capability, and workspace safety boundaries."""
        self._writer = writer
        self._capability_checker = capability_checker
        self._workspace_root = workspace_root

    def apply(
        self,
        plan: UpdatePlan,
        change_content: str,
        *,
        content_signature: str = "",
        public_key: str = "",
        verify_signature: Callable[[str, str, str], bool] | None = None,
    ) -> ApplyResult:
        """Apply one validated update plan or return fail-closed evidence."""
        target_paths = list(plan.target_paths)
        signature_failure = self._signature_failure(
            change_content,
            target_paths,
            content_signature=content_signature,
            public_key=public_key,
            verify_signature=verify_signature,
        )
        if signature_failure is not None:
            return signature_failure
        capability_failure = self._capability_failure(plan, target_paths)
        if capability_failure is not None:
            return capability_failure

        # 2. Workspace-confinement gate. Every target path must resolve INSIDE
        #    the workspace root. ``../`` traversal, percent-encoded escapes, and
        #    absolute paths outside the root are denied regardless of capability
        #    or kind. This runs before the protected-path check and before any
        #    write so an escaping path can never reach the SafeWriter.
        escapee, resolved_paths = _resolve_confined(
            target_paths, self._workspace_root
        )
        if escapee is not None:
            return _denied_result(target_paths, f"path escapes workspace root: {escapee}")

        # 3. Protected-path deny-list. Always wins over capability.
        protected = _first_protected(target_paths, self._workspace_root)
        if protected is not None:
            return _denied_result(target_paths, f"protected path refused: {protected}")

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
            return _denied_result(
                target_paths, "no target paths specified — refusing to apply empty change"
            )

        # 5. YAML-shaped kinds: validate then write.
        if kind in _YAML_KINDS:
            return self._apply_yaml(
                kind,
                target_paths,
                resolved_paths,
                change_content,
            )

        # 6. Unknown kind -> fail closed.
        return _denied_result(target_paths, f"unsupported plan kind: {kind!r}")

    def _signature_failure(
        self,
        change_content: str,
        target_paths: list[str],
        *,
        content_signature: str,
        public_key: str,
        verify_signature: Callable[[str, str, str], bool] | None,
    ) -> ApplyResult | None:
        """Return a fail-closed signature result, or allow the next gate."""
        if verify_signature is None:
            return None
        if not content_signature or not public_key:
            return _denied_result(
                target_paths,
                "signature verification configured but no "
                "content_signature or public_key provided — refusing to apply",
            )
        try:
            verified = verify_signature(
                change_content,
                content_signature,
                public_key,
            )
        except Exception as exc:
            return _denied_result(target_paths, f"signature verification raised: {exc}")
        if verified:
            return None
        return _denied_result(
            target_paths,
            "signature verification failed — content may be "
            "tampered, unsigned, or signed with a different key",
        )

    def _capability_failure(
        self,
        plan: UpdatePlan,
        target_paths: list[str],
    ) -> ApplyResult | None:
        """Return a fail-closed capability result, or allow the path gates."""
        try:
            allowed = bool(
                self._capability_checker.allows(plan.capability_required)
            )
        except Exception as exc:
            return _denied_result(target_paths, f"capability check raised: {exc}")
        if allowed:
            return None
        return _denied_result(
            target_paths, f"capability not allowed: {plan.capability_required!r}"
        )

    def _apply_yaml(
        self,
        kind: str,
        target_paths: list[str],
        resolved_paths: list[Path],
        change_content: str,
    ) -> ApplyResult:
        """Validate, write, revalidate, and roll back YAML-shaped changes."""
        try:
            yaml.safe_load(change_content)
        except yaml.YAMLError as exc:
            return _denied_result(target_paths, f"invalid yaml: {exc}")
        snapshots: list[tuple[Path, bytes | None]] = []
        for resolved in resolved_paths:
            try:
                prior = resolved.read_bytes() if resolved.exists() else None
            except OSError:
                prior = None
            snapshots.append((resolved, prior))
        try:
            for resolved in resolved_paths:
                self._writer.write(str(resolved), change_content)
        except Exception as exc:
            _restore_snapshots(snapshots)
            return _denied_result(target_paths, f"write failed: {exc}")
        try:
            for resolved in resolved_paths:
                if resolved.exists():
                    yaml.safe_load(resolved.read_text(encoding="utf-8"))
        except Exception as exc:
            _restore_snapshots(snapshots)
            return _denied_result(
                target_paths, f"post-write validation failed, rolled back: {exc}"
            )
        return ApplyResult(
            status="applied",
            target_paths=target_paths,
            evidence=f"applied {kind} change to {len(target_paths)} path(s)",
        )
