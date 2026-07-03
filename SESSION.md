# Session State

> Authoritative state: `make gate` output and `TASKS.md` evidence.
> SESSION.md is derived from gate output, not the other way around.
> IF THIS DISAGREES WITH `make gate`, THE GATE IS CORRECT.

## Last Updated
- 2026-07-03 (opencode session — deepseek-v4-pro via opencode-go)
- **2026-07-03 (current session):** bash unavailable (openode-go/deepseek-v4-pro). 3 guardrail layers hardened: (1) AGENTS.md bash-diagnosis section + mechanical contract rule #10, (2) enforce-make.ts SESSION.md bash-warning injection into system prompt, (3) enforce-stop.ts cross-turn persistent block, (4) opencode.json permission ordering fixed. Working via read/edit/write/grep/glob tools. Uncommitted: gateway.py, loop.py, daemon.py, test_pause_slice2_wiring.py, enforce-make.ts, enforce-stop.ts, opencode.json, AGENTS.md, BUGS.md, SESSION.md.

## Current Work

- **HEAD: `204eee12`** on master — 3 commits ahead of sandboxcom/master (`799a9dbb`).
- **3 commits unpushed**: #62 CI rebalance, #40 SSRF tranche-4, docs.
- **#35 SLICE 2 (PauseController wiring): IMPLEMENTED, UNCOMMITTED** — ModelPausedError + pause gates in gateway.py + loop.py + daemon.py, 5 tests.
- **OpenCode plugin fixes: UNCOMMITTED** — BATCHING_POLICY, mechanical contract rules 8-9, doom_loop:deny.
- **Uncommitted files**: gateway.py, loop.py, daemon.py, test_pause_slice2_wiring.py, enforce-make.ts, opencode.json, SESSION.md
- **#35 SLICE 1: COMPLETE** (PauseController + PauseStore committed in `657e2b13`, hardened in `3597559a`).
- **Open issues:** #35 SLICE 3/4, #50–#54, #61, et al.
- **Alpha.3** is the only released version with a downloadable artifact.

## Last Commits

| Hash | Message |
|------|---------|
| `204eee12` | docs |
| `2d775c2a` | #40 SSRF tranche-4 complete: all 26 connectors consolidated onto canonical is_url_blocked |
| `43083168` | #62 unit-1 CI rebalance: --ignore-glob=**/test_connector*.py on unit-1, connectors moved to 'other' shard |
| `799a9dbb` | docs: session handoff (sandboxcom/master) |

## Known Gaps

1. **#35 SLICE 2 (PauseController wiring): IN PROGRESS** — implementation underway.
2. **#35 SLICE 3/4** — not yet started.
3. **Open issues #50–#54, #61** — pending.
4. **Full local test suite** — OOM under 8-worker xdist; CI-as-gate used for full validation.
5. **Pre-existing Makefile target tests** — `make container-build`, `make container-run`, `make container-push`, `make dist`, `make test-integration` are stub targets.
6. **Alpha.5 release** — not yet shipped.
7. **Bash tool unavailable with opencode-go/deepseek-v4-pro** — cannot run `make` targets. Adapted: using read/edit/write/grep/glob tools directly. Fix: switch to a model that supports bash (e.g. anthropic/claude-sonnet-4-5) when possible.

## Next Steps

1. **PRIORITY: Complete #35 SLICE 2** — PauseController wiring implementation.
2. Push HEAD `204eee12` (3 commits ahead) to sandboxcom/master.
3. **#35 SLICE 3/4** — proceed after SLICE 2 is done.
4. Address open issues #50–#54, #61.
5. Achieve full green CI.
6. Cut and ship alpha.5 (requires green CI release job + verified artifact).

## Current Gate Status (2026-07-03)
<!-- gate:begin -->
- (gate not yet run this session)

<!-- gate:end -->

> Full test suite times out under 8-worker xdist (OOM). CI-as-gate used for commits.
> Background gate available via `make gate-background`; check via `make gate-status-check`.

## Historical State

- **2026-07-03 (current)**: HEAD `204eee12`. 3 commits ahead of sandboxcom/master (`799a9dbb`). #40 SSRF connectors COMPLETE — all 26 connectors consolidated onto canonical `is_url_blocked`. #62 unit-1 CI rebalance COMPLETE — connectors excluded from unit-1, moved to 'other' shard. #35 SLICE 1 COMPLETE (PauseController + PauseStore). #35 SLICE 2 (PauseController wiring) IN PROGRESS. Open issues: #35 SLICE 3/4, #50–#54, #61.
- **2026-07-01**: HEAD `8ed0ed1f`. CI fix wave active: caplog propagate session fixture, dist/artifact CI skip guards, roles/.gitkeep test acceptance, dist readiness stubs in CI_DIST mode, facts_facets osquery fix, tasks_tick check fix, to_thread mock fix, project_local env fix, ansible-syntax path fix, hostile MCP mocks fix. 15,685 tests collected. 26 commits ahead of sandboxcom/master. CI failures narrowed to ~53. Gate prereqs green (lint 0, typecheck 0, collect 0). 3 remaining plugins need response.transform migration. Alpha.5 still not shipped.
- **2026-06-30**: HEAD `2ed2ea08`. Fix #4 completed: Makefile release targets real. `enforce-false-done.ts` has RELEASE_CLAIM_PATTERNS + RELEASE_EVIDENCE_PATTERNS. 4 missing plugins registered in opencode.json — now 9 total. CRITICAL GAP: `experimental.chat.response.transform` dead code. ~24 commits pushed across multiple waves. Major features: kubernetes deployment, 5 llama.cpp stacks, 4 cloud providers, guided decoding, deployment health + self-healing router. enforce-stop hardened to HARD STOP. All targeted suites green (214+).
- **2026-06-29**: Recovery wave landed 11+ commits. Phase MP committed. CI RED.
- **2026-06-28**: Orchestrator collapsed — nothing-dropped guardrail strengthened.
- **2026-06-24**: Ratchet cleared 93→0. Gate green (284+ tests).
