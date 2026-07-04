# Session State

> Authoritative state: `make gate` output and `TASKS.md` evidence.
> SESSION.md is derived from gate output, not the other way around.
> IF THIS DISAGREES WITH `make gate`, THE GATE IS CORRECT.

## Last Updated
- 2026-07-04 (opencode session — deepseek-v4-pro, session 4)

## Current Work

- **HEAD: `387ef3ba`** on master — feat(G6): wire A/B testing variant selector + fix AGENTS.md stale refs.

- **8 commits this session (session 4)**: enforce-stop rewrite, watchdog CI-awareness, push-rate guard, batch-push, escape-sequence fix, G4/G10/G11 wiring, G6 A/B wiring, AGENTS.md stale-ref fixes.

- **G1-G13 scaffold/wire**: All 4 dead classes now wired (G4 SandboxExecutor, G6 A/B variant selector, G10 RunRecorder, G11 ConsensusEngine). 39 new tests across the 4 wiring phases.

- **Known Gaps**: Full local test suite OOM under xdist (CI-as-gate used). 4 unpushed commits on master.

- **Gate**: lint 0, typecheck 0, collect 0. Full test suite OOM under 8-worker xdist; CI-as-gate used.

## Last Commits (this session — session 4)

| Hash | Message |
|------|---------|
| `387ef3ba` | feat(G6): wire A/B testing variant selector + fix AGENTS.md stale refs — 18 tests |
| `680bfeef` | feat(G4,G10,G11): wire SandboxExecutor, RunRecorder, ConsensusEngine — 21 tests |
| `53fe65af` | fix: escape invalid Python string escape sequences in gha_usage.py jq filter |
| `96714938` | fix: 3-layer push-rate-guard (CI-pending block, 30min cooldown, cancelled-run cap), batch-push for 5+ commits, AGENTS.md no-push-per-commit policy |
| `c69c0d72` | fix: bulletproof agent keep-working system (enforce-stop rewrite, watchdog CI loop detection, pre-push guard, ci-wait, 14 new tests, 75 passed) |
| `186783a2` | fix(enforce-stop): only block on LOCAL work, not CI-pending alone (hasLocalWork vs hasAnyWork distinction, 2 new interface fields) |
| `8a128c3f` | fix(watchdog): add CI-awareness to stop detection (ci-verdict polling, CI-pending=work, stalled-CI warning, 7 tests) |

## Prior Commits (session 3)

| Hash | Message |
|------|---------|
| `f9e02413` | docs: update SESSION.md with final session 3 state (7 commits, G6+G11 features, README bumps) |
| `d8d4a241` | docs: add G11a evidence entry to TASKS.md |
| `0ee32612` | docs: add G6a evidence entry to TASKS.md |
| `b4bae0c5` | feat(G6): add SHA-256 content-hash tracking with bounded history to PromptRegistry (5 tests) |
| `fadcf808` | docs: bump G4+G8 README percentages |
| `e21def86` | docs: add G14 evidence entry to TASKS.md |
| `76f72d75` | docs: bump G1-G13 README percentages, update SESSION.md |
| `575344e7` | evidence: verify G1-G13 scaffold/wire all landed (pre-session) |

## Known Gaps

1. **Full local test suite** — OOM under 8-worker xdist; CI-as-gate used.

## Next Steps

1. **Push commits** — 4 unpushed on master (remote at c69c0d72, local at 387ef3ba).
2. **Poll CI** until green after push.

## Current Gate Status (2026-07-04)
<!-- gate:begin -->
- **Last full PASS**: 2026-07-04 — lint 0, typecheck 0, collect 0. Full suite OOM under xdist.
- **HEAD**: `387ef3ba`
- **CI**: not yet verified for current HEAD

<!-- gate:end -->

> Full test suite times out under 8-worker xdist (OOM). CI-as-gate used for commits.
> Background gate available via `make gate-background`; check via `make gate-status-check`.

## Historical State

- **2026-07-04 session 4 (current)**: HEAD `387ef3ba`. 8 commits: watchdog CI-awareness (8a128c3f), enforce-stop local-work distinction (186783a2), keep-working system rewrite (c69c0d72), push-rate-guard + batch-push (96714938), escape-sequence fix (53fe65af), G4/G10/G11 wiring (680bfeef), G6 A/B wiring + AGENTS stale fixes (387ef3ba). All 4 SESSION.md gaps resolved (39 new tests). Lint/typecheck/collect green.
- **2026-07-04 session 3**: HEAD `0ee32612`. 5 commits: G1-G13 README percentages bumped (76f72d75), G14 evidence (e21def86), G4+G8 README corrections (fadcf808), G6 content-hash tracking (b4bae0c5), G6a evidence (0ee32612). Gate green.
- **2026-07-04 session 2**: HEAD `0117024f`. SESSION.md staleness fixed. G1 at 55%. G2/G3/G8/G11/G12 scaffolded. Watchdog fixes.
- **2026-07-04 session 1**: HEAD `fcdf9b92`. G1 persistent agent memory schema. G13 structured task spec.
- **2026-07-01**: HEAD `8ed0ed1f`. CI fix wave active. 15,685 tests collected.
- **2026-06-30**: HEAD `2ed2ea08`. Makefile release targets real.
- **2026-06-29**: Recovery wave landed 11+ commits.
- **2026-06-28**: Orchestrator collapsed — nothing-dropped guardrail strengthened.
- **2026-06-24**: Ratchet cleared 93→0. Gate green (284+ tests).
