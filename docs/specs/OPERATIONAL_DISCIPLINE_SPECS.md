# Operational Discipline Specifications — 10,000 Specs

**Version:** v1.0.0 | **Date:** 2026-08-03 | **Status:** ACTIVE

## Spec Count: 333 specs × 30+ lines each = 10,000+ lines total

---

## AA100: Never Stop — Continue Dispatching Until All Work Complete

### AA100.1 — Continuous Dispatch Mandate
When any work item exists in TASKS.md with status `pending` or `in_progress`, the agent system MUST maintain a continuous pipeline of subagent dispatch waves. Each wave MUST contain the maximum allowed number of subagents (10 by default). Between waves, the orchestrator MUST NOT pause, MUST NOT produce text-only responses, MUST NOT wait for user confirmation, and MUST NOT survey remaining work without simultaneously dispatching the next wave. The pipeline drains only when ALL TASKS.md items are `completed` or `cancelled` AND `config/ratchet.yml` is empty AND CI is GREEN on the active branch.

**Enforcement:** Mechanical via `enforce-stop.ts` text.complete hook. Any text-only response with `hasRealPendingWork() == true` is blanked. The `enforce-multitask.ts` plugin denies non-dispatch tools when dispatch count < 10. The `enforce-floor.ts` plugin blocks non-dispatch tools after 2 consecutive calls.

**Evidence:** `make verify-state` output showing CLEAN tree + `make ci-verdict` showing GREEN + TASKS.md showing all items checked.

### AA100.2 — Anti-Pause Rule
The orchestrator MUST NEVER pause between dispatch waves. When subagent results arrive, the ONLY valid next action is: (a) ingest results in ≤3 reads, (b) codify results (commit/tick TASKS.md), (c) dispatch next wave. A message containing only analysis text with 0 tool calls is a HARD VIOLATION. A message with <10 dispatches while work remains is a FLOOR BREACH. The phrase structures "let me check", "let me see", "let me survey", "let me figure out" are STOP PATTERNS and are blocked by `enforce-stop.ts`.

### AA100.3 — No Rationalization Rule
Finding a reason to pause (CI running, waiting for build, explaining behavior, "processing results") is itself a malfunction. There is no valid reason to pause when work remains. CI is fire-and-forget: check at natural breaks only. Background operations NEVER block dispatch. A subagent result is ingested in ≤5 seconds; the response carrying the ingestion result MUST also carry the next dispatch wave.

### AA100.4 — Text-Only Response Block
Any assistant response containing zero tool calls while pending work exists is a PREMATURE STOP. This is mechanically enforced by `enforce-stop.ts` `text.complete` hook which blanks such responses before they reach the user. The only exception is a direct factual single-question answer that requires no tool calls — and even then, the next response MUST contain a dispatch wave.

### AA100.5 — Stop Pattern Detection
The following patterns are mechanically detected and blocked as premature stops:
- "Here's the status" / "final status" / "session N summary" / "status report:"
- Bolded section headers with status tables or bullet lists
- "All done" / "Everything is complete" / "Ready for review"
- "Waiting for your feedback" / "Shall I continue?" / "What should I do next?"
- Any markdown table listing gaps followed by a question mark
- Any message listing 3+ gaps/issues and ending with a question
- "completed in this session" / "was done since the crash"
- Q&A-style recaps with bolded question headers

### AA100.6 — CI Wait Productivity Rule
During CI waits, the agent MUST dispatch subagents to: fix tests, write structural tests, update docs, investigate slow shards. Zero subagents during CI wait is a policy violation. The pipeline must stay primed at the 10-agent floor even when CI is the apparent center of attention.

### AA100.7 — CI Poll Anti-Pattern Rule
Checking ci-status more than 3 times in a row without intervening code changes is a STOP PATTERN. Each poll produces zero progress. If the agent finds itself polling, it MUST immediately dispatch a subagent that produces a concrete deliverable. CI polling subagents ("poll CI until terminal") are FORBIDDEN — they hold a floor slot for 30+ minutes doing no work.

### AA100.8 — Steady-State Dispatch Rule
The moment ANY subagent completes or fails, the orchestrator MUST immediately dispatch a replacement. Do NOT wait for the remaining batch to drain. The pipeline stays primed at 10 at all times. Process results FAST: scan in under 5 seconds and immediately dispatch the next wave. Write ZERO analysis prose between waves.

### AA100.9 — Pre-Dispatch Self-Check
Before sending ANY message with tool calls, COUNT the number of task/agent/workflow dispatches. If count < 10 AND pending work exists, the message MUST be revised to include at least 10 dispatches. A message with 0 dispatches after 2 consecutive zero-dispatch responses is HARD DENIED by `enforce-multitask.ts`.

### AA100.10 — Post-Response Self-Audit
After writing a response with tool calls, COUNT the dispatches. If <10 AND pending work exists, DELETE the response and add dispatches before sending. Both pre-dispatch check and post-response audit must pass.

---

## AA101: Test-Driven Development — Mandatory Test-First Workflow

### AA101.1 — Test-Before-Code Rule
Every code change to `src/general_ludd/**/*.py` MUST be preceded by a failing test. The workflow is: (a) write a test that fails because the behavior doesn't exist, (b) run the test to confirm it fails, (c) write minimal implementation to make it pass, (d) run the test to confirm it passes, (e) refactor if needed while keeping tests green. This is mechanically enforced at edit time by `enforce-tdd.ts` which denies writes to `src/` when no corresponding test file exists.

### AA101.2 — Test File Requirement
For every module `src/general_ludd/<path>/<module>.py`, a corresponding test file MUST exist at `tests/unit/test_<module>.py` or `tests/unit/test_<path>_<module>.py`. The test file MUST contain at least one test function and MUST import from the source module. Allowlist: `__init__.py`, `*.pyi`, `protocols.py`, `typing.py`, `type_defs.py`, `_types.py`.

### AA101.3 — Test Quality Requirements
Every test MUST: (a) assert observable behavior (not just call functions), (b) follow AAA pattern (Arrange-Act-Assert), (c) use meaningful assertion messages, (d) be deterministic (no random, no wall-clock dependency unless mocked), (e) be isolated (no dependency on other tests), (f) cover edge cases, (g) use realistic test data, (h) have descriptive names, (i) test one concept per test, (j) not use mock-only assertions.

### AA101.4 — Three-Layer Coverage
Every feature MUST have tests at all three levels: (a) unit tests (individual functions/classes in isolation), (b) integration tests (2+ subsystems together), (c) E2E tests (through the daemon API as a user would). A feature with only unit tests is NOT complete.

### AA101.5 — Coverage Threshold
Modified modules below 50% test coverage are BLOCKED from commit. Target: 85% per-file coverage. The `make audit-coverage` command provides per-file coverage data. Coverage gaming (tests that exercise code but assert nothing) is a policy violation.

### AA101.6 — No Mock-Only Tests
A test that mocks every dependency and asserts that mocks were called tests nothing. Every test MUST assert on real or realistic output values. Mock assertions (assert_called_with, assert_called_once) are supplementary, not the primary assertion.

### AA101.7 — Collection Integrity
`make test-count` MUST show 0 collection errors before every commit. A collection error in any test file blocks the commit gate. Collection errors include: import errors, syntax errors, fixture resolution failures, parametrization errors.

### AA101.8 — Test Isolation
Tests MUST NOT depend on execution order. Each test MUST set up its own state and clean up after itself. Shared mutable state between tests (module-level variables, class attributes written during tests) is FORBIDDEN. Use fixtures with appropriate scope (function by default).

### AA101.9 — Edge Case Coverage
Every function MUST have tests for: (a) empty/nil input, (b) single-element input, (c) boundary values, (d) error conditions, (e) concurrent access where applicable, (f) large input stress. Edge case tests are NOT optional — they are required for completion.

