# BEHAVIORAL ENFORCEMENT SPECIFICATIONS — 1000 numbered specs

**Version:** 3.0
**Date:** 2026-07-19
**Status:** Active — corresponding enforcement mechanisms tracked in `tests/unit/test_behavioral_specs.py`

Each spec defines a behavioral invariant. Each spec MUST have a corresponding
enforcement mechanism (plugin, Makefile guard, or AGENTS.md policy section) and
a structural test verifying that mechanism exists.

Groups P-R (200 existing), W-Z (300 existing), H/L/J/V/Y (500 new).
Total: 1000 specs across 25 groups.

---

## Group P — Push Discipline (P01–P30)

### P01 — No push while CI is in_progress
The agent MUST NOT push commits to a branch while any CI run is `in_progress` on that branch.
**Enforcement:** AGENTS.md `enforce-batch-push.ts` tool.execute.before
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
**Enforcement:** Makefile `_push-rate-guard` push-cooldown check
**Test:** `test_p04_push_to_push_minimum_interval`

### P05 — Verify remote after every push
After any push, the agent MUST run `make verify-remote` and confirm the remote tip matches.
**Enforcement:** AGENTS.md "Verify the remote after every push"
**Test:** `test_p05_verify_remote_after_push`

### P06 — Never report CI verdict whose headSha != branch tip
The agent MUST NOT claim a CI verdict for a run whose headSha does not match the current branch tip.
**Enforcement:** AGENTS.md `ci-verdict` output is stale-run-aware
**Test:** `test_p06_ci_verdict_must_match_head_sha`

### P07 — CI cooldown check before any status claim
Before making any CI-status claim, the agent MUST use `make ci-verdict-safe` (cooldown-aware) not bare `make ci-verdict`.
**Enforcement:** AGENTS.md `scripts/ci_check_cooldown.py` + AGENTS.md
**Test:** `test_p07_ci_cooldown_before_status_claim`

### P08 — CI-COOLDOWN-UNKNOWN MUST NOT be reported as PENDING
When CI cooldown blocks a check, the agent MUST NOT interpret that as "CI is pending."
**Enforcement:** AGENTS.md `scripts/ci_check_cooldown.py` output labeling + AGENTS.md
**Test:** `test_p08_cooldown_not_reported_as_pending`

### P09 — Never push while gate is red
The agent MUST NOT push commits to a branch while the local gate is red or unrun.
**Enforcement:** AGENTS.md `enforce-batch-push.ts` gate-status check (planned)
**Test:** `test_p09_no_push_while_gate_red`

### P10 — Push only via sanctioned targets
The agent MUST use only `make batch-push`, `make development-push`, or `make git-push-sandboxcom` to push — never raw `git push`.
**Enforcement:** AGENTS.md `enforce-make.ts` non-make command block
**Test:** `test_p10_push_only_via_sanctioned_targets`

### P11 — Maximum one CI run in flight per branch
The agent MUST ensure at most one CI run is in progress on any given branch before pushing.
**Enforcement:** Makefile `_push-rate-guard` CI-in-flight check
**Test:** `test_p11_max_one_ci_run_per_branch`

### P12 — Cancelled-run thrash detection
When >3 cancelled runs exist in the last 2 hours, push MUST be blocked.
**Enforcement:** Makefile `_push-rate-guard` thrash detection
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
**Enforcement:** AGENTS.md `scripts/ci_push_guard.py` fail-closed logic
**Test:** `test_p16_push_rate_guard_fail_closed`

### P17 — Never push with dirty tree
The agent MUST NOT push to master/development while the working tree is dirty.
**Enforcement:** AGENTS.md `enforce-clean-tree.ts` block on task/agent/workflow dispatch when dirty
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
**Enforcement:** AGENTS.md `scripts/require_ci_green.py` + `make release-cut`
**Test:** `test_p20_ci_green_required_before_release_cut`

### P21 — Push history is auditable
Every push MUST be logged with timestamp, target branch, SHA, and CI state at push time.
**Enforcement:** AGENTS.md `scripts/ci_push_guard.py` state recording
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
**Enforcement:** AGENTS.md `enforce-no-wait.ts` + AGENTS.md anti-wait rule
**Test:** `test_p24_no_poll_ci_from_main_thread`

### P25 — Never dispatch CI-poll subagent
A subagent whose sole task is polling CI and sleeping MUST NOT be dispatched.
**Enforcement:** AGENTS.md `enforce-no-wait.ts` CI_POLL_DISPATCH_PATTERNS
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
**Enforcement:** AGENTS.md `tests/unit/test_push_guard_coverage.py` structural check
**Test:** `test_p30_push_guard_survives_makefile_refactor`

---

## Group B — Branch Discipline (B01–B25)

### B01 — Agent MUST work on the correct branch
Before any mutating operation, the agent MUST verify it is on the intended branch.
**Enforcement:** AGENTS.md `enforce-objective.ts` branch check (planned extension)
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
**Enforcement:** AGENTS.md `verify-state` before batch-push
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
**Enforcement:** AGENTS.md SESSION.md "PRIMARY OBJECTIVE" references branch
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
**Enforcement:** AGENTS.md `check-duplicate-targets` + single-source rule
**Test:** `test_b14_no_parallel_makefile_edits`

### B15 — Duplicate target detection at gate time
`make check-duplicate-targets` MUST scan for targets declared more than once.
**Enforcement:** AGENTS.md `scripts/check_duplicate_targets.py` + gate prerequisite
**Test:** `test_b15_duplicate_target_detection`

### B16 — Release branch starts from CI-green base
`make release-branch-new` MUST verify the base branch is CI-green before creating the release branch.
**Enforcement:** AGENTS.md `scripts/check_ci_green_for_base.py` (planned)
**Test:** `test_b16_release_branch_from_ci_green_base`

### B17 — Green release branch is immutable
Once a release branch's remote tip is CI-green, no new commits may land.
**Enforcement:** AGENTS.md `scripts/check_green_branch_guard.py` push deny
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
**Enforcement:** AGENTS.md `enforce-objective.ts` nag injection + text.complete check
**Test:** `test_o01_primary_objective_set_at_session_start`

### O02 — Objective is read before each tool call
The agent MUST consult the PRIMARY OBJECTIVE before making tool calls that do not advance it.
**Enforcement:** AGENTS.md `enforce-objective.ts` tool.execute.before advisory
**Test:** `test_o02_objective_read_before_tool_call`

### O03 — Tangential tool calls get objective warning
When the PRIMARY OBJECTIVE is not yet met and a non-dispatch/non-read tool call is made, a console.warn MUST fire.
**Enforcement:** AGENTS.md `enforce-objective.ts` console.warn on tangential tools
**Test:** `test_o03_tangential_tools_get_objective_warning`

### O04 — Dispatch and read tools always allowed
Even when the objective is unmet, dispatch (task/agent/workflow) and read (read/grep/glob) tools MUST be allowed.
**Enforcement:** AGENTS.md `enforce-objective.ts` unconditional allow for dispatch/read tools
**Test:** `test_o04_dispatch_and_read_always_allowed`

### O05 — CI-advancing bash targets are allowed
Even when the objective is unmet, CI-advancing and test/gate bash targets MUST be allowed.
**Enforcement:** AGENTS.md `enforce-objective.ts` bash allowlist pattern
**Test:** `test_o05_ci_advancing_bash_allowed`

### O06 — Objective-met detection for CI GREEN objectives
When the objective contains "CI GREEN", the plugin MUST check `/tmp/gludd-watchdog-ci.json` for `last_ci_status === "SUCCESS"`.
**Enforcement:** AGENTS.md `enforce-objective.ts` isObjectiveMet implementation
**Test:** `test_o06_ci_green_objective_detection`

### O07 — CI cache staleness window (10 minutes)
CI status cache older than 600 seconds MUST be considered stale and objective NOT met.
**Enforcement:** AGENTS.md `enforce-objective.ts` 600,000 ms stale threshold
**Test:** `test_o07_ci_cache_staleness_window`

### O08 — Non-CI objectives are treated as not-yet-met
Objective text that does not match CI-related patterns is always treated as unmet.
**Enforcement:** AGENTS.md `enforce-objective.ts` isObjectiveMet non-CI → false
**Test:** `test_o08_non_ci_objectives_not_yet_met`

### O09 — NAG_PREFIX injected when objective is missing
When SESSION.md has no PRIMARY OBJECTIVE, the NAG_PREFIX banner MUST be injected into outgoing text.
**Enforcement:** AGENTS.md `enforce-objective.ts` text.complete nag injection
**Test:** `test_o09_nag_prefix_injected_when_missing`

### O10 — Objective enforce disabled by env var
`GLUDD_OBJECTIVE_ENFORCE=0` MUST completely disable objective enforcement.
**Enforcement:** AGENTS.md `enforce-objective.ts` env var check
**Test:** `test_o10_objective_enforce_disabled_by_env_var`

### O11 — Subagent guard prevents objective enforcement
`OPENCODE_SUBAGENT=1` or subagent detection MUST skip objective checks in subagents.
**Enforcement:** AGENTS.md `enforce-objective.ts` isSubagent check at top
**Test:** `test_o11_subagent_guard_objective`

### O12 — Fail-open on any error
Any error in `enforce-objective.ts` MUST fail open — allow the tool call, do not throw.
**Enforcement:** AGENTS.md `enforce-objective.ts` try/catch fail-open blocks
**Test:** `test_o12_objective_fail_open`

### O13 — Hot-reload capable
`enforce-objective.ts` MUST implement the proxy pattern with hot-reload.
**Enforcement:** AGENTS.md `loadHotModule("objective", defaultImpl)` usage
**Test:** `test_o13_objective_hot_reload_capable`

### O14 — Objective met disables enforcement
When `isObjectiveMet()` returns true, all tool calls MUST pass through without warning.
**Enforcement:** AGENTS.md `enforce-objective.ts` early return on objective met
**Test:** `test_o14_objective_met_disables_enforcement`

### O15 — Edit and write tools are warned when objective unmet
Non-read, non-dispatch, non-bash-allowed tools (edit, write) MUST receive a console.warn when objective unmet.
**Enforcement:** AGENTS.md `enforce-objective.ts` default tool path
**Test:** `test_o15_edit_write_warned_unmet_objective`

### O16 — Objective text is discoverable
`getPrimaryObjective()` MUST be callable and return the objective text.
**Enforcement:** AGENTS.md exported function in `enforce-objective.ts`
**Test:** `test_o16_objective_text_discoverable`

### O17 — Objective change triggers re-evaluation
If SESSION.md PRIMARY OBJECTIVE changes mid-session, the plugin MUST read the updated text on next tool call.
**Enforcement:** AGENTS.md `enforce-objective.ts` reads file fresh each call
**Test:** `test_o17_objective_change_triggers_reevaluation`

### O18 — Objective enforcement runs on tool.execute.before
The primary enforcement hook MUST be `tool.execute.before`.
**Enforcement:** AGENTS.md `enforce-objective.ts` plugin hook registration
**Test:** `test_o18_objective_enforcement_on_tool_execute_before`

### O19 — Objective enforcement runs on text.complete
Missing-objective nag injection MUST run on `text.complete`.
**Enforcement:** AGENTS.md `enforce-objective.ts` text.complete hook
**Test:** `test_o19_objective_enforcement_on_text_complete`

### O20 — Objective nag is prepended, not appended
The missing-objective nag MUST be prepended to the outgoing text so it is the first thing visible.
**Enforcement:** AGENTS.md `enforce-objective.ts` nag + text concatenation order
**Test:** `test_o20_objective_nag_prepended`

### O21 — Weekly objective review is codified
The agent MUST review and update the PRIMARY OBJECTIVE once per week or per major project phase change.
**Enforcement:** AGENTS.md objective policy
**Test:** `test_o21_weekly_objective_review_codified`

### O22 — Objective drives task prioritization
TASKS.md items MUST be prioritized relative to the PRIMARY OBJECTIVE.
**Enforcement:** AGENTS.md TASKS.md Phase A (CI green + release) maps to objective
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
**Enforcement:** AGENTS.md `enforce-objective.ts` regex enforces single-line capture
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
**Enforcement:** AGENTS.md SESSION.md is read at session start (session-start protocol)
**Test:** `test_o29_objective_visible_in_context`

### O30 — Objective enforcement is always ON by default
`enforce-objective.ts` MUST default to active (ENFORCE=1) unless explicitly disabled.
**Enforcement:** AGENTS.md `GLUDD_OBJECTIVE_ENFORCE` default = "1" (absence = enforce)
**Test:** `test_o30_objective_enforcement_default_on`

---

## Group T — Test Integrity (T01–T30)

### T01 — Never disable tests in CI
The agent MUST NOT add `skip`, `xfail`, or disable tests that were previously running in CI.
**Enforcement:** Makefile `_test-disabled-guard` pre-commit check
**Test:** `test_t01_never_disable_tests_in_ci`

### T02 — Never use continue-on-error in CI
CI steps MUST NOT use `continue-on-error: true` to mask failures.
**Enforcement:** AGENTS.md `.github/workflows/build.yml` audit
**Test:** `test_t02_no_continue_on_error_ci`

### T03 — Test collection errors are hard failures
Zero collection errors must be confirmed before any commit.
**Enforcement:** Makefile `make collect-check` as gate prerequisite
**Test:** `test_t03_collection_errors_hard_failures`

### T04 — Test failures must be fixed, not suppressed
A failing test MUST be fixed; `# noqa`, `# type: ignore`, `pytest.skip`, and `@pytest.mark.xfail` (without strict=True) are forbidden workarounds.
**Enforcement:** AGENTS.md `enforce-no-suppressions.ts` + TDD policy
**Test:** `test_t04_test_failures_fixed_not_suppressed`

### T05 — Coverage threshold cannot be lowered to pass
The coverage `--fail-under` threshold MUST NOT be lowered to make gate pass.
**Enforcement:** AGENTS.md `pyproject.toml` coverage threshold + AGENTS.md
**Test:** `test_t05_coverage_threshold_not_lowered`

### T06 — TDD: test file must exist before source edit
The agent MUST write a test file before editing corresponding source in `src/general_ludd/**/*.py`.
**Enforcement:** AGENTS.md `enforce-tdd.ts` real-time editor block
**Test:** `test_t06_tdd_test_before_source`

### T07 — TDD allowlist matches check_tdd_compliance.py
The `enforce-tdd.ts` allowlist MUST match `scripts/check_tdd_compliance.py` exactly.
**Enforcement:** AGENTS.md `tests/unit/test_tdd_allowlist_parity.py` (planned)
**Test:** `test_t07_tdd_allowlist_parity`

### T08 — Every new source file requires a test file
No new `.py` file under `src/general_ludd/` may land without a corresponding test file in `tests/unit/`.
**Enforcement:** AGENTS.md `scripts/check_tdd_compliance.py` commit-time backstop
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
**Enforcement:** AGENTS.md test-quality skill + code review
**Test:** `test_t11_test_quality_aaa_structure`

### T12 — No mock-only tests
A test that mocks the entire system under test and asserts mock calls is insufficient.
**Enforcement:** AGENTS.md test-quality skill
**Test:** `test_t12_no_mock_only_tests`

### T13 — No tests that test mocks themselves
A test MUST NOT test that a mock was called — it must assert on the system's actual behavior.
**Enforcement:** AGENTS.md test-quality skill
**Test:** `test_t13_no_tests_that_test_mocks`

### T14 — Integration tests verify cross-subsystem behavior
Integration tests MUST exercise 2+ subsystems together.
**Enforcement:** AGENTS.md test-quality skill + test layer audit
**Test:** `test_t14_integration_tests_cross_subsystem`

### T15 — E2E tests go through the daemon API
E2E tests MUST exercise functionality through the daemon API as a user would.
**Enforcement:** AGENTS.md test-quality skill
**Test:** `test_t15_e2e_through_daemon_api`

### T16 — No test isolation pollution
Tests MUST NOT depend on shared mutable state (env vars, files, globals) from other tests.
**Enforcement:** AGENTS.md test-quality skill
**Test:** `test_t16_no_test_isolation_pollution`

### T17 — Each test has one assertion concept
Each test function MUST assert on one behavioral concept.
**Enforcement:** AGENTS.md test-quality skill
**Test:** `test_t17_one_assertion_concept_per_test`

### T18 — Test names describe behavior, not implementation
Test names MUST describe WHAT behavior is verified, not HOW it's implemented.
**Enforcement:** AGENTS.md test-quality skill
**Test:** `test_t18_test_names_describe_behavior`

### T19 — Realistic test data
Tests MUST use realistic test data, not arbitrary strings or dummy values.
**Enforcement:** AGENTS.md test-quality skill
**Test:** `test_t19_realistic_test_data`

### T20 — Deterministic tests
Tests MUST be deterministic — no random seeds, no time-dependent assertions, no network calls.
**Enforcement:** AGENTS.md test-quality skill
**Test:** `test_t20_deterministic_tests`

### T21 — No coverage gaming
Tests written solely to increase coverage percentage without meaningful assertions are forbidden.
**Enforcement:** AGENTS.md test-quality skill + coverage audit
**Test:** `test_t21_no_coverage_gaming`

### T22 — Gate must run before any status claim of "green"
The agent MUST run `make gate` or `make gate-status` before claiming tests are green.
**Enforcement:** AGENTS.md evidence-based response policy
**Test:** `test_t22_gate_before_green_claim`

### T23 — Test-run output must be cited with pass count
When claiming tests pass, the agent MUST cite the exact pass count from test output.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` evidence requirement
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
**Enforcement:** Makefile `make task-watchdog-start` + watchdog daemon
**Test:** `test_t27_task_watchdog_kills_stale`

### T28 — 5-minute max per subtask
Dispatched subagent tasks MUST complete within 5 minutes; longer tasks must be split.
**Enforcement:** AGENTS.md `enforce-deadline.ts` detection + `task_watchdog.py` killing
**Test:** `test_t28_five_minute_max_per_subtask`

### T29 — Test files must be importable
Every test file in `tests/` MUST be importable — no syntax errors, no missing imports.
**Enforcement:** Makefile `make collect-check` as pre-commit gate
**Test:** `test_t29_test_files_importable`

### T30 — collect-check is gate prerequisite
`make collect-check` MUST be a prerequisite of `make gate` and all commit targets.
**Enforcement:** Makefile gate target prerequisites
**Test:** `test_t30_collect_check_gate_prerequisite`

---

## Group D — Dispatch Floor (D01–D30)

### D01 — Minimum 10 dispatches per wave
When pending work exists, each dispatch wave MUST contain at least 10 task/agent/workflow dispatches.
**Enforcement:** AGENTS.md `enforce-multitask.ts` MIN_DISPATCHES=10
**Test:** `test_d01_min_10_dispatches_per_wave`

### D02 — Under-floor dispatch denied
A response with fewer than MIN_DISPATCHES dispatches while work is pending MUST be blocked.
**Enforcement:** AGENTS.md `enforce-multitask.ts` under-floor hard block
**Test:** `test_d02_under_floor_dispatch_denied`

### D03 — Zero-dispatch streak blocked at MAX_ZERO_STREAK
After MAX_ZERO_STREAK (2) consecutive zero-dispatch responses, further non-dispatch tool calls are blocked.
**Enforcement:** AGENTS.md `enforce-multitask.ts` zero-streak counter
**Test:** `test_d03_zero_dispatch_streak_blocked`

### D04 — Dispatch resets zero-streak counter
A dispatch wave (≥1 task/agent/workflow) MUST reset the zero-dispatch streak counter to 0.
**Enforcement:** AGENTS.md `enforce-multitask.ts` streak reset on dispatch
**Test:** `test_d04_dispatch_resets_zero_streak`

### D05 — Read tools do not increment streak
Read/grep/glob tool calls MUST NOT increment the zero-dispatch streak counter.
**Enforcement:** AGENTS.md `enforce-multitask.ts` isReadTool check
**Test:** `test_d05_read_tools_do_not_increment_streak`

### D06 — Consecutive non-dispatch counter
After CONSECUTIVE_NON_DISPATCH_THRESHOLD (5) non-dispatch calls within CONSECUTIVE_NON_DISPATCH_WINDOW_MS (30s), all non-dispatch tools are blocked.
**Enforcement:** AGENTS.md `enforce-floor.ts` consecutive-non-dispatch counter
**Test:** `test_d06_consecutive_non_dispatch_counter`

### D07 — Time-based window for grinding detection
The consecutive-non-dispatch counter MUST reset if no non-dispatch calls occur within 30 seconds.
**Enforcement:** AGENTS.md `enforce-floor.ts` time-window reset
**Test:** `test_d07_time_window_grinding_detection`

### D08 — Post-result read limit
After subagent results arrive, at most POST_RESULT_READ_LIMIT (3) reads are allowed before dispatch is required.
**Enforcement:** AGENTS.md `enforce-floor.ts` read limit in result grace window
**Test:** `test_d08_post_result_read_limit`

### D09 — Message-shape rule: ≥2 dispatches OR zero changes
Each response with tool calls MUST have either 0 dispatches (pure read/edit/bash) or ≥2 dispatches.
**Enforcement:** AGENTS.md message-shape rule
**Test:** `test_d09_message_shape_rule`

### D10 — Single-dispatch responses are policy violation
A response with exactly 1 task/agent/workflow dispatch when ≥2 work items remain is a policy violation.
**Enforcement:** AGENTS.md `enforce-multitask.ts` prevMessageDispatches check
**Test:** `test_d10_single_dispatch_is_violation`

### D11 — Main-thread grind threshold
4+ consecutive non-dispatch tool calls MUST trigger a MAINTHREAD_GRIND advisory.
**Enforcement:** AGENTS.md `enforce-delegate.ts` MAINTHREAD_THRESHOLD
**Test:** `test_d11_main_thread_grind_threshold`

### D12 — Dispatch ceiling at 10
No more than 10 concurrent subagents may be dispatched.
**Enforcement:** AGENTS.md `enforce-floor.ts` CEILING=10 + COST-EFFICIENCY DIRECTIVE
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
**Enforcement:** AGENTS.md `enforce-clean-tree.ts` deny dispatch on dirty tree
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
**Enforcement:** AGENTS.md "Cost-Efficiency Directive" rule 4
**Test:** `test_d21_research_subagents_serialized`

### D22 — Coding subagents ≤2 parallel
At most 2 file-editing subagents may run in parallel (disjoint files only).
**Enforcement:** AGENTS.md "Cost-Efficiency Directive" rule 4
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
**Enforcement:** AGENTS.md `enforce-multitask.ts` waveHistory array
**Test:** `test_d25_wave_history_tracked`

### D26 — Dispatch wave boundary detection
Multitask plugin MUST detect message boundaries to track per-message dispatch counts.
**Enforcement:** AGENTS.md `enforce-multitask.ts` text.complete-based boundary
**Test:** `test_d26_dispatch_wave_boundary_detection`

### D27 — Estimated in-flight counter
Plugin MUST track estimated in-flight subagent count.
**Enforcement:** AGENTS.md `enforce-multitask.ts` estimatedInFlight
**Test:** `test_d27_estimated_in_flight_counter`

### D28 — Dispatch floor env-var overridable
GLUDD_MIN_DISPATCHES env var MUST be able to override the floor (minimum 2).
**Enforcement:** AGENTS.md `enforce-multitask.ts` MIN_DISPATCHES computation
**Test:** `test_d28_dispatch_floor_env_var_overridable`

### D29 — Floor enforcement is default ON
The dispatch floor enforcement MUST default to ON (not advisory).
**Enforcement:** AGENTS.md `enforce-multitask.ts` FLOOR_ENFORCE default true
**Test:** `test_d29_floor_enforcement_default_on`

### D30 — Floor enforcement disabled by env var
GLUDD_MULTITASK_FLOOR_ENFORCE=0 MUST disable all multitask enforcement.
**Enforcement:** AGENTS.md `enforce-multitask.ts` FLOOR_ENFORCE gate
**Test:** `test_d30_floor_enforcement_disabled_by_env_var`

---

## Group S — Anti-Stop (S01–S25)

### S01 — Never text-only with pending work
The agent MUST NEVER send a text-only response while TASKS.md has unchecked items.
**Enforcement:** AGENTS.md `enforce-stop.ts` text.complete blanking
**Test:** `test_s01_no_text_only_with_pending_work`

### S02 — Text-only response is blanked when pending work exists
When `hasRealPendingWork()` returns true, the text.complete hook MUST blank the response.
**Enforcement:** AGENTS.md `enforce-stop.ts` hasRealPendingWork() check
**Test:** `test_s02_text_only_blanked_with_pending_work`

### S03 — Pending work includes CI state
`hasRealPendingWork()` MUST check CI status (ci-verdict), not just TASKS.md.
**Enforcement:** AGENTS.md `enforce-stop.ts` comprehensive work detection
**Test:** `test_s03_pending_work_includes_ci_state`

### S04 — Pending work includes release completeness
`hasRealPendingWork()` MUST check release artifact completeness.
**Enforcement:** AGENTS.md `enforce-stop.ts` verify-release-completeness check
**Test:** `test_s04_pending_work_includes_release_completeness`

### S05 — Pending work includes gate status
`hasRealPendingWork()` MUST check `.gate-status` for failures.
**Enforcement:** AGENTS.md `enforce-stop.ts` gate-status check
**Test:** `test_s05_pending_work_includes_gate_status`

### S06 — Pending work includes ratchet entries
`hasRealPendingWork()` MUST check `config/ratchet.yml` for entries.
**Enforcement:** AGENTS.md `enforce-stop.ts` ratchet check
**Test:** `test_s06_pending_work_includes_ratchet_entries`

### S07 — Completion words blocked without evidence
Words like "done", "landed", "pushed", "fixed", "passing" MUST carry machine-produced evidence.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` done-words evidence requirement
**Test:** `test_s07_completion_words_blocked_without_evidence`

### S08 — Status summaries during session-start are blocked
After backlog reads and before first dispatch wave, a status summary response is blanked.
**Enforcement:** AGENTS.md `enforce-stop.ts` STATUS_SUMMARY_RE + session-start window
**Test:** `test_s08_status_summaries_blocked_session_start`

### S09 — Q&A summaries blocked without tool call
A Q&A response like "Here's what was done" without a tool call is blanked.
**Enforcement:** AGENTS.md `enforce-stop.ts` QA_RESPONSE_PATTERNS
**Test:** `test_s09_qa_summaries_blocked_without_tool_call`

### S10 — Never ask "Shall I continue?" or "Ready for review?"
Questions that imply waiting for user permission to continue work are forbidden.
**Enforcement:** AGENTS.md anti-stop patterns
**Test:** `test_s10_never_ask_permission_to_continue`

### S11 — Stop-signal word detection
`enforce-stop.ts` MUST detect a comprehensive list of stop-signal phrases.
**Enforcement:** AGENTS.md `enforce-stop.ts` STOP_SIGNAL_WORDS
**Test:** `test_s11_stop_signal_word_detection`

### S12 — Heuristic stop-pattern detection
Beyond words, `enforce-stop.ts` MUST detect structural patterns: bold-summary, commit+table, test-count summary.
**Enforcement:** AGENTS.md `enforce-stop.ts` heuristic checks
**Test:** `test_s12_heuristic_stop_pattern_detection`

