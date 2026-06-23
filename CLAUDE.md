# CLAUDE.md — read before your first tool call

## Bash policy: make targets ONLY

Every Bash command in this repo MUST be `make <target>`. Anything else (`ls`, `git`, `find`, `tail`, `python`, `uv`, pipes, `&&`, `;`) is denied by permission rules (`opencode.json`, mirrored in this harness) and by `.opencode/plugin/enforce-make.ts`.

- Listing/status: `make git-status`, `make git-log`, `make git-diff`
- Tests: `make test`, `make test-unit TESTFILE='...'`, `make test-count` (collection check), `make test-e2e`
- Quality: `make lint`, `make typecheck`, `make qa` (lint+type+test+healthcheck), `make validate`
- Commit: `make git-add FILES='...'` then `make git-commit MSG='...'`; gated commit: `make test-and-commit`
- Branch: `make feature-start MSG='feature/x'`, `make feature-done MSG='feature/x'`
- Need something new? Add a Makefile target, then run it. Never bypass.

File reads/edits use the Read/Edit/Write tools, not shell.

## Known traps

- `make test-failures` historically masked collection ERRORs ("No failures" on a broken suite). Trust full `make test` output until GLM_REMEDIATION_GUIDE.md Phase R1.1 lands.
- Do NOT trust `SESSION.md` status claims; verify with gates. See `BUGS.md` for the incident history.
- Run `make test-count` before any commit — collection errors mean no commit.

## Opencode plugins

All 4 plugins in `.opencode/plugin/` are registered and active in `opencode.json`. They enforce the same policies as the `.claude/hooks/*.sh` layer:

- **`enforce-make.ts`** — Bash make-only policy: blocks non-make commands, metacharacters (`|`, `&&`, `;`, `$()`), concurrent gates, `.gate-status` writes, and edits that weaken guardrails across all hook/plugin files.
- **`enforce-floor.ts`** — agent floor/ceiling bands via `agent_liveness.py`: keeps ≥10 (env: `CLAUDE_AGENT_FLOOR`) live subagents; blocks stops when below the floor.
- **`enforce-delegate.ts`** — sonnet-dominant dispatch ratio (model utilization), worktree disk guards, opt-in force-delegate grind guard, and main-thread delegation budget.
- **`enforce-stop.ts`** — deferral-pattern block (no-wait), open-backlog block, session-start orchestration injection, and `AskUserQuestion` deny (no blocking questions).

## Key documents

- `AGENTS.md` — full agent policy (TDD, completion, guardrail integrity). Binding here too.
- `GLM_REMEDIATION_GUIDE_3.md` — CURRENT work plan (2026-06-12 third validation: adjudicates guide-2 checklist, ratchet burn-down, product spine, ship blockers). Supersedes guide-2 status claims.
- `GLM_REMEDIATION_GUIDE_2.md` — round-2 plan (historical; its checklist is re-adjudicated in guide 3 Section 1).
- `GLM_REMEDIATION_GUIDE.md` — round-1 remediation plan (historical; its TASKS.md ticks are re-adjudicated in guide 2 Section 1).
- `GLM_IMPLEMENTATION_GUIDE.md` — original gap analysis and task specs.
