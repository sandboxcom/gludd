"""M19: Release-promote is ff-only merge into master.

`make release-promote TAG=<tag>` MUST bind exact-SHA dual-track evidence
and readiness before an ff-only merge advances canonical master. Publication
then delegates to the single `release-cut` owner so tagging, pushing, and
artifact verification cannot drift across promotion paths.
"""

import re
from pathlib import Path

MAKEFILE = Path(__file__).parent.parent.parent / "Makefile"
SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"


def _find_target_recipe(content: str, target: str) -> str:
    idx = content.find(f"\n{target}:")
    if idx == -1:
        return ""
    end = content.find("\n\n", idx)
    if end == -1:
        end = len(content)
    return content[idx:end]


class TestM19ReleasePromoteFfOnly:
    """M19 — release-promote is ff-only merge into master."""

    def test_release_promote_target_exists_or_is_documented(self) -> None:
        content = MAKEFILE.read_text()
        target_names = set()
        for line in content.split("\n"):
            m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_./-]*)\s*:(?!=)", line.strip())
            if m:
                target_names.add(m.group(1))

        assert "release-promote" in target_names, "M19: release-promote target missing from Makefile"
        recipe = _find_target_recipe(content, "release-promote")
        assert "require-dual-track-green" in recipe, "M19: promotion must bind exact dual-track evidence"
        assert "release-readiness" in recipe, "M19: promotion must re-run release readiness"
        assert "merge --ff-only" in recipe, "M19: promotion must refuse divergent master history"
        assert "RELEASE_PROMOTE_VALIDATE_ONLY" in recipe, "M19: promotion needs a no-side-effect contract mode"

    def test_check_green_branch_guard_documents_promote(self) -> None:
        guard_path = SCRIPTS_DIR / "check_green_branch_guard.py"
        if not guard_path.exists():
            return
        content = guard_path.read_text()
        assert "release-promote" in content, "M19: check_green_branch_guard.py must document release-promote"
        assert "ff-only" in content.lower() or "ff-only" in content, (
            "M19: check_green_branch_guard.py must document ff-only merge"
        )

    def test_ship_async_uses_ff_only(self) -> None:
        content = MAKEFILE.read_text()
        recipe = _find_target_recipe(content, "ship-async")
        if not recipe:
            return
        script_path = SCRIPTS_DIR / "ship_async.sh"
        if script_path.exists():
            script_content = script_path.read_text()
            assert "ff-only" in script_content.lower() or "fast-forward" in script_content.lower(), (
                "M19: ship_async.sh must enforce ff-only or fast-forward merge"
            )

    def test_release_branch_new_checks_ci_green(self) -> None:
        content = MAKEFILE.read_text()
        target_names = set()
        for line in content.split("\n"):
            m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_./-]*)\s*:(?!=)", line.strip())
            if m:
                target_names.add(m.group(1))

        assert "release-branch-new" in target_names or "release-branch-new" in content, (
            "M19: release-branch-new target must exist (prerequisite for release-promote)"
        )