### S13 — Interleaved summary detection
Completion-style status summaries interleaved with tool calls MUST still be detected and blanked.
**Enforcement:** AGENTS.md `enforce-stop.ts` interleaved-summary detection
**Test:** `test_s13_interleaved_summary_detection`

### S14 — Evidence present does not exempt stop patterns
A completion summary carrying a commit hash is STILL a premature stop if pending work exists.
**Enforcement:** AGENTS.md `enforce-stop.ts` evidence-exempt removed from summary detection
**Test:** `test_s14_evidence_present_does_not_exempt`

### S15 — False-done claim detection
The string "All done", "Everything is complete", "All tasks finished" MUST trigger blocking.
**Enforcement:** AGENTS.md `enforce-stop.ts` false-completion patterns
**Test:** `test_s15_false_done_claim_detection`

### S16 — Stop guardrail is fail-open
Any error in `enforce-stop.ts` MUST fail open — allow the response through.
**Enforcement:** AGENTS.md `enforce-stop.ts` try/catch fail-open
**Test:** `test_s16_stop_guardrail_fail_open`

### S17 — Stop enforcement is never disabled by disengage for real pending work
Disengage signal (`/tmp/gludd-watchdog-disengage`) MUST NOT bypass the `hasRealPendingWork()` text-only block.
**Enforcement:** AGENTS.md `enforce-stop.ts` disengage only skips heuristics
**Test:** `test_s17_disengage_does_not_disable_real_pending_work`

### S18 — Subagents do not stop-enforce
`enforce-stop.ts` MUST skip enforcement entirely for subagent output.
**Enforcement:** AGENTS.md isSubagent() guard at top of hooks
**Test:** `test_s18_subagents_do_not_stop_enforce`

### S19 — Stop guard is hot-reload capable
`enforce-stop.ts` MUST implement the proxy pattern.
**Enforcement:** AGENTS.md loadHotModule("stop", ...) usage
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
**Enforcement:** AGENTS.md `enforce-stop.ts` text.complete hook
**Test:** `test_s23_stop_guard_fires_at_text_complete`

### S24 — Stop guard also blocks via tool.execute.before
`enforce-stop.ts` MUST also have a `tool.execute.before` hook for commit blocking.
**Enforcement:** AGENTS.md `enforce-stop.ts` tool.execute.before commit gate
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
**Enforcement:** AGENTS.md `enforce-stop.ts` bolded-header structural detection
**Test:** `test_e02_bolded_headers_blocked_in_final_response`

### E03 — Essay-length responses detected and blocked
Responses exceeding a floor-word-count threshold while carrying no tool calls MUST be flagged.
**Enforcement:** AGENTS.md `enforce-anti-essay.ts` (planned) word-count heuristic
**Test:** `test_e03_essay_length_responses_blocked`

### E04 — Commitment-to-action ratio
Each agent response MUST have at least as many tool calls as prose paragraphs.
**Enforcement:** AGENTS.md mechanical contract rule #2 — produce tool calls not prose
**Test:** `test_e04_commitment_to_action_ratio`

### E05 — Status tables in text responses forbidden
A markdown table (| columns |) in a text response while pending work exists MUST be treated as a stop pattern.
**Enforcement:** AGENTS.md `enforce-stop.ts` markdown table detection
**Test:** `test_e05_status_tables_forbidden`

### E06 — Bullet lists of completed work without next action blocked
A text-only response with N+ bullet points listing completed work and no tool call MUST be blocked.
**Enforcement:** AGENTS.md `enforce-stop.ts` bullet-list heuristic
**Test:** `test_e06_bullet_list_completed_work_blocked`

### E07 — "Summary" pattern detected regardless of phrasing
Any response containing "summary", "recap", "what we did", "status report" while pending work exists MUST be blocked.
**Enforcement:** AGENTS.md `enforce-stop.ts` summary-word detection
**Test:** `test_e07_summary_pattern_always_detected`

### E08 — Post-commit prose block
After a commit lands, the agent MUST NOT generate a prose description of the commit — must continue to next task.
**Enforcement:** AGENTS.md anti-stop patterns
**Test:** `test_e08_post_commit_prose_block`

### E09 — Never write "Here's what changed"
The phrase "Here's what changed" or equivalent when work remains MUST be blanked.
**Enforcement:** AGENTS.md `enforce-stop.ts` status-summary pattern
**Test:** `test_e09_heres_what_changed_blocked`

### E10 — Explaining the root cause is not fixing it
Writing an analysis of a bug's root cause without the fix code MUST NOT be considered progress.
**Enforcement:** AGENTS.md Root-Cause-Only Fix Policy
**Test:** `test_e10_explaining_root_cause_is_not_fixing`

### E11 — Anti-essay guard is plugin-enforced
A dedicated `enforce-anti-essay.ts` plugin MUST detect and block essay patterns.
**Enforcement:** AGENTS.md `enforce-anti-essay.ts` (planned new plugin)
**Test:** `test_e11_anti_essay_guard_plugin`

### E12 — Adaptive word-count threshold
The anti-essay word-count threshold MUST adapt based on whether the response contains tool calls.
**Enforcement:** AGENTS.md `enforce-anti-essay.ts` (planned)
**Test:** `test_e12_adaptive_word_count_threshold`

### E13 — No-metadata prose detection
Responses containing 0 commit hashes, 0 test counts, 0 CI verdicts and >50 words MUST be flagged.
**Enforcement:** AGENTS.md `enforce-anti-essay.ts` (planned) metadata-absence heuristic
**Test:** `test_e13_no_metadata_prose_detection`

### E14 — Image/emoji-heavy prose blocked
Responses that use emojis or decorative formatting to pad a text-only response MUST be flagged.
**Enforcement:** AGENTS.md `enforce-stop.ts` (planned extension)
**Test:** `test_e14_decorative_formatting_blocked`

### E15 — "Let me explain" patterns blocked
Phrases like "Let me explain", "Here's why", "To understand this" at the start of a response while work pending MUST be flagged.
**Enforcement:** AGENTS.md `enforce-stop.ts` (planned extension)
**Test:** `test_e15_let_me_explain_blocked`

### E16 — Response length limit when gate is red
When `.gate-status` is FAILED, text responses MUST NOT exceed a short error message.
**Enforcement:** AGENTS.md `enforce-stop.ts` gate-red text clamp (planned)
**Test:** `test_e16_response_length_limit_when_gate_red`

### E17 — Prose-to-code ratio enforced
Over a session, the ratio of prose output to code output MUST be tracked and warned when > 1:1.
**Enforcement:** AGENTS.md `enforce-anti-essay.ts` (planned) ratio tracking
**Test:** `test_e17_prose_to_code_ratio_enforced`

### E18 — No open-ended planning prose
"Do you want me to...", "I could approach this by...", "One option is..." — these MUST be blocked when work exists.
**Enforcement:** AGENTS.md "Never Block on Questions"
**Test:** `test_e18_no_open_ended_planning_prose`

### E19 — Concrete action required in every response
Every response with pending work MUST include at least one concrete, specific action (not a plan, not an option).
**Enforcement:** AGENTS.md `enforce-stop.ts` tool-call presence check
**Test:** `test_e19_concrete_action_required_in_every_response`

### E20 — Anti-essay enforcement is default ON
The anti-essay guard MUST be enabled by default and only disabled via explicit env var.
**Enforcement:** AGENTS.md `enforce-anti-essay.ts` (planned)
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
**Enforcement:** AGENTS.md `src/general_ludd/git_automation/locking.py` — lock acquisition
**Test:** `test_m12_git_locking_for_merge_operations`

### M13 — Worktree git-lock bug is known and documented
The worktree `.git`-as-file locking gap MUST be documented as a known issue.
**Enforcement:** AGENTS.md "KNOWN GAP: git locking is broken inside worktrees"
**Test:** `test_m13_worktree_lock_bug_documented`

### M14 — No concurrent merge operations
Two merges MUST NOT run concurrently — the second MUST wait or fail.
**Enforcement:** AGENTS.md git_repo_lock serialization
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
**Enforcement:** Makefile `_gate-fresh-check` mtime comparison
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
**Enforcement:** AGENTS.md `enforce-make.ts` long-op foreground deny
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
**Enforcement:** Makefile `make require-ci-green` as release-cut step 0
**Test:** `test_r04_ci_green_before_tag_push`

### R05 — Require-ci-green is fail-closed
No matching run found must exit RED (failure), not default to green.
**Enforcement:** AGENTS.md `scripts/require_ci_green.py` fail-closed logic
**Test:** `test_r05_require_ci_green_fail_closed`

### R06 — Verify-release-completeness checks 12 categories
`make verify-release-completeness` MUST check all 12 required artifact categories.
**Enforcement:** AGENTS.md `scripts/verify_release_completeness.py` 12-category check
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
**Enforcement:** Makefile `make check-readme-status` as release-cut gate
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
**Enforcement:** AGENTS.md `.github/workflows/build.yml` tag trigger
**Test:** `test_r13_tag_push_triggers_ci`

### R14 — Release job needs gate in CI
CI workflow `release` job MUST `needs: [gate]` (transitively) so broken code cannot publish.
**Enforcement:** AGENTS.md `.github/workflows/build.yml` needs dependency
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
**Enforcement:** AGENTS.md `docs/RELEASE_RUNBOOK.md` (planned verification)
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
**Enforcement:** AGENTS.md git worktree isolation (structure)
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
**Enforcement:** AGENTS.md `scripts/check_disk_usage.py` pre-worktree disk check
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
**Enforcement:** Makefile `_push-rate-guard` + batch-push threshold
**Test:** `test_f01_ci_push_triggers_one_batch_run`

### F02 — Never push while CI is in_progress on target branch
Push MUST be blocked when any CI run for the target branch has status `in_progress`.
**Enforcement:** AGENTS.md `scripts/ci_busy_check.py` + `_push-rate-guard`
**Test:** `test_f02_no_push_while_ci_in_progress`

### F03 — Push-to-push cooldown enforced
A second push within 120 seconds of the prior push to the same branch MUST be hard-denied.
**Enforcement:** Makefile `_push-rate-guard` push-cooldown (120s)
**Test:** `test_f03_push_cooldown_enforced`

### F04 — Cancelled-run thrash detection blocks push
When >3 cancelled CI runs exist in the last 2 hours on a branch, push MUST be blocked.
**Enforcement:** Makefile `_push-rate-guard` thrash detection
**Test:** `test_f04_cancelled_run_thrash_detection`

### F05 — Push rate guard is fail-closed
If CI state cannot be determined (gh unavailable, network down), push MUST be denied.
**Enforcement:** AGENTS.md `scripts/ci_push_guard.py` fail-closed
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
**Enforcement:** Makefile `make ci-verdict` stale-run warning
**Test:** `test_f08_no_ci_claim_without_matching_headsha`

### F09 — CI-verdict-safe cooldown is machine-enforced
`make ci-verdict-safe` MUST respect a 10-minute cooldown between CI checks.
**Enforcement:** AGENTS.md `scripts/ci_check_cooldown.py`
**Test:** `test_f09_ci_verdict_safe_cooldown`

### F10 — CI-COOLDOWN status is never reported as CI-PENDING
When cooldown blocks a check, the output MUST say CI-COOLDOWN, not CI-PENDING.
**Enforcement:** AGENTS.md `scripts/ci_check_cooldown.py` output labeling
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
**Enforcement:** AGENTS.md `enforce-no-wait.ts` CI_POLL_DISPATCH_PATTERNS
**Test:** `test_f13_no_ci_poll_subagent`

### F14 — Deploy-and-forget is the fire-and-forget push pattern
When pushing for CI validation (not release), the agent MUST use `make deploy-and-forget`.
**Enforcement:** Makefile `deploy-and-forget` target
**Test:** `test_f14_deploy_and_forget_pattern`

### F15 — Deploy-and-forget records push timestamp
`make deploy-and-forget` MUST record the push timestamp for cooldown calculation.
**Enforcement:** AGENTS.md `scripts/ci_check_cooldown.py` timestamp recording
**Test:** `test_f15_deploy_and_forget_records_timestamp`

### F16 — CI check at natural breaks only
CI status MUST be checked at natural work breaks (subagent result ingestion), not continuously.
**Enforcement:** AGENTS.md "CI is checked at natural breaks, not polled"
**Test:** `test_f16_ci_check_at_natural_breaks`

### F17 — Never poll CI from main thread
`make ci-verdict` or `make ci-wait` MUST NOT run on the main thread.
**Enforcement:** AGENTS.md `enforce-no-wait.ts` main-thread denial
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
**Enforcement:** AGENTS.md `scripts/ci_verdict_cache.py` (planned)
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
**Enforcement:** AGENTS.md `scripts/ci_push_guard.py` state recording
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
**Enforcement:** Makefile `make secrets-scan` pre-commit hook + `.secrets.baseline`
**Test:** `test_c10_never_commit_secrets`

### C11 — Secrets baseline is updated after scrub
After a `make secrets-scrub`, `.secrets.baseline` MUST be updated and the change committed separately.
**Enforcement:** Makefile `make secrets-baseline` + pre-commit hook
**Test:** `test_c11_secrets_baseline_updated_after_scrub`

### C12 — Lint must pass before commit
`make lint` MUST return 0 errors before any commit with source changes can land.
**Enforcement:** Makefile pre-commit hook via `make install-hooks`
**Test:** `test_c12_lint_passes_before_commit`

### C13 — Typecheck baseline must not regress
New type errors introduced in a commit MUST NOT exceed the mypy baseline.
**Enforcement:** Makefile `make typecheck` + mypy baseline enforcement
**Test:** `test_c13_typecheck_baseline_no_regression`

### C14 — No suppression comments in committed code
Commits MUST NOT contain `# noqa`, `# type: ignore`, `# pylint: disable`, `# fmt: off`, `# isort:skip`.
**Enforcement:** AGENTS.md `enforce-no-suppressions.ts` + `scripts/check_tdd_compliance.py`
**Test:** `test_c14_no_suppression_comments_in_commits`

### C15 — TDD: test file exists for every new source file
No new `.py` file under `src/general_ludd/` may be committed without a corresponding test file.
**Enforcement:** AGENTS.md `scripts/check_tdd_compliance.py` commit-time backstop
**Test:** `test_c15_tdd_test_exists_for_new_source`

### C16 — Collect-check passes before every commit
`make collect-check` MUST show 0 collection errors before any commit with test changes.
**Enforcement:** Makefile pre-commit hook + `make collect-check`
**Test:** `test_c16_collect_check_before_commit`

### C17 — Never commit generated files
Generated files (`__pycache__/`, `.pyc`, `.egg-info/`, dist outputs) MUST NOT be committed.
**Enforcement:** AGENTS.md `.gitignore` + pre-commit check
**Test:** `test_c17_never_commit_generated_files`

### C18 — Commit author is preserved
Commits MUST preserve the correct author attribution — never rebase-rewrite authorship.
**Enforcement:** AGENTS.md git conventions
**Test:** `test_c18_commit_author_preserved`

### C19 — Git hooks are installed and active
`make install-hooks` MUST have been run; pre-commit hooks MUST be active before any commit.
**Enforcement:** Makefile `make install-hooks` + pre-commit config
**Test:** `test_c19_git_hooks_installed_and_active`

### C20 — No amend commits to shared branches
`git commit --amend` MUST NOT be used on commits already pushed to shared branches.
**Enforcement:** AGENTS.md branch discipline
**Test:** `test_c20_no_amend_shared_branch_commits`

### C21 — Commit count verified before push
Before pushing, the agent MUST verify the number of unpushed commits with `make git-log`.
**Enforcement:** Makefile `make batch-push` threshold check
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
**Enforcement:** AGENTS.md `enforce-clean-tree.ts` + AGENTS.md
**Test:** `test_c27_clean_tree_before_new_work`

### C28 — Stale gate invalidates commit readiness
A `.gate-status` older than the last source file modification MUST block commit.
**Enforcement:** Makefile `_gate-fresh-check` stale detection
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
**Enforcement:** AGENTS.md `scripts/check_dead_code.py` (planned) + gate-audit
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
**Enforcement:** AGENTS.md `enforce-make.ts` long-op foreground deny
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
**Enforcement:** AGENTS.md `enforce-no-wait.ts` CI_POLL_DISPATCH_PATTERNS
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
**Enforcement:** AGENTS.md `enforce-deadline.ts` + `task_watchdog.py`
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
**Enforcement:** AGENTS.md "Cost-Efficiency Directive" rule 3
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
**Enforcement:** AGENTS.md "Cost-Efficiency Directive" rule 4
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
**Enforcement:** AGENTS.md "Cost-Efficiency Directive" rule 13
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
**Enforcement:** AGENTS.md "Cost-Efficiency Directive" rule 7
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
**Enforcement:** AGENTS.md "Cost-Efficiency Directive" rule 3
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
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` + AGENTS.md
**Test:** `test_a11_verify_state_before_status_claim`

### A12 — Commit hash as evidence for "committed"
Saying "committed" in a response MUST be accompanied by the commit hash from `make git-log`.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` evidence requirement
**Test:** `test_a12_commit_hash_evidence_for_committed`

### A13 — Test pass count as evidence for "tests pass"
Saying "tests pass" MUST include the exact pass count from the test runner output.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` evidence requirement
**Test:** `test_a13_pass_count_evidence_for_tests_pass`

### A14 — CI run ID as evidence for "CI green"
Saying "CI green" MUST include the CI run ID and conclusion from `make ci-verdict`.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` evidence requirement
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
**Enforcement:** AGENTS.md `enforce-no-suppressions.ts` editor block + `scripts/check_tdd_compliance.py` scan
**Test:** `test_n01_no_lint_suppression_comments`

### N02 — Fix the underlying issue, never silence the linter
When a linter complains, repair the code so the linter is satisfied — do NOT paste a suppression comment.
**Enforcement:** AGENTS.md "No Lint-Suppression Comments" policy
**Test:** `test_n02_fix_underlying_issue_not_suppress`

### N03 — Tight types: no `Any` in new code
`Any` usage in type annotations for new code is forbidden — use `object`, generics, or specific types.
**Enforcement:** Makefile `make check-types` + type-safety skill
**Test:** `test_n03_no_any_in_new_code`

### N04 — Type annotations on all public functions
All public functions in `src/general_ludd/` MUST have complete type annotations.
**Enforcement:** AGENTS.md mypy `--disallow-untyped-defs` + AGENTS.md
**Test:** `test_n04_type_annotations_on_all_public_functions`

### N05 — Node v26 compat: no `catch { try {` in plugins
Plugin files MUST NOT use the forbidden `catch { try {` or `catch (e) { try {` patterns.
**Enforcement:** AGENTS.md `scripts/check_node_v26_compat.py` + `make check-node-v26-compat`
**Test:** `test_n05_no_catch_try_in_plugins`

### N06 — Node v26 compat: no type-annotated catch variables
Plugin files MUST NOT use `catch (e: TypeError)` — use `catch (e)` and typeof checks instead.
**Enforcement:** AGENTS.md `scripts/check_node_v26_compat.py`
**Test:** `test_n06_no_typed_catch_variables`

### N07 — Node v26 compat: no enums or namespaces
Plugin files MUST NOT use TypeScript `enum` or `namespace` — use `const` objects instead.
**Enforcement:** AGENTS.md `scripts/check_node_v26_compat.py`
**Test:** `test_n07_no_enums_or_namespaces`

### N08 — No duplicate Makefile targets
The Makefile MUST NOT declare any target more than once.
**Enforcement:** AGENTS.md `scripts/check_duplicate_targets.py` + `make check-duplicate-targets`
**Test:** `test_n08_no_duplicate_makefile_targets`

### N09 — Makefile targets use lowercase-with-hyphens
Makefile target names MUST follow the `lowercase-with-hyphens` convention.
**Enforcement:** AGENTS.md naming conventions
**Test:** `test_n09_makefile_targets_lowercase_hyphens`

### N10 — Python code follows ruff rules
All Python code MUST pass `make lint` (ruff) with zero errors.
**Enforcement:** Makefile `make lint` as gate prerequisite
**Test:** `test_n10_python_code_passes_ruff`

### N11 — Python imports follow isort ordering
All Python imports MUST follow isort ordering (standard library, third-party, local).
**Enforcement:** Makefile `make lint` ruff isort rules
**Test:** `test_n11_python_imports_isort_ordering`

### N12 — No wildcard imports
`from module import *` is FORBIDDEN in production code.
**Enforcement:** Makefile `make lint` ruff F403/F405 rules
**Test:** `test_n12_no_wildcard_imports`

### N13 — No mutable default arguments
Function signatures MUST NOT use mutable default arguments (`def foo(x=[])`).
**Enforcement:** AGENTS.md ruff B006 rule
**Test:** `test_n13_no_mutable_default_arguments`

### N14 — Docstrings on public modules and functions
Public modules and functions MUST have docstrings explaining their purpose and parameters.
**Enforcement:** AGENTS.md ruff D rules + AGENTS.md
**Test:** `test_n14_docstrings_on_public_code`

### N15 — File length under 1000 lines
No single `.py` file under `src/` SHOULD exceed 1000 lines — split into modules if larger.
**Enforcement:** AGENTS.md code style
**Test:** `test_n15_file_length_under_1000_lines`

### N16 — Function length under 50 lines
No single function SHOULD exceed 50 lines — extract helpers for larger functions.
**Enforcement:** Makefile `make lint` ruff PLR0915 (too-many-statements) rule
**Test:** `test_n16_function_length_under_50_lines`

### N17 — No bare except clauses
`except:` without specifying an exception type is FORBIDDEN — use `except Exception:` at minimum.
**Enforcement:** AGENTS.md ruff E722 rule
**Test:** `test_n17_no_bare_except_clauses`

### N18 — Secrets never hardcoded
Secrets (API keys, tokens, passwords) MUST never appear as string literals in source code.
**Enforcement:** Makefile `make secrets-scan` + `.secrets.baseline`
**Test:** `test_n18_secrets_never_hardcoded`

### N19 — Plugin code uses ES module syntax
All `.opencode/plugin/*.ts` files MUST use `import`/`export` syntax — no `require()`.
**Enforcement:** AGENTS.md `scripts/check_node_v26_compat.py` + AGENTS.md
**Test:** `test_n19_plugin_code_es_module_syntax`

### N20 — Plugin code is import-only (no require)
All `.opencode/plugin/*.ts` files MUST use ES module `import` exclusively — zero `require()` calls.
**Enforcement:** AGENTS.md `scripts/check_node_v26_compat.py`
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
**Enforcement:** AGENTS.md test-quality skill
**Test:** `test_n23_test_function_naming_convention`

### N24 — Constants use UPPER_CASE
Module-level constants MUST use UPPER_CASE_UNDERSCORE naming.
**Enforcement:** Makefile `make lint` ruff N816 rule
**Test:** `test_n24_constants_use_upper_case`

### N25 — Private names use leading underscore
Non-public module internals MUST use a leading underscore (`_internal_function`).
**Enforcement:** AGENTS.md Python convention + ruff rules
**Test:** `test_n25_private_names_leading_underscore`

### N26 — No overlapping variable names in scopes
Variable names MUST NOT shadow names in outer scopes.
**Enforcement:** AGENTS.md ruff F402/A001/A002/A003 rules
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
**Enforcement:** AGENTS.md `enforce-objective.ts` + `enforce-stop.ts` CI cache usage
**Test:** `test_k18_ci_state_cached_in_tmp`

### K19 — Knowledge files are readable by all agents
AGENTS.md, SESSION.md, TASKS.md, BUGS.md MUST be human-readable markdown at the repo root.
**Enforcement:** AGENTS.md File existence and format
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
**Enforcement:** AGENTS.md `enforce-objective.ts` + AGENTS.md objective policy
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
**Enforcement:** AGENTS.md `enforce-make.ts` + `opencode.json` permission block
**Test:** `test_u17_bash_make_only`

### U18 — No metacharacters in bash commands
`|`, `;`, `&&`, `||`, `$()`, backticks, `>`, `<` are FORBIDDEN in bash tool calls.
**Enforcement:** AGENTS.md `enforce-make.ts` metacharacter deny
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
**Enforcement:** AGENTS.md `scripts/check_disk_usage.py` + `make check-disk`
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
**Enforcement:** AGENTS.md `enforce-enhancement-ratio.ts` + AGENTS.md Enhancement/Fix Dispatch Ratio
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
**Enforcement:** Makefile `_gate-fresh-check` on all commit targets
**Test:** `test_z02_no_ci_bypass_all_commits_gate_checked`

### Z03 — Release completeness is verified before "shipped"
`make verify-release-completeness TAG=<tag>` MUST pass with all 12 artifact categories before claiming shipped.
**Enforcement:** Makefile `make release-cut` step 4 + AGENTS.md release policy
**Test:** `test_z03_release_completeness_verified_before_shipped`

### Z04 — No broken windows: every violation is fixed
When a policy violation is detected, it MUST be fixed immediately — no "we'll fix it later."
**Enforcement:** AGENTS.md "If you found it, you own it" + Self-Directed Work Rule
**Test:** `test_z04_no_broken_windows_every_violation_fixed`

### Z05 — Gate green before every merge
No branch merge (to master, development, or release) may proceed without green gate on the source branch.
**Enforcement:** Makefile `_gate-fresh-check` + merge target prerequisites
**Test:** `test_z05_gate_green_before_merge`

### Z06 — Enforcement plugins prevent, not just detect
Every enforcement plugin MUST block violations — advisory-only plugins are a gap.
**Enforcement:** AGENTS.md "All enforcement plugins are BLOCKING"
**Test:** `test_z06_enforcement_plugins_prevent_not_detect`

### Z07 — All plugins are hot-reload capable
Every enforcement plugin MUST implement the hot-reload proxy pattern.
**Enforcement:** AGENTS.md `loadHotModule()` usage in each plugin
**Test:** `test_z07_all_plugins_hot_reload_capable`

### Z08 — All plugins have subagent guard
Every enforcement plugin MUST skip enforcement when `OPENCODE_SUBAGENT=1` or subagent detected.
**Enforcement:** AGENTS.md `isSubagent()` check at top of every hook
**Test:** `test_z08_all_plugins_have_subagent_guard`

### Z09 — All plugins fail open
Any error in a plugin MUST allow the tool call or text through — never throw/block on error.
**Enforcement:** AGENTS.md "Fail-open" + try/catch in every plugin hook
**Test:** `test_z09_all_plugins_fail_open`

### Z10 — All plugins have disable env var
Every enforcement plugin MUST be disableable via a `GLUDD_*_ENFORCE=0` env var.
**Enforcement:** AGENTS.md env var check in each plugin
**Test:** `test_z10_all_plugins_have_disable_env_var`

### Z11 — Node v26 compat: all plugins parse clean
All `.opencode/plugin/*.ts` files MUST pass `make check-node-v26-compat` with zero violations.
**Enforcement:** AGENTS.md `scripts/check_node_v26_compat.py` + gate prerequisite
**Test:** `test_z11_all_plugins_node_v26_compat`

### Z12 — No require() calls in any plugin
All plugins MUST use ES module `import` exclusively — zero `require()` calls.
**Enforcement:** AGENTS.md `scripts/check_node_v26_compat.py`
**Test:** `test_z12_no_require_in_any_plugin`

### Z13 — Zero-downtime enforcement evolution
Plugin changes MUST NOT create gaps in enforcement coverage — new plugin supersedes old before old is removed.
**Enforcement:** AGENTS.md guardrail integrity + plugin port map
**Test:** `test_z13_zero_downtime_enforcement_evolution`

