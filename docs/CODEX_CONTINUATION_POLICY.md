# Codex Continuation Policy

## Purpose and Honest Boundary

This document defines how the repository keeps a Codex-driven workflow moving
while tracked work remains. It is deliberately precise about what code can and
cannot guarantee. Repository code can intercept supported Codex lifecycle
events, inspect local state, return a continuation decision, and make progress
auditable. Repository code cannot control a host that has terminated Codex,
disabled hooks, refused hook trust, lost power, lost its runtime, or received an
explicit operator interruption. Calling the design mathematically bulletproof
would be inaccurate. The useful goal is stronger: every normal stop path is
observable, checked, and rejected when the work ledger says that work remains;
every exceptional path is recorded as an interruption rather than silently
reported as completion.

The policy therefore separates three states. A running state means an active
Codex turn, test process, gate, or supervisor owns a durable lease. A blocked
state means a stop was attempted while work, a ratchet failure, or an unsafe
workflow condition remained. A terminal state means the owner emitted a
verifiable pass or fail result and released its lease. Text such as “done” is
never treated as evidence by itself. Evidence is a hook result, a gate record,
a test result, a commit, or a durable supervisor event.

## Codex Stop Hook

The project registers `.codex/hooks.json` with a `Stop` lifecycle handler. The
handler receives the Codex event as JSON on standard input. It locates the
repository from the supplied working directory, reads `TASKS.md`, reads the
ratchet file, and counts unresolved entries. If both counts are zero, it emits
`{"continue": true}`. If either count is nonzero, it emits a JSON block decision
with a continuation reason. Codex documents that a `Stop` block creates a new
continuation prompt, so the model is returned to the event loop instead of
being allowed to finish the turn normally.

The reason includes a new `STOP CHALLENGE` token generated with a cryptographic
random source. The token is intentionally different on every stop attempt,
including attempts where Codex reports `stop_hook_active`. This prevents a
cached response, copied text, or stale state from satisfying the protocol. The
reason also states the number of outstanding task and ratchet entries and
requires the next continuation to perform tracked work and re-check the gate.

The hook entrypoint always emits valid JSON when it exits successfully. If its
input is malformed, if it cannot parse the event, or if an unexpected runtime
error occurs, it emits a block decision containing the error rather than
silently allowing the stop. This is fail-closed behavior. A hook failure must
become visible to the operator because a missing hook result is not equivalent
to permission to stop.

Project-local hooks require trust review in Codex. The operator must inspect
and trust the hook through `/hooks`. The repository cannot silently grant that
trust because Codex intentionally protects users from unreviewed commands.
The activation procedure is therefore part of release readiness: verify that
the project hook is listed, review its current hash, trust it, and run a
synthetic stop attempt while a temporary task is pending. The expected result
is a block decision and a visible challenge token. Run a second attempt and
verify that the token changes. Remove the temporary task, repeat the attempt,
and verify that the hook returns `continue: true`.

## Ledger Invariants

`TASKS.md` is the source of truth for planned work. Every implementation,
test, documentation change, infrastructure change, and release action must be
represented by a task entry. A task is not complete because a file exists. It
is complete only after the required tests, lint, collection, and gate evidence
are attached to the entry. `config/ratchet.yml` records known failures that
must remain visible until fixed. The stop hook treats either an unchecked task
or a ratchet entry as pending work.

The repository guard provides a second, command-line-compatible path. It emits
a challenge, records counts and timestamps in a namespaced audit file, and
returns a nonzero status while work remains. A separate confirmation operation
requires the exact latest token and a clean ledger. Corrupt challenge state is
rejected without crashing. This guard is useful to CI, wrappers, and human
operators; it does not pretend to be a replacement for the Codex lifecycle
hook.

The ledger is validated before commits. Task integrity checks ensure modified
paths map to registered tasks. Duplicate Make targets are rejected. These
checks prevent an agent from creating an untracked side project and then
claiming that the tracked work is complete.

## Durable Execution and Resumption

Long-running E2E work is supervised by a durable per-file state machine. Each
file starts in pending state and transitions to running, passed, failed,
skipped, or timed out. State is written atomically so an interrupted process
cannot leave a partially written record that looks successful. The supervisor
stores the revision, shard identity, file index, timestamps, and outcome. On
restart it refuses to reuse results from a different revision and resumes at
the first incomplete or failed file. Completed files are not rerun unless the
revision changes.

Every supervisor emits heartbeats at a bounded interval. A watchdog monitors
both total runtime and time since the last progress event. A process that stops
producing output is terminated with a visible stalled result. A process that
reaches its maximum runtime is terminated with a timeout result. The parent
never waits forever on a child with no observable event. Logs, status JSON, and
the final result identify the exact shard and revision.

Shards are deterministic. Sorted test files are partitioned into disjoint
groups, and each shard receives its own lock, basetemp directory, progress
file, and log. A shard can be retried without restarting unrelated shards. The
aggregate report records attempted, passed, failed, skipped, and remaining
counts. An interrupted aggregate is explicitly incomplete; it cannot be
mistaken for a complete coverage result.

## Process and Resource Safety

All heavy operations use a project namespace. Gate locks, E2E locks, model
leases, SearX leases, Terraform leases, and temporary directories are scoped to
that namespace. A stale owner is reclaimed only after PID identity and command
identity checks. A live owner is never killed merely because another process
wants the resource. Worker admission is bounded, and duplicate leases are
reported as an invariant violation.

Watchdogs and cleanup commands are similarly namespaced. Cleanup targets must
prove that a process belongs to the current Gludd worktree before signalling
it. External credentials, especially SSH keys, are outside the repository and
are never removed by cleanup or staging targets. These rules prevent the agent
from “fixing” a stuck test by destroying another project or a user credential.

