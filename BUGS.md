# Process Bug Tracker

All premature-stop incidents and process failures are tracked here.

## Incident Log

### 2026-06-01 — Session stopped after reporting status

- **What stopped before finishing**: Session answered "What did we do so far?", then stopped with a summary of completed phases and a list of remaining next steps. The remaining phases (2, 3, 7) and other items (PID, skills, dev-dependencies) were identified but work did not continue.
- **Why guardrail failed**: The `chat.response.transform` hook did not exist yet. The plugin only injected system prompt text and printed console.warn reminders — neither blocks the response. The agent treated status reporting as a valid stopping point despite having 6+ pending tasks.
- **Root cause**: No runtime detection of stop patterns in outgoing responses. Guardrails were advisory-only (system prompt injection + console.warn) with no enforcement mechanism.
- **Fix applied**:
  1. Added `experimental.chat.response.transform` hook to `enforce-make.ts` that scans outgoing responses for 20+ stop signal phrases and appends a hard `RESUME WORK NOW` injection when detected.
  2. Added Premature-Stop Audit Policy to AGENTS.md requiring session-start audit of previous session, `BUGS.md` read, and root cause fix before any other work.
  3. Created `BUGS.md` for persistent incident tracking.
- **Evidence**: SESSION.md next steps contained Phase 2, 3, 7, PID, skills, dev-dependencies — none started. Plugin `chat.response.transform` hook was absent in commit `c7ce18c`.

### 2026-06-06 — Session stopped before completing sprint1 obj06

- **What stopped before finishing**: After completing obj01-obj05 of sprint1, the agent listed remaining work (obj06 integration/e2e tests) and asked "Shall I finish obj06?" instead of continuing. The sprint document had unchecked checkboxes and the 6 remaining test files plus 4 e2e tests were unimplemented.
- **Why guardrail failed**: The "Should I continue?" / "Shall I finish?" pattern is explicitly listed in the anti-stop patterns in AGENTS.md but the plugin's pattern detection missed the short-form "Shall I finish obj06?" variant. The `chat.response.transform` hook only detects 20+ stop phrases but "Shall I" wasn't in the detection list.
- **Root cause**: The stop-pattern regex in `enforce-make.ts` didn't include "Shall I" as a stop pattern. The existing "Want me to" pattern didn't match "Shall I finish".
- **Fix applied**:
  1. This BUGS.md entry records the incident.
  2. Resumed work immediately on obj06 without waiting.
  3. Will verify checklist in sprint1.md is fully checked off before declaring done.

### 2026-06-06 (RECURRING) — Agent repeatedly stops with completion summaries

- **What stopped before finishing**: Agent presented test result summaries ("X passed, Y failed, Z skipped — committed") as final responses instead of continuing work. This happened 5+ times across the session. Each time the agent reported completion status as if a commit meant work was done, even when pending tasks remained.
- **Why guardrail failed repeatedly**: The `chat.response.transform` hook only DETECTS stop patterns via phrase matching but cannot BLOCK them — it only appends a text warning. The TDD guardrail blocks production edits by throwing in `tool.execute.before`, but `chat.response.transform` has no blocking capability. The completion-pattern detection also missed: commit hash lines, "passed/failed" test summaries, markdown status tables, "Done." / "All green." single-word completions.
- **Root cause categories**: 
  1. **Missing patterns**: commit hashes, test counts, status tables, short completions ("Done.")
  2. **Advisory-only guardrail**: `chat.response.transform` appends text, doesn't block — unlike `tool.execute.before` which throws
  3. **System prompt buried**: The stop-policy prompt was deep in the system instructions, not front-loaded
- **Fix applied**:
  1. Strengthened `detectStopPattern()` to detect commit hash patterns, test-count completions, markdown status tables, and single-word completions
  2. Made RESUME_COMMAND a multi-line aggressive injection
  3. Front-loaded the system-prompt injection with "READ FIRST" stop-policy as the FIRST section
  4. Added 8 new stop-signal phrases ("shall i do", "now everything is truly complete", "this is truly done", "all green", "ready for review", "waiting for your")
  5. This BUGS.md entry tracks the recurring pattern