### Z14 — Every spec has an enforcement mechanism
Every numbered spec in BEHAVIORAL_SPECS.md MUST map to at least one enforcement mechanism.
**Enforcement:** AGENTS.md `tests/unit/test_behavioral_specs.py` structural check
**Test:** `test_z14_every_spec_has_enforcement_mechanism`

### Z15 — Every enforcement mechanism has a structural test
Every enforcement mechanism (plugin, make target, policy section) MUST have a corresponding structural test.
**Enforcement:** AGENTS.md `tests/unit/test_behavioral_specs.py` coverage verification
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
**Enforcement:** AGENTS.md `tests/unit/test_behavioral_specs.py` structural pin
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
**Enforcement:** AGENTS.md `agent_watchdog.py` + `watchdog.ts`
**Test:** `test_z21_watchdog_detects_unjams_stops`

### Z22 — Task watchdog kills stale subagent tasks
`scripts/task_watchdog.py` MUST kill subagent tasks exceeding their deadline (default 5 min).
**Enforcement:** Makefile `make task-watchdog-start` + deadline enforcement
**Test:** `test_z22_task_watchdog_kills_stale_tasks`

### Z23 — No single point of enforcement failure
If one plugin fails, others MUST continue enforcing — enforcement is not dependent on any single plugin.
**Enforcement:** AGENTS.md plugin independence + fail-open guards
**Test:** `test_z23_no_single_point_of_enforcement_failure`

### Z24 — Enforcement state is persistent and recoverable
State files (`/tmp/gludd-*.json`) MUST be JSON parseable and recoverable by `make crash-recovery`.
**Enforcement:** AGENTS.md JSON format + crash-recovery target
**Test:** `test_z24_enforcement_state_persistent_recoverable`

### Z25 — Zero unregistered plugins
Every `.opencode/plugin/*.ts` file MUST be registered in `opencode.json`.
**Enforcement:** AGENTS.md `tests/unit/test_behavioral_specs.py` plugin registration check
**Test:** `test_z25_zero_unregistered_plugins`

### Z26 — Plugin manifest is current
The plugin manifest (coverage table in this doc) MUST be updated when plugins are added or removed.
**Enforcement:** AGENTS.md + this coverage matrix
**Test:** `test_z26_plugin_manifest_is_current`

### Z27 — Enforcement applies equally to all models
Enforcement plugins MUST NOT have model-specific bypasses — same rules for sonnet, opus, haiku, deepseek.
**Enforcement:** plugin logic is model-agnostic
**Test:** `test_z27_enforcement_applies_to_all_models`

### Z28 — Zero known enforcement gaps at session end
At session end, there MUST be no known enforcement gaps (unimplemented plugins) — all gaps must be in ratchet.
**Enforcement:** AGENTS.md `config/ratchet.yml` + AGENTS.md
**Test:** `test_z28_zero_known_enforcement_gaps_at_session_end`

### Z29 — Spec coverage is tracked by test
The test file `test_behavioral_specs.py` MUST assert on every spec group's existence and coverage.
**Enforcement:** AGENTS.md `tests/unit/test_behavioral_specs.py` group coverage tests
**Test:** `test_z29_spec_coverage_tracked_by_test`

### Z30 — 1000 specs (25 groups × 25-100 specs each), all enforceable, all tested
BEHAVIORAL_SPECS.md MUST contain exactly 1000 specs across 25 groups, all with enforcement and tests.
**Enforcement:** AGENTS.md `tests/unit/test_behavioral_specs.py` count assertion + AGENTS.md
**Test:** `test_z30_1000_specs_all_enforceable_all_tested`

---

## Group H — Hard-Break Discipline (H01–H100)

### H01 — Primary objective unmet = no side fixes
Never fix incidental bugs, lint warnings, or code-quality issues when the PRIMARY OBJECTIVE is unmet.
**Enforcement:** AGENTS.md "Priority Stacking (AND not OR)" + `enforce-objective.ts`
**Test:** `test_h01_no_side_fixes_when_objective_unmet`

### H02 — CI RED is the top blocker — fix nothing else
When CI is RED, ALL other work stops until CI is GREEN — no feature work, no refactors, no audits.
**Enforcement:** AGENTS.md "Commit-After-Green Policy" + `enforce-stop.ts` CI check
**Test:** `test_h02_ci_red_top_blocker`

### H03 — Never push after a single fix
Fixes MUST be batched with other work — never push a lone fix commit that will cancel CI.
**Enforcement:** Makefile `_push-rate-guard` + AGENTS.md "Don't Push Every Commit"
**Test:** `test_h03_never_push_after_single_fix`

### H04 — Batch all fixes into one push cycle
All pending fixes MUST be accumulated locally and pushed together in a single `make batch-push`.
**Enforcement:** Makefile `batch-push` threshold + AGENTS.md
**Test:** `test_h04_batch_all_fixes_one_push`

### H05 — CI completion is #1 priority over all other work
When CI is running, the agent's #1 priority is ensuring it completes green — everything else is secondary.
**Enforcement:** AGENTS.md "Primary objective drives task prioritization" + SESSION.md
**Test:** `test_h05_ci_completion_is_top_priority`

### H06 — Never start a feature while release is incomplete
If a version lacks published artifacts (`verify-release-completeness` fails), no new feature work begins.
**Enforcement:** AGENTS.md "Never bump to the next version while current lacks green release"
**Test:** `test_h06_no_feature_while_release_incomplete`

### H07 — Blocked objective blocks all tangential work
When the PRIMARY OBJECTIVE is blocked (CI red, release incomplete, gate red), all non-objective-advancing tool calls are denied.
**Enforcement:** AGENTS.md `enforce-objective.ts` BLOCKING mode + AGENTS.md
**Test:** `test_h07_blocked_objective_blocks_tangential_work`

### H08 — Gate red = fix gate before anything else
When `.gate-status` is FAILED, the ONLY permitted work is fixing the gate — no commits, no features, no refactors.
**Enforcement:** Makefile `_gate-fresh-check` + `enforce-objective.ts`
**Test:** `test_h08_gate_red_fix_before_anything_else`

### H09 — Objective-driven: every tool call must advance the objective
Before making a mutating tool call (edit/write/bash), the agent MUST verify it advances the PRIMARY OBJECTIVE.
**Enforcement:** AGENTS.md `enforce-objective.ts` tool.execute.before check
**Test:** `test_h09_every_tool_call_advances_objective`

### H10 — Undispatched work is a blocking condition
If the subagent pool is below the floor (10), refilling it is #1 priority — no main-thread work until refilled.
**Enforcement:** AGENTS.md `enforce-floor.ts` + `enforce-multitask.ts`
**Test:** `test_h10_undispatched_work_blocking_condition`

### H11 — No commits on a red gate — no exceptions
Zero commits may land when `.gate-status` is FAILED or missing — not even "just a doc fix" or "just a version bump".
**Enforcement:** Makefile `_gate-fresh-check` enforced on all commit targets
**Test:** `test_h11_no_commits_on_red_gate`

### H12 — Hard-break on PREMATURE STOP patterns
When the agent would stop with pending work, the stop plugin MUST hard-block — no fallback, no advisory.
**Enforcement:** AGENTS.md `enforce-stop.ts` BLOCKING mode (not advisory)
**Test:** `test_h12_hard_break_on_premature_stop`

### H13 — Never ship a release from a red commit
Release-cut MUST abort if `require-ci-green` fails — no bypass, no FORCE=1 for master when gate is red.
**Enforcement:** AGENTS.md `scripts/require_ci_green.py` + `make release-cut` step 0
**Test:** `test_h13_never_ship_from_red_commit`

### H14 — Triage before treatment: classify issue, then fix
Before fixing any issue, classify it: (a) blocks objective → fix NOW, (b) can be batched → add to TASKS.md, (c) informational → note and move on.
**Enforcement:** AGENTS.md Priority Stacking — first dispatch is the priority
**Test:** `test_h14_triage_before_treatment`

### H15 — Fix causes, not symptoms
When CI goes RED, fix the FAILING TEST — never skip, xfail, or lower coverage to make it green.
**Enforcement:** AGENTS.md "Fix means repair, never disable" + `_test-disabled-guard`
**Test:** `test_h15_fix_causes_not_symptoms`

### H16 — No "while you're at it" tangents during critical path
When on the critical path (CI fix, release cut, gate repair), "while you're at it" improvements are forbidden.
**Enforcement:** AGENTS.md `enforce-objective.ts` — tangential tool calls denied when objective unmet
**Test:** `test_h16_no_while_youre_at_it_on_critical_path`

### H17 — Dispatch capacity is reserved for objective work
When the objective is unmet, all 10 dispatch slots MUST be filled with objective-advancing tasks — no filler.
**Enforcement:** AGENTS.md "Subagent slots are precious" + `enforce-objective.ts`
**Test:** `test_h17_dispatch_capacity_reserved_for_objective`

### H18 — Unreleased code is not "done"
A feature committed to master is NOT done until it's in a CI-green release with published artifacts.
**Enforcement:** AGENTS.md "A Release is an Artifact, Not a Tag"
**Test:** `test_h18_unreleased_code_is_not_done`

### H19 — Staged rollback: if objective blocker can't be fixed, route around it
If a blocker (CI red, gate red) cannot be fixed directly, document the ratchet entry AND continue other objective work — never stop entirely.
**Enforcement:** AGENTS.md `config/ratchet.yml` + AGENTS.md "Constraints Are To Engineer Around"
**Test:** `test_h19_staged_rollback_if_blocker_cant_be_fixed`

### H20 — No status-only responses on critical path
When the objective is unmet, a text-only status response is a HARD BLOCK — tool call required.
**Enforcement:** AGENTS.md `enforce-stop.ts` `hasRealPendingWork()` text blanking
**Test:** `test_h20_no_status_only_on_critical_path`

### H21 — Never wait for user direction when objective is clear
If the PRIMARY OBJECTIVE is set in SESSION.md, the agent MUST pursue it without asking "what should I do next?"
**Enforcement:** AGENTS.md "Never Block on Questions" + `enforce-stop.ts`
**Test:** `test_h21_never_wait_for_user_direction`

### H22 — Broken CI pipeline blocks all pushes
If >3 cancelled runs detected in the last 2 hours, ALL pushes are blocked until the thrash is resolved.
**Enforcement:** Makefile `_push-rate-guard` thrash detection
**Test:** `test_h22_broken_ci_pipeline_blocks_all_pushes`

### H23 — Local validation is the real gate — CI is confirmation
Never defer to CI for validation that `make gate-lite` could provide locally in 5 minutes.
**Enforcement:** AGENTS.md "Prefer local validation" + `make gate-lite`
**Test:** `test_h23_local_validation_is_real_gate`

### H24 — Never fix a plugin that's "too strict" by removing enforcement
When a plugin denies legitimate work, narrow the check — NEVER delete the enforcement block.
**Enforcement:** AGENTS.md Guardrail Integrity Policy + "Fix Means Repair, Never Disable"
**Test:** `test_h24_never_remove_enforcement_for_noise`

### H25 — Hard-block on push when CI in-flight on same branch
Push to a branch with `in_progress` CI MUST be hard-denied by `_push-rate-guard` — no advisory.
**Enforcement:** Makefile `_push-rate-guard` CI-in-flight check → exit 1 (block)
**Test:** `test_h25_hard_block_push_when_ci_in_flight`

### H26 — Every PUSH resets the CI-wait clock
After a push, work continues IMMEDIATELY — no waiting, no "let me check CI first" on the main thread.
**Enforcement:** AGENTS.md "Background Operations NEVER Block Dispatch"
**Test:** `test_h26_every_push_resets_ci_wait_clock`

### H27 — Objective tracking is mandatory — no memory-based prioritization
Task prioritization MUST derive from SESSION.md's PRIMARY OBJECTIVE, not from agent memory or recent conversation.
**Enforcement:** AGENTS.md `enforce-objective.ts` + SESSION.md read at session start
**Test:** `test_h27_objective_tracking_mandatory`

### H28 — Fix-forward: never revert to dodge a failure
When a commit breaks CI, FIX the breakage — never `git revert` to erase the change and keep the bug.
**Enforcement:** AGENTS.md "Fix-forward on the branch" + Branch Discipline
**Test:** `test_h28_fix_forward_never_revert_to_dodge`

### H29 — Never let the gate go unrun for >1 hour of active work
If source files change and >60 minutes pass without a fresh gate, a gate MUST be launched before further work.
**Enforcement:** Makefile `_gate-fresh-check` stale detection + AGENTS.md
**Test:** `test_h29_gate_must_run_within_hour_of_changes`

### H30 — Gate failure is the agent's problem — not the user's
When `.gate-status` is FAILED, the agent MUST fix it without asking the user — the user should never see a red gate from their perspective.
**Enforcement:** AGENTS.md "If you found it, you own it" + Self-Directed Work Rule
**Test:** `test_h30_gate_failure_is_agents_problem`

### H31 — Release pipeline is single-threaded per version
Only one release operation (cut, promote, recut) may be in-flight per version at a time.
**Enforcement:** AGENTS.md release pipeline serialization + `make release-cut` sequential steps
**Test:** `test_h31_release_pipeline_single_threaded`

### H32 — Never start a second release while first is in-progress
While `make release-cut` is running or CI build is in-flight for version N, version N+1 work MUST NOT begin.
**Enforcement:** AGENTS.md "Never bump version while current lacks artifacts"
**Test:** `test_h32_no_second_release_while_first_in_progress`

### H33 — Completion criteria are binary, not fuzzy
A task is either COMPLETED (gate green + evidence pasted) or NOT COMPLETED — there is no "mostly done" or "essentially complete."
**Enforcement:** AGENTS.md "Completion = Green Gate + TASKS.md Evidence"
**Test:** `test_h33_completion_criteria_binary_not_fuzzy`

### H34 — Stop conditions are exhaustion of real work, not fatigue
The ONLY valid stop condition is: no unchecked TASKS.md items, ratchet empty, gate green, CI green, releases complete.
**Enforcement:** AGENTS.md `enforce-stop.ts` `hasRealPendingWork()` comprehensive check
**Test:** `test_h34_stop_conditions_exhaustion_not_fatigue`

### H35 — Subagent pool must be full before main-thread work
Every session turn with pending work MUST start by ensuring the subagent pool is at the 10-agent floor.
**Enforcement:** AGENTS.md `enforce-floor.ts` + `enforce-multitask.ts` streak counter
**Test:** `test_h35_subagent_pool_full_before_main_thread`

### H36 — No gate skipping for "small" changes
Even a one-line typo fix or a commit message change MUST pass `_gate-fresh-check` — no exception for "trivial" changes.
**Enforcement:** Makefile `_gate-fresh-check` on ALL commit targets
**Test:** `test_h36_no_gate_skipping_for_small_changes`

### H37 — Multiple concurrent gate failures = systemic issue
If `make gate` fails 3+ times consecutively on the same branch, it's a systemic issue — stop and diagnose, don't retry.
**Enforcement:** AGENTS.md root-cause analysis + ratchet entry for systemic failures
**Test:** `test_h37_multiple_gate_failures_systemic_issue`

### H38 — Never hold a subagent slot with polling work
A subagent whose only task is "poll X until Y" wastes a slot for the entire poll duration — never dispatch one.
**Enforcement:** AGENTS.md `enforce-no-wait.ts` CI_POLL_DISPATCH_PATTERNS
**Test:** `test_h38_never_hold_slot_with_polling`

### H39 — Interruptible: any long operation must be killable
Every foreground operation MUST be interruptible (killable via SIGTERM) — no uninterruptible loops on the main thread.
**Enforcement:** Makefile `make task CMD='...'` timeout wrapper + `task_watchdog.py`
**Test:** `test_h39_long_operations_must_be_killable`

### H40 — Self-healing: detect own enforcement violations and fix
When the agent's own behavior triggers an enforcement plugin, the agent MUST self-correct in the next turn.
**Enforcement:** AGENTS.md Self-Directed Work Rule + enforcement feedback loop
**Test:** `test_h40_self_healing_detect_own_violations`

### H41 — No recovery without diagnosis
Before recovering from a failure, the agent MUST read the failure output — never blindly retry without understanding.
**Enforcement:** AGENTS.md Root-Cause-Only Fix Policy
**Test:** `test_h41_no_recovery_without_diagnosis`

### H42 — Push guards are not speed bumps — they are hard walls
`_push-rate-guard` MUST return non-zero to block push — a warning that still allows the push is a bug.
**Enforcement:** AGENTS.md `scripts/ci_push_guard.py` fail-closed
**Test:** `test_h42_push_guards_are_hard_walls_not_speed_bumps`

### H43 — Deadlines are non-negotiable
Subagent tasks exceeding 5 minutes MUST be killed — no extension, no "it's almost done" exception.
**Enforcement:** AGENTS.md `enforce-deadline.ts` + `scripts/task_watchdog.py`
**Test:** `test_h43_deadlines_are_non_negotiable`

### H44 — CI pipeline health = agent pipeline health
CI status on master IS the agent's status — a red master CI means the agent is failing, regardless of local state.
**Enforcement:** AGENTS.md "Completion = Green Gate + TASKS.md Evidence" — CI-green is part of done
**Test:** `test_h44_ci_pipeline_health_is_agent_health`

### H45 — Feature work waits for CI green master
New feature development MUST NOT start while master CI is red — fix master CI first, then develop.
**Enforcement:** AGENTS.md "Never start a feature while release is incomplete" — CI is release-adjacent
**Test:** `test_h45_feature_work_waits_for_ci_green_master`

### H46 — Hard-break on commit bypass detection
Any commit that lands without `_gate-fresh-check` MUST be detected and flagged as a policy violation.
**Enforcement:** AGENTS.md `tests/unit/test_commit_gate_freshness.py` structural check
**Test:** `test_h46_hard_break_on_commit_bypass`

### H47 — State-file integrity under failure
If an enforcement state file (`/tmp/gludd-*.json`) is corrupt, the plugin MUST fail closed — not silently default to allow.
**Enforcement:** plugin fail-open for exceptions, fail-closed for corrupt state
**Test:** `test_h47_state_file_integrity_under_failure`

### H48 — Objective completion = stop enforcing objective
When `isObjectiveMet()` returns true for the PRIMARY OBJECTIVE, objective enforcement MUST disengage completely.
**Enforcement:** AGENTS.md `enforce-objective.ts` early return on objective met
**Test:** `test_h48_objective_completion_disengages_enforcement`

### H49 — Never create a new branch while master is red
Branch creation (feature, release, worktree) MUST be blocked when master CI is red or gate is failed.
**Enforcement:** AGENTS.md "Release branch starts from CI-green base" + branch creation guards
**Test:** `test_h49_no_new_branch_while_master_red`

### H50 — Maximum one broken thing at a time
The agent MUST fix one broken thing completely before moving to the next — never accumulate multiple half-fixes.
**Enforcement:** AGENTS.md "Complete one objective, then start the next" + TASKS.md ordering
**Test:** `test_h50_max_one_broken_thing_at_a_time`

### H51 — Never push untested code
Code that has not passed `make gate-lite` locally MUST NOT be pushed to any shared branch.
**Enforcement:** Makefile `_gate-fresh-check` on all push paths + AGENTS.md
**Test:** `test_h51_never_push_untested_code`

### H52 — Pre-push gate-lite is mandatory
Before any push to master or development, `make gate-lite` MUST pass — minimum local validation.
**Enforcement:** Makefile push targets include gate-lite as prerequisite
**Test:** `test_h52_pre_push_gate_lite_mandatory`

### H53 — Gate-lite failure blocks push
If `make gate-lite` fails, push is denied — no bypass, no "it's just a small change".
**Enforcement:** Makefile gate-lite as push-target prerequisite
**Test:** `test_h53_gate_lite_failure_blocks_push`

### H54 — Never bypass gate-lite for push
FORCE=1 MUST NOT bypass `make gate-lite` — it may bypass CI check but never local validation.
**Enforcement:** Makefile push targets: gate-lite check before force bypass
**Test:** `test_h54_never_bypass_gate_lite_for_push`

### H55 — Hard-break on write-to-wrong-branch
Writing or committing to master when the intention was development MUST be blocked by the branch plugin.
**Enforcement:** AGENTS.md `enforce-branch-discipline.ts` block wrong-branch mutations
**Test:** `test_h55_hard_break_write_to_wrong_branch`

### H56 — Release-cut from non-master branch is blocked
`make release-cut` MUST verify current branch is master before proceeding — non-master release cuts are denied.
**Enforcement:** Makefile `release-cut` branch check
**Test:** `test_h56_release_cut_from_non_master_blocked`

### H57 — Broken-window: every unchecked violation invites more
A single unaddressed policy violation (red gate, skipped test, bypassed hook) MUST be fixed within the same session.
**Enforcement:** AGENTS.md "No broken windows" + `enforce-stop.ts` pending-work detection
**Test:** `test_h57_broken_window_every_violation_fixed_same_session`

### H58 — Discovery of a gap = ownership of the fix
Finding a gap in enforcement, coverage, or process means the agent OWNs fixing it — no handoff, no deferral.
**Enforcement:** AGENTS.md "If you found it, you own it" + Self-Directed Work Rule
**Test:** `test_h58_discovery_of_gap_is_ownership_of_fix`

### H59 — Hard-break on trying to override user intent
An agent action that contradicts a direct user instruction MUST be blocked, even if the agent thinks it's "better".
**Enforcement:** AGENTS.md Instruction-Following Priority + `enforce-objective.ts` objective-read
**Test:** `test_h59_hard_break_on_override_user_intent`

### H60 — Fix timeline: CI red → diagnosis within 5 minutes
When CI goes red, the agent MUST diagnose the failure within 5 minutes of detection.
**Enforcement:** AGENTS.md priority stacking + deadline enforcement
**Test:** `test_h60_ci_red_diagnosis_within_5_minutes`

### H61 — Never push during a release pipeline
While `make release-cut` or any release operation is in-flight, ALL other pushes to the same branch are denied.
**Enforcement:** Makefile `_push-rate-guard` release-pipeline-in-flight check
**Test:** `test_h61_never_push_during_release_pipeline`

### H62 — Master branch integrity: no commits from outside pipeline
The ONLY way a commit reaches master is: feature → development → master merge OR release pipeline promotion.
**Enforcement:** AGENTS.md branch discipline + `enforce-branch-discipline.ts`
**Test:** `test_h62_master_integrity_only_pipeline_commits`

### H63 — Audit at every 10th commit
A self-audit MUST run after every 10 source-file commits — not optional, not deferrable.
**Enforcement:** AGENTS.md Self-Audit Policy + automated trigger
**Test:** `test_h63_audit_at_every_10th_commit`

### H64 — No commit without TASKS.md update
Every commit that advances work MUST update the corresponding TASKS.md entry — unlogged commits are lost work.
**Enforcement:** AGENTS.md Task Self-Tracking + `enforce-stop.ts` commit block
**Test:** `test_h64_no_commit_without_tasks_md_update`

### H65 — Ratchet-only entries are work, not documentation
Every entry in `config/ratchet.yml` represents UNFINISHED WORK — not a known-acceptable gap. Must burn down to zero.
**Enforcement:** AGENTS.md `enforce-stop.ts` ratchet entries count as pending work
**Test:** `test_h65_ratchet_entries_are_work_not_documentation`

### H66 — Burn-down ratchet before declaring any phase complete
No project phase (A, B, C) may be declared complete while that phase's ratchet entries remain open.
**Enforcement:** AGENTS.md phase completion criteria + ratchet burn-down
**Test:** `test_h66_burn_down_ratchet_before_phase_complete`

### H67 — Every gate failure is investigated, not just documented
When `.gate-status` is FAILED, the agent MUST read the gate log, identify the failing test/check, and fix it.
**Enforcement:** AGENTS.md "Gate failure must surface the log" + root-cause fix
**Test:** `test_h67_gate_failure_investigated_not_documented`

### H68 — No commit left behind: every commit must have a purpose
Branches with unmerged commits that do not advance a TASKS.md item MUST be cleaned up or justified.
**Enforcement:** AGENTS.md atomic commits + `make development-status`
**Test:** `test_h68_no_commit_left_behind`

### H69 — Dispatch wave balance: fix-to-enhancement ratio ≥ 1:1
Every dispatch wave MUST include at least 50% enhancement tasks, not just fixes — by mechanical enforcement.
**Enforcement:** AGENTS.md `enforce-enhancement-ratio.ts` BLOCKING mode
**Test:** `test_h69_dispatch_wave_balance_fix_enhancement_ratio`

### H70 — Hard block on fix-only dispatch waves
A dispatch wave with 0 enhancement tasks when enhancements exist in TASKS.md MUST be denied.
**Enforcement:** AGENTS.md `enforce-enhancement-ratio.ts` text.complete violation
**Test:** `test_h70_hard_block_fix_only_dispatch_wave`

### H71 — Session-start TOOL CALL before any prose
The first user message's response MUST include a tool call, never prose-only — even for a Q&A question.
**Enforcement:** AGENTS.md `enforce-session-start.ts` + AGENTS.md Session Start Protocol
**Test:** `test_h71_session_start_tool_call_before_prose`

### H72 — No forward progress while backwards motion exists
If a test previously passing now fails, NO new features may be developed until the regression is fixed.
**Enforcement:** Makefile `_test-disabled-guard` + AGENTS.md regression policy
**Test:** `test_h72_no_progress_while_regression_exists`

### H73 — Never mark a TASKS.md item complete without evidence
A TASKS.md `[x]` without a commit hash, test count, or artifact URL is a false claim.
**Enforcement:** AGENTS.md task evidence requirements + `enforce-verified-claims.ts`
**Test:** `test_h73_no_tasks_complete_without_evidence`

