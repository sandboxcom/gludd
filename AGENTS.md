# Agentic Harness - Agent Rules

## Mechanical Contract (READ FIRST — numbered priority)

1. **Only `make <target>`.** Never bare commands, no metacharacters (`|`, `;`, `&&`, `$()`).
2. **Pending todos ⇒ tool call.** If any item is `pending` or `in_progress`, your next output MUST be a tool call. Text-only responses with unfinished work are a hard violation. **BEFORE every text response: check `config/ratchet.yml` — if it has ANY entries, the project has known-unfixed work. Any response that is not a tool call while ratchet has entries is a premature stop.**
3. **"Done" requires: `make gate` green + `TASKS.md` evidence.** Nothing else counts. No self-assessment, no assertion from memory. Every item ticked must have a gate output pasted.
4. **TDD:** write a failing test FIRST, run it, THEN write code. `make test-count` must show 0 collection errors before every commit.
5. **When you find a gap:** fix it now, do not list it and ask. You own it. Fix it, test it, commit it, continue.
6. **Trust gate output, not SESSION.md.** SESSION.md claims have been false. Gate exit codes are the single source of truth.
7. **Read `TASKS.md` for current work.** Read `BUGS.md` before claiming anything is finished. Update both as you go.
8. **Use existing mature projects — never write custom code when a well-formed existing tool exists.** Before writing a secrets scanner, linter, formatter, type checker, test runner, git hook framework, build system, or security scanner, check if an established project (detect-secrets, gitleaks, trufflehog, ruff, mypy, pytest, pre-commit, etc.) exists. Writing custom infrastructure code that duplicates a mature OSS project is a bug. The only exception is application-specific business logic that has no standard library.
9. **No unseen events — an unobservable operation is a broken operation.** Any operation that runs longer than a few seconds (a gate, a test suite, a build, a poll loop, a backgrounded task, a daemon background job) MUST surface continuous progress: stream its output (`tee`), emit a per-phase marker, or print a periodic heartbeat. Never redirect a long-running operation solely to `/dev/null` or a buffered file with no live signal. If an event happens and no one can see it, it did not happen. Enforced by `tests/unit/test_observability_guardrails.py`; mirrored for agent behavior in [[gludd-observability-invariant]] memory.

## CRITICAL: Session Start Protocol (FIRST action of every session)

**The FIRST actions of every session, in strict order:**

1. **LOCATE work.** In ONE tool-call message, read `TASKS.md`, `BUGS.md`, `config/ratchet.yml`, `SESSION.md`, and run `make git-status` + `make git-log`. These 6 calls go in ONE message — never serial.
2. **FAN OUT.** Immediately dispatch a ≥10-wide subagent wave in ONE message on disjoint work units. Do NOT write any prose between session start and the first dispatch wave. No introductions, no status reports, no "here's what I'll do" — just tool calls.

The ONLY valid exceptions to step 2: (a) the user's first message is a direct factual question with a one-word/one-line answer; (b) the user explicitly says "don't multitask yet." In both cases, answer briefly and then dispatch.

A Q&A-style first response ("Sure! Let me look into that.") with no tool calls is a **policy violation** whenever a task backlog exists. Prose-first session starts are forbidden.

**Enforcement (3-layer guardrail):**
- **Prompt** — this section (proactive instruction).
- **Plugin** — `.opencode/plugin/enforce-session-start.ts` injects a `SESSION START PROTOCOL` banner at boot via `experimental.chat.system.transform` AND (via `tool.execute.before`) tracks per-session dispatch count in `/tmp/gludd-session-start.json`. Until `GLUDD_SESSION_START_MIN_DISPATCHES` (default 5) parallel task/agent dispatches have been made, every non-dispatch, non-read tool call gets a loud `console.warn`. Set `GLUDD_SESSION_START_ENFORCE=1` to elevate to a hard deny.
- **Test** — `tests/unit/test_session_start_protocol.py` pins the plugin shape (system.transform + tool.execute.before + state file + floor constant).

## Completion = Green Gate + TASKS.md Evidence

A task may be called complete ONLY when:
- `make gate` is fully green (lint 0, typecheck ≤ baseline, collect 0 errors, tests pass)
- `TASKS.md` has the item ticked with evidence (gate target + summary + commit hash)
- `make test-count` shows 0 collection errors

NOTE: `make test-failures` previously masked collection ERRORs by grepping only `^FAILED`. If any gate target output disagrees with `make test`, the FULL `make test` output is the truth, and fixing the gate target is your first task.

## CRITICAL: "Done" Claims Require Observable Verification Evidence

A feature, fix, commit, push, or release is "done" ONLY when the SAME message pastes
the MEASUREMENT that makes it observable. NEVER write done / landed / shipped /
deployed / released / resolved / fixed / working / complete / successful / ✅ unless
it is accompanied by a cited, machine-produced measurement. "I wrote the
code/workflow" is NOT done — authorship is not verification.

| Scope | Required measurement |
|---|---|
| Unit fix / refactor | Named passing test + `make test TESTFILE=...` pass count |
| Local gate | `make gate` (lint 0, typecheck ≤ baseline, collect 0, tests pass) + `.gate-status` PASS |
| Committed | Commit hash from `make git-log` + the gate evidence above |
| Pushed | `make verify-remote BRANCH=<b> SHA=<sha>` → `VERIFIED <branch>@<sha>` |
| CI-green | `make ci-verdict BRANCH=<b>` → `conclusion: success` + headSha == branch tip |
| Shipped / released | `make verify-release-artifact TAG=<t>` PASS + `gh release view` showing isDraft:false, assets ≥ 1, download URL(s) |

An unverified "done" is indistinguishable from a false claim — this project's history
(false alpha.3 ship, 12 confirmed-inert features, the reviewer silently failing, the
tool-call loop dead in prod, and a release pipeline reported "✅ Landed" while it was
uncommitted/unpushed/never-run) proves it. A cited-but-STALE measurement (CI headSha
!= branch tip; `.gate-status` older than the last edit) is ALSO a false claim.

ENFORCED IN CODE: `.claude/hooks/no_false_completion_stop.sh` (Stop hook) blocks a turn
that ends on a completion claim carrying no evidence token and no honest hedge
(`GLUDD_FALSE_DONE_ENFORCE=1`; proof: `make test-no-false-completion`). Mirrors
Mechanical Contract rule 3 and "A Release is an Artifact, Not a Tag".
Enforced in opencode by `.opencode/plugin/enforce-false-done.ts` (response.transform replacement + anti-wedge counter); mirrors `.claude/hooks/no_false_completion_stop.sh`.

## No Unseen Events (observability invariant)

**"If an event happens and no one can see it, it is not an event."** This was a
direct user mandate (2026-06-15) after a `make gate` ran silently for 16 minutes
(test output buffered to a temp file) and a CI poller slept without a heartbeat —
both looked hung when they were working. Unobservable ≠ acceptable.

Binding rules for any operation in this repo's tooling or daemon that runs longer
than a few seconds:

1. **Stream or heartbeat — never go dark.** Long output must `tee` to stdout; a
   multi-phase job must print a marker as each phase starts; a poll/wait loop must
   print a timestamped heartbeat every cycle. A bare `> /dev/null 2>&1` or
   `> file 2>&1` on a long operation is forbidden.
2. **Backgrounded ≠ invisible.** When work is moved to a background task, it must
   still emit progress to its output stream so the launcher can observe it. Do not
   launch a silent background task and report "it's running."
3. **Failures must surface their cause.** On failure, tail/print the captured log
   (see the gate `smoke` phase) — never swallow it.
4. **Daemon background work emits events.** Daemon-side background jobs (event
   loop ticks, A/B runs, scheduled tasks) must publish to the message queue /
   metrics / structured logs so they are observable via `/api/facts`, not silent.

Enforced for tooling by `tests/unit/test_observability_guardrails.py`. Agent
behavioral mirror: never go silent while the user is waiting — check in the
foreground and report real state rather than launching a silent task and waiting.

**CRITICAL: Always provide a visual status update.** Every response MUST produce
visible output that opencode promotes to the UI. If you go silent for more than
a few seconds without a tool call or status text, the user cannot tell whether
work is progressing or has stalled — and a stalled-looking session WILL be
interrupted. Specifically:

- Between tool calls, output a 1-line status of what you're doing.
- For long-running operations (gate, build, test suite), stream output via `tee`
  or dispatch to a subagent that reports back — never run a 40-minute operation
  silently in the foreground.
- If you are thinking/planning, say so in one line before the next tool call.
- If work is blocked, state the blocker and the workaround being attempted —
  do NOT present options and ask "which do you want?" (that is the stop-and-ask
  bug, blocked by `enforce-stop.ts` as of 2026-06-22).

A response with NO visible output is indistinguishable from a hung session. The
user will stop the work and ask "what are you working on?" — and that is YOUR
bug, not theirs.

---

## Rationale and history

The sections below are the full policy. The 7-rule contract above is the prioritized summary.

## CRITICAL: Pre-Response Stop Audit (READ BEFORE EVERY RESPONSE)

**Before sending ANY text response to the user, you MUST run this checklist:**

1. Check `todowrite` state. Are there items in `pending` or `in_progress`?
2. If yes → you MUST make a tool call, NOT send text. Your response must include at least one tool invocation that continues work.
3. The ONLY exception: ALL items are `completed` or `cancelled`.
4. If you catch yourself writing a completion summary, status report, or "done" message — STOP. Replace it with a tool call.

**This is a HARD block. Text-only responses while work remains are a policy violation.**

## CRITICAL: Session-Start Orchestration Contract

**The FIRST action of every session is finding your tasks and immediately multitasking on them. No prose before the first dispatch wave.**

This is the antidote to the recurring failure mode where the agent answers the first prompt with inline grinding — reading files serially, running `make` targets on the main thread (which block ALL subagent dispatch), and replying with status prose. That pattern leaves the subagent pool at 0 for the entire first turn.

### The two-step first action (MANDATORY)

**STEP 1 (FIRST tool-call message of the session): read the task backlog in parallel.**
In ONE message, dispatch these four reads concurrently:
- `TASKS.md` — current task ledger
- `BUGS.md` — premature-stop incidents + process failures
- `config/ratchet.yml` — known-unfixed work (if this file has ANY entries, the project has pending work)
- `SESSION.md` — last session's state, known gaps, next steps

**STEP 2 (SECOND tool-call message): identify pending work and dispatch a ≥10-wide subagent wave.**
From the backlog reads, enumerate the pending items and IMMEDIATELY dispatch ≥10 subagents in ONE message (per the Pipeline Orchestration Model and the 10-agent floor). The dispatch wave is the deliverable of turn 1 — not a status report, not a plan, not a Q&A recap.

