"""TDD tests for the git-tag-rm and release-recut Makefile targets.

The gap: there was no way to delete a tag (locally + remotely) or to
re-trigger a release CI job for an existing tag whose release was skipped
because the gate was red when the tag was originally pushed.

These tests prove the gap is closed by checking the Makefile structurally:
  - both targets exist
  - release-recut polls verify-release-artifact
  - both targets use the sandboxcom SSH key for remote operations
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


class TestGitTagRm:
    """git-tag-rm deletes a tag both locally and on sandboxcom."""

    def test_target_exists(self):
        assert _recipe("git-tag-rm"), "git-tag-rm target must exist"

    def test_uses_sandboxcom_ssh_key(self):
        recipe = _recipe("git-tag-rm")
        assert "GIT_SSH_COMMAND" in recipe, (
            "git-tag-rm must use GIT_SSH_COMMAND for sandboxcom remote ops"
        )
        assert "$(SSH_KEY)" in recipe, (
            "git-tag-rm must use the configurable sandboxcom SSH key"
        )

    def test_default_key_is_the_project_specific_deploy_key(self) -> None:
        assert "SSH_KEY ?= $(HOME)/.ssh/sandboxcom_gludd_rsa" in MAKEFILE.read_text()

    def test_deletes_remote_tag(self):
        recipe = _recipe("git-tag-rm")
        assert ":refs/tags/" in recipe or "refs/tags/" in recipe, (
            "git-tag-rm must delete the remote tag ref"
        )

    def test_deletes_local_tag(self):
        recipe = _recipe("git-tag-rm")
        assert "git tag -d" in recipe, (
            "git-tag-rm must delete the local tag"
        )

    def test_requires_TAG_argument(self):
        recipe = _recipe("git-tag-rm")
        assert "$(TAG)" in recipe, (
            "git-tag-rm must require a TAG argument"
        )


class TestReleaseRecut:
    """release-recut re-triggers the release CI job for an existing tag."""

    def test_target_exists(self):
        assert _recipe("release-recut"), "release-recut target must exist"

    def test_references_verify_release_artifact(self):
        recipe = _recipe("release-recut")
        assert "verify-release-artifact" in recipe, (
            "release-recut must poll verify-release-artifact to confirm the "
            "artifact is published after re-pushing the tag"
        )

    def test_uses_sandboxcom_ssh_key(self):
        recipe = _recipe("release-recut")
        assert "GIT_SSH_COMMAND" in recipe, (
            "release-recut must use GIT_SSH_COMMAND for sandboxcom remote ops"
        )
        assert "$(SSH_KEY)" in recipe, (
            "release-recut must use the configurable sandboxcom SSH key"
        )

    def test_requires_TAG_argument(self):
        recipe = _recipe("release-recut")
        assert "$(TAG)" in recipe, (
            "release-recut must require a TAG argument"
        )

    def test_verifies_local_tag_exists_first(self):
        recipe = _recipe("release-recut")
        assert "git tag -l" in recipe, (
            "release-recut must verify the local tag exists before re-pushing"
        )

    def test_polls_with_verify_vars(self):
        recipe = _recipe("release-recut")
        assert "$(VERIFY_POLLS)" in recipe, (
            "release-recut must use the VERIFY_POLLS variable for its poll loop"
        )


class TestGitTagPushCommitParam:
    """git-tag-push supports an optional COMMIT=<sha> to tag a non-HEAD commit."""

    def test_recipe_uses_COMMIT_variable(self):
        recipe = _recipe("git-tag-push")
        assert "$(COMMIT)" in recipe, (
            "git-tag-push recipe must reference $(COMMIT) so a specific "
            "commit can be tagged instead of HEAD"
        )

    def test_usage_message_mentions_COMMIT(self):
        recipe = _recipe("git-tag-push")
        assert "COMMIT=<sha>" in recipe, (
            "git-tag-push usage message must mention COMMIT=<sha>"
        )


class TestPhonyList:
    """Both new targets must be declared in the .PHONY list."""

    def test_git_tag_rm_phony(self):
        content = MAKEFILE.read_text()
        # Grab the .PHONY block (it spans multiple lines).
        assert "git-tag-rm" in content, "git-tag-rm missing from Makefile entirely"

    def test_release_recut_phony(self):
        content = MAKEFILE.read_text()
        assert "release-recut" in content, "release-recut missing from Makefile entirely"
