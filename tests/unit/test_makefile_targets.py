"""Structural tests for the presence of key release Makefile targets.

Verifies that `release-create`, `release-cut`, and `release-recut` targets
exist in the Makefile. Uses the same Makefile-scanning style as
test_commit_gate_freshness.py and test_release_recut_target.py.
"""

from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"


def _recipe(target: str) -> str:
    """Extract the full recipe body for a make target. Assert target exists."""
    content = MAKEFILE.read_text()
    marker = f"\n{target}:"
    assert marker in content, f"Makefile target '{target}' not found"
    start = content.index(marker) + len(marker)
    next_target = content.find("\n\n", start)
    if next_target == -1:
        return content[start:]
    return content[start:next_target]


class TestReleaseTargetsExist:
    """Required release-shaped targets must be present in the Makefile."""

    def test_release_cut_target_exists(self):
        assert _recipe("release-cut"), "release-cut target must exist"

    def test_release_recut_target_exists(self):
        assert _recipe("release-recut"), "release-recut target must exist"

    def test_release_create_target_exists(self):
        """release-create is the manual fallback for publishing a GitHub Release."""
        assert _recipe("release-create"), "release-create target must exist"

    def test_release_create_invokes_build_and_upload(self):
        """release-create must build artifacts locally and upload via gh."""
        recipe = _recipe("release-create")
        assert "build-executable" in recipe, (
            "release-create must build artifacts via build-executable"
        )
        assert "gh release" in recipe, (
            "release-create must create/upload the release via gh"
        )

    def test_release_create_verifies_artifact(self):
        """release-create must confirm the artifact via verify-release-artifact."""
        recipe = _recipe("release-create")
        assert "verify-release-artifact" in recipe, (
            "release-create must verify the published artifact"
        )
