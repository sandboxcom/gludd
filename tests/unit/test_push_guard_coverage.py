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

    @classmethod
    def _is_guarded(
        cls,
        content: str,
        name: str,
        all_names: set[str],
        seen: set[str] | None = None,
    ) -> bool:
        """Return whether *name* reaches the rate guard directly or transitively.

        Wrapper targets are deliberately DRY: ``force-push`` and
        ``commit-and-ship-push``, for example, delegate to the canonical push
        recipes instead of duplicating their safeguards.  Follow both Make
        prerequisites and recursive ``$(MAKE)`` calls so the audit verifies
        the effective call graph rather than requiring duplicated guards.
        """
        visited = set() if seen is None else seen
        if name in visited:
            return False
        visited.add(name)
        entry = cls._find_entry(content, name)
        if not entry:
            return False
        if "_push-rate-guard" in entry:
            return True

        lines = entry.splitlines()
        dependencies: set[str] = set()
        if lines:
            dependencies.update(
                token
                for token in re.findall(r"[a-zA-Z_][a-zA-Z0-9_./-]*", lines[0].partition(":")[2])
                if token in all_names
            )
        for line in lines[1:]:
            if "$(MAKE)" not in line:
                continue
            make_args = line.split("$(MAKE)", 1)[1]
            dependencies.update(
                token
                for token in re.findall(r"[a-zA-Z_][a-zA-Z0-9_./-]*", make_args)
                if token in all_names
            )
        return any(cls._is_guarded(content, dependency, all_names, visited.copy()) for dependency in dependencies)

    def test_primary_push_targets_include_guard(self):
        content = MAKEFILE.read_text()
        all_names = self._targets(content)
        violations = []
        for name in self._PRIMARY_PUSH_TARGETS:
            if name not in all_names:
                continue
            if not self._is_guarded(content, name, all_names):
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
                "audit-push-cooldown-integrity",
            )
        )
        violations = []
        for name in push_targets:
            if not self._is_guarded(content, name, all_names):
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

    def test_transitive_wrapper_guard_is_detected(self):
        content = "\ncanonical-push: _push-rate-guard\n\t@true\n\nwrapper-push:\n\t@$(MAKE) canonical-push\n"
        names = self._targets(content)
        assert self._is_guarded(content, "wrapper-push", names)