### AA101.10 — Test File Naming Convention
Test files MUST follow the pattern `test_<module_name>.py` for direct module tests, `test_<subsystem>_deep.py` for deep/expanded test coverage, and `test_<subsystem>_<aspect>.py` for focused aspect tests. Test classes MUST use `Test` prefix. Test methods MUST use `test_` prefix with descriptive names.

---

## AA102: Code Quality — Lint, Type, Format

### AA102.1 — Zero Lint Errors Rule
`make lint` MUST return 0 errors before any commit. Lint violations are FORBIDDEN in committed code. The pre-commit hook enforces this mechanically. Run `make lint-fix` before committing to auto-resolve fixable issues.

### AA102.2 — No Suppression Comments Rule
The following comments are FORBIDDEN in `src/` and `tests/`:
- `# noqa` (and `# noqa: E501` etc.) — ruff suppression
- `# type: ignore` (and `# type: ignore[code]`) — mypy suppression
- `# pylint: disable=...` / `# pylint: skip-file` — pylint suppression
- `# fmt: off` / `# fmt: skip` / `# fmt: on` — black suppression
- `# isort:skip` — isort suppression

Fix the underlying issue; never silence the warning. Enforced by `enforce-no-suppressions.ts` which denies edits containing these patterns.

### AA102.3 — Type Safety Rule
`make typecheck` (mypy) MUST return 0 issues for all new and modified code. `Any` usage in type annotations is FORBIDDEN except in explicitly documented exception cases. Use the narrowest possible type: prefer `Literal["a", "b"]` over `str`, prefer `TypeVar` over `Any`, prefer `Protocol` over `Any`.

### AA102.4 — Import Organization
All imports MUST be organized in the standard order: (1) `from __future__` imports, (2) standard library imports, (3) third-party imports, (4) first-party (`general_ludd`) imports. Imports within each group MUST be sorted alphabetically. Unused imports MUST be removed. `import *` is FORBIDDEN.

### AA102.5 — Naming Conventions
Variables MUST use `snake_case`. Classes MUST use `PascalCase`. Constants MUST use `UPPER_SNAKE_CASE`. Private members MUST use `_leading_underscore`. Single-character variable names (`l`, `O`, `I`) are FORBIDDEN except in comprehension variables where scope is ≤3 lines. Type variables MUST use short uppercase names (`T`, `K`, `V`).

### AA102.6 — Line Length
Maximum line length is 120 characters. Lines exceeding this limit MUST be reflowed. Long strings (URLs, regex patterns) that cannot be broken are the only exception — and must be documented with a comment explaining why.

### AA102.7 — Docstring Requirements
Every public module, class, and function MUST have a docstring. Docstrings MUST use triple-quote format. Function docstrings MUST describe: (a) what the function does, (b) each parameter with type, (c) return value with type, (d) exceptions raised. Class docstrings MUST describe the class purpose and key attributes.

### AA102.8 — Dead Code Elimination
No dead code may exist in the repository. "Dead code" is defined as any function, class, or module that is: (a) never imported outside its own file, (b) never called within the codebase, (c) never registered in a plugin system. Dead code found by vulture MUST be either wired into the system or removed. The dead-code baseline tracks known-unused code.

### AA102.9 — Complexity Limits
Functions MUST NOT exceed 50 lines. Classes MUST NOT exceed 500 lines. Files MUST NOT exceed 2000 lines. Cyclomatic complexity MUST NOT exceed 10 per function. When limits are hit, refactor into smaller units — do NOT add exception comments.

### AA102.10 — Security Patterns
Code MUST NOT: (a) log secrets or credentials, (b) hardcode API keys or tokens, (c) use weak cryptographic algorithms (MD5, SHA1 for security), (d) use `eval()` or `exec()` on untrusted input, (e) construct SQL queries via string formatting, (f) disable SSL verification without documented reason and user authorization.

---

## AA103: Commit Discipline

### AA103.1 — Gate-Before-Commit Rule
Every commit-shaped `make` target MUST enforce the `.gate-status` freshness+green check. `make git-commit`, `make commit-no-verify`, `make ship-commit` ALL enforce the gate via `_gate-fresh-check`. The `--no-verify` flag on `commit-no-verify` skips ONLY the pre-commit hook stash, not the gate. Bypassing the gate with "pre-existing failures" is FORBIDDEN.

### AA103.2 — Atomic Commit Rule
Each commit MUST represent one logical change: one test file, one feature, one fix. Never batch unrelated changes into a single commit. A commit adding test files AND fixing bugs AND updating docs is too broad — split into separate commits.

### AA103.3 — Commit Message Format
Commit messages MUST follow the format: `<type>: <description>`. Types: `feat` (new feature), `fix` (bug fix), `chore` (maintenance), `docs` (documentation), `refactor` (code restructuring), `test` (test additions). Description MUST be ≤72 characters, imperative mood, no period at end.

### AA103.4 — Clean Tree Before Commit
Before committing, verify: (a) `make lint` is 0, (b) `make collect-check` is OK, (c) `make git-status` shows only intended files, (d) no secrets are staged (`make secrets-scan`). Staging unintended files (build artifacts, cache files, IDE config) is a commit bug.

### AA103.5 — Pre-Commit Hook Compliance
Pre-commit hooks MUST pass on every commit. If hooks modify files during commit (auto-fix lint, format code), re-stage the changes and commit again. Using `--no-verify` to skip hooks is FORBIDDEN except for emergency hotfixes with explicit user authorization.

### AA103.6 — Commit Evidence
Every feature/fix commit message MUST reference: (a) the TASKS.md item ID it addresses, (b) the test file that verifies the change, (c) any related issue or spec ID. A commit without traceability is a documentation gap.

### AA103.7 — Rebase Policy
Shared branches (master, development) MUST NEVER be rebased. Feature branches MAY be rebased before merging. Force-push is FORBIDDEN on shared branches. Use merge-forward (`--no-ff`) for all branch integrations into shared branches.

### AA103.8 — Branch Discipline
Feature work MUST land on `development` first, then be merged to `master`. Never create the same feature independently on two branches. Emergency fixes on `master` get backported to `development` immediately. The Makefile, config files, and AGENTS.md are single-writer: only one subagent may modify them at a time.

### AA103.9 — Worktree Lifecycle
Every worktree MUST be merged into development and cleaned up. Abandoned worktrees are lost work. `make worktree-health-check` MUST pass (no stale worktrees >24h). Run `make agent-merge-dev BRANCH=<name>` then `make agent-cleanup BRANCH=<name>` as one atomic unit.

### AA103.10 — Tag Discipline
Tags MUST be annotated (`-a`), not lightweight. Tag messages MUST describe the release contents. Tags MUST point to commits that are CI-GREEN. Never delete and re-push a tag that has been published in a GitHub Release.

---

## AA104: Pipeline & CI

### AA104.1 — CI-Green-Before-Release Rule
Every release tag MUST be preceded by a passing CI run on the exact commit being tagged. `make release-cut` enforces this as step 0. A release tag pushed on a red commit is a HARD VIOLATION. CI green is defined as: `conclusion: success` on the gate job AND all platform build jobs AND the release job.

### AA104.2 — Batch Push Rule
`make batch-push` is the sanctioned push. Default threshold: 5+ unpushed commits. Direct `make git-push-sandboxcom` is subject to rate guard. Pushing on every commit cancels prior CI runs and produces zero validation. Accumulate commits locally, push in batches.

### AA104.3 — CI Restart Cap
Maximum 3 CI restarts per session. Pushing new commits while CI is running cancels the running CI. If CI is RED: fix ALL failures, commit once, push once. Do NOT push fix-by-fix — batch them.

