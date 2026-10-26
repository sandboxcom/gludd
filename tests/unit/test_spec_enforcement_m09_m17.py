"""M09/M10/M14/M17: Advanced merge and ship-async target enforcement.

Verifies gated-merge enforces preconditions, development-merge-to-master
requires CI green, no-concurrent-merge guard, and ship-async gates on
green before ff-merge.
"""

from pathlib import Path

from scripts.makefile_layout import compose_makefile

MAKEFILE = Path(__file__).parent.parent.parent / "Makefile"
SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"


def _find_recipe(content: str, target: str) -> str:
    idx = content.find(f"\n{target}:")
    if idx == -1:
        return ""
    end = content.find("\n\n", idx)
    if end == -1:
        end = len(content)
    return content[idx:end]


class TestM09M10M14M17MergeAndShip:
    """M09/M10/M14/M17 — gated-merge, dev-merge, ship-async enforcement."""

    def test_gated_merge_target_has_preconditions(self) -> None:
        content = compose_makefile(MAKEFILE)
        recipe = _find_recipe(content, "gated-merge")
        assert recipe, "M09: gated-merge target must exist"
        assert "gated_merge.sh" in recipe, "M09: gated-merge must invoke gated_merge.sh script"

    def test_gated_merge_script_exists(self) -> None:
        script_path = SCRIPTS_DIR / "gated_merge.sh"
        assert script_path.exists(), "M09: scripts/gated_merge.sh must exist for multi-condition merge"

    def test_gated_merge_accepts_manifest(self) -> None:
        content = compose_makefile(MAKEFILE)
        recipe = _find_recipe(content, "gated-merge")
        if not recipe:
            return
        assert "MANIFEST" in recipe, "M09: gated-merge must accept a MANIFEST variable"

    def test_development_merge_checks_ci_green(self) -> None:
        content = compose_makefile(MAKEFILE)
        recipe = _find_recipe(content, "development-merge-to-master")
        assert recipe, "M10: development-merge-to-master must exist"
        assert "outcome=retired" in recipe and "exit 2" in recipe
        assert "release-promote" in recipe
        assert "git merge" not in recipe
        canonical = _find_recipe(content, "release-promote")
        assert "require-dual-track-green" in canonical
        assert "DUAL_TRACK_CI_VALIDATE_ONLY=0" in canonical
        assert "RELEASE_READINESS_VALIDATE_ONLY=0" in canonical

    def test_retired_alias_has_no_prerequisite_side_effects(self) -> None:
        content = compose_makefile(MAKEFILE)
        recipe = _find_recipe(content, "development-merge-to-master")
        assert recipe.splitlines()[1] == "development-merge-to-master:"
        assert "git checkout" not in recipe
        canonical = _find_recipe(content, "release-promote")
        assert "worktree-guard" in canonical
        assert "main-worktree-guard" in canonical
        assert "REVIEWED_HEAD_INTEGRATION_RECEIPT" in canonical

    def test_ship_async_script_exists(self) -> None:
        script_path = SCRIPTS_DIR / "ship_async.sh"
        assert script_path.exists(), "M17: scripts/ship_async.sh must exist for ship-async"

    def test_ship_async_target_accepts_ref(self) -> None:
        content = compose_makefile(MAKEFILE)
        recipe = _find_recipe(content, "ship-async")
        assert recipe, "M17: ship-async target must exist"
        # Must accept REF (reference hash) and optionally TARGET
        assert "REF" in recipe or "$(REF)" in content.split("\nship-async:", 1)[1].split("\n\n", 1)[0], (
            "M17: ship-async must accept REF=<hash> parameter"
        )

    def test_merge_ready_script_exists(self) -> None:
        script_path = SCRIPTS_DIR / "workflow_state_guard.py"
        assert script_path.exists(), "M14: workflow_state_guard.py must exist for merge-ready precondition"