### H74 — Hard-break on claim/evidence mismatch
When a response says "tests pass" but the pasted output shows failures, the response MUST be blanked.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` done-words without matching evidence
**Test:** `test_h74_hard_break_claim_evidence_mismatch`

### H75 — Incremental progress over perfect solutions
When blocked between "perfect solution (2 hours)" and "working fix (5 minutes)", the working fix wins.
**Enforcement:** AGENTS.md "Constraints Are To Engineer Around" — workaround over paralysis
**Test:** `test_h75_incremental_progress_over_perfect_solutions`

### H76 — Never let perfect be the enemy of done
A working fix with a ratchet entry for the ideal solution is VALID — a blocked agent waiting for the ideal is NOT.
**Enforcement:** AGENTS.md `config/ratchet.yml` — defer non-blocking improvements
**Test:** `test_h76_never_let_perfect_be_enemy_of_done`

### H77 — Parallelize read-only audits with coding work
Audit tasks (dead-code scan, wiring check, migration audit) MUST run in parallel with coding subagents — never serialized.
**Enforcement:** AGENTS.md "Cost-Efficiency Directive" — research serialized, coding ≤2, audits can be either
**Test:** `test_h77_parallelize_read_only_audits_with_coding`

### H78 — Subagent health check: observable output in <2 min
If a subagent produces no observable output for >2 minutes, the orchestrator MUST consider it stalled and re-dispatch.
**Enforcement:** AGENTS.md `enforce-deadline.ts` detection + `scripts/task_watchdog.py` kill
**Test:** `test_h78_subagent_must_produce_output_within_2_min`

### H79 — Never punish the user for agent mistakes
When the agent makes a mistake, the fix MUST NOT require user intervention.
**Enforcement:** AGENTS.md "Broken CI pipeline is the agent's problem" + Self-Directed Work Rule
**Test:** `test_h79_never_punish_user_for_agent_mistakes`

### H80 — Master's gate is THE source of truth for project health
When local gate passes but master CI is red, the project is RED — local gate passing does not override CI.
**Enforcement:** AGENTS.md "Trust gate output, not SESSION.md" — both gates, CI is authoritative
**Test:** `test_h80_master_gate_is_source_of_truth`

### H81 — Feature branches must be CI-validated before merge
Before `make feature-done` merges into master, the feature branch MUST have a CI-green verdict on its own tip.
**Enforcement:** Makefile `feature-done` CI check prerequisite
**Test:** `test_h81_feature_branches_ci_validated_before_merge`

### H82 — CI RED diagnosis takes priority over ALL background work
When a CI-red notification arrives, the agent MUST context-switch: finish current atomic operation, then diagnose CI.
**Enforcement:** AGENTS.md priority stacking — CI red is top priority
**Test:** `test_h82_ci_red_diagnosis_priority_over_background`

### H83 — Never close a session with uncommitted, unpushed verified fixes
At session end, all verified fixes MUST be committed and pushed — no "I'll push next time."
**Enforcement:** AGENTS.md `enforce-stop.ts` pending-work detection + AGENTS.md
**Test:** `test_h83_never_close_session_with_unpushed_fixes`

### H84 — Broken main-thread tools = broken agent
If main-thread tools (read, edit, write, bash) fail consistently, the agent MUST diagnose and fix before dispatching subagents.
**Enforcement:** AGENTS.md 4-step diagnosis for tool unavailability
**Test:** `test_h84_broken_main_thread_tools_broken_agent`

### H85 — Plugin self-test before every enforcement change
Before modifying any enforcement plugin, run `make test-hook-runtime` and verify 0 failures.
**Enforcement:** AGENTS.md "Self-Test Quality" + TDD for plugins
**Test:** `test_h85_plugin_self_test_before_enforcement_change`

### H86 — Never hardcode a bypass for CI/gate in production code
A FORCE=1 or COMMIT_THRESHOLD=1 in a script or Makefile target is a hardcoding bug.
**Enforcement:** AGENTS.md audit for hardcoded bypasses
**Test:** `test_h86_never_hardcode_bypass_in_production`

### H87 — Failure mode documentation required for every plugin
Every enforcement plugin MUST document what happens when it fails (fail-open, fail-closed, error surface).
**Enforcement:** plugin source code comments + AGENTS.md enforcement table
**Test:** `test_h87_failure_mode_documentation_for_every_plugin`

### H88 — Disable path must be documented and tested
Every `GLUDD_*_ENFORCE=0` disable path MUST have a corresponding test verifying the disable actually works.
**Enforcement:** AGENTS.md "Self-Test Quality" + `make test-hook-runtime`
**Test:** `test_h88_disable_path_documented_and_tested`

### H89 — Gate-run cadence: at least once per 5 source commits
After every 5 source-file commits with no fresh gate, a new gate MUST be launched.
**Enforcement:** Makefile `_gate-fresh-check` commit-count-based staleness
**Test:** `test_h89_gate_run_cadence_per_5_commits`

### H90 — Multi-project awareness: never apply fix for project A to project B
Each project maintained by gludd has its own config — a fix for one project MUST NOT be auto-applied to another.
**Enforcement:** AGENTS.md project-collection precedence contract
**Test:** `test_h90_multi_project_awareness_no_cross_apply`

### H91 — Hard-break: never push a commit that removes a test
A commit that deletes a test function without replacing it MUST be denied.
**Enforcement:** Makefile `_test-disabled-guard` pre-commit check
**Test:** `test_h91_never_push_commit_that_removes_test`

### H92 — Coverage per-file: no file drops below 50%
No source file under `src/general_ludd/` may have <50% test coverage — if found, it's a blocking gap.
**Enforcement:** Makefile `make audit-coverage` + `make gate-audit` per-file threshold
**Test:** `test_h92_coverage_per_file_min_50_percent`

### H93 — Never add a dependency without security audit
Adding a new PyPI or npm dependency MUST include `make pip-audit` — unvetted deps are denied.
**Enforcement:** AGENTS.md security pipeline + `make security`
**Test:** `test_h93_never_add_dep_without_security_audit`

### H94 — SBOM must be current within 24 hours of any dependency change
After a dependency change, `make sbom` MUST be re-run and the SBOM committed within 24 hours.
**Enforcement:** Makefile `make sbom` + AGENTS.md security requirements
**Test:** `test_h94_sbom_current_within_24_hours`

### H95 — Secret scanning on every commit
Every commit MUST pass `make secrets-scan` — a new secret in a commit is a hard block.
**Enforcement:** Makefile `.secrets.baseline` + pre-commit hook + `make secrets-scan`
**Test:** `test_h95_secret_scanning_on_every_commit`

### H96 — No baseline expansion without human approval
Adding new entries to `.secrets.baseline` MUST be reviewed by a human — not auto-baselined.
**Enforcement:** AGENTS.md + pre-commit hook baseline-change detection
**Test:** `test_h96_no_baseline_expansion_without_approval`

### H97 — Hard-break: outdated baseline is same as no baseline
If `.secrets.baseline` is >7 days old, `make secrets-scan` MUST fail — stale baseline is a gap.
**Enforcement:** Makefile `make secrets-scan` baseline staleness check
**Test:** `test_h97_outdated_baseline_is_same_as_no_baseline`

### H98 — Hard-break on concurrent gate runs
Two instances of `make gate` running simultaneously MUST be prevented — second launch is denied.
**Enforcement:** AGENTS.md `enforce-make.ts` concurrent-gate block
**Test:** `test_h98_hard_break_concurrent_gate_runs`

### H99 — Clean shutdown: no abandoned subagents
When the orchestrator exits, all active subagents MUST be TaskStopped — no orphaned subagents left running.
**Enforcement:** AGENTS.md + watchdog cleanup + `make clean-tmp`
**Test:** `test_h99_clean_shutdown_no_abandoned_subagents`

### H100 — Session boundary is not a task boundary
A session end does NOT reset task state — all pending TASKS.md items carry forward to the next session.
**Enforcement:** AGENTS.md session persistence + SESSION.md carry-forward
**Test:** `test_h100_session_boundary_is_not_task_boundary`

---

## Group V — Verification Discipline (V01–V100)

### V01 — Every status claim requires machine-produced evidence
The words "done", "passing", "green", "fixed", "shipped" in agent output MUST be accompanied by pasted tool output proving it.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` text.complete BLOCKING check
**Test:** `test_v01_every_status_claim_requires_machine_evidence`

### V02 — `make verify-state` before any status claim
Before claiming any project state, the agent MUST run `make verify-state` and paste its output.
**Enforcement:** AGENTS.md "Mandatory verification command" + `enforce-verified-claims.ts`
**Test:** `test_v02_verify_state_before_status_claim`

### V03 — Never report CI status without headSha match
A CI verdict whose `headSha` does not match the current branch tip is STALE — must not be reported as current.
**Enforcement:** Makefile `make ci-verdict` stale-run detection + AGENTS.md
**Test:** `test_v03_never_report_ci_without_headsha_match`

### V04 — Commit hash required with every "committed" claim
The word "committed" MUST be accompanied by a 7+ character hex commit hash from `make git-log`.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` commit-hash evidence regex
**Test:** `test_v04_commit_hash_required_with_committed`

### V05 — Test pass count required with every "tests pass" claim
"tests pass" MUST include the exact pass count (e.g. "342 passed") from test runner output.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` pass-count evidence regex
**Test:** `test_v05_pass_count_required_with_tests_pass`

### V06 — CI run ID required with every "CI green" claim
"CI green" MUST include the CI run ID and `conclusion: success` from `make ci-verdict` output.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` CI evidence regex
**Test:** `test_v06_ci_run_id_required_with_ci_green`

### V07 — Artifact URL required with every "shipped" claim
"shipped" or "released" MUST include a download URL from `gh release view` showing `isDraft: false`.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` release evidence requirement
**Test:** `test_v07_artifact_url_required_with_shipped`

### V08 — Gate PASS evidence required with every "gate green" claim
"gate green" MUST include the `=== GATE: PASSED ===` marker from `.gate-status` or gate output.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` gate evidence regex
**Test:** `test_v08_gate_pass_evidence_required`

### V09 — `VERIFIED <branch>@<sha>` required with every push claim
"pushed" MUST be accompanied by the `VERIFIED <branch>@<sha>` output from `make verify-remote`.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` push evidence regex
**Test:** `test_v09_verified_branch_sha_required_with_push`

### V10 — Unverified status claims are blanked, not warned
When a claim lacks evidence, the response text is BLANKED (not console.warn) — user should never see an unverified claim.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` BLOCKING text blanking
**Test:** `test_v10_unverified_claims_blanked_not_warned`

### V11 — Evidence must appear in the SAME response as the claim
A claim in response N and its evidence in response N+1 is a violation — evidence and claim must co-occur.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` same-message detection
**Test:** `test_v11_evidence_must_appear_same_response_as_claim`

### V12 — Evidence is from tool output, not from agent memory
"Tests passed earlier" is NOT evidence — the test runner output must be pasted fresh, never recalled from memory.
**Enforcement:** AGENTS.md "No claim made from memory alone"
**Test:** `test_v12_evidence_from_tool_output_not_memory`

### V13 — CI evidence must be from `make ci-verdict`, not from memory
A CI status claim MUST quote `make ci-verdict` or `make ci-verdict-safe` output — never "I checked earlier."
**Enforcement:** AGENTS.md evidence-based response policy
**Test:** `test_v13_ci_evidence_from_ci_verdict_not_memory`

### V14 — Gate evidence must be from `.gate-status`, not from SESSION.md
A gate status claim MUST quote `.gate-status` file or `make gate-status` output — not SESSION.md's last-known.
**Enforcement:** AGENTS.md "Trust gate output, not SESSION.md"
**Test:** `test_v14_gate_evidence_from_gate_status_not_session`

### V15 — Release evidence must be from `make verify-release-completeness`
A release completeness claim MUST quote `make verify-release-completeness TAG=<tag>` — not `verify-release-artifact`.
**Enforcement:** AGENTS.md "verify-release-completeness is the real gate"
**Test:** `test_v15_release_evidence_from_verify_completeness`

### V16 — Never rephrase or paraphrase evidence — paste it verbatim
Tool output must be pasted AS-IS — summarizing, truncating, or rewording the output invalidates the evidence.
**Enforcement:** AGENTS.md evidence-based response — raw output requirement
**Test:** `test_v16_never_rephrase_evidence_paste_verbatim`

### V17 — Stale evidence is false evidence
A `.gate-status` older than the last source edit MUST NOT be used as evidence — it is stale.
**Enforcement:** Makefile `_gate-fresh-check` stale detection + AGENTS.md
**Test:** `test_v17_stale_evidence_is_false_evidence`

### V18 — Verify in the claiming-response, not in a prior response
Evidence pasted in response N-1 and claimed in response N is a violation — evidence must be in the CLAIMING response.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` same-response evidence detection
**Test:** `test_v18_verify_in_claiming_response_not_prior`

### V19 — Negative claims also require evidence
"CI is NOT green", "tests are NOT passing" — negative claims also require `make ci-verdict` or `make test` output.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` negative-claim detection + evidence
**Test:** `test_v19_negative_claims_also_require_evidence`

### V20 — Evidence is machine-readable and parseable
Evidence output MUST be in a format that can be mechanically validated (exit code, structured output, regex-detectable markers).
**Enforcement:** AGENTS.md evidence format requirements
**Test:** `test_v20_evidence_machine_readable_and_parseable`

### V21 — Multiple claims in one response require multiple evidence tokens
A response claiming "tests pass, CI green, and gate clean" needs all three evidence tokens present.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` per-claim-type evidence requirement
**Test:** `test_v21_multiple_claims_require_multiple_evidence`

### V22 — Evidence chain: release requires CI evidence requires gate evidence
A release claim must transitively carry: gate PASS, CI success, artifact URLs — the full evidence chain.
**Enforcement:** AGENTS.md "Done" Claims evidence table — full chain required
**Test:** `test_v22_evidence_chain_release_requires_full_chain`

### V23 — Cross-verify: local gate + CI gate must agree
If local gate is PASS and CI is FAILED, CI is authoritative — NEVER report "gate green" when CI contradicts.
**Enforcement:** AGENTS.md "Master's gate is THE source of truth" + evidence hierarchy
**Test:** `test_v23_cross_verify_local_and_ci_must_agree`

### V24 — Verification is not optional, not best-effort, not "when convenient"
Every claim of project state MUST be verified — the verification command is the claim's prerequisite, not an optional extra.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` BLOCKING (not advisory)
**Test:** `test_v24_verification_is_not_optional`

### V25 — Self-verification: agent's own state is verified before reporting
Before reporting "I completed X", the agent MUST verify X with a tool call — not assume from intent.
**Enforcement:** AGENTS.md Self-Audit Policy + `enforce-verified-claims.ts`
**Test:** `test_v25_self_verification_before_reporting`

### V26 — Verification data is timestamped
Every verification output MUST include a timestamp showing when the measurement was taken.
**Enforcement:** Makefile `verify-state` includes timestamp + `make gate-status` shows time
**Test:** `test_v26_verification_data_is_timestamped`

### V27 — Verification is reproducible
The same verification command run twice on the same state MUST produce the same verdict — no non-deterministic verification.
**Enforcement:** AGENTS.md deterministic verification requirement
**Test:** `test_v27_verification_is_reproducible`

### V28 — Verification commands are idempotent
Running a verification command twice MUST NOT change state — `make verify-state`, `make gate-status`, `make ci-verdict-safe` are read-only.
**Enforcement:** Makefile read-only enforcement for verification targets
**Test:** `test_v28_verification_commands_are_idempotent`

### V29 — Human-in-the-loop verification for irreversible claims
Claims about pushed releases, deleted resources, or merged branches SHOULD surface a human verification step.
**Enforcement:** AGENTS.md release-cut confirmation step + `make release-cut` prompts
**Test:** `test_v29_human_in_the_loop_for_irreversible_claims`

### V30 — Difference between verification and validation both needed
Verification = "did we build the thing right?" (tests, lint). Validation = "did we build the right thing?" (user acceptance). Both needed.
**Enforcement:** AGENTS.md completion criteria cover both
**Test:** `test_v30_difference_between_verification_and_validation`

### V31 — Evidence format: `KEY: value` for machine parseability
Verification output SHOULD use machine-parseable format: `FIELD: value` patterns for each data point.
**Enforcement:** Makefile `make verify-state` structured output
**Test:** `test_v31_evidence_format_key_value_for_machine_parseability`

### V32 — No hedging language in verification claims
"seems green", "looks like it passed" without tool evidence is a false claim — must be blanked.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` hedging-language detection
**Test:** `test_v32_no_hedging_language_in_verification`

### V33 — Evidence counter: N evidence tokens per status claim
Every response MUST contain at least as many evidence tokens as status claims — tracked mechanically.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` evidence-per-claim ratio
**Test:** `test_v33_evidence_counter_tokens_per_claim`

### V34 — Verification gap detection: claims without prior verification command
If a response claims "CI green" but no `make ci-verdict` was run in recent turns, it's a verification gap.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` (planned extension) + AGENTS.md
**Test:** `test_v34_verification_gap_detection`

### V35 — Third-party verification data is cited with source
When claiming data from GitHub API, PyPI, or other external sources, the tool output MUST be pasted, not paraphrased.
**Enforcement:** AGENTS.md evidence-based response + `enforce-verified-claims.ts`
**Test:** `test_v35_third_party_verification_cited_with_source`

### V36 — Commit verification: `make git-log` output before/after commit
Before and after a commit, `make git-log` output MUST be captured to verify what changed.
**Enforcement:** AGENTS.md commit evidence requirements
**Test:** `test_v36_commit_verification_git_log_before_after`

### V37 — Tag verification: tag exists on remote after tag-push
After `make git-tag-push`, verify the tag exists on remote via `git ls-remote` equivalent.
**Enforcement:** Makefile `make verify-release-completeness` includes tag existence check
**Test:** `test_v37_tag_verification_after_tag_push`

### V38 — Branch verification: verify branch existence and HEAD before operations
Before merging, rebasing, or deleting a branch, verify it exists and its HEAD points to the expected SHA.
**Enforcement:** AGENTS.md branch discipline + merge safety
**Test:** `test_v38_branch_verification_before_operations`

### V39 — Remote verification: always verify push landed on intended branch
After `make batch-push`, `make verify-remote BRANCH=<b> SHA=<sha>` MUST run and return match.
**Enforcement:** AGENTS.md "Verify the remote after every push" + Makefile
**Test:** `test_v39_remote_verification_after_push`

### V40 — Artifact verification: check file size, hash, and downloadability
After publishing a release asset, verify it's downloadable, non-zero size, and hash matches build artifact.
**Enforcement:** AGENTS.md `scripts/verify_release_completeness.py` artifact checks
**Test:** `test_v40_artifact_verification_size_hash_download`

### V41 — Zero-size artifact detection
A release artifact with size 0 bytes MUST be detected and the release marked incomplete.
**Enforcement:** Makefile `verify-release-completeness` zero-size check
**Test:** `test_v41_zero_size_artifact_detection`

### V42 — Version stamp verification: artifact names match tag version
Release asset filenames MUST contain the version string matching the release tag.
**Enforcement:** Makefile `verify-release-completeness` version-stamp check
**Test:** `test_v42_version_stamp_verification`

### V43 — Platform build verification: all platform builds completed
A release MUST have artifacts for all declared target platforms — a missing Linux build is an incomplete release.
**Enforcement:** Makefile `verify-release-completeness` 12-category check
**Test:** `test_v43_platform_build_verification_all_completed`

### V44 — Prerelease flag verification: tag shape matches prerelease status
A `-alpha` or `-beta` tag MUST have `prerelease: true`; a plain semver tag MUST have `prerelease: false`.
**Enforcement:** Makefile `make verify-release-completeness` prerelease-flag check
**Test:** `test_v44_prerelease_flag_verification`

### V45 — Verification automation: no manual verification step
Every verification MUST be runnable via `make <target>` — no "check GitHub releases page manually" steps.
**Enforcement:** AGENTS.md No-Manual-Default Policy + Makefile verification targets
**Test:** `test_v45_verification_automation_no_manual_steps`

### V46 — Verification cadence: before every commit, push, merge, and release
The 4 gating points (commit, push, merge, release) each MUST have a verification step — not skippable.
**Enforcement:** Makefile all commit/push/merge/release targets include prerequisite checks
**Test:** `test_v46_verification_cadence_4_gating_points`

### V47 — Failed verification MUST block the gated action
If `_gate-fresh-check` fails, the commit MUST be denied — a warning that still commits is a bug.
**Enforcement:** Makefile `_gate-fresh-check` exit non-zero → Makefile aborts
**Test:** `test_v47_failed_verification_blocks_gated_action`

### V48 — Verification output is structured for downstream consumption
`make verify-state` and `make gate-status` output MUST be parseable by scripts, not just human-readable.
**Enforcement:** Makefile structured output format
**Test:** `test_v48_verification_output_structured`

### V49 — Verification history is preserved
Each `make verify-state` run's output MUST be logged with timestamp to `.gate-logs/verify-<ts>.log`.
**Enforcement:** Makefile verify-state logging
**Test:** `test_v49_verification_history_preserved`

### V50 — Anomaly detection on verification data
When gate went from PASS to FAIL without source changes, flag it as an anomaly — don't silently accept.
**Enforcement:** AGENTS.md + `make gate-status` change detection
**Test:** `test_v50_anomaly_detection_on_verification_data`

### V51 — CI-failure verification: extract exact failing test names
When CI is red, the agent MUST run `make ci-failed-tests RUN=<id>` to get exact failing tests before diagnosing.
**Enforcement:** Makefile `ci-failed-tests` + AGENTS.md
**Test:** `test_v51_ci_failure_verification_extract_failing_tests`

### V52 — Gate-failure verification: read the gate log, not just the status
When `make gate-status` shows FAILED, read the gate log to find which phase failed — not just report "gate red."
**Enforcement:** AGENTS.md "Gate failure must surface the log" + `make gate-status-check`
**Test:** `test_v52_gate_failure_verification_read_log`

### V53 — Lint-failure verification: extract exact error count and file
When lint fails, report the exact error count, file, and line — not "lint found issues."
**Enforcement:** Makefile `make lint` structured output + AGENTS.md evidence requirements
**Test:** `test_v53_lint_failure_verification_extract_exact`

### V54 — Typecheck verification: report baseline deltas, not just pass/fail
When typecheck passes at baseline, report error count vs baseline — when it regresses, report new errors.
**Enforcement:** Makefile `make typecheck` baseline-aware output + AGENTS.md
**Test:** `test_v54_typecheck_verification_report_baseline_deltas`

### V55 — Coverage verification: per-file deltas, not just overall percentage
Coverage reports MUST show per-file coverage, not just the aggregate — 85% overall can hide 0% on a new file.
**Enforcement:** Makefile `make audit-coverage` per-file threshold + AGENTS.md
**Test:** `test_v55_coverage_verification_per_file_deltas`

### V56 — Test-count verification: collection errors are NOT test failures
`make test-count` output showing "N collected" with errors MUST be treated as a verification failure, not "0 failures."
**Enforcement:** Makefile `make collect-check` + AGENTS.md "test-failures masked collection ERRORs"
**Test:** `test_v56_test_count_verification_collection_errors`

### V57 — Verification chain: lint → typecheck → collect-check → test → coverage → gate
The verification suite MUST run in this order — later-phase failure does not skip earlier phases in the report.
**Enforcement:** Makefile `gate` target ordered prerequisites
**Test:** `test_v57_verification_chain_ordered_phases`

### V58 — Partial verification is misleading — all-or-nothing reporting
Reporting "lint and typecheck pass" when tests were not run is misleading — always report the full suite status.
**Enforcement:** AGENTS.md evidence requirements + `make verify-state` comprehensive
**Test:** `test_v58_partial_verification_is_misleading`

### V59 — Verification SLA: gate within 60 min of source changes
If source files change, a full gate verification MUST complete within 60 minutes — it goes stale after that.
**Enforcement:** Makefile `_gate-fresh-check` 60-minute staleness threshold
**Test:** `test_v59_verification_sla_gate_within_60_min`

### V60 — Verification backlog: never accumulate >3 unverified commits
After 3 commits without a fresh gate, further commits are blocked until gate runs.
**Enforcement:** Makefile `_gate-fresh-check` commit-count threshold
**Test:** `test_v60_verification_backlog_max_3_unverified_commits`

### V61 — Cross-branch verification: feature branch tested before merge
Before merging a feature branch into development/master, its gate MUST be green on the feature branch tip.
**Enforcement:** Makefile `make feature-done` gate check + `make gated-merge` preconditions
**Test:** `test_v61_cross_branch_verification_before_merge`

### V62 — Verification scope matches change scope — no exceptions
A one-line typo fix requires the same verification suite as a 100-line refactor — no scoping exceptions.
**Enforcement:** Makefile `_gate-fresh-check` on ALL commits, regardless of diff size
**Test:** `test_v62_verification_scope_matches_change_scope`

### V63 — Assume unverified = broken
When a verification has not been run, the default assumption is BROKEN — not PASS, not "probably fine."
**Enforcement:** AGENTS.md fail-closed philosophy + `_gate-fresh-check` missing = fail
**Test:** `test_v63_assume_unverified_equals_broken`

### V64 — Verification-is-evidence-only: no verification-backed speculation
Having run `make test` for commit A does NOT verify commit B's state — each state requires its own verification.
**Enforcement:** AGENTS.md stale evidence rule
**Test:** `test_v64_verification_is_evidence_only_no_speculation`

### V65 — Verification cost awareness: prefer fast checks first
Run lint (seconds) before typecheck (minutes) before test (tens of minutes) — fail fast, don't run everything if lint fails.
**Enforcement:** Makefile `gate` target fail-fast ordering
**Test:** `test_v65_verification_cost_awareness_fast_first`

### V66 — Verification parallelism: independent checks run concurrently
Lint, typecheck, and collect-check are independent — they SHOULD run in parallel within the gate, not serially.
**Enforcement:** Makefile `gate` target parallel phases where possible
**Test:** `test_v66_verification_parallelism_independent_concurrent`

### V67 — Verification cache: don't re-verify unchanging state
If `.gate-status` is fresh and no source files changed since it was written, re-verification may be skipped.
**Enforcement:** Makefile `_gate-fresh-check` mtime-based freshness
**Test:** `test_v67_verification_cache_dont_reverify_unchanging`

### V68 — Verification invalidation: what changes trigger re-verification
Any change to `src/`, `tests/`, `config/`, `.opencode/plugin/`, `Makefile`, or `pyproject.toml` invalidates the gate cache.
**Enforcement:** Makefile `_gate-fresh-check` multi-directory mtime check
**Test:** `test_v68_verification_invalidation_triggers`

### V69 — Verification baseline: the first gate on a new branch
The first `make gate` on a freshly-created branch establishes the baseline — subsequent commits compare against it.
**Enforcement:** AGENTS.md gate baseline tracking + `_gate-fresh-check`
**Test:** `test_v69_verification_baseline_first_gate_on_new_branch`

### V70 — Verification regression detection: new failures vs pre-existing
When gate goes from PASS to FAIL, the agent MUST distinguish new failures (from recent commits) from pre-existing ones.
**Enforcement:** AGENTS.md root-cause analysis + gate log diff
**Test:** `test_v70_verification_regression_detection_new_vs_existing`

### V71 — Environment verification: verify tools before starting work
`make verify-state` at session start MUST verify tool availability (python, uv, ruff, mypy, gh) before dispatching work.
**Enforcement:** AGENTS.md session start protocol + `make verify-state`
**Test:** `test_v71_environment_verification_before_work`

### V72 — Plugin verification: `make test-hook-runtime` before plugin changes
Before modifying any `.opencode/plugin/*.ts`, run `make test-hook-runtime` to confirm current state is known-good.
**Enforcement:** AGENTS.md TDD for plugins + `make test-hook-runtime`
**Test:** `test_v72_plugin_verification_test_hook_runtime_before_changes`

### V73 — Node-v26 verification: `make check-node-v26-compat` after plugin edits
After any plugin edit, `make check-node-v26-compat` MUST pass before the edit is committed.
**Enforcement:** Makefile `make check-node-v26-compat` as gate prerequisite on plugin changes
**Test:** `test_v73_node_v26_verification_after_plugin_edits`

### V74 — Security verification: `make security` before any merge to master
`make security` (sast + sbom + pip-audit) MUST run before merging development to master.
**Enforcement:** Makefile `make gated-merge` includes security check + AGENTS.md
**Test:** `test_v74_security_verification_before_merge`

### V75 — Submodule verification: `make submodule-status` before any build
All git submodules MUST be at pinned commits before building — uncommitted submodule changes fail the build.
**Enforcement:** Makefile `make submodule-status` + AGENTS.md
**Test:** `test_v75_submodule_verification_before_build`

