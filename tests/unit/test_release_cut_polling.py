"""Behavioral contract for release-cut's asynchronous GitHub release polling."""

from pathlib import Path


def _recipe(target: str) -> str:
    content = (Path(__file__).resolve().parents[2] / "Makefile").read_text()
    marker = f"\n{target}:"
    start = content.index(marker)
    end = content.find("\n\n", start)
    return content[start:end]


def test_release_cut_does_not_abort_before_artifact_polling() -> None:
    """A just-pushed tag can legitimately have no GitHub Release yet."""
    recipe = _recipe("release-cut")

    release_view = next(line for line in recipe.splitlines() if "release-view" in line)
    assert "||" in release_view
    assert "verify-release-artifact" in recipe
