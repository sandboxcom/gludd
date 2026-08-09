"""P17,P20,P21,P23-P27,P29: Push discipline and CI interaction specs.

P17: Never push with dirty tree
P20: CI-green required before release cut
P21: Push history is auditable
P23: Deploy-and-forget pattern for CI-triggering pushes
P24: Never poll CI from main thread
P25: Never dispatch CI-poll subagent
P26: CI status check at natural breaks only
P27: CI-wait reserved for release-cut only
P29: Push timing is recorded
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"
AGENTS = ROOT / "AGENTS.md"
SCRIPTS_DIR = ROOT / "scripts"


def _makefile_content() -> str:
    return MAKEFILE.read_text() if MAKEFILE.exists() else ""


def _target_names(content: str) -> set[str]:
    targets: set[str] = set()
    for line in content.split("\n"):
        m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_./-]*)\s*:(?!=)", line.strip())
        if m:
            targets.add(m.group(1))
    return targets


def _find_recipe(content: str, target: str) -> str:
    idx = content.find(f"\n{target}:")
    if idx == -1:
        return ""
    end = content.find("\n\n", idx)
    if end == -1:
        end = len(content)
    return content[idx:end]


def _agents_content() -> str:
    return AGENTS.read_text() if AGENTS.exists() else ""


class TestP17P20P21PushDiscipline:
    """P17/P20/P21: push guard, CI-green, and auditability."""

    def test_p17_no_push_with_dirty_tree_policy(self):
        agents = _agents_content()
        refs = "enforce-clean-tree.ts" in agents
        assert refs, "P17: AGENTS.md must reference enforce-clean-tree.ts for dirty-tree push block"

    def test_p20_ci_green_required_before_release_cut(self):
        agents = _agents_content()
        content = _makefile_content()
        recipe = _find_recipe(content, "release-cut")
        has_ci = "require-ci-green" in recipe or "require-ci-green" in agents
        assert has_ci, "P20: release-cut must gate on require-ci-green"

    def test_p21_push_history_auditable(self):
        agents = _agents_content()
        script = SCRIPTS_DIR / "ci_check_cooldown.py"
        has_state = script.exists()
        has_policy = "push" in agents.lower() and (
            "log" in agents.lower() or "timestamp" in agents.lower() or "audit" in agents.lower()
        )
        assert has_state or has_policy, "P21: push history must be auditable via state tracking"


class TestP23DeployAndForget:
    """P23: deploy-and-forget pattern."""

    def test_p23_deploy_and_forget_target_exists(self):
        content = _makefile_content()
        targets = _target_names(content)
        assert "deploy-and-forget" in targets, "P23: Makefile must have deploy-and-forget target"


class TestP24P25P26P27CINoPoll:
    """P24/P25/P26/P27: CI polling discipline."""

    def test_p24_no_ci_poll_from_main_thread_policy(self):
        agents = _agents_content()
        has_rule = "enforce-no-wait.ts" in agents or "Never poll CI from main thread" in agents
        assert has_rule, "P24: AGENTS.md must codify no-CI-poll-from-main-thread rule"

    def test_p25_no_ci_poll_subagent_policy(self):
        agents = _agents_content()
        has_rule = "CI_POLL_DISPATCH" in agents or "CI-poll subagent" in agents.lower()
        assert has_rule, "P25: AGENTS.md must codify no-CI-poll-subagent dispatch rule"

    def test_p26_ci_check_at_natural_breaks(self):
        agents = _agents_content()
        has_rule = "natural breaks" in agents.lower()
        assert has_rule, "P26: AGENTS.md must codify CI-check-at-natural-breaks rule"

    def test_p27_ci_wait_reserved_for_release_cut(self):
        agents = _agents_content()
        has_rule = "ci-wait" in agents.lower() and "release" in agents.lower()
        assert has_rule, "P27: AGENTS.md must restrict ci-wait to release-cut"


class TestP29PushTiming:
    """P29: push timing must be recorded."""

    def test_p29_push_timing_recorded(self):
        agents = _agents_content()
        content = _makefile_content()
        has_record = "_push-rate-guard" in content
        has_policy = "push" in agents.lower() and "timestamp" in agents.lower()
        assert has_record or has_policy, "P29: push timing must be recorded via _push-rate-guard or equivalent"
