# Dispatch Wave Enforcement

Delegation is optional and bounded. Zero subagents is valid when the current
work has no concrete, independent deliverable that benefits from delegation.
When an orchestrator voluntarily creates a dispatch wave, the wave contains
one to three prompts; the hard ceiling is three concurrent subagents. A fourth
dispatch is rejected.

The default mandatory floor is zero, so inline work is never blocked merely to
manufacture agent activity. An operator may configure a positive floor for a
specific environment. In that case, a completed wave smaller than the selected
floor is rejected, but the configured floor is still capped at three.
`GLUDD_DISPATCH_WAVE_WIDTH` remains an isolated test knob and is subject to the
same ceiling.

## Pre-dispatch audit

Before a voluntary dispatch, the plugin writes
`/tmp/gludd-dispatch-preflight.json`. This is an observable record of the
configured required width and the bounded pending items selected for that wave.
It is marked complete when the selected or configured width is reached. The
file records a decision; it does not create work or authorize duplicate work.

The orchestrator performs this audit before composing a wave:

1. Select up to three concrete, independent deliverables from the task ledger
   or current failures. Use zero subagents when no such deliverable exists.
2. Deduplicate task specifications and file ownership. Never assign shared
   infrastructure to more than one worker.
3. Classify each deliverable as an enhancement or a fix; at least half of a
   multi-task wave must be enhancement work.
4. Keep research serialized and limit concurrent coding or test work to two
   disjoint file sets.
5. Dispatch only the selected work in one response. Never add filler merely to
   satisfy a floor.

## Content-addressed ownership ledger

`enforce-delegate.ts` records every `task`, `agent`, and `workflow` dispatch in
`.gludd/dispatch-ledger.json` before the worker starts. The key is a SHA-256 of
the canonical tool kind, project-root digest, scope digest, and digest of the
NFKC/lower-case/whitespace-normalized task specification. The operator-owned
`GLUDD_PROJECT_ROOT` and `GLUDD_DISPATCH_SCOPE` values define those boundaries;
prompt text cannot change them. Tracked IDs such as `S83.157` are indexed within
the same project and scope, so rewording a prompt does not create a second owner
for the same tracked item. Only the repository's dotted, numeric task-ID form is
indexed; filename-like tokens such as `README.md` are ordinary prompt content.

Ledger v3 stores only project, scope, and specification digests—not prompt text
or a prompt preview. Denials contain a reason code, fingerprint, state, attempt
count, and `content_omitted=true`; they never echo the task body. Each claim also
records a hashed owner identity, PID, approximate process start, and immutable
claim time. Malformed entries, unavailable state, invalid scope/window settings,
and ambiguous ownership all fail closed.

An `in_progress` or `completed` fingerprint or tracked ID is denied before the
tool call. `failed` and `cancelled` are terminal too: neither is retried unless
the caller supplies structured `retry_terminal: true`, or appends the transport-
safe `Retry-Terminal: true` line to the prompt. That control line is removed
before canonical hashing, so it addresses the same claim rather than creating a
new fingerprint. `completed` is never retryable. An explicit retry may reclaim
`in_progress` work only when both conditions hold:
the immutable claim age exceeds `GLUDD_DISPATCH_STALE_MS` (six hours by default,
bounded to 1 ms–30 days) and the recorded owner PID is dead. A legacy claim with
unknown ownership is deliberately not auto-reclaimed. This conservative seam
prevents a merely slow worker from being mistaken for a crashed worker. Before
retrying work that may have produced an external side effect, the operator must
reconcile that effect; the dispatch ledger proves ownership, not transactional
exactly-once delivery to outside systems.

Writes use a private temporary file, file and directory synchronization, and
atomic rename. A process-owned exclusive writer lock prevents two OpenCode
processes from reading the same generation and losing one owner's update. A live
writer makes a new dispatch fail closed; its lock is reclaimed only after the
30-second lease is stale and the recorded process is dead. Malformed or
ambiguous locks are never deleted automatically.

The gate-facing `make check-dispatch-dedup` validates both legacy v2 and current
v3 during rollout. It recomputes every applicable fingerprint and validates
transition fields, owner identity, task IDs, attempt count, stale-recovery count,
and blocked-duplicate count. If the ledger has never been created it reports
`INACTIVE`; it does not misreport missing evidence as clean. A malformed ledger
fails closed.

On the first locked v3 mutation, a valid v2 ledger is converted in memory and
written by the same fsync-and-rename path. Status, attempts, canonical task IDs,
counters, and first-dispatch time survive; stored v2 prompt material is replaced
by digests. Filename-like values accepted by the broader v2 ID parser are
dropped during migration so they cannot create false cross-prompt collisions.
Existing processes keep their already-loaded implementation until the planned
OpenCode restart, so deploy and validation happen before activation.
During rollout the dual-version checker accepts either durable format; rollback
must preserve the ledger rather than delete ownership evidence. Because v3 is a
forward migration, roll back the application only with the matching v3-capable
owner library/checker or restore the pre-migration ledger snapshot. Never run a
v2-only owner against a v3 ledger.

Untracked work still receives exact canonical-content deduplication. Release and
backlog work should include a stable task ID so ownership also survives prompt
rewording. The ledger is Gludd-owned state under ignored `.gludd/`, so it
survives agent restarts without entering commits or colliding with source.

## Practitioner evidence

The bounded policy addresses failure modes reported by tool users:

- An OpenCode user reported that five parallel local-model contexts made a
  five-minute task take about 45 minutes and requested a configurable cap:
  <https://github.com/anomalyco/opencode/issues/27110>.
- A Claude Code user reported recursive fan-out turning five intended agents
  into hundreds of completed background agents and exhausting the usage quota:
  <https://github.com/anthropics/claude-code/issues/72566>.
- Another report describes parallel agents returning full transcripts and
  forcing parent-context compaction:
  <https://github.com/anthropics/claude-code/issues/18351>.
- A LangGraph user reported long-running subagents being silently dispatched
  again with identical content hashes, causing duplicate cost and work:
  <https://github.com/langchain-ai/langgraph/issues/7417>.
- Another LangGraph report documents identical request IDs creating forked
  checkpoints and multiple execution paths:
  <https://github.com/langchain-ai/langgraph/issues/6728>.
- A Claude Code report from March 2026 describes repeated attempts to resume a
  still-running background agent. It was eventually closed as stale, but the
  reproduced loop shows why a live or fresh owner remains non-retryable:
  <https://github.com/anthropics/claude-code/issues/32085>.
- A practitioner thread on `r/LangChain` describes crashed workers leaving work
  `in_progress` forever and warns that an aggressive sweeper can duplicate a
  merely slow worker. The discussion recommends atomic claims, attempt identity,
  leases, and reconciliation before retrying possible side effects:
  <https://www.reddit.com/r/LangChain/comments/1u8a4ts/how_are_you_handling_agent_crashes_midtask_with_a/>.
- An `r/mcp` operations thread reports long-running jobs being forgotten across
  sessions and argues that terminality must be durable state, while ambiguous
  timeouts should be reconciled rather than blindly rerun:
  <https://www.reddit.com/r/mcp/comments/1wbiem9/how_do_you_guys_keep_an_agent_from_forgetting/>.

These reports do not establish universal performance numbers. They do show why
Gludd treats concurrency as a bounded resource and delegation as an explicit
ownership decision rather than a mandatory activity count.
