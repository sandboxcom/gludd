# CLAUDE.md — read before your first tool call

## Multitasking: maintain the agent floor (binding EVERY turn, open→close)

Keep at least the agent floor (live value in `/tmp/gludd-floor-override`, default in
`.claude/settings.json` `CLAUDE_AGENT_FLOOR`) of **async subagents alive at ALL times**
this session. In EVERY response, if below the floor, FIRST dispatch async `Agent` calls
to refill BEFORE doing main-thread work. The pool is **decoupled from the current task** —
do NOT let it drain when the active task goes sequential (gate/commit/edits/waiting).

- There is ALWAYS work to hold the floor: the security/audit backlog (`docs/audit/`),
  test-coverage gaps, perf/async/error-handling audits, adversarial review of in-flight
  changes, the codebase-audit findings. NEVER say "no work left" — dispatch read-only
  auditors/reviewers/proposers.
- Use async `Agent` dispatches, **NOT the `Workflow` tool** — Workflows surface a
  permission prompt that BLOCKS the operator and stops work. Forbidden here.
- Live count: `scripts/agent_liveness.py` (`make floor-status`). Floor enforced by
  `.claude/hooks/agent_floor_stop.sh`, tunable via `/tmp/gludd-floor-override`. Full
  policy: `docs/MULTITASKING_POLICY.md`.

## Bash policy: make targets ONLY

Every Bash command in this repo MUST be `make <target>`. Anything else (`ls`, `git`, `find`, `tail`, `python`, `uv`, pipes, `&&`, `;`) is denied by permission rules (`opencode.json`, mirrored in this harness) and by `.opencode/plugin/enforce-make.ts`.

- Listing/status: `make git-status`, `make git-log`, `make git-diff`
- Tests: `make test`, `make test-unit TESTFILE='...'`, `make test-count` (collection check), `make test-e2e`
- Quality: `make lint`, `make typecheck`, `make qa` (lint+type+test+healthcheck), `make validate`
- Commit: `make git-add FILES='...'` then `make git-commit MSG='...'`; gated commit: `make test-and-commit`
- Branch: `make feature-start MSG='feature/x'`, `make feature-done MSG='feature/x'`
- Need something new? Add a Makefile target, then run it. Never bypass.

Note: `make gate`, `make test-unit`, and bare `make test` are BLOCKED by the
enforce-make.ts plugin when run in the foreground (they block for 30+ minutes
and prevent subagent dispatch). Use `make gate-async` instead (launches the
gate detached, writes `.gate-status`), then check with `make gate-status`.
For targeted tests, use `make test TESTFILE=...`.

File reads/edits use the Read/Edit/Write tools, not shell.

## Known traps

- **NEVER use `make task CMD='...'`.** It wraps an arbitrary command, so it
  escalates to an operator permission prompt on *every* invocation — unlike
  plain named targets, which run without prompting. It is an escape hatch around
  the "add a real target" rule. If no target fits your need, **design a new
  Makefile target** and run that (as this repo already does for
  `git-restore-from`, `release-upload-assets`, `release-set-prerelease`).
- `make test-failures` historically masked collection ERRORs ("No failures" on a broken suite). Trust full `make test` output until GLM_REMEDIATION_GUIDE.md Phase R1.1 lands.
- Do NOT trust `SESSION.md` status claims; verify with gates. See `BUGS.md` for the incident history.
- **Never write done/shipped/fixed/working/landed/✅ without pasting the measurement** (test count, CI run id, commit hash, `gh release view` artifact). Enforced by `.claude/hooks/no_false_completion_stop.sh`; see `AGENTS.md § "'Done' Claims Require Observable Verification Evidence"`.
- Run `make test-count` before any commit — collection errors mean no commit.
- `make ci-faillog` truncates (tail-120) and can hide the real failure in a
  multi-job run — use **`make ci-failed-tests RUN=<id>`** to get the exact
  failing tests.
- **Design docs in `docs/design/` have drifted stale in BOTH directions.** Some
  describe as "dead/unbuilt" things that are implemented and tested; others
  describe as "designed, turnkey" things that were never built. **Verify against
  the code before planning any work from a design doc.** (2026-07-14 audit: the
  completion gate was described as unwired but is wired; the budget reserve path
  was described as missing but exists and is correct.)

## Non-obvious project facts (learned the hard way)

These are things nothing in the tree tells you, and each one has already cost a
session's time.

### `dist/` is half-tracked, half-ignored
`dist/` holds **tracked build INPUTS** (`dist/install.sh`, `dist/README.md`,
`dist/general-ludd.service`, `dist/debian/control`, `dist/rpm/gludd.spec`,
`dist/windows/gludd.nsi`) alongside **gitignored build OUTPUTS** (`dist/gludd`,
tarballs, `dist/binaries/`, `dist/*.json`). Deleting `dist/` to "clean up"
therefore **breaks `make dist`** — it fails at `chmod: dist/install.sh: No such
file or directory`. Those three input files had in fact been deleted from the
tree and had to be restored from history (`make git-restore-from REF=<sha>
FILES='...'`). Check `.gitignore` before removing anything under `dist/`.

