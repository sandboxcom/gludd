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
