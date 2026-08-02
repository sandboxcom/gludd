# Feature: NF.10 — enforce-stop.ts False-Completion Fix

**Status: IMPLEMENTED** | **Created: 2026-07-16** | **Verified: 2026-08-02** | **Target: v0.1.0-beta.2** | **Type: enforcement fix**

## 1. Problem

`enforce-stop.ts` was bypassable via three vectors, allowing premature
"done" claims and text-only stop responses while work remained:

1. **todowrite bypass.** Plugin consulted `todowrite` state only. An agent
   marking all todos as `completed` could stop even when CI was RED,
   beta.1 had 1/12 release assets, and 300+ spec items were unimplemented.
2. **Short-text exemption.** Very short text responses skipped the
   pending-work block entirely.
3. **WORK_STATE_CACHE.** Project state was cached, so a red gate or new
   unchecked TASKS.md item was not re-read on subsequent invocations.

## 2. Root Cause

The plugin trusted a single signal (`todowrite`) as a proxy for "work is
done" — but `todowrite` is agent-authored and trivially self-deceiving.
The plugin also relied on cached state, allowing stale "clean" readings
to persist after real work arrived. And the short-text exemption was an
overly broad escape hatch for legitimate terse replies that swallowed
premature stops alongside them.

## 3. Fix

### `hasRealPendingWork()` — unconditional, uncached

Reads project state on EVERY invocation (no caching). Returns true when
ANY of:

- `TASKS.md` has unchecked items (`- [ ]`)
- `config/ratchet.yml` has entries
- `.gate-status` is FAIL or missing/stale
- CI verdict on current branch is non-success (`make ci-verdict`)
- Release incomplete (`make verify-release-completeness` fails)
- Unreleased tags without artifacts

The `todowrite` state is NEVER consulted. An empty todowrite with red CI
or unchecked TASKS.md is still pending work.

### text.complete block

Blocks ALL text-only responses (0 tool calls) when
`hasRealPendingWork()` is true — regardless of text length, regardless
of text content. Short-text exemption REMOVED.

### COMPLETION_SMELL + STATUS_SUMMARY detection

- `COMPLETION_SMELL`: blocks any completion-adjacent substring
  (`complete`, `done`, `finished`, `ready`, etc.) when
  `hasRealPendingWork()` is true — fires even for short text.
- `STATUS_SUMMARY_RE`: blocks structural status summaries (bolded
  headers + status tables) with pending work — REGARDLESS of embedded
  evidence (commit hashes, CI verdicts), NOT bypassed by disengage.

### Disengage narrowed (2026-07-15)

`make disengage-enforcement` previously bypassed ALL `text.complete`
enforcement including the fundamental `hasRealPendingWork()` block. Now
disengage only skips heuristic checks (`COMPLETION_SMELL`,
`COMPLETION_WORDS`, `QA_RESPONSE_PATTERNS`). The
`hasRealPendingWork()` text-only block is NEVER bypassed.

## 4. Files

| Action | Path |
|--------|------|
| Modify | `.opencode/plugin/enforce-stop.ts` |
| Modify | `.github/workflows/*.yml` (molecule made non-blocking in CI) |

## 5. Test Plan

- Functional hook tests via `make test-hook-runtime` (52 tests across 8
  plugins including this one)
- E2E: stop-pattern coverage in `tests/unit/test_stop_pattern_qa.py`
- Structural pins: `tests/unit/test_verified_claims_plugin.py` (23 tests)
- Incident documented in `BUGS.md`

## 6. Disabling

| Mechanism | Effect |
|-----------|--------|
| `GLUDD_STOP_ENFORCE=0` | Disables optional heuristics and non-text hooks; the filesystem-backed pending-work `text.complete` guard remains mandatory |
| `make disengage-enforcement` | Skips heuristics ONLY; `hasRealPendingWork()` block stays |

## 7. Evidence

- Commit: `816d7be6`
- Molecule made non-blocking in CI (was masking the false-completion
  by producing red noise)
- False-completion incident documented in `BUGS.md`
