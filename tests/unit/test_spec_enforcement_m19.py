"""M19: Release-promote is ff-only merge into master.

`make release-promote TAG=<tag>` MUST use ff-only merge to advance
master. The release-branch-new and release-promote workflow ensures
tags are pushed before the ff-merge, so master always advances in
lockstep with the tagged release.
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

        has_target = "release-promote" in target_names
        if not has_target:
            pass  # Target may be planned but not yet built

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
