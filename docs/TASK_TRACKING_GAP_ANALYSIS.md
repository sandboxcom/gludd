# Task Tracking Gap Analysis

**Date:** 2026-07-25 | **Session:** 51 | **Branch:** development

## Purpose

The user asked: "is your programming ensuring that you are updating your tasks
with each of my prompts and your findings or do you need to write code to
strengthen your opencode behavioral guardrails?"

This document audits the enforcement plugin system for task-tracking gaps and
recommends fixes.

---

## 1. What IS Enforced

### 1.1 Session-Start TASKS.md Read (`enforce-session-start.ts`, ~Lines 80-108, 240-264)

- Forces reading TASKS.md, BUGS.md, config/ratchet.yml, SESSION.md at
  session start via system.transform directive (STEP 1) and tool.execute.before
  gate (blocks non-read mutations until task files are read).
- Tracks `_lastTasksReadMtime` for staleness detection; injects "TASKS.md has
  NOT been read recently" nag after 5+ minutes (TASKS_STALE_MINUTES).

### 1.2 Pending-Work Detection from TASKS.md

- **`enforce-multitask.ts` `hasPendingWork()`** (lines 79-135): checks for
  unchecked `[ ]` checkboxes in TASKS.md. Used to gate floor enforcement
  (only blocks when real work exists).
- **`enforce-floor.ts` `openWorkExists()`** (lines 63-142): checks TASKS.md
  unchecked items via regex `[-*] [ ]`. Also checks ratchet.yml, BUGS.md,
  .gate-status, CI state, and todowrite state.
- **`enforce-stop.ts` impl `hasRealPendingWork()`** (line 550+): reads
  TASKS.md UNCONDITIONALLY on every invocation (no caching). Returns
  `tasksMdUnchecked` + count. Used to block text-only responses.

### 1.3 Done-Claim Evidence Enforcement (`enforce-verified-claims.ts`)

- Blocks text containing done-words (landed, committed, pushed, fixed, etc.)
  without machine-produced evidence (commit hash, test counts, gate output).
  16 DONE_WORDS, 10 EVIDENCE_PATTERNS.

### 1.4 Premature Stop Detection (`enforce-stop.ts`)

- Detects completion-smell patterns, QA response summaries, stop-pattern
  phrases. Blocks text-only responses when pending work exists.

### 1.5 Dispatch Command Building from TASKS.md (`enforce-floor.ts`, lines 145-181)

- `_buildDispatchCommands()` reads TASKS.md unchecked items and formats them
  as suggested dispatch commands for the floor-breach block message.

### 1.6 Todowrite State Checking

- Both `enforce-multitask.ts:124-129` and `enforce-floor.ts:75-85` read
  `/tmp/gludd-todowrite-state.json` to detect pending/in_progress items.

### 1.7 Task Ledger Validation (scripts, not plugins)

- `scripts/validate_task_ledger.py` — mechanically verifies dispatched tasks
  have corresponding TASKS.md entries.
- `scripts/check_dispatch_dedup.py` — prevents re-dispatching completed tasks.

---

## 2. What Is NOT Enforced (Gaps)

### Gap 1: No Plugin Forces Adding User Prompts as TASKS.md Entries

**Severity:** HIGH | **Current state:** Unenforced

When a user says "fix X" or "add Y", nothing mechanically forces the agent to
add that request as a TASKS.md entry. The agent relies on memory (AGENTS.md
instruction) and the `todowrite` system, but `todowrite` items are ALSO not
cross-referenced against TASKS.md. A user prompt can be answered and worked on
without ever creating a TASKS.md entry.

**Root cause:** No plugin hook fires on user message receipt. All current
hooks are `tool.execute.before` (agent-side) or `text.complete` (agent
response-side). There is no `user.message.received` hook surface.

### Gap 2: No Cross-Referencing of User Prompts Against TASKS.md Entries

**Severity:** HIGH | **Current state:** Unenforced

When the user asks for N things, no plugin verifies that N TASKS.md entries
exist. The agent may track 3 of 5 requests and the other 2 are silently lost.
There is no mechanical "you received 5 asks but only logged 3" check.

### Gap 3: No Detection of Stale TASKS.md Items (Committed but Not Ticked)

**Severity:** MEDIUM | **Current state:** Unenforced

When code is committed that fixes a TASKS.md item, no plugin detects that the
checkbox remains unticked. The `enforce-stop.ts` plugin will correctly see the
unchecked box and prevent a premature stop, but it won't explicitly flag:
"Commit abc123 appears to fix TASKS.md item X, but the checkbox is still
unchecked."

### Gap 4: No Tracking of "Last User Prompt Timestamp" vs "Last TASKS.md Update"

**Severity:** HIGH | **Current state:** Unenforced

The core gap the user identified. No plugin maintains a state file tracking:
- `last_user_prompt_ts` — timestamp of the most recent user message
- `last_tasks_md_update_ts` — mtime of TASKS.md

Without this comparison, nothing detects: "the user sent a message 5 minutes
ago, but TASKS.md hasn't been modified since then."

### Gap 5: No Verification That TASKS.md Was Modified Since Last User Prompt

**Severity:** HIGH | **Current state:** Unenforced

