"""P19: No-verify bypass never the default.

`--no-verify` and `COMMIT_THRESHOLD=1` MUST NOT be the default behavior
of any user-facing target. Internal helpers (`_`-prefixed) and emergency
bypass targets (`-nv`, `force-push`) are exempt from this check.
"""

import re
from pathlib import Path

MAKEFILE = Path(__file__).parent.parent.parent / "Makefile"


def _find_target_entry(content: str, target: str) -> str:
    idx = content.find(f"\n{target}:")
    if idx == -1:
        return ""
    end = content.find("\n\n", idx)
    if end == -1:
        end = len(content)
    return content[idx:end]


def _target_names(content: str) -> set[str]:
    targets: set[str] = set()
    for line in content.split("\n"):
        m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_./-]*)\s*:(?!=)", line.strip())
        if m:
            targets.add(m.group(1))
    return targets


class TestP19NoVerifyNeverDefault:
    """P19 — --no-verify and COMMIT_THRESHOLD=1 must not be defaults."""

    def test_no_verify_restricted_to_nv_prefixed_targets(self) -> None:
        content = MAKEFILE.read_text()
        all_targets = _target_names(content)
        user_facing = sorted(
            t
            for t in all_targets
            if ("push" in t or "commit" in t or "ship" in t or "release" in t or "deploy" in t or "merge" in t)
            and not t.startswith("_")
            and not t.endswith("-nv")
            and t not in ("force-push", "master-force-push", "batch-push-nv", "development-force-push", "git-amend-msg")
        )
        violations = []
        for name in user_facing:
            entry = _find_target_entry(content, name)
            if not entry:
                continue
            first_tab = entry.find("\n\t")
            if first_tab == -1:
                first_tab = entry.find("\n@")
            recipe_body = entry[first_tab:] if first_tab != -1 else ""
            if "--no-verify" in recipe_body:
                violations.append(name)
        assert not violations, (
            "P19 VIOLATION: user-facing targets hardcoding --no-verify without -nv suffix:\n" + "\n".join(violations)
        )

    def test_commit_threshold_not_hardcoded_default(self) -> None:
        content = MAKEFILE.read_text()
        all_targets = _target_names(content)
        opt_in_targets = {"batch-push-nv", "batch-push", "deploy-and-forget"}
        custom_var_targets = {"_push-parameter-audit", "_force-push-audit"}
        violations = []
        for name in sorted(all_targets):
            if name in opt_in_targets or name in custom_var_targets:
                continue
            if name.startswith("_"):
                continue
            entry = _find_target_entry(content, name)
            if not entry:
                continue
            first_tab = entry.find("\n\t")
            if first_tab == -1:
                first_tab = entry.find("\n@")
            recipe_body = entry[first_tab:] if first_tab != -1 else ""
            if "COMMIT_THRESHOLD=1" in recipe_body:
                violations.append(name)
        assert not violations, "P19 VIOLATION: targets hardcoding COMMIT_THRESHOLD=1 as default:\n" + "\n".join(
            violations
        )

    def test_push_targets_have_guard(self) -> None:
        content = MAKEFILE.read_text()
        push_targets = [
            "git-push-sandboxcom",
            "git-push-sandboxcom-nv",
            "push-dev-nv",
            "git-push-current-head-nv",
            "git-push-current-head-to-master-nv",
            "ci-push",
        ]
        target_names = _target_names(content)
        missing_guard = []
        for target in push_targets:
            if target not in target_names:
                continue
            entry = _find_target_entry(content, target)
            if "_push-rate-guard" not in entry:
                missing_guard.append(target)
        assert not missing_guard, "P19/P22: push targets missing _push-rate-guard: " + str(missing_guard)