The asynchronous gate launcher writes `RUNNING` before starting the child,
executes the production gate through Bash, records `PASS` or `FAIL` after the
child exits, and releases its lock in both success and failure paths. Its
default command is tested directly because test-only command overrides do not
prove that the production launcher works.

## Testing Strategy

Behavioral tests are written before fixes whenever a failure is discovered.
The tests cover both pure helpers and the actual command entrypoints. Codex
hook tests cover clean and pending ledgers, nested working directories,
ratchet-only blockers, repeated stop attempts, token uniqueness, malformed
stdin, JSON protocol shape, and corrupt state. Guard tests cover challenge
issuance, exact confirmation, token rotation, and fail-closed rejection.

Gate tests cover default command execution, pass and fail propagation, lock
contention, immediate status visibility, and status finalization. Supervisor
tests cover resume after failure, skipping completed files, revision changes,
heartbeat persistence, shard disjointness, and invalid shard coordinates.

The test suite must be collected before each commit. Lint and type checks run on
changed files and are repeated in the repository gate. A focused test passing
is necessary but insufficient; task registration, collection, and gate output
are also required. Tests are not weakened to hide a production failure. When
a provider limit, missing device, or unavailable external service prevents a
live test, the test must fail closed with an explicit reason rather than claim
that the live behavior passed.

## CI and Release Evidence

The development branch is pushed only from a clean worktree after a terminal
local gate result. CI verification binds the result to the exact commit SHA.
A successful run for an older SHA is not evidence for the current checkout.
Cancelled, skipped, stale, missing, or failed runs are all non-green. A new
run is started only after the current commit is present on the remote.

Release promotion requires task evidence, clean worktrees, a green gate, exact
SHA CI evidence, and release smoke tests. A stale `.gate-status` file cannot be
used as a pass claim. If a gate owner is stale, the stale owner is reclaimed,
the lock is removed, and a fresh gate is started. The recovery itself is logged
so the history explains why a prior run was not trusted.

## Stop Conditions and Host Limits

The policy distinguishes a normal model decision from an external termination.
The normal decision is intercepted by the Codex `Stop` hook. A host crash,
process kill, power loss, network failure, hook disablement, hook trust refusal,
or user cancellation is outside the repository. The correct response to these
events is durable resumption and an incomplete status, not a claim that the
agent continued invisibly.

No prompt can force a model to execute code after the host has stopped it. No
Make target can revive a process that no longer exists. No random token can
authenticate a stop hook that Codex never loaded. These are architectural
limits, not missing enthusiasm. The code therefore makes ordinary stops
expensive and visible, makes failures resumable, and makes false completion
claims fail validation.

## Operational Contract

Before editing, read the task ledger and ratchet. Before committing, run the
focused tests, collection, lint, task-integrity, and duplicate-target checks.
Before claiming completion, obtain a terminal gate result and exact-SHA CI
evidence. During long work, emit heartbeats and inspect resource ownership at
natural checkpoints. If a tool call fails, record the failure, write a
regression test, fix the code, and rerun the smallest affected shard before
resuming the broader gate.

This contract does not make the agent immortal. It does make the normal
workflow deterministic, observable, restartable, and resistant to premature
stopping. That is the strongest guarantee available without changing Codex
itself or deploying a separately managed supervisor outside the host.

## Evidence, Recovery, and Human Review

The final protection is evidence discipline. Every continuation decision should
leave enough information for another terminal, another operator, or a later
Codex session to reconstruct what happened. A challenge record includes a
timestamp, revision, task count, ratchet count, and session context. A blocked
hook response includes the challenge and a concrete reason, not an ambiguous
instruction such as “keep going.” A supervisor heartbeat identifies the shard,
file index, total files, current test, and last progress time. A gate status
identifies the process owner and the phase currently executing. These records
make a silent stop distinguishable from a completed run.

Recovery follows a fixed order. First, verify whether the owner process is
alive and whether its command still belongs to this project. Second, inspect
the durable status and the latest heartbeat. Third, preserve logs and state
before removing a stale lock. Fourth, resume from the first incomplete unit,
not from the beginning of the entire suite. Fifth, run the smallest regression
that reproduces the failure. Sixth, commit the fix with task evidence. Only
after that focused result is green should the broader shard or gate resume.
This order prevents a recovery action from destroying the evidence needed to
understand the original failure.

Human review remains an intentional part of the system. Trusting a Codex hook,
choosing a provider credential, approving a destructive cleanup, and promoting
a release are authority decisions. Automation can validate scopes, hashes,
ownership, and test results, but it should not silently invent operator
approval. The workflow therefore exposes the exact command, path, revision,
and reason for each approval boundary. If review is unavailable, the safe
result is a pending state, not a fabricated pass.

The policy is also designed for repeated sessions. A new session begins by
reading the task ledger, ratchet, session notes, gate state, and durable E2E
progress. It does not rely on conversational memory or an earlier assistant's
claim. If a previous session stopped unexpectedly, the next session sees the
unfinished task and the incomplete run, regenerates a challenge when it tries
to stop, and resumes the recorded work. This is how the repository reduces the
impact of an unreliable agent loop without pretending to own the Codex host.

Finally, every claimed improvement must be tested against the failure it was
intended to prevent. A hook change needs an actual stdin-to-JSON behavioral
test. A gate change needs a production-default launcher test. A resource fix
needs competing owners and stale-owner cases. A coverage change needs an
interrupted audit and a resumed audit. This requirement closes the gap between
code that looks protective and code that has demonstrated the protection under
the same conditions that caused the incident.
