# Pipeline Parallel Kickoff

Long-running validation is now a kickoff boundary, not a reason for the main
checkout to sit idle. When an agent launches `gate-async`, `gate-background`,
`gate-all-background`, `ship-async`, or `test-ci-shards-parallel-bg`, the
pipeline-kickoff plugin records the exact checkout and commit under test,
freezes that checkout against writes, and emits a bounded batch of independent
work that can proceed in isolated worktrees.

## Runtime contract

1. The pipeline is launched first. `gate-async` and `ship-async` receive an
   explicit `REF=<HEAD>` when the caller omitted one.
2. Candidate work is read from the existing todo, force-dispatch, and dispatch
   preflight state. Existing multitask state reduces the available slots for
   already-running agents, and capacity is refreshed before every dispatch so
   a worker started after kickoff still consumes a slot. The plugin does not
   parse a second backlog or invent work.
3. If `TASKS.md` declares one active release milestone as an exact task range,
   source records are admitted only when every explicit task ID maps to a
   currently open parent task in that range. Nested task IDs inherit their
   parent task's scope. Later-release, completed, backlog, mixed-scope, and
   unscoped records are excluded. Conflicting, malformed, or unreadable release
   metadata fails closed to zero candidates. Once every task in a valid
   historical milestone is checked with a compatible completion status, the
   milestone is inactive and ordinary repository-wide candidate behavior
   resumes; repositories with no versioned milestone use that behavior too.
4. Candidates are deduplicated, dependency-ready, and pairwise file-disjoint,
   following the repository `OrchestrationPlanner` resource contract. Polling,
   status watching, placeholders, and other filler are discarded.
5. The batch is capped at three total workers, including the in-flight estimate.
   An empty batch is valid and never creates quota-filling tasks.
6. The emitted candidate batch is digest-bound at kickoff. Only an exact emitted
   prompt may be dispatched; changing its objective, paths, task label, or receipt
   state fails closed. Content hashes reject the same task under a different ID.
7. Every emitted prompt names the frozen ref and checkout and affirmatively
   requires creation and use of a new isolated git worktree. Negated or merely
   descriptive worktree language is rejected. While the pipeline is active,
   writes and non-read-only Make targets in the tested checkout are rejected.
8. A terminal receipt unfreezes only its own checkout. Its path must remain under
   the frozen checkout, its content must differ from the pre-launch baseline,
   gate receipt epochs must not predate kickoff, and ship success must name the
   exact tested ref. The last receipt line is authoritative, so an older success
   cannot mask a newer failure. State also expires after four hours.

The machine-readable receipt is stored at a checkout-namespaced
`/tmp/gludd-pipeline-kickoff-<digest>.json` (override with
`GLUDD_PIPELINE_KICKOFF_STATE`). The tool result also receives a
`metadata.pipelineKickoff` object plus a human-readable candidate list.

## User reports that shaped the behavior

- [Codex issue #41007](https://github.com/openai/codex/issues/41007) reports an
  agent repeatedly polling a long-running build/deployment instead of advancing
  independent work. This is why kickoff exposes work immediately and rejects
  wait/status filler.
- [Codex discussion #3898](https://github.com/openai/codex/discussions/3898)
  recommends one branch/worktree per writing worker and warns against multiple
  workers editing one tree. This is why the checkout under test is frozen and
  every dispatch prompt carries the isolation boundary.
- [Codex issue #38989](https://github.com/openai/codex/issues/38989) documents
  runaway delegation and repeated verification. This is why the batch has a
  hard three-agent cap, subtracts in-flight work, and uses stable deduplication
  keys.
- [Codex issue #28701](https://github.com/openai/codex/issues/28701) shows how a
  terminal can silently open the base checkout instead of the active worktree.
  This is why receipts and prompts include the absolute frozen checkout path,
  not just a branch name.
- [Codex issue #48184](https://github.com/openai/codex/issues/48184) records an
  uninterrupted long-running task switching to unrelated invented work and asks
  the runtime to preserve an explicit task pointer and reject unauthorized scope
  changes. The active milestone range is that durable pointer at kickoff.
- [Codex issue #23937](https://github.com/openai/codex/issues/23937) has remained
  reproducible across multiple releases and reports unrelated sessions appearing
  in the current agent tree. It reinforces filtering shared orchestration state
  by explicit ownership instead of assuming every visible record belongs here.
- [OpenCode issue #41358](https://github.com/anomalyco/opencode/issues/41358)
  includes multiple reporters of goal drift after long-session compaction and
  recommends retaining the original goal across the continuation boundary. The
  frozen milestone receipt provides that stable scope even if later state files
  accumulate more work.
- [OpenCode issue #39987](https://github.com/anomalyco/opencode/issues/39987)
  records that plugin/config changes may require an explicit reload or the next
  process launch. This is why activation below distinguishes registration from
  a hot-module refresh instead of treating a source edit as live automatically.
- [OpenCode issue #42898](https://github.com/anomalyco/opencode/issues/42898)
  reports a plugin hot reload leaving the server unavailable until its background
  service was restarted. This is why the compiled default remains the fallback
  and operators verify the generated module before relying on it.

## Activation and live verification

Adding the plugin to `opencode.json` installs registration only; it does not add
hooks to an already-running process. **Restart OpenCode** after initial install or
any registration change, then invoke a harmless tool call so the plugin records
its `reportAlive` heartbeat.

Once the wrapper is already registered in the running process, source-only
changes can be activated through its fail-safe hot module:

```console
make hot-reload-plugins
make hot-reload-status
make test-hook-runtime
```

`make hot-reload-status` must show `gludd-hot-pipeline-kickoff.js`. A missing or
invalid module falls back to the startup-loaded implementation; it is not proof
that the edit is live. If the module is absent, the runtime test fails, or live
behavior still reflects the old implementation, Restart OpenCode and re-run the
narrow pipeline-kickoff tests before depending on the freeze.

## Active milestone ZDD and rollback

Milestone selection is a read-only pre-launch admission step. It reads the task
ledger and the three existing candidate sources, writes no source queue, and
persists the selected scope and counts with the new kickoff receipt. A running
pipeline and its already frozen batch are never rewritten; the next kickoff
adopts the new selector without stopping a gate, service, listener, or worker.
The scope receipt is launch-digest-bound alongside the exact candidate batch.
When the last active milestone task completes, the next kickoff returns to
repository-wide selection instead of retaining a historical release freeze.

For a source-only rollout, run `make hot-reload-plugins`, confirm
`gludd-hot-pipeline-kickoff.js` with `make hot-reload-status`, and execute
`make test-hook-runtime`. The bundle includes both the plugin and its selector
library. If the wrapper was not already registered, restart OpenCode once as
described above. Rollback restores the prior plugin and library together and
rebuilds the hot module; an existing immutable kickoff receipt remains usable
until its own terminal evidence or expiry. `GLUDD_PIPELINE_KICKOFF_ENFORCE=0`
is the emergency fail-open control, but it also removes checkout freezing and is
not a substitute for reverting a bad selector.

## Operator controls

- `GLUDD_PIPELINE_KICKOFF_ENFORCE=0` disables the plugin fail-open.
- `GLUDD_PIPELINE_TESTED_REF` supplies a deterministic ref for harnesses.
- `GLUDD_PIPELINE_STATUS_PATH` overrides the terminal receipt path.
- `GLUDD_PIPELINE_KICKOFF_MAX_AGE_MS` changes the four-hour stale-state limit.

The state receipt is diagnostic evidence, not permission to mutate the frozen
checkout. Only a validated terminal pipeline receipt ends the freeze.
