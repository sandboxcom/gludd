"""Behavioral contract for release-cut's asynchronous GitHub release polling."""

from pathlib import Path

from scripts.makefile_layout import compose_makefile


def _recipe(target: str) -> str:
    content = compose_makefile(Path(__file__).resolve().parents[2] / "Makefile")
    marker = f"\n{target}:"
    start = content.index(marker)
    end = content.find("\n\n", start)
    return content[start:end]


def test_release_cut_does_not_abort_before_artifact_polling() -> None:
    """A just-pushed tag can legitimately have no GitHub Release yet."""
    recipe = _recipe("release-cut")

    release_view = next(line for line in recipe.splitlines() if "release-view" in line)
    assert "||" in release_view
    assert "ci-await" in recipe
    assert "verify-release-artifact" in recipe


def test_release_cut_uses_bounded_exact_identity_wait_not_fixed_artifact_retries() -> None:
    recipe = _recipe("release-cut")

    assert 'BRANCH="$(TAG)"' in recipe
    assert "CI_AWAIT_WORKFLOW=\"Build and Release\"" in recipe
    assert "CI_AWAIT_EVENT=push" in recipe
    assert "RELEASE_AWAIT_TIMEOUT" in recipe
    assert "RELEASE_AWAIT_INTERVAL" in recipe
    assert "for i in 1 2 3 4 5 6 7 8 9 10" not in recipe
    assert "sleep 60" not in recipe
