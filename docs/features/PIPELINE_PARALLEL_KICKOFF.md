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
   already-running agents. The plugin does not parse a second backlog or invent
   work.
3. Candidates are deduplicated, dependency-ready, and pairwise file-disjoint,
   following the repository `OrchestrationPlanner` resource contract. Polling,
   status watching, placeholders, and other filler are discarded.
4. The batch is capped at three total workers, including the in-flight estimate.
   An empty batch is valid and never creates quota-filling tasks.
5. Every emitted prompt names the frozen ref and checkout and requires a new
   isolated git worktree. While the pipeline is active, writes and non-read-only
   Make targets in the tested checkout are rejected. Duplicate dispatch keys are
   rejected across task/agent/workflow tools.
6. A terminal gate/ship receipt unfreezes the checkout. State also expires after
   four hours so a missing receipt cannot wedge the workspace indefinitely.

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

## Operator controls

- `GLUDD_PIPELINE_KICKOFF_ENFORCE=0` disables the plugin fail-open.
- `GLUDD_PIPELINE_TESTED_REF` supplies a deterministic ref for harnesses.
- `GLUDD_PIPELINE_STATUS_PATH` overrides the terminal receipt path.
- `GLUDD_PIPELINE_KICKOFF_MAX_AGE_MS` changes the four-hour stale-state limit.

Plugin source is loaded at OpenCode startup. Restart OpenCode after installing
or changing this plugin before relying on live enforcement.