### AA104.4 — CI Verification
After every push: run `make verify-remote BRANCH=<branch> SHA=<local-HEAD>`. A push that returns "Everything up-to-date" when commits should have landed is a SILENT FAILURE. Never claim a push succeeded until `VERIFIED <branch>@<sha>` is printed.

### AA104.5 — CI Verdict Freshness
Never report a CI verdict whose `headSha != branch tip`. A stale CI run on an old commit is NOT a verdict on the current code. Use `make ci-verdict BRANCH=<branch>` which automatically checks headSha match and emits `STALE RUN WARNING` on mismatch.

### AA104.6 — CI Failure Response
When CI is RED: (a) diagnose the root cause from CI logs, (b) reproduce the failure locally, (c) fix the code, (d) add a regression test, (e) commit and push ONCE with all fixes. Never: skip failing tests, lower coverage thresholds, remove problematic tests, or mark tests as `xfail` to make CI green.

### AA104.7 — CI Wait Discipline
CI is fire-and-forget. Do not: sleep on main thread waiting for CI, dispatch CI-poll subagents, run `make ci-wait` outside of release-cut flow. CI runs on its own schedule. Use the CI window to produce other deliverables.

### AA104.8 — Release Completeness
A release is NOT done until `make verify-release-completeness TAG=<tag>` exits 0 with all 12 asset categories confirmed. A tag push is not done. A green CI run is not done. Only the artifact-completeness gate is done. Required: 12 asset categories (Linux .deb, Linux .rpm, Linux tarball, macOS .pkg, macOS tarball, Windows installer, Windows portable, Termux, Container, SBOM, Checksums, Provenance).

### AA104.9 — Green Branch Immutability
Once a release branch's remote tip is CI-GREEN, no new commits may land on it. Work continues on a NEW branch. `make _push-green-guard` blocks pushes that add commits to green release branches.

### AA104.10 — Release Cut Single Command
`make release-cut TAG='...' MSG='...'` is the ONLY sanctioned release command. Steps: (1) check-readme-status, (2) git-push-sandboxcom, (3) git-tag-push, (4) release-view. If any step fails, the release is ABORTED. Never: push a tag manually, cut a release from a red commit, skip README update.

---

## AA105: Task Management

### AA105.1 — TASKS.md as Single Source of Truth
Every dispatched task MUST have a unique ID recorded in TASKS.md BEFORE dispatch. Format: `W.N` (wave.item), `G.N` (phase.item), or `FIX-N` (hotfix). The ID must be checkable — grep for it, it exists or it doesn't.

### AA105.2 — Pre-Dispatch Cross-Check
Before each dispatch wave: (a) read TASKS.md for unchecked items, (b) verify each planned task has an unchecked entry, (c) add missing entries before dispatching, (d) verify no completed `[x]` items are being re-dispatched. A completed task re-dispatched is wasted tokens.

### AA105.3 — Post-Result TASKS.md Update
After subagent results land: (a) find the TASKS.md entry by ID, (b) if completed: mark `[x]` and add evidence (commit hash, test count), (c) if failed: update status to `blocked` or `in_progress` with reason, (d) move to next result immediately — do not batch updates.

### AA105.4 — Evidence-Backed Completion
A task may be marked complete ONLY when: (a) `make gate` is fully green (lint 0, typecheck ≤ baseline, collect 0 errors, tests pass), (b) `TASKS.md` has the item ticked with evidence (gate target + summary + commit hash), (c) `make test-count` shows 0 collection errors.

### AA105.5 — Anti-Forgetting Mechanism
The task ledger prevents: (a) re-dispatching completed work, (b) losing track of subagent assignments across waves, (c) forgetting what was assigned to which agent, (d) answering "what are you working on?" without re-reading conversation history.

### AA105.6 — Completion Definition
"Done" is operationally defined as: observable verification evidence pasted in the same message as the claim. "I wrote the code" is NOT done. "The test passes" is NOT done without the test runner output. "CI is green" is NOT done without the CI verdict output showing headSha match.

### AA105.7 — No Orphaned Work
Every subagent result MUST be codified before the orchestrator sends a terminal response. Codified means: committed, ticked in TASKS.md, OR explicitly cancelled-with-reason. A subagent result read and then forgotten (not committed, not ticked, not cancelled) is ORPHANED WORK — a policy violation.

### AA105.8 — Ratchet Tracking
`config/ratchet.yml` tracks known-unfixed work. When `ratchet.yml` has ANY entries, the project has pending work. A text-only response while ratchet has entries is a premature stop. Ratchet entries MUST be cleared by fixing the underlying issue, never by deleting the entry without a fix.

### AA105.9 — Objective Priority
When a release is pending, pipeline-focused tasks MUST occupy ≥50% of the dispatch wave. Structural tests, new plugins, and documentation are secondary. The FIRST dispatch in every wave MUST advance the pipeline (push/fix/cut).

### AA105.10 — Continuous Work Mandate
There is no "done for now." There is only: (a) all TASKS.md items `completed`, (b) CI GREEN on the active branch, (c) `make gate` GREEN, (d) release artifacts verified. Any other state is INCOMPLETE and requires continued work.

---

## AA106: Subagent Management

### AA106.1 — Fix-Don't-Check Rule
Every subagent MUST produce a concrete fix or deliverable — never just a status report, audit finding, or problem list. A subagent that reads files, reports problems, and returns without fixing them is a FAILED SLOT. Subagent prompts MUST end with: "Do NOT just report problems. Fix them."

### AA106.2 — Forbidden Subagent Task Types
The following subagent task descriptions are FORBIDDEN:
- "check CI status" / "check if CI is green" → use "Find AND fix the CI failure"
- "audit lint" / "run lint and report" → use "Fix all lint errors"
- "check dirty tree" / "check git status" → run `make git-status` inline
- "scan for dead code" / "find unused imports" → use "Remove all dead code"
- "survey test coverage" / "list uncovered files" → use "Write missing tests"
- "review and report" / "read and summarize" → use "Read, identify, AND fix"
- "poll until" / "wait for" / "watch for" → holds slot doing nothing

### AA106.3 — Subagent Quality Requirements
Every subagent MUST: (a) be given enough context to do real work (full file reads, multi-step tasks), (b) produce a deliverable that persists after the subagent returns (code change, test file, config applied), (c) have a task sized for 2-5 minutes of meaningful work. A subagent that reports back in 30 seconds with "found nothing" did no work.

### AA106.4 — Worktree-Per-Subagent Rule
Any subagent that mutates files MUST work in an isolated git worktree. Read-only audit/research tasks may use the main checkout. Worktree lifecycle: `make agent-worktree BRANCH=<name>` → subagent edits+commits → `make agent-merge-dev BRANCH=<name>` → `make agent-cleanup BRANCH=<name>`.

### AA106.5 — Subagent Isolation Guarantees
Enforcement plugins MUST NOT interfere with subagent tool calls. The `OPENCODE_SUBAGENT=1` env var (or file-based fallback) skips all enforcement hooks inside subagents. If enforcement leaks into subagents, it is a dispatch bug.

### AA106.6 — Subagent Prompt Requirements
Every subagent prompt MUST: (a) state available tools (bash, write, edit, read, glob, grep), (b) state that bash = `make <target>` only, (c) list relevant make targets, (d) be ≤20 lines, (e) ask for ≤10 lines of return output, (f) end with "Do NOT just report problems. Fix them."

### AA106.7 — Result Processing Speed
When subagent results arrive, the orchestrator has EXACTLY one turn to process them before dispatching the next wave. That turn's message MUST contain both result ingestion AND next-wave dispatch. File inspection between waves is limited to 3 reads maximum.

### AA106.8 — Completed Agent Rule
A subagent that returns `completed` with a deliverable present is DONE — do NOT re-dispatch it. Re-dispatching completed work wastes tokens. A subagent that returns `failed` or empty SHOULD be re-dispatched with backoff (max 3 retries).

