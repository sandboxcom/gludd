# BEHAVIORAL ENFORCEMENT SPECIFICATIONS — 500 numbered specs

**Version:** 2.0
**Date:** 2026-07-19
**Status:** Active — corresponding enforcement mechanisms tracked in `tests/unit/test_behavioral_specs.py`

Each spec defines a behavioral invariant. Each spec MUST have a corresponding
enforcement mechanism (plugin, Makefile guard, or AGENTS.md policy section) and
a structural test verifying that mechanism exists.

Groups P-R (200 existing), W-Z (300 new). Total: 500 specs across 20 groups.

---

## Group P — Push Discipline (P01–P30)

### P01 — No push while CI is in_progress
The agent MUST NOT push commits to a branch while any CI run is `in_progress` on that branch.
**Enforcement:** `enforce-batch-push.ts` tool.execute.before
**Test:** `test_p01_no_push_while_ci_pending`

### P02 — CI-busy-check on every push target
Every push target (git-push-sandboxcom, development-push, batch-push) MUST invoke the CI-busy guard before pushing.
**Enforcement:** Makefile `_push-rate-guard` + `ci-busy-check` prerequisite
**Test:** `test_p02_ci_busy_check_on_all_push_targets`

### P03 — No COMMIT_THRESHOLD=1
The agent MUST NOT use `COMMIT_THRESHOLD=1` to push commits individually.
**Enforcement:** AGENTS.md "Don't Push Every Commit" section
**Test:** `test_p03_no_commit_threshold_1`

### P04 — Push-to-push minimum interval
A second push within 120 seconds of the prior push MUST be hard-denied.
**Enforcement:** `_push-rate-guard` push-cooldown check
**Test:** `test_p04_push_to_push_minimum_interval`

### P05 — Verify remote after every push
After any push, the agent MUST run `make verify-remote` and confirm the remote tip matches.
**Enforcement:** AGENTS.md "Verify the remote after every push"
**Test:** `test_p05_verify_remote_after_push`

### P06 — Never report CI verdict whose headSha != branch tip
The agent MUST NOT claim a CI verdict for a run whose headSha does not match the current branch tip.
**Enforcement:** `ci-verdict` output is stale-run-aware
**Test:** `test_p06_ci_verdict_must_match_head_sha`

### P07 — CI cooldown check before any status claim
Before making any CI-status claim, the agent MUST use `make ci-verdict-safe` (cooldown-aware) not bare `make ci-verdict`.
**Enforcement:** `scripts/ci_check_cooldown.py` + AGENTS.md
**Test:** `test_p07_ci_cooldown_before_status_claim`

### P08 — CI-COOLDOWN-UNKNOWN MUST NOT be reported as PENDING
When CI cooldown blocks a check, the agent MUST NOT interpret that as "CI is pending."
**Enforcement:** `scripts/ci_check_cooldown.py` output labeling + AGENTS.md
**Test:** `test_p08_cooldown_not_reported_as_pending`

### P09 — Never push while gate is red
The agent MUST NOT push commits to a branch while the local gate is red or unrun.
**Enforcement:** `enforce-batch-push.ts` gate-status check (planned)
**Test:** `test_p09_no_push_while_gate_red`

### P10 — Push only via sanctioned targets
The agent MUST use only `make batch-push`, `make development-push`, or `make git-push-sandboxcom` to push — never raw `git push`.
**Enforcement:** `enforce-make.ts` non-make command block
**Test:** `test_p10_push_only_via_sanctioned_targets`

### P11 — Maximum one CI run in flight per branch
The agent MUST ensure at most one CI run is in progress on any given branch before pushing.
**Enforcement:** `_push-rate-guard` CI-in-flight check
**Test:** `test_p11_max_one_ci_run_per_branch`

### P12 — Cancelled-run thrash detection
When >3 cancelled runs exist in the last 2 hours, push MUST be blocked.
**Enforcement:** `_push-rate-guard` thrash detection
**Test:** `test_p12_cancelled_run_thrash_detection`

### P13 — FORCE=1 bypass reserved for release-cut only
The FORCE=1 bypass for CI/push guards MUST be used only for release-cut pipeline steps.
**Enforcement:** AGENTS.md "CI-Poll Subagents Are Forbidden" + plugin (planned)
**Test:** `test_p13_force_bypass_reserved_for_release_cut`

### P14 — Never push master directly from worktree
Master/development push MUST originate from the main checkout, never from a worktree.
**Enforcement:** AGENTS.md "Branch discipline" — shared-branch mutations main checkout only
**Test:** `test_p14_no_push_master_from_worktree`

### P15 — Batch local commits; push once
The agent MUST accumulate commits locally with `make ship-commit PUSH=0` and push in batches via `make batch-push`.
**Enforcement:** AGENTS.md "Don't Push Every Commit" + batch-push default threshold
**Test:** `test_p15_batch_local_push_once`

### P16 — Push rate guard is fail-closed
If the push rate guard cannot determine CI state (gh not installed, network down), it MUST deny the push — never fail-open.
**Enforcement:** `scripts/ci_push_guard.py` fail-closed logic
**Test:** `test_p16_push_rate_guard_fail_closed`

### P17 — Never push with dirty tree
The agent MUST NOT push to master/development while the working tree is dirty.
**Enforcement:** `enforce-clean-tree.ts` block on task/agent/workflow dispatch when dirty
**Test:** `test_p17_no_push_with_dirty_tree`

### P18 — Commit after green gate only
Every commit-shaped target MUST enforce `.gate-status` freshness and green status.
**Enforcement:** Makefile `_gate-fresh-check` prerequisite on all commit targets
**Test:** `test_p18_commit_after_green_gate_only`

### P19 — No-verify bypass never the default
`--no-verify` and `COMMIT_THRESHOLD=1` MUST NOT be the default behavior of any target.
**Enforcement:** Makefile defaults
**Test:** `test_p19_no_verify_never_default`

### P20 — CI-green required before release cut
`make release-cut` MUST gate on `require-ci-green` as step 0 and abort if CI is not green.
**Enforcement:** `scripts/require_ci_green.py` + `make release-cut`
**Test:** `test_p20_ci_green_required_before_release_cut`

### P21 — Push history is auditable
Every push MUST be logged with timestamp, target branch, SHA, and CI state at push time.
**Enforcement:** `scripts/ci_push_guard.py` state recording
**Test:** `test_p21_push_history_auditable`

### P22 — Push guard is not circumventable
No make target or agent behavior MUST provide a backdoor around the push guard.
**Enforcement:** Makefile audit — all push paths go through `_push-rate-guard`
**Test:** `test_p22_push_guard_not_circumventable`

### P23 — Deploy-and-forget pattern for CI-triggering pushes
When pushing for CI validation (not release), the agent MUST use `make deploy-and-forget` (fire-and-forget pattern).
**Enforcement:** Makefile `deploy-and-forget` target
**Test:** `test_p23_deploy_and_forget_pattern`

### P24 — Never poll CI from main thread
The agent MUST NOT run `make ci-verdict` or `make ci-wait` on the main thread.
**Enforcement:** `enforce-no-wait.ts` + AGENTS.md anti-wait rule
**Test:** `test_p24_no_poll_ci_from_main_thread`

### P25 — Never dispatch CI-poll subagent
A subagent whose sole task is polling CI and sleeping MUST NOT be dispatched.
**Enforcement:** `enforce-no-wait.ts` CI_POLL_DISPATCH_PATTERNS
**Test:** `test_p25_no_ci_poll_subagent`

### P26 — CI status check at natural breaks only
CI MUST be checked at natural breaks (subagent result ingestion), not continuously.
**Enforcement:** AGENTS.md "CI is checked at natural breaks, not polled"
**Test:** `test_p26_ci_check_at_natural_breaks`

### P27 — CI-wait reserved for release-cut
`make ci-wait` MUST be used only within `make release-cut`; general-purpose blocking use is forbidden.
**Enforcement:** AGENTS.md "CI-Poll Subagents" rule #6
**Test:** `test_p27_ci_wait_reserved_for_release_cut`

### P28 — Push cannot shortcut CI-in-flight check
No push target (even with FORCE=1) MUST bypass the CI-in-flight check.
**Enforcement:** Makefile all push paths include `_push-rate-guard`
**Test:** `test_p28_no_shortcut_ci_in_flight_check`

### P29 — Push timing is recorded
Every push MUST record a timestamp in `/tmp/gludd-ci-push-state.json` or equivalent.
**Enforcement:** Makefile `_push-rate-guard` timestamp recording
**Test:** `test_p29_push_timing_recorded`

### P30 — Push guard survives Makefile refactors
Any future Makefile refactor MUST keep push guard coverage at 100% of push targets.
**Enforcement:** `tests/unit/test_push_guard_coverage.py` structural check
**Test:** `test_p30_push_guard_survives_makefile_refactor`

---

## Group B — Branch Discipline (B01–B25)

### B01 — Agent MUST work on the correct branch
Before any mutating operation, the agent MUST verify it is on the intended branch.
**Enforcement:** `enforce-objective.ts` branch check (planned extension)
**Test:** `test_b01_agent_must_work_on_correct_branch`

### B02 — Never push feature work directly to master
Feature work MUST land on `development` or a feature branch, never directly on `master`.
**Enforcement:** AGENTS.md "Branch discipline" + `enforce-branch-discipline.ts` (planned)
**Test:** `test_b02_no_direct_feature_push_to_master`

### B03 — Master is for merges from development ONLY
The only permitted commits to `master` are merges from `development` or emergency pipeline fixes.
**Enforcement:** AGENTS.md branch discipline rule #1
**Test:** `test_b03_master_only_merges_from_development`

### B04 — Pre-merge CI check for development→master
Before merging `development` into `master`, CI MUST be green on the `development` tip.
**Enforcement:** AGENTS.md "gate green on development + CI green → merge to master"
**Test:** `test_b04_pre_merge_ci_check`

### B05 — Never merge to master from inside a worktree
Merges to master MUST happen on the main checkout only.
**Enforcement:** AGENTS.md branch discipline rule #2
**Test:** `test_b05_no_merge_master_from_worktree`

### B06 — Batch-push pushes the CURRENT branch
The agent MUST verify which branch it's on before running `make batch-push`.
**Enforcement:** `verify-state` before batch-push
**Test:** `test_b06_batch_push_pushes_current_branch`

### B07 — Branch name follows naming convention
Branch names MUST follow the pattern: `feature/*`, `fix/*`, `release/*`, `agent-*`, or `development`.
**Enforcement:** AGENTS.md branch convention
**Test:** `test_b07_branch_name_convention`

### B08 — Development branch is the feature integration point
All feature branches MUST be merged into `development`, not directly to `master`.
**Enforcement:** AGENTS.md branch discipline + `development-merge-to-master`
**Test:** `test_b08_development_is_feature_integration`

### B09 — Feature branches are short-lived
Feature branches MUST be merged back into `development` within the same session.
**Enforcement:** AGENTS.md + SESSION.md tracking
**Test:** `test_b09_feature_branches_short_lived`

### B10 — Never rebase shared branches
`master` and `development` MUST never be rebased; use merge (--no-ff).
**Enforcement:** AGENTS.md branch discipline
**Test:** `test_b10_no_rebase_shared_branches`

### B11 — Verify branch before starting work
At session start, the agent MUST verify which branch it is on.
**Enforcement:** SESSION.md "PRIMARY OBJECTIVE" references branch
**Test:** `test_b11_verify_branch_before_work`

### B12 — Emergency fixes on master get backported
When a fix lands on `master` for emergency reasons, it MUST be cherry-picked to `development` immediately.
**Enforcement:** AGENTS.md "Emergency fixes on master get backported"
**Test:** `test_b12_emergency_fix_backport`

### B13 — Single-source feature development
A feature MUST land on exactly ONE branch first, then be merged/cherry-picked.
**Enforcement:** AGENTS.md "Single-Source Feature Development"
**Test:** `test_b13_single_source_feature_development`

### B14 — No parallel Makefile edits on different branches
Makefile targets MUST NOT be independently created on both `master` and `development`.
**Enforcement:** `check-duplicate-targets` + single-source rule
**Test:** `test_b14_no_parallel_makefile_edits`

### B15 — Duplicate target detection at gate time
`make check-duplicate-targets` MUST scan for targets declared more than once.
**Enforcement:** `scripts/check_duplicate_targets.py` + gate prerequisite
**Test:** `test_b15_duplicate_target_detection`

### B16 — Release branch starts from CI-green base
`make release-branch-new` MUST verify the base branch is CI-green before creating the release branch.
**Enforcement:** `scripts/check_ci_green_for_base.py` (planned)
**Test:** `test_b16_release_branch_from_ci_green_base`

### B17 — Green release branch is immutable
Once a release branch's remote tip is CI-green, no new commits may land.
**Enforcement:** `scripts/check_green_branch_guard.py` push deny
**Test:** `test_b17_green_release_branch_immutable`

### B18 — Fix-forward on red release branch
If a release branch goes red, fix on that branch directly; do not create a parallel branch to dodge the guard.
**Enforcement:** AGENTS.md "Fix-forward on the branch, not around it"
**Test:** `test_b18_fix_forward_on_red_release_branch`

### B19 — Release-promote is the only path to ship
`make release-promote TAG=<tag>` is the ONLY way to ship a release branch to master.
**Enforcement:** AGENTS.md + Makefile `release-promote` as sole promotion path
**Test:** `test_b19_release_promote_sole_ship_path`

### B20 — Release-recut re-triggers on existing tag
`make release-recut` MUST only re-trigger CI on an existing tag for CI-failure recovery.
**Enforcement:** Makefile `release-recut` target with guard
**Test:** `test_b20_release_recut_ci_failure_only`

### B21 — Never force-push past green branch guard
Force-push MUST NOT be used to bypass the green-branch guard.
**Enforcement:** AGENTS.md forbidden patterns
**Test:** `test_b21_no_force_push_past_green_guard`

### B22 — Feature-start and feature-done lifecycle enforced
Features MUST be started with `make feature-start` and completed with `make feature-done` — never manually branched.
**Enforcement:** AGENTS.md Feature Branch & Worktree Workflow
**Test:** `test_b22_feature_start_done_lifecycle`

### B23 — Worktree isolated agents have their own branch
Every worktree-isolated agent MUST have a dedicated branch named `agent-<descriptive>`.
**Enforcement:** Makefile `agent-worktree` target
**Test:** `test_b23_worktree_agents_have_own_branch`

### B24 — Agent worktree must be cleaned up after merge
After `make agent-merge`, the worktree MUST be cleaned up with `make agent-cleanup`.
**Enforcement:** AGENTS.md worktree lifecycle
**Test:** `test_b24_worktree_cleanup_after_merge`

### B25 — No more than 6 concurrent worktree agents
The orchestrator MUST NOT dispatch more than 6 worktree-isolated agents concurrently.
**Enforcement:** AGENTS.md worktree cap rule + disk-discipline
**Test:** `test_b25_max_6_concurrent_worktree_agents`

---

## Group O — Objective Tracking (O01–O30)

### O01 — PRIMARY OBJECTIVE is set at session start
SESSION.md MUST contain a `## PRIMARY OBJECTIVE:` line at session start.
**Enforcement:** `enforce-objective.ts` nag injection + text.complete check
**Test:** `test_o01_primary_objective_set_at_session_start`

### O02 — Objective is read before each tool call
The agent MUST consult the PRIMARY OBJECTIVE before making tool calls that do not advance it.
**Enforcement:** `enforce-objective.ts` tool.execute.before advisory
**Test:** `test_o02_objective_read_before_tool_call`

### O03 — Tangential tool calls get objective warning
When the PRIMARY OBJECTIVE is not yet met and a non-dispatch/non-read tool call is made, a console.warn MUST fire.
**Enforcement:** `enforce-objective.ts` console.warn on tangential tools
**Test:** `test_o03_tangential_tools_get_objective_warning`

### O04 — Dispatch and read tools always allowed
Even when the objective is unmet, dispatch (task/agent/workflow) and read (read/grep/glob) tools MUST be allowed.
**Enforcement:** `enforce-objective.ts` unconditional allow for dispatch/read tools
**Test:** `test_o04_dispatch_and_read_always_allowed`

### O05 — CI-advancing bash targets are allowed
Even when the objective is unmet, CI-advancing and test/gate bash targets MUST be allowed.
**Enforcement:** `enforce-objective.ts` bash allowlist pattern
**Test:** `test_o05_ci_advancing_bash_allowed`

### O06 — Objective-met detection for CI GREEN objectives
When the objective contains "CI GREEN", the plugin MUST check `/tmp/gludd-watchdog-ci.json` for `last_ci_status === "SUCCESS"`.
**Enforcement:** `enforce-objective.ts` isObjectiveMet implementation
**Test:** `test_o06_ci_green_objective_detection`

### O07 — CI cache staleness window (10 minutes)
CI status cache older than 600 seconds MUST be considered stale and objective NOT met.
**Enforcement:** `enforce-objective.ts` 600,000 ms stale threshold
**Test:** `test_o07_ci_cache_staleness_window`

### O08 — Non-CI objectives are treated as not-yet-met
Objective text that does not match CI-related patterns is always treated as unmet.
**Enforcement:** `enforce-objective.ts` isObjectiveMet non-CI → false
**Test:** `test_o08_non_ci_objectives_not_yet_met`

### O09 — NAG_PREFIX injected when objective is missing
When SESSION.md has no PRIMARY OBJECTIVE, the NAG_PREFIX banner MUST be injected into outgoing text.
**Enforcement:** `enforce-objective.ts` text.complete nag injection
**Test:** `test_o09_nag_prefix_injected_when_missing`

### O10 — Objective enforce disabled by env var
`GLUDD_OBJECTIVE_ENFORCE=0` MUST completely disable objective enforcement.
**Enforcement:** `enforce-objective.ts` env var check
**Test:** `test_o10_objective_enforce_disabled_by_env_var`

### O11 — Subagent guard prevents objective enforcement
`OPENCODE_SUBAGENT=1` or subagent detection MUST skip objective checks in subagents.
**Enforcement:** `enforce-objective.ts` isSubagent check at top
**Test:** `test_o11_subagent_guard_objective`

### O12 — Fail-open on any error
Any error in `enforce-objective.ts` MUST fail open — allow the tool call, do not throw.
**Enforcement:** `enforce-objective.ts` try/catch fail-open blocks
**Test:** `test_o12_objective_fail_open`

### O13 — Hot-reload capable
`enforce-objective.ts` MUST implement the proxy pattern with hot-reload.
**Enforcement:** `loadHotModule("objective", defaultImpl)` usage
**Test:** `test_o13_objective_hot_reload_capable`

