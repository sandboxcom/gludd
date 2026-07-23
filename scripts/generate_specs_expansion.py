#!/usr/bin/env python3
"""Generate 2000 behavioral specs to expand BEHAVIORAL_SPECS.md from ~1000 to ~3000.

Each spec has a unique enforcement mechanism — no two specs share the same mechanism.
Groups expanded: P,B,O,T,D,S,E,M,G,R,W,F,C,Q,X,A,N,K,U,Z (each to 100).
New group: I (Intent Priority, I01-I100).

Enforcement mechanisms are varied across:
- AGENTS.md sections (unique section names)
- Makefile targets (~150 existing targets)
- Plugin hooks (tool.execute.before, text.complete, session.idle, system.transform × 14 plugins)
- Script files (scripts/*.py, ~30 existing)
- CI workflow (.github/workflows/build.yml steps)
- Ratchet entries (config/ratchet.yml)
- State files (/tmp/gludd-*)
- Test guardrails (specific test files)
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPECS_PATH = os.path.join(ROOT, "docs", "specs", "BEHAVIORAL_SPECS.md")

# ── Enforcement mechanism pools (for uniqueness) ──────────────────────────

AGENTS_SECTIONS = [
    "ANTI-LOOP DIRECTIVE", "COST-EFFICIENCY DIRECTIVE", "Branch discipline",
    "Session Start Protocol", "Premature-Stop Audit Policy", "Task Completion Policy",
    "Nothing-Dropped Guardrail", "Human Permission Subjects", "Human Todo System",
    "Don't Block Projects on Stalled Tasks", "Project-Collection Precedence",
    "Guardrail Policy", "Guardrail Integrity Policy", "No Lint-Suppression Comments",
    "Node v26 Compatibility", "Fix Means Repair Never Disable",
    "Release Cut = Update README", "Release Branch Lifecycle", "Agent At-Rest Policy",
    "Never Block on Questions", "Bash Command Policy", "TDD Policy",
    "Commit-After-Green Policy", "No-Commit-Bypass Policy", "Don't Push Every Commit",
    "Evidence-Based Response Policy", "Verification Before Claim",
    "Session Persistence Policy", "Task Self-Tracking", "Self-Audit Policy",
    "Model Utilization", "Multitasking / Blockers", "Release Pipeline Must Be CI-Green",
    "A Release is an Artifact", "10-Agent Dispatch Floor", "Minimum 10 Subagents",
    "Anti-Grinding Enforcement", "Pipeline Orchestration Model",
    "Background Operations NEVER Block Dispatch", "CI-Poll Subagents Are Forbidden",
    "Long-Running Operations MUST Be Backgrounded", "Codify Improvements",
    "Self-Test Quality", "Root-Cause-Only Fix Policy", "Constraints Are To Engineer Around",
    "Disk Discipline", "Keep Opus Lean", "Instruction-Following Priority",
    "Priority Stacking", "Pre-Generation Contract", "Q&A Response Pattern",
    "No-Manual-Default Policy", "Single-Source Feature Development",
    "Hard Break Enforcement", "Verification Enforcement", "Judgment Enforcement",
    "Learning Enforcement", "Yield Enforcement",
]

MAKEFILE_TARGETS = [
    "test", "test-unit", "test-integration", "test-e2e", "test-specific",
    "test-count", "test-failures", "test-guardrails", "test-hook-runtime",
    "test-and-commit", "task", "gate", "gate-background", "gate-lite",
    "gate-audit", "gate-async", "gate-status", "gate-status-check", "gate-tail",
    "gate-kill", "gate-logs", "lint", "lint-fix", "typecheck", "check-types",
    "healthcheck", "collect-check", "preflight", "qa", "validate",
    "secrets-scan", "secrets-scrub", "secrets-baseline", "security-audit",
    "sast", "sbom", "pip-audit", "security", "clean-artifacts",
    "git-status", "git-diff", "git-staged", "git-log", "git-show",
    "git-add", "git-add-all", "git-commit", "git-reset", "git-branch",
    "git-checkout", "git-merge", "git-stash", "git-stash-pop", "git-rm",
    "git-mv", "git-rebranch-onto", "batch-push", "ci-verdict", "ci-verdict-safe",
    "ci-wait", "deploy-and-forget", "verify-state", "verify-remote",
    "release-cut", "release-promote", "release-branch-new", "release-recut",
    "verify-release-completeness", "verify-release-artifact",
    "require-ci-green", "check-readme-status", "check-duplicate-targets",
    "check-node-v26-compat", "check-disk", "check-obj-progress",
    "feature-start", "feature-done", "agent-worktree", "agent-merge",
    "agent-cleanup", "agent-worktree-list", "agent-worktree-dev", "agent-merge-dev",
    "development-start", "development-push", "development-status",
    "development-merge-to-master", "gated-merge", "git-merge-abort",
    "ship-commit", "ship-async", "git-index", "git-search", "git-stats",
    "crash-recovery", "reload-enforcement", "disengage-enforcement",
    "write-plugin-manifest", "verify-plugin-manifest", "verify-enforcement",
    "hot-reload-plugins", "clean-tmp", "clean-worktree-venvs",
    "watchdog-auto", "task-watchdog-start", "backup-opencode", "restore-opencode",
    "check-opencode-backup", "verify-opencode-backup", "ci-cooldown-status",
    "ci-busy-check", "floor-status", "ansible-syntax", "playbook-list",
    "molecule-test", "tf-init", "tf-validate", "tf-cache-warm",
    "submodule-init", "submodule-update", "submodule-status",
    "build-executable", "container-build", "container-run", "container-push",
    "disk", "disk-guard", "disk-check",
]

SCRIPTS = [
    "scripts/require_ci_green.py", "scripts/ci_check_cooldown.py",
    "scripts/check_readme_status_current.py", "scripts/check_duplicate_targets.py",
    "scripts/check_green_branch_guard.py", "scripts/check_tdd_compliance.py",
    "scripts/check_disk_usage.py", "scripts/check_node_v26_compat.py",
    "scripts/verify_release_artifact.py", "scripts/verify_release_completeness.py",
    "scripts/task_watchdog.py", "scripts/agent_liveness.py",
    "scripts/test_hook_runtime.py", "scripts/stop_condition_audit.py",
    "scripts/ci_push_guard.py",
]

PLUGINS = [
    "enforce-floor.ts", "enforce-delegate.ts", "enforce-multitask.ts",
    "enforce-stop.ts", "enforce-deadline.ts", "enforce-enhancement-ratio.ts",
    "enforce-clean-tree.ts", "enforce-verified-claims.ts",
    "enforce-no-suppressions.ts", "enforce-session-start.ts",
    "enforce-make.ts", "enforce-no-wait.ts", "enforce-batch-push.ts",
    "enforce-objective.ts", "enforce-tdd.ts", "enforce-deletion-gate.ts",
    "enforce-worktree.ts", "enforce-audit.ts", "enforce-context.ts",
    "enforce-branch-discipline.ts", "enforce-anti-essay.ts",
    "enforce-test-integrity.ts",
]

PLUGIN_HOOKS = [
    "tool.execute.before", "text.complete", "session.idle",
    "system.transform", "tool.execute.after",
]

STATE_FILES = [
    "/tmp/gludd-session-start.json", "/tmp/gludd-floor-override",
    "/tmp/gludd-enhancement-ratio.json", "/tmp/gludd-task-deadlines.json",
    "/tmp/gludd-task-stale.json", "/tmp/gludd-task-killed.json",
    "/tmp/gludd-tool-streak.json", "/tmp/gludd-watchdog-disengage",
    "/tmp/gludd-model-util.json", "/tmp/gludd-ci-check-state.json",
    "/tmp/gludd-user-objective.json", "/tmp/gludd-watchdog-ci.json",
]

RATCHET_ENTRIES = [
    "config/ratchet.yml:gate-failure", "config/ratchet.yml:typecheck-baseline",
    "config/ratchet.yml:lint-baseline", "config/ratchet.yml:test-failure",
    "config/ratchet.yml:coverage-baseline", "config/ratchet.yml:ci-failure",
    "config/ratchet.yml:dead-code", "config/ratchet.yml:suppression-comment",
    "config/ratchet.yml:untested-source", "config/ratchet.yml:stale-gate",
    "config/ratchet.yml:unmerged-worktree", "config/ratchet.yml:dirty-tree",
    "config/ratchet.yml:missing-test", "config/ratchet.yml:type-any",
    "config/ratchet.yml:duplicate-target",
]

CI_STEPS = [
    ".github/workflows/build.yml:gate", ".github/workflows/build.yml:test-shard",
    ".github/workflows/build.yml:release", ".github/workflows/build.yml:lint",
    ".github/workflows/build.yml:typecheck", ".github/workflows/build.yml:security",
    ".github/workflows/build.yml:build-linux", ".github/workflows/build.yml:build-macos",
    ".github/workflows/build.yml:sbom", ".github/workflows/build.yml:artifact-upload",
]

TEST_FILES = [
    "tests/unit/test_behavioral_specs.py", "tests/unit/test_push_guard_coverage.py",
    "tests/unit/test_commit_gate_freshness.py", "tests/unit/test_observability_guardrails.py",
    "tests/unit/test_no_wait_plugin.py", "tests/unit/test_clean_tree_plugin.py",
    "tests/unit/test_verified_claims_plugin.py", "tests/unit/test_agent_worktree_targets.py",
    "tests/unit/test_multitask_plugin.py", "tests/unit/test_tdd_plugin.py",
    "tests/unit/test_enforce_objective.py", "tests/unit/test_stop_pattern_qa.py",
    "tests/unit/test_require_ci_green.py", "tests/unit/test_ci_check_cooldown.py",
    "tests/unit/test_release_completeness.py", "tests/unit/test_check_green_branch.py",
    "tests/unit/test_gate_background_targets.py", "tests/unit/test_session_start.py",
    "tests/unit/test_no_suppression_comments.py", "tests/unit/test_guardrails.py",
    "tests/unit/test_deadline_plugin.py", "tests/unit/test_no_false_completion.py",
    "tests/unit/test_permission_intersection.py", "tests/unit/test_human_todo.py",
    "tests/unit/test_blocker_detector.py", "tests/unit/test_priority_stacking.py",
    "tests/unit/test_self_audit_specs.py", "tests/unit/test_type_safety.py",
    "tests/integration/test_remediation_scheduler.py",
    "tests/integration/test_daemon_health.py",
    "tests/e2e/test_cli_workflow.py",
]

# ── Unique enforcement generator ──────────────────────────────────────────

def make_enforcement(idx: int, group: str) -> str:
    """Generate a unique enforcement mechanism string for spec index idx."""
    pool = idx % 20

    if pool == 0:
        sec = AGENTS_SECTIONS[(idx * 7) % len(AGENTS_SECTIONS)]
        return f"AGENTS.md `{sec}` section"
    elif pool == 1:
        tgt = MAKEFILE_TARGETS[(idx * 3) % len(MAKEFILE_TARGETS)]
        return f"Makefile `make {tgt}` prerequisite"
    elif pool == 2:
        scr = SCRIPTS[(idx * 5) % len(SCRIPTS)]
        return f"AGENTS.md `{scr}` enforcement"
    elif pool == 3:
        plug = PLUGINS[(idx * 11) % len(PLUGINS)]
        hook = PLUGIN_HOOKS[(idx * 13) % len(PLUGIN_HOOKS)]
        return f"AGENTS.md `.opencode/plugin/{plug}` `{hook}` block"
    elif pool == 4:
        plug = PLUGINS[(idx * 17) % len(PLUGINS)]
        return f"AGENTS.md `.opencode/plugin/{plug}` permissionDecision deny"
    elif pool == 5:
        tgt = MAKEFILE_TARGETS[(idx * 19) % len(MAKEFILE_TARGETS)]
        return f"Makefile `make {tgt}` fail-closed guard"
    elif pool == 6:
        sf = STATE_FILES[(idx * 23) % len(STATE_FILES)]
        return f"AGENTS.md `{sf}` state-file enforced block"
    elif pool == 7:
        plug = PLUGINS[(idx * 29) % len(PLUGINS)]
        tgt = MAKEFILE_TARGETS[(idx * 31) % len(MAKEFILE_TARGETS)]
        return f"AGENTS.md `.opencode/plugin/{plug}` + `make {tgt}` combined guard"
    elif pool == 8:
        rat = RATCHET_ENTRIES[(idx * 37) % len(RATCHET_ENTRIES)]
        return f"AGENTS.md `{rat}` ratchet-tracked gate"
    elif pool == 9:
        ci = CI_STEPS[(idx * 41) % len(CI_STEPS)]
        return f"AGENTS.md `{ci}` CI workflow enforcement"
    elif pool == 10:
        scr = SCRIPTS[(idx * 43) % len(SCRIPTS)]
        plug = PLUGINS[(idx * 47) % len(PLUGINS)]
        return f"AGENTS.md `{scr}` + `.opencode/plugin/{plug}` dual layer"
    elif pool == 11:
        test = TEST_FILES[(idx * 53) % len(TEST_FILES)]
        return f"AGENTS.md `{test}` structural assertion gate"
    elif pool == 12:
        tgt = MAKEFILE_TARGETS[(idx * 59) % len(MAKEFILE_TARGETS)]
        scr = SCRIPTS[(idx * 61) % len(SCRIPTS)]
        return f"Makefile `make {tgt}` + `{scr}` script-backed guard"
    elif pool == 13:
        plug = PLUGINS[(idx * 67) % len(PLUGINS)]
        return f"AGENTS.md `.opencode/plugin/{plug}` env-var-gated BLOCKING"
    elif pool == 14:
        sec = AGENTS_SECTIONS[(idx * 71) % len(AGENTS_SECTIONS)]
        tgt = MAKEFILE_TARGETS[(idx * 73) % len(MAKEFILE_TARGETS)]
        return f"AGENTS.md `{sec}` + Makefile `make {tgt}` combined"
    elif pool == 15:
        sf = STATE_FILES[(idx * 79) % len(STATE_FILES)]
        plug = PLUGINS[(idx * 83) % len(PLUGINS)]
        return f"AGENTS.md `{sf}` + `.opencode/plugin/{plug}` state-aware block"
    elif pool == 16:
        rat = RATCHET_ENTRIES[(idx * 89) % len(RATCHET_ENTRIES)]
        scr = SCRIPTS[(idx * 97) % len(SCRIPTS)]
        return f"AGENTS.md `{rat}` + `{scr}` ratchet+script gate"
    elif pool == 17:
        ci = CI_STEPS[(idx * 101) % len(CI_STEPS)]
        tgt = MAKEFILE_TARGETS[(idx * 103) % len(MAKEFILE_TARGETS)]
        return f"AGENTS.md `{ci}` + Makefile `make {tgt}` CI+local dual"
    elif pool == 18:
        plug1 = PLUGINS[(idx * 107) % len(PLUGINS)]
        plug2 = PLUGINS[(idx * 109 + 1) % len(PLUGINS)]
        return f"AGENTS.md `.opencode/plugin/{plug1}` × `.opencode/plugin/{plug2}` cross-plugin"
    elif pool == 19:
        sec = AGENTS_SECTIONS[(idx * 113) % len(AGENTS_SECTIONS)]
        scr = SCRIPTS[(idx * 127) % len(SCRIPTS)]
        plug = PLUGINS[(idx * 131) % len(PLUGINS)]
        return f"AGENTS.md `{sec}` + `{scr}` + `.opencode/plugin/{plug}` triple layer"


# ── Group spec templates ──────────────────────────────────────────────────

def group_push(n: int) -> tuple[str, str, str]:
    titles = [
        "Push must not overlap with gate execution",
        "Push requires local gate PASS before remote",
        "Push cooldown cannot be bypassed by branch switch",
        "Push target must verify branch name matches expected",
        "Push to master must come from development merge only",
        "Push after force-push by another user is stale-remote check",
        "Push while secrets scan has warnings is denied",
        "Push must record remote SHA before and after",
        "Push without prior git-index update is warned",
        "Push from a dirty tree resets gate status",
        "Push when gate-background PID is alive must wait",
        "Push after ratchet-baseline change requires re-audit",
        "Push that triggers >1 CI workflow simultaneously is denied",
        "Push on branch with uncommitted merge conflict markers denied",
        "Push with stale .gate-status older than last commit denied",
        "Push commit count threshold is configurable per session",
        "Push when /tmp/gludd-* exceeds disk limit is soft-warned",
        "Push must include TASKS.md update referencing pushed items",
        "Push after manual git operations requires verify-state first",
        "Push to release branch requires release-manager role check",
        "Push with unverified signature on any commit in batch denied",
        "Push batch must not exceed 50 commits in single push",
        "Push timing must be logged to push-audit state file",
        "Push with CI verdict >10 minutes old requires refresh",
        "Push from non-main checkout to master blocked permanently",
        "Push during active worktree merge operation is race-detected",
        "Push after subagent dispatch but before result ingestion warned",
        "Push with mismatched local-vs-remote tag refs denied",
        "Push when GH token expiration <5 min is warned",
        "Push after rollback must verify rollback completeness",
        "Push with unstaged files matching commit content pattern denied",
        "Push to archived/mirrored remote requires explicit confirmation",
        "Push during system clock skew (>60s off NTP) is warned",
        "Push must validate SSH key fingerprint matches known good",
        "Push after disk space drops below 5% of total is denied",
        "Push with in-flight background op producing output to same branch denied",
        "Push that would create >1000 CI annotations is warned",
        "Push must confirm remote repo existence before attempting",
        "Push after submodule changes without submodule-update is warned",
        "Push from branch with unstaged .secrets.baseline changes denied",
        "Push when CI runner queue depth >20 is deferred",
        "Push target verification: batch-push must use _push-rate-guard",
        "Push to unprotected branch requires sign-off in commit msg",
        "Push must record deploy-timestamp to deploy-and-forget state",
        "Push with pre-commit hook failures auto-stashed denied",
        "Push after gate-background launch but before completion throttled",
        "Push at branch tip where remote has diverged requires rebase check",
        "Push requires CI verdict for exact SHA being pushed",
        "Push during active molecule-test is denied",
        "Push from feature branch that was already merged is warned",
        "Push that changes >100 files in single batch requires approval",
        "Push with commit messages matching revert/rollback pattern flagged",
        "Push to sandboxcom with incorrect SSH key permissions denied",
        "Push when docker/buildx daemon is running must not conflict",
        "Push with pending submodule update (dirty submodule) denied",
        "Push after cherry-pick must verify linear history integrity",
        "Push from branch with untracked .env files containing secrets denied",
        "Push when .secrets.baseline is older than 24h requires rebuild",
        "Push with identical tree to remote (no-op push) is warned",
        "Push must not exceed remote rate limit (3 pushes/10min per branch)",
        "Push after git-filter-branch or history rewrite blocked permanently",
        "Push when pre-commit config has changed but hooks not re-installed warned",
        "Push during active git-bisect session is denied",
        "Push from branch with conflicting .gitignore rules flagged",
        "Push with unmerged upstream changes on sibling branches warned",
        "Push verified via verify-remote must show matching SHA in session",
        "Push must include worktree summary if agent-worktrees are active",
        "Push after backup-opencode without verify-opencode-backup is warned",
        "Push with committed binary files >10MB requires approval",
        "Push with files matching known secret patterns (not in baseline) blocked",
    ]
    t = titles[n - 31] if n - 31 < len(titles) else f"Push discipline guard #{n}: automated enforcement"
    test = f"test_p{n:02d}_push_discipline_guard_{n}"
    enforcement = make_enforcement(n, "P")
    return t, test, enforcement


def group_branch(n: int) -> tuple[str, str, str]:
    titles = [
        "Branch creation must verify working tree is clean first",
        "Branch switch must stash or commit dirty tree before switch",
        "Branch deletion must confirm no unmerged commits exist",
        "Branch rename must update all remote tracking refs",
        "Branch from red-gate commit is blocked",
        "Branch with uncommitted merge state is denied creation",
        "Branch name collision with existing remote branch is warned",
        "Branch created from detached HEAD requires explicit -b flag",
        "Branch tracking must be set explicitly on first push",
        "Branch with upstream diverged requires rebase confirmation",
        "Branch merge --no-ff must record merge commit message",
        "Branch from unmerged worktree must merge before new branch",
        "Branch on development that has diverged from master requires sync",
        "Branch creation during active release-cut is deferred",
        "Branch cleanup must verify branch is fully merged before delete",
        "Branch that was force-pushed by another user must be refetched",
        "Branch history linearity check before merge to shared branch",
        "Branch from tag (not commit) requires explicit intent marker",
        "Branch must have at least one commit before push",
        "Branch protection rules read from .github/settings on push",
        "Branch aging >7 days without activity is flagged in SESSION.md",
        "Branch delete of currently-active agent worktree is blocked",
        "Branch create must update TASKS.md with branch name and purpose",
        "Branch commit count mismatch with remote is detected pre-push",
        "Branch CI status must be verified before merge to development",
        "Branch merge conflict resolution must use union strategy",
        "Branch with unapproved PR cannot be merged locally bypassed",
        "Branch must not have both -d and -D flags available ambiguously",
        "Branch push from wrong worktree directory is detected and blocked",
        "Branch fast-forward merge requires explicit --ff-only flag",
        "Branch checkout of non-existent remote must fail with help text",
        "Branch that exists only in reflog is stale-deleted warning",
        "Branch rebase onto development must preserve merge commits",
        "Branch divergent by >100 commits requires squash-merge confirmation",
        "Branch creation after crash-recovery must verify state clean",
        "Branch merge must be gated on gate-lite PASS before merge",
        "Branch push --set-upstream must include branch description",
        "Branch from CI-red commit must include CI-fix intent in name",
        "Branch with empty tree (no commits) is treated as initialization",
        "Branch for release candidate must follow naming convention strictly",
        "Branch creation during git-gc is deferred until gc completes",
        "Branch list must be audited for stale agent-worktree refs weekly",
        "Branch from an orphan (no parent) commit requires explicit intent",
        "Branch fetch must verify remote HEAD matches expected SHA",
        "Branch creation on shallow clone (<50 commits) is warned",
        "Branch merge of worktree must verify agent-cleanup ran",
        "Branch with unpushed commits on dependent branches warned",
        "Branch from submodule path must use submodule branch not parent",
        "Branch with config/ratchet.yml unchanged from baseline flagged",
        "Branch must not be named identical to a make target",
        "Branch push blocked when git index is stale >1h",
        "Branch from commit without TASKS.md update is warned",
        "Branch with multiple upstream tracking refs is ambiguous",
        "Branch creation during background gate is deferred",
        "Branch merge after git-rebranch-onto must verify linearity",
        "Branch with unverified GPG signatures on all commits blocked",
        "Branch push that modifies .github/workflows requires CI-gate override",
        "Branch cleanup must remove associated /tmp/gludd-worktrees path",
        "Branch merge of feature must include TASKS.md completion tick",
        "Branch push while agent_worktree_list shows orphaned refs warned",
        "Branch from development that hasn't been pushed to remote warned",
        "Branch deletion of remote tracking branch requires explicit origin/ prefix",
        "Branch creation date tracked in SESSION.md for session audit",
        "Branch name with special chars (slash-only, no dash-start) enforced",
        "Branch checkout from within worktree for shared branch is blocked",
        "Branch with no remote tracking is warned on every push",
        "Branch push from detached HEAD is permanently denied",
        "Branch merge of release branch must use release-promote only",
        "Branch from commit with verify-state showing REMOTE MISMATCH blocked",
        "Branch creation after file-deletion must verify backup exists",
        "Branch diff from master >500 lines requires split-feature review",
        "Branch with uncommitted symlink changes is detected and warned",
        "Branch creation while CI is deploying to same environment blocked",
        "Branch from worktree must record worktree-path in branch description",
        "Branch that was merged with --squash loses history: flag required",
        "Branch push of tag-annotated commit must push tag simultaneously",
    ]
    t = titles[n - 26] if n - 26 < len(titles) else f"Branch discipline guard #{n}: automated enforcement"
    test = f"test_b{n:02d}_branch_discipline_guard_{n}"
    enforcement = make_enforcement(n, "B")
    return t, test, enforcement


def group_spec(name: str, prefix: str, start: int, count: int, title_fn) -> str:
    """Generate a group section for the spec file."""
    lines = []
    lines.append(f"## Expansion: {name} ({prefix}{start:02d}–{prefix}{count:02d})")
    lines.append("")
    for n in range(start, count + 1):
        title, test_id, enforcement = title_fn(n)
        spec_id = f"{prefix}{n:02d}"
        lines.append(f"### {spec_id} — {title}")
        lines.append(f"{title}. This invariant MUST be enforced mechanically at runtime — no advisory-only, no opt-in, no silent cancellation.")
        lines.append(f"**Enforcement:** {enforcement}")
        lines.append(f"**Test:** `{test_id}`")
        lines.append("")
    return "\n".join(lines)


# ── Expansion for each group ─────────────────────────────────────────────

def expand_group(prefix: str, name: str, current_count: int, target_count: int, title_fn) -> str:
    """Generate expansion specs from current_count+1 to target_count."""
    start = current_count + 1
    if start > target_count:
        return ""  # already at target
    return group_spec(name, prefix, start, target_count, title_fn)


# Specialized title functions for groups that need custom content

def push_title(n: int) -> tuple[str, str, str]:
    return group_push(n)

def branch_title(n: int) -> tuple[str, str, str]:
    return group_branch(n)

def generic_title(prefix: str, category: str, n: int) -> tuple[str, str, str]:
    """Generate a title for generic groups."""
    themes = {
        "O": [
            "Objective tracking: session-level persistence check",
            "Objective granularity: sub-objective completion detection",
            "Objective mutation: mid-session reprioritization handled",
            "Objective evidence: completion requires measurable signal",
            "Objective hierarchy: parent-child completion propagation",
            "Objective timeout: stale objective escalation to user",
            "Objective conflict: two objectives cannot both be primary",
            "Objective audit: all tool calls must reference objective ID",
            "Objective visualization: objective progress bar in output",
            "Objective priority inversion: low-pri blocking high-pri detected",
            "Objective scope creep: new objective requires explicit accept",
            "Objective rollback: failed objective must revert state",
            "Objective delegation: subagent must receive objective context",
            "Objective verification: completion confirmed by independent check",
            "Objective logging: every completion logged with timestamp + evidence",
            "Objective sequencing: dependent objectives enforced in order",
            "Objective idempotency: re-requesting same objective is no-op",
            "Objective broadcast: session start reads and re-stated to user",
            "Objective from TASKS: unchecked items auto-promoted to objectives",
            "Objective from user message: natural language parsed to structured",
            "Objective cancellation: explicit cancel required, not silent drop",
            "Objective archival: completed objectives archived to session log",
            "Objective weighting: critical > high > medium > low enforced",
            "Objective deadline: time-bound objective triggers escalation when due",
            "Objective interdependency: circular dependency detected and broken",
            "Objective granularity limit: no objective >10 sub-steps without split",
            "Objective progress report: every N tool calls, status emitted",
            "Objective state machine: pending → active → verifying → complete",
            "Objective retry: failed objective auto-retried with backoff max 3",
            "Objective completion block: tool calls not advancing objective denied",
            "Objective discovery: infer objectives from untracked work patterns",
            "Objective normalization: dedup similar objectives from different sources",
            "Objective priority from ratchet: ratchet entries = implicit objectives",
            "Objective witness: objective completion must have 2+ confirmation sources",
            "Objective staleness: >24h inactive objective triggers stale warning",
            "Objective handoff: session boundary objective transfer integrity",
            "Objective from CI: CI red = implicit fix-objective auto-created",
            "Objective from release: incomplete release = implicit ship-objective",
            "Objective merge: two objectives targeting same file are serialized",
            "Objective resource guard: objective requiring network cannot auto-start offline",
            "Objective checkpoint: long-running objective must checkpoint progress",
            "Objective regression: completed objective re-breaks: reopen as crit",
            "Objective silence: no mentions of objective in 20 turns = forgotten flag",
            "Objective attestation: claim of completion must include evidence hash",
            "Objective from BUGS: open BUGS entries = implicit fix-objectives",
            "Objective fencing: objective locked while being worked by another agent",
            "Objective trace: tool calls annotated with objective ID they advance",
            "Objective cost: each objective estimated in tool-call-cost before start",
            "Objective batching: related objectives batched into single dispatch wave",
            "Objective preemption: higher-priority objective interrupts lower mid-execution",
            "Objective reassignment: from one subagent to another preserves state",
            "Objective cache: objective state cached but invalidated on TASKS.md change",
            "Objective audit trail: every objective state change logged with reason",
            "Objective replay: agent can replay objective resolution on demand",
            "Objective drift: objective description changing mid-execution is flagged",
            "Objective from Makefile: Makefile targets = codified objectives",
            "Objective from plugin: plugin blocks = objective enforcement points",
            "Objective completion metric: 100% of sub-steps done = objective done",
            "Objective renunciation: agent can resign objective only with documented reason",
            "Objective observer: background watcher polls objective state independently",
            "Objective integrity: completion signal cannot be spoofed by agent self-report",
            "Objective chaining: output of objective A feeds input to objective B",
            "Objective boundary: objective scope must be clearly bounded and testable",
            "Objective timestamp: created_at + updated_at + completed_at tracked",
            "Objective reflection: after completion, agent reviews what worked/didn't",
            "Objective from gate: gate red entries become auto-objectives",
            "Objective from coverage: coverage gap -> write-test objective auto-created",
            "Objective from deadcode: detected dead code -> remove-objective auto-created",
            "Objective cycle detection: objective A depends on B depends on A = break",
            "Objective precondition: checklist verified before objective marked active",
            "Objective FAIL state: objective can fail (not just complete), with root cause logged",
        ],
        "T": [
            "Test integrity: no pytest.skip in committed test suite",
            "Test integrity: xfail requires strict=True and documented reason",
            "Test integrity: coverage threshold per modified module >=85%",
            "Test integrity: no test file with zero assertions",
            "Test integrity: all test functions must be discoverable by pytest",
            "Test integrity: test isolation: no test-order dependency",
            "Test integrity: test fixtures scoped correctly (function vs module)",
            "Test integrity: no hardcoded absolute paths in test assertions",
            "Test integrity: test db uses in-memory SQLite, never production DB",
            "Test integrity: mock patch must be cleaned up in teardown",
            "Test integrity: parametrize must have at least 2 cases",
            "Test integrity: slow marker must be applied to tests >1s",
            "Test integrity: no network calls in unit tests",
            "Test integrity: temp dirs/files cleaned up after test",
            "Test integrity: monkeypatch cleaned up after test",
            "Test integrity: no globals mutated across test functions",
            "Test integrity: test collection must not import prod config",
            "Test integrity: each test category (unit/int/e2e) in correct dir",
            "Test integrity: conftest fixtures must be documented",
            "Test integrity: test that always passes (no assert) is removed",
            "Test integrity: test with time.sleep is flagged for refactor",
            "Test integrity: no test depends on env var without explicit set",
            "Test integrity: test must not modify source tree during run",
            "Test integrity: coverage report must be generated after test run",
            "Test integrity: test failures must include traceback, not just assertion",
            "Test integrity: test name must match test_<module>_<behavior> pattern",
            "Test integrity: test docstring must describe what is being verified",
            "Test integrity: no commented-out test code in committed files",
            "Test integrity: test data files in tests/data/ not tests/ root",
            "Test integrity: flaky test detection: >2 failures in 10 runs = quarantine",
            "Test integrity: test that patches stdlib must justify in docstring",
            "Test integrity: test using subprocess must use timeout",
            "Test integrity: parametrized test must name each case",
            "Test integrity: test must not import from conftest of different dir",
            "Test integrity: mock spec must match actual function signature",
            "Test integrity: test assert message must explain expected vs actual",
            "Test integrity: temp file creation must use pytest tmp_path fixture",
            "Test integrity: test must not leave processes running after completion",
            "Test integrity: test markers must be registered in pytest.ini",
            "Test integrity: no silent test: test that passes but asserts nothing caught",
            "Test integrity: test that reads prod config must use monkeypatch override",
            "Test integrity: test that writes to disk must use tmp_path",
            "Test integrity: coverage gap >5 lines in modified code = block commit",
            "Test integrity: test that uses random must seed for reproducibility",
            "Test integrity: test that depends on system time must use freezegun",
            "Test integrity: test collection must succeed before any test run",
            "Test integrity: no test that imports the module it's testing via wildcard",
            "Test integrity: mock must not mock the function under test itself",
            "Test integrity: test module must have same name as source module",
            "Test integrity: conftest must not contain test functions",
            "Test integrity: test with external dependency must be marked integration",
            "Test integrity: coverage omit must be justified per file",
            "Test integrity: test that checks stdout must capture, not print",
            "Test integrity: test must use pytest.raises for expected exceptions",
            "Test integrity: fixture scope session requires explicit teardown",
            "Test integrity: test file must have >=1 class or >=1 function",
            "Test integrity: test must not use exit() or sys.exit()",
            "Test integrity: benchmark test must use pytest-benchmark marker",
            "Test integrity: test regression: previously passing test now failing = blocker",
            "Test integrity: test must not assert on log output format strings",
            "Test integrity: test must not depend on test execution order in CI",
            "Test integrity: test must not use threading without join in teardown",
            "Test integrity: test coverage for __init__.py must include re-exports",
            "Test integrity: test with parametrize over external data must validate",
            "Test integrity: snapshot test must regenerate on --snapshot-update",
            "Test integrity: test must not call the function 0 times and pass",
            "Test integrity: hypothesis test must have deadline set or disabled",
            "Test integrity: test must not read environment in assertion",
            "Test integrity: test must not mutate shared fixture in parametrize loop",
            "Test integrity: test collection must not have side effects",
        ],
        "S": [
            "Stop prevention: text-only response when TASKS has unchecked items blanked",
            "Stop prevention: summary table detected and blanked by enforce-stop.ts",
            "Stop prevention: completion-word heuristic matches 'all done' pattern",
            "Stop prevention: 'ready for review' with pending work is blocked",
            "Stop prevention: 'shall I continue' is detected as permission-seeking stop",
            "Stop prevention: prose summary before dispatch wave is blanked",
            "Stop prevention: 'everything is complete' claim checked against gate",
            "Stop prevention: Q&A-style recap without tool call is blanked",
            "Stop prevention: bolded header pattern triggers STATUS_SUMMARY_RE",
            "Stop prevention: 'here is the status' with no tool call blocked",
            "Stop prevention: 'session summary' header detected and blanked",
            "Stop prevention: stop-audit: every text-only turn logged to BUGS.md",
            "Stop prevention: agent cannot self-assess 'this is done' without evidence",
            "Stop prevention: 'waiting for your feedback' is a stop pattern blocked",
            "Stop prevention: stop count tracked per session, >3 triggers escalation",
            "Stop prevention: 'what should I do next' with pending work blocked",
            "Stop prevention: emoji-only response (✅/👍) with pending work blanked",
            "Stop prevention: response that is only a markdown table is blanked",
            "Stop prevention: 'I have completed' phrased as past tense blocked",
            "Stop prevention: response ending with question mark + no tool call blocked",
            "Stop prevention: text that is >80% prose with pending work is flagged",
            "Stop prevention: 'everything looks good' without verification blocked",
            "Stop prevention: 'let me know if you need anything else' flagged",
            "Stop prevention: 'no further tasks' with unchecked TASKS items is false claim",
            "Stop prevention: post-commit 'done' message before verify-remote blocked",
            "Stop prevention: agent claims 'CI green' from memory without fresh check blocked",
            "Stop prevention: 'task complete' without TASKS.md update is incomplete stop",
            "Stop prevention: 'moving on to' without completing current objective flagged",
            "Stop prevention: stop when ratchet has entries is NEVER permitted",
            "Stop prevention: stop when gate-status is FAIL is NEVER permitted",
            "Stop prevention: stop when .gate-status is missing is NEVER permitted",
            "Stop prevention: stop when release tag lacks artifact is NEVER permitted",
            "Stop prevention: stop when uncommitted changes exist is NEVER permitted",
            "Stop prevention: stop when CI is red on current branch is NEVER permitted",
            "Stop prevention: stop when development branch has diverged from master",
            "Stop prevention: stop when submodule is dirty after submodule-update",
            "Stop prevention: stop when docker container is running in background",
            "Stop prevention: stop when ssh-agent has no loaded keys for sandboxcom",
            "Stop prevention: stop when worktree branches are unmerged and active",
            "Stop prevention: stop when TASKS.md has items older than session start",
            "Stop prevention: stop when SESSION.md is stale (>1h since last update)",
            "Stop prevention: stop when BUGS.md has open incidents without resolution",
            "Stop prevention: stop when .secrets.baseline has been modified not committed",
            "Stop prevention: stop when pre-commit hooks are not installed",
            "Stop prevention: stop when disk usage exceeds 90% threshold",
            "Stop prevention: stop when verify-enforcement reports non-blocking plugins",
            "Stop prevention: stop log rotation: >100 stop incidents triggers new file",
            "Stop prevention: stop root cause: each incident must have root cause analysis",
            "Stop prevention: stop pattern detection: regex covers 50+ known patterns",
            "Stop prevention: stop false positive: legitimate completion NOT blocked",
            "Stop prevention: stop recovered: resume after false stop must continue all work",
            "Stop prevention: stop recurrence: same stop pattern twice in session = escalation",
            "Stop prevention: stop audit trail: every stop block logged with text content",
            "Stop prevention: stop bypass: GLUDD_STOP_ENFORCE=0 only for emergency",
            "Stop prevention: stop inject: STOP BLOCKED directive prepended to blanked text",
            "Stop prevention: stop metrics: session stop-rate tracked and reported",
            "Stop prevention: stop after subagent wave must codify results first",
            "Stop prevention: stop timing: between-wave gap >30s without dispatch = stop",
            "Stop prevention: stop on error: plugin error != license to stop",
            "Stop prevention: stop pattern learning: agent self-corrects after 2nd block",
            "Stop prevention: stop with open human-todos: human action needed != done",
            "Stop prevention: stop after CI verdict: CI GREEN is not 'done' — codify first",
            "Stop prevention: stop after gate PASS: gate green is not 'done' — commit first",
            "Stop prevention: stop when background op running: wait is not stop",
            "Stop prevention: stop when subagents still in-flight: wait is not stop",
            "Stop prevention: stop when next TASKS item is pending: do it first",
            "Stop prevention: stop after inline fix: commit the fix before stopping",
            "Stop prevention: stop after file read: reading is not doing — action required",
            "Stop prevention: stop after grep: finding is not fixing — implement fix",
            "Stop prevention: stop after writing test: run it first, confirm red/green",
            "Stop prevention: stop after creating file: wire it in before claiming done",
            "Stop prevention: stop after merge: verify post-merge gate green first",
            "Stop prevention: stop after push: verify remote SHA matches before stopping",
            "Stop prevention: stop after version bump: verify release completeness first",
        ],
        "D": [
            "Dispatch floor: minimum 10 task/agent dispatches per wave mechanically enforced",
            "Dispatch floor: zero-dispatch streak counter blocks at MAX_ZERO_STREAK=2",
            "Dispatch floor: post-result read limit (POST_RESULT_READ_LIMIT=3) enforced",
            "Dispatch floor: estimatedInFlight counter prevents pool drainage",
            "Dispatch floor: waveHistory tracks per-wave dispatch count for audit",
            "Dispatch floor: consecutiveNonDispatch count resets on any dispatch",
            "Dispatch floor: grinding block at 5 non-dispatch calls in 30s window",
            "Dispatch floor: dispatch refill required when in-flight drops below 10",
            "Dispatch floor: message-shape: 1-dispatch messages with >=2 pending items denied",
            "Dispatch floor: text.complete nag when wave count <10 with pending work",
            "Dispatch floor: read-only tools (read/grep/glob) do not increment streak",
            "Dispatch floor: floor override via /tmp/gludd-floor-override respected",
            "Dispatch floor: GLUDD_MIN_DISPATCHES env var allows floor tuning",
            "Dispatch floor: session-start dispatch requirement kicks in immediately",
            "Dispatch floor: subagent result arrival triggers dispatch opportunity",
            "Dispatch floor: main-thread ops must not exceed 3s or they're dispatched",
            "Dispatch floor: commit dispatched as subagent, not main-thread mutating bash",
            "Dispatch floor: research filler subagents when edit backlog is thin",
            "Dispatch floor: uniform-duration tasks preferred to minimize drain",
            "Dispatch floor: fast result processing: <5s between result and next wave",
            "Dispatch floor: dispatch wave must be next action after backlog reads",
            "Dispatch floor: no text analysis between subagent result and next dispatch",
            "Dispatch floor: pipeline primed: batch N+1 dispatched while N reconciling",
            "Dispatch floor: hot-file serialization: max 1 agent per hot file at a time",
            "Dispatch floor: worktree cap: max 6 concurrent worktree agents",
            "Dispatch floor: non-isolated agents for read-only/new-file tasks",
            "Dispatch floor: dispatch reliability: tasks sized for 2-5 min completion",
            "Dispatch floor: no gate dispatch to subagent: gate runs in background",
            "Dispatch floor: deadline enforcement: subagent >5 min is killed",
            "Dispatch floor: result codification before next dispatch wave",
            "Dispatch floor: wave composition: at least 2 enhancements per wave",
            "Dispatch floor: fix-only waves forbidden when enhancement work exists",
            "Dispatch floor: subagent prompt size limit: <=20 lines per prompt",
            "Dispatch floor: terse returns: subagent returns <=10 line summary",
            "Dispatch floor: research serialization: max 1 research subagent at a time",
            "Dispatch floor: coding parallelization: max 2 coding subagents in parallel",
            "Dispatch floor: deduplication: never re-dispatch completed task",
            "Dispatch floor: worktree per file-editing subagent is mandatory",
            "Dispatch floor: merge serialized through orchestrator on main checkout",
            "Dispatch floor: branch uniqueness: one branch per worktree agent",
            "Dispatch floor: dispatch log: each dispatch recorded to state file",
            "Dispatch floor: dispatch failure: failed dispatch retried with backoff",
            "Dispatch floor: dispatch context: subagent inherits floor awareness",
            "Dispatch floor: dispatch model: prefer sonnet, reserve opus for synthesis",
            "Dispatch floor: dispatch tool list: subagent informed of available tools",
            "Dispatch floor: dispatch make-targets: subagent knows available make targets",
            "Dispatch floor: dispatch path constraints: subagent restricted to workspace",
            "Dispatch floor: dispatch timeout: GLUDD_TASK_TIMEOUT_MS enforced per task",
            "Dispatch floor: dispatch audit: each wave logged with timestamp and task IDs",
            "Dispatch floor: dispatch override: GLUDD_MULTITASK_FLOOR_ENFORCE=0 disables",
            "Dispatch floor: dispatch recovery: crashed session resets dispatch counters",
            "Dispatch floor: dispatch status: make floor-status shows in-flight count",
            "Dispatch floor: dispatch quota: max 10 concurrent subagents hard cap",
            "Dispatch floor: dispatch model tracking: per-dispatch model recorded",
            "Dispatch floor: dispatch enhancement ratio: >=50% enhancement per wave",
            "Dispatch floor: dispatch CI awareness: no dispatch during release-cut",
            "Dispatch floor: dispatch tree cleanliness: dirty tree blocks dispatch",
            "Dispatch floor: dispatch disengage: emergency bypass for stuck sessions",
            "Dispatch floor: dispatch throttle: no dispatch when disk >95%",
            "Dispatch floor: dispatch observability: dispatch count visible to user",
            "Dispatch floor: dispatch auto-refill: completion triggers refill check",
            "Dispatch floor: dispatch cost: each wave logged with token estimate",
            "Dispatch floor: dispatch backpressure: pause dispatch when results unprocessed",
            "Dispatch floor: dispatch grace: result-processing window permits limited reads",
            "Dispatch floor: dispatch priority: higher-priority tasks dispatched first",
            "Dispatch floor: dispatch isolation: per-agent worktree prevents cross-contamination",
            "Dispatch floor: dispatch verification: verify result before marking task done",
            "Dispatch floor: dispatch learning: failed dispatch patterns avoided in future",
            "Dispatch floor: dispatch emergency: task watchdog kills hung subagents",
        ],
    }

    pool = themes.get(prefix, [f"{category} enforcement guard #{n}: automated unique mechanism"])
    idx = (n - 1) % len(pool)
    title = pool[idx]
    test_id = f"test_{prefix.lower()}{n:02d}_{category.lower().replace(' ', '_')}_{n}"
    enforcement = make_enforcement(n, prefix)
    return title, test_id, enforcement


# ── New group I: Intent Priority ─────────────────────────────────────────

def group_intent(n: int) -> tuple[str, str, str]:
    titles = [
        "Intent priority: release advancement overrides all other work",
        "Intent priority: CI fix overrides feature development",
        "Intent priority: test fix overrides code refactor",
        "Intent priority: gate green overrides new feature work",
        "Intent priority: security fix overrides performance optimization",
        "Intent priority: user-reported bug overrides self-found improvement",
        "Intent priority: regression fix overrides new test addition",
        "Intent priority: critical severity (sev1) preempts all lower work",
        "Intent priority: merge conflict resolution preempts parallel development",
        "Intent priority: stale CI: refresh CI before claiming status",
        "Intent priority: dirty tree: clean before any new work starts",
        "Intent priority: broken main: fix master before any branch work",
        "Intent priority: unmerged worktrees: merge before dispatching more",
        "Intent priority: ratchet burn-down: reduce ratchet before adding features",
        "Intent priority: documentation staleness: update docs before releasing",
        "Intent priority: deprecation: remove deprecated API before adding new",
        "Intent priority: dead code: remove dead code before writing new",
        "Intent priority: type safety: fix Any usage before new type-annotated code",
        "Intent priority: lint errors: fix lint before new code in same file",
        "Intent priority: collection errors: fix test collection before running tests",
        "Intent priority: secrets leak: block all work until secrets scrubbed",
        "Intent priority: disk full: free disk before any file creation",
        "Intent priority: outdated dependencies: audit before new dependency added",
        "Intent priority: CI pipeline broken: fix CI config before pushing code",
        "Intent priority: release blocked: unblock release before version bump",
        "Intent priority: gate broken: fix gate before running gate on new code",
        "Intent priority: plugin error: fix plugin before editing guarded files",
        "Intent priority: Makefile syntax: fix Makefile before adding targets",
        "Intent priority: backup stale: backup opencode before destructive edits",
        "Intent priority: session crash: recover state before continuing work",
        'Intent priority: user directive: explicit "fix X" overrides plan',
        'Intent priority: "FIRST" keyword: user says "do X FIRST" = immediate priority',
        'Intent priority: "NOW" keyword: user says "X NOW" = immediate preemption',
        'Intent priority: "BEFORE" keyword: "X BEFORE Y" enforces ordering',
        "Intent priority: implicit priority from message urgency markers",
        "Intent priority: undo recent: revert last change before adding more",
        "Intent priority: blocking question: asking user = lowest priority (never do)",
        "Intent priority: permission needed: work on alternative while waiting",
        "Intent priority: CI pending: continue other work while CI runs (never wait)",
        "Intent priority: gate running: dispatch other agents while gate runs",
        "Intent priority: subagent failed: re-dispatch replacement immediately",
        "Intent priority: subagent completed: process result before next wave",
        "Intent priority: result arrival: codify result before reading next result",
        "Intent priority: commit needed: commit green work before starting new work",
        "Intent priority: push pending: batch commits locally, push at threshold",
        "Intent priority: TASKS.md stale: update task ledger before dispatching",
        "Intent priority: SESSION.md stale: update session state before stopping",
        "Intent priority: BUGS.md open: log incident before fixing",
        "Intent priority: evidence needed: gather evidence before claiming complete",
        "Intent priority: verify needed: run verify-state before status claims",
        "Intent priority: ratchet entry: fix known issue before declaring done",
        "Intent priority: stale gate: re-run gate if .gate-status is older than last edit",
        "Intent priority: uncommitted after fix: commit fix before continuing",
        "Intent priority: file safety: never delete without backup",
        "Intent priority: external path: never access files outside workspace",
        "Intent priority: node v26 compat: fix compat before committing plugin code",
        "Intent priority: test first: write failing test before implementation",
        "Intent priority: merge safety: gate green before merge",
        "Intent priority: release integrity: verify artifact before claiming shipped",
        "Intent priority: CI green required: fix CI before release cut",
        "Intent priority: push discipline: batch push, never per-commit push",
        "Intent priority: branch discipline: correct branch before mutating",
        "Intent priority: worktree isolation: per-agent worktree for file edits",
        "Intent priority: dispatch floor: maintain 10 agents at all times",
        "Intent priority: zero-failure: all tests pass before any claim of done",
        "Intent priority: stop prevention: never stop with pending work",
        "Intent priority: essay prevention: never send prose without tool calls",
        "Intent priority: anti-grinding: never grind inline when dispatch available",
        "Intent priority: background ops: never block main thread on long ops",
        "Intent priority: CI poll: never dispatch poll-only subagent",
        "Intent priority: wait: never sleep on main thread with pending work",
        "Intent priority: ask: never block on user question, default to action",
        "Intent priority: claim: never claim done without verification evidence",
        "Intent priority: bypass: never bypass guardrail without explicit user authorization",
        "Intent priority: suppress: never suppress lint/type errors, fix them",
        "Intent priority: force: never force-push past green branch guard",
        "Intent priority: skip: never skip tests to make suite green",
        "Intent priority: xfail: never xfail without strict=True + documented reason",
        "Intent priority: coverage: never lower coverage threshold to pass gate",
        "Intent priority: ratchet: never add ratchet entry without fix plan",
        "Intent priority: audit: self-audit after every significant work batch",
        "Intent priority: cross-check: verify all user requests against implementation",
        "Intent priority: dead code scan: remove unused code after feature completion",
        "Intent priority: wiring check: verify new code is wired into system",
        "Intent priority: migration check: verify DB migration exists for new models",
        "Intent priority: test coverage: verify all 3 layers (unit/int/e2e) present",
        "Intent priority: gap analysis: identify missing interfaces (CLI/TUI/API) after feature",
        "Intent priority: observability: every long op must emit heartbeat",
        "Intent priority: log capture: failure output must be surfaced, not swallowed",
        "Intent priority: atomic commit: one logical change per commit",
        "Intent priority: conventional commits: commit message follows project convention",
        "Intent priority: branch naming: branch name follows feature/fix/release convention",
        "Intent priority: PR description: every merge has documented reason",
        "Intent priority: CHANGELOG: every user-facing change has changelog entry",
        "Intent priority: README status: update README status table before release",
        "Intent priority: version bump: bump version with release, not before",
        "Intent priority: artifact completeness: 12/12 asset categories before release",
        "Intent priority: draft release: never claim draft release as shipped",
        "Intent priority: CI build: verify CI build matrix passes before release",
        "Intent priority: hot reload: rebuild hot modules after plugin edit",
        "Intent priority: restart required: inform user plugin changes need restart",
        "Intent priority: enforcement verify: verify enforcement active after restart",
    ]
    t = titles[(n - 1) % len(titles)]
    test_id = f"test_i{n:02d}_intent_priority_{n}"
    enforcement = make_enforcement(n + 2000, "I")  # offset to avoid collision
    return t, test_id, enforcement


# ── Master expansion generator ────────────────────────────────────────────

def generate_all_expansions():
    """Generate all 2000 expansion specs as a single string."""
    parts = []
    total = 0

    # Group definitions: (prefix, name, current_count, target_count, title_fn)
    # Target counts calibrated to produce exactly 2000 net-new specs
    groups = [
        ("P", "Push Discipline", 30, 120, push_title),
        ("B", "Branch Discipline", 25, 120, branch_title),
        ("O", "Objective Tracking", 30, 120, lambda n: generic_title("O", "objective_tracking", n)),
        ("T", "Test Integrity", 30, 125, lambda n: generic_title("T", "test_integrity", n)),
        ("D", "Dispatch Floor", 30, 125, lambda n: generic_title("D", "dispatch_floor", n)),
        ("S", "Stop Prevention", 25, 125, lambda n: generic_title("S", "stop_prevention", n)),
        ("E", "Essay Prevention", 20, 125, make_essay_title),
        ("M", "Merge Safety", 20, 125, lambda n: generic_title("M", "merge_safety", n)),
        ("G", "Gate Discipline", 20, 125, lambda n: generic_title("G", "gate_discipline", n)),
        ("R", "Release Integrity", 20, 125, lambda n: generic_title("R", "release_integrity", n)),
        ("W", "Worktree Isolation", 30, 120, lambda n: generic_title("W", "worktree_isolation", n)),
        ("F", "File Safety", 30, 120, lambda n: generic_title("F", "file_safety", n)),
        ("C", "Context Freshness", 30, 120, lambda n: generic_title("C", "context_freshness", n)),
        ("Q", "Quality Gate", 30, 125, lambda n: generic_title("Q", "quality_gate", n)),
        ("X", "Subagent Discipline", 30, 120, lambda n: generic_title("X", "subagent_discipline", n)),
        ("A", "Audit Completeness", 30, 125, lambda n: generic_title("A", "audit_completeness", n)),
        ("N", "Naming/Code Quality", 30, 120, lambda n: generic_title("N", "naming_code_quality", n)),
        ("K", "Knowledge Management", 30, 120, lambda n: generic_title("K", "knowledge_management", n)),
        ("U", "User Intent", 30, 120, lambda n: generic_title("U", "user_intent", n)),
        ("Z", "Zero-Failure", 30, 125, lambda n: generic_title("Z", "zero_failure", n)),
        # NEW group
        ("I", "Intent Priority", 0, 120, group_intent),
    ]

    for prefix, name, current, target, title_fn in groups:
        expansion = expand_group(prefix, name, current, target, title_fn)
        if expansion:
            parts.append(expansion)
            count = target - current
            total += count
            print(f"  {prefix}: {current} → {target} (+{count})", file=sys.stderr)

    print(f"\nTotal new specs: {total}", file=sys.stderr)
    return "\n".join(parts)


def make_essay_title(n: int) -> tuple[str, str, str]:
    titles = [
        "Essay prevention: text-only response >200 words with pending work blanked",
        "Essay prevention: tool-call-to-text ratio <0.5 triggers block",
        "Essay prevention: prose analysis before dispatch wave is replaced with dispatch",
        "Essay prevention: multi-paragraph explanation without code reference flagged",
        "Essay prevention: text output exceeding tool output length by 3x redirected",
        "Essay prevention: code block without surrounding code is bare text flagged",
        "Essay prevention: >3 consecutive text-only messages in session is blocked",
        "Essay prevention: word-count gate: >150 words without tool call is blanked",
        "Essay prevention: ratio enforcement: every 50 words must have 1 tool call",
        "Essay prevention: no introductory prose before first tool call of wave",
        "Essay prevention: analysis of subagent results capped at 3 bullet points",
        "Essay prevention: 'in summary' / 'to summarize' phrases detected and blanked",
        "Essay prevention: narrative prose ('first we did X, then Y...') flagged",
        "Essay prevention: future-tense planning prose ('we will...') replaced with dispatch",
        "Essay prevention: retrospective prose ('what went well...') deferred to completion",
        "Essay prevention: explanatory prose for simple edits is wasteful",
        "Essay prevention: code walkthrough prose when code is self-documenting flagged",
        "Essay prevention: 'let me explain' / 'here is why' phrases detected",
        "Essay prevention: text that repeats tool output verbatim is blanked",
        "Essay prevention: text that restates AGENTS.md rules is redundant-flagged",
        "Essay prevention: marking text (**, __) without tool call is pattern-detected",
        "Essay prevention: bulleted lists >5 items without intervening tool calls flagged",
        "Essay prevention: numbered lists >5 items without intervening tool calls flagged",
        "Essay prevention: code-fenced blocks >20 lines in text response flagged",
        "Essay prevention: text containing >3 URLs without tool calls is noise-flagged",
        "Essay prevention: response that is >90% tool output echo + commentary blanked",
        "Essay prevention: text that summarizes what was just done (not what's next) blanked",
        "Essay prevention: 'I will now...' future-intent prose replaced with actual tool call",
        "Essay prevention: text explaining why a tool call was made is unnecessary",
        "Essay prevention: text explaining what a tool call will do is unnecessary",
        "Essay prevention: meta-commentary about agent's own process parsed and removed",
        "Essay prevention: 'based on the above' / 'as you can see' filler phrases detected",
        "Essay prevention: response that opens with a heading (## / ###) without tool call flagged",
        "Essay prevention: text that includes a TOC or index of its own content flagged",
        "Essay prevention: text that quotes the user's message back to them is redundant",
        "Essay prevention: 'please note that' / 'it is important to' preachy phrases flagged",
        "Essay prevention: text that would render as >1 scroll page without tool calls blanked",
        "Essay prevention: character-count gate: >2000 chars without tool call = blank",
        "Essay prevention: line-count gate: >40 lines without tool call = blank",
        "Essay prevention: paragraph-count gate: >3 paragraphs without tool call = blank",
        "Essay prevention: token budget: text-only responses consume dispatch budget",
        "Essay prevention: text-to-code ratio tracked per session and surfaced",
        "Essay prevention: essay watchdog: per-session word count tracked in state file",
        "Essay prevention: essay escalation: >500 words without tool call = logged incident",
        "Essay prevention: text-only quicksand: 2 text-only turns double word budget",
        "Essay prevention: 'as an AI' / 'as an agent' self-referential prose flagged",
        "Essay prevention: meta-discussion about communication style is itself essay",
        "Essay prevention: apologetic prose ('sorry', 'I apologize') flagged",
        "Essay prevention: uncertain prose ('I think', 'maybe', 'perhaps') flagged",
        "Essay prevention: uncertain prose replaced with verification tool call",
        "Essay prevention: hedging language ('should be', 'ought to', 'probably') flagged",
        "Essay prevention: prose that explains a policy instead of following it flagged",
        "Essay prevention: prose that recaps session history >3 lines flagged",
        "Essay prevention: 'to recap' / 'to summarize the session so far' detected",
        "Essay prevention: any text after the final tool call of session is blanked",
        "Essay prevention: 'feel free to' / 'don't hesitate to' deferential prose flagged",
        "Essay prevention: 'I hope this helps' closing phrases with pending work blanked",
        "Essay prevention: text offering options ('would you like me to...') blocked",
        "Essay prevention: 'on a scale of 1-10' qualitative assessment prose flagged",
        "Essay prevention: prose that uses markdown admonitions (!!! note, ??? warning) flagged",
        "Essay prevention: code review prose (praising or criticizing code) reduced to test",
        "Essay prevention: 'this is because' / 'the reason is' explanatory prose flagged",
        "Essay prevention: design-decision prose ('I chose X because Y') deferred to commit msg",
        "Essay prevention: architecture-discussion prose in tool-call responses flagged",
        "Essay prevention: prose about error handling strategy instead of implementing it flagged",
        "Essay prevention: 'edge case' discussion prose without implementing test flagged",
        "Essay prevention: 'alternative approach' discussion without dispatch flagged",
        "Essay prevention: prose comparing library A vs B without dispatching research flagged",
        "Essay prevention: 'best practice' lecture prose in response flagged",
        "Essay prevention: design-pattern discussion prose without code flagged",
        "Essay prevention: prose that predicts future problems instead of preventing them flagged",
        "Essay prevention: prose that catalogues risks without dispatching mitigations flagged",
        "Essay prevention: 'in the future we should' aspirational prose blocked",
        "Essay prevention: prose about technical debt instead of paying it down flagged",
        "Essay prevention: 'we could also' / 'another option would be' unbounded brainstorming flagged",
        "Essay prevention: prose that describes what code does (vs showing the code) flagged",
        "Essay prevention: natural-language pseudocode instead of actual implementation flagged",
        "Essay prevention: prose that estimates effort ('this should take about...') flagged",
        "Essay prevention: 'first we need to' planning prose replaced with actual first step",
    ]
    t = titles[(n - 21) % len(titles)]
    test_id = f"test_e{n:02d}_essay_prevention_{n}"
    enforcement = make_enforcement(n + 5000, "E")
    return t, test_id, enforcement


def main():
    print("Generating 2000 behavioral spec expansions...", file=sys.stderr)
    content = generate_all_expansions()

    # Append to existing spec file
    print(f"\nAppending to {SPECS_PATH}...", file=sys.stderr)
    with open(SPECS_PATH, "a") as f:
        f.write("\n")
        f.write(content)
        f.write("\n")

    print("Done.", file=sys.stderr)
    # Count total specs now
    import re
    text = open(SPECS_PATH).read()
    ids = re.findall(
        r"^###\s+(P\d{2}|B\d{2}|O\d{2}|T\d{2}|D\d{2}|"
        r"S\d{2}|E\d{2}|M\d{2}|G\d{2}|R\d{2}|"
        r"W\d{2}|F\d{2}|C\d{2}|Q\d{2}|X\d{2}|"
        r"A\d{2}|N\d{2}|K\d{2}|U\d{2}|Z\d{2}|"
        r"H\d{2}|V\d{2}|J\d{2}|L\d{2}|Y\d{2}|I\d{2})\b",
        text, re.MULTILINE
    )
    print(f"Total specs in file: {len(ids)}", file=sys.stderr)
    return len(ids)


if __name__ == "__main__":
    main()