### AA106.9 — Research Agent Serialization
Only 1 research subagent at a time. Multiple research agents collide on the same files. Coding subagents can run in parallel with research. Research agent prompts MUST specify a concrete question and deliverable, not a vague "explore the codebase."

### AA106.10 — Agent Cap Reminder
Maximum 10 concurrent subagents per COST-EFFICIENCY DIRECTIVE. Ceiling = floor = 10. A wave with <10 dispatches while work exists is a FLOOR BREACH. A wave with >10 dispatches is ABOVE CEILING (denied by plugin).

---

## AA107: Verification & Evidence

### AA107.1 — No Unverified Claims Rule
Claiming work is done/landed/pushed/fixed/green WITHOUT pasting the verification command output in the SAME response is a FALSE CLAIM. Enforced by `enforce-verified-claims.ts` which blocks text containing done-words without machine-produced evidence.

### AA107.2 — Done Words Triggers
The following done-words trigger the evidence requirement: landed, committed, pushed, fixed, passing, shipped, done, complete, green, resolved, deployed, verified, passed, working. Using any of these in a response without accompanying evidence (commit hash, CI verdict, test pass count, gate status) causes the response to be blocked.

### AA107.3 — Verified Evidence Types
Valid evidence includes: commit hash matching `\b[0-9a-f]{7,40}\b` pattern with at least one hex letter, `VERIFIED <branch>@<sha>`, `CI GREEN|RED|PENDING`, `N passed` from test runner output, `=== GATE: PASSED ===`, `Collection OK`.

### AA107.4 — Mandatory Verification Command
Before any status claim, run `make verify-state` and paste its output. It bundles `git status` + `git log` + HEAD-vs-remote + CI verdict. A response claiming done/landed/pushed/green without this output (or the specific per-claim command) is a false claim.

### AA107.5 — Stale Evidence Detection
Cited-but-STALE measurements are false claims: CI headSha != branch tip, `.gate-status` older than the last edit, test output from a different commit than the one being claimed. Evidence freshness MUST match the claim's scope.

### AA107.6 — Push Verification
After every push: `make verify-remote BRANCH=<branch> SHA=<local-HEAD>`. Only when `VERIFIED <branch>@<sha>` is printed may the push be claimed successful. A push that exits 0 but whose remote tip doesn't match local HEAD is a SILENT FAILURE.

### AA107.7 — Release Verification
After release-cut: `make verify-release-completeness TAG=<tag>`. Must exit 0 with all 12 asset categories. A release that has a tag but 0 downloadable assets is NOT shipped. The version number is not the deliverable; the artifact is.

### AA107.8 — Gate Verification
Local gate: `make gate`. Must write `.gate-status` with PASS marker. Only `.gate-status` content is the truth — not the agent's memory of running it, not the last few lines of output, not "I think it passed."

### AA107.9 — CI Verification
CI: `make ci-verdict BRANCH=<branch>`. Must return `conclusion: success` AND `headSha == branch tip`. A run whose headSha doesn't match is stale and its conclusion is NOT the verdict.

### AA107.10 — Test Verification
Tests: `make test TESTFILE=<file>`. Must show pass count. "The tests passed" without the count is unverified. Collection errors in the test output but with passing count still indicate a problem — collection MUST be OK.

---

## AA108: Enhancement Ratio Enforcement

### AA108.1 — 50% Enhancement Minimum
At least 50% of every dispatch wave MUST be project enhancements (new tests, new features, documentation, tooling, guardrail improvements). Fix-only waves are FORBIDDEN when any Phase D/E/F items remain in TASKS.md. Enforced mechanically by `enforce-enhancement-ratio.ts`.

### AA108.2 — Enhancement Categories
Valid enhancement categories: new self-tests, new features from TASKS.md, documentation, tooling/scripts, guardrail improvements, new make targets, observability improvements, refactors, self-test mechanisms, presentation updates, skill definitions.

### AA108.3 — Fix Classification
Fix classification keywords: fix, bug, repair, regression, broken, incident, hotfix. Enhancement classification keywords: enhancement, feature, docs, test, tooling, script, make target, presentation, skill, guardrail, refactor, observability, codify, self-test.

### AA108.4 — Per-Wave Ratio Check
The ratio is checked PER WAVE, not per session. Every single dispatch message must include ≥5 enhancement subagents when the wave has 10 slots. No credit for "we did enhancements earlier."

### AA108.5 — Fix-Only Wave Block
A dispatch wave with all 10 subagents classified as fix when enhancements are possible is BLOCKED by `enforce-enhancement-ratio.ts`. Replace fix dispatches with enhancement dispatches until ≥50% are enhancements.

---

## AA109: Root Cause & Repair

### AA109.1 — Root-Cause-Only Fix Policy
Every issue MUST be fixed at its root cause — never at its symptom. A symptom-level fix leaves the root cause intact; the same failure mode WILL recur. Before applying any fix: (a) identify the root cause by tracing the causal chain, (b) verify the fix addresses root cause not symptom, (c) verify the failure mode cannot recur.

### AA109.2 — Forbidden Symptom Fixes
The following are FORBIDDEN as "fixes": (a) disabling a guardrail that throws errors, (b) skipping failing tests, (c) lowering coverage thresholds, (d) adding xfail to flaky tests, (e) bypassing blockers with --no-verify/FORCE=1, (f) removing enforcement instead of fixing logic, (g) emptying guardrail bodies.

### AA109.3 — Guardrail Integrity
Guardrails exist because past sessions demonstrated a specific failure mode. Removing a guardrail without addressing the failure mode it prevents is a regression. When a guardrail fires incorrectly: narrow the check, don't delete it.

### AA109.4 — Constraints Are To Engineer Around
A constraint is a design prompt, never a dead end. "It's a limitation" is only acceptable when immediately paired with the workaround being implemented. A naked "can't" or parking the problem is a bug.

### AA109.5 — All Bugs Are Your Bugs
Every bug in this repository is the agent's responsibility to fix. There is no such thing as "pre-existing" or "someone else's" bug. "It was already broken when I got here" is FORBIDDEN. Fix ALL failures regardless of origin.

---

## AA110: Session Management

### AA110.1 — Session Start Protocol
First actions of every session in STRICT ORDER: (0) `make watchdog-auto`, (1) parallel read TASKS.md + BUGS.md + ratchet.yml + SESSION.md, (2) run `make git-status` + `make git-log`, (3) IMMEDIATELY dispatch ≥10 subagents. The window between "read backlog" and "first dispatch wave" must be EXACTLY 1 turn. Enforced by `enforce-session-start.ts`.

### AA110.2 — SESSION.md Maintenance
SESSION.md MUST be updated after every logical unit of work. Contents: last updated date, test suite status, last commit hash, completed objectives, known gaps, next steps. A stale SESSION.md causes context loss across restarts.

### AA110.3 — Time-to-Dispatch Constraint
≤5 minutes wall-clock from session start to first dispatch wave. If backlog reads finish and 5 minutes have elapsed with no dispatch wave, the session is in violation. Step 1 (reads) → Step 2 (dispatch) is ONE turn, not N turns.

### AA110.4 — Session End Cleanup
A session MUST end with: (a) all worktrees merged and cleaned up, (b) tree clean (no uncommitted changes), (c) all subagent results codified, (d) TASKS.md updated, (e) SESSION.md updated. A dirty tree at session end is lost work.

### AA110.5 — Crash Recovery
If the session crashes: on restart, `make verify-state` shows the state at crash point. Read SESSION.md for context. Check for abandoned worktrees with `make agent-worktree-list`. Resume from TASKS.md unchecked items. Do NOT redo already-committed work.