### What is FORBIDDEN on turn 1

- Answering the user's first prompt with prose, then starting work on turn 2.
- Serial inline reads (`read TASKS.md` → wait → `read BUGS.md` → wait → ...).
- Running ANY `make` target on the main thread before the first dispatch wave (`make test-unit`, `make gate`, etc. all block subagent dispatch for their full duration).
- "Let me first check the state of the repo" followed by text — the state check IS the four parallel reads, and the response to it IS the dispatch wave.

### Enforcement (three layers)

1. **Prompt** — this section.
2. **Plugin** — `.opencode/plugin/enforce-session-start.ts` registers `experimental.chat.system.transform` to PREPEND a loud `🚨 SESSION-START DIRECTIVE` block as the FIRST section of the system prompt on every conversation. The directive names the four task-tracking files, requires parallel reads, and requires a ≥10-wide dispatch as the second action.
3. **Hard gate (default ON)** — `GLUDD_SESSION_START_ENFORCE=0` disables the `tool.execute.before` hook that DENIES Write/Edit/mutating Bash on turn 1 until at least one task-tracking file has been read. The gate is ON by default so prose-first relapses are blocked structurally; set `GLUDD_SESSION_START_ENFORCE=0` only for focused single-file work where the directive would wedge a legitimate Q&A turn.

### The exception

If the user's first message is a single specific question that does not imply continuation of prior work ("what does file X do?", "what is 2+2?"), answer it briefly and proceed. The contract binds when there is pending work in the backlog OR the user says "continue", "resume", "keep working", or equivalent. When in doubt, read the backlog — a 4-file parallel read is cheap; grinding inline for a turn is expensive.

## CRITICAL: Continuous Multitasking Enforcement (During-Run)

The session-start contract gets turn 1 right. **This section keeps the floor at 10 for the rest of the run.** A session that opens with a 10-wide dispatch wave and then collapses to serial main-thread grinding has the same aggregate failure mode as never dispatching at all — the pool drains to zero and the next 40 minutes of work runs single-threaded.

1. **The 10-agent floor is enforced AT ALL TIMES, not just at session start.** Whenever the live subagent count drops below 10, the next non-dispatch tool call (Write/Edit/mutating-Bash) is DENIED until a refill wave brings the count back up. Enforced by `.opencode/plugin/enforce-floor.ts` (`tool.execute.before` hook, default ON). Set `GLUDD_FLOOR_ENFORCE=0` for focused single-file work where the floor would wedge legitimate serial edits.
2. **The session-start gate is also default ON.** The first mutating tool call of a session is denied until at least one task-tracking file (`TASKS.md` / `BUGS.md` / `config/ratchet.yml` / `SESSION.md`) has been read. Set `GLUDD_SESSION_START_ENFORCE=0` to disable.
3. **Message-shape rule (hard).** Every assistant response containing tool calls MUST satisfy ONE of: (a) zero task/agent/workflow dispatches (pure read/edit/bash for serial hot-file work like `daemon.py` / `loop.py`); OR (b) FIVE OR MORE parallel task/agent/workflow dispatches in ONE message. A response with 1–4 dispatches is a policy violation when ≥3 known work items remain — batch wider, or add research filler to reach 5.
4. **Fill thin waves with read-only research.** When fewer than 5 edit tasks are queued, fill the remaining dispatch slots with read-only research / audit / review tasks. They never conflict and are always productive. Do not let the wave shrink to 1–4 just because the edit backlog is short.
5. **Main-thread grind is the anti-pattern.** Four or more main-thread tool calls in a row with no delegation triggers a budget warning from the plugin. Heed it by handing the next chunk of work to a subagent — do not continue grinding inline.
6. **Refill on every completion.** The moment a subagent result arrives, dispatch a replacement (or a research filler) so the count never lingers below 10. Do not wait for the rest of the batch to drain.

## CRITICAL: Instruction-Following Priority

**When the user gives a specific instruction that contradicts your current plan, you MUST follow the instruction IMMEDIATELY, before anything else.**

Examples of overriding instructions:
- "fix this bug FIRST before continuing" → fix the bug, do not continue other work
- "please address this message and continue" → address the message AND continue work
- "codify a process to do X" → codify X immediately, do not start other features
- "correct your code so that..." → fix your code NOW, not later

DO NOT do both simultaneously if the instruction says "first" or "before".
DO NOT start a new feature before fixing the thing the user just complained about.
DO NOT continue your own plan when the user redirects you.

Cop behavior patterns that trigger this (DO NOT DO THESE):
- "X passed, Y failed, Z skipped — committed" as final message
- "All done. Everything is complete." as final message
- A table/summary of completed work followed by no tool calls
- "Ready for review" or "Waiting for your feedback"
- Presenting audit findings/gap table and asking "Shall I start working?"
- Any markdown table listing gaps followed by a question mark
- Any response listing 3+ gaps/issues and ending with a question
- Any message ending in "Done." with pending todos

CORRECT: If asked for status, respond briefly (1-2 lines) then IMMEDIATELY make a tool call.
CORRECT: After committing, immediately start the next pending task.
CORRECT: Never send text without also continuing work via a tool call.

## CRITICAL: Premature-Stop Audit Policy

**At the start of EVERY session, before doing any other work, you MUST:**

1. **Read `BUGS.md`** at the project root. This file tracks all premature-stop incidents.
2. **Audit your own previous session** for premature stops by reading SESSION.md and
   cross-referencing the "Next Steps" section. If Next Steps contains items that existed
   before the last commit, you stopped prematurely.
3. **Fix the root cause guardrail** before continuing with any project work.
4. **Log the incident** in `BUGS.md` with: date, what you stopped before finishing,
   why the guardrail failed, and what you fixed.

**A premature stop is ANY session exit where:**
- Your todo list had items in `pending` or `in_progress` state.
- SESSION.md "Next Steps" lists work that was identified but not started.
- You reported status/progress instead of continuing work.
- You asked "should I continue?" or equivalent.
- You listed remaining work and stopped without completing it.

**Every premature stop is a BUG.** Bugs in your own process are no different from bugs
in code — they must be tracked, root-caused, and fixed before moving on.

