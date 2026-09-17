"""Phase CID — CI Interaction Discipline verification.

Structural tests verifying the CI interaction guardrails referenced by the 15
CID-phase items in TASKS.md. The features already exist; these tests pin their
presence so a regression that strips them is caught at gate time.

Coverage map (CID item -> test class):
  CID.1  cooldown-enforced ci-verdict-safe       -> TestCiVerdictSafeCooldown
  CID.2  no CI-poll subagent / limiter           -> TestCiPollLimiter, TestNoCiPollDispatchRule
  CID.3  deploy-and-forget pattern               -> TestDeployAndForget
  CID.4  CI green required only for release-cut  -> TestCiWaitReleaseCutOnly
  CID.5  ci-wait is release-cut only             -> TestCiWaitReleaseCutOnly
  CID.6  cooldown != pending (exit 3 = UNKNOWN)  -> TestCiVerdictSafeExitCodes
  CID.7  stale-run warning (headSha mechanism)   -> TestCiVerdictHeadSha, TestStaleRunDocumentation
  CID.8  ci-cancel for emergency cancellation    -> TestCiCancelTarget
  CID.9  monitor CI from subagent, not main      -> TestCiStatusReadOnly, TestNoWaitPluginRule
  CID.10 run id + headSha in claims              -> TestCiVerdictHeadSha
  CID.11 cancellation discipline                 -> TestCiCancelTarget
  CID.12 verify-remote after push                -> TestVerifyRemoteRule
  CID.13 CI status format                        -> TestCiVerdictOutputFormat
  CID.14 no-op push detection                    -> TestNoopPushDetectionRule
  CID.15 CI run URL in release evidence          -> TestReleaseEvidenceRule
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-no-ci-poll.ts"
NOWAIT_PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-no-wait.ts"
COOLDOWN_SCRIPT = ROOT / "scripts" / "ci_check_cooldown.py"
MAKEFILE = ROOT / "Makefile"
OPENCODE_JSON = ROOT / "opencode.json"
AGENTS_MD = ROOT / "AGENTS.md"


def _plugin_src() -> str:
    assert PLUGIN_PATH.exists(), f"Plugin not found: {PLUGIN_PATH}"
    return PLUGIN_PATH.read_text(encoding="utf-8")


def _nowait_src() -> str:
    assert NOWAIT_PLUGIN_PATH.exists(), f"Plugin not found: {NOWAIT_PLUGIN_PATH}"
    return NOWAIT_PLUGIN_PATH.read_text(encoding="utf-8")


def _cooldown_src() -> str:
    assert COOLDOWN_SCRIPT.exists(), f"Cooldown script not found: {COOLDOWN_SCRIPT}"
    return COOLDOWN_SCRIPT.read_text(encoding="utf-8")


def _makefile() -> str:
    return MAKEFILE.read_text(encoding="utf-8")


def _target_recipe(makefile: str, target: str) -> str:
    """Extract a single Makefile target's recipe lines (for assertion context)."""
    lines = makefile.splitlines()
    out: list[str] = []
    capturing = False
    for line in lines:
        if line.startswith(f"{target}:"):
            capturing = True
            out.append(line)
            continue
        if capturing:
            if (
                line
                and not line.startswith(("\t", " ", "#"))
                and not line.startswith(target)
                and (line[0].isalpha() or line[0] == "_")
            ):
                break
            out.append(line)
    return "\n".join(out)


# ── CID.1 / CID.6: ci-verdict-safe cooldown ──────────────────────────────────


class TestCiVerdictSafeCooldown:
    """CID.1 — Check CI at most once per 10 minutes via ci-verdict-safe."""

    def test_target_exists(self):
        mk = _makefile()
        assert "\nci-verdict-safe:" in mk or mk.startswith("ci-verdict-safe:"), (
            "ci-verdict-safe target must exist in Makefile (CID.1)"
        )

    def test_target_invokes_cooldown_script(self):
        mk = _makefile()
        assert "scripts/ci_check_cooldown.py check" in mk, (
            "ci-verdict-safe must delegate to ci_check_cooldown.py (CID.1)"
        )

    def test_cooldown_script_exists(self):
        assert COOLDOWN_SCRIPT.exists(), (
            "scripts/ci_check_cooldown.py must exist (CID.1 / MX.15 / MK.16)"
        )

    def test_default_cooldown_is_600_seconds(self):
        src = _cooldown_src()
        assert "600" in src, "Default cooldown must be 600s (10 min) per CID.1 / LM.6"
        assert "CI_CHECK_COOLDOWN_SEC" in src, (
            "CI_CHECK_COOLDOWN_SEC env override must be documented (LM.6)"
        )

    def test_cooldown_records_last_known_verdict(self):
        src = _cooldown_src()
        assert "last_verdict" in src, (
            "Cooldown state must record last_verdict (CID.6 / CP.9 — cooldown != pending)"
        )

    def test_cooldown_help_documents_force_bypass(self):
        src = _cooldown_src()
        assert "FORCE=1" in src, "FORCE=1 release-cut bypass must be documented"


