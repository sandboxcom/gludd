# spec.md — End-to-End Examples

Concrete walkthroughs showing the agent's expected behavior at each stage.
The example task is `auto-save-by-sound` (auto-save a recording clip when a loud sound is detected).

---

## Example 1 — Initialization

**User:** `Initialize spec.md`

**Agent actions:**

1. Detects `.spec.md/` is missing.
2. Creates scaffold:
   ```
   .spec.md/
   ├── AGENTS.md
   ├── memory.md
   └── specs/{drafts,active,archive}/
   ```
3. Scans repo: `package.json`, `tsconfig.json`, `eslint.config.*`, `__tests__/`, etc.
4. Writes `.spec.md/AGENTS.md` with detected stack:

   ```markdown
   # AGENTS.md

   ## Tech Stack

   - React Native (Expo SDK 53), TypeScript
   - Nitro Modules, react-native-vision-camera
   - ffmpeg-kit-react-native for media processing

   ## Coding Standards

   - ESLint + Prettier (config in `eslint.config.mjs`)
   - Path aliases via `tsconfig.json#paths`

   ## Testing

   - Jest with `@testing-library/react-native`
   - Test files in `__tests__/` mirroring `src/`

   ## Personas

   - Mobile RN Engineer (default)
   - Media/FFmpeg specialist (for buffer/encoding tasks)
   ```

5. Presents `AGENTS.md` to user and waits for approval.

---

## Example 2 — Draft Stage

**User:** `Create task auto-save-by-sound`

**Agent actions:**

1. **Context Loading** (in order): reads open files, `AGENTS.md`, `memory.md`, scans `src/lib/BufferManager.ts` and `src/screens/RecordingScreen.tsx`.
2. Normalizes the name to kebab-case (`auto-save-by-sound`) and confirms no folder with that name exists in `drafts/`, `active/`, or `archive/`.
3. Creates `.spec.md/specs/drafts/auto-save-by-sound/` and generates the three files using the templates. Excerpts:

**`APPROACH.md`** (excerpt):

```markdown
## Execution Plan

1. Step 1 — Add `AudioLevelMonitor` module
   - Files: `src/lib/AudioLevelMonitor.ts`
   - Tests added: AC-1, AC-2
   - Done when: unit tests pass and module exposes `onPeak(threshold, cb)`.
2. Step 2 — Wire into RecordingScreen
   - Files: `src/screens/RecordingScreen.tsx`
   - Done when: peak above threshold triggers `BufferManager.exportClip()`.
3. Step 3 — Final validation (lint, typecheck, manual smoke on device).
```

**`BUSINESS_CONTEXT.md`** (excerpt):

```markdown
## Acceptance Criteria

- AC-1: Given the recording is active, when audio peak ≥ -6 dBFS for ≥ 200 ms,
  then a 15s clip ending at the peak is exported.
- AC-2: Given two peaks within 5s, when the second occurs, then a single clip
  covering both peaks is exported (no double-save).
