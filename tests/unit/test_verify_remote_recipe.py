"""TDD tests for the verify-remote Makefile target recipe.

The bug: `make verify-remote` called `git ls-remote sandboxcom $$BR`. When a
branch and tag share a name (e.g. both `master`), `git ls-remote` returns two
lines for the same ref name — the resulting `awk '{print $1}'` pipeline
produces a two-line SHA, and the equality check silently fails with a spurious
`REMOTE MISMATCH`.

The fix: pin the ref to heads only via `refs/heads/$$BR`. This structurally
disambiguates the branch ref from any same-named tag, so `ls-remote` returns
exactly one line.

This test file pins the contract so the regression cannot be reintroduced
silently.
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


class TestVerifyRemoteRecipe:
    """The verify-remote target must be hardened against same-named branch+tag."""

    def test_verify_remote_target_exists(self):
        assert _recipe("verify-remote"), "verify-remote target must exist"

    def test_recipe_pins_to_heads_ref(self):
        """verify-remote must look up refs/heads/$$BR — not bare $$BR.

        Before: `git ls-remote sandboxcom $$BR` returned multiple lines when
        a branch and tag shared a name, breaking the SHA comparison with a
        spurious REMOTE MISMATCH.
        After: `git ls-remote sandboxcom refs/heads/$$BR` pins to the branch
        ref namespace, guaranteeing exactly one line of output.
        """
        recipe = _recipe("verify-remote")
        assert "refs/heads/" in recipe, (
            "verify-remote must pin to refs/heads/$$BR to disambiguate "
            "same-named branch+tag — bare $$BR breaks the comparison"
        )
        assert "refs/heads/$$BR" in recipe, (
            "verify-remote must use refs/heads/$$BR verbatim"
        )

    def test_recipe_supports_sha_parameter(self):
        """verify-remote must honor an explicit $$SHA override.

        Default: compare against `git rev-parse HEAD`. Override: caller passes
        SHA=<sha> to verify a specific commit landed on the remote.
        """
        recipe = _recipe("verify-remote")
        assert "$(or $(SHA)" in recipe, (
            "verify-remote must support SHA= override via $(or $(SHA),...)"
        )

    def test_recipe_supports_branch_parameter(self):
        """verify-remote must honor a $$BRANCH override, defaulting to master."""
        recipe = _recipe("verify-remote")
        assert "$(or $(BRANCH),master)" in recipe, (
            "verify-remote must support BRANCH= override defaulting to master"
        )

    def test_recipe_emits_verified_on_success(self):
        """On match, verify-remote prints VERIFIED <branch>@<sha>."""
        recipe = _recipe("verify-remote")
        assert "VERIFIED" in recipe, (
            "verify-remote must emit a VERIFIED success marker"
        )

    def test_recipe_emits_remote_mismatch_on_failure(self):
        """On mismatch, verify-remote prints REMOTE MISMATCH and exits 1.

        The mismatch message is the operator-facing failure signal — it must
        include both the observed remote SHA and the expected SHA so the
        discrepancy is diagnosable without a separate git invocation.
        """
        recipe = _recipe("verify-remote")
        assert "REMOTE MISMATCH" in recipe, (
            "verify-remote must emit a REMOTE MISMATCH failure marker"
        )
        assert "exit 1" in recipe, (
            "verify-remote must exit non-zero on mismatch so CI/operators fail"
        )

    def test_recipe_shortens_remote_to_sha_length(self):
        """verify-remote compares remote SHA truncated to expected SHA length.

        ls-remote returns a full 40-char SHA, but the expected SHA may be a
        short form. The comparison must truncate remote to expected length
        via `cut -c1-` so short-SHA comparisons succeed.
        """
        recipe = _recipe("verify-remote")
        assert "cut -c1-" in recipe, (
            "verify-remote must shorten remote SHA to expected SHA length "
            "before comparison"
        )

    def test_no_bare_branch_lookup_remains(self):
        """Regression guard: no `ls-remote sandboxcom $$BR` without refs/heads/.

        Catches a future edit that re-introduces the bare-ref lookup that
        collides with same-named tags.
        """
        recipe = _recipe("verify-remote")
        assert "ls-remote sandboxcom $$BR" not in recipe, (
            "verify-remote must NOT contain a bare `ls-remote sandboxcom $$BR` "
            "— this is the bug that re-introduces the branch/tag collision"
        )