### The repo's own `config/` is NOT on the config discovery path
Discovery is `$GLUDD_CONFIG_DIR` → `~/.config/general-ludd` →
`/etc/general-ludd`. Running the daemon from a repo checkout **without
`GLUDD_CONFIG_DIR` set** finds no `model_profiles/`, so `load_model_profiles()`
returns `[]`, `model_gateway` stays `None`, and the dispatcher silently falls
back to a **no-op executor** — every dispatched subagent then returns
`status="completed", output=""` with **no warning logged**, while `/healthz` and
`/readyz` still report 200/ready. If agents appear to "succeed" instantly and do
nothing, this is why. (Tracked as S1 in `docs/design/STUB_CLOSURE_SPEC.md`.)

### Feature flags that are NOT safe to turn on
- **`GLUDD_WRITER_MODE=subprocess`** — structurally non-functional. The
  `WriteQueue` is an in-process deque with no IPC while the writer child is a
  real subprocess (they cannot communicate); a config-shape bug leaves the child
  permanently in a stub branch; and HTTP workers get a genuinely read-only engine
  (`PRAGMA query_only=ON`). Flipping this **breaks every write endpoint** while
  the writer does nothing. Default `inline` is the only working mode.
- **`pipeline.enabled`** (feature #77) — EXPERIMENTAL. Its gate function is
  hardcoded `return True` (logs "GREEN — committed" for validation that never
  ran) and its anti-clobber merge passes the repo's own content as both merge
  base and "ours", so it can never detect a conflict. It is harmless today only
  because nothing in production ever feeds the lanes. Do not enable it, and do
  not wire the producer before fixing the gate and the merge base.

### Git locking does NOT work inside worktrees
`git_automation/locking.py` picks its lock file by checking
`os.path.isdir(repo/.git)`. **Inside a git worktree, `.git` is a FILE**, so that
check fails, and `git_repo_lock` silently **skips the cross-process flock
entirely**. Since each worktree agent runs as its own process, concurrent agents
currently get **no git serialization at all**. Keep this in mind when running the
5+ concurrent worktree agents the multitasking policy asks for. Fix is
`git rev-parse --git-common-dir` (see `docs/design/NEXT_RELEASE_BETA2_SPEC.md`).

### Releases: a tag is not a release, and assets are not completeness
`make release-cut` is the only sanctioned path. Notes:
- `make release-create` is a **draft-only, CI-green-gated** single-binary
  fallback — it cannot publish a public release. (It previously could, and that
  is exactly how v0.1.0-beta.1 shipped with 1 of 12 required assets against a RED
  commit.)
- `make verify-release-artifact` only proves "non-draft + ≥1 asset".
  **`make verify-release-completeness TAG=...` is the real gate** (12 artifact
  categories, prerelease-flag-vs-tag, version-stamped names, no zero-size
  assets). CI runs it as a blocking step on tag builds.
- A cold tag-triggered matrix build takes **30–60 min**, but the local poll is
  ~10 min — **poll timeout means "still building", NOT failure.** Re-check with
  `make verify-release-completeness` rather than assuming a bad release.
- Repair path for an already-published release:
  `make release-upload-assets TAG=... FILES='...'` and
  `make release-set-prerelease TAG=...`. Only ever upload **CI-built artifacts
  from the tagged SHA** — never locally-built binaries, which would falsify
  provenance.

## Opencode plugins

All 13 plugins in `.opencode/plugin/` are registered and active in `opencode.json`. They enforce the same policies as the `.claude/hooks/*.sh` layer:

- **`enforce-make.ts`** — Bash make-only policy: blocks non-make commands, metacharacters (`|`, `&&`, `;`, `$()`), concurrent gates, `.gate-status` writes, and edits that weaken guardrails across all hook/plugin files.
- **`enforce-floor.ts`** — agent floor/ceiling bands via `agent_liveness.py`: keeps ≥10 (env: `CLAUDE_AGENT_FLOOR`) live subagents; blocks stops when below the floor.
- **`enforce-delegate.ts`** — sonnet-dominant dispatch ratio (model utilization), worktree disk guards, opt-in force-delegate grind guard, and main-thread delegation budget.
- **`enforce-stop.ts`** — deferral-pattern block (no-wait), open-backlog block, session-start orchestration injection, and `AskUserQuestion` deny (no blocking questions).

## Key documents

- `AGENTS.md` — full agent policy (TDD, completion, guardrail integrity). Binding here too.
- `docs/RELEASE_RUNBOOK.md` — how to cut a release and how to know it actually
  shipped. **Read before touching any release target.**
- `docs/design/NEXT_RELEASE_BETA2_SPEC.md` — the next release's plan (waves 0-5).
- `docs/design/STUB_CLOSURE_SPEC.md` — S1-S26: verified stubs and dead wiring.
- `docs/design/RELEASE_INTEGRITY_AND_ARTIFACT_COMPLETENESS.md` — the beta.1
  incident record + release-integrity requirements.
- `docs/STABILIZATION_PLAN.md` — CURRENT work plan. Supersedes the GLM_REMEDIATION_GUIDE_* status claims below.
- `GLM_REMEDIATION_GUIDE_3.md` — round-3 plan (historical; 2026-06-12 validation, adjudicates guide-2 checklist, ratchet burn-down, product spine, ship blockers). Superseded by STABILIZATION_PLAN.md.
- `GLM_REMEDIATION_GUIDE_2.md` — round-2 plan (historical; its checklist is re-adjudicated in guide 3 Section 1).
- `GLM_REMEDIATION_GUIDE.md` — round-1 remediation plan (historical; its TASKS.md ticks are re-adjudicated in guide 2 Section 1).
- `GLM_IMPLEMENTATION_GUIDE.md` — original gap analysis and task specs.
