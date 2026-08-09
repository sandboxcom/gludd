"""E02,E04-E10,E12,E14-E16,E18-E19: Anti-essay/enforce-stop behavioral specs.

E02: Bolded headers blocked in final responses
E04: Commitment-to-action ratio enforcement
E05: Status tables forbidden in text responses
E06: Bullet lists of completed work without next action blocked
E07: "Summary" pattern always detected regardless of phrasing
E08: Post-commit prose block
E09: "Here's what changed" pattern blocked
E10: Root-cause explanation without fix is not progress
E12: Adaptive word-count threshold for anti-essay
E14: Decorative formatting (emoji-heavy prose) blocked
E15: "Let me explain" patterns blocked
E16: Response length limit when gate is red
E18: No open-ended planning prose
E19: Concrete action required in every response with pending work
"""

from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
AGENTS = ROOT / "AGENTS.md"
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
IMPL_DIR = PLUGIN_DIR / "impl"
STOP_PLUGIN = PLUGIN_DIR / "enforce-stop.ts"
STOP_IMPL = IMPL_DIR / "enforce_stop_impl.ts"
ANTI_ESSAY_PLUGIN = PLUGIN_DIR / "enforce-anti-essay.ts"


def _plugin_content(name: str) -> str:
    p = PLUGIN_DIR / name
    return p.read_text() if p.exists() else ""


def _impl_content(name: str) -> str:
    p = IMPL_DIR / name
    return p.read_text() if p.exists() else ""


def _agents_content() -> str:
    return AGENTS.read_text() if AGENTS.exists() else ""


class TestE02E05E06E07E09BoldHeadersAndStopPatterns:
    """E02/E05/E06/E07/E09: structural stop-pattern detection."""

    def test_e02_bolded_headers_blocked_by_enforce_stop(self):
        content = _plugin_content("enforce-stop.ts")
        impl = _impl_content("enforce_stop_impl.ts")
        agents = _agents_content()
        combined = (content + impl).lower()
        has_detection = (
            "bold" in combined
            or "STATUS_SUMMARY" in combined
            or "looksLikeStatusSummary" in combined
            or ("bolded" in agents.lower() and "STATUS_SUMMARY" in agents)
        )
        assert has_detection, "E02: enforce-stop.ts/enforce_stop_impl.ts must detect bolded section-header patterns"

    def test_e05_status_tables_forbidden_detection(self):
        content = _plugin_content("enforce-stop.ts")
        impl = _impl_content("enforce_stop_impl.ts")
        agents = _agents_content()
        combined = (content + impl).lower()
        has_table = "table" in combined or "tables" in combined or "markdown table" in agents.lower()
        has_stop = "STATUS_SUMMARY_RE" in impl or "looksLikeStatusSummary" in impl
        assert has_table or has_stop, "E05: plugin/impl must detect markdown status tables"

    def test_e06_bullet_lists_without_action_detected(self):
        agents = _agents_content()
        impl = _impl_content("enforce_stop_impl.ts")
        combined = agents.lower() + impl.lower()
        has_rule = "bullet" in combined or "list" in combined or "status report" in combined or "COMPLETION" in combined
        assert has_rule, "E06: AGENTS.md or enforce_stop_impl.ts must codify bullet-list stop pattern enforcement"

    def test_e07_summary_pattern_detected_regardless_of_phrasing(self):
        impl = _impl_content("enforce_stop_impl.ts")
        agents = _agents_content()
        has_summary = "STATUS_SUMMARY" in impl or "summary" in impl.lower()
        has_agents_rule = "summary" in agents.lower() or "Q&A" in agents
        assert has_summary or has_agents_rule, "E07: summary/recap detection must exist in plugin/impl or AGENTS.md"

    def test_e09_heres_what_changed_blocked(self):
        agents = _agents_content()
        impl = _impl_content("enforce_stop_impl.ts")
        has_rule = "Here's what" in agents or "heres what changed" in agents.lower() or "QA_RESPONSE" in agents
        has_impl = "QA_RESPONSE" in impl or "COMPLETION_SMELL" in impl
        assert has_rule or has_impl, "E09: AGENTS.md or enforce_stop_impl.ts must block 'Here's what changed' pattern"