# ── CID.6: ci-verdict-safe exit codes ────────────────────────────────────────


class TestCiVerdictSafeExitCodes:
    """CID.6 — exit 3 means cooldown REFUSED (state UNKNOWN), not PENDING."""

    def test_exit_code_3_returned_on_cooldown_block(self):
        src = _cooldown_src()
        assert "return 3" in src, (
            "Cooldown block must return exit 3 (CID.6 contract)"
        )

    def test_exit_code_1_on_failure_during_cooldown(self):
        src = _cooldown_src()
        assert "return 1" in src, (
            "Last-known failure during cooldown must still surface as exit 1 (CID.6)"
        )

    def test_makefile_documents_exit_codes(self):
        mk = _makefile()
        recipe = _target_recipe(mk, "ci-verdict-safe")
        assert "3=COOLDOWN" in mk or "3=COOLDOWN" in recipe, (
            "Makefile must document exit 3 = COOLDOWN-ACTIVE (CID.6)"
        )
        for code_doc in ("0=GREEN", "1=RED", "2=PENDING"):
            assert code_doc in mk, (
                f"Makefile must document ci-verdict-safe exit code {code_doc} (CID.6)"
            )

    def test_cooldown_unknown_not_pending(self):
        """CID.6 — cooldown output must not be misread as PENDING."""
        src = _cooldown_src()
        assert "CI-COOLDOWN" in src, (
            "Cooldown output must be prefixed CI-COOLDOWN (CID.6 — distinguish from PENDING)"
        )


# ── CID.2 / FM.21: CI polling limiter plugin ─────────────────────────────────


class TestCiPollLimiter:
    """CID.2 / FM.21 — enforce-no-ci-poll.ts blocks after 3 consecutive polls."""

    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.exists(), (
            "enforce-no-ci-poll.ts must exist (CID.2 / BP.2 / RP.18)"
        )

    def test_plugin_registered_in_opencode(self):
        cfg = OPENCODE_JSON.read_text(encoding="utf-8")
        assert "enforce-no-ci-poll" in cfg, (
            "enforce-no-ci-poll must be registered in opencode.json (CID.2)"
        )

    def test_subagent_guard_present(self):
        assert "isSubagent()" in _plugin_src(), (
            "Plugin must skip enforcement inside subagents (subagent isolation)"
        )

    def test_fail_open_present(self):
        assert "catch" in _plugin_src(), "Plugin must fail-open on error"

    def test_tracks_consecutive_ci_calls(self):
        src = _plugin_src()
        assert "MAX_CONSECUTIVE_POLLS" in src, (
            "Plugin must track consecutive CI polls (CID.2)"
        )
        assert "readPollStreak" in src or "writePollStreak" in src, (
            "Plugin must persist poll streak counter (CID.2)"
        )

    def test_default_max_polls_is_3(self):
        src = _plugin_src()
        assert '"3"' in src, (
            "Default MAX_CONSECUTIVE_POLLS must be 3 (CID.2 / FM.21)"
        )

    def test_denies_after_threshold(self):
        src = _plugin_src()
        assert "count > MAX_CONSECUTIVE_POLLS" in src, (
            "Plugin must deny when count exceeds MAX_CONSECUTIVE_POLLS (CID.2)"
        )
        assert "permissionDecision" in src and "deny" in src, (
            "Deny must be a permissionDecision (CID.2)"
        )

    def test_resets_on_productive_work(self):
        src = _plugin_src()
        for productive in ("git-commit", "ship-commit", "release-cut", "batch-push"):
            assert productive in src, (
                f"Plugin must reset counter on productive target '{productive}' (CID.2)"
            )

    def test_tracks_ci_verdict_safe(self):
        src = _plugin_src()
        assert "ci-verdict-safe" in src, (
            "Plugin must count ci-verdict-safe toward the poll limit (CID.1)"
        )

    def test_state_file_path(self):
        src = _plugin_src()
        assert "gludd-ci-poll" in src, (
            "Plugin must persist state to a gludd-ci-poll state file (CID.2)"
        )

    def test_env_override_for_threshold(self):
        src = _plugin_src()
        assert "GLUDD_CI_POLL_MAX" in src, (
            "GLUDD_CI_POLL_MAX env override must exist (CID.2)"
        )


