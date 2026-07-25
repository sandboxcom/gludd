"""TDD structural tests for the release-tag-push Makefile target (CP.7).

Closes the gap where push + tag were two separate steps that could race with
master CI runs. The target must atomically:
  1. Cancel any in_progress CI run on master (avoid concurrency cancellation)
  2. Push master to sandboxcom
  3. Create + push the annotated tag

References: CP.7, EX.1, CID.8, RC2.4.
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


class TestReleaseTagPushTarget:
    """release-tag-push atomically cancels master CI + pushes master + pushes tag."""

    def test_target_exists(self):
        assert _recipe("release-tag-push"), (
            "release-tag-push target must exist (CP.7)"
        )

    def test_requires_TAG_argument(self):
        recipe = _recipe("release-tag-push")
        assert "$(TAG)" in recipe, (
            "release-tag-push must require a TAG argument"
        )

    def test_calls_git_push_sandboxcom(self):
        recipe = _recipe("release-tag-push")
        assert "git-push-sandboxcom" in recipe, (
            "release-tag-push must invoke git-push-sandboxcom to push master"
        )

    def test_calls_git_tag_push(self):
        recipe = _recipe("release-tag-push")
        assert "git-tag-push" in recipe, (
            "release-tag-push must invoke git-tag-push to push the tag"
        )

    def test_cancels_master_ci_run(self):
        recipe = _recipe("release-tag-push")
        assert "ci-cancel" in recipe or "ci-active" in recipe, (
            "release-tag-push must cancel (or check) in_progress master CI runs "
            "before pushing to avoid concurrency conflict (CID.8)"
        )

    def test_documents_usage(self):
        recipe = _recipe("release-tag-push")
        assert "TAG=" in recipe, (
            "release-tag-push usage message must mention TAG=<tag>"
        )


class TestReleaseTagPushPhony:
    """release-tag-push must be discoverable in the Makefile."""

    def test_target_in_makefile(self):
        content = MAKEFILE.read_text()
        assert "release-tag-push" in content, (
            "release-tag-push must be present in the Makefile"
        )