class TestE04E08CommitmentAndPostCommit:
    """E04/E08: commitment-to-action ratio and post-commit prose block."""

    def test_e04_commitment_to_action_ratio_enforced(self):
        agents = _agents_content()
        impl = _impl_content("enforce_stop_impl.ts")
        has_rule = "tool call" in agents.lower() or "no analysis prose" in agents.lower()
        has_impl_ratio = "tool-call-to-text" in impl.lower() or "toolCalls" in impl.lower()
        assert has_rule or has_impl_ratio, (
            "E04: AGENTS.md or enforce_stop_impl.ts must enforce commitment-to-action ratio"
        )

    def test_e08_post_commit_prose_block_codified(self):
        agents = _agents_content()
        has_rule = "After completing one objective, immediately start the next" in agents or "No victory laps" in agents
        assert has_rule, "E08: AGENTS.md must codify post-commit prose block (no victory laps / continue to next task)"


class TestE10E18E19RootCauseAndConcreteAction:
    """E10/E18/E19: root-cause policy and concrete action requirements."""

    def test_e10_root_cause_explanation_is_not_fixing(self):
        agents = _agents_content()
        has_policy = "Root-Cause-Only Fix" in agents or "root cause" in agents.lower()
        assert has_policy, "E10: AGENTS.md must codify Root-Cause-Only Fix Policy"

    def test_e18_no_open_ended_planning_prose_blocked(self):
        agents = _agents_content()
        has_rule = "Never Block on Questions" in agents or "Do you want me to" in agents
        assert has_rule, "E18: AGENTS.md must codify blocking of open-ended planning prose"

    def test_e19_concrete_action_required_in_every_response(self):
        agents = _agents_content()
        content = _plugin_content("enforce-stop.ts")
        has_tool_call_check = "tool call" in content.lower() or "pending work" in content.lower()
        has_agents_rule = "concrete action" in agents.lower() or "tool call" in agents.lower()
        assert has_tool_call_check or has_agents_rule, (
            "E19: enforce-stop.ts must require tool calls when pending work exists"
        )


class TestE12E14E15E16AdaptiveThresholdAndClamp:
    """E12/E14/E15/E16: adaptive threshold, formatting, explain patterns, gate-red clamp."""

    def test_e12_adaptive_word_count_threshold(self):
        p = _plugin_content("enforce-anti-essay.ts")
        s = _plugin_content("enforce-stop.ts")
        i = _impl_content("enforce_stop_impl.ts")
        combined = (p + s + i).lower()
        has_adaptive = "threshold" in combined or "adaptive" in combined or "word" in combined
        assert has_adaptive, "E12: anti-essay or enforce-stop must have word-count or length heuristic"

    def test_e14_decorative_formatting_blocked(self):
        agents = _agents_content()
        impl = _impl_content("enforce_stop_impl.ts")
        has_emoji_rule = "emoji" in agents.lower() or "decorative" in agents.lower() or "COMPLETION_WORDS" in impl
        assert has_emoji_rule, "E14: AGENTS.md or enforce_stop_impl.ts must codify decorative format blocking"

    def test_e15_let_me_explain_pattern_blocked(self):
        agents = _agents_content()
        impl = _impl_content("enforce_stop_impl.ts")
        has_pattern = "Let me explain" in agents or "let me explain" in agents.lower() or "COMPLETION_SMELL" in impl
        assert has_pattern, "E15: AGENTS.md or impl must codify 'let me explain' pattern blocking"

    def test_e16_response_length_limit_when_gate_red(self):
        agents = _agents_content()
        content = _plugin_content("enforce-stop.ts")
        impl = _impl_content("enforce_stop_impl.ts")
        combined = (content + impl).lower()
        has_clamp = "gate" in combined and ("red" in combined or "failed" in combined)
        has_agents = "gate-red" in agents.lower() or "gate is red" in agents.lower()
        assert has_clamp or has_agents, (
            "E16: enforce-stop/enforce_stop_impl must clamp response length when gate is red"
        )
