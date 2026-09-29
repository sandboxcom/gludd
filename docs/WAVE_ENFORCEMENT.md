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
the tool kind plus the lower-cased, whitespace-normalized task specification.
Tracked IDs such as `S83.157` are indexed separately, so rewording a prompt does
not create a second owner for the same tracked item.

An `in_progress` or `completed` fingerprint or tracked ID is denied before the
tool call. A tool result with an explicit error moves the entry to `failed` and
permits a bounded retry; a successful result moves it to `completed`. Writes use
a private temporary file, file and directory synchronization, and atomic
rename. A process-owned exclusive writer lock prevents two OpenCode processes
from reading the same generation and losing one owner's update. A live writer
makes a new dispatch fail closed; a lock is reclaimed only after its 30-second
lease is stale and its recorded process is dead. Malformed or ambiguous locks
are never deleted automatically. The gate-facing
`make check-dispatch-dedup` validates every fingerprint, transition field, task
ID list, attempt count, and blocked-duplicate count. If the ledger has never
been created it reports `INACTIVE`; it does not misreport missing evidence as a
clean history. A malformed ledger fails closed.

Untracked work still receives exact normalized-content deduplication. Release
and backlog work should include its stable task ID so ownership also survives
prompt rewording. The ledger is Gludd-owned state under the ignored `.gludd/`
directory, so it survives agent restarts without entering commits or colliding
with application source. Updating the enforcement plugin requires an OpenCode
restart; the currently loaded version remains available until the replacement
is validated and restarted, preserving zero-downtime agent operation. Rollback
restores the prior plugin and checker together rather than deleting ledger
evidence.

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

These reports do not establish universal performance numbers. They do show why
Gludd treats concurrency as a bounded resource and delegation as an explicit
ownership decision rather than a mandatory activity count.
