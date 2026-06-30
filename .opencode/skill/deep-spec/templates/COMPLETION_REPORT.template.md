# [C] COMPLETION REPORT — [name]

> **The Evidence.** Updated continuously during Active execution.
> Initialize as `[PENDING]`. Set `[IN REVIEW]` when entering Review Gate. Finalize on `"Complete task"`. Set `[DISCARDED]` on `"Discard task"` (draft only).

**Status:** `[PENDING]` | `[IN PROGRESS]` | `[IN REVIEW]` | `[DONE]` | `[DISCARDED]`
**Started:** YYYY-MM-DD
**Completed:** YYYY-MM-DD

## Discarded

Fill this section only when the draft is archived via `"Discard task"` (never implemented).

- **Discarded:** YYYY-MM-DD
- **Discard reason:** …

## Execution Log

Append an entry after each APPROACH step (active execution only).

### Step 1 — <title>

- **Files touched:** `…`
- **Tests added:** `AC-1`, `AC-2`
- **Decisions:** …
- **Snippets / commands:**
  ```
  …
  ```
- **Outcome:** ✅ done | ⚠️ partial | ❌ blocked (see Deviations)

### Step 2 — <title>

- …

## Review Gate

### Review submission — YYYY-MM-DD

- **Submitted by:** agent (auto on checklist complete)
- **AC summary:** AC-1 ✅, AC-2 ✅, …
- **User decision:** pending | approved (`Complete task`) | changes requested (Round N)

### Round N — YYYY-MM-DD

- **Feedback:** …
- **Delta steps executed:** R1.1, R1.2
- **Outcome:** ✅ done | ⚠️ partial

## Test Evidence

- Unit tests: `<count> passing` (file: `__tests__/...`)
- Integration/E2E: `<count> passing`
- Coverage delta (if tracked): …
- Manual verification: …

## Deviations from APPROACH

If anything diverged from the approved plan, record it here with rationale.
Any non-trivial deviation should have triggered a re-approval (see Operating Rules).

- **Deviation 1:** what changed and why
- **Deviation 2:** …

## Acceptance Criteria Status

- [x] AC-1 — verified by `<test file>`
- [x] AC-2 — verified by `<test file>`
- [ ] AC-3 — deferred (see Follow-ups)

## Performance / Non-Functional Checks

- Lint: ✅ / ❌
- Typecheck: ✅ / ❌
- Bundle/size impact: …
- Runtime perf observations: …

## Lessons Learned

Concise notes worth promoting to `memory.md` (gotchas, surprises, reusable patterns).

- …

## Follow-ups

Anything deliberately deferred. Each item should become a new task ID if pursued.

- [ ] …
