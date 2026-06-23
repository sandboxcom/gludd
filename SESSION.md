# Session State

> Authoritative state: `make gate` output and `TASKS.md` evidence.
> SESSION.md is derived from gate output, not the other way around.
> IF THIS DISAGREES WITH `make gate`, THE GATE IS CORRECT.

## Last Updated
- 2026-06-23

## Current State — Gate fixes + CI pipeline repair

### Completed this session (uncommitted in working tree):
- Ported all 20 Claude shell hooks to 4 opencode TypeScript plugins
- Fixed commit-no-verify gate bypass (Makefile _gate-fresh-check)
- Flipped NO_WAIT_ENFORCE to blocking default + ratchet-based stop-audit
- Codified 10-subagent floor (CLAUDE_AGENT_FLOOR=10 across all layers)
- CI fixes: FK constraint test fix, get_running_loop, seed_initial_queues TOCTOU, coverage gate shard fix
- Fixed: conftest get_event_loop deprecation, enforce-delegate disk advisory bug, enforce-stop stale messages, enforce-make duplicate operands, greedy regexes bounded
- Restored deleted dist/ files, cleaned up ratchet (18→14), bumped RATCHET_MAX
- Added subagent dispatch reliability rules to AGENTS.md

### Blockers:
- Full gate needs to run (~40min) to get fresh green .gate-status for commit
- CI was red on commit 06af9cc — fixes are in working tree but uncommitted

## Wave 3 Ship — Status: GATE RUNNING, NOT YET CONFIRMED

Tip commit: `6063e51` (built on `bd4cddb` + 9 fixes). Branch is FF-safe off master (`3223c67`). A clean-worktree gate is currently RUNNING to verify. Do NOT treat this as merged or green until the gate result lands.

Wave 3 contents (committed, unverified green):
- Integration batch 3: convergence fixes + feature packages + connector/registry layer
- Observability connector layer batches 1+2: 16+ connector groups, normalize + model-deploy check
- Security hardening: MCP hardening, webmcp dogfood, fs-write policy, conflict scanner, CI regression guards
- 4 features: saturation controller (#42), feature-db dogfood (#29), capability_policy default-DENY (#44), execution-engine fixes (#48)
- 220+ tests added

## Fast-Follow Branches (awaiting post-ship gated-merge)

| Branch | Tip SHA | Contents | Gate State |
|---|---|---|---|
| feature/batch3-security | 85158c2 | F5b/F6a/F6b security features, 14/14 tests passing, ancestor-clean | Gate-clean, pending merge |
| batch-4-security | building | D-04/05/06/29/30/31 security items | Building |
| mt-6-watchdog | building | Watchdog/stall detection improvements | Building |
| floor_controller-consolidated | building | Gate-safe + predictive floor controller | Building |

Full cascade plan: `docs/integration/POSTSHIP_MERGE_CASCADE_2026-06-18.md`

## Hooks — Current State

Hooks hardened and enforcing as of 2026-06-18:
- All hooks emit empty-or-valid-JSON + exit 0 (non-zero = hook error, not a block)
- Block decisions use `{"decision":"block"}` (Stop) or `{"permissionDecision":"deny"}` (PreToolUse)
- `make test-hooks` added: 20+ cases covering all hooks across all input paths
- 3 memory→guardrail hooks being wired: `guardrail_integrity_edit_pretool.sh` (prevent disable-as-fix), plus agent floor/ceiling improvements

## Current Gate Status
## Current Gate Status (2026-06-18)
<!-- gate:begin -->
- lint PASS 0
- typecheck FAIL 2
- collect PASS 0
- test FAIL non-zero-exit

<!-- gate:end -->

## Ratchet Burn-Down Progress
- Started: 93 entries (2026-06-11)
- After session 4 (30d66a3): 23 entries — 17 strict + 6 flaky
- Current: ~12 entries (2 strict + 10 flaky) — last verified 2026-06-16 against config/ratchet.yml
- **Total burned**: ~81 entries (93 → ~12, ~87% reduction)

## Known Gaps / Next Steps

1. **Ship gate result pending** — wave 3 gate must complete and pass before merge.
2. **F5a auth fail-open** — needs explicit user go/no-go before wiring. NOT proceeding without it.
3. **D-backlog (D-07..D-47)** — catalogued in `docs/audit/NEW_FINDINGS`; not yet scheduled.
4. **Backlog JSON mt-6/mt-7 SHAs** — need to be repointed to real builder commits once branches land.
5. **Ratchet strict entries** (~2 remaining) — daemon lifespan real DB, container-runtime tests.
6. Work plan: `GLM_REMEDIATION_GUIDE_3.md` (2026-06-12 validation pass, still current).

## Historical Gate Status (2026-06-12, pre-wave-3)
- lint PASS 0
- typecheck PASS 0
- collect PASS 0
- test PASS (gate ALL PASSED at 65fc28b)