```

**`COMPLETION_REPORT.md`**: initialized with `Status: [PENDING]`.

4. Asks: _"A-B-C drafted in `auto-save-by-sound/`. Review the files and respond with `Approve task` to implement."_

---

## Example 2b — Discard Draft (abandoned spec)

**User:** `Discard task` (or `Discard task` with a reason: scope moved to another project)

**Agent actions:**

1. Moves folder: `.spec.md/specs/drafts/auto-save-by-sound/` → `.spec.md/specs/archive/` (skips `active/`).
2. Updates `COMPLETION_REPORT.md`: `Status: [DISCARDED]`, discard date, optional reason.
3. Appends to `.spec.md/memory.md`:
   ```markdown
   [2026-05-28] auto-save-by-sound: [discarded] Product pivot — loud-peak auto-save deferred. Ref: specs/archive/auto-save-by-sound
   ```
4. Confirms: _"Draft auto-save-by-sound discarded and archived. No implementation was started."_

**When to use:** the plan is no longer valid, duplicated, or explicitly shelved. Do **not** use `"Complete task"` (that is only for tasks that reached `active/`).

---

## Example 3 — Approval (Stage Transition + immediate execution)

**User:** `Approve task` (often after reviewing the draft or applying feedback to A-B-C)

**Agent actions (single turn — do not stop after the announce):**

1. `git mv .spec.md/specs/drafts/auto-save-by-sound .spec.md/specs/active/` (or equivalent file move).
2. Sets `COMPLETION_REPORT.md` → `Status: [IN PROGRESS]`.
3. Announces briefly: _"Plan approved. Entering active execution. Execution Plan is now immutable."_
4. Posts the progress checklist:
   ```
   spec.md Progress auto-save-by-sound:
   - [ ] Tests written from BUSINESS_CONTEXT acceptance criteria
   - [ ] APPROACH step 1: Add AudioLevelMonitor module
   - [ ] APPROACH step 2: Wire into RecordingScreen
   - [ ] APPROACH step 3: Final validation
   - [ ] All tests pass locally
   - [ ] Lint/typecheck clean
   - [ ] COMPLETION_REPORT.md updated
   - [ ] Review Gate: user approved (`Complete task`) or iteration complete
   ```
5. **In the same message**, starts Step 1 by writing the test file first (TDD) and continues execution per §4 until Review Gate or a true blocker.

---

## Example 4 — Active Execution (single step)

**Step 1 in progress:**

1. Writes `__tests__/AudioLevelMonitor.test.ts` covering AC-1 and AC-2 (tests fail — red).
2. Implements `src/lib/AudioLevelMonitor.ts` (tests pass — green).
3. Updates the checklist:
   ```
   - [x] Tests written from BUSINESS_CONTEXT acceptance criteria
   - [x] APPROACH step 1: Add AudioLevelMonitor module
   - [ ] APPROACH step 2: Wire into RecordingScreen
   ...
   ```
4. Appends to `COMPLETION_REPORT.md`:

   ```markdown
   ### Step 1 — Add AudioLevelMonitor module

   - Files touched: `src/lib/AudioLevelMonitor.ts`, `__tests__/AudioLevelMonitor.test.ts`
   - Tests added: AC-1, AC-2 (both passing)
   - Decisions: used a 50 ms ring-buffer over `Audio.recordingStatusListener` to compute peak dBFS.
   - Outcome: ✅ done
   ```

---

## Example 5 — Deviation Mid-Flight

While executing Step 2, the agent discovers `BufferManager.exportClip()` doesn't accept an `endTimestamp` parameter (was assumed in APPROACH).

**Correct behavior:**

1. **Stop** execution immediately.
2. Update `APPROACH.md`:

   ```markdown
   ## Deviations (added 2026-05-11)

   - Step 2 requires extending `BufferManager.exportClip(opts)` to accept
     `endTimestamp`. Adding sub-step 2a.
   ```

3. Ask user: _"APPROACH needs an update (see diff). Respond with `Approve task` to re-lock and resume execution."_
4. On `"Approve task"`: re-lock the contract and **continue §4 in the same turn** (do not only acknowledge).
5. **Do not** silently change scope.

---

## Example 6b — Review Gate (direct approval)

**Context:** execution checklist is 100% complete.

**Agent actions:**

1. Sets `COMPLETION_REPORT.md` → `Status: [IN REVIEW]`.
2. Appends Review submission under `## Review Gate` with AC summary and `User decision: pending`.
3. Announces Review Gate and posts review package:
   ```
   Execução concluída. Review Gate aberto.
   - AC-1 ✅ — __tests__/AudioLevelMonitor.test.ts
   - AC-2 ✅ — __tests__/RecordingScreen.test.ts
   - Deviations: none
   - Files: src/lib/AudioLevelMonitor.ts, src/screens/RecordingScreen.tsx, …
   Responda com Complete task para arquivar, ou descreva ajustes.
   ```