# ── CID.2: no CI-poll subagent dispatch (enforce-no-wait rule) ────────────────


class TestNoCiPollDispatchRule:
    """CID.2 — dispatching a 'poll CI until terminal' subagent is forbidden."""

    def test_no_wait_plugin_exists(self):
        assert NOWAIT_PLUGIN_PATH.exists(), (
            "enforce-no-wait.ts must exist to block CI-poll subagent dispatch (CID.2)"
        )

    def test_dispatch_patterns_defined(self):
        src = _nowait_src()
        assert "ci-verdict" in src, (
            "enforce-no-wait.ts must reference ci-verdict in dispatch patterns (CID.2)"
        )

    def test_release_cut_only_guidance_present(self):
        src = _nowait_src()
        assert "release-cut" in src.lower(), (
            "Plugin must guide that ci-wait is release-cut only (CID.5)"
        )


# ── CID.3: deploy-and-forget pattern ─────────────────────────────────────────


class TestDeployAndForget:
    """CID.3 — push + record timestamp + resume work."""

    def test_target_exists(self):
        mk = _makefile()
        assert "\ndeploy-and-forget:" in mk or mk.startswith("deploy-and-forget:"), (
            "deploy-and-forget target must exist (CID.3)"
        )

    def test_invokes_cooldown_deploy(self):
        mk = _makefile()
        recipe = _target_recipe(mk, "deploy-and-forget")
        assert "ci_check_cooldown.py deploy" in recipe, (
            "deploy-and-forget must record push timestamp via cooldown script (CID.3)"
        )

    def test_invokes_one_push_path_without_threshold_retry(self) -> None:
        """A rejected push must not fall through to a second guarded push."""
        recipe = _target_recipe(_makefile(), "deploy-and-forget")
        assert "COMMIT_THRESHOLD=1" not in recipe, (
            "deploy-and-forget must not invoke the forbidden one-commit threshold"
        )
        assert "batch-push" not in recipe, (
            "deploy-and-forget must select one direct guarded push target, not retry"
        )
        assert recipe.count("git-push-sandboxcom") == 1
        assert recipe.count("push-dev") == 1

    def test_validate_only_exercises_routing_without_network_or_push(self, tmp_path: Path) -> None:
        state_file = tmp_path / "cooldown.json"
        history_file = tmp_path / "history.json"
        restart_file = tmp_path / "restart-count"
        env = os.environ.copy()
        env.update(
            {
                "GLUDD_CI_STATE_FILE": str(state_file),
                "GLUDD_CI_HISTORY_FILE": str(history_file),
                "GLUDD_CI_RESTART_COUNT_FILE": str(restart_file),
            }
        )
        result = subprocess.run(
            [
                "make",
                "deploy-and-forget",
                "BRANCH=feature/hermetic-ci",
                "DEPLOY_AND_FORGET_VALIDATE_ONLY=1",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        assert "DEPLOY-AND-FORGET-VALID" in result.stdout
        assert "route=current-branch" in result.stdout
        assert "no network, push, or state mutation" in result.stdout
        assert not state_file.exists()
        assert not history_file.exists()
        assert not restart_file.exists()

    def test_prints_checkback_time(self):
        src = _cooldown_src()
        assert "CHECKBACK" in src, (
            "deploy-and-forget must print a CHECKBACK time (CID.3 — fire-and-forget)"
        )

    def test_documents_resume_work(self):
        src = _cooldown_src()
        assert "dispatch real work" in src.lower() or "resume real work" in src.lower(), (
            "deploy-and-forget must instruct resuming real work (CID.3)"
        )


# ── CID.4 / CID.5: ci-wait is release-cut only ───────────────────────────────


class TestCiWaitReleaseCutOnly:
    """CID.4 / CID.5 — ci-wait exists ONLY for the release-cut pipeline."""

    def test_ci_wait_target_exists(self):
        mk = _makefile()
        assert "\nci-wait:" in mk or mk.startswith("ci-wait:"), (
            "ci-wait target must exist for release-cut internals (CID.5)"
        )

    def test_ci_wait_release_cut_documented_in_agents(self):
        agents = AGENTS_MD.read_text(encoding="utf-8")
        assert "release-cut" in agents.lower(), (
            "AGENTS.md must document release-cut policy (CID.4)"
        )
        assert "ci-wait" in agents, "AGENTS.md must mention ci-wait (CID.5)"

    def test_ci_wait_used_by_release_cut(self):
        mk = _makefile()
        assert "ci-wait" in mk, "ci-wait must be referenced by release-cut pipeline (CID.5)"

    def test_agents_states_ci_wait_release_cut_only(self):
        agents = AGENTS_MD.read_text(encoding="utf-8")
        snippet = "release-cut ONLY"
        assert snippet.lower() in agents.lower() or "release-cut only" in agents.lower(), (
            "AGENTS.md must state ci-wait is release-cut ONLY (CID.5)"
        )


# ── CID.7: stale-run detection (headSha mechanism) ───────────────────────────


class TestCiVerdictHeadSha:
    """CID.7 / CID.10 — ci-verdict prints headSha so stale runs are detectable."""

    def test_ci_verdict_target_exists(self):
        mk = _makefile()
        assert "\nci-verdict:" in mk or mk.startswith("ci-verdict:"), (
            "ci-verdict target must exist (CID.7)"
        )

    def test_ci_verdict_prints_headsha(self):
        mk = _makefile()
        recipe = _target_recipe(mk, "ci-verdict")
        assert "headSha" in recipe, (
            "ci-verdict must print headSha in output (CID.7 — stale-run detection)"
        )

    def test_ci_verdict_prints_run_id(self):
        mk = _makefile()
        recipe = _target_recipe(mk, "ci-verdict")
        assert "RUN_ID" in recipe or "databaseId" in recipe, (
            "ci-verdict must print the run id (CID.10)"
        )

    def test_ci_verdict_prefers_safe_variant(self):
        mk = _makefile()
        assert "ci-verdict-safe" in mk, (
            "Makefile must direct callers to ci-verdict-safe over bare ci-verdict (CID.1)"
        )


class TestStaleRunDocumentation:
    """CID.7 — the stale-run contract (headSha != branch tip = stale) is documented."""

    def test_agents_documents_stale_run_rule(self):
        agents = AGENTS_MD.read_text(encoding="utf-8")
        assert "headSha" in agents, (
            "AGENTS.md must document headSha matching for CI verdicts (CID.7)"
        )
        assert "stale" in agents.lower(), (
            "AGENTS.md must describe stale-run semantics (CID.7)"
        )

    def test_branch_landing_integrity_section_exists(self):
        agents = AGENTS_MD.read_text(encoding="utf-8")
        assert "Branch-landing integrity" in agents, (
            "AGENTS.md must have the branch-landing integrity section (CID.7 / CID.12)"
        )


# ── CID.8 / CID.11: ci-cancel target ─────────────────────────────────────────


class TestCiCancelTarget:
    """CID.8 / CID.11 / MK.18 — ci-cancel target for emergency cancellation."""

    def test_target_exists(self):
        mk = _makefile()
        assert "\nci-cancel:" in mk or mk.startswith("ci-cancel:"), (
            "ci-cancel target must exist (CID.8 / MK.18)"
        )

    def test_invokes_gh_run_cancel(self):
        mk = _makefile()
        recipe = _target_recipe(mk, "ci-cancel")
        assert "gh run cancel" in recipe, (
            "ci-cancel must invoke `gh run cancel` (CID.8)"
        )

    def test_requires_run_id(self):
        mk = _makefile()
        recipe = _target_recipe(mk, "ci-cancel")
        assert "$(RUN)" in recipe, "ci-cancel must take a RUN=<id> argument (CID.8)"


# ── CID.9: monitor CI from subagent, not main thread ─────────────────────────


class TestCiStatusReadOnly:
    """CID.9 — ci-status is a read-only listing; never polled on main thread."""

    def test_target_exists(self):
        mk = _makefile()
        assert "\nci-status:" in mk or mk.startswith("ci-status:"), (
            "ci-status target must exist (CID.9)"
        )

    def test_uses_gh_run_list(self):
        mk = _makefile()
        recipe = _target_recipe(mk, "ci-status")
        assert "gh run list" in recipe, (
            "ci-status must use `gh run list` (read-only) (CID.9)"
        )


class TestNoWaitPluginRule:
    """CID.9 — enforce-no-wait blocks main-thread CI monitoring."""

    def test_no_wait_plugin_exists(self):
        assert NOWAIT_PLUGIN_PATH.exists(), (
            "enforce-no-wait.ts must exist to block main-thread CI ops (CID.9 / DP.2)"
        )

    def test_plugin_registered(self):
        cfg = OPENCODE_JSON.read_text(encoding="utf-8")
        assert "enforce-no-wait" in cfg, (
            "enforce-no-wait must be registered in opencode.json (CID.9)"
        )

    def test_blocks_ci_wait_on_main_thread(self):
        src = _nowait_src()
        assert "ci-wait" in src or "gate-tail" in src, (
            "enforce-no-wait must block ci-wait/gate-tail on main thread (CID.9)"
        )


# ── CID.13: CI status output format ──────────────────────────────────────────


class TestCiVerdictOutputFormat:
    """CID.13 — verdict format: 'CI GREEN: sha= run=' / 'CI RED: sha= run= conclusion='."""

    def test_green_format(self):
        mk = _makefile()
        recipe = _target_recipe(mk, "ci-verdict")
        assert "CI GREEN" in recipe, (
            "ci-verdict must emit 'CI GREEN: ...' format (CID.13)"
        )

    def test_red_format(self):
        mk = _makefile()
        recipe = _target_recipe(mk, "ci-verdict")
        assert "CI RED" in recipe, "ci-verdict must emit 'CI RED: ...' format (CID.13)"

    def test_pending_format(self):
        mk = _makefile()
        recipe = _target_recipe(mk, "ci-verdict")
        assert "CI PENDING" in recipe, (
            "ci-verdict must emit 'CI PENDING: ...' format (CID.13)"
        )

    def test_includes_sha_and_run_in_output(self):
        mk = _makefile()
        recipe = _target_recipe(mk, "ci-verdict")
        assert "HEAD_SHA" in recipe and "RUN_ID" in recipe, (
            "ci-verdict output must include both SHA and run id (CID.13)"
        )


# ── CID.12 / CID.14 / CID.15: documented rules (AGENTS.md) ───────────────────


class TestVerifyRemoteRule:
    """CID.12 — verify-remote after every push (documented rule)."""

    def test_verify_remote_documented(self):
        agents = AGENTS_MD.read_text(encoding="utf-8")
        assert "verify-remote" in agents, (
            "AGENTS.md must document make verify-remote after push (CID.12)"
        )

    def test_verify_remote_target_exists(self):
        mk = _makefile()
        assert "verify-remote" in mk, (
            "Makefile must define a verify-remote target (CID.12)"
        )


class TestNoopPushDetectionRule:
    """CID.14 — don't push if remote already has the HEAD SHA."""

    def test_batch_push_target_exists(self):
        mk = _makefile()
        assert "batch-push" in mk, (
            "Makefile must define batch-push (CID.14 — sanctioned push)"
        )

    def test_agents_documents_push_discipline(self):
        agents = AGENTS_MD.read_text(encoding="utf-8")
        assert "batch-push" in agents, (
            "AGENTS.md must document batch-push discipline (CID.14)"
        )


class TestReleaseEvidenceRule:
    """CID.15 — release evidence includes CI run URL + conclusion."""

    def test_release_evidence_documented(self):
        agents = AGENTS_MD.read_text(encoding="utf-8")
        assert "verify-release" in agents, (
            "AGENTS.md must reference verify-release-completeness for evidence (CID.15)"
        )

    def test_ci_verdict_referenced_for_evidence(self):
        agents = AGENTS_MD.read_text(encoding="utf-8")
        assert "ci-verdict" in agents, (
            "AGENTS.md must reference ci-verdict as evidence source (CID.15 / CID.10)"
        )