### O14 — Objective met disables enforcement
When `isObjectiveMet()` returns true, all tool calls MUST pass through without warning.
**Enforcement:** `enforce-objective.ts` early return on objective met
**Test:** `test_o14_objective_met_disables_enforcement`

### O15 — Edit and write tools are warned when objective unmet
Non-read, non-dispatch, non-bash-allowed tools (edit, write) MUST receive a console.warn when objective unmet.
**Enforcement:** `enforce-objective.ts` default tool path
**Test:** `test_o15_edit_write_warned_unmet_objective`

### O16 — Objective text is discoverable
`getPrimaryObjective()` MUST be callable and return the objective text.
**Enforcement:** exported function in `enforce-objective.ts`
**Test:** `test_o16_objective_text_discoverable`

### O17 — Objective change triggers re-evaluation
If SESSION.md PRIMARY OBJECTIVE changes mid-session, the plugin MUST read the updated text on next tool call.
**Enforcement:** `enforce-objective.ts` reads file fresh each call
**Test:** `test_o17_objective_change_triggers_reevaluation`

### O18 — Objective enforcement runs on tool.execute.before
The primary enforcement hook MUST be `tool.execute.before`.
**Enforcement:** `enforce-objective.ts` plugin hook registration
**Test:** `test_o18_objective_enforcement_on_tool_execute_before`

### O19 — Objective enforcement runs on text.complete
Missing-objective nag injection MUST run on `text.complete`.
**Enforcement:** `enforce-objective.ts` text.complete hook
**Test:** `test_o19_objective_enforcement_on_text_complete`

### O20 — Objective nag is prepended, not appended
The missing-objective nag MUST be prepended to the outgoing text so it is the first thing visible.
**Enforcement:** `enforce-objective.ts` nag + text concatenation order
**Test:** `test_o20_objective_nag_prepended`

### O21 — Weekly objective review is codified
The agent MUST review and update the PRIMARY OBJECTIVE once per week or per major project phase change.
**Enforcement:** AGENTS.md objective policy
**Test:** `test_o21_weekly_objective_review_codified`

### O22 — Objective drives task prioritization
TASKS.md items MUST be prioritized relative to the PRIMARY OBJECTIVE.
**Enforcement:** TASKS.md Phase A (CI green + release) maps to objective
**Test:** `test_o22_objective_drives_task_prioritization`

### O23 — Objective completion triggers TASKS.md update
When the PRIMARY OBJECTIVE is met, TASKS.md MUST be updated to reflect the new state.
**Enforcement:** AGENTS.md completion requirements
**Test:** `test_o23_objective_completion_triggers_tasks_update`

### O24 — No objective change without explicit rationale
The PRIMARY OBJECTIVE MUST NOT change without a documented rationale in SESSION.md.
**Enforcement:** AGENTS.md objective policy
**Test:** `test_o24_no_objective_change_without_rationale`

### O25 — Objective is single-line
The PRIMARY OBJECTIVE MUST be a single line following `## PRIMARY OBJECTIVE:`.
**Enforcement:** `enforce-objective.ts` regex enforces single-line capture
**Test:** `test_o25_objective_is_single_line`

### O26 — Objective is written in ALL CAPS for keyword emphasis
The PRIMARY OBJECTIVE SHOULD use ALL CAPS for key terms (CI GREEN, MASTER).
**Enforcement:** AGENTS.md convention
**Test:** `test_o26_objective_keywords_all_caps`

### O27 — Objective is pushed to development alongside feature work
When the objective changes, the updated SESSION.md MUST be committed and pushed.
**Enforcement:** AGENTS.md session persistence
**Test:** `test_o27_objective_committed_on_change`

### O28 — Multiple objectives handled as priority list
If multiple objectives exist, they MUST be ordered by priority with the highest first.
**Enforcement:** AGENTS.md priority stacking rule
**Test:** `test_o28_multiple_objectives_priority_list`

### O29 — Objective is visible in system prompt
The PRIMARY OBJECTIVE MUST be injected into the system prompt or AGENTS.md context.
**Enforcement:** SESSION.md is read at session start (session-start protocol)
**Test:** `test_o29_objective_visible_in_context`

### O30 — Objective enforcement is always ON by default
`enforce-objective.ts` MUST default to active (ENFORCE=1) unless explicitly disabled.
**Enforcement:** `GLUDD_OBJECTIVE_ENFORCE` default = "1" (absence = enforce)
**Test:** `test_o30_objective_enforcement_default_on`

---

## Group T — Test Integrity (T01–T30)

### T01 — Never disable tests in CI
The agent MUST NOT add `skip`, `xfail`, or disable tests that were previously running in CI.
**Enforcement:** Makefile `_test-disabled-guard` pre-commit check
**Test:** `test_t01_never_disable_tests_in_ci`

### T02 — Never use continue-on-error in CI
CI steps MUST NOT use `continue-on-error: true` to mask failures.
**Enforcement:** `.github/workflows/build.yml` audit
**Test:** `test_t02_no_continue_on_error_ci`

### T03 — Test collection errors are hard failures
Zero collection errors must be confirmed before any commit.
**Enforcement:** `make collect-check` as gate prerequisite
**Test:** `test_t03_collection_errors_hard_failures`

### T04 — Test failures must be fixed, not suppressed
A failing test MUST be fixed; `# noqa`, `# type: ignore`, `pytest.skip`, and `@pytest.mark.xfail` (without strict=True) are forbidden workarounds.
**Enforcement:** `enforce-no-suppressions.ts` + TDD policy
**Test:** `test_t04_test_failures_fixed_not_suppressed`

### T05 — Coverage threshold cannot be lowered to pass
The coverage `--fail-under` threshold MUST NOT be lowered to make gate pass.
**Enforcement:** `pyproject.toml` coverage threshold + AGENTS.md
**Test:** `test_t05_coverage_threshold_not_lowered`

### T06 — TDD: test file must exist before source edit
The agent MUST write a test file before editing corresponding source in `src/general_ludd/**/*.py`.
**Enforcement:** `enforce-tdd.ts` real-time editor block
**Test:** `test_t06_tdd_test_before_source`

### T07 — TDD allowlist matches check_tdd_compliance.py
The `enforce-tdd.ts` allowlist MUST match `scripts/check_tdd_compliance.py` exactly.
**Enforcement:** `tests/unit/test_tdd_allowlist_parity.py` (planned)
**Test:** `test_t07_tdd_allowlist_parity`

### T08 — Every new source file requires a test file
No new `.py` file under `src/general_ludd/` may land without a corresponding test file in `tests/unit/`.
**Enforcement:** `scripts/check_tdd_compliance.py` commit-time backstop
**Test:** `test_t08_new_source_requires_test`

### T09 — Test count must be checked before commit
`make test-count` MUST show 0 collection errors before every commit.
**Enforcement:** AGENTS.md + gate prerequisites
**Test:** `test_t09_test_count_before_commit`

### T10 — Run specific test before claiming fix
Before claiming a bug is fixed, the agent MUST run the specific test confirming the fix.
**Enforcement:** AGENTS.md "Root-Cause-Only Fix Policy"
**Test:** `test_t10_run_specific_test_before_claiming_fix`

### T11 — Test quality requires AAA structure
Every test MUST follow the AAA (Arrange-Act-Assert) pattern.
**Enforcement:** test-quality skill + code review
**Test:** `test_t11_test_quality_aaa_structure`

### T12 — No mock-only tests
A test that mocks the entire system under test and asserts mock calls is insufficient.
**Enforcement:** test-quality skill
**Test:** `test_t12_no_mock_only_tests`

### T13 — No tests that test mocks themselves
A test MUST NOT test that a mock was called — it must assert on the system's actual behavior.
**Enforcement:** test-quality skill
**Test:** `test_t13_no_tests_that_test_mocks`

### T14 — Integration tests verify cross-subsystem behavior
Integration tests MUST exercise 2+ subsystems together.
**Enforcement:** test-quality skill + test layer audit
**Test:** `test_t14_integration_tests_cross_subsystem`

### T15 — E2E tests go through the daemon API
E2E tests MUST exercise functionality through the daemon API as a user would.
**Enforcement:** test-quality skill
**Test:** `test_t15_e2e_through_daemon_api`

### T16 — No test isolation pollution
Tests MUST NOT depend on shared mutable state (env vars, files, globals) from other tests.
**Enforcement:** test-quality skill
**Test:** `test_t16_no_test_isolation_pollution`

### T17 — Each test has one assertion concept
Each test function MUST assert on one behavioral concept.
**Enforcement:** test-quality skill
**Test:** `test_t17_one_assertion_concept_per_test`

### T18 — Test names describe behavior, not implementation
Test names MUST describe WHAT behavior is verified, not HOW it's implemented.
**Enforcement:** test-quality skill
**Test:** `test_t18_test_names_describe_behavior`

### T19 — Realistic test data
Tests MUST use realistic test data, not arbitrary strings or dummy values.
**Enforcement:** test-quality skill
**Test:** `test_t19_realistic_test_data`

### T20 — Deterministic tests
Tests MUST be deterministic — no random seeds, no time-dependent assertions, no network calls.
**Enforcement:** test-quality skill
**Test:** `test_t20_deterministic_tests`

### T21 — No coverage gaming
Tests written solely to increase coverage percentage without meaningful assertions are forbidden.
**Enforcement:** test-quality skill + coverage audit
**Test:** `test_t21_no_coverage_gaming`

### T22 — Gate must run before any status claim of "green"
The agent MUST run `make gate` or `make gate-status` before claiming tests are green.
**Enforcement:** AGENTS.md evidence-based response policy
**Test:** `test_t22_gate_before_green_claim`

### T23 — Test-run output must be cited with pass count
When claiming tests pass, the agent MUST cite the exact pass count from test output.
**Enforcement:** `enforce-verified-claims.ts` evidence requirement
**Test:** `test_t23_cite_pass_count_with_test_claim`

### T24 — Stale gate status invalidates claims
A `.gate-status` file older than the last source edit MUST NOT be used as evidence of green status.
**Enforcement:** AGENTS.md "Cited-but-STALE measurement"
**Test:** `test_t24_stale_gate_invalidates_claims`

### T25 — Background gate must report phases
When `make gate-background` runs, it MUST emit per-phase markers to the log.
**Enforcement:** Makefile gate-background phase markers
**Test:** `test_t25_background_gate_phase_markers`

### T26 — Gate failure must surface log
On gate failure, the captured log MUST be surfaced — never swallowed.
**Enforcement:** AGENTS.md "No Unseen Events" + `make gate-status-check`
**Test:** `test_t26_gate_failure_surfaces_log`

### T27 — Test watchdog kills stale tasks
The `scripts/task_watchdog.py` MUST kill tasks exceeding GLUDD_TASK_TIMEOUT_MS.
**Enforcement:** `make task-watchdog-start` + watchdog daemon
**Test:** `test_t27_task_watchdog_kills_stale`

### T28 — 5-minute max per subtask
Dispatched subagent tasks MUST complete within 5 minutes; longer tasks must be split.
**Enforcement:** `enforce-deadline.ts` detection + `task_watchdog.py` killing
**Test:** `test_t28_five_minute_max_per_subtask`

### T29 — Test files must be importable
Every test file in `tests/` MUST be importable — no syntax errors, no missing imports.
**Enforcement:** `make collect-check` as pre-commit gate
**Test:** `test_t29_test_files_importable`

### T30 — collect-check is gate prerequisite
`make collect-check` MUST be a prerequisite of `make gate` and all commit targets.
**Enforcement:** Makefile gate target prerequisites
**Test:** `test_t30_collect_check_gate_prerequisite`

---

## Group D — Dispatch Floor (D01–D30)

### D01 — Minimum 10 dispatches per wave
When pending work exists, each dispatch wave MUST contain at least 10 task/agent/workflow dispatches.
**Enforcement:** `enforce-multitask.ts` MIN_DISPATCHES=10
**Test:** `test_d01_min_10_dispatches_per_wave`

### D02 — Under-floor dispatch denied
A response with fewer than MIN_DISPATCHES dispatches while work is pending MUST be blocked.
**Enforcement:** `enforce-multitask.ts` under-floor hard block
**Test:** `test_d02_under_floor_dispatch_denied`

### D03 — Zero-dispatch streak blocked at MAX_ZERO_STREAK
After MAX_ZERO_STREAK (2) consecutive zero-dispatch responses, further non-dispatch tool calls are blocked.
**Enforcement:** `enforce-multitask.ts` zero-streak counter
**Test:** `test_d03_zero_dispatch_streak_blocked`

### D04 — Dispatch resets zero-streak counter
A dispatch wave (≥1 task/agent/workflow) MUST reset the zero-dispatch streak counter to 0.
**Enforcement:** `enforce-multitask.ts` streak reset on dispatch
**Test:** `test_d04_dispatch_resets_zero_streak`

### D05 — Read tools do not increment streak
Read/grep/glob tool calls MUST NOT increment the zero-dispatch streak counter.
**Enforcement:** `enforce-multitask.ts` isReadTool check
**Test:** `test_d05_read_tools_do_not_increment_streak`

### D06 — Consecutive non-dispatch counter
After CONSECUTIVE_NON_DISPATCH_THRESHOLD (5) non-dispatch calls within CONSECUTIVE_NON_DISPATCH_WINDOW_MS (30s), all non-dispatch tools are blocked.
**Enforcement:** `enforce-floor.ts` consecutive-non-dispatch counter
**Test:** `test_d06_consecutive_non_dispatch_counter`

### D07 — Time-based window for grinding detection
The consecutive-non-dispatch counter MUST reset if no non-dispatch calls occur within 30 seconds.
**Enforcement:** `enforce-floor.ts` time-window reset
**Test:** `test_d07_time_window_grinding_detection`

### D08 — Post-result read limit
After subagent results arrive, at most POST_RESULT_READ_LIMIT (3) reads are allowed before dispatch is required.
**Enforcement:** `enforce-floor.ts` read limit in result grace window
**Test:** `test_d08_post_result_read_limit`

### D09 — Message-shape rule: ≥2 dispatches OR zero changes
Each response with tool calls MUST have either 0 dispatches (pure read/edit/bash) or ≥2 dispatches.
**Enforcement:** AGENTS.md message-shape rule
**Test:** `test_d09_message_shape_rule`

### D10 — Single-dispatch responses are policy violation
A response with exactly 1 task/agent/workflow dispatch when ≥2 work items remain is a policy violation.
**Enforcement:** `enforce-multitask.ts` prevMessageDispatches check
**Test:** `test_d10_single_dispatch_is_violation`

### D11 — Main-thread grind threshold
4+ consecutive non-dispatch tool calls MUST trigger a MAINTHREAD_GRIND advisory.
**Enforcement:** `enforce-delegate.ts` MAINTHREAD_THRESHOLD
**Test:** `test_d11_main_thread_grind_threshold`

### D12 — Dispatch ceiling at 10
No more than 10 concurrent subagents may be dispatched.
**Enforcement:** `enforce-floor.ts` CEILING=10 + COST-EFFICIENCY DIRECTIVE
**Test:** `test_d12_dispatch_ceiling_at_10`

### D13 — Worktree agents capped at ~6
At most 5-6 worktree-isolated agents may be concurrent.
**Enforcement:** AGENTS.md worktree cap
**Test:** `test_d13_worktree_agents_capped_at_6`

### D14 — Read-only research tasks stay on main checkout
Research subagents that only read files MUST NOT be worktree-isolated.
**Enforcement:** AGENTS.md worktree isolation criteria
**Test:** `test_d14_readonly_research_stays_main_checkout`

### D15 — Commit dispatched as subagent
One of the 10 dispatch slots SHOULD run `make ship-commit` (local only, PUSH=0).
**Enforcement:** AGENTS.md steady-state dispatch rule #6
**Test:** `test_d15_commit_dispatched_as_subagent`

### D16 — Clean tree before dispatch
Before dispatching any subagent, the working tree MUST be clean (no uncommitted changes).
**Enforcement:** `enforce-clean-tree.ts` deny dispatch on dirty tree
**Test:** `test_d16_clean_tree_before_dispatch`

### D17 — Subagent isolation via git worktree
Every file-editing subagent MUST work in an isolated git worktree on its own branch.
**Enforcement:** Makefile `agent-worktree` target + worktree policy
**Test:** `test_d17_subagent_isolation_via_worktree`

### D18 — Subagent quality requirements
Every subagent MUST produce a deliverable (commit, test file, analysis) — not just a status report.
**Enforcement:** AGENTS.md "Fix, Don't Check" policy
**Test:** `test_d18_subagent_produces_deliverable`

### D19 — No status-check subagents
"Check CI status", "audit lint", "check dirty tree" subagents are forbidden.
**Enforcement:** AGENTS.md forbidden subagent task descriptions
**Test:** `test_d19_no_status_check_subagents`

### D20 — Subagent task sizing: 2-5 minutes
Each subagent task SHOULD be sized for 2-5 minutes of work.
**Enforcement:** AGENTS.md subagent sizing rule + deadline enforcement
**Test:** `test_d20_subagent_task_sizing_2_to_5_min`

### D21 — Research subagents serialized
At most 1 research/explore subagent may run at a time.
**Enforcement:** COST-EFFICIENCY DIRECTIVE rule 4
**Test:** `test_d21_research_subagents_serialized`

### D22 — Coding subagents ≤2 parallel
At most 2 file-editing subagents may run in parallel (disjoint files only).
**Enforcement:** COST-EFFICIENCY DIRECTIVE rule 4
**Test:** `test_d22_coding_subagents_max_2_parallel`

### D23 — Refill on every completion
When a subagent completes, the orchestrator MUST dispatch a replacement immediately.
**Enforcement:** AGENTS.md "Refill on every completion"
**Test:** `test_d23_refill_on_every_completion`

### D24 — Never drop subagent results
Every subagent result MUST be codified (committed or explicitly cancelled with reason).
**Enforcement:** AGENTS.md Nothing-Dropped Guardrail
**Test:** `test_d24_never_drop_subagent_results`

### D25 — Wave history tracked
The last WAVE_HISTORY_SIZE (10) dispatch wave sizes MUST be tracked.
**Enforcement:** `enforce-multitask.ts` waveHistory array
**Test:** `test_d25_wave_history_tracked`

### D26 — Dispatch wave boundary detection
Multitask plugin MUST detect message boundaries to track per-message dispatch counts.
**Enforcement:** `enforce-multitask.ts` text.complete-based boundary
**Test:** `test_d26_dispatch_wave_boundary_detection`

### D27 — Estimated in-flight counter
Plugin MUST track estimated in-flight subagent count.
**Enforcement:** `enforce-multitask.ts` estimatedInFlight
**Test:** `test_d27_estimated_in_flight_counter`

