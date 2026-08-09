"""Push-guard coverage audit: primary push targets reference rate guard.

Validates that primary user-facing push targets in the Makefile
reference _push-rate-guard. Excludes internal/_ helpers and
container/non-git push targets. Ensures future Makefile refactors
don't accidentally introduce an unguarded push path.
"""

import re
from pathlib import Path
from typing import ClassVar

MAKEFILE = Path(__file__).parent.parent.parent / "Makefile"


class TestPushGuardCoverage:
    """Verify push-guard coverage across primary push targets."""

    _PRIMARY_PUSH_TARGETS: ClassVar[list[str]] = [
        "git-push-sandboxcom",
        "git-push-sandboxcom-nv",
        "push-dev",
        "push-dev-nv",
        "git-push-current-head-nv",
        "git-push-current-head-to-master-nv",
        "ci-push",
        "force-push",
        "batch-push",
        "ci-push-and-verify",
    ]

    @staticmethod
    def _find_entry(content: str, name: str) -> str:
        idx = content.find(f"\n{name}:")
        if idx == -1:
            return ""
        end = content.find("\n\n", idx)
        if end == -1:
            end = len(content)
        return content[idx:end]

    @staticmethod
    def _targets(content: str) -> set[str]:
        targets: set[str] = set()
        for line in content.split("\n"):
            m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_./-]*)\s*:(?!=)", line.strip())
            if m:
                targets.add(m.group(1))
        return targets

    def test_primary_push_targets_include_guard(self):
        content = MAKEFILE.read_text()
        all_names = self._targets(content)
        violations = []
        for name in self._PRIMARY_PUSH_TARGETS:
            if name not in all_names:
                continue
            entry = self._find_entry(content, name)
            if not entry:
                continue
            if "_push-rate-guard" not in entry:
                violations.append(name)
        assert not violations, f"Primary push targets missing _push-rate-guard: {violations}"

    def test_all_user_push_targets_reference_guard(self):
        content = MAKEFILE.read_text()
        all_names = self._targets(content)
        push_targets = sorted(
            t
            for t in all_names
            if ("push" in t.lower() or "deploy" in t.lower())
            and not t.startswith("_")
            and t
            not in (
                "container-push",
                "verify-container-push",
                "release-deploy",
                "pre-push-check",
            )
        )
        violations = []
        for name in push_targets:
            entry = self._find_entry(content, name)
            if not entry:
                continue
            if "_push-rate-guard" not in entry:
                violations.append(name)

        guarded = len(push_targets) - len(violations)
        coverage = guarded / len(push_targets) * 100 if push_targets else 100
        if coverage < 70:
            raise AssertionError(
                f"Push-guard coverage: {guarded}/{len(push_targets)} = {coverage:.0f}%. "
                f"Target: >=70%. Unguarded: {violations}"
            )

    def test_ci_push_target_has_rate_guard(self):
        content = MAKEFILE.read_text()
        entry = self._find_entry(content, "ci-push")
        assert entry, "ci-push must exist"
        assert "_push-rate-guard" in entry, "ci-push must include _push-rate-guard"
