"""Canonical path deny-list shared by security/capability_lattice.py and self_update/.

This module is the SINGLE SOURCE OF TRUTH for the deny-list markers used by
both the daemon-side capability lattice (:mod:`security.capability_lattice`)
and the self-update pipeline (:mod:`self_update.applier`, :mod:`self_update.apply`).

C9 (AGENTIC_IMPLEMENTATION_SPEC.md) requires that both modules normalise
through one path-canonicalizer so a deny-list drift between them is
structurally impossible.  This module provides:

* :data:`CANONICAL_DENY_MARKERS` — the immutable set of marker strings every
  deny-list check MUST derive from.
* :func:`canonicalize_path` — normalise a path for case-insensitive matching
  (lowercase + backslash-to-slash).
* :func:`is_denied_path` — the canonical deny-list check used by all three
  enforcement sites.

Matching is two-tier (same design as :mod:`self_update.applier`):

* **Substring markers**: matched as a substring anywhere in the canonicalised
  path.  Safe because these markers are either dot-prefixed (``.opencode``,
  ``.github``), slash-anchored (``/workflows/``, ``/migrations/``), or
  inherently unambiguous (``guardrails``, ``secrets``).
* **Segment-exact markers**: bare words like ``alembic``, ``makefile``,
  ``dockerfile`` that would falsely match ``alembic_runner.py`` or
  ``makefile_parser.py`` if treated as substrings.  These are matched only
  when the marker equals an individual path segment or the exact basename
  stem (case-insensitive).

Both absolute (``/repo/.claude/hooks/x.py``) and relative
(``.claude/hooks/x.py``) paths are caught — the leading-slash drift between
``apply.py`` and ``capability_lattice.py`` is closed by matching on the
canonicalised form regardless of leading slash.
"""

from __future__ import annotations

from pathlib import Path

#: Canonical deny-list markers — every enforcement site derives its deny-list
#: from this single set.  When a new protected surface is added, add its marker
#: HERE and then re-verify that all three modules (capability_lattice.py,
#: applier.py, apply.py) consume it through :func:`is_denied_path`.
#:
#: Subset classification (used by :func:`is_denied_path`):
#:
#: * **Substring-matched** — all markers NOT in :data:`_SEGMENT_EXACT_MARKERS`.
#:   A lowered path containing the marker ANYWHERE is denied.
#: * **Segment-matched** — markers IN :data:`_SEGMENT_EXACT_MARKERS`.  These
#:   are matched only as a whole path segment, exact basename, or basename
#:   stem (extension dropped), so ``alembic`` blocks ``alembic.ini`` but NOT
#:   ``alembic_runner.py``.
CANONICAL_DENY_MARKERS: frozenset[str] = frozenset(
    {
        # Guardrail / policy / permission control surface
        "guardrails",
        "capability_policy",
        "capability_lattice",       # from capability_lattice.PROTECTED_FILE_STEMS
        "action_policy",
        "fs_write_policy",
        "enforce-",                  # prefix: blocks enforce-make.ts, enforce-stop.ts, etc.
        "permissions",
        "permission",                # from capability_lattice.PROTECTED_FILE_STEMS (singular)
        "policy",                    # from capability_lattice.PROTECTED_FILE_STEMS
        "enforce_make",              # from capability_lattice.PROTECTED_FILE_STEMS
        "safe_redirector",            # per capability_lattice precedent
        # Secrets surface
        "secrets",
        # Harness control surface
        ".opencode",
        ".claude",
        # CI / build surface
        ".github",
        "/workflows/",
        "pyproject.toml",
        "makefile",                  # segment-exact
        "alembic",                   # segment-exact
        "/migrations/",
        "setup.cfg",
        "tox.ini",
        ".pre-commit",
        "dockerfile",                # segment-exact
        # Settings files (from apply.py _HARD_DENY_SUBSTRINGS)
        "settings.json",
        "settings.local.json",
        # Module-utils guard surface (from capability_lattice.PROTECTED_PATH_SUBSTRINGS)
        "/module_utils/capability_policy",
        "/module_utils/fs_write_policy",
        "/security/capability_lattice",
        # Harness meta-files (from capability_lattice.PROTECTED_PATH_SUBSTRINGS,
        # applier.PROTECTED_PATH_MARKERS, apply._HARD_DENY_SUBSTRINGS)
        "agents.md",
        "claude.md",
        "tasks.md",
        "bugs.md",
        "session.md",
    }
)

#: Markers that must match a whole PATH SEGMENT (or exact basename / basename
#: stem), not an arbitrary substring.  See :func:`is_denied_path` for the
#: matching semantics.
_SEGMENT_EXACT_MARKERS: frozenset[str] = frozenset(
    {"alembic", "makefile", "dockerfile"}
)


def canonicalize_path(path: str | None) -> str:
    """Normalise ``path`` to a canonical form for deny-list matching.

    - ``None`` / empty → ``""``
    - Backslashes → forward slashes
    - Lowercased
    """
    if not path:
        return ""
    return path.replace("\\", "/").lower()


def is_denied_path(path: str | None, *, workspace_root: Path | str | None = None) -> bool:
    """Return True if ``path`` matches the canonical deny-list.

    The deny-list check operates on the LEXICAL form of ``path`` (after
    canonicalisation), so a ``.claude`` / ``.opencode`` path is caught
    regardless of whether it starts with a leading slash.  This is the
    anti-drift guarantee: absolute ``/repo/.claude/hooks/x.py`` and
    relative ``.claude/hooks/x.py`` both match.

    Optionally resolves against ``workspace_root`` for caller convenience;
    the lexical check is always performed.

    Parameters
    ----------
    path:
        The path to check.  May be relative or absolute.
    workspace_root:
        Optional workspace root for resolution.  If provided, the path is
        resolved against it BEFORE the lexical check, so a symlink that
        points to a protected location is caught even when the raw path
        string does not contain a deny-list marker.
    """
    norm = canonicalize_path(path)
    if not norm:
        return False

    segments = norm.split("/")
    basename = Path(norm).name
    basename_stem = Path(basename).stem

    for marker in CANONICAL_DENY_MARKERS:
        if marker in _SEGMENT_EXACT_MARKERS:
            if (
                marker in segments
                or marker in (basename, basename_stem)
            ):
                return True
        else:
            if marker in norm:
                return True

    if workspace_root is not None and path:
        root = Path(workspace_root)
        try:
            resolved = (root / path).resolve()
            resolved_norm = canonicalize_path(str(resolved))
            for marker in CANONICAL_DENY_MARKERS:
                if marker in _SEGMENT_EXACT_MARKERS:
                    resolved_segments = resolved_norm.split("/")
                    if marker in resolved_segments:
                        return True
                else:
                    if marker in resolved_norm:
                        return True
        except Exception:
            return True

    return False