Related to Gap 4 but distinct: even if timestamps are tracked, no hook checks
whether TASKS.md's content actually changed. A common bypass would be to
`touch TASKS.md` without adding new items.

### Gap 6: No Detection of Unanswered/Untracked User Prompts

**Severity:** MEDIUM | **Current state:** Unenforced

If the user says "implement feature Z" and the agent never dispatches work on
it and never creates a TASKS.md entry, the prompt is silently dropped. Current
plugins don't detect this because they only check "is there ANY pending work?"
not "was the SPECIFIC thing the user asked for tracked?"

---

## 3. Plugin Coverage Matrix

| Concern | enforce-stop | enforce-session-start | enforce-multitask | enforce-floor | enforce-verified-claims | Proposed: enforce-task-tracking |
|---|---|---|---|---|---|---|
| Read TASKS.md at startup | - | YES | - | - | - | - |
| Detect unchecked items | YES | - | YES | YES | - | - |
| Block done-words without evidence | - | - | - | - | YES | - |
| Block premature stops | YES | - | - | - | - | - |
| Add user prompts to TASKS.md | - | - | - | - | - | NEEDED |
| Cross-ref prompts to entries | - | - | - | - | - | NEEDED |
| Detect stale ticked items | - | - | - | - | - | NEEDED |
| Track prompt-vs-TASKS mtime | - | - | - | - | - | NEEDED |
| Verify TASKS.md modified since prompt | - | - | - | - | - | NEEDED |
| Detect unanswered prompts | - | - | - | - | - | NEEDED |

---

## 4. Recommended Fixes (Priority Order)

### P0: `enforce-task-tracking.ts` — prompt-to-TASKS.md update enforcement

**Hook:** `text.complete` — before sending a response, compare
`last_user_prompt_ts` to `TASKS.md` mtime. If the user sent a non-trivial
message (not "ok", "yes", "continue") and TASKS.md hasn't been modified
since, inject an advisory warning.

**State file:** `/tmp/gludd-task-tracking.json`
- `last_user_prompt_ts: number` — epoch ms of last user message
- `last_tasks_md_mtime: number` — mtime of TASKS.md at last check
- `missed_update_count: number` — consecutive misses
- `last_prompt_text_hash: string` — hash of last user prompt for dedup

**Behavior:**
- Advisory only (does not block). Escalates to warning after 3 missed updates.
- On first miss: "Note: TASKS.md may need updating for the new user request."
- On third consecutive miss: "WARNING: 3 user prompts without TASKS.md update."

**Disable:** `GLUDD_TASK_TRACKING_ENFORCE=0`

### P1: TASKS.md Write Verification at Commit Time

Extend the existing `scripts/check_dispatch_dedup.py` or create a new
`scripts/check_tasks_currency.py` that runs as a pre-commit hook:
- Checks TASKS.md mtime vs the last user prompt timestamp
- Warns if TASKS.md is stale relative to user activity
- Optional blocking mode for release branches

### P2: Cross-Reference User Prompts to TASKS.md

A background script (invoked by watchdog) that:
- Reads the opencode conversation DB for recent user messages
- Greps TASKS.md for corresponding entries
- Reports unmatched user requests as a nag

### P3: Stale TASKS.md Item Detection

A script that:
- Compares git log commit messages to TASKS.md unchecked items
- Flags items whose description matches a recent commit message but remain
  unchecked (suggesting they were fixed but not ticked)

---

## 5. Current Enforcement Architecture (for reference)

```text
User message → [NO HOOK SURFACE AVAILABLE]
               ↓
Agent reads TASKS.md (enforce-session-start gate)
               ↓
Agent dispatches subagents (enforce-multitask floor, enforce-floor streak)
               ↓
Subagents return → Agent updates TASKS.md (HOPED FOR, NOT ENFORCED)
               ↓                          ↑
               ↓                          │ GAP: nothing verifies this step
               ↓                          │
Agent sends response → enforce-stop checks pending work
                     → enforce-verified-claims checks evidence
                     → enforce-multitask checks wave size
```

The critical gap is between "Agent receives subagent results" and "Agent ticks
TASKS.md." Nothing mechanically bridges this gap — the agent MUST remember to
update TASKS.md, and the existing checks only catch the ABSENCE of an update
(missing checkbox → premature stop blocked) rather than enforcing the UPDATE
itself.

---

## 6. Related AGENTS.md Sections

- **Task Self-Tracking (Anti-Forgetting)** — specifies TASKS.md update workflow
  but is advisory only (no mechanical enforcement of the update step).
- **Completion = Green Gate + TASKS.md Evidence** — defines what constitutes
  completion but doesn't enforce the update-to-TASKS.md step.
- **Verification Before Claim** — enforces evidence on claims but doesn't
  enforce TASKS.md currency.

---

## 7. Test Coverage

- `tests/unit/test_task_tracking_guardrails.py` — structural tests pinning
  this analysis (gap inventory, plugin reference inventory, spec existence).
- Existing: `tests/unit/test_session_start_protocol.py`,
  `tests/unit/test_multitask_plugin.py`, `tests/unit/test_verified_claims_plugin.py`,
  `tests/unit/test_stop_pattern_qa.py`.
- Proposed: `tests/unit/test_task_tracking_plugin.py` (for the new
  enforce-task-tracking.ts plugin — behavioral tests).
