# BEHAVIORAL ENFORCEMENT SPECIFICATIONS — 200 numbered specs

**Version:** 1.0  
**Date:** 2026-07-19  
**Status:** Active — corresponding enforcement mechanisms tracked in `tests/unit/test_behavioral_specs.py`

Each spec defines a behavioral invariant. Each spec MUST have a corresponding
enforcement mechanism (plugin, Makefile guard, or AGENTS.md policy section) and
a structural test verifying that mechanism exists.

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

---

## Audit Log

| Date | Change | Author |
|------|--------|--------|
| 2026-07-19 | Initial 200 specs created | Agent |
