"""Tests for prerelease validation before a release exists."""

from scripts.check_prerelease_flag import candidate_workflow_matches

WORKFLOW = """
      - uses: softprops/action-gh-release@pinned
        with:
          prerelease: ${{ contains(github.ref_name, '-') }}
"""


def test_candidate_workflow_matches_prerelease_tag_shape() -> None:
    assert candidate_workflow_matches("v0.1.0-beta.4", WORKFLOW)
    assert candidate_workflow_matches("v0.1.0", WORKFLOW)


def test_candidate_workflow_rejects_invalid_tag_or_missing_expression() -> None:
    assert not candidate_workflow_matches("beta4", WORKFLOW)
    assert not candidate_workflow_matches("v0.1.0-beta.4", "prerelease: false")