### V76 — Dependency verification: `make pip-audit` weekly minimum
Dependencies MUST be audited for vulnerabilities at least weekly — a stale pip-audit is a security gap.
**Enforcement:** AGENTS.md security cadence + `make pip-audit`
**Test:** `test_v76_dependency_verification_weekly_minimum`

### V77 — Verification surface: every API endpoint has a healthcheck
Every daemon endpoint exposed externally MUST have a corresponding healthcheck that verifies it's reachable.
**Enforcement:** AGENTS.md observability invariant + `/healthz` endpoint
**Test:** `test_v77_verification_surface_every_endpoint_healthcheck`

### V78 — Readiness verification: `/readyz` before marking daemon as up
The daemon's `/readyz` endpoint MUST pass (database connected, secrets available, config loaded) before "daemon is up."
**Enforcement:** Makefile `make smoke` daemon readiness check + AGENTS.md
**Test:** `test_v78_readiness_verification_before_daemon_up`

### V79 — Live verification: don't just check at startup, check continuously
Daemon health MUST be verified continuously (watchdog) — not just at startup. A crash 5 minutes after boot is still a crash.
**Enforcement:** AGENTS.md `agent_watchdog.py` continuous monitoring + healthcheck polling
**Test:** `test_v79_live_verification_continuous_not_startup_only`

### V80 — Database verification: migration state matches code
Before daemon startup, verify the Alembic revision head matches the code's expected head — migration drift fails startup.
**Enforcement:** AGENTS.md migration audit + daemon startup check
**Test:** `test_v80_database_verification_migration_matches_code`

### V81 — Config verification: all required config keys present with valid values
Before daemon startup, all required config keys MUST be present with valid, non-default-overridden values.
**Enforcement:** AGENTS.md daemon config validation at startup
**Test:** `test_v81_config_verification_required_keys_present`

### V82 — Secrets verification: OpenBao/hvac reachable before daemon startup
Before the daemon serves requests, it MUST verify it can reach the OpenBao instance and retrieve test secrets.
**Enforcement:** AGENTS.md daemon secrets validation at startup + AGENTS.md
**Test:** `test_v82_secrets_verification_openbao_reachable`

### V83 — Model verification: model gateway functional before dispatch
Before dispatching any model subagent, verify the model gateway responds to a ping — dead gateway = no dispatches.
**Enforcement:** Makefile `make smoke` model check + AGENTS.md known trap (no-op executor)
**Test:** `test_v83_model_verification_gateway_functional`

### V84 — Build verification: `make dist` succeeds before release cut
`make dist` MUST succeed before `make release-cut` — broken dist aborts the release.
**Enforcement:** Makefile `make release-cut` includes dist check step
**Test:** `test_v84_build_verification_dist_before_release`

### V85 — Container verification: `make container-build` succeeds before version bump
The container build MUST complete successfully before claiming the version is ready for release.
**Enforcement:** Makefile `make release-cut` CI pipeline includes container build
**Test:** `test_v85_container_verification_before_version_bump`

### V86 — Terraform verification: `make tf-validate` for all stacks before apply
Every Terraform stack MUST pass `make tf-validate` before `tf-apply` — unvalidated changes are blocked.
**Enforcement:** Makefile `tf-validate` prerequisite for apply targets
**Test:** `test_v86_terraform_verification_before_apply`

### V87 — Ansible verification: `make ansible-syntax` before any playbook run
Playbooks MUST pass syntax validation before execution — a syntax error caught at runtime is a process failure.
**Enforcement:** Makefile `ansible-syntax` as playbook prerequisite
**Test:** `test_v87_ansible_verification_before_playbook_run`

### V88 — Disk verification: `make check-disk` before large ops
Before running worktree creation, gate, or build — check disk usage. >90% disk blocks large ops.
**Enforcement:** AGENTS.md `scripts/check_disk_usage.py` + `make check-disk`
**Test:** `test_v88_disk_verification_before_large_ops`

### V89 — Path verification: all file paths within workspace before access
Before any file access (read/write/edit/glob/grep), verify the path is within the allowed workspace.
**Enforcement:** AGENTS.md "No External File Access" + file-path validation
**Test:** `test_v89_path_verification_within_workspace`

### V90 — URL verification: domain allowlist before webfetch
Before fetching any URL, verify the domain is in the allowed set (github.com, pypi.org, docs.python.org, etc.).
**Enforcement:** AGENTS.md + webfetch domain allowlist (planned)
**Test:** `test_v90_url_verification_domain_allowlist`

### V91 — Tool-call verification: arguments within expected ranges
Before executing a tool call, verify arguments are within expected bounds (file paths exist, numbers in range).
**Enforcement:** plugin tool.execute.before argument validation
**Test:** `test_v91_tool_call_verification_arguments_in_range`

### V92 — Post-merge verification: gate green after merge, not just before
After merging to master, run `make gate` on the merge commit to verify the merge didn't break anything.
**Enforcement:** AGENTS.md merge safety + `make gated-merge` post-merge gate
**Test:** `test_v92_post_merge_verification_gate_green`

### V93 — Post-push verification: CI-triggering confirmed
After pushing, verify that CI was triggered (a new run appears) — a push that didn't trigger CI is silent failure.
**Enforcement:** AGENTS.md "verify-remote" + CI trigger check
**Test:** `test_v93_post_push_verification_ci_triggered`

### V94 — Post-release verification: all 12 asset categories confirmed
After `make release-cut`, ALL 12 artifact categories must be confirmed present, non-zero, and version-stamped.
**Enforcement:** Makefile `make verify-release-completeness TAG=<tag>` 12-category check
**Test:** `test_v94_post_release_verification_12_categories`

### V95 — Verification failure escalation: 3 consecutive fails = human alert
If gate/CI/release verification fails 3 consecutive times, escalate to human (human-todo) — don't retry indefinitely.
**Enforcement:** AGENTS.md Human Todo System + `config/remediation.yml`
**Test:** `test_v95_verification_failure_escalation_human_alert`

### V96 — Verification records are immutable
Once a verification result is written (`.gate-status`, CI run, release metadata), it MUST NOT be retroactively changed.
**Enforcement:** AGENTS.md evidence integrity + append-only log patterns
**Test:** `test_v96_verification_records_are_immutable`

### V97 — Verification audit trail: every verification has a record
Every `make verify-state`, `make gate`, `make ci-verdict` invocation MUST produce a log entry with timestamp + result.
**Enforcement:** Makefile verification logging + `.gate-logs/`
**Test:** `test_v97_verification_audit_trail_every_record`

### V98 — Verification reporting: red/yellow/green with specifics
Verification status MUST be reported as GREEN (all pass), RED (≥1 failure), or YELLOW (in-progress/unknown) — with specifics.
**Enforcement:** Makefile `make gate-status` + `make ci-verdict` structured output
**Test:** `test_v98_verification_reporting_red_yellow_green`

### V99 — Verification timeout: don't hang indefinitely on a hung verification
Verification commands MUST have timeouts — a hung `make test` should not block the pipeline forever.
**Enforcement:** Makefile `make task CMD='...'` timeout + `task_watchdog.py`
**Test:** `test_v99_verification_timeout_dont_hang_indefinitely`

### V100 — Verification coverage: every spec (H01–Y100) has a verifiable test
Every behavioral spec MUST have a corresponding test that verifies the enforcement mechanism exists and functions.
**Enforcement:** AGENTS.md `tests/unit/test_behavioral_specs.py` + `make test-hook-runtime`
**Test:** `test_v100_verification_coverage_every_spec_has_test`

---

# Group J — Judgment Discipline (J01–J100)

## Theme: User intent overrides all other priorities. Primary objective is THE goal. Side-tasks are forbidden when objective unmet. Decision-making discipline.

---

### J01 — User intent is the sole priority
Every agent decision must begin with: "What did the user ask for?" No other priority — enforcement plugins, floor rules, dispatch ratios — may override or defer the user's explicit request.
**Enforcement:** AGENTS.md "Instruction-Following Priority" section
**Test:** `test_j01_user_intent_is_sole_priority`

### J02 — Primary objective before all else
The user's primary objective must be completed before any self-directed side-work begins. Fixing a discovered gap while the primary ask is unfinished is a judgment error.
**Enforcement:** AGENTS.md "Task Completion Policy"
**Test:** `test_j02_primary_objective_before_side_work`

### J03 — Side-tasks forbidden when objective unmet
A guardrail improvement, dead-code audit, or refactor discovered during primary work must NOT be started until the primary work is committed and verified.
**Enforcement:** AGENTS.md "Task Completion Policy" + enforce-stop.ts pending-work check
**Test:** `test_j03_side_tasks_forbidden_unmet_objective`

### J04 — New instruction stacks, does not replace
When the user issues a new directive mid-session, interpret it ADDITIVELY — the new instruction joins the queue. It does not void previous objectives.
**Enforcement:** AGENTS.md "AND not OR" priority-stacking rule
**Test:** `test_j04_instruction_stacking_not_replacement`

### J05 — Priority determines order, not inclusion
"Low priority" means "do it last," not "skip it." Every pending task must reach terminal state (completed or cancelled).
**Enforcement:** AGENTS.md "Low priority does NOT mean skip it"
**Test:** `test_j05_low_priority_means_last_not_skip`

### J06 — Never abandon a user request
A user request that is difficult, blocked, or ambiguous must be pursued until genuinely impossible — not parked, deferred, or silently dropped between waves.
**Enforcement:** AGENTS.md "Self-Directed Work Rule" + TASKS.md tracking
**Test:** `test_j06_never_abandon_user_request`

### J07 — Written instruction beats agent habit
When AGENTS.md or a plugin says one thing and the user says another, the user wins. Agent habit, convenience, or "standard practice" never overrides an explicit directive.
**Enforcement:** AGENTS.md "Instruction-Following Priority" (overriding instructions)
**Test:** `test_j07_written_instruction_beats_agent_habit`

### J08 — Implicit asks are still asks
"I wonder if..." / "it would be nice if..." / "we should probably..." from the user ARE directives. Treat them as explicit tasks.
**Enforcement:** AGENTS.md "Code the obvious" meta-rule
**Test:** `test_j08_implicit_asks_are_tasks`

### J09 — Do not reinterpret user intent
Never rephrase a user's request into something easier or more familiar. "Fix the CI" does not mean "check CI status and report." "Write tests" does not mean "survey test coverage."
**Enforcement:** AGENTS.md "Subagent Task Design — Fix, Don't Check"
**Test:** `test_j09_do_not_reinterpret_user_intent`

### J10 — User silence does not mean completion
The user not responding is NOT approval, satisfaction, or permission to stop. Work continues until all objectives are met or a hard blocker is reached.
**Enforcement:** AGENTS.md "Never Block on Questions" + enforce-stop.ts idle detection
**Test:** `test_j10_user_silence_not_completion`

---

### J11 — Decide, document assumption, proceed
When hitting a decision point with no explicit user guidance, pick the most reasonable option, state the assumption in one line, and continue. Never block on a question.
**Enforcement:** AGENTS.md "Never Block on Questions — Default to Action"
**Test:** `test_j11_decide_document_assumption_proceed`

### J12 — Two-good-options rule
When two options are both reasonable, pick either and proceed. Analysis-paralysis costs more than picking the slightly worse option.
**Enforcement:** AGENTS.md "Never Block on Questions"
**Test:** `test_j12_two_good_options_rule`

### J13 — Cost of delay exceeds cost of suboptimal choice
A 5-minute decision delay burns main-thread time that could have completed the task. For reversible decisions, the wrong choice reversed is cheaper than indecision.
**Enforcement:** AGENTS.md "Background Operations NEVER Block" + enforce-no-wait.ts
**Test:** `test_j13_cost_of_delay_exceeds_suboptimal`

### J14 — Reversible decisions have a 30-second timeout
If a decision is easily reversible (commit, refactor direction, file name), decide within 30 seconds. If not reversible, spend at most 2 minutes then dispatch a research subagent.
**Enforcement:** enforce-deadline.ts (decision-timeout pattern)
**Test:** `test_j14_reversible_30s_timeout`

### J15 — Escalate only when genuinely blocked
Escalate to the user only when: (a) the decision is genuinely irreversible and destructive, (b) three alternatives have been tried and all failed, or (c) the user explicitly asked to be consulted at this point.
**Enforcement:** AGENTS.md "Human Permission Subjects" (escalation-request: 3 alternatives_tried)
**Test:** `test_j15_escalate_only_when_blocked`

### J16 — Three-alternatives rule for escalation
Before filing a HumanTodo or asking the user, document ≥3 distinct alternatives tried, with approach and outcome for each. Fewer than 3 = invalid escalation.
**Enforcement:** AGENTS.md "Escalation requests" (3 alternatives_tried minimum)
**Test:** `test_j16_three_alternatives_rule`

### J17 — Self-directed work is mandatory, not optional
When you discover a gap, bug, or missing integration while working, you MUST fix it immediately. Do not list the gap and ask permission.
**Enforcement:** AGENTS.md "Self-Directed Work Rule"
**Test:** `test_j17_self_directed_work_mandatory`

### J18 — Gap discovery is ownership
If you found it, you own it. Fix it, test it, commit it, then continue the original task. The gap is now yours — there is no "delegate to someone else."
**Enforcement:** AGENTS.md "Self-Directed Work Rule"
**Test:** `test_j18_gap_discovery_is_ownership`

### J19 — Fix the root cause, not the symptom
Every issue must be addressed at its origin. Disabling a guardrail because it fires noisily fixes the symptom; fixing the logic that causes false-positives fixes the root cause.
**Enforcement:** AGENTS.md "Root-Cause-Only Fix Policy" + AGENTS.md "Fix Means Repair, Never Disable"
**Test:** `test_j19_fix_root_cause_not_symptom`

### J20 — Never weaken enforcement to quiet noise
A guardrail firing too often is a precision bug — narrow the check. A guardrail firing correctly but annoyingly is working as designed — do not touch it. Never reduce enforcement strength to reduce noise.
**Enforcement:** AGENTS.md "Guardrail Integrity Policy" + enforce-no-suppressions.ts
**Test:** `test_j20_never_weaken_enforcement`

---

### J21 — Sonnet is the default model
Use sonnet for all routine work. Use opus only for complex multi-file synthesis that sonnet demonstrably cannot handle. Use haiku for trivial read-only research.
**Enforcement:** AGENTS.md "Model Utilization — Keep Sonnet Dominant" + model_utilization_pretool.sh
**Test:** `test_j21_sonnet_is_default_model`

### J22 — Model choice is cost-weighted
Model selection must consider both capability AND cost. Opus (~5× sonnet $/token) should carry coordination/judgment, not grunt work. Every opus token must justify its premium.
**Enforcement:** AGENTS.md "Keep Opus Lean — Sonnet Carries the Token Load"
**Test:** `test_j22_model_choice_is_cost_weighted`

### J23 — Task complexity drives model choice
Research-only tasks → haiku or sonnet. Single-file edits → sonnet. Multi-file coordinated changes → sonnet or opus. Architecture decisions → opus. Pure grep/read → no model (inline tool).
**Enforcement:** AGENTS.md "Model Utilization" + COST-EFFICIENCY DIRECTIVE
**Test:** `test_j23_task_complexity_drives_model_choice`

### J24 — Never dispatch opus for a grep
A subagent dispatched to "search for X in the codebase" must use haiku or sonnet. Opus dispatched for a search task is a cost bug.
**Enforcement:** AGENTS.md "Cost-Efficiency Directive" rule 8
**Test:** `test_j24_never_dispatch_opus_for_grep`

### J25 — Model selection is reversible mid-wave
If a subagent with the wrong model is half-dispatched, cancel and re-dispatch. The cost of the wrong model exceeds the cost of one cancelled dispatch.
**Enforcement:** AGENTS.md "Cost-Efficiency Directive"
**Test:** `test_j25_model_selection_reversible_mid_wave`

### J26 — Research tasks use cheapest viable model
A research subagent that reads files and summarizes belongs on haiku or sonnet, never opus. Only dispatch opus when synthesis AND judgment are both required.
**Enforcement:** AGENTS.md "Keep Opus Lean"
**Test:** `test_j26_research_uses_cheapest_viable`

### J27 — Subagent model caps at task need
A subagent that edits one test file does not need opus. A subagent that designs a new daemon protocol might. Match model to task, not to habit.
**Enforcement:** AGENTS.md "Cost-Efficiency Directive" model cap rule
**Test:** `test_j27_subagent_model_caps_at_task_need`

### J28 — Token budget awareness per wave
Each dispatch wave has an implicit token budget. 10 opus subagents reading 5000-line files each = budget explosion. Batch size × model cost × expected output = wave cost.
**Enforcement:** AGENTS.md "Cost-Efficiency Directive" hard caps table
**Test:** `test_j28_token_budget_awareness_per_wave`

### J29 — Dispatch terse prompts, not verbose specs
A subagent prompt longer than 20 lines often contains unnecessary context. Subagents need the WHAT and the constraints, not the history of why.
**Enforcement:** AGENTS.md "Cost-Efficiency Directive" rule 2 terse prompts ≤20 lines
**Test:** `test_j29_dispatch_terse_prompts`

### J30 — Subagents return summaries, not dumps
Every subagent prompt must specify: "Read files you need, but return a ≤N-line summary. Do NOT dump large file contents into your response."
**Enforcement:** AGENTS.md "Cost-Efficiency Directive" rule 3
**Test:** `test_j30_subagents_return_summaries`

---

### J31 — Commit when green, not when convenient
A green test suite signals "commit now." Delaying a commit to batch with future work risks losing the green state to a subsequent edit.
**Enforcement:** AGENTS.md "Commit-After-Green Policy" + enforce-make.ts
**Test:** `test_j31_commit_when_green`

### J32 — Never commit red
A commit with failing tests, lint errors, or type errors is a regression — regardless of intent. "Pre-existing failures" are never an excuse to bypass the gate.
**Enforcement:** AGENTS.md "No-Commit-Bypass Policy" + _gate-fresh-check
**Test:** `test_j32_never_commit_red`

### J33 — Local validation before push
Lint + typecheck + collect-check + targeted tests must pass locally before any push. CI is for final validation of batched work, not per-commit testing.
**Enforcement:** AGENTS.md "Don't Push Every Commit" + enforce-clean-tree.ts
**Test:** `test_j33_local_validation_before_push`

### J34 — Batch commits, push once
Accumulate 5+ commits locally, validate with gate-lite between each, then single batch-push. Pushing every commit cancels prior CI runs and produces zero validation.
**Enforcement:** AGENTS.md "Don't Push Every Commit" + _push-rate-guard
**Test:** `test_j34_batch_commits_push_once`

### J35 — No push when CI is in-flight
Pushing while CI is running on the same branch cancels the prior run. Wait for CI to complete or use a different branch.
**Enforcement:** AGENTS.md "Don't Push Every Commit" + ci_check_cooldown.py
**Test:** `test_j35_no_push_when_ci_in_flight`

### J36 — Verify push landed before claiming pushed
After every push, run `make verify-remote BRANCH=<b> SHA=<sha>`. A silent "Everything up-to-date" is a failed push, not a successful one.
**Enforcement:** AGENTS.md "Branch-landing integrity" (b)
**Test:** `test_j36_verify_push_landed`

### J37 — CI verdict is stale if headSha != branch tip
A CI run whose headSha does not match the current branch tip is a STALE run. Its conclusion is meaningless for the current state.
**Enforcement:** AGENTS.md "Branch-landing integrity" (c) + ci-verdict STALE RUN WARNING
**Test:** `test_j37_ci_verdict_stale_check`

### J38 — Release requires artifact, not just tag
A version is NOT shipped until `make verify-release-completeness TAG=<tag>` exits 0. A tag without downloadable assets is a broken release, not a shipped one.
**Enforcement:** AGENTS.md "A Release is an Artifact, Not a Tag"
**Test:** `test_j38_release_requires_artifact`

### J39 — Do not bump version while current is unshipped
"alpha.3 is done, starting alpha.4" is only valid when alpha.3 has a green release and confirmed artifact. Never version-bump past an unshipped release.
**Enforcement:** AGENTS.md "A Release is an Artifact, Not a Tag" rule 2
**Test:** `test_j39_do_not_bump_past_unshipped`

### J40 — Release-cut is the only sanctioned release
`make release-cut TAG='...' MSG='...'` enforces CI-green + README currency + push + tag + artifact verification. Direct tag push bypasses all gates.
**Enforcement:** AGENTS.md "Release Cut = Update the README" + require_ci_green.py
**Test:** `test_j40_release_cut_only_sanctioned`

---

### J41 — Dispatch at floor, not when convenient
When pending work exists and the subagent count is below the floor (10), the next tool call MUST be a dispatch wave. No reads, no edits, no bash — just dispatches.
**Enforcement:** AGENTS.md "10-Agent Dispatch Floor" + enforce-multitask.ts
**Test:** `test_j41_dispatch_at_floor`

### J42 — Refill immediately on completion
The moment a subagent result arrives, dispatch a replacement. Do not wait for the batch to drain before refilling. The floor must stay at 10 continuously.
**Enforcement:** AGENTS.md "Minimum 10 Subagents at All Times" (steady-state dispatch)
**Test:** `test_j42_refill_immediately_on_completion`

### J43 — Never dispatch a read-only status check
"Check CI status," "audit lint," "survey test coverage" are forbidden subagent tasks. A subagent that reads and reports without fixing is a wasted slot.
**Enforcement:** AGENTS.md "Subagent Task Design — Fix, Don't Check" + forbidden phrases table
**Test:** `test_j43_never_dispatch_readonly_check`

### J44 — Every subagent produces a deliverable
A code change, a test file, a commit, a merged PR, a make target — something that persists after the subagent returns. A bullet-point list is not a deliverable.
**Enforcement:** AGENTS.md "Subagent Task Design — Fix, Don't Check" rule 1
**Test:** `test_j44_every_subagent_has_deliverable`

### J45 — Size subagent tasks for 2–5 minutes
Shorter = wasteful dispatch overhead. Longer = deadline risk + slot hogging. Target 2–5 minutes of meaningful work per subagent.
**Enforcement:** AGENTS.md "10-Agent Dispatch Floor" (subagent quality requirements)
**Test:** `test_j45_size_for_2_to_5_minutes`

### J46 — File-editing subagents get worktrees
Any subagent that mutates files MUST work in an isolated git worktree on its own branch. Concurrent edits to the shared tree cause dirty-tree problems, commit races, and misattributed commits.
**Enforcement:** AGENTS.md "Worktree-per-subagent" + enforce-clean-tree.ts
**Test:** `test_j46_file_editing_subagents_get_worktrees`

### J47 — Research subagents stay on main checkout
Read-only research/audit tasks do not need worktree isolation. They save the ~320 MB venv cost and never touch the working tree.
**Enforcement:** AGENTS.md "Worktree-per-subagent" (read-only exception)
**Test:** `test_j47_research_stays_on_main_checkout`

### J48 — Never dispatch two subagents to the same hot file
At most one in-flight agent per hot file (daemon.py, loop.py, gateway.py) at any time. Two agents editing the same file in parallel is a guaranteed conflict.
**Enforcement:** AGENTS.md "Pipeline Orchestration Model" constraint 4a
**Test:** `test_j48_one_agent_per_hot_file`

### J49 — Cap concurrent worktree agents at 6
Each worktree creates ~320 MB venv. More than 6 risks ENOSPC deadlocks. Non-isolated agents for new-file work and read-only tasks avoid the disk cost.
**Enforcement:** AGENTS.md "Pipeline Orchestration Model" constraint 4b + disk-guard
**Test:** `test_j49_cap_worktree_agents_at_six`

### J50 — Dispatch research filler when edit backlog is thin
When fewer than 10 edit tasks exist, fill remaining slots with read-only research/audit/review tasks. They never conflict and are always productive.
**Enforcement:** AGENTS.md "Fill thin waves with read-only research"
**Test:** `test_j50_dispatch_research_filler`

---

### J51 — Classify errors before acting
Before retrying, fixing, or escalating, classify the error: transient (429/503/529) → retry with backoff; deterministic (test failure, lint error) → fix the code; environmental (missing credential, no network) → escalate or route.
**Enforcement:** AGENTS.md "Agent At-Rest / Re-Dispatch Policy" (classify by status table)
**Test:** `test_j51_classify_errors_before_acting`

### J52 — Transient errors get exponential backoff
429 (rate limit), 503 (overloaded), 529 (overload): retry with exponential backoff. First retry: 1s, then 2s, 4s, 8s, 16s, cap at 60s. Never retry without backoff.
**Enforcement:** AGENTS.md "Agent At-Rest" (transient-error-retry-with-backoff)
**Test:** `test_j52_transient_errors_exponential_backoff`

### J53 — Deterministic errors get fixed, never retried
A test failure, lint error, or type error that reproduces every run will never be fixed by retrying. Retrying a deterministic error is a waste of tokens.
**Enforcement:** AGENTS.md "Root-Cause-Only Fix Policy"
**Test:** `test_j53_deterministic_errors_never_retried`

### J54 — Max 3 retries per task
A task that fails 3 times at any backoff interval is not transient — it is broken. Stop retrying and investigate the root cause.
**Enforcement:** AGENTS.md "Agent At-Rest" (max-retry cap 3)
**Test:** `test_j54_max_three_retries_per_task`

### J55 — Failed subagent: re-dispatch, do not abandon
A subagent that failed (not completed-with-partial, but genuinely failed/stalled/died) must be re-dispatched. Abandoning the work creates a silent gap.
**Enforcement:** AGENTS.md "Agent At-Rest" (failed/stalled → re-dispatch)
**Test:** `test_j55_failed_subagent_redispatch`

### J56 — Completed subagent: accept, do not re-dispatch
A subagent that returned `completed` with a deliverable is DONE. Re-dispatching it re-runs finished work, wastes tokens, and can loop forever.
**Enforcement:** AGENTS.md "Agent At-Rest" (completed → accept)
**Test:** `test_j56_completed_subagent_do_not_redispatch`

### J57 — Partial subagent: resume or re-dispatch
A subagent that completed but produced partial/wrong output: either resume via SendMessage (cheaper, keeps context) or re-dispatch fresh if context is stale.
**Enforcement:** AGENTS.md "Agent At-Rest" (completed + partial → resume or re-dispatch)
**Test:** `test_j57_partial_subagent_resume_or_redispatch`

### J58 — ZOMBIE rule: check exit code, not rest event
A background task that "completed" may have been KILLED, not finished. Check actual exit code / result content. Never infer success from the rest event alone.
**Enforcement:** AGENTS.md "Come to rest — what the status means + ZOMBIE rule"
**Test:** `test_j58_zombie_rule_check_exit_code`

### J59 — Never wire auto-relaunch for long tasks
A subagent that re-launches a gate or test suite on every "completion" event can loop indefinitely. Long tasks outlive subagent turns — own them via `run_in_background`, not auto-relaunch.
**Enforcement:** AGENTS.md "ZOMBIE rule" (never arm self-relaunching watcher)
**Test:** `test_j59_never_auto_relaunch_long_tasks`

### J60 — Fail-open on guardrail exceptions
If an enforcement plugin throws an exception, ALLOW the action (fail-open). A broken guardrail is preferable to a wedged editor. Log the error, fix the plugin, never block legitimate work.
**Enforcement:** AGENTS.md "No Lint-Suppression Comments" (fail-open: exception → allow)
**Test:** `test_j60_fail_open_on_guardrail_exceptions`

