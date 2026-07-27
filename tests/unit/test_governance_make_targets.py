"""Structural tests for governance Makefile targets."""

from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"


def _recipe(target: str) -> str:
    content = MAKEFILE.read_text()
    marker = f"\n{target}:"
    assert marker in content, f"Makefile target '{target}' not found"
    start = content.index(marker) + len(marker)
    next_blank = content.find("\n\n", start)
    if next_blank == -1:
        return content[start:]
    return content[start:next_blank]


class TestGovernanceMakeTargets:
    def test_test_governance_target_exists(self):
        recipe = _recipe("test-governance")
        assert recipe, "test-governance target must have a recipe"
        assert "governance/tests" in recipe

    def test_governance_syntax_target_exists(self):
        recipe = _recipe("governance-syntax")
        assert recipe, "governance-syntax target must have a recipe"
        assert "governance/roles" in recipe

    def test_governance_health_target_exists(self):
        recipe = _recipe("governance-health")
        assert recipe, "governance-health target must have a recipe"
        assert "module_utils" in recipe

    def test_governance_in_test_collections(self):
        recipe = _recipe("test-collections")
        assert "test-governance" in recipe, "test-collections must include test-governance"
