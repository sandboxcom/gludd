"""S.9 — self_update/applier.py substring-only protected-path bypass (D5/CA-E5).

Substring matching + os.path.normpath BEFORE the check allows bypass:
``guardrails/../../etc`` becomes ``../../etc`` after normpath, losing the
``guardrails`` marker entirely.  The fix closes this by (a) checking the raw
(pre-normpath) path and (b) using path-segment matching instead of arbitrary
substring matching for bare-word markers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from general_ludd.security.path_canonicalizer import is_denied_path
from general_ludd.self_update.applier import _first_protected

# ── Core bypass: normpath strips the marker before the check ────────────────


@pytest.mark.parametrize(
    "bypass_path,marker_that_should_block",
    [
        ("guardrails/../../etc/passwd", "guardrails"),
        ("secrets/../../../etc/shadow", "secrets"),
        ("guardrails/../src/arbitrary.yml", "guardrails"),
        ("secrets/../.opencode/plugin/x.ts", "secrets"),
        ("guardrails/../.github/workflows/x.yml", "guardrails"),
        ("permissions/../../../.opencode/plugin/x.ts", "permissions"),
        ("guardrails/../../.claude/settings.json", "guardrails"),
        (".opencode/../../../etc/passwd", ".opencode"),
        (".claude/../../../etc/shadow", ".claude"),
        (".github/../../../etc/passwd", ".github"),
        # Double-hop: walk through TWO protected dirs
        (
            "guardrails/../secrets/../../../etc/passwd",
            "guardrails",  # both markers stripped by normpath
        ),
        # enforce- prefix marker: enforce-/../ escapes the enforce tree
        (
            "enforce-/../.claude/settings.json",
            "enforce-",
        ),
    ],
)
def test_raw_path_bypass_normpath_strips_marker(
    bypass_path: str, marker_that_should_block: str
) -> None:
    """A path that WALKS THROUGH a protected directory with ``..`` must still
    be caught — even though ``os.path.normpath`` collapses the protected
    segment away before the substring check runs.

    Before the fix, ``_first_protected`` called ``os.path.normpath`` on line
    168 of applier.py BEFORE ``is_denied_path``, so a raw path like
    ``guardrails/../../etc`` became ``../../etc`` — losing the ``guardrails``
    marker and bypassing the deny-list.
    """
    assert is_denied_path(bypass_path), (
        f"Path {bypass_path!r} must be denied — it walks through "
        f"a protected directory ({marker_that_should_block!r}). "
        f"normpath() must NOT strip the marker before the check."
    )


# ── _first_protected integration: path walking through protected dir ────────


@pytest.mark.parametrize(
    "bypass_path",
    [
        "guardrails/../../etc/passwd",
        "secrets/../../../etc/shadow",
        "guardrails/../some/safe/config.yml",
        ".opencode/../../../etc/passwd",
        ".claude/../../.github/workflows/x.yml",
        ".github/../../../etc/passwd",
        "enforce-/../../etc/passwd",
    ],
)
def test_first_protected_catches_normpath_bypass(bypass_path: str, tmp_path: Path) -> None:
    """Regression: ``_first_protected`` must catch paths that walk THROUGH a
    protected directory even when the final resolved location is outside the
    workspace (caught by confinement) or within the workspace (matching no
    marker after normpath)."""
    protected = _first_protected([bypass_path], workspace_root=tmp_path)
    assert protected is not None, (
        f"Path {bypass_path!r} must be caught by _first_protected. "
        f"It walks through a protected directory."
    )


# ── Segment-based matching: no false positives on innocent substrings ───────


@pytest.mark.parametrize(
    "innocent_path",
    [
        "config/safeguard_rules.yml",  # "guard" not "guardrails"
        "src/my_secrets_parser.py",  # "secrets" embedded in a word
        "docs/permissions_guide.md",  # "permissions" is part of a filename
        "lib/enforcement.py",  # "enforce" without the trailing dash
        "tools/open_code_generator.py",  # "opencode" without the dot
        "research/claude_paper.md",  # "claude" without the dot or path
        "deploy/github_actions.md",  # "github" without the dot
    ],
)
def test_segment_matching_no_false_positive(innocent_path: str) -> None:
    """Bare-word markers like ``guardrails``, ``secrets``, ``permissions``
    must match as WHOLE path segments — not as arbitrary substrings inside
    longer words.  ``safeguard_rules.yml`` must NOT be denied just because
    ``guard`` is a substring of ``safeguard``."""
    assert not is_denied_path(innocent_path), (
        f"Path {innocent_path!r} must NOT be denied — "
        f"it does not contain a protected marker as a whole path segment."
    )


# ── True positives still caught with segment matching ───────────────────────


@pytest.mark.parametrize(
    "should_deny",
    [
        "guardrails/config.yml",
        "config/guardrails.yml",
        "secrets/openbao.yml",
        ".opencode/plugin/enforce-make.ts",
        ".claude/settings.json",
        "permissions/admin.yml",
        "config/permissions/local.yml",
        ".github/workflows/release.yml",
        "enforce-make.ts",  # enforce- prefix in filename
        ".pre-commit-config.yaml",
        "pyproject.toml",
        "agents.md",
        "tasks.md",
        "bugs.md",
        "session.md",
    ],
)
def test_segment_matching_still_denies_true_positives(should_deny: str) -> None:
    """Paths that genuinely contain a protected marker as a path segment must
    still be denied after the fix."""
    assert is_denied_path(should_deny), (
        f"Path {should_deny!r} must STILL be denied — "
        f"it contains a protected marker as a path segment."
    )


# ── Path-anchored markers (slashed) still work ──────────────────────────────


@pytest.mark.parametrize(
    "denied_path",
    [
        "config/../workflows/release.yml",  # workflows segment + ..
        "collections/module_utils/capability_policy.py",
        "collections/module_utils/fs_write_policy.py",
        "security/capability_lattice.py",
        "src/general_ludd/security/capability_lattice.py",
    ],
)
def test_slash_anchored_markers_still_caught(denied_path: str) -> None:
    """Path-anchored markers (``/workflows/``, ``/module_utils/...``,
    ``/security/...``) must still be denied after switching to segment-based
    matching."""
    assert is_denied_path(denied_path), (
        f"Path {denied_path!r} must be denied — it contains a "
        f"slash-anchored protected marker."
    )


# ── No regression: existing protected-path tests still pass ─────────────────


@pytest.mark.parametrize(
    "protected",
    [
        "config/guardrails.yml",
        "secrets/openbao.yml",
        ".opencode/plugin/enforce-make.ts",
        ".claude/settings.json",
        ".opencode/plugin/enforce-anything.ts",
        "config/permissions.yml",
        ".github/workflows/release.yml",
        "pyproject.toml",
        "Makefile",
        "alembic.ini",
        "db/migrations/001_init.sql",
        "Dockerfile",
        "setup.cfg",
        "tox.ini",
        ".pre-commit-config.yaml",
    ],
)
def test_existing_protected_paths_still_denied(protected: str) -> None:
    """No regression: every path denied by the existing test suite BEFORE the
    S.9 fix must still be denied AFTER the fix."""
    assert is_denied_path(protected), (
        f"REGRESSION: {protected!r} was denied before the S.9 fix, "
        f"but is now allowed."
    )