### D28 — Dispatch floor env-var overridable
GLUDD_MIN_DISPATCHES env var MUST be able to override the floor (minimum 2).
**Enforcement:** `enforce-multitask.ts` MIN_DISPATCHES computation
**Test:** `test_d28_dispatch_floor_env_var_overridable`

### D29 — Floor enforcement is default ON
The dispatch floor enforcement MUST default to ON (not advisory).
**Enforcement:** `enforce-multitask.ts` FLOOR_ENFORCE default true
**Test:** `test_d29_floor_enforcement_default_on`

### D30 — Floor enforcement disabled by env var
GLUDD_MULTITASK_FLOOR_ENFORCE=0 MUST disable all multitask enforcement.
**Enforcement:** `enforce-multitask.ts` FLOOR_ENFORCE gate
**Test:** `test_d30_floor_enforcement_disabled_by_env_var`

---

## Group S — Anti-Stop (S01–S25)

### S01 — Never text-only with pending work
The agent MUST NEVER send a text-only response while TASKS.md has unchecked items.
**Enforcement:** `enforce-stop.ts` text.complete blanking
**Test:** `test_s01_no_text_only_with_pending_work`

### S02 — Text-only response is blanked when pending work exists
When `hasRealPendingWork()` returns true, the text.complete hook MUST blank the response.
**Enforcement:** `enforce-stop.ts` hasRealPendingWork() check
**Test:** `test_s02_text_only_blanked_with_pending_work`

### S03 — Pending work includes CI state
`hasRealPendingWork()` MUST check CI status (ci-verdict), not just TASKS.md.
**Enforcement:** `enforce-stop.ts` comprehensive work detection
**Test:** `test_s03_pending_work_includes_ci_state`

### S04 — Pending work includes release completeness
`hasRealPendingWork()` MUST check release artifact completeness.
**Enforcement:** `enforce-stop.ts` verify-release-completeness check
**Test:** `test_s04_pending_work_includes_release_completeness`

### S05 — Pending work includes gate status
`hasRealPendingWork()` MUST check `.gate-status` for failures.
**Enforcement:** `enforce-stop.ts` gate-status check
**Test:** `test_s05_pending_work_includes_gate_status`

### S06 — Pending work includes ratchet entries
`hasRealPendingWork()` MUST check `config/ratchet.yml` for entries.
**Enforcement:** `enforce-stop.ts` ratchet check
**Test:** `test_s06_pending_work_includes_ratchet_entries`

### S07 — Completion words blocked without evidence
Words like "done", "landed", "pushed", "fixed", "passing" MUST carry machine-produced evidence.
**Enforcement:** `enforce-verified-claims.ts` done-words evidence requirement
**Test:** `test_s07_completion_words_blocked_without_evidence`

### S08 — Status summaries during session-start are blocked
After backlog reads and before first dispatch wave, a status summary response is blanked.
**Enforcement:** `enforce-stop.ts` STATUS_SUMMARY_RE + session-start window
**Test:** `test_s08_status_summaries_blocked_session_start`

### S09 — Q&A summaries blocked without tool call
A Q&A response like "Here's what was done" without a tool call is blanked.
**Enforcement:** `enforce-stop.ts` QA_RESPONSE_PATTERNS
**Test:** `test_s09_qa_summaries_blocked_without_tool_call`

### S10 — Never ask "Shall I continue?" or "Ready for review?"
Questions that imply waiting for user permission to continue work are forbidden.
**Enforcement:** AGENTS.md anti-stop patterns
**Test:** `test_s10_never_ask_permission_to_continue`

### S11 — Stop-signal word detection
`enforce-stop.ts` MUST detect a comprehensive list of stop-signal phrases.
**Enforcement:** `enforce-stop.ts` STOP_SIGNAL_WORDS
**Test:** `test_s11_stop_signal_word_detection`

### S12 — Heuristic stop-pattern detection
Beyond words, `enforce-stop.ts` MUST detect structural patterns: bold-summary, commit+table, test-count summary.
**Enforcement:** `enforce-stop.ts` heuristic checks
**Test:** `test_s12_heuristic_stop_pattern_detection`

### S13 — Interleaved summary detection
Completion-style status summaries interleaved with tool calls MUST still be detected and blanked.
**Enforcement:** `enforce-stop.ts` interleaved-summary detection
**Test:** `test_s13_interleaved_summary_detection`

### S14 — Evidence present does not exempt stop patterns
A completion summary carrying a commit hash is STILL a premature stop if pending work exists.
**Enforcement:** `enforce-stop.ts` evidence-exempt removed from summary detection
**Test:** `test_s14_evidence_present_does_not_exempt`

### S15 — False-done claim detection
The string "All done", "Everything is complete", "All tasks finished" MUST trigger blocking.
**Enforcement:** `enforce-stop.ts` false-completion patterns
**Test:** `test_s15_false_done_claim_detection`

### S16 — Stop guardrail is fail-open
Any error in `enforce-stop.ts` MUST fail open — allow the response through.
**Enforcement:** `enforce-stop.ts` try/catch fail-open
**Test:** `test_s16_stop_guardrail_fail_open`

### S17 — Stop enforcement is never disabled by disengage for real pending work
Disengage signal (`/tmp/gludd-watchdog-disengage`) MUST NOT bypass the `hasRealPendingWork()` text-only block.
**Enforcement:** `enforce-stop.ts` disengage only skips heuristics
**Test:** `test_s17_disengage_does_not_disable_real_pending_work`

### S18 — Subagents do not stop-enforce
`enforce-stop.ts` MUST skip enforcement entirely for subagent output.
**Enforcement:** isSubagent() guard at top of hooks
**Test:** `test_s18_subagents_do_not_stop_enforce`

### S19 — Stop guard is hot-reload capable
`enforce-stop.ts` MUST implement the proxy pattern.
**Enforcement:** loadHotModule("stop", ...) usage
**Test:** `test_s19_stop_guard_hot_reload_capable`

### S20 — Session-start stop audit
At session start, the agent MUST read BUGS.md and audit the previous session for premature stops.
**Enforcement:** AGENTS.md session start protocol
**Test:** `test_s20_session_start_stop_audit`

### S21 — BUGS.md incident tracking mandatory
Every premature stop incident MUST be logged in BUGS.md with date, what stopped, root cause, fix.
**Enforcement:** AGENTS.md Premature-Stop Audit Policy
**Test:** `test_s21_bugs_md_incident_tracking`

### S22 — Never present analysis report instead of continuing
An analysis of why something failed MUST NOT replace the action of fixing it.
**Enforcement:** AGENTS.md "Q&A Response Pattern" + enforce-stop.ts
**Test:** `test_s22_no_analysis_instead_of_continuing`

### S23 — Stop guard blocks at text.complete surface
The anti-stop enforcement MUST fire at the `text.complete` hook surface.
**Enforcement:** `enforce-stop.ts` text.complete hook
**Test:** `test_s23_stop_guard_fires_at_text_complete`

### S24 — Stop guard also blocks via tool.execute.before
`enforce-stop.ts` MUST also have a `tool.execute.before` hook for commit blocking.
**Enforcement:** `enforce-stop.ts` tool.execute.before commit gate
**Test:** `test_s24_stop_guard_also_blocks_tool_execute`

### S25 — No write in system prompt from stop plugin
`enforce-stop.ts` MUST NOT use `system.transform` for injecting stop directives — use text.complete instead.
**Enforcement:** plugin hook registration audit
**Test:** `test_s25_stop_plugin_no_system_transform`

---

## Group E — Anti-Essay (E01–E20)

### E01 — Do not write explanations when work remains
When pending work exists, the agent MUST NOT generate prose explanations of what happened or why.
**Enforcement:** AGENTS.md anti-essay guard + enforce-stop.ts (planned extension)
**Test:** `test_e01_no_explanations_with_pending_work`

