"""Structural test: the release job marks GitHub Releases as prerelease for
beta/alpha/rc tags.

Background (task CP.20): a release tag like ``v0.1.0-beta.1`` MUST publish a
GitHub Release with ``prerelease=true``.  Without it, pre-release versions
appear in the "latest release" slot and consumers pulling ``latest`` get an
unstable build.  This test parses ``build.yml`` and asserts the release-creation
step carries a prerelease flag whose expression evaluates truthy for beta,
alpha, and rc tags.  It FAILS if the flag is absent or if the expression would
not mark the three pre-release patterns.

The release step uses ``softprops/action-gh-release`` with::

    prerelease: ${{ contains(github.ref_name, '-') }}

which follows the SemVer convention that any version with a hyphen is a
pre-release (covers ``beta``, ``alpha``, ``rc``, ``dev``, etc.).
"""
from __future__ import annotations

import re

from tests.unit.test_release_pipeline_structure import BUILD_YML

# Tags that MUST be marked prerelease (task scope: beta/alpha/rc).
PRERELEASE_TAGS = (
    "v0.1.0-beta.1",
    "v0.1.0-alpha.5",
    "v0.1.0-rc.1",
)
# A final (non-prerelease) tag — included to keep the test honest: the logic
# must NOT mark stable releases as prerelease.
STABLE_TAG = "v1.0.0"


def _workflow_source() -> str:
    assert BUILD_YML.exists(), f"build.yml not found at {BUILD_YML}"
    return BUILD_YML.read_text()


def _extract_prerelease_expression(src: str) -> str | None:
    """Return the ``prerelease:`` expression from the release-creation step.

    Looks for a line beginning with ``prerelease:`` (after stripping YAML
    indentation) and returns the value verbatim.  Returns None when no such
    line exists.
    """
    m = re.search(r"^\s*prerelease:\s*(.+?)\s*$", src, re.MULTILINE)
    return m.group(1) if m else None


def _expression_marks_prerelease(expr: str, tag: str) -> bool:
    """Evaluate whether ``expr`` would mark ``tag`` as a prerelease.

    Supports the two sanctioned forms:

    1. ``${{ contains(github.ref_name, '-') }}`` — SemVer hyphen convention.
    2. ``${{ contains(github.ref_name, 'beta') || ... }}`` — explicit named
       pattern list (beta / alpha / rc).

    Any ``contains(github.ref_name, '<lit>')`` clause is evaluated as
    ``<lit> in tag``; clauses joined by ``||`` are OR-ed.  Returns False for
    any expression shape we do not recognise (fail-closed: the test then
    fails on the assertion, forcing a human to inspect).
    """
    body = expr.strip()
    body = body.replace("${{", "").replace("}}", "").strip()
    clauses = re.split(r"\s*\|\|\s*", body)
    matched = False
    for clause in clauses:
        cm = re.search(
            r"contains\s*\(\s*github\.ref_name\s*,\s*['\"]([^'\"]*)['\"]\s*\)",
            clause.strip(),
        )
        if cm and cm.group(1) in tag:
            matched = True
            break
    return matched


class TestPrereleaseFlag:
    """The release-creation step MUST flag pre-release tags as prerelease."""

    def test_release_step_exists(self) -> None:
        """A release-creation step (softprops/action-gh-release or gh release
        create) must be present in the release job.
        """
        src = _workflow_source()
        has_action = "softprops/action-gh-release" in src
        has_gh_create = re.search(r"gh\s+release\s+create", src) is not None
        assert has_action or has_gh_create, (
            "no GitHub-Release creation step found (expected "
            "softprops/action-gh-release or `gh release create`)"
        )

    def test_prerelease_field_present(self) -> None:
        """The release step must carry a ``prerelease:`` field — absent means
        GitHub defaults the release to non-prerelease, leaking beta tags into
        the ``latest`` slot.
        """
        expr = _extract_prerelease_expression(_workflow_source())
        assert expr is not None, (
            "release step has no `prerelease:` field — beta tags would "
            "publish as stable releases"
        )

    def test_prerelease_marks_beta_tags(self) -> None:
        """The prerelease expression MUST evaluate truthy for beta/alpha/rc
        tags.  Failures here mean either the flag was removed or the
        expression no longer recognises pre-release tag patterns.
        """
        expr = _extract_prerelease_expression(_workflow_source())
        assert expr is not None, "prerelease field missing (see test_prerelease_field_present)"
        for tag in PRERELEASE_TAGS:
            assert _expression_marks_prerelease(expr, tag), (
                f"prerelease expression `{expr}` would NOT mark `{tag}` as "
                f"prerelease — beta/alpha/rc tags require prerelease=true"
            )

    def test_prerelease_does_not_mark_stable(self) -> None:
        """The prerelease expression must NOT mark a stable tag (v1.0.0) as a
        prerelease — that would hide the stable release from the ``latest``
        slot.
        """
        expr = _extract_prerelease_expression(_workflow_source())
        assert expr is not None
        assert not _expression_marks_prerelease(expr, STABLE_TAG), (
            f"prerelease expression `{expr}` would incorrectly mark "
            f"`{STABLE_TAG}` as prerelease"
        )

    def test_release_job_gated_on_tag(self) -> None:
        """The release job must only run on tag pushes (``if: startsWith(
        github.ref, 'refs/tags/v')``) — otherwise branch pushes would trigger
        spurious releases.
        """
        src = _workflow_source()
        idx = src.find("  release:")
        assert idx >= 0, "release job not found"
        section = src[idx : idx + 800]
        assert "startsWith(github.ref, 'refs/tags/v')" in section or (
            "startsWith(github.ref, \"refs/tags/v\")" in section
        ), "release job must be gated on refs/tags/v*"
