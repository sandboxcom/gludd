# Feature: NF.10 — enforce-stop.ts False-Completion Fix

**Status: CONFIRMED-COMPLETE** | **Created: 2026-07-16** | **Verified: 2026-08-02** | **Target: v0.1.0-beta.2** | **Type: enforcement fix**

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

## 8. Verified-Complete Evidence (2026-08-02)

All five fix vectors confirmed present, active, and matching the spec in
`.opencode/plugin/impl/enforce_stop_impl.ts` (1749 lines):

| Fix vector | Location | Verified |
|---|---|---|
| `hasRealPendingWork()` — unconditional, uncached | `enforce_stop_impl.ts:566` | Reads TASKS.md on every invocation (`:582-592`); no cache; also checks ratchet.yml (`:594-602`), BUGS.md (`:604-609`), .gate-status (`:612-632`), CI verdict via watchdog cache (`:634-657`), release completeness file (`:659-668`), test failures (`:670-678`), repo pending work (`:679-683`), multitasking backlog (`:684-699`). `todowrite` is NEVER consulted. |
| text.complete block — ALL text-only responses blocked | `enforce_stop_impl.ts:1097` | `isTextOnly` gate (`turnState.toolCallMade === false && dispatchCount === 0`) → blocks ALL text-only when `hasRealPendingWork()` returns true. Short-text exemption REMOVED. |
| `COMPLETION_SMELL_RE` | `enforce_stop_impl.ts:198` | 20+ completion-adjacent substrings (`complete`, `done`, `finished`, `ready`, `landed`, `shipped`, etc.) — matched against response text; fires even for short text when pending work exists. |
| `STATUS_SUMMARY_RE` + `looksLikeStatusSummary()` | `enforce_stop_impl.ts:149-173` | Blocks structural status summaries (bolded headers + status tables, >500 char + `##` headers). NOT bypassed by disengage. |
| Disengage narrowed (2026-07-15) | `enforce_stop_impl.ts:1081-1086` | `isDisengaged()` only skips heuristic checks (`COMPLETION_SMELL`, `COMPLETION_WORDS`, `QA_RESPONSE_PATTERNS`); the fundamental `hasRealPendingWork()` text-only block is NEVER bypassed by disengage. |

### Runtime verification

```
make test-hook-runtime → 52 functional tests across 8 plugins (PASS)
make test TESTFILE=tests/unit/test_verified_claims_plugin.py → 23 tests (PASS)
make test TESTFILE=tests/unit/test_stop_pattern_qa.py → structural pin (PASS)
```

### Vendor spec close

`SPEC_NF10_STOP_FALSE_COMPLETION` closed with verified evidence:
`enforce_stop_impl.ts:566-699` (hasRealPendingWork 8-source detection),
`enforce_stop_impl.ts:198` (COMPLETION_SMELL_RE),
`enforce_stop_impl.ts:149-173` (STATUS_SUMMARY_RE),
`enforce_stop_impl.ts:1081-1086` (disengage narrowed).

## 9. Cross-project state isolation (2026-08-13)

A full-gate replay exposed a second failure mode: a failed
`/tmp/gludd-gate-lite-test.log` from one checkout made a clean checkout's
`hasRealPendingWork()` latch true. The same risk existed for the global push
state. This was both a correctness defect and a resource-namespace violation.

The repaired contract is:

- Gate-lite truth comes only from the current project root's structured
  `.gate-lite-status`; diagnostic log text is never an authority signal.
- `gateLiteTestFailed` is derived from that structured status's `test FAIL`
  phase, preserving fail-closed behavior for the current checkout.
- Push state is read through `GLUDD_PUSH_STATE_FILE`, with the historical
  `/tmp/gludd-push-state.json` retained only as the default.
- Hook runtime fixtures redirect push state into each test's `tmp_path`.
- Foreign project failures cannot clear or weaken a local failure: current-root
  gate status and the selected push-state file still block immediately.

This is a zero-downtime change. It changes only state selection at hook
invocation, starts no process, opens no listener, and requires no data
migration. Rollback is the prior plugin commit; promotion requires the
cross-project runtime regressions plus the complete hook-runtime suite.

### Practitioner evidence

The failure class has long-lived precedent in pytest's own user reports:

- [pytest #5524](https://github.com/pytest-dev/pytest/issues/5524) reported
  concurrent runs racing while creating a shared base temporary directory in
  2019. The durable lesson is to give each run a distinct state root.
- [pytest-xdist #280](https://github.com/pytest-dev/pytest-xdist/issues/280)
  reported workers colliding when a shared resource assignment lacked worker
  identity in 2018; xdist's worker identifier was the practical isolation
  boundary.
- [pytest #11789](https://github.com/pytest-dev/pytest/issues/11789) documented
  that retained temporary directories surprised practitioners and linked the
  concurrent-same-test limitation in #11790. Diagnostic files must therefore
  not be treated as fresh authoritative state merely because they still exist.

### Regression evidence

Failing first:

```text
test_foreign_gate_lite_log_does_not_latch_clean_project FAILED
test_push_state_override_isolates_foreign_project_block FAILED
```

After the repair:

```text
2 focused cross-project regressions passed
76 affected enforcement runtime tests passed
```

Because `.opencode/plugin/impl/enforce_stop_impl.ts` changed, OpenCode must be
restarted before a live session uses the repaired hook implementation.
