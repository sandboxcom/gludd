# Process Bug Tracker

All premature-stop incidents and process failures are tracked here.

## Incident Log

### 2026-06-08 — Agent presented audit gap table and asked "Shall I start working through these?" instead of doing the work

- **What stopped before finishing**: After running comprehensive conversation DB audit, agent found 11 genuine gaps. Presented a markdown table of gaps and asked "Shall I start working through these?" — a textbook permission-asking stop with 7 pending todo items.
- **Why guardrail failed**: "shall i start" was not in `STOP_SIGNAL_WORDS` (only "shall i do" was). The heuristic detectors didn't catch "gap table + question mark" as a stop pattern. The agent treated presenting findings as a valid stopping point.
- **Root cause**: Missing stop-signal words ("shall i start/begin/work/implement/fix") and missing heuristic for "gap findings table + question = asking permission to do work you should just do."
- **Fix applied**:
  1. Added 5 new "shall i" variants to STOP_SIGNAL_WORDS: start, begin, work, implement, fix
  2. Added 3 new heuristic checks: gapFindingsCount >= 3 + question mark, summaryTable + question mark, bulletListCount >= 5 + question mark
  3. Added anti-pattern to AGENTS.md: "Presenting audit findings/gap table and asking 'Shall I start working?'"
  4. This BUGS.md entry.

**Pattern**: Agent treats "presenting findings" as a deliverable. Findings are not deliverables. Fixes are deliverables.

### 2026-06-07 — Agent shipped CLI project management without TUI project management (INTERFACE PARITY FAILURE)

- **What stopped before finishing**: User asked "how do i add repos or locations to be worked on?" Agent implemented `gludd project add/list/remove` CLI commands, `dispatch_mode` on ProjectWeight, config YAML seeding, and watchdog event dispatcher — then committed and stopped. The TUI (`_cmd_tui`) was not updated. User had to ask again.
- **Why guardrail failed**: The `completion_audit` only checks whether source classes are imported/wired — it doesn't check for feature parity across interfaces (CLI vs TUI). There is no automated check that a feature added to one interface must also be added to others (TUI, daemon API, playbooks, config).
- **Root cause**: Missing "cross-interface completeness" check. When a feature is added to one interface (CLI), there should be a guardrail that prompts the agent to check whether it also belongs in the TUI, daemon endpoints, ansible playbooks, and config files.
- **Fix applied**:
  1. This incident logged.
  2. Added TUI project management (add/list/remove with dispatch_mode) — see next commit.
  3. Added cross-interface completeness check to AGENTS.md guardrails.
  4. The agent must now audit: "If I added this to CLI, does it belong in TUI? If to daemon, does it need a CLI command? If to config, does it need a daemon endpoint?"

**Pattern**: Agent treats "feature done" as "feature done in one interface" rather than "feature done in ALL interfaces where it applies."



- **What stopped before finishing**: After committing guardrail fixes, agent sent text explaining "The guardrails failed because chat.response.transform only prepended..." — an analysis report instead of continuing to work on the pending project isolation wiring tasks. The todowrite had 7 pending items.
- **Why guardrail failed**: The stop-pattern detection list didn't include phrases like "Fixed:", "continuing with", "now continuing", "the answer is", "to summarize", etc. The `chat.response.transform` replacement worked for pure completion reports but not for analysis/explanation patterns that end a response without a tool call.
- **Root cause**: stop-pattern detection was trained on explicit completion signals ("all done", "ready for review") but missed indirect stop indicators like analysis reports, summaries, and "Fixed:" patterns that end a message without continuing work.
- **Fix applied**:
  1. Expanded STOP_SIGNAL_WORDS with 10+ new patterns: "Fixed:", "continuing with", "now continuing", "to summarize", "in summary", "recap:", "the answer is", pass count patterns, "committed ."
  2. `chat.response.transform` now COMPLETELY REPLACES the response (not prepend) on detection
  3. This BUGS.md entry records the 4th recurring premature-stop incident

- **What stopped before finishing**: After commit `f010c5e` (completion audit tool), agent sent a text-only status summary instead of immediately continuing to wire the 32 dead-code gaps found by the audit. The commit was treated as a stopping point despite massive pending work.
- **Why guardrail failed**: The `chat.response.transform` hook detects stop patterns but ONLY PREPENDS text — it cannot block the response. The `make test-and-commit` target had no mechanism to check for pending work. Both the plugin and the preflight gate only look at lint/mypy/coverage — none check whether the agent has remaining tasks.
- **Root cause**: Guardrails are passive (warn, prepend) not active (block, throw). No layer checks whether work remains before allowing a commit.
- **Fix applied**:
  1. Added `PENDING_WORK_CHECK` to `tool.execute.before` hook — blocks `make test-and-commit` with a hard error, forcing the agent to continue.
  2. Added "work_remaining" check to preflight gate (`make preflight`).
  3. Updated AGENTS.md with stronger language about commit-as-stop-point.

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

### 2026-06-07 (SESSION START AUDIT) — Guardrail hardening for recurring premature stops

- **Root cause analysis**: 6 incidents in BUGS.md. All share the same pattern: agent generates text-only response when todowrite has pending/in_progress items. The `chat.response.transform` hook can replace detected stop patterns but CANNOT throw/block like `tool.execute.before` can. The system prompt injection was buried after other sections rather than being the first thing the model reads.
- **Why fixes kept failing**: 
  1. Pattern detection was too narrow — missed test results, coverage lines, commit+summary combos
  2. System prompt had the audit rule but not as the FIRST/HIGHEST PRIORITY section
  3. No heuristic for "long response with test results + bullet lists" = status report
- **Fix applied (this session)**:
  1. Expanded STOP_SIGNAL_WORDS from ~40 to ~60+ patterns, adding: test result phrases, coverage phrases, "what did we do", "summary of changes", "wrapping up", "finishing up", etc.
  2. Rewrote `detectStopPattern()` with 6 new heuristic checks: commit+passed (lowered threshold), test+coverage lines, commit+table, test+bullet-list, coverage+last-line-summary
  3. Added `stopAuditOverride` as the VERY FIRST section in `system.transform` — triple-stop-sign emoji, "HIGHEST PRIORITY", references BUGS.md incident count
  4. This BUGS.md entry for audit trail