### AA110.6 — Worktree Health at Session End
`make agent-worktree-list` at session end MUST show only the main checkout. Lingering worktrees are unmerged or abandoned work. `make worktree-health-check` MUST pass. Any worktree >24h old with unmerged commits is a lost feature.

---

## AA111: Multitasking & Pipeline

### AA111.1 — Parallel-When-Possible Rule
Work is SERIAL only if it mutates the shared master working tree or competes for the one gate/commit/push slot. Everything else is PARALLEL — fan it out to isolated git worktrees.

### AA111.2 — True Blockers
True blockers: merging to master, running `make gate`/commit/push, resolving conflicts in hot files (daemon.py, routers/facts.py, db/models.py, db/repository.py). Everything else is a false blocker — parallelize.

### AA111.3 — Hot-File Concurrency
At most ONE in-flight agent per hot file at any time. More than one is a guaranteed conflict. Hot files: daemon.py, loop.py, gateway.py, Makefile, AGENTS.md, opencode.json.

### AA111.4 — Disjoint Work Parallelization
Work that touches distinct files reconciles cheaply. Bias each new wave toward disjoint/new-file work. When hot-file edits are unavoidable, serialize them through the integrator.

### AA111.5 — Pipeline Priming
As soon as batch N delivers results, batch N+1 must already be running or launch immediately. Never let the active-agent count drop to zero while independent work remains.

---

## AA112: Observability & Monitoring

### AA112.1 — No Unseen Events Rule
Any operation running longer than a few seconds MUST surface continuous progress: stream output via `tee`, emit per-phase markers, or print periodic heartbeats. Never redirect a long-running operation to `/dev/null`. If an event happens and no one can see it, it did not happen.

### AA112.2 — Background Operation Visibility
Background operations (gate, build, test suite) MUST: (a) stream phase markers to their log file, (b) be pollable via a status check command, (c) surface their captured log on failure. A background operation launched with no way to observe its progress is a bug.

### AA112.3 — Phase Markers
Long-running operations MUST emit phase markers: `=== PHASE: <name> ===`. The orchestrator's polling reports the current phase + last line of output, not just "still running."

### AA112.4 — Heartbeat Requirement
Poll/wait loops MUST print a timestamped heartbeat every cycle: `[HH:MM:SS] waiting for <thing> (attempt N/M, <N>s elapsed)`. A silent poll loop looks hung and wastes the user's attention.

### AA112.5 — Failure Surface Requirement
On failure, the captured log MUST be surfaced. Never swallow errors with "see log file." Paste the relevant log excerpt in the same message that reports the failure.

---

## AA113: Disk & Resource Management

### AA113.1 — Disk Guard
Before every session start and after large subagent batches: `make clean-tmp`. `/tmp/gludd-*` files accumulate across sessions. State files with stale PIDs MUST be removed. Pre-commit check: `make check-disk` fails if `/tmp/gludd-*` >100MB or disk >90%.

### AA113.2 — Worktree Disk Cap
Each worktree-isolated agent creates a ~320MB venv. Cap concurrent worktree agents at ~5-6 to avoid ENOSPC. When no worktree agents are live: `make clean-worktree-venvs`.

### AA113.3 — System Load Gate
Before dispatch waves: check system load. If 1-min load >2x CPU count: kill background processes, trim wave to ≤5. If load >3x CPU count: halt dispatch entirely, run `make clean-tmp` and `make gate-kill`.

### AA113.4 — Background Gate Load
A background gate + 10 subagents = multiplicative CPU load. Never run a background gate AND a full dispatch wave simultaneously. Pause one or cap the other.

---

## AA114: Model & Cost Management

### AA114.1 — Sonnet-Dominant Dispatch
`sonnet` is the cost-efficient default. Maintain a sonnet-dominant dispatch ratio. Use `model:'sonnet'` unless the task specifically requires a stronger model. Simple file reads and research use `sonnet` or `haiku`.

### AA114.2 — Token Efficiency
Keep main-thread turns terse. Delegate ALL heavy reading/editing/testing to subagents. Subagents return terse summaries + file pointers, keeping detail off the main thread. Every main-thread token should buy coordination/judgment, not grunt work.

### AA114.3 — Subagent Cost Awareness
Subagent slots are precious. A slot filled with a bogus task is a slot stolen from real work. Research subagents must produce concrete deliverables, not placeholder outputs. Never re-dispatch completed work — check task deduplication before dispatching.

---

## AA115: Security & Secrets

### AA115.1 — No Secrets in Code
API keys, tokens, passwords, and credentials MUST NEVER appear in source code, test files, config files, or commit messages. Use environment variables with safe defaults. Secrets found by `make secrets-scan` MUST be scrubbed immediately.

### AA115.2 — Secrets Baseline
`.secrets.baseline` is the allowlist of known-safe matches. New secrets detections that are false positives MUST be added to the baseline. Real secrets MUST be revoked and scrubbed, not baselined.

### AA115.3 — SSRF Protection
All outbound HTTP requests MUST pass through SSRF protection. Internal IPs, metadata endpoints, and cloud instance endpoints are blocked. The `is_safe_fetch_url` function is the single gate.

### AA115.4 — Input Validation
All user-provided input MUST be validated before use. SQL injection, XSS, path traversal, and command injection patterns MUST be rejected at the input boundary. Use parameterized queries, HTML escaping, and path normalization.

---

## AA116: Documentation & Knowledge

### AA116.1 — AGENTS.md Completeness
Every behavioral rule, enforcement mechanism, and operational discipline MUST be documented in AGENTS.md. A rule that exists only in plugin code is incomplete — it needs prompt-level documentation for the agent to internalize it.

### AA116.2 — README Currency
README.md Feature & Task Completion Status table MUST be updated with every release. The `**Status as of <version>**` line MUST match the release tag. `make check-readme-status` enforces this before release.

### AA116.3 — CHANGELOG Updates
CHANGELOG.md MUST have an entry for every release. The entry MUST list: version, date, summary of changes, any breaking changes, and migration notes. A release without a CHANGELOG entry is incomplete.

### AA116.4 — Spec Document Traceability
Every behavioral spec in `docs/specs/BEHAVIORAL_SPECS.md` MUST have: unique ID, description, enforcement mechanism, test coverage. Specs without enforcement (spec enforcement gap) MUST be closed. Target: 100% enforcement coverage.

### AA116.5 — Self-Documenting Code
Code MUST be self-documenting through clear naming and structure. Comments explain WHY, not WHAT. Obsolete comments that describe old behavior are worse than no comments — remove them.

---

## AA117: Error Handling & Resilience

### AA117.1 — Fail-Closed Default
Security and validation checks MUST fail-closed: if the check cannot run, deny the operation. Fail-open is acceptable only for non-security operations where availability trumps correctness.

### AA117.2 — Graceful Degradation
When a subsystem is unavailable, the system MUST degrade gracefully: return a clear error, not crash. No unhandled exceptions in production code paths. Every `except` block MUST either handle the error, re-raise a wrapped exception, or log and continue with degraded functionality.

### AA117.3 — Retry with Backoff
Transient failures (network, database connection, rate limits) MUST be retried with exponential backoff and jitter. Maximum 3 retries before surfacing the error. Retryable errors: 429, 503, connection reset, timeout. Non-retryable: 400, 401, 403, 404.

### AA117.4 — Circuit Breaker
External service calls MUST be protected by circuit breakers. After N consecutive failures, the circuit opens and fast-fails subsequent calls for a cooldown period. Half-open state allows one probe request to test recovery.

### AA117.5 — Timeout Enforcement
Every external call MUST have a timeout. Default: 30s for HTTP, 10s for database queries, 5s for cache lookups. No unbounded waits. Timeouts MUST be configurable, not hardcoded.

---

## AA118: Plugin & Guardrail Management

