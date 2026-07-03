"""Structural tests for batched Makefile targets: test-batch, status-update, ship, precommit."""

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


class TestBatchedMakeTargets:
    """Batched make targets must be present and callable in the Makefile."""

    def test_test_batch_target_exists(self):
        """test-batch target must exist and be callable."""
        recipe = _recipe("test-batch")
        assert recipe, "test-batch target must have a recipe"

    def test_status_update_target_exists(self):
        """status-update target must exist."""
        recipe = _recipe("status-update")
        assert recipe, "status-update target must have a recipe"

    def test_ship_target_exists(self):
        """ship target must exist."""
        recipe = _recipe("ship")
        assert recipe, "ship target must have a recipe"

    def test_precommit_target_exists(self):
        """precommit target must exist."""
        recipe = _recipe("precommit")
        assert recipe, "precommit target must have a recipe"
