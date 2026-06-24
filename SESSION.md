# Session State

> Authoritative state: `make gate` output and `TASKS.md` evidence.
> SESSION.md is derived from gate output, not the other way around.
> IF THIS DISAGREES WITH `make gate`, THE GATE IS CORRECT.

## Last Updated
- 2026-06-23

## Current State — master@`41befa8`

### MERGED:
- `fix/self-update-sec` → master (all previous work landed)
- master advanced to `41befa8` (CI pending — treat as unconfirmed until GREEN)

### Phase 2 — COMPLETE (all 7 steps done):
- Self-update flow wired end-to-end
- All seven Phase 2 steps finished

### EventLoop Wiring (new since 4c06268):
- Collection handler + role dispatch wired into EventLoop
- LOC ledger wired — `accounting/ledger.py` `loc_changed` now invoked from dispatch path
- Spend limiter `would_exceed` wired into dispatch path (gates spend before each dispatch)

### A-05 Overload Retry Cap — FIXED:
- Retry behavior on overload now bounded (previously unbounded)

### Multitasking Bugs — see authoritative source:
- Authoritative root-cause analysis: `docs/audit/floor_breach_rootcause_2026-06-17.md`
- BUGS.md contains no enumerated list of 5 bugs; the "5 documented" count is not grounded there
- The 3 restart-bound bugs live in `.opencode/plugin/*.ts` (plugin code is loaded on opencode restart, not hot-reloaded)

### InfraTracker:
- Implemented and wired into the daemon/event loop

### Pricing (15/15 sources COMPLETE):
- LIVE sources registered: RunPod, Z.AI, AWS, + 12 more (15/15 returning real data)
- **HuggingFace:** static table implemented (10 GPU instances)
- **Fireworks:** FireworksSource removed — LiteLLM JSON source covers it (no remaining gap)
- **Endpoints:** `/api/pricing` and `/api/pricing/compute` added
- **Connector CLI:** `list`, `health`, `query` commands
- **Spend limiter:** wired to PricingCatalog
- All sources complete; no pending sources

### Dead Code (corrected 2026-06-23):
- `orchestration/` — `.py` files are DELETED; only `__pycache__` remains (not "unwired", gone)
- `pricing_intel/` — FULLY WIRED (daemon.py:1057, 12+ importers, 2 live HTTP endpoints `/api/pricing` and `/api/pricing/compute`). Not dead.

### Connectors:
- `UserConfig.connectors` field added — 82 modules unblocked

### CI Watch:
- `ci-verdict-fast` + `ci-verdict-loop` make targets added
- Polling intervals lowered
- Gate passing, shards running

### Tests:
- 12,627 tests collected

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

## Current Gate Status (2026-06-23)
<!-- gate:begin -->
From `.gate-status` (2026-06-24T00:25:28Z):
- lint PASS 0
- typecheck PASS 0
- collect PASS 0
- test INCOMPLETE (gate interrupted at test phase; no pass/fail recorded)

<!-- gate:end -->

## Ratchet Burn-Down Progress
- Started: 93 entries (2026-06-11)
- After session 4 (30d66a3): 23 entries — 17 strict + 6 flaky
- Current: 14 entries — last verified 2026-06-23 against config/ratchet.yml
- **Total burned**: ~79 entries (93 → 14, ~85% reduction)

## Known Gaps / Next Steps

1. **Master CI pending** — master@`41befa8` CI run must complete and pass; treat as unconfirmed until GREEN.
2. **Multitasking bugs** — root-cause analysis in `docs/audit/floor_breach_rootcause_2026-06-17.md`; restart-bound bugs live in `.opencode/plugin/*.ts` (clear on opencode restart).
3. **F5a auth fail-open** — needs explicit user go/no-go before wiring. NOT proceeding without it.
4. **D-backlog (D-07..D-47)** — catalogued in `docs/audit/NEW_FINDINGS`; not yet scheduled.
5. **Backlog JSON mt-6/mt-7 SHAs** — need to be repointed to real builder commits once branches land.
6. **Ratchet** — 14 entries remaining; continue burn-down after master CI confirms green.
7. Work plan: `GLM_REMEDIATION_GUIDE_3.md` (2026-06-12 validation pass, still current).

## Historical Gate Status (2026-06-12, pre-wave-3)
- lint PASS 0
- typecheck PASS 0
- collect PASS 0
- test PASS (gate ALL PASSED at 65fc28b)
