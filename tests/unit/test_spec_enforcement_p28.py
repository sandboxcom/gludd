"""P28: Push cannot shortcut CI-in-flight check.

No push target (even with FORCE=1) MUST bypass the CI-in-flight check.
Every push path must include `_push-rate-guard` which enforces the
branch-level active-run check via `ci_push_guard.py`.
"""

from pathlib import Path
from typing import ClassVar

MAKEFILE = Path(__file__).parent.parent.parent / "Makefile"


def _find_target_recipe(content: str, target: str) -> str:
    """Extract a target's recipe including its prerequisites."""
    idx = content.find(f"\n{target}:")
    if idx == -1:
        return ""
    end = content.find("\n\n", idx)
    if end == -1:
        end = len(content)
    return content[idx:end]


class TestP28PushNoShortcutCIInFlight:
    """P28 — all push paths include _push-rate-guard (CI-in-flight check)."""

    _ALL_PUSH_TARGETS: ClassVar[list[str]] = [
        "git-push-sandboxcom",
        "git-push-sandboxcom-nv",
        "push-dev",
        "push-dev-nv",
        "git-push-current-head-nv",
        "git-push-current-head-to-master-nv",
        "ci-push",
        "force-push",
        "batch-push",
    ]

    def test_no_push_target_skips_push_rate_guard(self) -> None:
        content = MAKEFILE.read_text()
        violations = []
        for target in self._ALL_PUSH_TARGETS:
            recipe = _find_target_recipe(content, target)
            if not recipe:
                continue
            has_guard = "_push-rate-guard" in recipe
            if not has_guard:
                violations.append(f"'{target}' does not reference _push-rate-guard")
        if violations:
            raise AssertionError(
                "P28 VIOLATION — push targets missing _push-rate-guard (CI-in-flight check):\n" + "\n".join(violations)
            )

    def test_push_rate_guard_uses_ci_push_guard(self) -> None:
        content = MAKEFILE.read_text()
        guard_recipe = _find_target_recipe(content, "_push-rate-guard")
        assert guard_recipe, "_push-rate-guard target must exist"
        assert "scripts/ci_push_guard.py" in guard_recipe, (
            "P28: _push-rate-guard must invoke ci_push_guard.py for CI-in-flight check"
        )
        assert "PUSH_BRANCH" in guard_recipe, "P28: _push-rate-guard must check branch-specific CI state"

    def test_force_push_delegates_to_guarded_target(self) -> None:
        content = MAKEFILE.read_text()
        force_recipe = _find_target_recipe(content, "force-push")
        assert force_recipe, "force-push target must exist"
        assert "git-push-sandboxcom" in force_recipe or "_push-rate-guard" in force_recipe, (
            "P28: force-push must delegate to a guarded push target"
        )

    def test_deploy_and_forget_records_timestamp(self) -> None:
        content = MAKEFILE.read_text()
        if "deploy-and-forget:" in content:
            recipe = _find_target_recipe(content, "deploy-and-forget")
            assert "ci_check_cooldown.py" in recipe or "push-timestamps" in recipe, (
                "P28: deploy-and-forget must record push timestamp"
            )
