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

- `make test-failures` historically masked collection ERRORs ("No failures" on a broken suite). Trust full `make test` output until GLM_REMEDIATION_GUIDE.md Phase R1.1 lands.
- Do NOT trust `SESSION.md` status claims; verify with gates. See `BUGS.md` for the incident history.
- **Never write done/shipped/fixed/working/landed/✅ without pasting the measurement** (test count, CI run id, commit hash, `gh release view` artifact). Enforced by `.claude/hooks/no_false_completion_stop.sh`; see `AGENTS.md § "'Done' Claims Require Observable Verification Evidence"`.
- Run `make test-count` before any commit — collection errors mean no commit.

## Opencode plugins

All 13 plugins in `.opencode/plugin/` are registered and active in `opencode.json`. They enforce the same policies as the `.claude/hooks/*.sh` layer:

- **`enforce-make.ts`** — Bash make-only policy: blocks non-make commands, metacharacters (`|`, `&&`, `;`, `$()`), concurrent gates, `.gate-status` writes, and edits that weaken guardrails across all hook/plugin files.
- **`enforce-floor.ts`** — agent floor/ceiling bands via `agent_liveness.py`: keeps ≥10 (env: `CLAUDE_AGENT_FLOOR`) live subagents; blocks stops when below the floor.
- **`enforce-delegate.ts`** — sonnet-dominant dispatch ratio (model utilization), worktree disk guards, opt-in force-delegate grind guard, and main-thread delegation budget.
- **`enforce-stop.ts`** — deferral-pattern block (no-wait), open-backlog block, session-start orchestration injection, and `AskUserQuestion` deny (no blocking questions).

## Key documents

- `AGENTS.md` — full agent policy (TDD, completion, guardrail integrity). Binding here too.
- `docs/STABILIZATION_PLAN.md` — CURRENT work plan. Supersedes the GLM_REMEDIATION_GUIDE_* status claims below.
- `GLM_REMEDIATION_GUIDE_3.md` — round-3 plan (historical; 2026-06-12 validation, adjudicates guide-2 checklist, ratchet burn-down, product spine, ship blockers). Superseded by STABILIZATION_PLAN.md.
- `GLM_REMEDIATION_GUIDE_2.md` — round-2 plan (historical; its checklist is re-adjudicated in guide 3 Section 1).
- `GLM_REMEDIATION_GUIDE.md` — round-1 remediation plan (historical; its TASKS.md ticks are re-adjudicated in guide 2 Section 1).
- `GLM_IMPLEMENTATION_GUIDE.md` — original gap analysis and task specs.