4. **Does not** suggest or run `Complete task` on its own.

**User:** `Complete task`

**Agent:** proceeds to Example 6 (archive).

---

## Example 6c — Review Gate (iteration)

**User (at Review Gate):** _"O threshold de -6 dBFS está agressivo; use -12 e adicione teste."_

**Agent actions:**

1. Appends to `APPROACH.md`:

   ```markdown
   ## Review Rounds

   ### Round 1 — 2026-05-31

   **Feedback:** Threshold too aggressive; change to -12 dBFS with test coverage.
   **Delta steps:**

   1. **Step R1.1 — Lower peak threshold to -12 dBFS**
      - Files: `src/lib/AudioLevelMonitor.ts`, `__tests__/AudioLevelMonitor.test.ts`
      - Done when: tests pass with -12 default.
   ```

2. Implements R1.1 (TDD).
3. Logs in `COMPLETION_REPORT.md` → `### Round 1 — 2026-05-31` with outcome ✅.
4. Re-opens Review Gate with updated package (_"Round 1 concluída"_).

**User:** `Complete task`

**Agent:** proceeds to Example 6 (archive).

---

## Example 6 — Completion

**Prerequisite:** task is in `active/` with `Status: [IN REVIEW]` and user said `Complete task`.

**User:** `Complete task`

**Agent actions:**

1. Sets `COMPLETION_REPORT.md` → `Status: [DONE]`; Review Gate `User decision: approved`.
2. Moves folder: `.spec.md/specs/active/auto-save-by-sound/` → `.spec.md/specs/archive/`.
3. Finalizes test evidence and AC checklist in `COMPLETION_REPORT.md`.
4. Appends to `.spec.md/memory.md`:

   ```markdown
   [2026-05-11] auto-save-by-sound: Auto-save clip on loud audio peak. Ref: specs/archive/auto-save-by-sound

   ## Lessons

   - `Audio.recordingStatusListener` fires at ~10 Hz on Android; for peak detection
     prefer a native module if sub-100ms latency is required.
   ```

5. Confirms to user: _"Task auto-save-by-sound archived and indexed in memory.md."_

---

## Anti-Examples (what NOT to do)

❌ **Skipping Draft:** agent jumps to editing `src/` after `"Create task"`.
✅ Correct: only A-B-C files are created until `"Approve task"`.

❌ **Approve without executing:** agent moves to `active/` and says the spec is “ready for implementation” but writes no tests/code in that turn.
✅ Correct: same turn as `"Approve task"` — checklist + `[IN PROGRESS]` + start TDD (Example 3).

❌ **Silent pivot:** agent changes implementation strategy without updating APPROACH.
✅ Correct: pause, update APPROACH, ask for re-approval.

❌ **Reading `archive/` on a hunch:** wastes context.
✅ Correct: only read `archive/` when a reference in `memory.md` points there.

❌ **Heavy docs on small task:** 3-page APPROACH for a 1-file change.
✅ Correct: apply the Task Sizing table — Small tasks get bullet-list APPROACH.

❌ **Deleting a draft folder:** loses audit trail of what was considered and why it stopped.
✅ Correct: `"Discard task"` → `archive/` + `[discarded]` line in `memory.md`.

❌ **Complete task on a draft:** `Complete task` only applies to `active/` tasks.
✅ Correct: use `"Discard task"` for drafts you will not implement.

❌ **Skipping Review Gate:** agent runs `Complete task` right after the last APPROACH step.
✅ Correct: set `[IN REVIEW]`, present review package, wait for user `Complete task` or feedback.

❌ **Complete task while `[IN PROGRESS]`:** user tries to archive before execution finishes.
✅ Correct: refuse — _"Implementação ainda em andamento. Complete o checklist primeiro."_

❌ **Editing Execution Plan at Review Gate:** agent rewrites original steps for post-impl feedback.
✅ Correct: append delta steps under `## Review Rounds` only.