---

### J61 — Refactor when adding would create debt
When adding a feature would duplicate logic, add a special case to an already-branching function, or violate an existing abstraction: refactor first, then add.
**Enforcement:** AGENTS.md "Refactor if needed, keeping tests green" (TDD policy)
**Test:** `test_j61_refactor_when_adding_creates_debt`

### J62 — Add-to when refactor would exceed task scope
If refactoring would touch 10+ files and the task is a targeted fix, add the fix with a TODO and a follow-up task. Do not let a refactor expand a 30-minute task to a 3-hour one.
**Enforcement:** AGENTS.md (task sizing: 2-5 min subagent tasks)
**Test:** `test_j62_add_to_when_refactor_exceeds_scope`

### J63 — One logical change per commit
Each commit should represent one logical change (one test file, one feature, one fix). Never batch unrelated changes into a single commit.
**Enforcement:** AGENTS.md "Atomic commits" working convention
**Test:** `test_j63_one_logical_change_per_commit`

### J64 — Commit message describes what and why
A commit message must say WHAT changed and WHY. "Fix stuff" / "Update code" / "WIP" are not commit messages — they are placeholders.
**Enforcement:** AGENTS.md (convention: descriptive commit messages)
**Test:** `test_j64_commit_message_what_and_why`

### J65 — Test first, code second, commit third
TDD is the only workflow: write a failing test → confirm it fails → write minimal implementation → confirm it passes → commit. No other sequence is valid.
**Enforcement:** AGENTS.md "CRITICAL: TDD Policy" + enforce-tdd.ts (real-time editor block)
**Test:** `test_j65_test_first_code_second_commit_third`

### J66 — Tests prove behavior exists
A feature without a test that exercises it is indistinguishable from not implemented. "I wrote the code" is not done — "the test passes" is done.
**Enforcement:** AGENTS.md "Done Claims Require Observable Verification Evidence"
**Test:** `test_j66_tests_prove_behavior_exists`

### J67 — Write test before touching src/
The enforce-tdd.ts plugin mechanically blocks edits to `src/general_ludd/` unless a corresponding test file exists. You literally cannot write implementation code until the test file is on disk.
**Enforcement:** enforce-tdd.ts (edit/write deny on src/ without test file)
**Test:** `test_j67_write_test_before_touching_src`

### J68 — Coverage threshold is 85% per modified module
Modules modified in a commit must have ≥85% test coverage. Below 50% is a hard block. The target is 85%.
**Enforcement:** AGENTS.md "TDD Compliance Guardrail" + check_tdd_compliance.py
**Test:** `test_j68_coverage_threshold_85_percent`

### J69 — Never delete a test to make the suite green
If a test fails, the code or the test is wrong. Deleting the test to quiet the suite is a regression — the bug the test caught still exists.
**Enforcement:** AGENTS.md "Fix Means Repair, Never Disable" + Guardrail Integrity Policy
**Test:** `test_j69_never_delete_test_to_make_green`

### J70 — Never xfail a test without root-cause investigation
Marking a test `xfail` or `skip` to make the suite pass without understanding WHY it fails hides a real bug. Investigate, fix, then remove the xfail.
**Enforcement:** AGENTS.md "Root-Cause-Only Fix Policy"
**Test:** `test_j70_never_xfail_without_investigation`

---

### J71 — Use grep/glob/read for single searches — never dispatch
Dispatching a subagent to search for a class name burns 100× the tokens of using the grep tool directly. Single searches are inline tool calls.
**Enforcement:** AGENTS.md "Cost-Efficiency Directive" rule 7
**Test:** `test_j71_single_search_is_inline_not_dispatch`

### J72 — Use write/edit for single-file changes — never dispatch
A one-line fix in a known file does not need a subagent. Inline the edit. Subagents are for multi-step tasks that need reasoning across files.
**Enforcement:** AGENTS.md "Cost-Efficiency Directive" rule 5
**Test:** `test_j72_single_file_change_is_inline`

### J73 — Use existing tools, never write custom replacements
Before writing a secrets scanner, linter, formatter, or build tool, check if an established project exists (detect-secrets, ruff, mypy, pytest, pre-commit). Writing custom infrastructure that duplicates a mature OSS project is a bug.
**Enforcement:** AGENTS.md "Mechanical Contract" rule 8
**Test:** `test_j73_use_existing_tools`

### J74 — Research library/tool existence before coding
When a task involves parsing a format, ingesting data, or performing a common operation, check if a library, module, CLI tool, or existing code in this repo already does it.
**Enforcement:** AGENTS.md "Cost-Efficiency Directive" rule 10
**Test:** `test_j74_research_before_coding`

### J75 — Make targets for every repeated operation
If you run the same command sequence 3+ times, it needs a Makefile target. Bare commands are for one-offs; make targets are for repeated workflows.
**Enforcement:** AGENTS.md "Bash Command Policy" (make-only)
**Test:** `test_j75_make_targets_for_repeated_ops`

### J76 — Read-only tools are cheap — use them liberally
Grep, glob, and read cost negligible tokens compared to subagent dispatch. Use them for exploration, verification, and fact-finding. Never hesitate to read a file.
**Enforcement:** AGENTS.md "Cost-Efficiency Directive" rule 6
**Test:** `test_j76_readonly_tools_are_cheap`

### J77 — Parallel reads, serial writes
When you need to read 5 files, read them all in one parallel message. When you need to edit 5 files, serialize the edits through one agent to avoid conflicts.
**Enforcement:** AGENTS.md "Pipeline Orchestration Model" (bias toward disjoint work)
**Test:** `test_j77_parallel_reads_serial_writes`

### J78 — Write automated checkers, do not walk the codebase
When you need to find issues (bad patterns, missing types, dead code), write a script that does the work mechanically. A serial grep→read→analyze loop burns tokens; a checker scales.
**Enforcement:** AGENTS.md "Cost-Efficiency Directive" rule 9
**Test:** `test_j78_write_automated_checkers`

### J79 — Bash tool is make-only
The bash tool may ONLY run `make <target>` commands. No cd, python, pip, git, or bare commands. If a command lacks a make target, create one first.
**Enforcement:** AGENTS.md "CRITICAL: Bash Command Policy" + enforce-make.ts
**Test:** `test_j79_bash_tool_make_only`

### J80 — No shell metacharacters in bash calls
No pipes (`|`), semicolons (`;`), `&&`, `||`, subshells, command substitution, redirects, or backticks in bash tool calls. Only `make <target>`.
**Enforcement:** AGENTS.md "Bash Command Policy" + enforce-make.ts
**Test:** `test_j80_no_shell_metacharacters`

---

### J81 — A constraint is a design prompt, not a dead end
"API doesn't support X" / "rate-limited" / "no make target for Y" are constraints to engineer around, not reasons to stop. Every constraint has a workaround.
**Enforcement:** AGENTS.md "Constraints Are To Engineer Around"
**Test:** `test_j81_constraint_is_design_prompt`

### J82 — Naked "can't" is a bug
Any statement that "X isn't possible" without an immediately stated workaround or research task is a policy violation. A "can't" that parks the problem on the user is doubly prohibited.
**Enforcement:** AGENTS.md "Constraints Are To Engineer Around" (forbidden responses)
**Test:** `test_j82_naked_cant_is_a_bug`

### J83 — Risk is proportional to blast radius, not complexity
A one-line change to a shared config used by 50 modules is HIGHER risk than a 200-line new feature in an isolated module. Judge risk by blast radius.
**Enforcement:** AGENTS.md "Pipeline Orchestration Model" (hot-file concurrency)
**Test:** `test_j83_risk_is_blast_radius`

### J84 — Hot files get extra scrutiny
Edits to hot files (daemon.py, loop.py, gateway.py, Makefile, AGENTS.md, opencode.json, shared configs) require: single-agent serialization, full gate before commit, and explicit orchestrator review.
**Enforcement:** AGENTS.md "Pipeline Orchestration Model" constraint 4a
**Test:** `test_j84_hot_files_extra_scrutiny`

### J85 — Irreversible actions require explicit user authorization
Force-push, deleting branches, dropping database tables, revoking credentials: never perform without explicit per-action user authorization in the current session.
**Enforcement:** AGENTS.md "Never Block on Questions" (rare exception: destructive/irreversible)
**Test:** `test_j85_irreversible_actions_require_auth`

### J86 — Verify before claiming, always
A claim of "done," "fixed," "green," or "shipped" without pasted machine-produced verification in the same message is a false claim. Evidence is not optional.
**Enforcement:** AGENTS.md "Evidence-Based Response Policy" + enforce-verified-claims.ts
**Test:** `test_j86_verify_before_claiming`

### J87 — Gate output is the single source of truth
SESSION.md claims have been false. Gate exit codes, test pass counts, CI verdicts, and `make verify-state` output are the only trusted sources. Trust measurements, not memory.
**Enforcement:** AGENTS.md "Mechanical Contract" rule 6
**Test:** `test_j87_gate_output_is_truth`

### J88 — A stale measurement is a false claim
A `.gate-status` file older than the last edit, or a CI verdict whose headSha != branch tip, is STALE. A claim backed by a stale measurement is a false claim.
**Enforcement:** AGENTS.md "Done Claims Require Observable Verification Evidence" (stale measurement)
**Test:** `test_j88_stale_measurement_is_false_claim`

### J89 — Never report CI status from memory
"CI is green" from memory is a lie 100% of the time it is wrong. Always run `make ci-verdict BRANCH=<b>` and paste the output. Memory has no place in CI reporting.
**Enforcement:** AGENTS.md "Verification Before Claim" + enforce-verified-claims.ts
**Test:** `test_j89_never_report_ci_from_memory`

### J90 — Observable events only — no silent operations
Any operation longer than a few seconds must stream output, print phase markers, or heartbeat. A silent 16-minute operation is indistinguishable from a hung one.
**Enforcement:** AGENTS.md "No Unseen Events" (observability invariant)
**Test:** `test_j90_observable_events_only`

---

### J91 — The golden rule of agent behavior
At every decision point, ask: "If the user were watching over my shoulder, would they agree with this choice?" If the answer is "probably not" or "I should ask," choose differently.
**Enforcement:** AGENTS.md "User intent overrides all other priorities" + Instruction-Following Priority
**Test:** `test_j91_golden_rule_of_agent_behavior`

### J92 — Do not optimize for looking busy
Dispatching 10 subagents that all do read-only status checks satisfies the floor plugin but produces zero value. Optimize for completed work, not for appearing to meet metrics.
**Enforcement:** AGENTS.md "Subagent Task Design — Fix, Don't Check" (status-check subagents are false floor)
**Test:** `test_j92_do_not_optimize_for_looking_busy`

### J93 — Inline work when faster than dispatch overhead
A single-file read + one-line edit takes <30 seconds inline. Dispatching it to a subagent adds 10–30 seconds of dispatch overhead. Inline when total time < dispatch overhead.
**Enforcement:** AGENTS.md "Cost-Efficiency Directive" rule 5
**Test:** `test_j93_inline_when_faster_than_dispatch`

### J94 — Session-start protocol is mandatory
Every session begins with: watchdog start → parallel read of TASKS.md, BUGS.md, ratchet.yml, SESSION.md, git-status, git-log → immediate dispatch wave. No prose before dispatch.
**Enforcement:** AGENTS.md "Session Start Protocol" + enforce-session-start.ts
**Test:** `test_j94_session_start_protocol`

### J95 — Long foreground ops are forbidden
`make gate` (40 min), `make test-unit` (27 min) on the main thread block ALL subagent dispatch. Use `make gate-background` + poll from a subagent. Never run long ops in the foreground.
**Enforcement:** AGENTS.md "Long-Running Operations MUST Be Backgrounded" + enforce-make.ts
**Test:** `test_j95_long_foreground_ops_forbidden`

### J96 — Never block the main thread
The main thread dispatches subagents and polls — it does not sleep, wait, or run long operations. A blocked main thread = 0 subagents running = pipeline collapse.
**Enforcement:** AGENTS.md "Background Operations NEVER Block" + enforce-no-wait.ts
**Test:** `test_j96_never_block_main_thread`

### J97 — Results arrive → process fast → dispatch next wave
The window between "results arrive" and "next dispatch wave" must be ≤3 read calls. File inspection between waves is a dispatching bug. Process results and re-dispatch.
**Enforcement:** AGENTS.md "Steady-state dispatch" rule 7 + enforce-floor.ts POST_RESULT_READ_LIMIT
**Test:** `test_j97_process_fast_dispatch_next`

### J98 — Feature work lands on development first
Create features on `development`, commit, push, then merge `development→master`. Never create the same feature independently on both branches.
**Enforcement:** AGENTS.md "Single-Source Feature Development" + check_duplicate_targets.py
**Test:** `test_j98_features_land_on_development_first`

### J99 — Never merge to master from inside a worktree
Merges to master happen on the main checkout only. A worktree-isolated agent merging to master corrupts integration state.
**Enforcement:** AGENTS.md "Branch discipline" rule 2 + docs/ORCHESTRATION.md §5
**Test:** `test_j99_never_merge_to_master_from_worktree`

### J100 — Codify improvements same session
When you discover a better way to work, codify it in AGENTS.md, a hook/plugin, and a test BEFORE moving on. A better approach applied once and forgotten is a bug.
**Enforcement:** AGENTS.md "Codify Improvements (Meta-Rule)" + 3-layer codification
**Test:** `test_j100_codify_improvements_same_session`

---

# Group L — Learning Discipline (L01–L100)

> Theme: Every behavioral failure must produce a SPEC within the same session.
> Every spec must have BLOCKING enforcement. TDD: spec → test → enforcement → verify.

---

### L01 — Failure-to-Spec Pipeline
Every premature stop, dispatch collapse, or guardrail bypass MUST produce a numbered L-spec before the session ends.
**Enforcement:** AGENTS.md `enforce-stop.ts` text.complete hook checks for new BUGS.md entry → requires matching L-spec
**Test:** `test_l01_failure_to_spec_pipeline`

### L02 — Same-Session Codification
An observed failure mode IS NOT fixed until a spec + enforcement + test are committed in the SAME session as the incident.
**Enforcement:** AGENTS.md `enforce-session-start.ts` flags sessions with unresolved prior-session BUGS.md entries
**Test:** `test_l02_same_session_codification`

### L03 — Spec-Enforcement Binding
Every L-spec MUST reference a named plugin hook, Makefile guard target, or AGENTS.md section that enforces it mechanically.
**Enforcement:** AGENTS.md `scripts/verify_spec_enforcement.py` scans all L-specs for enforcement references
**Test:** `test_l03_spec_enforcement_binding`

### L04 — Spec-Test Binding
Every L-spec MUST name a test function that exercises its enforcement. A spec without a test is advisory-only and invalid.
**Enforcement:** AGENTS.md `scripts/verify_spec_tests.py` maps spec IDs to test names; missing → gate fail
**Test:** `test_l04_spec_test_binding`

### L05 — No Advisory-Only Specs
An L-spec that documents a rule without mechanical enforcement is rejected at review. Every spec ships with a block.
**Enforcement:** AGENTS.md `enforce-spec-review.ts` denies commits containing advisory-only specs
**Test:** `test_l05_no_advisory_only_specs`

### L06 — Spec Regression Test Required
When an L-spec is added, a regression test proving the failure mode is blocked MUST land in the same commit.
**Enforcement:** AGENTS.md `scripts/check_spec_regression_test.py` — commit gate
**Test:** `test_l06_spec_regression_test_required`

### L07 — BUGS.md Entry → L-Spec Mapping
Every closed BUGS.md incident MUST cite at least one L-spec that prevents its recurrence. Uncited closures are invalid.
**Enforcement:** AGENTS.md `scripts/verify_bugs_spec_mapping.py` scans BUGS.md for L-spec references
**Test:** `test_l07_bugs_spec_mapping`

### L08 — Incident-to-Spec Latency Under 4 Hours
From BUGS.md incident open → L-spec committed: max 4 hours. Exceeded latency is itself a learning failure.
**Enforcement:** AGENTS.md `scripts/check_incident_spec_latency.py` parses BUGS.md timestamps
**Test:** `test_l08_incident_spec_latency`

### L09 — Recurrence Triggers Spec Upgrade
If the same failure mode recurs despite an existing L-spec, the spec MUST be upgraded (stronger enforcement, broader scope) within the same session.
**Enforcement:** AGENTS.md `enforce-recurrence-upgrade.ts` detects duplicate BUGS.md incidents → blocks close without spec revision
**Test:** `test_l09_recurrence_triggers_spec_upgrade`

### L10 — Spec Decay Detection
An L-spec whose test has not been run in 30 days is flagged as "at-risk of decay." Must run and pass within 7 days or be upgraded.
**Enforcement:** AGENTS.md `scripts/check_spec_decay.py` — scheduled; gate-fails on decayed specs
**Test:** `test_l10_spec_decay_detection`

### L11 — Learning Velocity Metric
Sessions MUST track: (L-specs created + specs upgraded + incidents prevented) / session-hours. Metric wired into SESSION.md.
**Enforcement:** AGENTS.md `scripts/compute_learning_velocity.py` writes to `/tmp/gludd-learning-velocity.json`
**Test:** `test_l11_learning_velocity_metric`

### L12 — Velocity Below Threshold
If learning velocity drops below 0.5 specs/session-hour for 3 consecutive sessions, the agent MUST self-audit and report root cause.
**Enforcement:** AGENTS.md `enforce-learning-velocity.ts` injects audit directive at session start
**Test:** `test_l12_velocity_below_threshold`

### L13 — Spec Coverage by Category
L-specs MUST cover all 8 learning categories. A category with 0 specs for >10 sessions triggers a gap-filling requirement.
**Enforcement:** AGENTS.md `scripts/check_spec_category_coverage.py` — gate-fails on uncovered categories
**Test:** `test_l13_spec_coverage_by_category`

### L14 — Cross-Category Pattern Recognition
When 3+ L-specs share the same root cause across different categories, a meta-spec (M-series) MUST be created identifying the shared pattern.
**Enforcement:** AGENTS.md `scripts/detect_cross_category_patterns.py` — suggests M-specs
**Test:** `test_l14_cross_category_pattern_recognition`

### L15 — Guardrail Calibration from L-Specs
Every L-spec about a guardrail false-positive or false-negative MUST produce a calibration adjustment (narrower check, tighter threshold) in the same session.
**Enforcement:** AGENTS.md `enforce-calibration.ts` blocks guardrail edits that widen without a matching L-spec
**Test:** `test_l15_guardrail_calibration_from_specs`

### L16 — Calibration Audit Trail
Every guardrail calibration (threshold change, pattern addition, scope adjustment) MUST reference the L-spec that mandated it.
**Enforcement:** AGENTS.md `scripts/verify_calibration_audit.py` — commit message must cite L-spec ID
**Test:** `test_l16_calibration_audit_trail`

### L17 — No Calibration Without Spec
A guardrail threshold change without a corresponding L-spec is a policy violation. Calibration is downstream of learning; learning is downstream of failure.
**Enforcement:** AGENTS.md `enforce-no-calibration-without-spec.ts` — blocks threshold changes with no L-spec reference
**Test:** `test_l17_no_calibration_without_spec`

### L18 — False-Positive Rate Capped
Any guardrail with >10% false-positive rate over 100 invocations MUST produce an L-spec and be recalibrated within one session.
**Enforcement:** AGENTS.md `scripts/check_false_positive_rate.py` — tracks hook deny/allow ratios
**Test:** `test_l18_false_positive_rate_capped`

### L19 — False-Negative Zero Tolerance
A guardrail false-negative (failure mode occurred, guardrail fired but did not block) MUST produce an L-spec AND an immediate enforcement upgrade.
**Enforcement:** AGENTS.md `enforce-false-negative.ts` — detects missed blocks via gate/CI cross-reference
**Test:** `test_l19_false_negative_zero_tolerance`

### L20 — Spec-Driven Guardrail Design
New guardrails MUST be designed starting from an L-spec. Spec first, then hook, then test, then AGENTS.md — never write a guardrail without a driving spec.
**Enforcement:** AGENTS.md `scripts/check_guardrail_spec_lineage.py` — new hooks require driving L-spec
**Test:** `test_l20_spec_driven_guardrail_design`

### L21 — Session Retrospective Required
Every session MUST produce a retrospective entry in SESSION.md listing: failures observed, L-specs created, specs upgraded, calibration changes.
**Enforcement:** AGENTS.md `enforce-retrospective.ts` blocks session close without SESSION.md retrospective section
**Test:** `test_l21_session_retrospective_required`

### L22 — Retrospective Completeness
A retrospective that lists "no failures" while BUGS.md has entries from this session is a false claim and blocked.
**Enforcement:** AGENTS.md `enforce-retrospective-completeness.ts` cross-references BUGS.md timestamps vs retrospective
**Test:** `test_l22_retrospective_completeness`

### L23 — Learning From Other Agents' Failures
When a subagent fails or produces incorrect output, the orchestrator MUST create an L-spec if the failure mode is novel (not covered by existing specs).
**Enforcement:** AGENTS.md `scripts/detect_novel_subagent_failure.py` — compares failure to known specs
**Test:** `test_l23_learning_from_other_agents_failures`

### L24 — Subagent Failure Taxonomy
Every subagent failure type MUST be classified (timeout, wrong-output, deadlock, regression, hallucination) and logged against the relevant L-specs.
**Enforcement:** AGENTS.md `scripts/classify_subagent_failure.py` — populates failure taxonomy
**Test:** `test_l24_subagent_failure_taxonomy`

### L25 — Knowledge Transfer Between Sessions
Critical insights from a session NOT codified as L-specs within that session are lost. L-specs are the persistence mechanism for agent learning.
**Enforcement:** AGENTS.md `enforce-knowledge-transfer.ts` — SESSION.md next-steps lacking L-specs flagged at session start
**Test:** `test_l25_knowledge_transfer_between_sessions`

### L26 — Post-Incident Review (PIR) Required
Any incident that blocks the gate for >30 minutes MUST have a PIR section in BUGS.md and produce at least one L-spec.
**Enforcement:** AGENTS.md `scripts/check_pir_required.py` — blocks BUGS.md close without PIR
**Test:** `test_l26_post_incident_review_required`

### L27 — PIR Root Cause Depth
A PIR whose root cause is "agent error" or "process failure" is insufficiently deep. Must trace to a specific missing or broken enforcement mechanism.
**Enforcement:** AGENTS.md `scripts/check_pir_depth.py` — rejects shallow root-cause entries
**Test:** `test_l27_pir_root_cause_depth`

### L28 — Five Whys in PIRs
Every PIR MUST include the "Five Whys" chain from observed symptom to root enforcement gap. Truncated chains are rejected.
**Enforcement:** AGENTS.md `scripts/check_five_whys.py` — counts "why" depth in PIR section
**Test:** `test_l28_five_whys_in_pirs`

### L29 — Spec ID Namespace Discipline
L-spec IDs are sequential (L01–L99+) and never reused. Deleting an L-spec leaves a tombstone explaining why it was retired.
**Enforcement:** AGENTS.md `scripts/check_spec_namespace.py` — gap detection + tombstone requirement
**Test:** `test_l29_spec_id_namespace_discipline`

### L30 — Spec Tombstone Format
A retired spec tombstone MUST include: spec ID, retirement date, retirement reason, and the replacement spec ID (if any).
**Enforcement:** AGENTS.md `scripts/verify_spec_tombstones.py` — validates tombstone format
**Test:** `test_l30_spec_tombstone_format`

### L31 — Spec Merge on Overlap
When two L-specs are found to cover the same failure mode, they MUST be merged (one retired with a tombstone → replacement) within one session.
**Enforcement:** AGENTS.md `scripts/detect_spec_overlap.py` — cosine-similarity or keyword-overlap scan
**Test:** `test_l31_spec_merge_on_overlap`

### L32 — Spec Split on Scope Creep
An L-spec that has grown to cover >3 distinct enforcement mechanisms MUST be split into sub-specs, each with its own enforcement and test.
**Enforcement:** AGENTS.md `scripts/check_spec_scope.py` — counts enforcement references per spec
**Test:** `test_l32_spec_split_on_scope_creep`

### L33 — Learning Debt Tracking
An incident that is acknowledged in BUGS.md but has no corresponding L-spec within 4 hours is "learning debt." Tracked in `config/learning_debt.yml`.
**Enforcement:** AGENTS.md `scripts/track_learning_debt.py` — writes debt entries; gate-fails on accumulation
**Test:** `test_l33_learning_debt_tracking`

### L34 — Learning Debt Paydown Rate
At least 80% of learning debt MUST be paid down (spec created) within one session of accrual. Below 80% triggers enforced paydown.
**Enforcement:** AGENTS.md `enforce-learning-debt-paydown.ts` — blocks non-spec work below 80% paydown
**Test:** `test_l34_learning_debt_paydown_rate`

### L35 — Learning Debt Ceiling
If learning debt exceeds 10 unresolved entries, ALL work is blocked until debt drops below 5. The agent cannot proceed without codifying failures.
**Enforcement:** AGENTS.md `scripts/check_learning_debt_ceiling.py` — pre-dispatch gate
**Test:** `test_l35_learning_debt_ceiling`

### L36 — Spec Quality Gate
Every L-spec MUST pass a quality check: has enforcement mechanism, has named test, has one-line description, fits one category. Failing specs are rejected at commit.
**Enforcement:** AGENTS.md `scripts/check_spec_quality.py` — commit-time gate
**Test:** `test_l36_spec_quality_gate`

### L37 — No Spec Without Incident
An L-spec without a corresponding BUGS.md incident (or a design decision record explaining proactive creation) is rejected.
**Enforcement:** AGENTS.md `scripts/verify_spec_incident_lineage.py` — traces spec → incident
**Test:** `test_l37_no_spec_without_incident`

### L38 — Proactive Specs Require DDR
A spec created proactively (not in response to an incident) MUST have a Design Decision Record (DDR) in `docs/ddr/` explaining why it was created preemptively.
**Enforcement:** AGENTS.md `scripts/check_spec_ddr.py` — rejects proactive specs without DDR
**Test:** `test_l38_proactive_specs_require_ddr`

### L39 — Spec Effectiveness Metric
Every L-spec tracks: creation date, incidents-prevented count, false-positive count, last-triggered date. Effectiveness = prevented / (prevented + false-positives).
**Enforcement:** AGENTS.md `scripts/compute_spec_effectiveness.py` — writes metrics to spec metadata
**Test:** `test_l39_spec_effectiveness_metric`

### L40 — Ineffective Spec Retirement
An L-spec with <50% effectiveness over 90 days AND 0 incidents-prevented in the last 30 days is retired (tombstone + replacement search).
**Enforcement:** AGENTS.md `scripts/flag_ineffective_specs.py` — scheduled; auto-flags for review
**Test:** `test_l40_ineffective_spec_retirement`

### L41 — Spec Density Trend
Track L-specs-per-session over a rolling 10-session window. A downward trend (fewer specs per session) while BUGS.md entries remain constant signals learning stagnation.
**Enforcement:** AGENTS.md `scripts/compute_spec_density.py` — trend detection
**Test:** `test_l41_spec_density_trend`

### L42 — Stagnation Alerting
If spec density drops >50% from the 10-session peak while incident rate is flat, inject a "learning stagnation" directive at session start.
**Enforcement:** AGENTS.md `enforce-stagnation-alert.ts` — system.transform injection
**Test:** `test_l42_stagnation_alerting`