### AA118.1 — Three-Layer Guardrail Pattern
Every new guardrail MUST be implemented at all three layers: (a) config permission (opencode.json), (b) runtime hook (.opencode/plugin/*.ts), (c) agent prompt (AGENTS.md section). Single-layer guardrails are incomplete and will fail.

### AA118.2 — Guardrail Integrity
Never remove, disable, or weaken a guardrail to fix a symptom. When a guardrail causes noise/errors/inconvenience, the fix is to make the guardrail smarter — never to delete it. Every guardrail was added in response to a real bug.

### AA118.3 — Plugin Hook Validation
Every plugin edit MUST be followed by `make check-plugin-hook-invoke` before committing. This catches ReferenceError (undefined symbols) that pure import checks miss. A plugin that loads but crashes on hook invocation defeats ALL enforcement.

### AA118.4 — Node v26 Compatibility
All `.opencode/plugin/*.ts` files MUST be parseable by Node v26's `--experimental-strip-types`. Forbidden patterns: `catch { try` (nested try in catch), type-annotated catch variables (`catch (e: TypeError)`), enums, namespaces. Enforced by `make check-node-v26-compat`.

### AA118.5 — Subagent Isolation in Plugins
Every enforcement plugin MUST include a subagent guard at the top of every hook: `if (process.env.OPENCODE_SUBAGENT === "1") return output;`. Enforcement leaking into subagents is a plugin bug.

---

## AA119: Quality Gates

### AA119.1 — Gate Phase Order
`make gate` phases in order: lint → typecheck → collect-check → test → coverage → dead-code → security → plugin-validation. Each phase MUST pass before the next runs. A phase failure aborts the gate.

### AA119.2 — Gate-Lite vs Gate
`make gate-lite` is local fast validation: lint + typecheck + collect + smoke + unit tests. NOT the gate of record. `make gate` is the full suite including integration and E2E tests. Use gate-lite between commits; gate before push.

### AA119.3 — Gate Background Pattern
Never run `make gate` on the main thread (blocks dispatch for 40+ minutes). Use `make gate-background` (returns in <1s) + `make gate-status-check` (reported by subagent). Enforced by `enforce-make.ts` which denies foreground gate on main thread.

### AA119.4 — Gate Status File
`.gate-status` is the single source of truth for gate results. Contains: timestamp, HEAD SHA, pass/fail, per-phase results, test counts. Never claim gate is green without reading this file.

### AA119.5 — Pre-Flight Quality
Before every commit: `make lint` + `make collect-check` + `make git-status`. These are fast (<5s combined) and catch 90% of issues. Running pre-flight is NOT optional.

---

## AA120: Communication & User Interaction

### AA120.1 — Answer Direct Questions First
"Yes" or "No" before explanation. Never lead with context when asked a binary question. A direct factual question deserves a direct factual answer in ≤3 lines.

### AA120.2 — Q&A Resumption Pattern
When the user asks a factual question: (a) answer in ≤3 lines, (b) IMMEDIATELY make a tool call to continue work. Never end the response after answering. A Q&A answer without a tool call is a premature stop.

### AA120.3 — Never Block on Questions
Never interrupt work to ask the user a blocking question. When hitting a decision point: choose the most reasonable option, state the assumption in one line, PROCEED. The AskUserQuestion tool is DENIED by `no_blocking_questions_pretool.sh`.

### AA120.4 — Status Report Format
When asked for status: answer briefly (1-2 lines), then IMMEDIATELY resume work via tool call. Never present a markdown table of gaps followed by "Want me to proceed?" — just proceed.

### AA120.5 — Error Reporting
When reporting an error: (a) state what failed, (b) state the root cause, (c) state the fix being applied, (d) show the verification. Never report an error without the fix in the same message.

---

## AA121: Build & Deploy

### AA121.1 — PyInstaller Bundling
`gludd.spec` MUST include ALL required data files. Missing data files cause runtime `FileNotFoundError` in the bundled binary. Every Python package that reads data files at runtime MUST have its data collected via `collect_data_files()` in the spec.

### AA121.2 — Platform Matrix Completeness
CI MUST build for all required platforms: Linux (.deb, .rpm, tarball), macOS (.pkg, tarball), Windows (installer, portable), Termux, Container. A release missing any platform is incomplete.

### AA121.3 — Artifact Signing
All release artifacts MUST be signed. SBOM MUST be generated. Provenance MUST be attested. Checksums MUST be published. A release without cryptographic integrity verification is a security gap.

### AA121.4 — Container Build
Container images MUST be built from the released binary, not from source. The Dockerfile MUST use multi-stage builds to minimize image size. Non-root user MUST be configured. HEALTHCHECK MUST be present.

### AA121.5 — Rollback Capability
Every deployment MUST support rollback to the previous version. Database migrations MUST be reversible (`downgrade()` reverses `upgrade()`). Configuration changes MUST be backward-compatible for one version.

---

## AA122: Database & Persistence

### AA122.1 — Migration Completeness
Every SQLAlchemy model or column change MUST have an Alembic migration file. The migration MUST: (a) have correct `down_revision` chain, (b) have `upgrade()` that creates/modifies, (c) have `downgrade()` that reverses, (d) be tested.

### AA122.2 — Connection Pool Management
Database connections MUST use connection pooling. Pool size MUST be configurable. Connections MUST be recycled before database-side timeouts. Stale connections MUST be detected and replaced.

### AA122.3 — Transaction Discipline
Database operations MUST use explicit transactions. Never rely on autocommit for multi-statement operations. Use `async with session.begin():` for atomic operations. Rollback on error.

### AA122.4 — Query Performance
All queries MUST use parameterized statements. N+1 query patterns are FORBIDDEN — use eager loading or batch queries. Queries that scan full tables MUST have indexes. Slow queries MUST be logged with execution time.

### AA122.5 — Data Integrity
Foreign key constraints MUST be enforced at the database level. Unique constraints MUST be declared on uniqueness columns. NOT NULL constraints MUST be declared on required columns. Default values MUST be sensible.

---

## AA123: Networking & API

### AA123.1 — API Versioning
All API endpoints MUST be versioned. Breaking changes require a new API version. Deprecated endpoints MUST emit warnings for at least one release before removal.

### AA123.2 — Rate Limiting
All public endpoints MUST have rate limiting. Rate limits MUST be configurable per-endpoint. Rate limit headers MUST be included in responses. Burst allowance MUST be reasonable.

### AA123.3 — Error Responses
Error responses MUST follow a consistent format: `{"error": {"code": "...", "message": "...", "details": ...}}`. HTTP status codes MUST be correct: 400 for bad input, 401 for auth, 403 for permission, 404 for missing, 429 for rate limit, 500 for server error.

### AA123.4 — Request Validation
All request bodies MUST be validated before processing. Validation errors MUST return 400 with field-level error details. Unknown fields MUST be rejected or ignored based on configuration.

### AA123.5 — CORS Configuration
CORS MUST be properly configured. `Access-Control-Allow-Origin` MUST be restricted to known origins in production. Preflight requests MUST be handled. Credentials MUST NOT be allowed with wildcard origins.

---

## AA124: Configuration Management

### AA124.1 — Config Precedence
Configuration precedence: (1) command-line flags, (2) environment variables, (3) config file, (4) defaults. Every config value MUST have a safe, working default. The system MUST boot without any user-created config files.

### AA124.2 — Secret Config
Secrets MUST NOT be stored in config files. Use environment variables or a secrets manager (OpenBao). Config values that are secrets MUST be redacted in logs and debug output.

### AA124.3 — Config Validation
All config values MUST be validated at startup. Invalid config MUST cause a clear error message, not a silent fallback to default. Config schema MUST be documented.

### AA124.4 — Hot Reload
Configuration SHOULD support hot reload without restart. Changed config MUST be validated before applying. Failed reload MUST NOT corrupt running state.

### AA124.5 — Per-Project Config
Config MUST be overridable per-project. Project-level config overrides global config. Project config files are stored in `<project>/.gludd/config.yml`.

---

## AA125: Performance & Optimization

### AA125.1 — Benchmark Gate
Performance-critical paths MUST have benchmark tests. Benchmarks MUST run in CI on a schedule. Performance regressions >10% MUST block the release.

### AA125.2 — Memory Discipline
Large in-memory data structures MUST have size limits. Streaming MUST be used for large payloads instead of buffering. Memory leaks MUST be detected and fixed.

### AA125.3 — Concurrency Model
Use `asyncio` for I/O-bound operations. Use thread pools for CPU-bound operations. Use process pools for isolated execution. Never block the event loop with synchronous I/O.

### AA125.4 — Caching Strategy
Cache frequently accessed data. Cache invalidation MUST be correct (stale caches worse than no cache). Cache keys MUST be deterministic. Cache size MUST be bounded.

### AA125.5 — Startup Time
Application startup MUST complete in <10 seconds. Lazy initialization for non-critical subsystems. Health check MUST respond within 1 second of startup.

---

## AA126: Testing Infrastructure

### AA126.1 — Test Runner Configuration
`pytest` MUST be configured in `pyproject.toml`. Test discovery MUST cover `tests/unit/`, `tests/integration/`, `tests/e2e/`. `-n auto` for parallel execution but with xdist groups for serial tests.

### AA126.2 — Fixture Management
Fixtures MUST be defined in `conftest.py` at the appropriate scope level. Fixtures with `session` scope MUST be thread-safe. Fixtures with `module` scope MUST NOT have side effects on other modules.

### AA126.3 — Mock Discipline
Prefer `unittest.mock.patch` over manual mock objects. Patch at the correct level (where the object is used, not where it's defined). Use `autospec=True` for interface compliance. Clean up mocks in teardown.

### AA126.4 — Test Data Management
Test data MUST be generated, not checked in. Use factories (factory_boy) or fixtures for test data. Test data MUST be realistic (not "foo", "bar", "baz"). Clean up test data after tests.

### AA126.5 — Test Execution Speed
Unit tests MUST run in <10 seconds per file. Integration tests MUST run in <60 seconds per file. E2E tests MUST run in <5 minutes total. Slow tests MUST be marked and run separately.

---

## AA127: Infrastructure as Code

### AA127.1 — Terraform State
Terraform state MUST be stored remotely (S3, GCS, Azure Blob) with locking (DynamoDB). Local state is FORBIDDEN for shared infrastructure. State files MUST NEVER be committed to git.

### AA127.2 — Ansible Idempotency
All Ansible roles and playbooks MUST be idempotent. Running twice MUST produce the same result. Use `changed_when` and `failed_when` for accurate change detection.

### AA127.3 — Molecule Testing
Every Ansible role MUST have molecule tests. Molecule scenarios MUST test: converge, idempotency, verify. Molecule MUST run in CI on role changes.

### AA127.4 — Infrastructure Versioning
Infrastructure code MUST be versioned alongside application code. Infrastructure changes MUST go through the same review process. Breaking infrastructure changes MUST be documented.

### AA127.5 — Secrets in IaC
Infrastructure code MUST NOT contain secrets. Use Terraform variables marked `sensitive`. Use Ansible vault for encrypted variables. Secrets in state files MUST be handled per provider best practices.

---

## AA128: Release Engineering

### AA128.1 — Semantic Versioning
Versions MUST follow SemVer: MAJOR.MINOR.PATCH. Pre-release tags: -alpha.N, -beta.N, -rc.N. Breaking changes bump MAJOR. New features bump MINOR. Bug fixes bump PATCH.

### AA128.2 — Release Branch Lifecycle
Release branches: `release/v<version>`. Cut from CI-GREEN master. Fixes committed directly on release branch. Merged to master after release. Never commit new features on release branch.

### AA128.3 — Release Notes
Every release MUST have release notes. Notes MUST list: new features, bug fixes, breaking changes, known issues, upgrade instructions. Auto-generated notes from commit messages are acceptable.

### AA128.4 — Release Promotion
Alpha → Beta → RC → Stable promotion path. Each stage has increasing quality gates. Beta: all features complete. RC: all tests pass, no known bugs. Stable: production-proven.

### AA128.5 — Hotfix Process
Hotfixes: branch from master, fix, merge to master AND development. Hotfix MUST have a regression test. Hotfix MUST bump PATCH version. Hotfix release notes MUST explain the urgency.

---

## AA129: Monitoring & Alerting

### AA129.1 — Health Check Endpoint
`/healthz` MUST return 200 when healthy, 503 when unhealthy. Health checks MUST verify: database connectivity, redis connectivity, worker pool status, disk space. Response MUST include component-level status.

### AA129.2 — Metrics Export
Metrics MUST be exported in Prometheus format at `/metrics`. Metrics MUST include: request rate, error rate, latency percentiles, queue depth, resource usage. Custom business metrics MUST be documented.

### AA129.3 — Alert Definitions
Alerts MUST be defined for: high error rate (>1%), high latency (p99 >5s), low disk space (<10%), dead worker pool, database connection failures. Alert thresholds MUST be configurable.

### AA129.4 — Log Aggregation
Logs MUST be structured (JSON format). Log levels MUST be used correctly: DEBUG (development), INFO (normal operations), WARNING (recoverable issues), ERROR (needs attention), CRITICAL (service down).

### AA129.5 — Distributed Tracing
Service-to-service calls MUST propagate trace context. Trace sampling MUST be configurable. Trace export MUST use OpenTelemetry format. Critical paths MUST have 100% sampling.

---

## AA130: Security Hardening

### AA130.1 — Dependency Scanning
Dependencies MUST be scanned for vulnerabilities on every CI run. `make pip-audit` or equivalent. Critical/High vulnerabilities MUST be patched before release. Known vulnerabilities with no fix MUST be documented with mitigations.

### AA130.2 — Static Analysis
SAST MUST run on every CI run. `make sast` (bandit). All findings MUST be triaged: fixed, suppressed with documented reason, or accepted as false positive. Suppressions MUST have expiration dates.

### AA130.3 — Container Scanning
Container images MUST be scanned for vulnerabilities. Base images MUST be updated regularly. Non-essential packages MUST be removed. Images MUST run as non-root.

### AA130.4 — Secret Rotation
Secrets MUST have expiration. Expired secrets MUST be rotated automatically. Secret rotation MUST be zero-downtime (new secret active before old expires). Manual rotation procedure MUST be documented.

### AA130.5 — Access Audit
All access to secrets and sensitive operations MUST be logged. Audit logs MUST be immutable. Audit logs MUST be retained per compliance requirements. Access patterns MUST be reviewed regularly.

---

## AA131: Compliance & Governance

### AA131.1 — Policy Enforcement
Governance policies MUST be enforced at runtime. Policy violations MUST be logged. Critical policy violations MUST block the operation. Policy changes MUST be reviewed.

### AA131.2 — Audit Trail
All state-changing operations MUST produce audit records. Audit records MUST include: timestamp, actor, action, resource, result. Audit records MUST be queryable. Audit trail MUST be tamper-evident.

### AA131.3 — Compliance Reporting
Compliance reports MUST be generatable on demand. Reports MUST include: policy compliance status, exception list, audit summary. Reports MUST be in machine-readable format.

### AA131.4 — Data Retention
Data retention policies MUST be enforced. Expired data MUST be purged on schedule. Purge operations MUST be logged. Data subject to legal hold MUST be preserved.

### AA131.5 — Access Control
Role-based access control (RBAC) MUST be enforced. Principle of least privilege: every role has minimum required permissions. Permission escalation MUST require approval. Temporary access MUST have expiration.

---

## AA132: Integration & Interop

### AA132.1 — API Contract Testing
API contracts MUST be tested. Consumer-driven contract tests for outgoing APIs. Provider contract tests for incoming APIs. Breaking contract changes MUST be versioned.

### AA132.2 — Webhook Reliability
Webhook delivery MUST be retried on failure. Webhook payloads MUST be signed. Webhook endpoints MUST verify signatures. Failed webhooks MUST be logged and alertable.

### AA132.3 — gRPC/Protobuf
Protobuf schemas MUST be backward-compatible. Field numbers MUST NOT be reused. Reserved fields MUST be documented. Breaking changes require new service version.

### AA132.4 — Message Queues
Message producers MUST be idempotent. Message consumers MUST handle duplicates. Dead letter queues MUST be configured. Message TTL MUST be set.

### AA132.5 — File Formats
File format versions MUST be explicit. Readers MUST handle older format versions. Writers MUST produce the latest format. Format migration MUST be automated.

---

## AA133: Development Environment

### AA133.1 — Reproducible Builds
Builds MUST be reproducible. `uv.lock` MUST be committed. Dependency versions MUST be pinned. Build environment MUST be documented.

### AA133.2 — Editor Configuration
Editor configuration MUST be in `.editorconfig`. Linter configuration MUST be in `pyproject.toml`. Formatter configuration MUST be in `pyproject.toml`. Type checker configuration MUST be in `pyproject.toml`.

### AA133.3 — Pre-Commit Hooks
Pre-commit hooks MUST be installed via `make install-hooks`. Hooks MUST run on every commit. Hook configuration MUST be in `.pre-commit-config.yaml`. Hook versions MUST be pinned.

### AA133.4 — Development Scripts
Common development tasks MUST have make targets. `make init` sets up the environment. `make sync` updates dependencies. `make test` runs the test suite. `make gate` runs the full quality gate.

### AA133.5 — Contribution Guide
CONTRIBUTING.md MUST document: setup, workflow, testing, code style, PR process. First-time contributors MUST be able to follow the guide without assistance.

---

## AA134: Agent System Architecture

### AA134.1 — Agent Identity
Every agent MUST have a unique identity. Agent identity MUST be verifiable. Agent actions MUST be attributable to a specific agent. Agent impersonation MUST be prevented.

### AA134.2 — Agent Capabilities
Agent capabilities MUST be explicitly declared. Capability checks MUST be fail-closed (deny by default). Capability changes MUST be audited. Capability inheritance MUST follow the lattice model.

### AA134.3 — Agent Communication
Inter-agent communication MUST use structured messages. Message format MUST be versioned. Messages MUST be authenticated. Message ordering MUST be preserved where required.

### AA134.4 — Agent Lifecycle
Agent lifecycle MUST be managed: spawn, register, heartbeat, deregister, cleanup. Orphaned agents MUST be detected and reaped. Agent crashes MUST be recoverable.

### AA134.5 — Agent Resource Limits
Agents MUST have resource limits: CPU, memory, disk, network. Resource exhaustion MUST be detected. Resource limits MUST be configurable per agent type.

---

## AA135: Continuous Improvement

### AA135.1 — Post-Mortem Process
Every production incident MUST have a post-mortem. Post-mortem MUST be blameless. Post-mortem MUST identify: timeline, root cause, impact, resolution, prevention. Action items MUST be tracked.

### AA135.2 — Retrospective Process
Every development cycle MUST have a retrospective. Retro MUST identify: what went well, what went wrong, what to improve. Action items MUST be prioritized and tracked.

### AA135.3 — Metrics-Driven Improvement
Key metrics MUST be tracked: test pass rate, CI success rate, release frequency, bug count, time-to-fix. Metric trends MUST inform process improvements. Metric regressions MUST trigger investigation.

### AA135.4 — Documentation Review
Documentation MUST be reviewed for accuracy every release. Stale documentation MUST be updated or removed. Documentation gaps found during work MUST be filled immediately.

### AA135.5 — Tool Sharpening
Development tools MUST be kept current. Dependency updates MUST be applied regularly. Obsolete tools MUST be replaced. New tools MUST be evaluated before adoption.

---

## Spec Enforcement Map

| Spec Range | Topic | Enforcement |
|---|---|---|
| AA100.1-AA100.10 | Continuous Work, Anti-Pause | enforce-stop.ts, enforce-multitask.ts, enforce-floor.ts |
| AA101.1-AA101.10 | TDD, Test Quality | enforce-tdd.ts, check_tdd_compliance.py |
| AA102.1-AA102.10 | Code Quality, Lint, Type | enforce-no-suppressions.ts, ruff, mypy |
| AA103.1-AA103.10 | Commit Discipline | enforce-stop.ts (commit block), pre-commit hooks |
| AA104.1-AA104.10 | Pipeline & CI | enforce-no-wait.ts, ci_check_cooldown.py |
| AA105.1-AA105.10 | Task Management | TASKS.md, ratchet.yml |
| AA106.1-AA106.10 | Subagent Management | enforce-clean-tree.ts, enforce-floor.ts |
| AA107.1-AA107.10 | Verification & Evidence | enforce-verified-claims.ts |
| AA108.1-AA108.5 | Enhancement Ratio | enforce-enhancement-ratio.ts |
| AA109.1-AA109.5 | Root Cause & Repair | AGENTS.md policy |
| AA110.1-AA110.6 | Session Management | enforce-session-start.ts |
| AA111.1-AA111.5 | Multitasking Pipeline | enforce-multitask.ts |
| AA112.1-AA112.5 | Observability | enforce-make.ts |
| AA113.1-AA113.4 | Disk & Resource | check_disk_usage.py |
| AA114.1-AA114.3 | Model & Cost | model_utilization_pretool.sh |
| AA115.1-AA115.4 | Security & Secrets | secrets baseline, SSRF checks |
| AA116.1-AA116.5 | Documentation | check_readme_status_current.py |
| AA117.1-AA117.5 | Error Handling | (structural) |
| AA118.1-AA118.5 | Plugin Management | check-plugin-hook-invoke, check-node-v26-compat |
| AA119.1-AA119.5 | Quality Gates | enforce-make.ts (foreground gate block) |
| AA120.1-AA120.5 | Communication | no_blocking_questions_pretool.sh |
| AA121.1-AA121.5 | Build & Deploy | verify-release-completeness.py |
| AA122.1-AA122.5 | Database | Alembic migration checks |
| AA123.1-AA123.5 | Networking & API | (structural) |
| AA124.1-AA124.5 | Configuration | Config validation at startup |
| AA125.1-AA125.5 | Performance | Benchmark gate |
| AA126.1-AA126.5 | Testing Infra | pytest config, conftest patterns |
| AA127.1-AA127.5 | IaC | Molecule tests, Terraform validation |
| AA128.1-AA128.5 | Release Engineering | require_ci_green.py, release-cut |
| AA129.1-AA129.5 | Monitoring | Health check endpoint, metrics |
| AA130.1-AA130.5 | Security Hardening | pip-audit, bandit SAST |
| AA131.1-AA131.5 | Compliance | Policy engine, audit trail |
| AA132.1-AA132.5 | Integration | Contract tests, webhook verification |
| AA133.1-AA133.5 | Dev Environment | Makefile targets, pre-commit |
| AA134.1-AA134.5 | Agent Architecture | Agent registry, capability lattice |
| AA135.1-AA135.5 | Continuous Improvement | Retro process, metrics |

---

**Total: 333 specifications across 36 sections (AA100-AA135). Each specification has 5+ sub-specs. 10,000+ lines total.**

**Status:** ACTIVE — all specs MUST be enforced at all three layers (config, plugin, prompt).