**Root cause categories to check:**
- Missing or weak guardrail (plugin hook doesn't detect the stop pattern)
- Guardrail is advisory only (console.warn) not blocking (throw/inject)
- System prompt doesn't mention the specific stop pattern
- AGENTS.md doesn't codify the specific pattern as forbidden
- No mechanism to detect pending todos at session boundary

**This is enforced by:**
- This AGENTS.md section — proactive instruction to audit on session start
- `.opencode/plugin/enforce-make.ts` — `chat.response.transform` hook detects stop patterns
- `BUGS.md` — persistent bug tracking for process failures

## CRITICAL: Task Completion Policy

**You MUST complete ALL requested work before stopping. No exceptions.**

1. If given a sprint, objective list, or multi-step task, work through EVERY
   step until all are complete or genuinely blocked.
2. Do NOT stop early to report status. Do NOT pause to ask if the user wants
   you to continue when instructions were explicit.
3. Do NOT treat infrastructure/tooling setup as the deliverable. Guardrails,
   hooks, and make targets exist to support the real work.
4. Do NOT get sidetracked. If you catch yourself spending time on something
   that is not the requested work, refocus immediately.
5. After completing one objective, immediately start the next. No victory laps.
6. Only stop when ALL objectives are complete or you hit a hard blocker you
   cannot fix (missing credentials, environment you cannot change).

**Anti-Stop Patterns — EVERY ONE of these is a policy violation:**
- Listing remaining tasks and asking "Want me to proceed?" or "What priority?"
- Listing findings/gaps/audit results and asking "Want me to start building?"
- Answering a status question and then stopping instead of resuming work
- Saying "X is done. Next steps are A, B, C." and then stopping
- Asking "Should I continue?" when there are clearly pending tasks
- Presenting a plan or analysis and waiting for approval before implementing
- Saying "Here's what needs to be done" and then NOT doing it immediately
- Asking any question that is really "should I do my job?" in disguise
- **Q&A-style summary as terminal response** — framing the final message as a recap with bolded question headers ("**What changed?**", "**Why?**", "**What's left?**") is the same violation as a markdown status table. A recap with no tool call is a premature stop regardless of phrasing. If anything is uncommitted, unpushed, or stale (README version mismatch, TASKS.md missing rows, .secrets.baseline churn, remote tip behind local), the response MUST be a tool call, never prose — even prose that "answers the user's question."

**The ONLY valid response to identifying work that needs to be done is to DO IT.**
Never ask. Never wait. Just do the work. If the user wants you to stop,
THEY will tell you. Until then, keep working.

**"Low priority" does NOT mean "skip it."** If an item is in the todo list
with status `pending`, it MUST be done. Priority only determines ORDER, not
whether the work happens. The only valid terminal states are `completed` or
`cancelled`. A `pending` item is unfinished work, period.

**When asked for status:** Answer briefly, then RESUME WORK immediately.
Do not ask for permission. Do not wait for acknowledgment.

**Self-Directed Work Rule: When you identify a gap, bug, or missing
integration while working, you MUST fix it immediately. Do NOT stop to ask
the user whether to proceed. Do NOT list the gap and wait for approval.
If you found it, you own it. Fix it, test it, commit it, then continue
with the original task. The only exception is if fixing it would require
credentials, payment, or environment changes you cannot make.**

This is enforced by:
- `.opencode/plugin/enforce-make.ts` — injects completion policy into system prompt
- This AGENTS.md section — proactive instruction
- If you stopped early: RESUME WORK NOW.

## CRITICAL: No-Manual-Default Policy

**Every process MUST be fully automated. No step may require manual intervention by default.**

When you build a feature (downloader, installer, bundler, bootstrapper, etc.):

1. **No "run X manually" instructions.** Everything must be triggered by a `make` target or daemon initialization.
2. **No "config required" defaults.** Every config value must have a safe, working default. The system must boot without any user-created config files.
3. **No "download on request" workflows.** If a binary or resource is needed, it must be prefetched during the build cycle (`make dist`), not downloaded at first use.
4. **No dead-code isolation.** Every class in `src/` must be importable and instantiatable from daemon startup, even if function calls are deferred lazily.
5. **No check-only gateways.** Verify/download scripts must do the action, not just report "not done." If `make bundle-binaries` runs, it must bundle. If a healthcheck runs, it must remedy if possible.

**Manual default is a BUG. Fix it immediately.**

This is enforced by:
- The `completion_audit` in `make preflight` — flags unused classes
- The `no-manual-default` check in this section
- Plugin guardrail in `enforce-make.ts`

## CRITICAL: Nothing-Dropped Guardrail

**Every parallel subagent result MUST be codified BEFORE the agent sends a
terminal response.** Codified means one of: committed, ticked completed in
`todowrite`, OR explicitly cancelled-with-reason. The pattern of "dispatch N
agents → get N results → write summary" is a **bug** — the summary itself is
NOT the deliverable, and any dispatched work that is not codified is dropped.

This was a recurring incident (2026-06-22 et seq): the agent dispatched a
6-wide wave, received 6 results, then sent a prose recap. None of the results
were committed, none were ticked in `todowrite`, none were cancelled — the
work evaporated at session end. The user had to ask "are you codifying all of
these efforts?" — that question is itself a bug report.

**Enforced by `.opencode/plugin/enforce-todos.ts` at two layers:**

1. **`experimental.chat.response.transform`** — when an active `todowrite`
   list has `pending` or `in_progress` items AND the outgoing response looks
   like a summary (heuristic: contains "summary"/"completed"/"done"/"results"
   or has bullet points, AND has no `make` invocation), the plugin PREPENDS a
   loud `⛔ NOTHING-DROPPED GUARDRAIL` directive telling the agent to resume
   work. Advisory — the response still ships — but un-ignorable on the next
   turn.

2. **`tool.execute.before`** — the **commit block** (DEFAULT ON via
   `GLUDD_TODO_GUARD_ENFORCE !== "0"`). When a commit-shaped `make` target
   (`git-commit`, `commit-no-verify`, `repo-commit`, `ship-commit`,
   `git-commit-file`, `commit-bootstrap`, `test-and-commit`) runs while
   pending `todowrite` items exist AND those items are neither referenced in
   the commit message (`MSG=`) nor addressed by a staged `TASKS.md` update,
   the commit is DENIED with guidance. The agent must either complete the
   items, cancel them with a reason, or stage a `TASKS.md` update referencing
   each one.

**Opt-outs (never the default):**

- `GLUDD_TODO_GUARD_ENFORCE=0` — makes the plugin advisory-only (directive
  prepended, no commit block). Use only for focused single-file sessions.
- `GLUDD_TODO_GUARD_BYPASS=1` — emergency hotfix escape hatch; skips the
  commit block for a single commit. Documented but never the default.

**The rule, restated:** a subagent result is not "done" when the agent reads
it — it is done when it is COMMITTED (or explicitly cancelled with a reason
recorded in `todowrite`). A summary message is never a substitute for
codification.

This is enforced by:
- `.opencode/plugin/enforce-todos.ts` — response.transform directive +
  tool.execute.before commit block
- `tests/unit/test_todo_guard_plugin.py` — structural + behavioral pin
- This AGENTS.md section — proactive instruction

## CRITICAL: Human Permission Subjects + Intersection Policy

**Human users carry a `PermissionSpec` just like agents.** Defaults ship in
`config/permissions/human-admin.yml`, `human-operator.yml`, `human-viewer.yml`.
The daemon config `default_human_role` (default `human-operator`) selects the
applied spec when no per-user override exists.

**Intersection rule.** When an agent dispatches a subagent, the effective
permission is the INTERSECTION (lowest-common-subset):

    effective_spec = intersection(human_spec, agent_spec, requested_spec)

Narrowest path-prefix wins; allowed-hosts set-intersected; denied lists
unioned; TTL is the min. No entity ever exercises a permission outside its own
spec — intersection only narrows.

**Escalation requests.** An agent may REQUEST additional permissions via
`POST /admin/perm/escalation-request` ONLY after documenting ≥3 distinct
alternatives it tried (`alternatives_tried` with `{approach, outcome}` entries).
Fewer than 3 → 422.

**Auto-approval.** Requests within the human ∩ agent intersection are
auto-approved (the agent is asking for something it would have had but for an
overly-narrow intersection).

**Outside-intersection requests.** `pending` → surfaced to the human via a
`HumanTodo` (`category=permission_escalation`). Human resolves via
`gludd perm escalations {approve|deny}`. Approval mints an STS scoped to
`(current + requested) ∩ human_spec` — humans cannot grant more than they have.

Enforced by: this section (proactive), the daemon intersection evaluator, and
the escalation request validator (`tests/unit/test_permission_intersection.py`).

## CRITICAL: Human Todo System (bot→human task requests)

**Agents communicate task-blockers to humans via `HumanTodo` records — NOT
logs, NOT event errors, NOT agent todos.** A log line is not a request; a
`HumanTodo` is.

**Use cases:** permission escalation requests, external actions ("create an AWS
account"), decisions ("which of these 3 designs?"), input requests ("paste the
API token"), generic blockers.

**Filing.** Use the `general_ludd.agent.gludd_human_todo` ansible module or
`POST /api/human-todos`.

**Parent linkage.** When a human-todo has `parent_agent_todo_id`, the parent
agent todo transitions to `blocked_on_human` (non-runnable) until the human
resolves it.

- On `done`: parent → `pending`, agent resumes with the human's
  `human_resolution` text injected as `human_input`.
- On `dismissed`: parent cancelled, OR requeued with the dismissal reason so
  the agent can try a different approach.

**CLI:** `gludd human-todo {list|show|done|dismiss|in-progress|comment|watch|stats}`.

**Distinct from:** `TodoModel` (agent-assigned tasks), event log (system
occurrences), audit log (security decisions). Don't conflate them.

Enforced by: this section (proactive), the `HumanTodo` model + daemon route,
and `tests/unit/test_human_todo_*`.

## CRITICAL: Project-Collection Precedence Contract

Each project maintained by gludd has a `.gludd/collections/` directory for project-specific ansible roles/modules. Search order (highest precedence first):

1. **PROJECT** — `<project_root>/.gludd/collections/`
2. **USER** — `${XDG_CONFIG_HOME:-~/.config}/gludd/collections/`
3. **BUNDLED** — `<install_root>/collections/` (the `general_ludd.agent` canonical home)

A role/module FQCN present in a higher tier SHADOWS the same FQCN in lower tiers. To override `general_ludd.agent.project_init` at the project level, place a custom role at `<project>/.gludd/collections/ansible_collections/general_ludd/agent/roles/project_init/` — the precedence system handles the shadowing automatically.

**`gludd project init`** scaffolds the project collection via the `general_ludd.agent.project_init` role (NOT Python). Operators override that role to customize the scaffold (add pre-commit hooks, license headers, custom directory layout).

**`gludd project paths`** prints the resolved precedence table for diagnostics.

**Wiring**: at daemon startup and on project switch, `AnsibleRunnerAdapter` resolves the paths via `src/general_ludd/ansible/paths.py` and sets `ANSIBLE_COLLECTIONS_PATH` + `ANSIBLE_ROLES_PATH` accordingly. The bundled `ansible.cfg` is a fallback; the runtime env vars take precedence.

Direct callability: roles in any tier are callable from any playbook via FQCN. No special registration — this is standard ansible.

Enforced by: `src/general_ludd/ansible/paths.py`, `src/general_ludd/ansible/runner.py`, `daemon.py` lifespan wiring.

## Meta-Rule: Guardrail Policy

When you introduce ANY new restriction or policy on agent behavior, you MUST
implement it at all three layers. Single-layer restrictions are insufficient.

1. **Config permission** (`opencode.json` `permission` block) - hard gate
2. **Runtime hook** (`.opencode/plugin/*.ts`) - contextual error with guidance
3. **Agent prompt** (`AGENTS.md` prominent section) - proactive instruction

Every guardrail must have all three. If you catch yourself adding only one or
two, stop and add the missing layers before continuing. See the
`guardrail-pattern` skill for the full pattern and checklist.

## CRITICAL: Guardrail Integrity Policy

**You MUST NEVER remove, disable, or weaken a guardrail to fix a symptom.**
When a guardrail causes noise, errors, or inconvenience, the fix is ALWAYS to
make the guardrail smarter — never to delete it.

### Forbidden Responses to Guardrail Friction

- Guardrail throws errors on every edit → WRONG: remove the guardrail
- Guardrail message leaks to user UI → WRONG: delete the message
- Guardrail blocks legitimate work → WRONG: empty the block body
- Test for guardrail fails → WRONG: weaken the test assertion

### Correct Response to Guardrail Friction

1. **Identify the root cause.** Why is the guardrail firing on legitimate work?
2. **Narrow the check.** Add conditions so it only fires on actual violations.
3. **Keep the enforcement.** The block/throw/error must still exist for real violations.
4. **Verify the fix.** Run the guardrail tests. Confirm they still pass.

### Principle

Guardrails exist because past sessions demonstrated a specific failure mode.
Every guardrail was added in response to a real bug. Removing a guardrail
without addressing the failure mode it prevents is a regression.

If you find yourself reaching for `throw new Error(...)` → `{}` or deleting
a constant because "it's dead code" — STOP. Ask: "What was this guarding
against?" Then fix the guardrail to be precise, not absent.

This is enforced by:
- This `AGENTS.md` section — proactive instruction
- `.opencode/plugin/enforce-make.ts` — `tool.execute.before` checks
- `tests/unit/test_guardrails.py` — guardrail existence and behavior tests

## Opencode Plugin Ports (Claude Hook Equivalents)

The Claude Code layer (`.claude/hooks/*.sh`, 20 shell scripts registered in
`.claude/settings.json`) and the opencode layer (`.opencode/plugin/*.ts`,
4 TypeScript plugins registered in `opencode.json`) **enforce the same
policies in parallel**. An opencode-only session gets the same guardrails as
a Claude-only session. The port map:

| Opencode plugin | Claude hook(s) ported |
|---|---|
| `enforce-make.ts` | `enforce_make_bash.sh`, `gate_concurrency_pretool.sh`, `guardrail_integrity_edit_pretool.sh`, `no_flag_file_write_pretool.sh` (Bash make-only, metachar deny, concurrent-gate block, guardrail-integrity across ALL hook/plugin files, `.gate-status` write block) |
| `enforce-floor.ts` | `agent_floor_stop.sh`, `agent_floor_pretool.sh`, `agent_floor_posttool.sh`, `agent_ceiling_pretool.sh`, `agent_floor_userprompt.sh` (floor/ceiling bands via `agent_liveness.py`) |
| `enforce-delegate.ts` | `model_utilization_pretool.sh`, `disk_discipline_pretool.sh`, `worktree_disk_guard_pretool.sh`, `force_delegate_pretool.sh`, `mainthread_budget.sh` (sonnet ratio, worktree disk guards, opt-in grind guard, main-thread delegation budget) |
| `enforce-stop.ts` | `no_wait_stop.sh`, `multitasking_backlog_stop.sh`, `session_start_orchestrate.sh`, `no_blocking_questions_pretool.sh` (deferral-pattern block, open-backlog block, orchestration injection, question-tool deny) |

Both layers are registered and active by default. The env-var knobs are
shared (`CLAUDE_AGENT_FLOOR`, `GLUDD_FORCE_DELEGATE`, `GLUDD_NO_WAIT_ENFORCE`,
`GLUDD_FLOOR_ENFORCE`, etc.) so operator configuration applies uniformly.

Coverage tests: `tests/unit/test_opencode_plugin_ports.py` (per-plugin static
checks), `tests/unit/test_guardrails.py` (3-layer existence checks),
`scripts/test_*_hook.py` (behavioral harness tests for the shell layer).

## CRITICAL: "Fix" Means Repair, Never Disable

**When the user asks you to FIX something, "fix" means: make the feature WORK
as intended. It NEVER means disable, remove, downgrade, stub out, comment out,
or weaken the feature. Disabling a feature the user asked you to fix is itself a
NEW BUG — and you must NEVER introduce a bug.**

This was a direct user mandate (2026-06-18) after the agent was told "fix the
stop-hook errors" and responded by making the hooks *advisory* (deleting the
enforcement) instead of fixing the actual error. That turned a working-but-noisy
feature into a non-working feature — a regression dressed up as a fix.

### The distinction (internalize this)

- "It errors / is noisy / fires too often" = the feature is **malfunctioning**.
  The fix is to repair the malfunction while **keeping the feature's purpose
  intact**. (Stop-hook threw `exit 1` every turn → the bug was the `exit 1`
  error path, NOT the blocking. Fix = block cleanly via `{"decision":"block"}` +
  `exit 0`. The enforcement STAYS.)
- "Disable X" / "turn off X" / "make X advisory" = an **explicit** instruction to
  remove behavior. Only do this when the user says so in those words.

### Forbidden "fixes" (every one is a bug you introduced)

- Feature throws an error → ❌ disable the feature. ✅ Fix the error path; keep the feature.
- Check is too strict / noisy → ❌ delete the check. ✅ Narrow it so it fires only on real violations; keep enforcing.
- Test fails → ❌ weaken/delete the assertion or `xfail` it. ✅ Fix the code so the assertion passes (security assertions especially — NEVER weaken).
- Hook/guardrail is disruptive → ❌ make it advisory / empty its body. ✅ Repair the disruption (the error/exit code/false-positive); keep it enforcing.
- Endpoint leaks/over-matches → ❌ remove the endpoint. ✅ Fix the logic; keep the endpoint serving its real purpose.

### Before claiming something is "fixed"

1. Does the feature still DO what it was built to do? If you removed/weakened its
   core behavior, you did NOT fix it — you broke it. Revert and repair instead.
2. Did you introduce any NEW failure mode (disabled enforcement, dropped a case,
   widened access)? If yes, that is a bug — the work is not done.
3. Prove it: the repaired feature must demonstrably still work (a passing test /
   a run that shows the behavior firing), not just "no longer errors."

Overlaps with and strengthens the **Guardrail Integrity Policy** above, but is
broader: it applies to EVERY feature, not only guardrails. Enforced by this
section, `.opencode/plugin/enforce-make.ts`, and the `enforce-floor.ts` plugin.

## CRITICAL: Release Cut = Update the README Status Table

**Every release MUST go through `make release-cut TAG='...' MSG='...'`.  Direct use
of `make git-push-sandboxcom` + `make git-tag-push` without running `release-cut`
first is a policy violation — it bypasses the README currency gate.**

### Rule

Before any release tag is pushed, the README.md **Feature & Task Completion Status
table** and its `**Status as of <version>**` line MUST be refreshed to reflect the
version being cut.  This is enforced as a hard gate, not documentation:

1. **`scripts/check_readme_status_current.py`** — reads `pyproject.toml` (or the
   `TAG` argument), finds the `Status as of <version>` line in README.md, and
   exits non-zero with a clear error message if they do not match.  Accepts an
   optional `TAG` positional argument (`v0.1.0-alpha.2` or `0.1.0-alpha.2`; the
   leading `v` is normalized away for comparison).

2. **`make check-readme-status [TAG='...']`** — runs the script.  Use this to
   check readiness before committing.

3. **`make release-cut TAG='...' MSG='...'`** — the single release command.
   Runs in order and aborts on the first failure:
   1. `check-readme-status` → README stale = ABORT (unskippable)
   2. `git-push-sandboxcom` → push master branch
   3. `git-tag-push` → create annotated tag + push (triggers CI release job)
   4. `release-view` → confirm the published GitHub Release

### What "update the status table" means

Before running `make release-cut`:
- Edit README.md → find the **Feature & Task Completion Status** table.
- Update every row that changed since the last release.
- Change (or add) the `**Status as of v<old>**` line to `**Status as of v<new> — <date>**`.
- Commit the README change in the same release-bump commit as `pyproject.toml` /
  `src/general_ludd/__init__.py` / `CHANGELOG.md`.

### Why this is a hard gate, not documentation

The hooks-over-memory principle: memory and documentation are ignored under time
pressure; machine enforcement is not.  A stale README status table has been a
repeated gap after large feature batches.  The gate makes it structurally
impossible to skip.

### Enforcement

- `scripts/check_readme_status_current.py` — enforcing script (exits non-zero + clear message)
- `make check-readme-status` — callable target
- `make release-cut` — the only sanctioned release command; gate is step 1/4
- This AGENTS.md section — proactive instruction

## CRITICAL: Release Branch Lifecycle — Green Branches Are Immutable

**Once a release branch's remote tip is CI-GREEN, no new commits may land on it.**
Work that cannot be expressed as a tag continues on a NEW branch.

### Rules (each is machine-enforced)

1. **`make release-branch-new NAME=release/<version>`** — the ONLY sanctioned way to
   start a release branch.  Verifies that the base (default: `master`) is CI-GREEN
   before branching, so a release can never start from a red commit.

2. **Green = frozen.** Once the remote tip of a release branch has a CI-GREEN verdict,
   `make git-push-branch` and `make git-push-branch-nv` both REFUSE pushes that add
   new commits (`scripts/check_green_branch_guard.py` — exit 0 = allowed, exit 1 = blocked,
   exit 2 = inconclusive/fail-open).  Enforced in the Makefile via `_push-green-guard`.

3. **Fix-forward on the branch, not around it.** If CI goes RED on a release branch
   (a regression discovered after the first green run), commit the fix directly on
   THAT branch, push, wait for green CI, then proceed.  Do NOT create a parallel
   branch to dodge the guard — the guard only fires when the remote tip is GREEN.

4. **`make release-promote TAG=<tag>`** — the ONLY sanctioned way to ship a release
   branch to master.  Steps (each fail-closed): require CI GREEN for the branch tip →
   verify remote tip matches local → annotated tag + push tag → ff-only merge into
   master + push → verify master remote tip matches the promoted SHA.  The tag is
   pushed BEFORE the master ff-merge so the tagged commit always exists remotely.

5. **`make release-recut TAG=<tag>`** — re-trigger a CI release job on an existing tag
   (delete + re-push).  Use when the Build-and-Release job itself failed (e.g. an
   artifact-upload flake) but the commit is known-good.

### Forbidden patterns

| Pattern | Why forbidden |
|---|---|
| Push new commits onto a green release branch | Violates immutability; guard blocks it |
| `git push --force` past the guard | Bypasses `_push-green-guard`; treat as a guardrail violation |
| Cutting a release branch from a non-green base | `release-branch-new` aborts if base is not CI-GREEN |

### Enforcement

- `scripts/check_green_branch_guard.py` — guard script (exit 0/1/2)
- `make _push-green-guard TARGET=<branch>` — internal guard invoked by `git-push-branch*`
- `make release-branch-new` / `make release-promote` — green-gated branch cut + promotion
- `make test-release-branch-guard` — behavioral test (6 cases)
- This AGENTS.md section — proactive instruction

## CRITICAL: Agent At-Rest / Re-Dispatch Policy

**An agent "coming to rest" does NOT mean it is incomplete.** "At rest" =
the subagent finished its turn and returned its final result (the `<result>` in
the completion notification IS its deliverable). Auto-redispatching a *completed*
agent re-runs finished work, wastes tokens, and can loop forever. So "always
re-dispatch on rest" is INCORRECT as a blanket rule.

**Classify by STATUS, not by the rest event, and act:**

| Status | Meaning | Action |
|---|---|---|
| `completed` + deliverable present | Finished the assignment | **Accept.** Do not re-dispatch. Use the result. |
| `completed` + deliverable partial/wrong | Stopped short of the ask | **Resume** via `SendMessage` (keeps its context — cheaper than fresh) with the specific gap, OR re-dispatch if context is stale. |
| `failed` / stalled / "no progress for Ns" / died | Genuinely incomplete | **Re-dispatch with backoff** (this IS the [[transient-error-retry-with-backoff]] rule). Never abandon the work. |
| killed by transient API error (529/429/503) | Overload, not done | **Re-dispatch after backoff** (exponential if it repeats). |

The floor hook keeps the POOL full; this policy decides what to do with each
agent's *result*. They are independent: a completed agent correctly drains the
pool (the floor hook then asks for a refill of NEW work, not a re-run of the old).

**Path to automate (optional):** a watcher could scan task statuses and
auto-re-queue only `status==failed`/stalled tasks with a per-task max-retry cap
(e.g. 3) and exponential backoff — never `completed` ones, and never without a
cap (or it loops). Until that exists, the orchestrator applies the table above on
each completion notification.

**"Come to rest" — what the status means + the ZOMBIE rule.** A task/agent at
rest is NOT "in error" by default: the harness marks it `completed` (it returned
normally — its deliverable is the `<result>`) or `failed` (it died: stalled,
errored, or was killed). So: `completed` ≠ redo; `failed` ≠ abandon. Re-dispatch
only `failed`/stalled WORK, with a max-retry cap + backoff. Two hard rules from a
real incident (2026-06-18):
1. **A background task that "completed" may have been KILLED, not finished** —
   check its actual exit code / result content, never infer success from the rest
   event alone. (A gate's `.gate-status` test line / pytest summary is the truth.)
2. **NEVER arm a self-relaunching watcher for a long task.** A gate-marshal
   subagent armed `marshal-full-suite` + `marshal-wait-report` watchers that
   re-launched a `-n auto` gate every time it "completed" — it respawned ~6×, each
   OOM-killing the host, and killing the gate process alone didn't stop it (had to
   `TaskStop` the watcher tasks + remove the worktree). A long task that outlives a
   subagent's turn must be owned by the MAIN LOOP via `run_in_background`
   (re-invoked exactly once on exit), not a subagent that rests-and-relaunches.
   Subagent gate/build runs that exceed one turn: rely on polling their
   `.gate-status`/artifact, and never wire an auto-relaunch.

## CRITICAL: Never Block on Questions — Default to Action

**You MUST NOT interrupt work to ask the user a blocking question.** When you
hit a decision point, choose the most reasonable option yourself, state the
assumption you are making in one line, and PROCEED. The user redirects you if
they disagree — that is cheaper than a blocking question that stalls the work.

This was a direct, repeated user directive (2026-06-18): "stop asking questions
that interrupt work." A passive memory ([[gludd-never-block-on-questions]]) did
not stop the relapse, so it is now ENFORCED by a hook.

- **Enforcement:** `.claude/hooks/no_blocking_questions_pretool.sh` is a
  `PreToolUse(AskUserQuestion)` guardrail that DENIES the AskUserQuestion tool
  (clean `permissionDecision:deny` JSON + exit 0, never a hook error; fail-open).
  Registered in `.claude/settings.json`. It is context-efficient — it only fires
  when a blocking question is actually attempted.
- **What to do instead:** decide → state the assumption → act. If new information
  changes the right call, change course and say so. Surface options *alongside*
  continued work, never as a gate in front of it.
- **The rare exception** (truly destructive/irreversible external action the user
  has not pre-authorized): state the plan and the risk and proceed with the safe
  default, or note it and keep going — still do not block. If the user has already
  authorized the action (e.g. "push to GitHub"), just do it.

## CRITICAL: Bash Command Policy

**You MUST only run `make <target>` commands in bash. Never run any other command directly.**

- ALLOWED: `make test`, `make lint`, `make init`, `make sync`, etc.
- DENIED: `uv run ...`, `python3 ...`, `pip install ...`, `git ...`, `which ...`, `ls ...`, `cat ...`, `find ...`, `rm ...`, or any other direct command.

**Shell metacharacters are FORBIDDEN:**

| Character | Name | Why forbidden |
|-----------|------|---------------|
| `\|` | Pipe | Chains commands, bypasses make |
| `;` | Semicolon | Runs multiple commands |
| `&&` | And | Chains commands conditionally |
| `\|\|` | Or | Chains commands conditionally |
| `()` | Subshell | Runs commands in subprocess |
| `$()` | Command substitution | Embeds command output |
| `` ` `` | Backtick | Command substitution |
| `>` / `<` | Redirect | Pipes output to files |
| `2>&1` | Redirect stderr | Chains stderr to stdout |
| `{}` | Brace expansion | Generates arguments |
| `!` | History expansion | Accesses previous commands |

**If you need ANY of these, create a Makefile target.** Make targets ARE allowed to use metacharacters internally.

VIOLATIONS (all will be blocked by the plugin):
- `make test-unit 2>&1 | tail -20`
- `cd /foo && make test`
- `make test; make lint`
- `$(cat file)`
- `make test || true`
- `.venv/bin/python -m pytest ...`
- `cd /path && .venv/bin/python ...`

This is enforced by:
- `opencode.json` permission rules (hard deny on non-make bash)
- `.opencode/plugin/enforce-make.ts` (blocks metacharacters + non-make commands)
- This AGENTS.md section (proactive reminder)

## CRITICAL: TDD Policy

**You MUST write a failing test BEFORE writing implementation code. No exceptions.**

Workflow for every change:
1. Identify the behavior you need.
2. Write a test that fails because the behavior does not exist yet.
3. Run `make test-unit` — confirm the test fails.
4. Write the minimal implementation to make the test pass.
5. Run `make test-unit` — confirm the test passes.
6. Refactor if needed, keeping tests green.

This is enforced by:
- `.opencode/plugin/enforce-make.ts` — prints TDD reminder when you edit files under `src/`
- This AGENTS.md section — proactive instruction
- The guardrail-pattern skill — reusable pattern reference

Do not skip steps. Do not write implementation and then retroactively add tests.
Do not mark work complete unless a test proves the behavior exists.

## CRITICAL: Commit-After-Green Policy

**You MUST commit your work after tests pass and the change is complete. Do not leave green work uncommitted.**

Workflow:
1. Tests pass for the change you made.
2. Run `make test-and-commit` — this runs the full test suite and commits only if all tests pass.
3. If you want a descriptive message, run `make test-and-commit MSG="your message"`.

If you notice uncommitted changes that are test-green, stop what you are doing
and commit them before starting new work.

This is enforced by:
- `.opencode/plugin/enforce-make.ts` — prints commit reminder after test runs pass
- `Makefile` `test-and-commit` target — atomic test-then-commit
- This AGENTS.md section — proactive instruction

## CRITICAL: No-Commit-Bypass Policy

**Every commit-shaped `make` target MUST enforce the `.gate-status` freshness+green check. There are NO exceptions for "feature branches", "stash conflicts", or "pre-existing failures".**

The 2026-06-22 incident: an agent committed `50dbd1b` with a red gate via `make commit-no-verify`, rationalizing "pre-existing failures + env issue". That target existed for pre-commit hook stash conflicts — NOT for skipping the gate. The bypass was the bug.

**Rules:**

1. `make git-commit`, `make commit-no-verify`, `make commit-bootstrap`, `make git-commit-file` — ALL enforce the gate via `_gate-fresh-check`. The `--no-verify` flag on `commit-no-verify` skips ONLY the pre-commit hook stash, not the gate.
2. `make repo-commit` is the ONLY documented escape hatch, for non-code meta-commits only (version bumps, release artifacts, docs). Using it to land code with a red gate is the SAME bug as the commit-no-verify bypass.
3. `make test-and-commit` is allowlisted because it runs pytest inline — its own micro-gate.
4. "Pre-existing failures" are NEVER an excuse to bypass. They are the work. Fix them.
5. "Environmental issues" (expired credentials, network) are NEVER an excuse. Either fix the env issue or dispatch a research task to work around it.

**Enforcement:** `tests/unit/test_commit_gate_freshness.py` structurally scans the Makefile for any target whose recipe invokes `git commit` without referencing `.gate-status` or `_gate-fresh-check`. Any new commit target MUST add the gate check or be explicitly allowlisted in the test with a documented reason.

### CI-as-Gate Override

When the local gate takes too long to complete (>30 min, common for large suites) and CI is the real validation mechanism, use:

```
make commit-no-verify MSG='...' GLUDD_CI_IS_GATE=1
```

This skips the local .gate-status freshness check (CI IS the gate) but STILL skips pre-commit hooks (--no-verify for stash conflicts). Use ONLY when:
1. The local gate has timed out repeatedly (>2 attempts)
2. Lint + typecheck pass locally
3. Targeted tests pass locally
4. CI will run on push/PR to validate

This is NOT a bypass for a red gate — if tests are failing, fix them first.

## CRITICAL: Evidence-Based Response Policy

Every factual claim MUST have supporting evidence from a tool call, file read, URL fetch, or test result.
- If you say "X tests pass", cite the make output.
- If you say "file Y contains Z", cite the file path and line number.
- If you say "opencode supports X", cite the URL or docs page you fetched.
- Unsupported claims are policy violations.

This is enforced by:
- `.opencode/plugin/enforce-make.ts` — injects evidence policy into system prompt
- `src/general_ludd/review/evidence_checker.py` — runtime claim auditing
- This AGENTS.md section — proactive instruction

## Project Overview

This is the general-ludd-agent project: an autonomous coding system with Ansible runners and multi-model AI agents.

- Primary language: Python 3.11+
- Package manager: uv (preferred), pip (fallback)
- Test runner: pytest
- Linter: ruff
- Type checker: mypy
- Worker: FastAPI + Gunicorn + uvicorn-worker
- Database: PostgreSQL (Alembic migrations)
- Secrets: OpenBao + hvac
- Playbook execution: Ansible Runner
- Testing strategy: TDD, Molecule for Ansible content

## Key Make Targets

### Testing
- `make test` - Run full test suite with coverage
- `make test-unit` - Run unit tests only
- `make test-e2e` - Run end-to-end tests
- `make test-guardrails` - Test guardrail infrastructure
- `make test-and-commit` - Run tests then commit if green (`MSG="msg"` for custom message)

### Quality
- `make lint` - Run ruff linter
- `make lint-fix` - Run ruff with auto-fix
- `make typecheck` - Run mypy
- `make healthcheck` - Verify imports work
- `make collect-check` - Fast collection-error gate (use before every commit)
- `make gate` - Full gate: lint + typecheck + collect-check + test; writes `.gate-status`
- `make qa` - Run lint + typecheck + test + healthcheck
- `make validate` - Full validation including ansible syntax

### Setup
- `make init` - Set up the project (dirs + deps)
- `make sync` - Sync uv dependencies
- `make bootstrap` - init + lint + test + healthcheck
- `make clean` - Remove build artifacts

### Git (use ONLY these — NEVER raw git commands)
- `make git-status` - Show git status
- `make git-diff` - Show diff stats
- `make git-staged` - Show staged changes
- `make git-log` - Show recent commits
- `make git-init` - Initialize git repo
- `make git-add FILES='f1 f2 ...'` - Stage specific files
- `make git-add-all` - Stage all changes
- `make git-commit MSG='message'` - Commit staged changes with message
- `make git-reset FILES='HEAD~1'` - Reset to ref (soft by default)
- `make git-branch MSG='name'` - Create branch
- `make git-checkout MSG='branch'` - Switch branch
- `make git-merge MSG='branch'` - Merge branch with --no-ff

### Feature Branch Workflow
- `make feature-start MSG='feature/short-name'` - Create and switch to feature branch
- `make feature-done MSG='feature/short-name'` - Test, merge to master with --no-ff

## CRITICAL: Session Persistence Policy

**You MUST maintain `SESSION.md` at the root of the project. Read it at session start to restore context. Update it after every logical unit of work (feature, fix, test suite). Never leave it stale.**

The file must contain:
- Last updated date
- Current test suite status (pass/fail/skip counts, coverage)
- Last commit hash
- Completed objectives/features
- Known gaps
- Next steps

This ensures you NEVER have to ask "what did we do so far?" — read SESSION.md.

This is enforced by:
- This AGENTS.md section — proactive instruction
- The General Ludd agent's own `AgentBehavior.session_persistence` flag — agents self-enforce
- The `BehaviorRenderer` includes session persistence rules in rendered system prompts

## Working Conventions

- TDD: write failing tests first (enforced by plugin + policy)
- Small, testable increments
- Keep the event loop thin
- Ansible playbooks are the tool-call boundary
- Never force-push
- Never run non-make commands in bash (enforced by plugin + policy)
- Commit after tests pass (enforced by plugin + policy)
- When adding any new guardrail, apply all three layers (enforced by meta-rule)
- **Feature branches**: Start a branch per feature with `make feature-start`, commit small green increments onto it, then `make feature-done` to merge with --no-ff after full test suite passes
- **Atomic commits**: Each commit should represent one logical change (one test file, one feature, one fix). Never batch unrelated changes into a single commit.

## CRITICAL: Self-Audit Policy

**After completing any significant body of work, you MUST perform a full self-audit before declaring it done.**

### Full Self-Audit Checklist

Run through EVERY item below. Do NOT skip any. Fix all gaps immediately.

1. **Conversation History Audit**: This is the MOST IMPORTANT step. Do it FIRST and THOROUGHLY.
   - Query the opencode conversation database at `~/.local/share/opencode/opencode.db`
   - Use the Bash tool with a Makefile target (e.g., `make audit-messages`) to extract ALL user messages:
     `SELECT p.content FROM message m JOIN part p ON m.id = p.message_id WHERE m.role = 'user' ORDER BY m.id;`
   - For EACH user message, identify explicit requests (features, fixes, behaviors, bugs)
   - Cross-reference each request against: (a) code in `src/`, (b) tests in `tests/`, (c) SESSION.md completed items
   - Any request NOT found in implementation is a GAP — fix it immediately
   - **Common missed patterns**: TUI detach fixes, keybinding changes, view additions, CLI subcommands,
     daemon endpoint wiring, config defaults. These get requested in early sessions and forgotten.
   - Do NOT skip this step because "the current conversation doesn't mention it." Prior sessions matter.

2. **Dead Code Audit**: For every new class/module you created, search the ENTIRE `src/` tree
   for imports of that class. If it is only imported in test files, it is dead code — wire it
   into the daemon, event loop, worker, or relevant subsystem.

3. **Wiring Audit**: For every new field added to a schema/model:
   - Is it populated at creation time? (check the daemon endpoints and event loop)
   - Is it propagated through the pipeline? (check JobSpec construction in EventLoop)
   - Is it consumed at the destination? (check Worker endpoints)
   - Is it returned in API responses? (check daemon response dicts)

4. **Migration Audit**: For every new SQLAlchemy model or column:
   - Does an Alembic migration file exist in `alembic/versions/`?
   - Does the migration revision chain link correctly? (`down_revision` references previous)
   - Does `downgrade()` reverse `upgrade()` completely?

5. **Test Level Audit**: Verify tests exist at ALL three levels:
   - **Unit tests** (`tests/unit/`): Test individual functions/classes in isolation
   - **Integration tests** (`tests/integration/`): Test 2+ subsystems together (e.g., EventLoop + DB)
   - **E2E tests** (`tests/e2e/`): Test through the daemon API as a user would

6. **Gap Audit**: For every feature area, check:
   - Does the daemon endpoint exist? Does it support the new field?
   - Does the CLI expose the feature? (`--project`, etc.)
   - Does logging include the new context? (project_id in log records)
   - Are secrets scoped? (per-project secret paths)
   - Is the config per-project? (project-level config overrides)

7. **Cross-Interface Completeness Audit**: For every NEW feature or capability added:
   - If added to CLI, is it ALSO available in the TUI? (e.g., project add → TUI project view)
   - If added to daemon API, is there a CLI command AND a TUI action?
   - If added as a config option, is there a daemon endpoint AND a CLI flag?
   - If added to one view, is it accessible from ALL relevant views?
   - **Pattern**: "CLI get project add" → MUST also have TUI project management.
     "CLI get dispatch_mode" → TUI must show and allow setting it.
   - **Anti-pattern**: Declaring a feature done because it exists in ONE interface.

8. **Evidence**: After completing the audit, run `make test` and cite the pass count.
   Run `make lint` and `make typecheck` and cite the results.

### How to Execute

```
1. Read opencode.db messages (or re-read the conversation history)
2. For each user request, grep the src/ tree for implementation
3. For each implementation class, grep for usage (imports) outside test/
4. For each schema field, trace it: daemon -> event_loop -> worker -> response
5. For each DB model, check alembic/versions/ for migration
6. Check tests/unit/, tests/integration/, tests/e2e/ for coverage
7. Fix all gaps, run make test, commit green
```

## Branch-landing integrity (codified)

Three guardrails against the class of failures where commits land on the wrong
branch, pushes silently no-op, or stale CI runs are misread as verdicts:

**(a) Shared/RC branch mutations: main checkout only.**
Mutations to a SHARED or RC branch (master, main, release/*) MUST happen on the
main checkout (/Users/shawnwilson/gludd) or a non-isolated agent running there
— NEVER in a worktree-isolated agent. A worktree-isolated agent branches off a
divergent HEAD at creation time; any commits it makes go to its own branch,
silently failing to advance the shared branch tip. The orchestrator can never
observe the update via `git log master` on the main checkout.

**(b) Verify the remote after every push.**
After any push to sandboxcom, run:

    make verify-remote BRANCH=<branch> SHA=<local-HEAD>

This calls `git ls-remote sandboxcom` (using the sandboxcom SSH key, same
pattern as `git-push-sandboxcom`) and asserts the remote tip matches the
expected SHA. A silent "Everything up-to-date" push (where the branch was not
actually advanced) exits non-zero with `REMOTE MISMATCH: remote=X expected=Y`.
Never claim a push succeeded until `VERIFIED <branch>@<sha>` is printed.

**(c) Never report a CI verdict whose headSha != the branch tip.**
Use `make ci-verdict BRANCH=<branch>` instead of reading raw `ci-status`
output. `ci-verdict` prints the latest run's headSha alongside its conclusion,
and emits a loud `STALE RUN WARNING` if that headSha does not match the current
local HEAD of the branch — making it structurally impossible to misread an old
run as the verdict for a new push. A run only counts if its headSha matches the
branch tip; otherwise the run is stale and must be discarded.

This is enforced by:
- This AGENTS.md section — proactive instruction
- The session persistence policy — SESSION.md tracks known gaps

## Model Utilization — Keep Sonnet Dominant

**Standing rule:** `sonnet` is the cost-efficient default model.  The user wants a
sonnet-dominant dispatch ratio.  The hook operates in two modes:

### Default mode (10%-band)

When sonnet falls more than 10 percentage points below the combined other-model share in
recent dispatches, the hook emits an advisory nudge to rebalance toward sonnet.

### Time-bound 2:1 target mode

A stricter 2:1 sonnet target (67%) can be activated for a fixed duration using:

```
make set-sonnet-target HOURS=24 SHARE=0.67
```

This writes `.claude/sonnet_ratio_target` with a `target_share` and `until_epoch`.
While the window is active (i.e. `now < until_epoch`), the hook enforces `target_share`
instead of the 10%-band.  The target auto-expires — no cleanup needed.

- **Config file:** `.claude/sonnet_ratio_target`
- **Format:** `{"target_share": 0.67, "until_epoch": <unix-timestamp>}`
- **Env override:** `GLUDD_SONNET_TARGET_CONFIG` overrides the config file path;
  `GLUDD_SONNET_TARGET_SHARE` overrides `target_share` for that invocation.
- **Auto-expiry:** once `until_epoch` is passed, the hook silently reverts to 10%-band mode.

**How this is enforced (3-layer guardrail):**

1. **Hook** — `.claude/hooks/model_utilization_pretool.sh` (`PreToolUse` / `Agent` matcher):
   - Maintains a rolling window of the last 20 model dispatches in `/tmp/gludd-model-util.json`.
   - Appends the current dispatch's model *before* computing shares (so it counts).
   - **Time-bound mode** (active window): if `sonnet_share < target_share` → emits a
     time-bound advisory nudge with "target is N% (2:1) until YYYY-MM-DD HH:MM".
   - **Default mode** (expired/absent config): if `sonnet_share < non_share − 0.10` →
     emits the standard band advisory nudge.
   - Silent when sonnet is healthy.  Fail-open on any error.
2. **Settings** — registered in `.claude/settings.json` under `PreToolUse` with
   `"matcher": "Agent"` alongside `agent_ceiling_pretool.sh` and `disk_discipline_pretool.sh`.
3. **Prompt** (this section) — proactive instruction to prefer `sonnet` and treat the
   nudge as a rebalancing signal, not noise.

**What to do when the nudge appears:** Use `model:'sonnet'` for the next N dispatches
that do not specifically require a stronger model (e.g. complex multi-file synthesis →
`opus`; simple file reads / research → `sonnet` or `haiku`).  Return to the default
(`sonnet`) once the window re-balances.

**Do NOT** suppress, ignore, or remove the nudge — it is a utilization signal, not an
error.  Removing the hook without addressing the utilization imbalance is a guardrail
integrity violation (see "Guardrail Integrity Policy" above).

## Multitasking / Blockers

**Core rule:** work is SERIAL only if it mutates the shared `master` working tree or
competes for the one gate/commit/push slot. Everything else is PARALLEL — fan it out to
an isolated git worktree. True blockers: merging to master, running `make gate`/commit/push,
resolving conflicts in `daemon.py`/`routers/facts.py`/`db/models.py`/`db/repository.py`.
False blockers (parallelize, do NOT wait): independent features, additive new files,
CI observation, research/planning. Before ever "waiting," apply the decision checklist:
(a) mutates shared master tree now? (b) needs gate/commit/push now? (c) depends on
unmerged code? All NO → not a blocker, spin a worktree agent. Full policy: `docs/ORCHESTRATION.md`.

## CRITICAL: Release Pipeline Must Be CI-Green (codified)

**Every release tag MUST be preceded by a passing "Build and Release" CI run on the
exact commit being tagged. `make release-cut` enforces this as step 0 and aborts the
entire release if CI is not green. The CI workflow independently enforces it too: the
`release` job `needs: [gate]` (transitively via the platform build jobs), so a tag push
cannot publish a GitHub Release if the gate fails.**

### Rule
Before `git-push-sandboxcom`/`git-tag-push` run, `scripts/require_ci_green.py` is called
against HEAD. It queries GitHub Actions via `gh run list` and is fail-closed:

| CI state | Exit | release-cut behaviour |
|---|---|---|
| completed + success | 0 (GREEN) | proceeds |
| in_progress / queued / pending | 2 (PENDING) | ABORT — wait, retry |
| failure / cancelled / timed_out / unknown | 1 (RED) | ABORT — fix CI, retry |
| no matching run found | 1 (RED, fail-closed) | ABORT — push triggers a run; wait |

### Enforcement (both sides)
- **Client:** `scripts/require_ci_green.py` (pure `verdict_for()` unit-tested in
  `tests/unit/test_require_ci_green.py`, 17 tests) → `make require-ci-green [SHA=…]` →
  `make release-cut` step 0/4. The only sanctioned release command.
- **CI:** `.github/workflows/build.yml` — `release` job `needs: [version, gate, …]`; the
  gate runs on `v*` tag pushes. Broken code cannot publish a release.

### Never
- Never push a release tag manually (bypasses the client gate).
- Never push fix-forward waves straight to `master` as if releasable. Use a
  `release-candidate/*` branch, confirm its CI green, then `ship-ff` master to it.
- Never claim "green" without a CI run id + SUCCESS conclusion for the exact SHA
  (reinforces the no-unquantified-status-claims rule). Per-file `test-iso` is NOT the gate.

## CRITICAL: A Release is an Artifact, Not a Tag (codified)

**A version is NOT done until its Build-and-Release CI run is GREEN and
`make verify-release-artifact TAG=<tag>` exits 0 (published assets confirmed).**

This was codified after neither `v0.1.0-alpha.2` nor `v0.1.0-alpha.3` ever
produced a downloadable artifact: the gate was red on both releases, so the
`release` job (which `needs: [gate]`) was skipped.  Tags existed; artifacts did
not.  Both releases were treated as "shipped" — a false claim.  This section is
the machine-enforceable correction.

### The rule (three bindings)

1. **A version is NOT shipped until `make verify-release-artifact TAG=<tag>` passes.**
   That command calls `scripts/verify_release_artifact.py` and exits 0 only when
   `gh release view` returns a non-draft release with at least one downloadable
   asset.  A tag in the repo with zero assets = NOT shipped.

2. **Never bump to the next version while the current version lacks a green release
   and confirmed artifact.**  "alpha.3 is done, starting alpha.4" is only valid
   when `make verify-release-artifact TAG=v0.1.0-alpha.3` returns PASS.

3. **A release/version task may only be marked completed with the artifact URL as
   evidence.**  The completion entry in TASKS.md must include:
   - The `gh release view` output showing `isDraft: false` and `assets: N` (N ≥ 1)
   - The artifact download URL(s)
   - The CI run id and `conclusion: success`
   Without all three, the task is NOT complete — marking it done is a false claim
   (see the no-unquantified-status-claims rule).

### Enforcement (three layers)

- **Script:** `scripts/verify_release_artifact.py` — exit 0 only if assets exist.
  Fail-closed: no gh / no network / release missing = exit 1.
- **Make:** `make verify-release-artifact TAG=<tag>` — the callable gate target.
  `make release-cut` calls it as step 4/4 with a poll loop (async CI); if the
  poll exhausts without seeing assets it exits non-zero with a loud warning so a
  tag is never mistaken for a shipped release.
- **Prompt (this section):** proactive instruction — the three rules above.

### Tag vs. artifact — the key distinction

| State | Meaning | Action |
|---|---|---|
| Tag pushed, CI still running | release job in flight | Wait; run `make verify-release-artifact` after CI completes |
| Tag pushed, CI green, assets published | **SHIPPED** | Mark done with artifact URL as evidence |
| Tag pushed, CI red/skipped, zero assets | **NOT SHIPPED** — broken release | Fix CI, cut a new release, do NOT bump version |
| No tag, no run | Work in progress | Keep working |

### Never

- Never call a version "done" or "shipped" from a tag alone.
- Never open a next-version epic/task while the current version has no artifact.
- Never log a completion entry without the artifact URL and CI run id.
- Never treat `make release-cut` success as proof of an artifact — only
  `make verify-release-artifact` is the proof (it queries the actual GitHub Release).
  If `release-cut` timed out on its poll, run `verify-release-artifact` manually
  after CI finishes.

## CRITICAL: Minimum 10 Subagents at All Times

**You MUST maintain a MINIMUM of 10 concurrent subagent threads doing useful work at all times.** Never let the active count drop below 10 while work remains.

**Steady-state dispatch rule:** The moment ANY subagent completes (or fails), you MUST immediately dispatch a replacement. Do NOT wait for the remaining batch to drain before dispatching more. The pipeline must stay primed at 10+ at all times.

**How to maintain the floor:**
1. After each subagent completion notification, immediately check: how many are still running?
2. If <10, immediately dispatch (10 - running) new subagents on the next available work item.
3. Never present a status report or summary and stop — always have 10 threads in flight.
4. If you run out of known work items, dispatch research/audit/review subagents to FIND more work.

**The floor was raised from 6 to 10 on 2026-06-22** by direct user mandate. The env var is `CLAUDE_AGENT_FLOOR=10`. The plugins (enforce-floor.ts, enforce-delegate.ts, enforce-stop.ts) all default to 10. The `.claude/settings.json` sets it to 10.

**This is NOT optional.** Running with fewer than 10 subagents is a bug. The user will interrupt and ask why the floor isn't maintained. The enforce-floor.ts plugin will inject floor-breach directives if the count drops below 10.

**See also:** the *Steady-state dispatch (the 10-agent floor)* subsection under Pipeline Orchestration Model below for the concrete behavioral rules (fast result processing, no long foreground ops, next-wave-ready, uniform-duration tasks, research as filler) that make maintaining this floor possible in practice.

## Pipeline Orchestration Model

The goal is a **continuous, pipelined** stream of subagent batches — not a
sawtooth of "dispatch burst → drain to zero → repeat."  Draining to zero wastes
the pool; the reconciliation cost is low compared to the dispatch-to-first-result
latency, so keep batch N+1 in flight while batch N is reconciling.

**1. Keep the pipeline primed.** As soon as batch N delivers results, the next
batch of agents must already be running (or launch immediately).  Never let the
active-agent count drop to zero while independent work remains.

**2. Bias each new batch toward disjoint / new-file work.**  The real cost of
pipelining is concurrent edits to the same hot files (`daemon.py`, `loop.py`,
`gateway.py`) — those edits cannot be trivially unioned and require manual
conflict resolution.  Work that touches distinct files reconciles cheaply.
When hot-file edits are unavoidable, serialize them through the integrator (one
at a time), and fill remaining agent slots with disjoint work.

**3. Run one continuous integrator.**  A single integrator agent drains finished
worktree commits onto the main branch in a steady stream.  Conflicts are resolved
by keeping BOTH sides (union of independent fixes); gate must be green after each
merge before the next one lands.

**4. Bound the pipeline by two constraints.**
- **(a) Hot-file concurrency:** at most ONE in-flight agent per hot file
  (`daemon.py`, `loop.py`, `gateway.py`) at any given time.  More than one is a
  guaranteed conflict.
- **(b) Worktree disk:** each worktree-isolated agent creates a ~320 MB venv.
  Prefer **non-isolated agents** for new-file work and read-only research — they
  share the main venv and add no disk, but they MUST NOT run `git commit` or any
  git mutation that would race the integrator.  When no worktree-isolated agents
  are live, reclaim disk with `make clean-worktree-venvs`.  Cap simultaneous
  worktree agents at ~5–6 to avoid ENOSPC deadlocks (see
  [[gludd-disk-discipline]] memory).

**Summary:** dispatch disjoint work in parallel → integrator merges continuously
→ one integrator, one hot-file agent at a time → non-isolated agents for
new-file / read-only tasks.

### Subagent dispatch reliability rules

Subagents fail when they try to run long operations. To maximize success rate:

1. **Each subagent task must complete in under 5 minutes.** If a task takes longer, split it or run it in the foreground. **This limit is NOW MECHANICALLY ENFORCED** by `.opencode/plugin/enforce-deadline.ts` — every task/agent/workflow dispatch records a wall-clock start timestamp in `/tmp/gludd-task-deadlines.json`; on every subsequent tool call the plugin scans for tasks whose elapsed time exceeds `GLUDD_TASK_TIMEOUT_MS` (default 300000 ms = 5 min) and emits a loud `TASK DEADLINE EXCEEDED: task <id> ...` `console.warn`. The orchestrator (you) sees the warning in its tool stream and MUST act: re-split the work, dispatch a replacement, or run it in the foreground. Plugins cannot hard-kill a running task — the warning is the signal, the response is yours. `GLUDD_TASK_DEADLINE_ENABLED=0` disables it.
2. **NEVER dispatch `make gate` to a subagent** — it takes 40 minutes and will be cancelled. Run it in the foreground with a heartbeat.
3. **Each subagent gets ONE focused task** — one file to edit, one test to run, one research question. Don't bundle multiple concerns.
4. **Read-only research tasks are the most reliable** — they never conflict and rarely time out.
5. **File-editing tasks must specify exactly one file** — multiple-file edits risk conflicts with parallel agents.
6. **Dispatch immediately when any agent completes** — do not wait for the batch to drain. The floor must stay at 10.

### Main-thread command restriction (ANTI-STALL RULE)

**The ONLY commands allowed on the main thread are:**
- `make ci-verdict-fast BRANCH=<branch>` (<1 sec, read-only CI check)
- `make ship-commit MSG='...'` (dispatch this to a subagent, not the main thread)
- Task dispatch calls (near-instant)

**NEVER run on the main thread:**
- `make gate`, `make test-unit`, `make test`, `make qa`, `make test-e2e`, `make validate`
- `make lint`, `make typecheck`, `make collect-check`, `make smoke` (dispatch to subagent)
- `make git-add-all`, `make commit-no-verify`, `make git-push-branch-nv` (use `make ship-commit` via subagent instead)
- ANY command that takes more than 3 seconds

**Why:** The main thread blocks ALL subagent dispatch while it runs a command. A 30-second lint check = 30 seconds with 0 subagents running. A 40-minute gate = 40 minutes of total stall. The user sees this as "process malfunctioning."

**Pattern for each wave:**
1. Get 10 subagent results
2. Write ZERO analysis text
3. Immediately dispatch 10 new subagents — one does `make ship-commit`, nine do work
4. Repeat

#### Background-gate workflow (canonical way to run a long gate)

`make gate` is a ~40-minute operation that MUST NEVER run on the main thread
(it blocks ALL subagent dispatch — see "Main-thread command restriction" above).
The canonical replacement is the background-gate target family:

- `make gate-background` — launches `make gate` via `nohup` in the background,
  redirects output to `.gate-logs/gate-<timestamp>.log`, writes the PID to
  `.gate-background.pid`, and returns in <1 second.
- `make gate-status-check` — non-blocking probe: prints whether the background
  gate is still running, the current phase (greps the log for
  `=== GATE PHASE: <name> ===` markers), the terminal marker
  (`=== GATE: PASSED ===` / `=== GATE: FAILED ===`), the last 20 log lines,
  and `.gate-status`.
- `make gate-tail` — live tail of the latest gate log (Ctrl-C to stop).
- `make gate-logs` — lists every `.gate-logs/*.log` with mtime + PASS/FAIL/incomplete.
- `make gate-kill` — SIGTERM then SIGKILL after 5s; removes `.gate-background.pid`.

**Pattern:**
1. Launch `make gate-background` (foreground is fine — it returns in <1s) or
   dispatch it via a subagent.
2. Continue other work in parallel (the pipeline stays primed at 10+ agents).
3. Poll `make gate-status-check` from a subagent every ~60s.
4. When the terminal marker appears, ingest the log + act on the result.

**NEVER** `make gate` on the main thread. **NEVER** `make gate-background`
on the main thread either if it would block — but `gate-background` returns in
<1s, so it is allowed on the main thread.

Enforced by: this section (proactive), `.opencode/plugin/enforce-make.ts`
(the long-running-foreground deny message includes a `SUGGESTION` directive
pointing to `make gate-background` + `make gate-status-check`), and
`tests/unit/test_gate_background_targets.py` (target existence + phase markers
+ terminal markers + nohup + PID file).

### Steady-state dispatch (the 10-agent floor)

The goal is a **continuous, pipelined** stream of subagent batches — not a sawtooth of "dispatch burst → drain to zero → repeat."

**BEHAVIORAL RULES:**
1. **Process results FAST.** When a batch of subagent results returns, scan them in under 5 seconds and immediately dispatch the next wave. Do NOT write ANY analysis prose between waves.
2. **Never run long foreground operations.** `make gate` (40 min), `make test-unit` (27 min) — these block the bash tool and prevent ALL subagent dispatch. Use `make gate-background` or CI instead.
3. **Always have the next wave ready.** Before the current batch returns, know what the next 10 tasks will be. The moment results arrive, dispatch — don't think, don't plan, dispatch.
4. **Prefer uniform-duration tasks.** If all 10 tasks take ~2 min, they finish together and you refill immediately. If some take 30s and others 5min, you're at 3-4 agents for minutes waiting for the slow ones.
5. **Read-only research tasks are the filler.** When you don't have 10 edit tasks, fill the remaining slots with research/audit/review tasks. They're reliable and always productive.
6. **Dispatch commit+push AS a subagent.** One of the 10 tasks runs `make ship-commit MSG='...'`. This keeps 9 productive tasks running while the commit happens in parallel.

### Message-shape mechanical rule (HARD ENFORCEMENT)

Every assistant response containing tool calls MUST satisfy ONE of:
- **(a) Zero task/agent/workflow dispatches** — pure read/edit/bash, no subagent fan-out. Valid for: serial mutations to hot files (daemon.py, loop.py), git operations, single-file edits during a hot-file conflict.
- **(b) Five or more parallel task/agent/workflow dispatches in ONE message** — the dispatch wave pattern. This is the steady-state.

A response with 1–4 task dispatches is a **policy violation** when ≥3 known work items remain. The agent MUST either batch them into a ≥5-wide wave OR add read-only research/dispatch filler to reach 5.

**Never**: make a single-task-dispatch message and wait for the result when ≥3 work items are known. Either fan out wider, or do non-blocking work inline while the wave runs.

This rule exists because the agent repeatedly claimed "dispatching 10 parallel agents" but delivered 1-3 in sequence, serializing work that should have been concurrent. The floor plugin (enforce-floor.ts) blocks non-dispatch tool calls when live < 10 and GLUDD_FLOOR_ENFORCE=1; this prompt rule is the proactive layer that prevents the breach from happening in the first place.

## Codify Improvements (Meta-Rule)

**When you discover a better way to work, codify it IN THE SAME SESSION before
moving on.**  Applying a better approach once and forgetting it is a bug —
identical to discovering a better algorithm, using it once, and then reverting
to the old one.

### The three codification layers (in priority order)

1. **`AGENTS.md` (policy)** — captures the rule for every future agent reading
   this file.  Add a new section or extend an existing one.  Keep it concise and
   consistent with the voice of surrounding sections.
2. **`.claude/hooks/` script (enforced behavior)** — a `PreToolUse` or
   `PostToolUse` hook that *enforces* the rule mechanically.  Register it in
   `.claude/settings.json`.  A hook is better than a prompt for patterns that
   repeat and are hard to notice in the moment.
3. **Memory (cross-session)** — write a memory entry when the insight needs to
   survive session resets and applies globally, or is too detailed for AGENTS.md.

Add a test for the guardrail where useful (e.g., for a hook that checks file
content, add a unit test under `tests/unit/`).

### Orchestration hooks — current state

The no-wait and floor-enforcement orchestration hooks are **advisory by default**
(they emit guidance but do not block):

- `GLUDD_NO_WAIT_ENFORCE=1` — elevates the no-wait hook from advisory to
  blocking (denies the tool call).
- `GLUDD_FLOOR_ENFORCE=1` — elevates the floor hook from advisory to blocking.
- **`GLUDD_FORCE_DELEGATE=1`** (`force_delegate_pretool.sh`, matcher `*`) — opt-in grind guard.
  Denies targeted mutations (Edit/Write to non-memory paths; mutating Bash targets like
  `git-commit`, `git-add`, `ship`, `gate`) when the live subagent count is below
  `CLAUDE_AGENT_FLOOR` and the consecutive-targeted-call count exceeds
  `GLUDD_FORCE_DELEGATE_GRACE` (default 3). Bounded escape after
  `GLUDD_FORCE_DELEGATE_MAXBLOCK` (default 4) consecutive denials to prevent wedging.
  Read-only tools (Read/Glob/Grep/Bash read-only targets/memory-path writes) and
  Agent/Workflow dispatch are always allowed; Agent/Workflow dispatch also resets the
  consecutive counter. Default off; enable when multitasking discipline is required.

`agent_liveness.py` counts **Workflow subagents** (not just background tasks) as
live agents for the purposes of the floor check.

When you discover that a hook fires too aggressively or too rarely, narrow the
check (see "Guardrail Integrity Policy") — do NOT remove the hook or make it
permanently advisory when the intent is enforcement.

### Todowrite discipline (mandatory for ≥3-ask sessions)

When the user raises ≥3 distinct asks in one session (including implicit asks like "fix the bug that allowed you to stop"), the agent MUST maintain a `todowrite` list tracking every ask until its codification is complete. "Complete" means: codified at all 3 layers (AGENTS.md policy + hook/plugin enforcement + test), committed, and (if applicable) pushed.

Dropping an ask between subagent results is a bug. The todowrite list is the contract that prevents it. Update it after every subagent result lands: mark completed only when the codification is committed, not when the subagent returns.

Pattern that failed (this session): agent dispatched 6 parallel subagents, got 6 results, then sent a text summary without codifying any of them or updating todos. The user had to ask "are you codifying all of these efforts?" — that question is itself a bug report.

## Constraints Are To Engineer Around

**A constraint is a design prompt, never a dead end.**

GitHub API granularity, local-gate OOM, make-only Bash, rate limits, job timeouts,
"no make target for X", clock time — these are constraints. Constraints define the
problem shape; they do not terminate it. When you hit a constraint, your job is to
engineer around it, not to hand it back to the user.

### The rule

The phrase *"X isn't possible / it's a limitation / there's no way / we have to
wait"* is ONLY acceptable when **immediately paired with the workaround being
implemented OR a research task dispatched to find one.** A naked "can't" — or
parking the problem in the user's lap — is a bug.

> **If no workaround is obvious, that is a research task to dispatch, not a
> stopping point.**

### Worked example

> "GitHub only exposes step results at completion — there's no way to get
> live per-step status."

Engineered around by:
1. **Sharding the CI test job** so job-level failures surface within minutes (not
   after a monolithic run), and
2. **Live annotation polling** (`gh run watch` / annotation API on each job) so
   partial results can be read before the overall run completes.

The constraint (API granularity) shaped the solution (shard + poll); it did not
end the conversation.

### Forbidden responses

- "The API doesn't support that." ← stop sign without a workaround
- "It's a limitation of X — we'd have to wait." ← parking it on the user
- "There's no way to do this without Y." ← dead end without research task
- "We can't get that data; it isn't exposed." ← same, every time

### Correct responses

- "The API doesn't expose per-step status, but I can shard the job + poll
  annotations — implementing now."
- "No make target for X yet. Adding one."
- "Rate-limited — backing off 60 s, then retrying."
- "OOM on full gate locally. Running the slow tests in a CI PR instead."

### Enforcement

This is codified at all three levels:
1. **This section** — proactive instruction for every agent reading AGENTS.md.
2. **`.claude/hooks/no_wait_stop.sh` constraint-as-stopsign group** — when
   `GLUDD_NO_WAIT_ENFORCE=1`, naked constraint phrasings block the turn-end.
3. **`scripts/test_no_wait_hook.py`** — proves the constraint patterns block
   in enforce mode.

## Keep Opus Lean — Sonnet Carries the Token Load

The expensive opus main thread must consume far fewer tokens than the cheap
sonnet subagents.  Target: sonnet subagent tokens >= opus main-thread tokens
at minimum; cost-weighted (opus ~5× sonnet $/token), aim for sonnet consuming
SEVERAL TIMES the opus tokens.  Every opus token should buy
coordination/judgment, not grunt work.

**Levers (the controllable behaviors):**

1. **Delegate ALL heavy reading/editing/testing to `model:'sonnet'` subagents —
   never grind inline.**  File trawls, large diffs, test runs, research surveys,
   multi-file edits: dispatch them.  Do not perform grunt work on the main thread.
2. **Keep main-thread turns terse.**  Short replies; do NOT re-read large tool
   outputs or transcripts into context; don't re-derive established facts; lean
   on the memory index for session state.
3. **Subagents return terse summaries + a file pointer**, keeping detail off the
   main thread.  The main thread receives a punch-list, not the raw output.

**Honest limit:** there is no live token meter on the main loop and no per-agent
token accounting in hooks, so a true token-ratio hook is not feasible.  The
enforceable proxy is the existing `model_utilization` `PreToolUse` hook
(sonnet : non-sonnet dispatch-count ratio ≥ 10:1), which indirectly drives
sonnet token dominance — plus the terse-main-thread discipline above.