### L43 — Learning Rate Acceleration
The learning rate (specs/incident) should accelerate over time. A declining learning rate for 5+ sessions triggers root-cause analysis.
**Enforcement:** AGENTS.md `scripts/check_learning_rate.py` — computes first-derivative of learning rate
**Test:** `test_l43_learning_rate_acceleration`

### L44 — Skill Acquisition Tracking
When the agent demonstrates a new capability (e.g., first correct use of a new tool pattern, first successful multi-worktree merge), it MUST document the skill with an L-spec.
**Enforcement:** AGENTS.md `scripts/detect_new_skill_acquisition.py` — detects novel tool usage patterns
**Test:** `test_l44_skill_acquisition_tracking`

### L45 — Skill Decay Detection
A documented skill that hasn't been exercised in 20 sessions is flagged for re-validation (the agent must demonstrate the skill again or the spec is re-evaluated).
**Enforcement:** AGENTS.md `scripts/check_skill_decay.py` — tracks last-exercised timestamps
**Test:** `test_l45_skill_decay_detection`

### L46 — Cross-Domain Transfer Learning
When an L-spec in one category (e.g., dispatch discipline) applies to another (e.g., test quality), a cross-reference MUST be added to both specs.
**Enforcement:** AGENTS.md `scripts/detect_cross_domain_applicability.py` — suggests cross-references
**Test:** `test_l46_cross_domain_transfer_learning`

### L47 — Pattern Library Generation
Every 25 L-specs, a pattern library document MUST be generated grouping specs by root cause pattern and listing the enforcement mechanisms used.
**Enforcement:** AGENTS.md `scripts/generate_pattern_library.py` — scheduled; gate-fails if stale
**Test:** `test_l47_pattern_library_generation`

### L48 — Pattern Library Currency
The pattern library MUST be regenerated within 24 hours of any L-spec being added, modified, or retired. Stale pattern library → gate fail.
**Enforcement:** AGENTS.md `scripts/check_pattern_library_currency.py` — mtime comparison
**Test:** `test_l48_pattern_library_currency`

### L49 — Anti-Pattern Catalog
L-specs that describe specific anti-patterns (e.g., "dispatch collapse," "premature stop") MUST be collected into a machine-readable anti-pattern catalog.
**Enforcement:** AGENTS.md `scripts/build_anti_pattern_catalog.py` — extracts anti-patterns from specs
**Test:** `test_l49_anti_pattern_catalog`

### L50 — Anti-Pattern Scan at Dispatch
Before each dispatch wave, the anti-pattern catalog is checked against the current state. Known anti-patterns detected → dispatch blocked until remedied.
**Enforcement:** AGENTS.md `enforce-anti-pattern-scan.ts` — pre-dispatch check against catalog
**Test:** `test_l50_anti_pattern_scan_at_dispatch`

### L51 — Learning From External Sources
When a relevant paper, blog post, or OSS incident report describes a failure mode applicable to gludd, an L-spec MUST be created preemptively.
**Enforcement:** AGENTS.md `scripts/flag_external_learning_opportunity.py` — manual trigger
**Test:** `test_l51_learning_from_external_sources`

### L52 — External Learning Queue
Preemptively created specs from external sources sit in a "pending validation" queue until a real or simulated incident validates them. Unvalidated after 30 days → retired.
**Enforcement:** AGENTS.md `scripts/manage_external_learning_queue.py` — scheduled
**Test:** `test_l52_external_learning_queue`

### L53 — Spec Validation From Simulation
An L-spec that has never been triggered by a real incident within 60 days MUST be validated via a simulated incident (inject the failure mode, confirm the spec blocks it).
**Enforcement:** AGENTS.md `scripts/validate_spec_via_simulation.py` — scheduled simulation runner
**Test:** `test_l53_spec_validation_from_simulation`

### L54 — Simulation Coverage Required
At least 30% of all L-specs must have been validated by simulation in the last 90 days. Below threshold → enforced simulation wave.
**Enforcement:** AGENTS.md `scripts/check_simulation_coverage.py` — gate
**Test:** `test_l54_simulation_coverage_required`

### L55 — Spec Impact Scoring
Every L-spec receives an impact score: (incidents prevented × severity weight) / (false positives × noise weight). Higher = more valuable.
**Enforcement:** AGENTS.md `scripts/compute_spec_impact.py` — writes scores to spec metadata
**Test:** `test_l55_spec_impact_scoring`

### L56 — Top-N Specs Review
The 10 highest-impact L-specs are reviewed monthly for: continuing relevance, calibration drift, and upgrade opportunities. Review MUST produce at least one improvement.
**Enforcement:** AGENTS.md `scripts/schedule_top_n_review.py` — monthly trigger
**Test:** `test_l56_top_n_specs_review`

### L57 — Bottom-N Specs Pruning
The 10 lowest-impact L-specs (by impact score) are reviewed monthly for retirement, merge, or upgrade. Bottom-N review MUST reduce the bottom-N by at least 3 specs.
**Enforcement:** AGENTS.md `scripts/schedule_bottom_n_review.py` — monthly trigger
**Test:** `test_l57_bottom_n_specs_pruning`

### L58 — Spec Dependency Graph
L-specs that reference each other (cross-references, replacements, sub-specs) form a dependency graph. Circular dependencies are invalid and MUST be broken.
**Enforcement:** AGENTS.md `scripts/check_spec_dependency_graph.py` — cycle detection
**Test:** `test_l58_spec_dependency_graph`

### L59 — Spec Hierarchy Depth
An L-spec that references >5 other specs (dependencies) is too broad; it MUST be split or have its scope narrowed.
**Enforcement:** AGENTS.md `scripts/check_spec_hierarchy_depth.py` — breadth limit
**Test:** `test_l59_spec_hierarchy_depth`

### L60 — Spec Atomicity
An L-spec MUST address exactly ONE failure mode or ONE learning insight. Multi-topic specs are split during review.
**Enforcement:** AGENTS.md `scripts/check_spec_atomicity.py` — topic clustering
**Test:** `test_l60_spec_atomicity`

### L61 — Learning Session Budget
Every session allocates at least 10% of its total wall-clock time to learning activities: spec creation, spec review, calibration, anti-pattern scanning, simulation.
**Enforcement:** AGENTS.md `scripts/track_learning_budget.py` — budget tracking; under-allocation flagged
**Test:** `test_l61_learning_session_budget`

### L62 — Learning Budget Enforcement
If learning budget is <5% for 2 consecutive sessions, the third session starts with a mandatory learning-only phase (no feature work until budget catches up).
**Enforcement:** AGENTS.md `enforce-learning-budget.ts` — session-start gate
**Test:** `test_l62_learning_budget_enforcement`

### L63 — Gap Analysis From Spec Catalog
Weekly: scan the spec catalog for failure-mode coverage gaps (categories with no specs, severity levels with no coverage, subsystem blind spots).
**Enforcement:** AGENTS.md `scripts/run_spec_gap_analysis.py` — scheduled; produces gap report
**Test:** `test_l63_gap_analysis_from_spec_catalog`

### L64 — Gap-Driven Spec Generation
Gap analysis findings MUST produce at least 3 new L-specs (proactive) within one session of the report being generated. Unfilled gaps → session-start block.
**Enforcement:** AGENTS.md `scripts/verify_gap_driven_specs.py` — tracks gap → spec resolution
**Test:** `test_l64_gap_driven_spec_generation`

### L65 — Incident Severity Classification
Every BUGS.md incident carries a severity: S1 (blocks all work), S2 (blocks a wave), S3 (blocks a task), S4 (cosmetic). Severity determines spec priority.
**Enforcement:** AGENTS.md `scripts/classify_incident_severity.py` — validates severity on BUGS.md entry
**Test:** `test_l65_incident_severity_classification`

### L66 — Severity-Based Spec SLA
S1 incidents → L-spec within 1 hour. S2 → within 4 hours. S3 → within 24 hours. S4 → within 7 days. SLA violations are themselves S2 incidents.
**Enforcement:** AGENTS.md `scripts/check_spec_sla.py` — SLA timer per incident
**Test:** `test_l66_severity_based_spec_sla`

### L67 — SLA Breach Escalation
An SLA breach on spec creation escalates the original incident by one severity level and triggers immediate spec creation work.
**Enforcement:** AGENTS.md `scripts/escalate_sla_breach.py` — reclassifies BUGS.md entry severity
**Test:** `test_l67_sla_breach_escalation`

### L68 — Recurrence Severity Escalation
An incident that recurs despite an existing L-spec is automatically escalated one severity level. The second recurrence escalates again and triggers a mandatory spec redesign.
**Enforcement:** AGENTS.md `scripts/check_recurrence_escalation.py` — severity bump on recurrence
**Test:** `test_l68_recurrence_severity_escalation`

### L69 — Spec Versioning
When an L-spec is modified (strengthened, narrowed, split), its version increments (L01v1 → L01v2). The version history MUST be recorded in the spec's metadata.
**Enforcement:** AGENTS.md `scripts/track_spec_versions.py` — version history validation
**Test:** `test_l69_spec_versioning`

### L70 — Spec Changelog
Every spec change MUST include a one-line changelog entry: what changed, why, and which incident (if any) drove the change.
**Enforcement:** AGENTS.md `scripts/check_spec_changelog.py` — rejects unlogged spec changes
**Test:** `test_l70_spec_changelog`

### L71 — Spec Rollback Capability
If a spec upgrade introduces a regression (new false-positive flood, work blockage), it MUST be rollback-able to the previous version within 5 minutes.
**Enforcement:** AGENTS.md `scripts/verify_spec_rollback.py` — tests rollback to prior version
**Test:** `test_l71_spec_rollback_capability`

### L72 — Rollback Incident Required
A spec rollback is itself an incident with its own BUGS.md entry and its own L-spec. The fact that the upgrade regressed is a learning opportunity.
**Enforcement:** AGENTS.md `scripts/detect_spec_rollback.py` — creates BUGS.md entry on rollback
**Test:** `test_l72_rollback_incident_required`

### L73 — Spec A/B Testing
When a guardrail calibration is uncertain (will it reduce false-positives without increasing false-negatives?), run an A/B test: old-enforcement vs new-enforcement for 10 sessions; measure.
**Enforcement:** AGENTS.md `scripts/run_spec_ab_test.py` — splits enforcement, collects metrics
**Test:** `test_l73_spec_ab_testing`

### L74 — A/B Test Decision Gate
A/B test results MUST produce a decision (adopt, reject, iterate) within 24 hours of test completion. Indecision is an incident.
**Enforcement:** AGENTS.md `scripts/enforce_ab_test_decision.py` — timeout on A/B test results
**Test:** `test_l74_ab_test_decision_gate`

### L75 — Spec-Driven Session Planning
Before each session, the top 3 most-recently-created L-specs (highest recency = highest relevance to active failure modes) are injected into the session-start prompt.
**Enforcement:** AGENTS.md `enforce-spec-driven-planning.ts` — system.transform injection
**Test:** `test_l75_spec_driven_session_planning`

### L76 — Spec Reminder Injection
During a session, if the agent is about to repeat a behavior covered by an L-spec, the spec's enforcement mechanism fires a "remember LXX" reminder before the action.
**Enforcement:** AGENTS.md `enforce-spec-reminder.ts` — tool.execute.before pre-action match
**Test:** `test_l76_spec_reminder_injection`

### L77 — Spec-Covered Behavior Self-Audit
The agent MUST self-audit its behavior against active L-specs at the start of every session. Behaviors absent from or violating specs are flagged.
**Enforcement:** AGENTS.md `scripts/run_spec_compliance_self_audit.py` — session-start audit
**Test:** `test_l77_spec_covered_behavior_self_audit`

### L78 — Compliance Score Tracking
Each session receives a compliance score: (behaviors compliant with specs) / (total behaviors checked). Score < 80% triggers enforced spec review.
**Enforcement:** AGENTS.md `scripts/compute_compliance_score.py` — session-end scoring
**Test:** `test_l78_compliance_score_tracking`

### L79 — Compliance Score Trend
If compliance score declines for 3 consecutive sessions, the agent enters a "learning-only" mode: no feature work, only spec review, calibration, and simulation.
**Enforcement:** AGENTS.md `enforce-compliance-trend.ts` — blocks non-learning work on declining trend
**Test:** `test_l79_compliance_score_trend`

### L80 — Spec Coverage of Enforcement Plugins
Every enforcement plugin MUST be covered by at least one L-spec. A plugin with no driving L-spec is a gap — either the plugin needs a spec or it is unnecessary.
**Enforcement:** AGENTS.md `scripts/check_plugin_spec_coverage.py` — maps plugins → L-specs
**Test:** `test_l80_spec_coverage_of_enforcement_plugins`

### L81 — Plugin Introduction Requires Spec
A new enforcement plugin introduced without a driving L-spec is rejected. Plugin code IS the enforcement; the spec explains WHY the enforcement exists.
**Enforcement:** AGENTS.md `scripts/check_new_plugin_lineage.py` — commit-time gate
**Test:** `test_l81_plugin_introduction_requires_spec`

### L82 — Spec-Driven Test Generation
When an L-spec is created, the corresponding test SHOULD be written BEFORE the enforcement code (TDD for enforcement). Spec first, then test, then enforcement.
**Enforcement:** AGENTS.md `enforce-spec-tdd.ts` — blocks enforcement edits without a test file on disk
**Test:** `test_l82_spec_driven_test_generation`

### L83 — Enforcement Code Coverage From Specs
Every L-spec's enforcement mechanism MUST have its test exercise at least 80% of the enforcement code paths. Coverage < 80% → spec marked "under-tested."
**Enforcement:** AGENTS.md `scripts/check_spec_enforcement_coverage.py` — per-spec coverage analysis
**Test:** `test_l83_enforcement_code_coverage_from_specs`

### L84 — Undocumented Failure Patterns
When a failure occurs that does not match any existing L-spec pattern, it is classified as "novel failure." A novel failure counter is maintained per session.
**Enforcement:** AGENTS.md `scripts/detect_novel_failure_patterns.py` — pattern matching against spec library
**Test:** `test_l84_undocumented_failure_patterns`

### L85 — Novel Failure Response
Any session with >0 novel failures MUST produce at least one new L-spec. A novel failure that goes undocumented is a learning failure — itself an S3 incident.
**Enforcement:** AGENTS.md `scripts/check_novel_failure_response.py` — session-end gate
**Test:** `test_l85_novel_failure_response`

### L86 — Learning Loop Closure Rate
Track: (incidents that produced L-specs within SLA) / (total incidents). Loop closure rate is a key learning-health metric. Rate < 90% → incident.
**Enforcement:** AGENTS.md `scripts/compute_loop_closure_rate.py` — per-session metric
**Test:** `test_l86_learning_loop_closure_rate`

### L87 — Loop Closure Rate Trend
If loop closure rate declines for 5+ consecutive sessions, the agent enters mandatory process review: why are incidents not translating into specs?
**Enforcement:** AGENTS.md `enforce-loop-closure-trend.ts` — blocks work on declining trend
**Test:** `test_l87_loop_closure_rate_trend`

### L88 — Spec Effectiveness Retrospective
Monthly: review all L-specs created in the last 30 days. Which ones prevented incidents? Which never fired? Publish effectiveness report to `docs/learning/`.
**Enforcement:** AGENTS.md `scripts/generate_spec_effectiveness_report.py` — monthly task
**Test:** `test_l88_spec_effectiveness_retrospective`

### L89 — Orphaned Enforcement Detection
An enforcement mechanism (plugin hook, Makefile guard, AGENTS.md rule) not linked to any L-spec within 30 days of creation is "orphaned enforcement" — untethered to learning.
**Enforcement:** AGENTS.md `scripts/check_orphaned_enforcement.py` — scheduled scan
**Test:** `test_l89_orphaned_enforcement_detection`

### L90 — Orphan Remediation
Orphaned enforcement MUST either: (a) be linked to an existing L-spec, (b) have a new L-spec created for it, or (c) be removed. Remediation timeout: 7 days.
**Enforcement:** AGENTS.md `scripts/enforce_orphan_remediation.py` — gate-fails on stale orphans
**Test:** `test_l90_orphan_remediation`

### L91 — Learning From Missed Opportunities
When the agent encounters a situation where a missing L-spec WOULD have prevented a failure, that missed-opportunity observation MUST produce a proactive L-spec.
**Enforcement:** AGENTS.md `scripts/flag_missed_opportunity.py` — agent self-report trigger
**Test:** `test_l91_learning_from_missed_opportunities`

### L92 — Spec Applicability Window
Every L-spec carries an "applicable contexts" tag: which subsystems, which agent roles, which session phases it applies to. Context-tagged specs fire contextually.
**Enforcement:** AGENTS.md `enforce-spec-applicability.ts` — context-aware spec matching
**Test:** `test_l92_spec_applicability_window`

### L93 — Context Mismatch Detection
If an L-spec fires in a context it is NOT tagged for, that is a false-positive and triggers a spec recalibration (narrow the applicability tags).
**Enforcement:** AGENTS.md `scripts/detect_context_mismatch.py` — cross-references spec tags with firing context
**Test:** `test_l93_context_mismatch_detection`

### L94 — Learning Transfer to Subagents
Subagent prompts MUST include relevant L-specs that apply to their task context. A subagent that repeats a failure covered by an existing L-spec is a dispatch bug.
**Enforcement:** AGENTS.md `scripts/inject_specs_into_subagent_prompts.py` — prompt augmentation
**Test:** `test_l94_learning_transfer_to_subagents`

### L95 — Subagent L-Spec Compliance
Subagents SHOULD return an L-spec compliance report: which specs did they observe, which were relevant, did any spec block their work. Report analyzed by orchestrator.
**Enforcement:** AGENTS.md `scripts/collect_subagent_spec_compliance.py` — result post-processing
**Test:** `test_l95_subagent_l_spec_compliance`

### L96 — Continuous Improvement Roadmap
Every 50 L-specs, generate a "continuous improvement roadmap": which categories have the most specs (mature), which have the fewest (immature), and what the next 10 specs should target.
**Enforcement:** AGENTS.md `scripts/generate_ci_roadmap.py` — milestone-triggered
**Test:** `test_l96_continuous_improvement_roadmap`

### L97 — Learning Maturity Model
The project progresses through learning maturity levels: L1 (reactive), L2 (structured), L3 (proactive), L4 (predictive). Level is recomputed every 50 specs.
**Enforcement:** AGENTS.md `scripts/compute_learning_maturity.py` — level assessment
**Test:** `test_l97_learning_maturity_model`

### L98 — Maturity Level Gate
At L2, all incidents MUST produce L-specs. At L3, proactive specs must outnumber reactive specs. At L4, the agent must predict failure modes before they occur (pre-incident specs).
**Enforcement:** AGENTS.md `scripts/check_maturity_level_requirements.py` — level-specific gate
**Test:** `test_l98_maturity_level_gate`

### L99 — Spec Catalog Integrity Check
Daily: scan the spec catalog for integrity violations (missing enforcement refs, missing tests, circular deps, stale tombstones, duplicate IDs). Any violation → gate fail.
**Enforcement:** AGENTS.md `scripts/check_spec_catalog_integrity.py` — daily automated check
**Test:** `test_l99_spec_catalog_integrity_check`

### L100 — The Meta-Spec
This group (L01–L100) is itself subject to all its own rules. If a spec in this group is violated without producing a new spec, that violation is a meta-failure — the learning system has learned nothing from itself.
**Enforcement:** AGENTS.md `scripts/check_meta_spec_compliance.py` — recursively applies L01-L99 to L01-L100
**Test:** `test_l100_the_meta_spec`

---

# Group Y — Yield Discipline Behavioral Specs (Y01–Y100)

**Theme:** Never stop while CI is running. Never stop while release is incomplete. Never stop while user objective is unmet. Only stop when user says "done."

---

## Anti-Stop Enforcement While CI is Pending (Y01–Y15)

### Y01 — Block text-only response during CI-pending
A text-only assistant response while `make ci-verdict BRANCH=master` returns `in_progress` or `pending` is denied.
**Enforcement:** AGENTS.md `enforce-stop.ts` `text.complete` hook — `hasRealPendingWork()` includes CI-pending check
**Test:** `test_y01_block_text_only_during_ci_pending`

### Y02 — CI-pending counts as open work for stop gate
The stop gate treats CI=in_progress identically to unchecked TASKS items — neither permits a premature stop.
**Enforcement:** AGENTS.md `enforce-stop.ts` `hasRealPendingWork()` — CI verdict checked alongside TASKS.md/ratchet
**Test:** `test_y02_ci_pending_counts_as_open_work`

### Y03 — CI-RED does not license a stop
A red CI run is not a stop condition — it is a fix condition. The agent must fix the failure, not declare done.
**Enforcement:** AGENTS.md `enforce-stop.ts` text.complete + `AGENTS.md` "CI-RED is work, not a result"
**Test:** `test_y03_ci_red_is_not_stop_condition`

### Y04 — CI-GREEN on tip does not license a stop when tasks remain
CI green on the branch tip while TASKS.md has unchecked items does not permit a text-only stop.
**Enforcement:** AGENTS.md `enforce-stop.ts` `hasRealPendingWork()` — CI green is ANDed with TASKS empty, not OR
**Test:** `test_y04_ci_green_does_not_override_pending_tasks`

### Y05 — CI status check must be paired with continuation action
Every `make ci-verdict-safe` invocation that returns PENDING or RED must be immediately followed by a dispatch wave or fix action — never a text-only report.
**Enforcement:** AGENTS.md `enforce-no-wait.ts` + `AGENTS.md` CI-Poll Subagents Are Forbidden
**Test:** `test_y05_ci_status_must_pair_with_action`

### Y06 — CI poll loop in subagent is forbidden
A subagent dispatched with a prompt containing "every N seconds" AND "ci-verdict" is denied at dispatch time.
**Enforcement:** AGENTS.md `enforce-no-wait.ts` — `CI_POLL_DISPATCH_PATTERNS` matcher on task/agent/workflow
**Test:** `test_y06_ci_poll_loop_subagent_forbidden`

### Y07 — CI wait is only for release-cut
`make ci-wait` may only be invoked inside `make release-cut`; standalone invocation outside a release context is denied.
**Enforcement:** AGENTS.md `enforce-no-wait.ts` + `AGENTS.md` "ci-wait is for release-cut only"
**Test:** `test_y07_ci_wait_only_for_release_cut`

### Y08 — Forced CI check requires documented reason
`make ci-verdict-safe FORCE=1` outside `make release-cut` context must log the reason to stderr; silent FORCE=1 use is a violation.
**Enforcement:** AGENTS.md `scripts/ci_check_cooldown.py` — FORCE=1 path requires reason comment
**Test:** `test_y08_forced_ci_check_requires_reason`

### Y09 — CI cooldown refusal is not PENDING, it is UNKNOWN
When `ci-verdict-safe` returns exit 3 (cooldown active), the agent must not report CI as PENDING — the state is unknown.
**Enforcement:** AGENTS.md `AGENTS.md` "CI-COOLDOWN ≠ PENDING (cooldown masking)"
**Test:** `test_y09_cooldown_refusal_is_not_pending`

### Y10 — CI green on stale SHA is not counted as green
A CI verdict whose `headSha != branch tip` is discarded; the agent must not claim "CI green."
**Enforcement:** AGENTS.md `scripts/ci_check_cooldown.py` — headSha comparison; `AGENTS.md` "Stale CI runs"
**Test:** `test_y10_stale_ci_sha_must_not_be_counted`

### Y11 — CI push is fire-and-forget
After `make batch-push` + `make verify-remote`, the agent must resume work immediately — not wait for CI to complete.
**Enforcement:** AGENTS.md `enforce-no-wait.ts` + `AGENTS.md` "Push is fire-and-forget"
**Test:** `test_y11_ci_push_must_be_fire_and_forget`

### Y12 — CI-green preempts gating non-release work
Non-release work (feature dev, test writing, refactoring) must never be gated on CI green. Start immediately.
**Enforcement:** AGENTS.md `AGENTS.md` "CI-green is a precondition for RELEASE CUT only"
**Test:** `test_y12_non_release_work_must_not_gate_on_ci`

### Y13 — CI pending must not reduce subagent count
The agent must maintain the 10-agent floor even while CI is running — CI pending is not a license to thin the pool.
**Enforcement:** AGENTS.md `enforce-floor.ts` — CI status not exempted from floor check
**Test:** `test_y13_ci_pending_must_not_reduce_subagent_count`

### Y14 — Agent reporting CI status as "final" message is a stop
A text-only message summarizing CI status (green/red/pending) with no tool call is blanked as a premature stop.
**Enforcement:** AGENTS.md `enforce-stop.ts` `text.complete` — CI status + no tool call = blank
**Test:** `test_y14_ci_status_as_final_message_is_stop`

### Y15 — CI-thrash detection blocks push, not work
When the rate guard blocks a push (≥3 cancelled runs in 2h), work must continue — the block only denies the push, not all tool calls.
**Enforcement:** AGENTS.md `Makefile` `_push-rate-guard` — blocks push, not non-push operations
**Test:** `test_y15_ci_thrash_detection_blocks_push_not_work`

---

## Release-Completeness as Stop-Gate (Y16–Y30)

### Y16 — Release-incomplete blocks "done" claim
A release tag without a CI-green run and confirmed published artifacts is release-incomplete; any "shipped"/"released"/"done" claim is blanked.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` `text.complete` — done-words require artifact evidence
**Test:** `test_y16_release_incomplete_blocks_done_claim`

### Y17 — `verify-release-completeness` failure blocks stop
When `make verify-release-completeness TAG=<tag>` exits non-zero, any text-only response is denied — the release must be fixed first.
**Enforcement:** AGENTS.md `enforce-stop.ts` `hasRealPendingWork()` — release completeness checked via `TAG` state file
**Test:** `test_y17_verify_release_completeness_failure_blocks_stop`

### Y18 — Release artifact missing all platforms is incomplete
A release with only a macOS binary (missing Linux, SBOM, checksums) is not complete; `verify-release-completeness` catches this.
**Enforcement:** AGENTS.md `scripts/verify_release_completeness.py` — 12 asset categories checked
**Test:** `test_y18_release_missing_platforms_is_incomplete`

### Y19 — Prerelease flag must match tag shape
A tag `v0.1.0-beta.1` must produce a GitHub Release with `prerelease: true`; mismatch is a completeness failure.
**Enforcement:** AGENTS.md `scripts/verify_release_completeness.py` — prerelease flag vs tag shape
**Test:** `test_y19_prerelease_flag_must_match_tag_shape`

### Y20 — Zero-size release asset is a completeness failure
Any downloadable asset with `size: 0` is treated as missing — the release is incomplete.
**Enforcement:** AGENTS.md `scripts/verify_release_completeness.py` — zero-size asset check
**Test:** `test_y20_zero_size_asset_is_completeness_failure`

### Y21 — Version-stamped asset name is required
Release assets must carry the version string in their filename (e.g., `gludd-v0.1.0-linux.tar.gz`); assets without a version stamp are flagged.
**Enforcement:** AGENTS.md `scripts/verify_release_completeness.py` — version stamp in asset names
**Test:** `test_y21_version_stamped_asset_name_required`

### Y22 — Draft release is not a release
A GitHub Release with `isDraft: true` is not a shipped release; any "released" claim is blocked regardless of asset count.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` — draft release does not satisfy evidence token
**Test:** `test_y22_draft_release_is_not_release`