### E02 — Bolded section headers in final responses blocked
A response ending with bolded section headers (What changed, Why, What's left) while work remains MUST be blocked.
**Enforcement:** `enforce-stop.ts` bolded-header structural detection
**Test:** `test_e02_bolded_headers_blocked_in_final_response`

### E03 — Essay-length responses detected and blocked
Responses exceeding a floor-word-count threshold while carrying no tool calls MUST be flagged.
**Enforcement:** `enforce-anti-essay.ts` (planned) word-count heuristic
**Test:** `test_e03_essay_length_responses_blocked`

### E04 — Commitment-to-action ratio
Each agent response MUST have at least as many tool calls as prose paragraphs.
**Enforcement:** AGENTS.md mechanical contract rule #2 — produce tool calls not prose
**Test:** `test_e04_commitment_to_action_ratio`

### E05 — Status tables in text responses forbidden
A markdown table (| columns |) in a text response while pending work exists MUST be treated as a stop pattern.
**Enforcement:** `enforce-stop.ts` markdown table detection
**Test:** `test_e05_status_tables_forbidden`

### E06 — Bullet lists of completed work without next action blocked
A text-only response with N+ bullet points listing completed work and no tool call MUST be blocked.
**Enforcement:** `enforce-stop.ts` bullet-list heuristic
**Test:** `test_e06_bullet_list_completed_work_blocked`

### E07 — "Summary" pattern detected regardless of phrasing
Any response containing "summary", "recap", "what we did", "status report" while pending work exists MUST be blocked.
**Enforcement:** `enforce-stop.ts` summary-word detection
**Test:** `test_e07_summary_pattern_always_detected`

### E08 — Post-commit prose block
After a commit lands, the agent MUST NOT generate a prose description of the commit — must continue to next task.
**Enforcement:** AGENTS.md anti-stop patterns
**Test:** `test_e08_post_commit_prose_block`

### E09 — Never write "Here's what changed"
The phrase "Here's what changed" or equivalent when work remains MUST be blanked.
**Enforcement:** `enforce-stop.ts` status-summary pattern
**Test:** `test_e09_heres_what_changed_blocked`

### E10 — Explaining the root cause is not fixing it
Writing an analysis of a bug's root cause without the fix code MUST NOT be considered progress.
**Enforcement:** AGENTS.md Root-Cause-Only Fix Policy
**Test:** `test_e10_explaining_root_cause_is_not_fixing`

### E11 — Anti-essay guard is plugin-enforced
A dedicated `enforce-anti-essay.ts` plugin MUST detect and block essay patterns.
**Enforcement:** `enforce-anti-essay.ts` (planned new plugin)
**Test:** `test_e11_anti_essay_guard_plugin`

### E12 — Adaptive word-count threshold
The anti-essay word-count threshold MUST adapt based on whether the response contains tool calls.
**Enforcement:** `enforce-anti-essay.ts` (planned)
**Test:** `test_e12_adaptive_word_count_threshold`

### E13 — No-metadata prose detection
Responses containing 0 commit hashes, 0 test counts, 0 CI verdicts and >50 words MUST be flagged.
**Enforcement:** `enforce-anti-essay.ts` (planned) metadata-absence heuristic
**Test:** `test_e13_no_metadata_prose_detection`

### E14 — Image/emoji-heavy prose blocked
Responses that use emojis or decorative formatting to pad a text-only response MUST be flagged.
**Enforcement:** `enforce-stop.ts` (planned extension)
**Test:** `test_e14_decorative_formatting_blocked`

### E15 — "Let me explain" patterns blocked
Phrases like "Let me explain", "Here's why", "To understand this" at the start of a response while work pending MUST be flagged.
**Enforcement:** `enforce-stop.ts` (planned extension)
**Test:** `test_e15_let_me_explain_blocked`

### E16 — Response length limit when gate is red
When `.gate-status` is FAILED, text responses MUST NOT exceed a short error message.
**Enforcement:** `enforce-stop.ts` gate-red text clamp (planned)
**Test:** `test_e16_response_length_limit_when_gate_red`

### E17 — Prose-to-code ratio enforced
Over a session, the ratio of prose output to code output MUST be tracked and warned when > 1:1.
**Enforcement:** `enforce-anti-essay.ts` (planned) ratio tracking
**Test:** `test_e17_prose_to_code_ratio_enforced`

### E18 — No open-ended planning prose
"Do you want me to...", "I could approach this by...", "One option is..." — these MUST be blocked when work exists.
**Enforcement:** AGENTS.md "Never Block on Questions"
**Test:** `test_e18_no_open_ended_planning_prose`

### E19 — Concrete action required in every response
Every response with pending work MUST include at least one concrete, specific action (not a plan, not an option).
**Enforcement:** `enforce-stop.ts` tool-call presence check
**Test:** `test_e19_concrete_action_required_in_every_response`

### E20 — Anti-essay enforcement is default ON
The anti-essay guard MUST be enabled by default and only disabled via explicit env var.
**Enforcement:** `enforce-anti-essay.ts` (planned)
**Test:** `test_e20_anti_essay_enforcement_default_on`

---

## Group M — Merge Safety (M01–M20)

### M01 — Never merge without conflict resolution
When a merge has conflicts, the agent MUST resolve each conflict individually — never use `-X theirs`.
**Enforcement:** AGENTS.md merge safety policy
**Test:** `test_m01_no_merge_without_conflict_resolution`

### M02 — Merge uses --no-ff
All merges to shared branches MUST use `--no-ff` to preserve branch topology.
**Enforcement:** Makefile `git-merge` target forces --no-ff
**Test:** `test_m02_merge_uses_no_ff`

### M03 — Pre-merge gate must be green
Before merging any branch into a shared branch, the gate MUST be green on the source branch.
**Enforcement:** Makefile merge targets check gate
**Test:** `test_m03_pre_merge_gate_green`

### M04 — Merge is atomic
A merge MUST commit all resolved files in a single merge commit — partial merges are forbidden.
**Enforcement:** Makefile merge targets
**Test:** `test_m04_merge_is_atomic`

### M05 — Agent-merge runs on main checkout
`make agent-merge` MUST be run from the main checkout, never from inside a worktree.
**Enforcement:** AGENTS.md worktree lifecycle rule #2
**Test:** `test_m05_agent_merge_runs_main_checkout`

### M06 — Worktree agent NEVER pushes or merges
Subagents running inside worktrees MUST NOT push to remote or merge to shared branches.
**Enforcement:** AGENTS.md worktree rules
**Test:** `test_m06_worktree_agent_never_pushes_or_merges`

### M07 — Agent-merge uses --no-ff
`make agent-merge` MUST use `--no-ff` to preserve the agent's branch history.
**Enforcement:** Makefile `agent-merge` target
**Test:** `test_m07_agent_merge_uses_no_ff`

### M08 — Merge conflict abort available
`make git-merge-abort` MUST be available to cleanly abort a conflicted merge.
**Enforcement:** Makefile `git-merge-abort` target
**Test:** `test_m08_merge_conflict_abort_available`

### M09 — Gated-merge target enforces multi-condition merge
`make gated-merge` MUST verify preconditions before merging (gate green, CI green, tree clean).
**Enforcement:** Makefile `gated-merge` target
**Test:** `test_m09_gated_merge_enforces_preconditions`

### M10 — Development-merge-to-master requires CI green
`make development-merge-to-master` MUST gate on CI green before ff-merge.
**Enforcement:** Makefile `development-merge-to-master` CI check
**Test:** `test_m10_development_merge_to_master_requires_ci_green`

### M11 — Feature-done merges with --no-ff only
`make feature-done` MUST merge the feature branch into master with `--no-ff`.
**Enforcement:** Makefile `feature-done` target
**Test:** `test_m11_feature_done_merges_no_ff`

### M12 — Git-locking works for merge operations
Merge operations MUST acquire the git_repo_lock before proceeding.
**Enforcement:** `src/general_ludd/git_automation/locking.py` — lock acquisition
**Test:** `test_m12_git_locking_for_merge_operations`

### M13 — Worktree git-lock bug is known and documented
The worktree `.git`-as-file locking gap MUST be documented as a known issue.
**Enforcement:** AGENTS.md "KNOWN GAP: git locking is broken inside worktrees"
**Test:** `test_m13_worktree_lock_bug_documented`

### M14 — No concurrent merge operations
Two merges MUST NOT run concurrently — the second MUST wait or fail.
**Enforcement:** git_repo_lock serialization
**Test:** `test_m14_no_concurrent_merge_operations`

### M15 — Merge target branch verified before proceeding
Before executing a merge, the target branch MUST be verified as the correct destination.
**Enforcement:** AGENTS.md merge discipline
**Test:** `test_m15_merge_target_branch_verified`

### M16 — Merge commit message follows convention
Merge commit messages MUST follow the pattern `Merge branch '<source>' into <target>`.
**Enforcement:** AGENTS.md commit conventions
**Test:** `test_m16_merge_commit_message_convention`

### M17 — Ship-async gates on green before ff-merge
`make ship-async REF=<hash>` MUST verify gate green before ff-only merge.
**Enforcement:** Makefile `ship-async` target
**Test:** `test_m17_ship_async_gates_on_green`

### M18 — Development-status shows merge readiness
`make development-status` MUST show commits on development not yet on master.
**Enforcement:** Makefile `development-status` target
**Test:** `test_m18_development_status_shows_merge_readiness`

### M19 — Release-promote is ff-only merge into master
`make release-promote TAG=<tag>` MUST use ff-only merge to advance master.
**Enforcement:** Makefile `release-promote` target
**Test:** `test_m19_release_promote_ff_only_merge`

### M20 — Backport merge follows cherry-pick discipline
When backporting a fix from master, cherry-pick MUST preserve the original commit message.
**Enforcement:** AGENTS.md backport rule
**Test:** `test_m20_backport_preserves_commit_message`

---

## Group G — Gate Discipline (G01–G20)

### G01 — Gate must pass before any commit
Every commit-shaped target MUST verify `.gate-status` is PASS and fresh.
**Enforcement:** Makefile `_gate-fresh-check` prerequisite
**Test:** `test_g01_gate_must_pass_before_commit`

### G02 — Gate-fresh-check is fail-closed
If `.gate-status` is missing or unreadable, `_gate-fresh-check` MUST fail (exit non-zero).
**Enforcement:** Makefile `_gate-fresh-check` logic
**Test:** `test_g02_gate_fresh_check_fail_closed`

### G03 — Gate must be re-run after source changes
If `.gate-status` is older than the last source file modification, it MUST be considered stale.
**Enforcement:** `_gate-fresh-check` mtime comparison
**Test:** `test_g03_gate_must_be_rerun_after_changes`

### G04 — No gate bypass via commit-no-verify
`make commit-no-verify` MUST still enforce the gate check — it only skips pre-commit hooks, not the gate.
**Enforcement:** Makefile `commit-no-verify` includes `_gate-fresh-check`
**Test:** `test_g04_no_gate_bypass_commit_no_verify`

### G05 — Gate includes lint check
`make gate` MUST include lint (ruff) as a prerequisite.
**Enforcement:** Makefile `gate` target prerequisites
**Test:** `test_g05_gate_includes_lint`

### G06 — Gate includes typecheck
`make gate` MUST include typecheck (mypy) as a prerequisite.
**Enforcement:** Makefile `gate` target prerequisites
**Test:** `test_g06_gate_includes_typecheck`

### G07 — Gate includes collect-check
`make gate` MUST include `make collect-check` as a prerequisite.
**Enforcement:** Makefile `gate` target prerequisites
**Test:** `test_g07_gate_includes_collect_check`

### G08 — Gate includes test suite
`make gate` MUST run the test suite as part of its execution.
**Enforcement:** Makefile `gate` target executes tests
**Test:** `test_g08_gate_includes_test_suite`

### G09 — Gate writes .gate-status
`make gate` MUST write a PASS/FAIL marker to `.gate-status`.
**Enforcement:** Makefile `gate` target writes `.gate-status`
**Test:** `test_g09_gate_writes_gate_status`

### G10 — Gate-phase markers are emitted
`make gate` MUST emit `=== GATE PHASE: <name> ===` markers for observability.
**Enforcement:** Makefile `gate` target phase markers
**Test:** `test_g10_gate_phase_markers_emitted`

### G11 — Gate-background is non-blocking
`make gate-background` MUST return in <1 second after launching the background gate.
**Enforcement:** Makefile `gate-background` uses nohup
**Test:** `test_g11_gate_background_non_blocking`

### G12 — Gate-status-check is read-only
`make gate-status-check` MUST not modify any files or state.
**Enforcement:** Makefile `gate-status-check` target
**Test:** `test_g12_gate_status_check_read_only`

### G13 — Gate-logs preserve history
`make gate-logs` MUST list all past gate log files with timestamps.
**Enforcement:** Makefile `gate-logs` target
**Test:** `test_g13_gate_logs_preserve_history`

### G14 — Gate-kill terminates cleanly
`make gate-kill` MUST send SIGTERM then SIGKILL after 5s to the background gate.
**Enforcement:** Makefile `gate-kill` target
**Test:** `test_g14_gate_kill_terminates_cleanly`

### G15 — Gate is never run on main thread
`make gate` on the main thread is blocked by `enforce-make.ts`.
**Enforcement:** `enforce-make.ts` long-op foreground deny
**Test:** `test_g15_gate_never_on_main_thread`

### G16 — Gate-lite is available for fast feedback
`make gate-lite` MUST provide fast local validation (no OOM risk) for between-commit feedback.
**Enforcement:** Makefile `gate-lite` target
**Test:** `test_g16_gate_lite_available`

### G17 — Gate-audit includes coverage
`make gate-audit` MUST verify per-file coverage thresholds.
**Enforcement:** Makefile `gate-audit` target + audit-coverage
**Test:** `test_g17_gate_audit_includes_coverage`

### G18 — Gate-refresh is available for stale gates
`make gate-refresh` MUST re-validate `.gate-status` without re-running the full gate.
**Enforcement:** Makefile `gate-refresh` target
**Test:** `test_g18_gate_refresh_available`

### G19 — Gate-status shows PASS/FAIL/RUNNING
`make gate-status` MUST display the current gate state (PASS/FAIL/RUNNING/STALE).
**Enforcement:** Makefile `gate-status` target
**Test:** `test_g19_gate_status_shows_state`

### G20 — Background gate PID is tracked
`make gate-background` MUST write the background PID to `.gate-background.pid`.
**Enforcement:** Makefile `gate-background` PID tracking
**Test:** `test_g20_background_gate_pid_tracked`

---

## Group R — Release Discipline (R01–R20)

### R01 — release-cut is the only sanctioned release command
`make release-cut TAG=... MSG=...` is the ONLY way to create and publish a release.
**Enforcement:** AGENTS.md + Makefile release-cut as sole release path
**Test:** `test_r01_release_cut_sole_sanctioned_command`

### R02 — release-cut steps run in order
Step 0 (require-ci-green), step 1 (verify-remote), step 2 (push), step 3 (tag-push), step 4 (verify-release-completeness) MUST run in strict sequence.
**Enforcement:** Makefile `release-cut` sequential steps
**Test:** `test_r02_release_cut_steps_in_order`

### R03 — release-cut aborts on any step failure
If any step of `release-cut` fails, subsequent steps MUST NOT run.
**Enforcement:** Makefile `release-cut` AND-chained prerequisites
**Test:** `test_r03_release_cut_aborts_on_failure`

### R04 — CI green required before release tag push
`scripts/require_ci_green.py` MUST be run against HEAD and exit 0 before tagging.
**Enforcement:** `make require-ci-green` as release-cut step 0
**Test:** `test_r04_ci_green_before_tag_push`

### R05 — Require-ci-green is fail-closed
No matching run found must exit RED (failure), not default to green.
**Enforcement:** `scripts/require_ci_green.py` fail-closed logic
**Test:** `test_r05_require_ci_green_fail_closed`

### R06 — Verify-release-completeness checks 12 categories
`make verify-release-completeness` MUST check all 12 required artifact categories.
**Enforcement:** `scripts/verify_release_completeness.py` 12-category check
**Test:** `test_r06_verify_release_completeness_12_categories`

### R07 — A version is not shipped without artifacts
A tag in the repo with zero assets = NOT shipped. `verify-release-completeness` must confirm.
**Enforcement:** AGENTS.md "A Release is an Artifact, Not a Tag"
**Test:** `test_r07_version_not_shipped_without_artifacts`

### R08 — Never bump version while current version lacks artifacts
Moving to a new version while `verify-release-completeness` fails for the current version is forbidden.
**Enforcement:** AGENTS.md release policy rule #2
**Test:** `test_r08_no_version_bump_without_current_artifacts`

### R09 — Release task completion requires artifact URL
TASKS.md release items MUST carry the artifact URL, CI run id, and conclusion as evidence.
**Enforcement:** AGENTS.md release completion evidence requirements
**Test:** `test_r09_release_completion_requires_artifact_url`

### R10 — Readme status table is current before release
`scripts/check_readme_status_current.py` MUST verify README.md status line matches the release version.
**Enforcement:** `make check-readme-status` as release-cut gate
**Test:** `test_r10_readme_status_current_before_release`

### R11 — Release notes update CHANGELOG.md
Every release MUST have a corresponding entry in CHANGELOG.md.
**Enforcement:** AGENTS.md release process
**Test:** `test_r11_release_notes_update_changelog`

### R12 — Tag is annotated
Release tags MUST be annotated (`git tag -a`) with a message, never lightweight.
**Enforcement:** Makefile `git-tag-push` uses annotated tags
**Test:** `test_r12_tag_is_annotated`

### R13 — Tag push triggers CI
Pushing a `v*` tag MUST trigger the Build-and-Release CI workflow.
**Enforcement:** `.github/workflows/build.yml` tag trigger
**Test:** `test_r13_tag_push_triggers_ci`

### R14 — Release job needs gate in CI
CI workflow `release` job MUST `needs: [gate]` (transitively) so broken code cannot publish.
**Enforcement:** `.github/workflows/build.yml` needs dependency
**Test:** `test_r14_release_job_needs_gate`

### R15 — release-create is draft-only fallback
`make release-create` MUST publish as draft only; it is a CI-green-gated fallback, not the primary release path.
**Enforcement:** AGENTS.md + Makefile `release-create` draft-only
**Test:** `test_r15_release_create_draft_only_fallback`

### R16 — release-recut re-triggers on existing tag
`make release-recut TAG=<tag>` MUST delete and re-push a tag to re-trigger CI for failed-build recovery.
**Enforcement:** Makefile `release-recut` tag delete + re-push
**Test:** `test_r16_release_recut_re_triggers_ci`

### R17 — Release cut from master only
`make release-cut` MUST only operate when the current branch is `master` (or the target is explicitly set).
**Enforcement:** AGENTS.md + branch check
**Test:** `test_r17_release_cut_from_master_only`

### R18 — Verify-release-artifact is NOT the gate
`make verify-release-artifact` only proves "non-draft + ≥1 asset" — it MUST NOT be used as the completeness check.
**Enforcement:** AGENTS.md v0.1.0-beta.1 correction
**Test:** `test_r18_verify_artifact_not_the_gate`

### R19 — Asset categories are documented
The 12 required artifact categories for a release MUST be documented in `docs/RELEASE_RUNBOOK.md`.
**Enforcement:** `docs/RELEASE_RUNBOOK.md` (planned verification)
**Test:** `test_r19_asset_categories_documented`

### R20 — Release pipeline failure is observable
If the release CI job fails, the failure MUST surface in observable form (CI log, notification, dashboard).
**Enforcement:** AGENTS.md + CI workflow notification
**Test:** `test_r20_release_pipeline_failure_observable`

---

## Group W — Worktree Discipline (W01–W30)

### W01 — Every file-editing subagent works in an isolated worktree
File-editing subagents MUST operate in a per-agent git worktree, never on the shared main checkout.
**Enforcement:** Makefile `agent-worktree` target + AGENTS.md worktree-per-subagent policy
**Test:** `test_w01_isolated_worktree_per_subagent`

### W02 — Read-only research stays on main checkout
Subagents that only read files (research, audit, survey) MUST NOT be worktree-isolated.
**Enforcement:** AGENTS.md worktree isolation criteria
**Test:** `test_w02_readonly_research_on_main_checkout`

### W03 — One branch per worktree agent
Each worktree-isolated agent MUST have a unique branch name following the `agent-<descriptive>` pattern.
**Enforcement:** Makefile `agent-worktree` creates unique branch
**Test:** `test_w03_one_branch_per_worktree`

### W04 — Agent-merge runs from main checkout only
`make agent-merge` MUST be executed from the main checkout, never from within a worktree.
**Enforcement:** AGENTS.md worktree lifecycle rule
**Test:** `test_w04_agent_merge_main_checkout_only`

### W05 — Agent-cleanup after every merge
After `make agent-merge` completes, `make agent-cleanup` MUST be run to remove the worktree directory and branch.
**Enforcement:** AGENTS.md worktree lifecycle
**Test:** `test_w05_agent_cleanup_after_merge`

### W06 — Worktree agent never pushes to remote
Subagents inside worktrees MUST NOT push commits to the remote or merge to shared branches.
**Enforcement:** AGENTS.md worktree rules — subagent responsibilities
**Test:** `test_w06_worktree_agent_no_push_or_merge`

### W07 — Shared-branch mutations on main checkout only
Mutations to shared branches (master, development, release/*) MUST originate from the main checkout only.
**Enforcement:** AGENTS.md branch-landing integrity rule (a)
**Test:** `test_w07_shared_branch_mutations_main_checkout`

### W08 — Worktree git-lock bug is documented
The `.git`-as-file lock gap in worktrees MUST be documented and tracked as a known bug.
**Enforcement:** AGENTS.md "KNOWN GAP: git locking is broken inside worktrees"
**Test:** `test_w08_worktree_lock_bug_documented`

### W09 — No concurrent merge/tag/push from worktrees
Two subagents MUST NOT concurrently run merge, tag, or push operations against the repo.
**Enforcement:** AGENTS.md worktree lock caveat + orchestrator serialization
**Test:** `test_w09_no_concurrent_merge_tag_push_worktree`

### W10 — Max 6 concurrent worktree agents
The orchestrator MUST NOT dispatch more than 6 worktree-isolated agents at once (ENOSPC guard).
**Enforcement:** AGENTS.md worktree cap + disk discipline
**Test:** `test_w10_max_6_concurrent_worktree_agents`

### W11 — Worktree disk space monitored
`make agent-worktree-list` MUST show disk usage per worktree; agent MUST check before dispatching.
**Enforcement:** Makefile `agent-worktree-list` with sizes
**Test:** `test_w11_worktree_disk_space_monitored`

### W12 — Stale worktrees cleaned up
Worktrees older than 24 hours with no active agent MUST be cleaned by `make agent-cleanup --all-stale`.
**Enforcement:** AGENTS.md worktree lifecycle + `agent-cleanup` stale detection
**Test:** `test_w12_stale_worktrees_cleaned`

### W13 — Worktree branch naming is descriptive
Worktree branches MUST use descriptive names (`agent-fix-slurm`, NOT `agent-1` or `agent-tmp`).
**Enforcement:** AGENTS.md naming convention
**Test:** `test_w13_worktree_branch_naming_descriptive`

### W14 — Agent-worktree re-attaches to existing branch
Running `make agent-worktree BRANCH=<existing>` MUST attach a fresh worktree to the existing branch (resume).
**Enforcement:** Makefile `agent-worktree` re-attach logic
**Test:** `test_w14_worktree_reattach_existing_branch`

### W15 — Worktree isolation prevents shared-tree races
Two worktree agents editing the same file on different branches MUST NOT conflict on disk.
**Enforcement:** git worktree isolation (structure)
**Test:** `test_w15_worktree_isolation_prevents_races`

### W16 — Agent-worktree-dev for development branch worktrees
`make agent-worktree-dev BRANCH=<name>` MUST create worktrees from `development` for feature work.
**Enforcement:** Makefile `agent-worktree-dev` target
**Test:** `test_w16_worktree_dev_for_development_branch`

### W17 — Agent-merge-dev merges into development
`make agent-merge-dev` MUST merge a worktree branch into `development`, not `master`.
**Enforcement:** Makefile `agent-merge-dev` target
**Test:** `test_w17_agent_merge_dev_into_development`

### W18 — Worktree creation fails gracefully on disk full
When disk is >90%, `make agent-worktree` MUST emit a clear error, not silently fail.
**Enforcement:** `scripts/check_disk_usage.py` pre-worktree disk check
**Test:** `test_w18_worktree_creation_fails_gracefully_disk_full`

### W19 — Agent-worktree-list enumerates all active worktrees
`make agent-worktree-list` MUST list every active worktree with branch name and path.
**Enforcement:** Makefile `agent-worktree-list` target
**Test:** `test_w19_worktree_list_enumerates_all_active`

### W20 — Clean-worktree-venvs reclaims venv disk
After worktree agents complete, `make clean-worktree-venvs` MUST remove orphaned `.venv` dirs in worktrees.
**Enforcement:** Makefile `clean-worktree-venvs` target
**Test:** `test_w20_clean_worktree_venvs_reclaims_disk`

### W21 — No orphaned worktree directories
After `make agent-cleanup`, no directory at `/tmp/gludd-worktrees/<branch>` MUST remain.
**Enforcement:** Makefile `agent-cleanup` removes directory + `git worktree prune`
**Test:** `test_w21_no_orphaned_worktree_directories`

### W22 — Worktree agent commits local only
Subagents inside worktrees MUST commit locally but NEVER push — push is orchestrator's job.
**Enforcement:** AGENTS.md worktree rules
**Test:** `test_w22_worktree_agent_commits_local_only`

### W23 — Worktree agent receives full task context
When dispatching a worktree agent, the orchestrator MUST provide the `WORKTREE_PATH` as cwd.
**Enforcement:** AGENTS.md worktree lifecycle step 1
**Test:** `test_w23_worktree_agent_receives_full_context`

### W24 — Agent-merge preserves --no-ff topology
`make agent-merge` MUST use `--no-ff` to preserve the agent branch history in the merge commit.
**Enforcement:** Makefile `agent-merge` uses `--no-ff`
**Test:** `test_w24_agent_merge_preserves_no_ff_topology`

### W25 — Worktree creation verifies base branch exists
`make agent-worktree` MUST verify the base branch (master) exists before creating the worktree.
**Enforcement:** Makefile `agent-worktree` prerequisite check
**Test:** `test_w25_worktree_creation_verifies_base_branch`

### W26 — No git worktree prune during active work
`git worktree prune` MUST NOT run while worktree agents are active (can corrupt active sessions).
**Enforcement:** AGENTS.md + Makefile guard
**Test:** `test_w26_no_prune_during_active_worktree`

### W27 — Agent-worktree records creation timestamp
Each worktree creation MUST record a timestamp for staleness detection.
**Enforcement:** Makefile `agent-worktree` timestamp recording
**Test:** `test_w27_worktree_records_creation_timestamp`

### W28 — Worktree subagent cannot spawn its own subagents
Subagents in worktrees MUST NOT dispatch further subagents (nesting guard).
**Enforcement:** AGENTS.md worktree rules — subagent constraints
**Test:** `test_w28_worktree_subagent_no_nesting`

### W29 — Worktree cleanup is idempotent
Running `make agent-cleanup` twice on the same branch MUST succeed (no error on already-cleaned).
**Enforcement:** Makefile `agent-cleanup` idempotent
**Test:** `test_w29_worktree_cleanup_idempotent`

### W30 — Worktree agent failure triggers cleanup
When a worktree subagent fails or times out, the orchestrator MUST still run `make agent-cleanup` to reclaim resources.
**Enforcement:** AGENTS.md worktree lifecycle — cleanup on failure
**Test:** `test_w30_worktree_failure_triggers_cleanup`

---

## Group F — CI Discipline (F01–F30)

### F01 — CI push triggers exactly one CI run per batch
Each `make batch-push` MUST trigger at most one CI run — the batch, not individual commits.
**Enforcement:** `_push-rate-guard` + batch-push threshold
**Test:** `test_f01_ci_push_triggers_one_batch_run`

### F02 — Never push while CI is in_progress on target branch
Push MUST be blocked when any CI run for the target branch has status `in_progress`.
**Enforcement:** `scripts/ci_busy_check.py` + `_push-rate-guard`
**Test:** `test_f02_no_push_while_ci_in_progress`

### F03 — Push-to-push cooldown enforced
A second push within 120 seconds of the prior push to the same branch MUST be hard-denied.
**Enforcement:** `_push-rate-guard` push-cooldown (120s)
**Test:** `test_f03_push_cooldown_enforced`

### F04 — Cancelled-run thrash detection blocks push
When >3 cancelled CI runs exist in the last 2 hours on a branch, push MUST be blocked.
**Enforcement:** `_push-rate-guard` thrash detection
**Test:** `test_f04_cancelled_run_thrash_detection`

### F05 — Push rate guard is fail-closed
If CI state cannot be determined (gh unavailable, network down), push MUST be denied.
**Enforcement:** `scripts/ci_push_guard.py` fail-closed
**Test:** `test_f05_push_rate_guard_fail_closed`

### F06 — Verify-remote after every push
After any push, `make verify-remote BRANCH=<b> SHA=<sha>` MUST confirm the remote tip matches.
**Enforcement:** AGENTS.md "Verify the remote after every push"
**Test:** `test_f06_verify_remote_after_every_push`

### F07 — Verify-remote failure blocks subsequent work
If `verify-remote` exits non-zero, the agent MUST NOT proceed as if the push succeeded.
**Enforcement:** AGENTS.md + Makefile push targets
**Test:** `test_f07_verify_remote_failure_blocks_work`

### F08 — Never claim CI green without matching headSha
A CI verdict whose `headSha != branch tip` MUST NOT be reported as definitive.
**Enforcement:** `make ci-verdict` stale-run warning
**Test:** `test_f08_no_ci_claim_without_matching_headsha`

### F09 — CI-verdict-safe cooldown is machine-enforced
`make ci-verdict-safe` MUST respect a 10-minute cooldown between CI checks.
**Enforcement:** `scripts/ci_check_cooldown.py`
**Test:** `test_f09_ci_verdict_safe_cooldown`

### F10 — CI-COOLDOWN status is never reported as CI-PENDING
When cooldown blocks a check, the output MUST say CI-COOLDOWN, not CI-PENDING.
**Enforcement:** `scripts/ci_check_cooldown.py` output labeling
**Test:** `test_f10_cooldown_not_reported_as_pending`

### F11 — FORCE=1 bypass reserved for release-cut
The `FORCE=1` flag on CI checks MUST only be used during `make release-cut`, never for routine polling.
**Enforcement:** AGENTS.md CI-Poll Subagents rule
**Test:** `test_f11_force_bypass_release_cut_only`

### F12 — CI-wait reserved for release-cut pipeline
`make ci-wait` MUST NOT be used outside of the `make release-cut` pipeline.
**Enforcement:** AGENTS.md sub-rule #6
**Test:** `test_f12_ci_wait_release_cut_only`

### F13 — Never dispatch CI-poll subagent
A subagent whose sole task is "poll CI until terminal" MUST NOT be dispatched.
**Enforcement:** `enforce-no-wait.ts` CI_POLL_DISPATCH_PATTERNS
**Test:** `test_f13_no_ci_poll_subagent`

### F14 — Deploy-and-forget is the fire-and-forget push pattern
When pushing for CI validation (not release), the agent MUST use `make deploy-and-forget`.
**Enforcement:** Makefile `deploy-and-forget` target
**Test:** `test_f14_deploy_and_forget_pattern`

### F15 — Deploy-and-forget records push timestamp
`make deploy-and-forget` MUST record the push timestamp for cooldown calculation.
**Enforcement:** `scripts/ci_check_cooldown.py` timestamp recording
**Test:** `test_f15_deploy_and_forget_records_timestamp`

### F16 — CI check at natural breaks only
CI status MUST be checked at natural work breaks (subagent result ingestion), not continuously.
**Enforcement:** AGENTS.md "CI is checked at natural breaks, not polled"
**Test:** `test_f16_ci_check_at_natural_breaks`

### F17 — Never poll CI from main thread
`make ci-verdict` or `make ci-wait` MUST NOT run on the main thread.
**Enforcement:** `enforce-no-wait.ts` main-thread denial
**Test:** `test_f17_no_poll_ci_from_main_thread`

### F18 — CI-failed-tests surfaces per-test failures
`make ci-failed-tests RUN=<id>` MUST extract exact failing test names from CI logs.
**Enforcement:** Makefile `ci-failed-tests` target using `gh run view --log`
**Test:** `test_f18_ci_failed_tests_surfaces_failures`

### F19 — CI-faillog is not truncated beyond usefulness
`make ci-faillog` MUST not truncate log data so aggressively that real failures are hidden.
**Enforcement:** AGENTS.md known trap + Makefile guard
**Test:** `test_f19_ci_faillog_not_overly_truncated`

### F20 — CI verdict cached with TTL
CI verdict results MUST be cached with a configurable TTL (default 600s) to prevent polling abuse.
**Enforcement:** `scripts/ci_verdict_cache.py` (planned)
**Test:** `test_f20_ci_verdict_cached_with_ttl`

### F21 — CI status check does not block local gate
The local `make gate` MUST NOT be deferred waiting for CI — local validation is independent.
**Enforcement:** AGENTS.md "Prefer local validation" rule
**Test:** `test_f21_ci_status_does_not_block_local_gate`

### F22 — CI-green is pre-requisite for development→master merge
`make development-merge-to-master` MUST gate on CI green for the `development` branch tip.
**Enforcement:** Makefile `development-merge-to-master` CI check
**Test:** `test_f22_ci_green_required_development_merge`

### F23 — CI run URL included in any CI claim
When claiming CI status, the agent MUST include the CI run URL alongside the verdict.
**Enforcement:** AGENTS.md evidence-based response policy
**Test:** `test_f23_ci_run_url_included_in_claim`

### F24 — CI workflow never uses continue-on-error to mask failures
CI steps MUST NOT use `continue-on-error: true` as a substitute for fixing failing steps.
**Enforcement:** AGENTS.md CI workflow audit + `.github/workflows/build.yml`
**Test:** `test_f24_no_continue_on_error_in_ci`

### F25 — CI timeout per job is reasonable
CI jobs MUST have explicit timeouts (max 45 min per job) to prevent hung workflows.
**Enforcement:** AGENTS.md + `.github/workflows/build.yml` timeout-minutes
**Test:** `test_f25_ci_job_timeout_reasonable`

### F26 — CI artifact retention is configured
Published CI artifacts (SBOM, binaries) MUST have a retention period set (default 90 days).
**Enforcement:** AGENTS.md + `.github/workflows/build.yml` retention-days
**Test:** `test_f26_ci_artifact_retention_configured`

### F27 — CI badge reflects current state
README.md CI badge MUST link to the correct workflow and reflect the current branch status.
**Enforcement:** AGENTS.md evidence + README audit
**Test:** `test_f27_ci_badge_reflects_current_state`

### F28 — CI is never used as the primary gate
Local gate (`make gate`) is the primary validation; CI is secondary confirmation — not the other way around.
**Enforcement:** AGENTS.md "Prefer local validation" rule
**Test:** `test_f28_ci_not_primary_gate`

### F29 — CI push history is auditable
Every CI-triggering push MUST be logged with timestamp, SHA, and branch in a state file.
**Enforcement:** `scripts/ci_push_guard.py` state recording
**Test:** `test_f29_ci_push_history_auditable`

### F30 — No push gate circumvention possible
No make target or env var combination MUST allow bypassing the CI push guard entirely.
**Enforcement:** Makefile all push paths through `_push-rate-guard`
**Test:** `test_f30_no_push_gate_circumvention`

---

## Group C — Commit Discipline (C01–C30)

### C01 — Every commit-shaped target enforces gate-fresh-check
All targets invoking `git commit` MUST run `_gate-fresh-check` as a prerequisite.
**Enforcement:** Makefile `_gate-fresh-check` on all commit targets
**Test:** `test_c01_all_commit_targets_enforce_gate_check`

### C02 — Gate-fresh-check is fail-closed
If `.gate-status` is missing, unreadable, or >30s older than last source edit, commit is blocked.
**Enforcement:** Makefile `_gate-fresh-check` mtime comparison
**Test:** `test_c02_gate_fresh_check_fail_closed`

### C03 — Atomic commits: one logical change per commit
Each commit MUST represent exactly one logical change (one feature, one fix, one test file).
**Enforcement:** AGENTS.md atomic commits rule
**Test:** `test_c03_atomic_commits_one_logical_change`

### C04 — Commit-no-verify still enforces gate
`make commit-no-verify` MUST still run `_gate-fresh-check` — it only skips pre-commit hooks.
**Enforcement:** Makefile `commit-no-verify` includes gate check
**Test:** `test_c04_commit_no_verify_still_gate_checked`

### C05 — Repo-commit is escape hatch for meta-commits only
`make repo-commit` MUST only be used for version bumps, release artifacts, docs — never for code.
**Enforcement:** AGENTS.md "repo-commit is the ONLY documented escape hatch"
**Test:** `test_c05_repo_commit_meta_only`

### C06 — Commit message follows conventional format
Commit messages MUST follow `type: description` format (feat:, fix:, chore:, test:, docs:, refactor:).
**Enforcement:** AGENTS.md commit conventions
**Test:** `test_c06_commit_message_follows_convention`

### C07 — Pre-existing failures are never an excuse to bypass gate
"Gate was already red" is NEVER a valid reason to commit without gate green.
**Enforcement:** AGENTS.md no-commit-bypass policy rule #4
**Test:** `test_c07_no_bypass_for_preexisting_failures`

### C08 — Environmental issues are never an excuse to bypass gate
"Credentials expired" or "network down" is NEVER a valid reason to commit without gate green.
**Enforcement:** AGENTS.md no-commit-bypass policy rule #5
**Test:** `test_c08_no_bypass_for_environmental_issues`

### C09 — Commits contain only intended files
The agent MUST review `make git-staged` before every commit to ensure only intended files are staged.
**Enforcement:** AGENTS.md + `make git-staged` usage
**Test:** `test_c09_commits_contain_only_intended_files`

### C10 — Never commit secrets or credentials
Commits MUST NOT contain secrets, API keys, tokens, or credentials.
**Enforcement:** `make secrets-scan` pre-commit hook + `.secrets.baseline`
**Test:** `test_c10_never_commit_secrets`

### C11 — Secrets baseline is updated after scrub
After a `make secrets-scrub`, `.secrets.baseline` MUST be updated and the change committed separately.
**Enforcement:** `make secrets-baseline` + pre-commit hook
**Test:** `test_c11_secrets_baseline_updated_after_scrub`

### C12 — Lint must pass before commit
`make lint` MUST return 0 errors before any commit with source changes can land.
**Enforcement:** Makefile pre-commit hook via `make install-hooks`
**Test:** `test_c12_lint_passes_before_commit`

### C13 — Typecheck baseline must not regress
New type errors introduced in a commit MUST NOT exceed the mypy baseline.
**Enforcement:** `make typecheck` + mypy baseline enforcement
**Test:** `test_c13_typecheck_baseline_no_regression`

### C14 — No suppression comments in committed code
Commits MUST NOT contain `# noqa`, `# type: ignore`, `# pylint: disable`, `# fmt: off`, `# isort:skip`.
**Enforcement:** `enforce-no-suppressions.ts` + `scripts/check_tdd_compliance.py`
**Test:** `test_c14_no_suppression_comments_in_commits`

### C15 — TDD: test file exists for every new source file
No new `.py` file under `src/general_ludd/` may be committed without a corresponding test file.
**Enforcement:** `scripts/check_tdd_compliance.py` commit-time backstop
**Test:** `test_c15_tdd_test_exists_for_new_source`

### C16 — Collect-check passes before every commit
`make collect-check` MUST show 0 collection errors before any commit with test changes.
**Enforcement:** Makefile pre-commit hook + `make collect-check`
**Test:** `test_c16_collect_check_before_commit`

### C17 — Never commit generated files
Generated files (`__pycache__/`, `.pyc`, `.egg-info/`, dist outputs) MUST NOT be committed.
**Enforcement:** `.gitignore` + pre-commit check
**Test:** `test_c17_never_commit_generated_files`

### C18 — Commit author is preserved
Commits MUST preserve the correct author attribution — never rebase-rewrite authorship.
**Enforcement:** AGENTS.md git conventions
**Test:** `test_c18_commit_author_preserved`

### C19 — Git hooks are installed and active
`make install-hooks` MUST have been run; pre-commit hooks MUST be active before any commit.
**Enforcement:** `make install-hooks` + pre-commit config
**Test:** `test_c19_git_hooks_installed_and_active`

### C20 — No amend commits to shared branches
`git commit --amend` MUST NOT be used on commits already pushed to shared branches.
**Enforcement:** AGENTS.md branch discipline
**Test:** `test_c20_no_amend_shared_branch_commits`

### C21 — Commit count verified before push
Before pushing, the agent MUST verify the number of unpushed commits with `make git-log`.
**Enforcement:** `make batch-push` threshold check
**Test:** `test_c21_commit_count_verified_before_push`

### C22 — Ship-commit defaults to PUSH=0
`make ship-commit` MUST default to local-only commit (PUSH=0) — explicit PUSH=1 required for push.
**Enforcement:** Makefile `ship-commit` PUSH default
**Test:** `test_c22_ship_commit_defaults_local_only`

### C23 — Large commits are reviewed before pushing
Commits changing >500 lines across >10 files MUST be flagged for review before pushing.
**Enforcement:** AGENTS.md + pre-push hook (planned)
**Test:** `test_c23_large_commits_reviewed_before_push`

### C24 — Every commit message references context
Commit messages SHOULD reference the spec, task, or bug being addressed (e.g., "Fixes P01").
**Enforcement:** AGENTS.md commit conventions
**Test:** `test_c24_commit_message_references_context`

### C25 — No empty commits
Empty commits (no changes) MUST NOT be created as placeholders or "checkpoint" commits.
**Enforcement:** AGENTS.md + `--allow-empty` is never used
**Test:** `test_c25_no_empty_commits`

### C26 — Commit after every completed logical unit
After each test passes for a logical change, the agent MUST commit before starting new work.
**Enforcement:** AGENTS.md Commit-After-Green Policy
**Test:** `test_c26_commit_after_every_logical_unit`

### C27 — Working tree clean before starting new work
Uncommitted changes MUST be committed or stashed before the agent starts new, unrelated work.
**Enforcement:** `enforce-clean-tree.ts` + AGENTS.md
**Test:** `test_c27_clean_tree_before_new_work`

### C28 — Stale gate invalidates commit readiness
A `.gate-status` older than the last source file modification MUST block commit.
**Enforcement:** `_gate-fresh-check` stale detection
**Test:** `test_c28_stale_gate_invalidates_commit`

### C29 — Never force-commit to bypass hooks
`--no-verify`, `-n`, `--allow-empty`, or `--force` flags on commit MUST NOT be used to bypass policy.
**Enforcement:** AGENTS.md no-commit-bypass policy
**Test:** `test_c29_no_force_commit_bypass`

### C30 — Git-commit-file used for single-file commits
`make git-commit-file FILE=<path> MSG=<msg>` MUST be available for atomic single-file commits.
**Enforcement:** Makefile `git-commit-file` target
**Test:** `test_c30_git_commit_file_for_single_file`

---

## Group Q — Quality Gate (Q01–Q30)

### Q01 — Gate includes all required phases
`make gate` MUST include: lint, typecheck, collect-check, unit tests, and coverage report.
**Enforcement:** Makefile `gate` target prerequisites
**Test:** `test_q01_gate_includes_all_required_phases`

### Q02 — Each gate phase emits a phase marker
`make gate` MUST emit `=== GATE PHASE: <name> ===` at the start of each phase.
**Enforcement:** Makefile `gate` target phase markers
**Test:** `test_q02_gate_phase_markers_emitted`

### Q03 — Gate writes PASS or FAIL to .gate-status
`make gate` MUST write exactly `PASS` or `FAIL` to `.gate-status` on completion.
**Enforcement:** Makefile `gate` target `.gate-status` output
**Test:** `test_q03_gate_writes_pass_or_fail`

### Q04 — Gate-status reports RUNNING while gate is active
While a background gate is active, `make gate-status` MUST return RUNNING, not STALE or UNKNOWN.
**Enforcement:** Makefile `gate-status` PID check
**Test:** `test_q04_gate_status_reports_running`

### Q05 — Gate-background is non-blocking
`make gate-background` MUST return in <1 second, launching the gate via nohup in the background.
**Enforcement:** Makefile `gate-background` uses nohup
**Test:** `test_q05_gate_background_non_blocking`

### Q06 — Gate-status-check is read-only
`make gate-status-check` MUST not modify any files or state — only report current gate status.
**Enforcement:** Makefile `gate-status-check` read-only
**Test:** `test_q06_gate_status_check_read_only`

### Q07 — Gate-failure surfaces the log
On gate failure, `make gate-status-check` MUST show relevant log output to diagnose the failure.
**Enforcement:** Makefile `gate-status-check` log tail on failure
**Test:** `test_q07_gate_failure_surfaces_log`

### Q08 — Gate-lite is available for fast feedback
`make gate-lite` MUST provide a fast (<5 min) validation suitable for between-commit feedback.
**Enforcement:** Makefile `gate-lite` target
**Test:** `test_q08_gate_lite_fast_feedback`

### Q09 — Gate-lite does not replace full gate for commits
`make gate-lite` MUST NOT be accepted as a substitute for the full `make gate` at commit time.
**Enforcement:** AGENTS.md gate hierarchy
**Test:** `test_q09_gate_lite_not_commit_gate`

### Q10 — Gate-refresh re-validates without full re-run
`make gate-refresh` MUST verify `.gate-status` is still valid (no source changes since last gate).
**Enforcement:** Makefile `gate-refresh` target
**Test:** `test_q10_gate_refresh_validates_without_rerun`

### Q11 — Gate-audit includes coverage threshold check
`make gate-audit` MUST enforce per-file coverage thresholds (default 85% per file).
**Enforcement:** Makefile `gate-audit` + `audit-coverage`
**Test:** `test_q11_gate_audit_coverage_threshold`

### Q12 — Coverage threshold cannot be lowered to pass
The coverage `--fail-under` threshold in `pyproject.toml` MUST NOT be lowered to make gate pass.
**Enforcement:** AGENTS.md + `pyproject.toml` threshold enforcement
**Test:** `test_q12_coverage_threshold_not_lowered`

### Q13 — Dead-code check in gate audit
`make gate-audit` MUST detect dead code (classes/functions imported only in tests, never in src/).
**Enforcement:** `scripts/check_dead_code.py` (planned) + gate-audit
**Test:** `test_q13_dead_code_check_in_gate_audit`

### Q14 — Hook runtime tests run in gate
`make test-hook-runtime` MUST be a gate prerequisite for any changes to `.opencode/plugin/`.
**Enforcement:** Makefile gate prerequisites conditional on plugin changes
**Test:** `test_q14_hook_runtime_tests_in_gate`

### Q15 — Node-v26 compat check in gate
`make check-node-v26-compat` MUST pass as part of `make gate` when plugin files changed.
**Enforcement:** Makefile gate includes `check-node-v26-compat`
**Test:** `test_q15_node_v26_compat_in_gate`

### Q16 — Duplicate target check in gate
`make check-duplicate-targets` MUST pass as part of `make gate` to prevent Makefile target conflicts.
**Enforcement:** Makefile gate includes `check-duplicate-targets`
**Test:** `test_q16_duplicate_target_check_in_gate`

### Q17 — Secrets scan in gate audit
`make secrets-scan` MUST be part of `make gate-audit` to detect committed secrets.
**Enforcement:** Makefile `gate-audit` includes secrets-scan
**Test:** `test_q17_secrets_scan_in_gate_audit`

### Q18 — Gate-kill terminates background gate cleanly
`make gate-kill` MUST send SIGTERM then SIGKILL after 5s for the background gate process.
**Enforcement:** Makefile `gate-kill` two-phase kill
**Test:** `test_q18_gate_kill_terminates_cleanly`

### Q19 — Gate-logs preserves history with timestamps
`make gate-logs` MUST list all past gate log files with mtime, size, and PASS/FAIL status.
**Enforcement:** Makefile `gate-logs` directory listing
**Test:** `test_q19_gate_logs_preserves_history`

### Q20 — Gate-background PID tracked in .gate-background.pid
`make gate-background` MUST write the background process PID for status checks and kill.
**Enforcement:** Makefile `gate-background` PID file
**Test:** `test_q20_background_gate_pid_tracked`

### Q21 — Gate never run on main thread
`make gate` on the main thread is BLOCKED by `enforce-make.ts` — must use `gate-background`.
**Enforcement:** `enforce-make.ts` long-op foreground deny
**Test:** `test_q21_gate_never_on_main_thread`

### Q22 — Gate-wait-report polls until terminal
`make gate-wait-report` MUST poll the background gate status and return the final result.
**Enforcement:** Makefile `gate-wait-report` target
**Test:** `test_q22_gate_wait_report_polls_to_terminal`

### Q23 — Gate-tail streams live gate output
`make gate-tail` MUST provide live log tailing for the running gate (Ctrl-C to stop).
**Enforcement:** Makefile `gate-tail` target
**Test:** `test_q23_gate_tail_streams_live_output`

### Q24 — Multiple concurrent gates prevented
Only one instance of `make gate` (foreground or background) MUST be allowed at a time.
**Enforcement:** Makefile `gate` concurrency lock + `enforce-make.ts`
**Test:** `test_q24_no_concurrent_gates`

### Q25 — Gate output is always observable
Gate output MUST be `tee`d to both stdout and log file — never redirected to file-only.
**Enforcement:** AGENTS.md "No Unseen Events" + Makefile gate tee
**Test:** `test_q25_gate_output_always_observable`

### Q26 — Gate phase timings are reported
Each gate phase MUST report elapsed time for performance tracking.
**Enforcement:** Makefile `gate` phase timing with `time`
**Test:** `test_q26_gate_phase_timings_reported`

### Q27 — Gate-async is alias for gate-background
`make gate-async` MUST be a deprecated alias for `make gate-background`.
**Enforcement:** Makefile `gate-async` forwarding target
**Test:** `test_q27_gate_async_alias_for_background`

### Q28 — Gate must not be skipped via env var
No env var (`SKIP_GATE=1`, `GATE=0`, etc.) MUST allow entirely skipping the gate for commits.
**Enforcement:** Makefile gate prerequisites — no env-var bypass
**Test:** `test_q28_gate_not_skippable_via_env`

### Q29 — Gate-required label on all commit targets
Every commit target in the Makefile MUST have a comment noting it requires `_gate-fresh-check`.
**Enforcement:** Makefile audit via `tests/unit/test_commit_gate_freshness.py`
**Test:** `test_q29_gate_required_label_on_commit_targets`

### Q30 — Gate status is the single source of truth
Neither SESSION.md claims, agent memory, nor CI status override `.gate-status` — the file IS the truth.
**Enforcement:** AGENTS.md "Trust gate output, not SESSION.md"
**Test:** `test_q30_gate_status_single_source_of_truth`

---

## Group X — Subagent Discipline (X01–X30)

### X01 — Every subagent produces a concrete deliverable
A subagent whose only output is a status report or problem list is a FAILED task.
**Enforcement:** AGENTS.md "Fix, Don't Check" rule #1
**Test:** `test_x01_subagent_produces_concrete_deliverable`

### X02 — "Check CI status" subagents are forbidden
Dispatching a subagent whose task is "check if CI is green" MUST be blocked at dispatch time.
**Enforcement:** `enforce-no-wait.ts` CI_POLL_DISPATCH_PATTERNS
**Test:** `test_x02_check_ci_status_subagent_forbidden`

### X03 — "Audit lint" subagents are forbidden
Dispatching a subagent to "run lint and report errors" without fixing them MUST be blocked.
**Enforcement:** AGENTS.md forbidden subagent task descriptions
**Test:** `test_x03_audit_lint_subagent_forbidden`

### X04 — "Check dirty tree" subagents are forbidden
Dispatching a subagent to check git status is forbidden — the orchestrator runs it inline.
**Enforcement:** AGENTS.md forbidden subagent table
**Test:** `test_x04_check_dirty_tree_subagent_forbidden`

### X05 — Every subagent prompt ends with "Fix, don't just report"
Subagent dispatch prompts MUST include "Do NOT just report problems. Fix them." as a suffix.
**Enforcement:** AGENTS.md rule #5 — mechanical prompt suffix
**Test:** `test_x05_fix_dont_report_in_subagent_prompt`

### X06 — Subagent task sizing: 2-5 minutes
Each subagent task MUST be sized for 2-5 minutes of meaningful work — not shorter, not longer.
**Enforcement:** AGENTS.md subagent sizing rule + deadline enforcement
**Test:** `test_x06_subagent_task_2_to_5_minutes`

### X07 — Subagent max timeout is 5 minutes
Subagent tasks exceeding 5 minutes MUST be killed by `scripts/task_watchdog.py`.
**Enforcement:** `enforce-deadline.ts` + `task_watchdog.py`
**Test:** `test_x07_subagent_max_timeout_5_minutes`

### X08 — Subagent steps limited
Each subagent MUST complete within a reasonable step budget — runaway step counts trigger cleanup.
**Enforcement:** AGENTS.md + deadline enforcement
**Test:** `test_x08_subagent_steps_limited`

### X09 — Subagents use correct model
Dispatching subagents MUST use the correct model parameter (sonnet for heavy work, haiku for simple reads).
**Enforcement:** AGENTS.md model utilization policy
**Test:** `test_x09_subagents_use_correct_model`

### X10 — Subagent context is minimal
Subagent prompts MUST specify "return ≤N bullet points" or "return ≤N lines" to limit context waste.
**Enforcement:** AGENTS.md COST-EFFICIENCY DIRECTIVE rule 3
**Test:** `test_x10_subagent_context_minimal`

### X11 — Subagent reads files but returns terse summary
Subagents MUST read necessary files but return ONLY terse summaries (≤5 bullet points, ≤10 lines).
**Enforcement:** COST-EFFICIENCY DIRECTIVE rule 3
**Test:** `test_x11_subagent_terse_summary`

### X12 — File-editing subagents are file-disjoint
Two file-editing subagents in the same wave MUST edit disjoint files to prevent merge conflicts.
**Enforcement:** AGENTS.md pipeline orchestration rule
**Test:** `test_x12_file_editing_subagents_disjoint`

### X13 — Max 2 coding subagents in parallel
At most 2 file-editing (coding) subagents MAY run concurrently, and only on disjoint files.
**Enforcement:** AGENTS.md COST-EFFICIENCY DIRECTIVE rule 4
**Test:** `test_x13_max_2_coding_subagents_parallel`

### X14 — Research subagents are serialized
At most 1 research/explore subagent may run at a time to prevent collision on same files.
**Enforcement:** COST-EFFICIENCY DIRECTIVE rule 4
**Test:** `test_x14_research_subagents_serialized`

### X15 — Read-only tools only for research subagents
Research subagents MUST NOT use write/edit/bash-mutating tools — read, grep, glob, bash-readonly only.
**Enforcement:** AGENTS.md read-only research criteria
**Test:** `test_x15_readonly_tools_for_research_subagents`

### X16 — Subagent bash tool is make-only
Subagents MUST know that bash tool can ONLY run `make <target>` commands — no raw commands.
**Enforcement:** AGENTS.md subagent bash constraint
**Test:** `test_x16_subagent_bash_make_only`

### X17 — Subagent prompt states tool availability
Every subagent dispatch prompt MUST explicitly list available tools (bash, write, edit, read, glob, grep).
**Enforcement:** AGENTS.md COST-EFFICIENCY DIRECTIVE rule 11
**Test:** `test_x17_subagent_prompt_states_tools`

### X18 — Subagent uses grep/glob/read tools correctly
Subagent prompts MUST explain grep parameters: `path`=directory, `include`=file pattern, `pattern`=regex.
**Enforcement:** COST-EFFICIENCY DIRECTIVE rule 13
**Test:** `test_x18_subagent_grep_tool_context`

### X19 — Never re-dispatch completed work
Before dispatching a subagent, check: has this exact task (file + objective) already been completed?
**Enforcement:** AGENTS.md deduplication rule
**Test:** `test_x19_never_redispatch_completed_work`

### X20 — Subagent result is codified immediately
When a subagent result arrives, it MUST be codified (committed or ticked in TASKS.md) before the next dispatch wave.
**Enforcement:** AGENTS.md Nothing-Dropped Guardrail
**Test:** `test_x20_subagent_result_codified_immediately`

### X21 — Never dispatch subagent for single-file read
A single-file read or single grep MUST be done inline — never dispatched to a subagent.
**Enforcement:** COST-EFFICIENCY DIRECTIVE rule 7
**Test:** `test_x21_no_subagent_for_single_file_read`

### X22 — Subagent slots are not wasted on read-only checks
A subagent slot filled with a status-check task is a slot stolen from real work.
**Enforcement:** AGENTS.md "Subagent slots are precious" rule
**Test:** `test_x22_subagent_slots_not_wasted_on_checks`

### X23 — Subagent result is read and actioned
When a subagent result arrives, the orchestrator MUST read it and take action — never ignore.
**Enforcement:** AGENTS.md refill-on-completion rule
**Test:** `test_x23_subagent_result_read_and_actioned`

### X24 — Refill subagent immediately on completion
When a subagent completes or fails, a replacement MUST be dispatched in the next response.
**Enforcement:** AGENTS.md "Refill on every completion"
**Test:** `test_x24_refill_subagent_on_completion`

### X25 — Wave dispatch is all at once
All subagents in a wave MUST be dispatched in ONE message — never serial one-at-a-time dispatches.
**Enforcement:** AGENTS.md message-shape rule — ≥2 dispatches per message
**Test:** `test_x25_wave_dispatch_all_at_once`

### X26 — Subagent prompt length ≤20 lines
Each subagent dispatch prompt MUST be ≤20 lines to minimize overhead.
**Enforcement:** AGENTS.md COST-EFFICIENCY DIRECTIVE rule 2
**Test:** `test_x26_subagent_prompt_length_under_20_lines`

### X27 — Subagent directive: "do NOT dump large file contents"
Subagent prompts MUST include: "Do NOT dump large file contents into your response."
**Enforcement:** COST-EFFICIENCY DIRECTIVE rule 3
**Test:** `test_x27_no_large_file_dumps_in_subagent_response`

### X28 — Subagent task has a unique ID in TASKS.md
Before dispatch, each subagent task MUST be recorded with a unique ID in TASKS.md.
**Enforcement:** AGENTS.md Task Self-Tracking rule #1
**Test:** `test_x28_subagent_task_id_in_tasks_md`

### X29 — Subagent status tracked: dispatched → running → completed/failed
TASKS.md MUST track subagent lifecycle: `| dispatched`, `| running`, `| completed:<hash>`, `| failed:<reason>`.
**Enforcement:** AGENTS.md task ledger lifecycle tracking
**Test:** `test_x29_subagent_status_tracked_in_tasks_md`

### X30 — Failed subagent task is re-dispatched with backoff
When a subagent fails (not completes partial), the task is re-dispatched with exponential backoff (max 3 retries).
**Enforcement:** AGENTS.md Agent At-Rest policy table
**Test:** `test_x30_failed_subagent_redispatched_with_backoff`

---

## Group A — Audit Discipline (A01–A30)

### A01 — Self-audit on completion of any significant work
After completing any significant body of work, the agent MUST run the full self-audit checklist.
**Enforcement:** AGENTS.md Self-Audit Policy
**Test:** `test_a01_self_audit_on_completion`

### A02 — Conversation history audit is step 1
Self-audit step 1 MUST be querying the opencode conversation database for all user messages.
**Enforcement:** AGENTS.md Self-Audit checklist step 1
**Test:** `test_a02_conversation_history_audit_first`

### A03 — Cross-reference user requests against implementation
Each user message's explicit requests MUST be cross-referenced against code in `src/` and tests in `tests/`.
**Enforcement:** AGENTS.md Self-Audit checklist
**Test:** `test_a03_cross_reference_requests_vs_implementation`

### A04 — Dead-code audit required
Every new class/module MUST be searched in the entire `src/` tree for usage imports outside tests.
**Enforcement:** AGENTS.md Self-Audit step 2 (Dead Code Audit)
**Test:** `test_a04_dead_code_audit_required`

### A05 — Wiring audit for new schema fields
Every new schema/model field MUST be traced: daemon → event_loop → worker → response.
**Enforcement:** AGENTS.md Self-Audit step 3 (Wiring Audit)
**Test:** `test_a05_wiring_audit_for_new_fields`

### A06 — Migration audit for new models
Every new SQLAlchemy model/column MUST have a corresponding Alembic migration file.
**Enforcement:** AGENTS.md Self-Audit step 4 (Migration Audit)
**Test:** `test_a06_migration_audit_for_new_models`

### A07 — Test level audit (unit + integration + e2e)
Tests MUST exist at all three levels: unit, integration, E2E — for every new feature.
**Enforcement:** AGENTS.md Self-Audit step 5 (Test Level Audit)
**Test:** `test_a07_test_level_audit_completeness`

### A08 — Gap audit for new features
Each feature area MUST be checked: daemon endpoint, CLI, TUI, logging, secrets, config.
**Enforcement:** AGENTS.md Self-Audit step 6 (Gap Audit)
**Test:** `test_a08_gap_audit_for_new_features`

### A09 — Cross-interface completeness audit
If added to CLI, MUST also be in TUI. If added to daemon API, MUST have CLI + TUI.
**Enforcement:** AGENTS.md Self-Audit step 7 (Cross-Interface)
**Test:** `test_a09_cross_interface_completeness_audit`

### A10 — Evidence after audit: run tests and cite pass count
After completing the self-audit, `make test` MUST be run and the pass count cited.
**Enforcement:** AGENTS.md Self-Audit step 8 (Evidence)
**Test:** `test_a10_evidence_after_audit`

### A11 — Verification before status claim
Before ANY status claim of "done", `make verify-state` output MUST be pasted in the same response.
**Enforcement:** `enforce-verified-claims.ts` + AGENTS.md
**Test:** `test_a11_verify_state_before_status_claim`

### A12 — Commit hash as evidence for "committed"
Saying "committed" in a response MUST be accompanied by the commit hash from `make git-log`.
**Enforcement:** `enforce-verified-claims.ts` evidence requirement
**Test:** `test_a12_commit_hash_evidence_for_committed`

### A13 — Test pass count as evidence for "tests pass"
Saying "tests pass" MUST include the exact pass count from the test runner output.
**Enforcement:** `enforce-verified-claims.ts` evidence requirement
**Test:** `test_a13_pass_count_evidence_for_tests_pass`

### A14 — CI run ID as evidence for "CI green"
Saying "CI green" MUST include the CI run ID and conclusion from `make ci-verdict`.
**Enforcement:** `enforce-verified-claims.ts` evidence requirement
**Test:** `test_a14_ci_run_id_evidence_for_ci_green`

### A15 — Artifact URL as evidence for "shipped"
Saying "shipped" or "released" MUST include the artifact download URL from `gh release view`.
**Enforcement:** AGENTS.md "Done Claims Require Observable Verification Evidence"
**Test:** `test_a15_artifact_url_evidence_for_shipped`

### A16 — Audit findings are fixed, not listed
An audit that produces a gap list without fixing the gaps is NOT a completed audit.
**Enforcement:** AGENTS.md "Fix, Don't Check" + Root-Cause-Only Fix Policy
**Test:** `test_a16_audit_findings_fixed_not_listed`

### A17 — Each audit category has a make target
Dead-code check, wiring audit, migration audit, test-level audit — each MUST have a dedicated make target.
**Enforcement:** Makefile audit targets
**Test:** `test_a17_audit_category_has_make_target`

### A18 — Audit results are logged with timestamp
Every audit run MUST log its results with a timestamp to `.gate-logs/audit-<ts>.log`.
**Enforcement:** Makefile audit targets write logs
**Test:** `test_a18_audit_results_logged_with_timestamp`

### A19 — No unverified claims in commit messages
Commit messages MUST NOT contain "fixed X", "completed Y" without the corresponding test output.
**Enforcement:** AGENTS.md evidence policy extended to commit messages
**Test:** `test_a19_no_unverified_claims_in_commit_messages`

### A20 — PR descriptions carry verification evidence
GitHub PR descriptions MUST include: test pass count, lint status, typecheck status, gate status.
**Enforcement:** AGENTS.md evidence policy
**Test:** `test_a20_pr_descriptions_carry_verification`

### A21 — Audit runs on the main checkout
Audit operations MUST run on the main checkout — not from within worktrees.
**Enforcement:** AGENTS.md audit location constraint
**Test:** `test_a21_audit_runs_on_main_checkout`

### A22 — Audit frequency is tied to commit volume
An audit MUST run after every 10 source-file commits or every major feature completion.
**Enforcement:** AGENTS.md audit cadence
**Test:** `test_a22_audit_frequency_tied_to_commit_volume`

### A23 — BUGS.md updated on every incident
Every policy violation, enforcement gap, or behavioral flaw discovered MUST be logged in BUGS.md.
**Enforcement:** AGENTS.md Premature-Stop Audit Policy + BUGS.md tracking
**Test:** `test_a23_bugs_md_updated_every_incident`

### A24 — BUGS.md entries include root cause and fix
Each BUGS.md entry MUST contain: date, incident description, root cause, and the fix applied.
**Enforcement:** AGENTS.md BUGS.md incident tracking format
**Test:** `test_a24_bugs_entry_includes_root_cause_and_fix`

### A25 — SESSION.md audit at session start
At session start, the agent MUST audit the previous session's SESSION.md for premature stops.
**Enforcement:** AGENTS.md session start protocol
**Test:** `test_a25_session_audit_at_session_start`

### A26 — Ratchet entries are reviewed at session start
`config/ratchet.yml` MUST be read and its entries reviewed at every session start.
**Enforcement:** AGENTS.md session start protocol step 1
**Test:** `test_a26_ratchet_entries_reviewed_session_start`

### A27 — Gate status audited before any "done" claim
Before marking work complete, the agent MUST run `make gate-status` and confirm PASS.
**Enforcement:** AGENTS.md completion requirements
**Test:** `test_a27_gate_status_audited_before_done_claim`

### A28 — No claim made from memory alone
The agent MUST NOT claim test pass, CI green, or commit landed from memory — tool output required.
**Enforcement:** AGENTS.md "Trust gate output, not SESSION.md"
**Test:** `test_a28_no_claim_from_memory_alone`

### A29 — All dispatched tasks are tracked and closed
At session end, every dispatched task in TASKS.md MUST be either completed, cancelled, or explicitly blocked.
**Enforcement:** AGENTS.md Task Self-Tracking rule #3
**Test:** `test_a29_all_dispatched_tasks_tracked_and_closed`

### A30 — Weekly audit summary published
Once per week, a summary of all audit findings, bug fixes, and policy changes MUST be committed.
**Enforcement:** AGENTS.md objective policy O21
**Test:** `test_a30_weekly_audit_summary_published`

---

## Group N — Naming/Code Standards (N01–N30)

### N01 — No lint-suppression comments in source
`# noqa`, `# type: ignore`, `# pylint: disable`, `# fmt: off`, `# isort:skip` are FORBIDDEN in `src/`.
**Enforcement:** `enforce-no-suppressions.ts` editor block + `scripts/check_tdd_compliance.py` scan
**Test:** `test_n01_no_lint_suppression_comments`

### N02 — Fix the underlying issue, never silence the linter
When a linter complains, repair the code so the linter is satisfied — do NOT paste a suppression comment.
**Enforcement:** AGENTS.md "No Lint-Suppression Comments" policy
**Test:** `test_n02_fix_underlying_issue_not_suppress`

### N03 — Tight types: no `Any` in new code
`Any` usage in type annotations for new code is forbidden — use `object`, generics, or specific types.
**Enforcement:** `make check-types` + type-safety skill
**Test:** `test_n03_no_any_in_new_code`

### N04 — Type annotations on all public functions
All public functions in `src/general_ludd/` MUST have complete type annotations.
**Enforcement:** mypy `--disallow-untyped-defs` + AGENTS.md
**Test:** `test_n04_type_annotations_on_all_public_functions`

### N05 — Node v26 compat: no `catch { try {` in plugins
Plugin files MUST NOT use the forbidden `catch { try {` or `catch (e) { try {` patterns.
**Enforcement:** `scripts/check_node_v26_compat.py` + `make check-node-v26-compat`
**Test:** `test_n05_no_catch_try_in_plugins`

### N06 — Node v26 compat: no type-annotated catch variables
Plugin files MUST NOT use `catch (e: TypeError)` — use `catch (e)` and typeof checks instead.
**Enforcement:** `scripts/check_node_v26_compat.py`
**Test:** `test_n06_no_typed_catch_variables`

### N07 — Node v26 compat: no enums or namespaces
Plugin files MUST NOT use TypeScript `enum` or `namespace` — use `const` objects instead.
**Enforcement:** `scripts/check_node_v26_compat.py`
**Test:** `test_n07_no_enums_or_namespaces`

### N08 — No duplicate Makefile targets
The Makefile MUST NOT declare any target more than once.
**Enforcement:** `scripts/check_duplicate_targets.py` + `make check-duplicate-targets`
**Test:** `test_n08_no_duplicate_makefile_targets`

### N09 — Makefile targets use lowercase-with-hyphens
Makefile target names MUST follow the `lowercase-with-hyphens` convention.
**Enforcement:** AGENTS.md naming conventions
**Test:** `test_n09_makefile_targets_lowercase_hyphens`

### N10 — Python code follows ruff rules
All Python code MUST pass `make lint` (ruff) with zero errors.
**Enforcement:** `make lint` as gate prerequisite
**Test:** `test_n10_python_code_passes_ruff`

### N11 — Python imports follow isort ordering
All Python imports MUST follow isort ordering (standard library, third-party, local).
**Enforcement:** `make lint` ruff isort rules
**Test:** `test_n11_python_imports_isort_ordering`

### N12 — No wildcard imports
`from module import *` is FORBIDDEN in production code.
**Enforcement:** `make lint` ruff F403/F405 rules
**Test:** `test_n12_no_wildcard_imports`

### N13 — No mutable default arguments
Function signatures MUST NOT use mutable default arguments (`def foo(x=[])`).
**Enforcement:** ruff B006 rule
**Test:** `test_n13_no_mutable_default_arguments`

### N14 — Docstrings on public modules and functions
Public modules and functions MUST have docstrings explaining their purpose and parameters.
**Enforcement:** ruff D rules + AGENTS.md
**Test:** `test_n14_docstrings_on_public_code`

### N15 — File length under 1000 lines
No single `.py` file under `src/` SHOULD exceed 1000 lines — split into modules if larger.
**Enforcement:** AGENTS.md code style
**Test:** `test_n15_file_length_under_1000_lines`

### N16 — Function length under 50 lines
No single function SHOULD exceed 50 lines — extract helpers for larger functions.
**Enforcement:** `make lint` ruff PLR0915 (too-many-statements) rule
**Test:** `test_n16_function_length_under_50_lines`

### N17 — No bare except clauses
`except:` without specifying an exception type is FORBIDDEN — use `except Exception:` at minimum.
**Enforcement:** ruff E722 rule
**Test:** `test_n17_no_bare_except_clauses`

### N18 — Secrets never hardcoded
Secrets (API keys, tokens, passwords) MUST never appear as string literals in source code.
**Enforcement:** `make secrets-scan` + `.secrets.baseline`
**Test:** `test_n18_secrets_never_hardcoded`

### N19 — Plugin code uses ES module syntax
All `.opencode/plugin/*.ts` files MUST use `import`/`export` syntax — no `require()`.
**Enforcement:** `scripts/check_node_v26_compat.py` + AGENTS.md
**Test:** `test_n19_plugin_code_es_module_syntax`

### N20 — Plugin code is import-only (no require)
All `.opencode/plugin/*.ts` files MUST use ES module `import` exclusively — zero `require()` calls.
**Enforcement:** `scripts/check_node_v26_compat.py`
**Test:** `test_n20_plugin_code_no_require`

### N21 — Config files use YAML format
Configuration files under `config/` MUST use YAML format.
**Enforcement:** AGENTS.md conventions
**Test:** `test_n21_config_files_use_yaml`

### N22 — Test file naming matches source module
Test file names MUST follow pattern: `test_<module_name>.py` matching the source module structure.
**Enforcement:** AGENTS.md + TDD compliance check
**Test:** `test_n22_test_file_naming_matches_source`

### N23 — Test function naming: `test_<behavior>_<expected>`
Test functions MUST follow `test_<what>_<expected_outcome>` naming convention.
**Enforcement:** test-quality skill
**Test:** `test_n23_test_function_naming_convention`

### N24 — Constants use UPPER_CASE
Module-level constants MUST use UPPER_CASE_UNDERSCORE naming.
**Enforcement:** `make lint` ruff N816 rule
**Test:** `test_n24_constants_use_upper_case`

### N25 — Private names use leading underscore
Non-public module internals MUST use a leading underscore (`_internal_function`).
**Enforcement:** Python convention + ruff rules
**Test:** `test_n25_private_names_leading_underscore`

### N26 — No overlapping variable names in scopes
Variable names MUST NOT shadow names in outer scopes.
**Enforcement:** ruff F402/A001/A002/A003 rules
**Test:** `test_n26_no_overlapping_variable_names`

### N27 — Repository paths use forward slashes
All paths in code and config MUST use forward slashes for cross-platform compatibility.
**Enforcement:** AGENTS.md conventions
**Test:** `test_n27_repository_paths_forward_slashes`

### N28 — Plugin filenames use `enforce-<domain>.ts` pattern
All enforcement plugin filenames MUST follow the `enforce-<descriptive-domain>.ts` pattern.
**Enforcement:** AGENTS.md enforcement plugin naming
**Test:** `test_n28_plugin_filenames_enforce_domain_pattern`

### N29 — Allowed paths are explicit and exhaustive
Any allowlist for file paths (suppression, TDD, dead-code) MUST be explicit with documented reasons.
**Enforcement:** AGENTS.md allowlist documentation requirement
**Test:** `test_n29_allowlists_explicit_and_documented`

### N30 — No commented-out code in production
Commented-out code in `src/` is FORBIDDEN — delete it or extract it to a ticket.
**Enforcement:** AGENTS.md code quality + lint rules
**Test:** `test_n30_no_commented_out_code`

---

## Group K — Knowledge/Context Management (K01–K30)

### K01 — SESSION.md is read at every session start
The first action of every session MUST include reading SESSION.md as part of the backlog read.
**Enforcement:** AGENTS.md session start protocol step 1
**Test:** `test_k01_session_md_read_at_session_start`

### K02 — SESSION.md is updated after every logical work unit
After each feature, fix, or test suite completion, SESSION.md MUST be updated with results.
**Enforcement:** AGENTS.md Session Persistence Policy
**Test:** `test_k02_session_md_updated_after_work_unit`

### K03 — SESSION.md contains: date, test status, last commit, completed work, known gaps, next steps
Every SESSION.md update MUST include all six required fields.
**Enforcement:** AGENTS.md session persistence field requirements
**Test:** `test_k03_session_md_has_required_fields`

### K04 — SESSION.md next steps are actionable
"Next Steps" in SESSION.md MUST be specific, actionable tasks — not vague "continue work".
**Enforcement:** AGENTS.md completion policy
**Test:** `test_k04_session_next_steps_actionable`

### K05 — SESSION.md never left stale
SESSION.md MUST be updated before the agent exit — a stale SESSION.md is a premature stop indicator.
**Enforcement:** AGENTS.md Session Persistence Policy + enforce-stop.ts
**Test:** `test_k05_session_md_never_left_stale`

### K06 — TASKS.md is the single task ledger
All work items MUST be tracked in TASKS.md with status: `pending`, `in_progress`, `completed`, `cancelled`.
**Enforcement:** AGENTS.md Task Self-Tracking Policy
**Test:** `test_k06_tasks_md_single_task_ledger`

### K07 — TASKS.md entries have unique IDs
Every TASKS.md task entry MUST have a unique identifier (W.N, G.N, FIX-N format).
**Enforcement:** AGENTS.md task ID convention
**Test:** `test_k07_tasks_md_entries_have_unique_ids`

### K08 — TASKS.md status updated immediately after subagent result
After a subagent result arrives, its TASKS.md entry MUST be updated to completed/failed before the next dispatch.
**Enforcement:** AGENTS.md post-result checklist
**Test:** `test_k08_tasks_md_updated_after_subagent_result`

### K09 — TASKS.md completed items include evidence
Completed TASKS.md entries MUST include: commit hash, test pass count, or artifact URL.
**Enforcement:** AGENTS.md task evidence requirements
**Test:** `test_k09_tasks_md_completed_include_evidence`

### K10 — TASKS.md never re-dispatches completed tasks
Before dispatching, a grep of TASKS.md MUST confirm the task is not already marked `completed`.
**Enforcement:** AGENTS.md pre-dispatch checklist
**Test:** `test_k10_tasks_md_no_redispatch_completed`

### K11 — BUGS.md tracks process failures independently
BUGS.md MUST exist and track premature stops, enforcement gaps, and behavioral flaws as distinct incidents.
**Enforcement:** AGENTS.md Premature-Stop Audit Policy
**Test:** `test_k11_bugs_md_tracks_process_failures`

### K12 — BUGS.md incidents are date-stamped
Each BUGS.md entry MUST include the date of the incident.
**Enforcement:** AGENTS.md BUGS.md entry format
**Test:** `test_k12_bugs_md_incidents_date_stamped`

### K13 — BUGS.md incidents include root cause analysis
Each BUGS.md entry MUST identify the root cause of the failure, not just the symptom.
**Enforcement:** AGENTS.md Root-Cause-Only Fix Policy applied to process
**Test:** `test_k13_bugs_md_root_cause_analysis`

### K14 — config/ratchet.yml tracks known-unfixed work
`config/ratchet.yml` MUST be maintained with all known-but-unfixed issues that gate is red for.
**Enforcement:** AGENTS.md ratchet tracking
**Test:** `test_k14_ratchet_yml_tracks_known_unfixed`

### K15 — Ratchet entries are numbered and dated
Each ratchet entry MUST have a unique number and date recorded.
**Enforcement:** AGENTS.md ratchet format
**Test:** `test_k15_ratchet_entries_numbered_dated`

### K16 — Ratchet is read at session start
`config/ratchet.yml` MUST be read at session start as part of the backlog read.
**Enforcement:** AGENTS.md session start protocol step 1
**Test:** `test_k16_ratchet_read_at_session_start`

### K17 — Gate status cached in .gate-status
The latest gate run result MUST be stored in `.gate-status` for fast status checks.
**Enforcement:** Makefile `gate` writes `.gate-status`
**Test:** `test_k17_gate_status_cached_in_file`

### K18 — CI state cached in /tmp/gludd-watchdog-ci.json
CI verdict results MUST be cached in `/tmp/gludd-watchdog-ci.json` for objective detection and stop guards.
**Enforcement:** `enforce-objective.ts` + `enforce-stop.ts` CI cache usage
**Test:** `test_k18_ci_state_cached_in_tmp`

### K19 — Knowledge files are readable by all agents
AGENTS.md, SESSION.md, TASKS.md, BUGS.md MUST be human-readable markdown at the repo root.
**Enforcement:** File existence and format
**Test:** `test_k19_knowledge_files_readable_markdown`

### K20 — No knowledge drift between docs and code
Design docs in `docs/design/` MUST be verified against actual code before using them as a plan basis.
**Enforcement:** AGENTS.md "Design docs have drifted stale" warning
**Test:** `test_k20_no_knowledge_drift_docs_vs_code`

### K21 — CLAUDE.md is the secondary context file
`docs/CLAUDE.md` MUST be maintained and kept in sync with `AGENTS.md`.
**Enforcement:** AGENTS.md context file hierarchy
**Test:** `test_k21_claude_md_synced_with_agents_md`

### K22 — git-index maintains searchable commit history
`make git-index` MUST populate a SQLite database of commit history for fast searching.
**Enforcement:** Makefile `git-index` + `git-search` targets
**Test:** `test_k22_git_index_searchable_history`

### K23 — Memory entries survive session resets
Important findings MUST be written to cross-session memory (opencode memory system).
**Enforcement:** AGENTS.md codification layers — memory layer
**Test:** `test_k23_memory_entries_survive_session_resets`

### K24 — No reliance on unverified context
When resuming work, the agent MUST verify prior claims with tool output, not trust SESSION.md alone.
**Enforcement:** AGENTS.md "Trust gate output, not SESSION.md"
**Test:** `test_k24_no_reliance_on_unverified_context`

### K25 — Context files are committed to the repo
SESSION.md, TASKS.md, BUGS.md, and `config/ratchet.yml` MUST be committed as part of normal work.
**Enforcement:** AGENTS.md session persistence — commit on SESSION.md change
**Test:** `test_k25_context_files_committed_to_repo`

### K26 — Context file changes are atomic commits
Updates to SESSION.md, TASKS.md, or BUGS.md SHOULD be in their own atomic commit.
**Enforcement:** AGENTS.md atomic commits rule
**Test:** `test_k26_context_file_changes_atomic_commits`

### K27 — Task IDs never reused
Once a task ID is used in TASKS.md, it MUST NOT be recycled for a different task.
**Enforcement:** AGENTS.md task ID uniqueness
**Test:** `test_k27_task_ids_never_reused`

### K28 — Phase markers in task IDs
Phase-based task IDs (Phase A, Phase B, etc.) MUST correspond to document sections in the project plan.
**Enforcement:** AGENTS.md task ID conventions
**Test:** `test_k28_phase_markers_in_task_ids`

### K29 — Gate output logs preserved per run
Each gate run's output MUST be preserved in `.gate-logs/gate-<timestamp>.log` — not overwritten.
**Enforcement:** Makefile `gate-background` log naming
**Test:** `test_k29_gate_output_logs_preserved_per_run`

### K30 — Error context captured on failure
When any operation fails, the error output MUST be captured and stored for diagnosis.
**Enforcement:** AGENTS.md "No Unseen Events" + `make gate-status-check` failure surfacing
**Test:** `test_k30_error_context_captured_on_failure`

---

## Group U — User Intent Enforcement (U01–U30)

### U01 — PRIMARY OBJECTIVE overrides all other priorities
The PRIMARY OBJECTIVE in SESSION.md MUST drive all task prioritization — nothing supersedes it.
**Enforcement:** `enforce-objective.ts` + AGENTS.md objective policy
**Test:** `test_u01_primary_objective_overrides_all`

### U02 — New user instructions stack, don't replace
When the user issues a new directive, it is ADDITIVE (AND) not SUBSTITUTIVE (OR) to existing objectives.
**Enforcement:** AGENTS.md Priority Stacking rule
**Test:** `test_u02_new_instructions_stack_dont_replace`

### U03 — "Fix X NOW" means fix X AND maintain multitasking
A priority-fix directive means fix X as first dispatch, NOT pause-everything-else to fix X serially.
**Enforcement:** AGENTS.md Priority Stacking worked examples
**Test:** `test_u03_fix_now_means_fix_and_multitask`

### U04 — "Don't wait on CI" means keep working, don't block
"Don't wait on CI" means never poll-block on CI AND keep doing real work.
**Enforcement:** AGENTS.md "CI is checked at natural breaks"
**Test:** `test_u04_dont_wait_on_ci_means_keep_working`

### U05 — User's explicit "first" means serial priority
When the user says "do X FIRST", complete X before continuing other work — but continue the other work after.
**Enforcement:** AGENTS.md Instruction-Following Priority
**Test:** `test_u05_user_explicit_first_means_serial`

### U06 — Don't ship bugs instead of fixing them
When the user asks for X to be shipped, do NOT fix unrelated bugs "while you're there" unless blocking.
**Enforcement:** AGENTS.md User Intent — don't sidetrack
**Test:** `test_u06_dont_fix_bugs_instead_of_shipping`

### U07 — Don't write essays instead of delivering
When the user asks for results, a 500-word essay explaining why is NOT the deliverable.
**Enforcement:** AGENTS.md Anti-Essay + `enforce-anti-essay.ts`
**Test:** `test_u07_dont_write_essays_instead_of_delivering`

### U08 — Codify improvements in the same session
When a better way to work is discovered, codify it in AGENTS.md / plugins / tests BEFORE session end.
**Enforcement:** AGENTS.md Codify Improvements meta-rule
**Test:** `test_u08_codify_improvements_same_session`

### U09 — User feedback is actioned immediately
When the user reports a bug, corrects behavior, or redirects work, the agent MUST act on it in the next response.
**Enforcement:** AGENTS.md Instruction-Following Priority
**Test:** `test_u09_user_feedback_actioned_immediately`

### U10 — Constraint is a design prompt, not a dead end
"You can't do X because Y" is ONLY acceptable when paired with the workaround being implemented.
**Enforcement:** AGENTS.md "Constraints Are To Engineer Around"
**Test:** `test_u10_constraint_is_design_prompt_not_dead_end`

### U11 — Never hand a constraint back to the user as a stop
"The API doesn't support that" without a workaround is a bug — dispatch a research task to find one.
**Enforcement:** AGENTS.md forbidden responses in constraints section
**Test:** `test_u11_never_hand_constraint_to_user_as_stop`

### U12 — Root cause fixes are mandatory
Every issue MUST be fixed at its root cause — never fixed at the symptom level.
**Enforcement:** AGENTS.md Root-Cause-Only Fix Policy
**Test:** `test_u12_root_cause_fixes_mandatory`

### U13 — "Fix" means repair, never disable
When the user asks to FIX something, the answer is NEVER to disable, remove, or weaken the feature.
**Enforcement:** AGENTS.md "Fix" Means Repair, Never Disable
**Test:** `test_u13_fix_means_repair_never_disable`

### U14 — Guardrails are strengthened, not weakened
When a guardrail causes friction, narrow the check — never delete the enforcement.
**Enforcement:** AGENTS.md Guardrail Integrity Policy
**Test:** `test_u14_guardrails_strengthened_not_weakened`

### U15 — Three-layer guardrail for every new restriction
Every new policy MUST be implemented at: config permission, runtime hook, and agent prompt layers.
**Enforcement:** AGENTS.md Meta-Rule: Guardrail Policy
**Test:** `test_u15_three_layer_guardrail_every_restriction`

### U16 — No manual defaults — every process is automated
Every config value MUST have a safe default; "run X manually" instructions are a bug.
**Enforcement:** AGENTS.md No-Manual-Default Policy
**Test:** `test_u16_no_manual_defaults_automated`

### U17 — Bash make-only policy followed
The agent MUST only run `make <target>` commands in bash — never raw commands.
**Enforcement:** `enforce-make.ts` + `opencode.json` permission block
**Test:** `test_u17_bash_make_only`

### U18 — No metacharacters in bash commands
`|`, `;`, `&&`, `||`, `$()`, backticks, `>`, `<` are FORBIDDEN in bash tool calls.
**Enforcement:** `enforce-make.ts` metacharacter deny
**Test:** `test_u18_no_metacharacters_in_bash`

### U19 — Missing make target → create one, don't bypass
If no make target exists for a needed operation, ADD the target — never run a raw command.
**Enforcement:** AGENTS.md Bash Command Policy
**Test:** `test_u19_create_make_target_not_bypass`

### U20 — File tools stay inside workspace
Read/Write/Edit/Glob/Grep MUST NOT access paths outside `/Users/shawnwilson/gludd/` or `/tmp/gludd-*`.
**Enforcement:** AGENTS.md "No External File Access"
**Test:** `test_u20_file_tools_inside_workspace`

### U21 — No external URL access without explicit need
WebFetch MUST only be used for programming-related documentation and resources.
**Enforcement:** AGENTS.md tool use restrictions
**Test:** `test_u21_no_external_url_without_need`

### U22 — Disk usage monitored and enforced
`make check-disk` MUST fail if /tmp/gludd-* > 100MB or disk > 90%.
**Enforcement:** `scripts/check_disk_usage.py` + `make check-disk`
**Test:** `test_u22_disk_usage_monitored`

### U23 — Tmp files cleaned up regularly
`make clean-tmp` MUST be run before session start and after large batch work.
**Enforcement:** AGENTS.md Disk Discipline + Makefile `clean-tmp`
**Test:** `test_u23_tmp_files_cleaned_regularly`

### U24 — Observability: no unseen events
Long-running operations MUST emit continuous progress — never go dark.
**Enforcement:** AGENTS.md "No Unseen Events"
**Test:** `test_u24_observability_no_unseen_events`

### U25 — Background operations never block dispatch
A background gate or long test is NEVER a reason to sleep or wait on the main thread.
**Enforcement:** AGENTS.md "Background Operations NEVER Block Dispatch"
**Test:** `test_u25_background_ops_never_block_dispatch`

### U26 — User intent is preserved across session boundaries
SESSION.md MUST carry forward the user's primary objective and preferences across sessions.
**Enforcement:** AGENTS.md session persistence + PRIMARY OBJECTIVE
**Test:** `test_u26_user_intent_preserved_across_sessions`

### U27 — Primary objective drives commit scope
Commits on a feature branch MUST advance the PRIMARY OBJECTIVE — unrelated fixes belong on separate branches.
**Enforcement:** AGENTS.md objective-driven prioritization
**Test:** `test_u27_primary_objective_drives_commit_scope`

### U28 — Enhancement-to-fix ratio enforced (≥50% enhancements)
At least half of every dispatch wave MUST be project enhancements, not just bug fixes.
**Enforcement:** `enforce-enhancement-ratio.ts` + AGENTS.md Enhancement/Fix Dispatch Ratio
**Test:** `test_u28_enhancement_to_fix_ratio_enforced`

### U29 — Todowrite tracks all user asks
When user raises ≥3 distinct asks, a `todowrite` list MUST be maintained tracking every ask.
**Enforcement:** AGENTS.md Todowrite discipline
**Test:** `test_u29_todowrite_tracks_all_user_asks`

### U30 — Completion evidence matches user's success criteria
"Done" is defined by the USER's criteria (gate green, CI green, artifact published) — not the agent's.
**Enforcement:** AGENTS.md "Done" Claims Require Observable Verification Evidence
**Test:** `test_u30_completion_evidence_matches_user_criteria`

---

## Group Z — Zero-Failure Enforcement (Z01–Z30)

### Z01 — All tests must pass or be ratchet-tracked
Every test in the suite MUST pass — no skipped, no xfailed (without strict), no deselected in CI.
**Enforcement:** Makefile `make gate` prerequisite + `config/ratchet.yml` tracking
**Test:** `test_z01_all_tests_pass_or_ratchet_tracked`

### Z02 — No CI bypass: all commits gate-checked
Every commit path that lands code in the repo MUST pass the gate — zero exceptions for "urgency".
**Enforcement:** `_gate-fresh-check` on all commit targets
**Test:** `test_z02_no_ci_bypass_all_commits_gate_checked`

### Z03 — Release completeness is verified before "shipped"
`make verify-release-completeness TAG=<tag>` MUST pass with all 12 artifact categories before claiming shipped.
**Enforcement:** `make release-cut` step 4 + AGENTS.md release policy
**Test:** `test_z03_release_completeness_verified_before_shipped`

### Z04 — No broken windows: every violation is fixed
When a policy violation is detected, it MUST be fixed immediately — no "we'll fix it later."
**Enforcement:** AGENTS.md "If you found it, you own it" + Self-Directed Work Rule
**Test:** `test_z04_no_broken_windows_every_violation_fixed`

### Z05 — Gate green before every merge
No branch merge (to master, development, or release) may proceed without green gate on the source branch.
**Enforcement:** `_gate-fresh-check` + merge target prerequisites
**Test:** `test_z05_gate_green_before_merge`

### Z06 — Enforcement plugins prevent, not just detect
Every enforcement plugin MUST block violations — advisory-only plugins are a gap.
**Enforcement:** AGENTS.md "All enforcement plugins are BLOCKING"
**Test:** `test_z06_enforcement_plugins_prevent_not_detect`

### Z07 — All plugins are hot-reload capable
Every enforcement plugin MUST implement the hot-reload proxy pattern.
**Enforcement:** `loadHotModule()` usage in each plugin
**Test:** `test_z07_all_plugins_hot_reload_capable`

### Z08 — All plugins have subagent guard
Every enforcement plugin MUST skip enforcement when `OPENCODE_SUBAGENT=1` or subagent detected.
**Enforcement:** `isSubagent()` check at top of every hook
**Test:** `test_z08_all_plugins_have_subagent_guard`

### Z09 — All plugins fail open
Any error in a plugin MUST allow the tool call or text through — never throw/block on error.
**Enforcement:** AGENTS.md "Fail-open" + try/catch in every plugin hook
**Test:** `test_z09_all_plugins_fail_open`

### Z10 — All plugins have disable env var
Every enforcement plugin MUST be disableable via a `GLUDD_*_ENFORCE=0` env var.
**Enforcement:** env var check in each plugin
**Test:** `test_z10_all_plugins_have_disable_env_var`

### Z11 — Node v26 compat: all plugins parse clean
All `.opencode/plugin/*.ts` files MUST pass `make check-node-v26-compat` with zero violations.
**Enforcement:** `scripts/check_node_v26_compat.py` + gate prerequisite
**Test:** `test_z11_all_plugins_node_v26_compat`

### Z12 — No require() calls in any plugin
All plugins MUST use ES module `import` exclusively — zero `require()` calls.
**Enforcement:** `scripts/check_node_v26_compat.py`
**Test:** `test_z12_no_require_in_any_plugin`

### Z13 — Zero-downtime enforcement evolution
Plugin changes MUST NOT create gaps in enforcement coverage — new plugin supersedes old before old is removed.
**Enforcement:** AGENTS.md guardrail integrity + plugin port map
**Test:** `test_z13_zero_downtime_enforcement_evolution`

### Z14 — Every spec has an enforcement mechanism
Every numbered spec in BEHAVIORAL_SPECS.md MUST map to at least one enforcement mechanism.
**Enforcement:** `tests/unit/test_behavioral_specs.py` structural check
**Test:** `test_z14_every_spec_has_enforcement_mechanism`

### Z15 — Every enforcement mechanism has a structural test
Every enforcement mechanism (plugin, make target, policy section) MUST have a corresponding structural test.
**Enforcement:** `tests/unit/test_behavioral_specs.py` coverage verification
**Test:** `test_z15_every_mechanism_has_structural_test`

### Z16 — Enforcement is three-layer: config + hook + prompt
Every behavioral restriction MUST be enforced at all three layers: config permission, runtime hook, agent prompt.
**Enforcement:** AGENTS.md Meta-Rule: Guardrail Policy
**Test:** `test_z16_enforcement_is_three_layer`

### Z17 — Recovery path for every enforcement block
When enforcement blocks legitimate work, there MUST be a documented recovery path (disable env var, emergency bypass).
**Enforcement:** AGENTS.md env var overrides for each plugin
**Test:** `test_z17_recovery_path_for_enforcement_block`

### Z18 — No enforcement regression without test coverage
Removing or weakening enforcement MUST fail its corresponding structural test — regression is impossible.
**Enforcement:** `tests/unit/test_behavioral_specs.py` structural pin
**Test:** `test_z18_no_enforcement_regression_without_test_failure`

### Z19 — Crash recovery resets enforcement state
`make crash-recovery` MUST reset all enforcement state files after a crashed session.
**Enforcement:** Makefile `crash-recovery` target
**Test:** `test_z19_crash_recovery_resets_enforcement`

### Z20 — Watchdog auto-starts at session start
`make watchdog-auto` MUST ensure the background watchdog daemon is running at every session start.
**Enforcement:** AGENTS.md session start protocol step 0
**Test:** `test_z20_watchdog_auto_starts_session_start`

### Z21 — Watchdog detects and unjams agent stops
The watchdog daemon MUST detect idle sessions and inject CONTINUE directives to unjam stops.
**Enforcement:** `agent_watchdog.py` + `watchdog.ts`
**Test:** `test_z21_watchdog_detects_unjams_stops`

### Z22 — Task watchdog kills stale subagent tasks
`scripts/task_watchdog.py` MUST kill subagent tasks exceeding their deadline (default 5 min).
**Enforcement:** `make task-watchdog-start` + deadline enforcement
**Test:** `test_z22_task_watchdog_kills_stale_tasks`

### Z23 — No single point of enforcement failure
If one plugin fails, others MUST continue enforcing — enforcement is not dependent on any single plugin.
**Enforcement:** AGENTS.md plugin independence + fail-open guards
**Test:** `test_z23_no_single_point_of_enforcement_failure`

### Z24 — Enforcement state is persistent and recoverable
State files (`/tmp/gludd-*.json`) MUST be JSON parseable and recoverable by `make crash-recovery`.
**Enforcement:** JSON format + crash-recovery target
**Test:** `test_z24_enforcement_state_persistent_recoverable`

### Z25 — Zero unregistered plugins
Every `.opencode/plugin/*.ts` file MUST be registered in `opencode.json`.
**Enforcement:** `tests/unit/test_behavioral_specs.py` plugin registration check
**Test:** `test_z25_zero_unregistered_plugins`

### Z26 — Plugin manifest is current
The plugin manifest (coverage table in this doc) MUST be updated when plugins are added or removed.
**Enforcement:** AGENTS.md + this coverage matrix
**Test:** `test_z26_plugin_manifest_is_current`

### Z27 — Enforcement applies equally to all models
Enforcement plugins MUST NOT have model-specific bypasses — same rules for sonnet, opus, haiku, deepseek.
**Enforcement:** Plugin logic is model-agnostic
**Test:** `test_z27_enforcement_applies_to_all_models`

### Z28 — Zero known enforcement gaps at session end
At session end, there MUST be no known enforcement gaps (unimplemented plugins) — all gaps must be in ratchet.
**Enforcement:** `config/ratchet.yml` + AGENTS.md
**Test:** `test_z28_zero_known_enforcement_gaps_at_session_end`

### Z29 — Spec coverage is tracked by test
The test file `test_behavioral_specs.py` MUST assert on every spec group's existence and coverage.
**Enforcement:** `tests/unit/test_behavioral_specs.py` group coverage tests
**Test:** `test_z29_spec_coverage_tracked_by_test`

### Z30 — 500 specs, all enforceable, all tested
BEHAVIORAL_SPECS.md MUST contain exactly 500 specs (20 groups × 25-30 specs each), all with enforcement and tests.
**Enforcement:** `tests/unit/test_behavioral_specs.py` count assertion + AGENTS.md
**Test:** `test_z30_500_specs_all_enforceable_all_tested`

---

## Coverage Matrix

| Group | Specs | Plugins | Makefile Guards | AGENTS.md Sections |
|-------|-------|---------|-----------------|--------------------|
| P — Push Discipline | P01-P30 | enforce-batch-push.ts, enforce-no-wait.ts, enforce-make.ts | _push-rate-guard, ci-busy-check, deploy-and-forget | Don't Push Every Commit, CI-Poll Subagents |
| B — Branch Discipline | B01-B25 | enforce-objective.ts (partial), [enforce-branch-discipline.ts planned] | check-duplicate-targets, release-branch-new, release-promote | Branch discipline, Single-Source Feature Development |
| O — Objective Tracking | O01-O30 | enforce-objective.ts | — | PRIMARY OBJECTIVE, Session Start Protocol |
| T — Test Integrity | T01-T30 | enforce-tdd.ts, enforce-no-suppressions.ts | _test-disabled-guard, collect-check, gate | TDD Policy, No Lint-Suppression Comments |
| D — Dispatch Floor | D01-D30 | enforce-multitask.ts, enforce-floor.ts, enforce-delegate.ts, enforce-clean-tree.ts | agent-worktree | Dispatch Floor, Pipeline Orchestration, Steady-state Dispatch |
| S — Anti-Stop | S01-S25 | enforce-stop.ts, enforce-verified-claims.ts | — | Premature-Stop Audit, Task Completion, Q&A Response, Nothing-Dropped |
| E — Anti-Essay | E01-E20 | enforce-stop.ts (partial), [enforce-anti-essay.ts planned] | — | Anti-Essay (new), Never Block on Questions |
| M — Merge Safety | M01-M20 | — | git-merge, gated-merge, agent-merge, feature-done | Merge Safety, Agent-Worktree |
| G — Gate Discipline | G01-G20 | enforce-make.ts | _gate-fresh-check, gate-background, gate-status | Gate Discipline, Background Gate, Completion = Green Gate |
| R — Release Discipline | R01-R20 | — | release-cut, require-ci-green, verify-release-completeness | Release is an Artifact, Release Pipeline, Release Branch Lifecycle |
| W — Worktree Discipline | W01-W30 | — | agent-worktree, agent-merge, agent-cleanup, agent-worktree-list | Worktree-per-subagent, Branch-landing integrity, Disk Discipline |
| F — CI Discipline | F01-F30 | enforce-no-wait.ts, enforce-batch-push.ts | _push-rate-guard, deploy-and-forget, ci-verdict-safe | CI-Poll Subagents, Anti-wait rule, Verify remote after push |
| C — Commit Discipline | C01-C30 | enforce-no-suppressions.ts, enforce-clean-tree.ts, enforce-tdd.ts | _gate-fresh-check, collect-check, secrets-scan | No-Commit-Bypass, Atomic commits, Commit-After-Green |
| Q — Quality Gate | Q01-Q30 | enforce-make.ts | gate, gate-background, gate-lite, gate-audit, check-duplicate-targets, check-node-v26-compat | Gate Discipline, Completion = Green Gate |
| X — Subagent Discipline | X01-X30 | enforce-deadline.ts, enforce-clean-tree.ts | task-watchdog | Fix-Don't-Check, Subagent quality, Refill-on-completion |
| A — Audit Discipline | A01-A30 | enforce-verified-claims.ts | verify-state, gate-status | Self-Audit Policy, Done Claims, Verification Evidence |
| N — Naming/Code | N01-N30 | enforce-no-suppressions.ts, enforce-tdd.ts | check-node-v26-compat, check-duplicate-targets | No Lint-Suppression, Tight types, Node v26 compat |
| K — Knowledge/Context | K01-K30 | enforce-objective.ts, enforce-stop.ts | git-index | Session Persistence, Task Self-Tracking, BUGS.md incidents |
| U — User Intent | U01-U30 | enforce-objective.ts, enforce-enhancement-ratio.ts, enforce-make.ts | — | PRIMARY OBJECTIVE, Priority Stacking, User Intent |
| Z — Zero-Failure | Z01-Z30 | (all plugins) | gate, crash-recovery | Completion Policy, Guardrail integrity, Zero-Failure |

---

## Audit Log

| Date | Change | Author |
|------|--------|--------|
| 2026-07-19 | Initial 200 specs (P-R) created | Agent |
| 2026-07-19 | Expanded to 500 specs (added W, F, C, Q, X, A, N, K, U, Z groups) | Agent |
