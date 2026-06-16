"""
Reusable filesystem-write allow/deny policy for general_ludd.agent modules.

Any write-capable module or action (worktree creation, file copy, template
render, ...) can call :func:`enforce_write_policy` BEFORE it touches the
filesystem to guarantee the destination resolves inside an explicit allowlist
of path roots.  The policy is **default-DENY**: a path is rejected unless it
provably resolves under one of the configured allowed roots.

Threat model / rejected vectors
--------------------------------
* Absolute paths outside every allowed root (``/etc/passwd``, ``/root/...``).
* ``..`` traversal in the supplied path (``allowed/../../etc``), evaluated
  both lexically (a literal ``..`` component is refused outright) and after
  normalisation (the resolved target must still be inside a root).
* Symlink escapes — a path whose real (symlink-resolved) location lands
  outside the roots, even when the lexical path looks contained.  Both the
  final component and intermediate components are resolved.
* Encoded / NUL-byte trickery — a NUL (``\\x00``) anywhere in the path is
  refused (it can truncate the path at the OS layer).
* Sensitive targets — writes that touch ``.git`` internals, SSH keys,
  ``.env``/secret files, and similar are refused even when they sit inside an
  allowed root, because corrupting them is a privilege/escape risk.

This util has **no third-party dependencies** (stdlib ``os`` only) so it runs
inside Ansible module execution on a managed node without extra installs,
matching the constraint already documented in ``gludd.py``.

Usage in a module
-----------------
    from ansible_collections.general_ludd.agent.plugins.module_utils.fs_write_policy import (
        WritePolicy,
        WritePolicyError,
        default_policy,
    )

    policy = default_policy(
        workspace="/workspace/myrepo",
        worktree_root="/tmp/worktrees",
    )
    try:
        safe = policy.check(worktree_path)
    except WritePolicyError as exc:
        module.fail_json(**error_result(f"write denied by policy: {exc}"))
        return
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Sensitive path components — refused even inside an allowed root.
# Matched case-sensitively against each component of the resolved path.
# ---------------------------------------------------------------------------

#: Directory/file names that must never be a write target or a parent of one.
SENSITIVE_NAMES: frozenset[str] = frozenset(
    {
        ".git",  # git internals — corrupting these is an integrity/escape risk
        ".ssh",  # ssh keys / known_hosts / authorized_keys
        ".gnupg",  # gpg keyring
        ".aws",  # cloud credentials
        ".netrc",  # plaintext credentials
        ".env",  # dotenv secrets
        ".secrets",
        "secrets",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "id_dsa",
        "authorized_keys",
        "known_hosts",
        ".npmrc",
        ".pypirc",
        ".dockercfg",
        ".docker",
        ".kube",
    }
)

#: Filename suffixes that signal a secret/key file (matched on the basename).
SENSITIVE_SUFFIXES: tuple[str, ...] = (
    ".pem",
    ".key",
    "_rsa",
    "_ed25519",
    "_ecdsa",
    "_dsa",
)


class WritePolicyError(Exception):
    """Raised (fail-closed) when a write target violates the policy."""


def _normalize_root(root: str) -> str:
    """Return the absolute, symlink-resolved form of a configured root.

    Roots are resolved with ``os.path.realpath`` so that an allowlist entry
    that is itself a symlink (e.g. ``/tmp`` -> ``/private/tmp`` on macOS) still
    matches targets resolved the same way.
    """
    return os.path.realpath(os.path.abspath(root))


def _is_within(target: str, root: str) -> bool:
    """True iff ``target`` is ``root`` itself or strictly beneath it.

    Both arguments must already be absolute + realpath-resolved.  The trailing
    separator guard prevents ``/workspace-evil`` from matching root
    ``/workspace`` via a naive ``startswith``.
    """
    if target == root:
        return True
    root_prefix = root.rstrip(os.sep) + os.sep
    return target.startswith(root_prefix)


@dataclass
class WritePolicy:
    """An allow/deny policy for filesystem write targets.

    Parameters
    ----------
    allowed_roots:
        Explicit allowlist of path roots a write may land under.  Stored
        realpath-resolved.  Empty => everything is denied (strict default-DENY).
    sensitive_names:
        Component names refused even inside an allowed root.
    sensitive_suffixes:
        Basename suffixes refused even inside an allowed root.
    """

    allowed_roots: list[str] = field(default_factory=list)
    sensitive_names: frozenset[str] = SENSITIVE_NAMES
    sensitive_suffixes: tuple[str, ...] = SENSITIVE_SUFFIXES

    def __post_init__(self) -> None:
        # Resolve + de-dupe roots up front so check() is cheap and consistent.
        seen: list[str] = []
        for r in self.allowed_roots:
            if not r:
                continue
            nr = _normalize_root(r)
            if nr not in seen:
                seen.append(nr)
        self.allowed_roots = seen

    # -- internal helpers ---------------------------------------------------

    def _resolved_target(self, path: str) -> str:
        """Resolve ``path`` to an absolute, symlink-followed location.

        ``os.path.realpath`` walks and resolves every symlink in the path,
        including intermediate components, so a symlink planted partway through
        an otherwise-allowed path cannot smuggle the real target outside the
        roots.  Non-existent leaf components are fine — realpath resolves the
        existing prefix and appends the rest lexically.
        """
        return os.path.realpath(os.path.abspath(path))

    def _reject_sensitive(self, resolved: str, original: str) -> None:
        parts = resolved.replace("\\", "/").split("/")
        for comp in parts:
            if not comp:
                continue
            if comp in self.sensitive_names:
                raise WritePolicyError(
                    f"refusing write touching sensitive path component "
                    f"{comp!r}: {original!r}"
                )
        base = parts[-1] if parts else ""
        for suffix in self.sensitive_suffixes:
            if base.endswith(suffix):
                raise WritePolicyError(
                    f"refusing write to sensitive key/secret file {base!r}: "
                    f"{original!r}"
                )

    # -- public API ---------------------------------------------------------

    def check(self, path: str) -> str:
        """Validate ``path`` as a write target; return its resolved location.

        Raises :class:`WritePolicyError` (fail-closed) on any violation.  A
        return value is ONLY produced for a path that provably resolves inside
        an allowed root and touches no sensitive component.
        """
        if not isinstance(path, str) or path == "":
            raise WritePolicyError("refusing empty/invalid write path")

        # NUL byte can truncate the path at the syscall layer — refuse outright.
        if "\x00" in path:
            raise WritePolicyError("refusing write path containing NUL byte")

        # Lexical traversal primitive: a literal `..` component is never needed
        # for a legitimate target and is the classic escape — refuse before any
        # resolution so the intent is rejected even if the cwd happens to
        # contain it.
        lex_parts = os.path.normpath(path).replace("\\", "/").split("/")
        if ".." in path.replace("\\", "/").split("/") or ".." in lex_parts:
            raise WritePolicyError(
                f"refusing write path containing '..' traversal: {path!r}"
            )

        resolved = self._resolved_target(path)

        # Default-DENY: with no roots, nothing is writable.
        if not self.allowed_roots:
            raise WritePolicyError(
                f"refusing write — no allowed roots configured (default-deny): "
                f"{path!r}"
            )

        if not any(_is_within(resolved, root) for root in self.allowed_roots):
            raise WritePolicyError(
                f"refusing write outside allowed roots "
                f"{self.allowed_roots!r}: {path!r} -> {resolved!r}"
            )

        # Even inside a root, never write to .git/.ssh/secrets/keys.
        self._reject_sensitive(resolved, path)

        return resolved

    def is_allowed(self, path: str) -> bool:
        """Boolean convenience wrapper around :meth:`check` (never raises)."""
        try:
            self.check(path)
            return True
        except WritePolicyError:
            return False


def default_policy(
    workspace: str | None = None,
    worktree_root: str | None = None,
    extra_roots: list[str] | None = None,
    include_tmp: bool = True,
) -> WritePolicy:
    """Build the standard default-DENY policy used by write-capable modules.

    The allowlist is the project workspace, the worktree root, ``/tmp`` scratch
    (unless disabled), plus any caller-supplied ``extra_roots``.  Anything not
    under one of those is denied.
    """
    roots: list[str] = []
    if workspace:
        roots.append(workspace)
    if worktree_root:
        roots.append(worktree_root)
    if include_tmp:
        # Both the canonical tmp and its realpath (macOS /tmp -> /private/tmp)
        # are added; __post_init__ realpath-resolves + de-dupes them.
        roots.append("/tmp")
        tmpdir = os.environ.get("TMPDIR")
        if tmpdir:
            roots.append(tmpdir)
    if extra_roots:
        roots.extend(extra_roots)
    return WritePolicy(allowed_roots=roots)