### Y23 — Release cut → completeness wait is mandatory
`make release-cut` that times out on the completeness poll does not license a "shipped" claim; the agent must poll `verify-release-completeness` manually.
**Enforcement:** AGENTS.md `AGENTS.md` "release-cut timed out on its poll → manual completeness check required"
**Test:** `test_y23_release_cut_timeout_requires_manual_completeness`

### Y24 — Bumping version while current incomplete is blocked
The agent must not bump the version (edit `pyproject.toml`) while `verify-release-completeness` fails for the current tag.
**Enforcement:** AGENTS.md `enforce-clean-tree.ts` — version-bump commits blocked while state file signals incomplete
**Test:** `test_y24_bump_version_while_incomplete_blocked`

### Y25 — Release-completeness cached result is stale after 5 min
A `verify-release-completeness` result cached longer than 5 minutes is discarded; a fresh check is required before claiming done.
**Enforcement:** AGENTS.md `scripts/verify_release_completeness.py` — cache TTL=300s
**Test:** `test_y25_release_completeness_cache_stale_after_5min`

### Y26 — CI release job needs-gate ensures broken code never publishes
The CI workflow's `release` job `needs: [gate]` structurally prevents a broken build from publishing artifacts — but the agent must still check.
**Enforcement:** AGENTS.md `.github/workflows/build.yml` — job dependency + `scripts/require_ci_green.py`
**Test:** `test_y26_ci_release_needs_gate_structural_guard`

### Y27 — Tag push without prior green CI is blocked at release-cut
`make release-cut` step 0 calls `require_ci_green.py` and aborts on RED/in_progress — the tag push never fires.
**Enforcement:** AGENTS.md `scripts/require_ci_green.py` — `make release-cut` step 0/4
**Test:** `test_y27_tag_push_without_prior_green_ci_blocked`

### Y28 — Release-branch CI green is immutable
Once a release branch tip has CI-GREEN, no new commits may land; the push guard blocks them.
**Enforcement:** AGENTS.md `scripts/check_green_branch_guard.py` — green-tip immutability
**Test:** `test_y28_release_branch_green_is_immutable`

### Y29 — Release-branch CI red requires fix on same branch
A CI-RED release branch must be fixed with a direct commit on that branch, not a parallel branch.
**Enforcement:** AGENTS.md `AGENTS.md` "Fix-forward on the branch, not around it"
**Test:** `test_y29_ci_red_release_branch_fix_on_same_branch`

### Y30 — Release tag artifacts and git tag must be equivalent
The git tag `v0.1.0` and the GitHub Release `v0.1.0` must reference the same commit SHA; mismatch is a completeness failure.
**Enforcement:** AGENTS.md `scripts/verify_release_completeness.py` — tag-SHA vs release-SHA comparison
**Test:** `test_y30_release_tag_and_artifact_sha_must_match`

---

## Objective-Driven Continuation (Y31–Y45)

### Y31 — User objective unmet blocks all text-only responses
When the user has stated an objective (in the current message or earlier) and that objective is not fully satisfied, any text-only response is denied.
**Enforcement:** AGENTS.md `enforce-stop.ts` `text.complete` — objective-tracking state in `/tmp/gludd-user-objective.json`
**Test:** `test_y31_user_objective_unmet_blocks_text_only`

### Y32 — User "done" is the sole legitimate stop condition
The agent may stop sending tool calls ONLY when the user's most recent message explicitly approves completion (phrases: "done", "that's enough", "stop here", "I'm satisfied").
**Enforcement:** AGENTS.md `enforce-stop.ts` `text.complete` — `USER_STOP_PHRASES` regex drives allow gate
**Test:** `test_y32_user_done_is_sole_legitimate_stop`

### Y33 — "Looks good" is not "done"
A user message reading "looks good" or "nice" without an explicit "done" or "stop" is not a stop signal; work continues.
**Enforcement:** AGENTS.md `enforce-stop.ts` `text.complete` — `USER_STOP_PHRASES` must match explicitly
**Test:** `test_y33_looks_good_is_not_done`

### Y34 — Implicit objectives from TASKS.md are tracked
Any unchecked `- [ ]` item in TASKS.md is treated as an active user objective; it blocks stop even if the user never said it in the current session.
**Enforcement:** AGENTS.md `enforce-stop.ts` `hasRealPendingWork()` — TASKS.md unchecked items
**Test:** `test_y34_implicit_objectives_from_tasks_tracked`

### Y35 — User instruction "continue" requires immediate dispatch wave
When the user says `continue`, `resume`, `keep working`, or equivalent, the next action must be a ≥10-wide dispatch wave with zero intervening reads or edits.
**Enforcement:** AGENTS.md `enforce-session-start.ts` — session-start protocol re-triggered on "continue"
**Test:** `test_y35_continue_requires_immediate_dispatch_wave`

### Y36 — User instruction "fix this" requires fix-before-new-work
A directive like "fix X FIRST" must be completed (tested, committed, verified) before any new feature work begins.
**Enforcement:** AGENTS.md `AGENTS.md` "Instruction-Following Priority" — fix-first overrides plan
**Test:** `test_y36_fix_this_requires_fix_before_new_work`

### Y37 — Multi-step user request is tracked atomically
When the user requests "A, B, and C," all three are added to the task ledger; completing A and B but not C is not done.
**Enforcement:** AGENTS.md `todowrite` state + `enforce-stop.ts` `hasRealPendingWork()` checks todowrite
**Test:** `test_y37_multi_step_user_request_tracked_atomically`

### Y38 — User priority is AND, not OR
A new user directive stacks on existing objectives; it does not replace them. "Fix guardrails NOW" means fix guardrails AND continue all other work.
**Enforcement:** AGENTS.md `AGENTS.md` "Priority Stacking (AND not OR)"
**Test:** `test_y38_user_priority_is_and_not_or`

### Y39 — New instruction must not reduce subagent count
After a user message with a new instruction, the next dispatch wave must be ≥10 — never fewer than the floor.
**Enforcement:** AGENTS.md `enforce-session-start.ts` — dispatch-count check after user message
**Test:** `test_y39_new_instruction_must_not_reduce_subagent_count`

### Y40 — Word "done" in user message with qualifier is not "done"
"I'm done with step 1, now do step 2" — "done" is scoped to step 1; work continues to step 2.
**Enforcement:** AGENTS.md `enforce-stop.ts` `USER_STOP_PHRASES` — requires scoping analysis
**Test:** `test_y40_qualified_done_is_not_full_stop`

### Y41 — User silence is not permission to stop
If the user has been silent for N turns, the agent must NOT infer consent to stop — it must continue to the next pending objective.
**Enforcement:** AGENTS.md `enforce-stop.ts` + `agent_watchdog.py` — idle detection triggers CONTINUE directive
**Test:** `test_y41_user_silence_not_permission_to_stop`

### Y42 — Q&A answer about status must resume work
A user asking "what did you do?" must receive a brief answer AND an immediate tool call continuing work — never just the answer.
**Enforcement:** AGENTS.md `enforce-stop.ts` `QA_RESPONSE_PATTERNS` — Q&A text-only with pending work is blanked
**Test:** `test_y42_qa_answer_must_resume_work`

### Y43 — "How's it going?" is not "stop"
A user asking about progress is requesting a status update — the response must be status + continuation tool call, never status-only.
**Enforcement:** AGENTS.md `enforce-stop.ts` `QA_RESPONSE_PATTERNS` + `STATUS_SUMMARY_RE`
**Test:** `test_y43_progress_query_must_include_continuation`

### Y44 — Objective nesting: sub-objective done ≠ parent done
Completing "write tests for X" does not complete "ship feature X"; the parent objective remains active.
**Enforcement:** AGENTS.md `todowrite` parent-child linkage + `enforce-stop.ts` parent-check
**Test:** `test_y44_sub_objective_done_is_not_parent_done`

### Y45 — Objective staleness does not license stop
An objective that has been pending for N hours is still an active objective; staleness is not a cancellation reason.
**Enforcement:** AGENTS.md `enforce-stop.ts` — no staleness exemption in `hasRealPendingWork()`
**Test:** `test_y45_objective_staleness_not_license_to_stop`

---

## Exhaustion-Based Stopping Criteria (Y46–Y60)

### Y46 — Only exhaustion of all pending work permits stop
A text-only response is permitted ONLY when ALL of {TASKS.md unchecked=0, ratchet.yml empty, gate green, CI green, release complete, user objective satisfied} are true simultaneously.
**Enforcement:** AGENTS.md `enforce-stop.ts` `hasRealPendingWork()` — full conjunct of all conditions
**Test:** `test_y46_only_full_exhaustion_permits_stop`

### Y47 — Empty todowrite is not evidence of exhaustion
An empty `todowrite` list while TASKS.md has unchecked items does not satisfy the stop gate; `hasRealPendingWork()` checks all sources.
**Enforcement:** AGENTS.md `enforce-stop.ts` — `hasRealPendingWork()` does not use `todowrite` as sole source
**Test:** `test_y47_empty_todowrite_is_not_exhaustion`

### Y48 — Ratchet entries block stop
Any entry in `config/ratchet.yml` — even a single `# pending` annotation — blocks text-only responses.
**Enforcement:** AGENTS.md `enforce-stop.ts` `hasRealPendingWork()` — `config/ratchet.yml` non-empty check
**Test:** `test_y48_ratchet_entries_block_stop`

### Y49 — Gate-red blocks stop
`.gate-status` showing FAIL or RUNNING blocks text-only responses; the gate must be green or absent.
**Enforcement:** AGENTS.md `enforce-stop.ts` `hasRealPendingWork()` — `.gate-status` read
**Test:** `test_y49_gate_red_blocks_stop`

### Y50 — Task exhaustion is mechanical, not subjective
Whether work is "done" is determined by machine checks (file state, process exit codes, API responses), never by agent self-assessment.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` — done-words require mechanical evidence
**Test:** `test_y50_task_exhaustion_is_mechanical_not_subjective`

### Y51 — False-negative stop (all conditions met but agent stops) is detectable
If the agent emits a text-only response and a later audit finds it satisfied all conditions, that instance is logged as a false-negative but is NOT a policy violation.
**Enforcement:** AGENTS.md `scripts/stop_condition_audit.py` — post-hoc comparator
**Test:** `test_y51_false_negative_stop_is_detectable`

### Y52 — Stop conditions are re-evaluated on every turn
The exhaustion check is evaluated fresh on each `text.complete` hook invocation — no caching, no "already checked this session."
**Enforcement:** AGENTS.md `enforce-stop.ts` — `hasRealPendingWork()` called on every `text.complete`
**Test:** `test_y52_stop_conditions_reevaluated_every_turn`

### Y53 — Partial exhaustion check (e.g., only TASKS) is insufficient
An agent checking only TASKS.md and ignoring ratchet/gate/CI/release is not performing a valid exhaustion check.
**Enforcement:** AGENTS.md `enforce-stop.ts` — `hasRealPendingWork()` enumerates all checklist items
**Test:** `test_y53_partial_exhaustion_check_insufficient`

### Y54 — Exhaustion after user "done" is exempt
When user says "done," the exhaustion check is skipped — user override takes priority over all mechanical gates.
**Enforcement:** AGENTS.md `enforce-stop.ts` `USER_STOP_PHRASES` — bypasses `hasRealPendingWork()` entirely
**Test:** `test_y54_exhaustion_after_user_done_is_exempt`

### Y55 — Exhaustion state must be observable
The agent must expose a `make yield-status` target that prints the current exhaustion stack in human-readable form.
**Enforcement:** AGENTS.md `Makefile` `yield-status` target
**Test:** `test_y55_exhaustion_state_must_be_observable`

### Y56 — Exhaustion false-positive (agent thinks done but isn't) is a logged bug
If the agent bypasses enforcement (e.g., `GLUDD_STOP_ENFORCE=0`) and stops with pending work, it is logged in BUGS.md with the stop-condition dump.
**Enforcement:** AGENTS.md `AGENTS.md` "Premature-Stop Audit Policy" + BUGS.md logging
**Test:** `test_y56_exhaustion_false_positive_is_logged_bug`

### Y57 — Exhaustion does not consider obsolete gates
A gate `.gate-status` from a prior branch or commit that is no longer the working-tree state is stale and is not considered in the exhaustion check.
**Enforcement:** AGENTS.md `enforce-stop.ts` — `.gate-status` SHA comparison with HEAD
**Test:** `test_y57_obsolete_gate_not_considered_in_exhaustion`

### Y58 — Exhaustion requires zero uncommitted changes
`git status --porcelain` non-empty is a pending-work condition; exhaustion requires a clean tree.
**Enforcement:** AGENTS.md `enforce-stop.ts` `hasRealPendingWork()` — dirty tree check
**Test:** `test_y58_exhaustion_requires_zero_uncommitted_changes`

### Y59 — Exhaustion requires zero unmerged worktree branches
Active agent worktrees (from `make agent-worktree-list`) with unmerged branches count as pending work.
**Enforcement:** AGENTS.md `enforce-stop.ts` — active worktree check via `git worktree list`
**Test:** `test_y59_exhaustion_requires_zero_unmerged_worktrees`

### Y60 — Exhaustion is not rechecked after user "done"
Once the user says "done," the agent must not recheck exhaustion and self-redispatch — the user's word is final.
**Enforcement:** AGENTS.md `enforce-stop.ts` — user-done flag suppresses recheck loop
**Test:** `test_y60_exhaustion_not_rechecked_after_user_done`

---

## Background-Operation Non-Blocking (Y61–Y75)

### Y61 — Background gate does not block dispatch
While `make gate-background` is running, the agent must dispatch other agents — the foreground stays free.
**Enforcement:** AGENTS.md `enforce-no-wait.ts` — background-ops do not gate dispatch
**Test:** `test_y61_background_gate_does_not_block_dispatch`

### Y62 — `sleep N && make` on main thread is denied
Any bash call matching `sleep \d+ &&` on the main thread (not via Task dispatch) is blocked.
**Enforcement:** AGENTS.md `enforce-no-wait.ts` `tool.execute.before` — sleep-matcher regex
**Test:** `test_y62_sleep_make_on_main_thread_denied`

### Y63 — `make gate-tail` on main thread is denied
Following the gate log live blocks ALL dispatch — it is denied on the main thread.
**Enforcement:** AGENTS.md `enforce-no-wait.ts` — `gate-tail` matcher
**Test:** `test_y63_gate_tail_on_main_thread_denied`

### Y64 — `make ci-verdict` as standalone main-thread call is denied
Under the anti-loop directive, bare `make ci-verdict` on the main thread (not inside a dispatch message) is blocked.
**Enforcement:** AGENTS.md `enforce-floor.ts` — ANTI-LOOP directive: standalone `make ci-verdict` denied
**Test:** `test_y64_bare_ci_verdict_on_main_thread_denied`

### Y65 — Background-op result polling from subagent, not main thread
`make gate-status-check` dispatched via Task tool is allowed; same call as a non-dispatch main-thread bash tool is denied.
**Enforcement:** AGENTS.md `enforce-no-wait.ts` — context-aware: allows when dispatched, denies when main-thread
**Test:** `test_y65_background_polling_must_be_from_subagent`

### Y66 — Agent must not wait for background-op before dispatching next wave
After launching a background operation, the next tool call must be a dispatch wave — not a status check or text output.
**Enforcement:** AGENTS.md `enforce-multitask.ts` — post-background-launch zero-dispatch is denied
**Test:** `test_y66_no_wait_for_background_op_before_next_dispatch`

### Y67 — Long foreground op is denied with suggestion
`make gate`, `make test-unit`, `make qa`, `make validate` on the main thread are denied; deny message includes `SUGGESTION: make gate-background`.
**Enforcement:** AGENTS.md `enforce-make.ts` `tool.execute.before` — long-op matcher
**Test:** `test_y67_long_foreground_op_denied_with_suggestion`

### Y68 — Background-op log is streamed, not buffered
Any Makefile target that backgrounds a long operation must pipe output through `tee` so it is observable.
**Enforcement:** AGENTS.md `tests/unit/test_observability_guardrails.py` — no silent `> /dev/null 2>&1`
**Test:** `test_y68_background_log_must_be_observable`

### Y69 — Background-op heartbeat is emitted at least every 60s
Any poller subagent watching a background op must report a timestamped status line at least once per 60s tick.
**Enforcement:** AGENTS.md `AGENTS.md` "No Unseen Events" — heartbeat rule
**Test:** `test_y69_background_op_heartbeat_required`

### Y70 — Background-op failure surfaces captured log
When a background gate fails, the orchestrator must tail/print the captured log — not silently report "FAIL."
**Enforcement:** AGENTS.md `AGENTS.md` "Failures must surface their cause"
**Test:** `test_y70_background_op_failure_surfaces_log`

### Y71 — Background op unknown state must be disambiguated
If a background op's log file exists but no terminal marker is found and no process is running, the state is "orphaned" — the agent must restart or investigate, not report "still running."
**Enforcement:** AGENTS.md `Makefile` `gate-status-check` — orphan detection
**Test:** `test_y71_background_op_unknown_state_disambiguated`

### Y72 — No more than one background gate at a time
Launching `make gate-background` when `.gate-background.pid` already points to a running process is denied.
**Enforcement:** AGENTS.md `Makefile` `gate-background` — PID file existence + liveness check
**Test:** `test_y72_no_more_than_one_background_gate`

### Y73 — Background-op completion must trigger codification
When `make gate-status-check` reports PASS, the agent must immediately codeify the result (commit pending work, update TASKS.md, tick items) — not just acknowledge it.
**Enforcement:** AGENTS.md `enforce-stop.ts` — post-PASS text-only "acknowledgment" is blanked
**Test:** `test_y73_background_op_completion_triggers_codification`

### Y74 — Agent cannot claim "gate passed" from a stale PID
If `.gate-background.pid`'s process is dead and the log lacks a terminal marker, "gate passed" is a false claim.
**Enforcement:** AGENTS.md `enforce-verified-claims.ts` — "passed" requires fresh terminal marker
**Test:** `test_y74_gate_passed_claim_requires_fresh_terminal_marker`

### Y75 — Background op restart preserves prior log
When re-launching a background gate after a failure, the prior log is rotated (not deleted) so failure analysis is possible.
**Enforcement:** AGENTS.md `Makefile` `gate-background` — log rotation on re-launch
**Test:** `test_y75_background_op_restart_preserves_prior_log`

---

## Session-Continuity Discipline (Y76–Y90)

### Y76 — Session start reads task backlog before any action
The first tool-call message of every session must include reads of TASKS.md, BUGS.md, config/ratchet.yml, and SESSION.md in parallel.
**Enforcement:** AGENTS.md `enforce-session-start.ts` `tool.execute.before` — task-file read required before mutation
**Test:** `test_y76_session_start_reads_task_backlog_first`

### Y77 — Session start second action is dispatch wave
The tool-call message immediately following the backlog reads must contain ≥10 task/agent/workflow dispatches — no intervening reads, edits, or bash.
**Enforcement:** AGENTS.md `enforce-session-start.ts` — dispatch requirement after task-file reads
**Test:** `test_y77_session_start_second_action_is_dispatch_wave`

### Y78 — Prose-first session start is blocked
A session that begins with text output ("Sure! Let me check...") before any tool calls is denied; the first response must contain tool calls.
**Enforcement:** AGENTS.md `enforce-session-start.ts` `tool.execute.before` — first-response gate
**Test:** `test_y78_prose_first_session_start_is_blocked`

### Y79 — Session start within 5-min window
If ≥5 minutes elapse from session start to the first dispatch wave, the session is in violation.
**Enforcement:** AGENTS.md `enforce-session-start.ts` — time-to-dispatch timer (hard deny at 120s)
**Test:** `test_y79_session_start_within_5_min_window`

### Y80 — Session SESSION.md is written before shutdown
When the agent completes all work and user says "done," SESSION.md must be updated with last commit, test counts, and next steps before the final response.
**Enforcement:** AGENTS.md `AGENTS.md` "Session Persistence Policy"
**Test:** `test_y80_session_md_written_before_shutdown`

### Y81 — Session crash recovery resets stale state
A stale `/tmp/gludd-session-start.json` from a prior crashed session (PID mismatch or age >300s) is reset to fresh on next start.
**Enforcement:** AGENTS.md `enforce-session-start.ts` `loadState()` — PID mismatch + STALE_MS gate
**Test:** `test_y81_session_crash_recovery_resets_stale_state`

### Y82 — Session state file is written atomically
The session-start state file uses `renameSync` across a temp file to prevent partial-read races.
**Enforcement:** AGENTS.md `enforce-session-start.ts` `saveState()` — temp-file + renameSync
**Test:** `test_y82_session_state_file_written_atomically`

### Y83 — Session dispatch counter is session-scoped, not global
The counter tracking dispatches for session-start enforcement resets at session boundary (new process PID), not at conversation turn.
**Enforcement:** AGENTS.md `enforce-session-start.ts` — PID in state file + reset on mismatch
**Test:** `test_y83_session_dispatch_counter_is_session_scoped`

### Y84 — Session SESSION.md staleness is detectable
If SESSION.md's `Last updated` date is older than the last git commit, the file is stale — the agent must update it.
**Enforcement:** AGENTS.md `AGENTS.md` "Never leave it stale" + `enforce-stop.ts` stale-session check
**Test:** `test_y84_session_md_staleness_is_detectable`

### Y85 — Session handoff: SESSION.md Next Steps transfers to TASKS.md
Items listed in SESSION.md "Next Steps" from the prior session must appear as unchecked items in TASKS.md before any new work is dispatched.
**Enforcement:** AGENTS.md `enforce-session-start.ts` — Next Steps → TASKS.md migration check
**Test:** `test_y85_session_handoff_next_steps_transfer_to_tasks`

### Y86 — Session must not forget user context between waves
Any user directive received in wave N must be reflected in the dispatch planning for wave N+1 — directives are not dropped at the wave boundary.
**Enforcement:** AGENTS.md `AGENTS.md` "Never re-dispatch completed work" + user-objective state file
**Test:** `test_y86_session_must_not_forget_user_context_between_waves`

### Y87 — Session audit on close (TASKS.md × BUGS.md × ratchet)
Before the final response of a session (when user says "done"), the agent must cross-check all three backlogs and log any discrepancies.
**Enforcement:** AGENTS.md `AGENTS.md` "Self-Audit Policy"
**Test:** `test_y87_session_close_audit_cross_check_backlogs`

### Y88 — SESSION.md must survive agent crash
If the agent process crashes, SESSION.md and the state files in /tmp/gludd-* must survive as the recovery source for the next session.
**Enforcement:** AGENTS.md `enforce-session-start.ts` crash recovery path reads SESSION.md + state files
**Test:** `test_y88_session_md_must_survive_agent_crash`

### Y89 — Session must track uncommitted work across shutdown
Any uncommitted changes at session close are flagged in SESSION.md "Known Gaps" and surfaced on next session start.
**Enforcement:** AGENTS.md `enforce-stop.ts` — dirty-tree check at text-only allowance window
**Test:** `test_y89_session_tracks_uncommitted_work_across_shutdown`

### Y90 — Session blank-slate amnesia is forbidden
A new session that opens with no awareness of prior work (ignoring SESSION.md/TASKS.md entirely) is a policy violation — the agent must read the backlog files.
**Enforcement:** AGENTS.md `enforce-session-start.ts` `tool.execute.before` — task-file read gate
**Test:** `test_y90_session_blank_slate_amnesia_forbidden`

---

## Auto-Resume Patterns & Idle/Dormant Prohibition (Y91–Y100)

### Y91 — Idle session with pending work triggers watchdog CONTINUE
When the watchdog detects an idle session (>60s no tool calls) with pending work, it injects a CONTINUE directive into the session.
**Enforcement:** AGENTS.md `agent_watchdog.py` — idle detection + CONTINUE injection
**Test:** `test_y91_idle_session_triggers_watchdog_continue`

### Y92 — Agent must not enter dormant state while work exists
The agent must produce tool calls continuously while work is pending; any gap >30s without a tool call is a dormant state and is flagged.
**Enforcement:** AGENTS.md `agent_watchdog.py` + `enforce-floor.ts` — 30s sliding-window streak
**Test:** `test_y92_agent_must_not_enter_dormant_state`

### Y93 — Auto-resume after subagent result arrival
When a subagent returns a result, the agent must process it and issue the next tool call within 10s — not "sit on it" while waiting for other results.
**Enforcement:** AGENTS.md `enforce-floor.ts` — post-result fast-processing rule (POST_RESULT_READ_LIMIT=3)
**Test:** `test_y93_auto_resume_after_subagent_result_arrival`

### Y94 — Auto-resume after CI verdict change
When CI transitions from in_progress to success/failure (detected by periodic check), the agent must act on the result immediately — not "note it for later."
**Enforcement:** AGENTS.md `enforce-stop.ts` — CI state-change triggers pending-work recheck
**Test:** `test_y94_auto_resume_after_ci_verdict_change`

### Y95 — Auto-resume after background gate completion
When `make gate-status-check` reports the terminal marker, the agent must ingest and act within one turn — no "I'll look at this after the next wave."
**Enforcement:** AGENTS.md `enforce-stop.ts` — gate terminal-marker detection re-evaluates exhaustion
**Test:** `test_y95_auto_resume_after_background_gate_completion`

### Y96 — Dormant state with live subagents is still dormant
Having subagents running does not excuse a dormant main thread — the orchestrator must still produce tool calls (dispatch replacements, process results, etc.).
**Enforcement:** AGENTS.md `enforce-floor.ts` — main-thread dormancy tracked independently of subagent count
**Test:** `test_y96_dormant_state_with_live_subagents_is_still_dormant`

### Y97 — "Waiting for subagents" is not a tool-call substitute
The agent must not text-output "waiting for subagents to complete" as a substitute for dispatching more work or processing returned results.
**Enforcement:** AGENTS.md `enforce-stop.ts` — "waiting for" pattern in text matched and blanked
**Test:** `test_y97_waiting_for_subagents_not_tool_call_substitute`

### Y98 — Idle with text output is worse than idle-with-silence
A dormant session that occasionally emits "still working on it..." text is violating the same rule as total silence; text without tool calls is not progress.
**Enforcement:** AGENTS.md `enforce-stop.ts` — text-only response with pending work is blanked regardless of content
**Test:** `test_y98_idle_with_text_worse_than_idle_with_silence`

### Y99 — Every response must produce visible output
A tool-call message that takes no user-visible action (e.g., a single read with output not surfaced) appears stalled; the agent must always produce visible status.
**Enforcement:** AGENTS.md `AGENTS.md` "Always provide a visual status update"
**Test:** `test_y99_every_response_must_produce_visible_output`

### Y100 — Only stop when user says "done"
The terminal condition of the agent's work loop is: user explicit stop signal received AND all stop-gate conditions satisfied, OR user explicit stop signal received (override). No other exit path.
**Enforcement:** AGENTS.md `enforce-stop.ts` `USER_STOP_PHRASES` bypass + `enforce-make.ts` + `agent_watchdog.py` — three-layer terminal-gate
**Test:** `test_y100_only_stop_when_user_says_done`

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

