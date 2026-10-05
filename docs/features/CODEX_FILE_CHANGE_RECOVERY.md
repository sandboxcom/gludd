# Codex File-Change Recovery

## Purpose

Parallel agents must edit isolated branches and worktrees. If an integration
process nevertheless replaces uncommitted files in the canonical checkout,
Gludd can replay a bounded set of completed Codex `fileChange` records from the
read-only thread projection. The replay validates every event in a temporary
tree before publishing any file, so a late malformed patch cannot leave a
half-restored checkout.

This is recovery, not a substitute for worktree isolation. New write-capable
tasks still start with `make agent-worktree` and integrate sequentially.

## Practitioner evidence

Long-lived public reports show the failure is not unique to this repository:

- [Codex issue #23095](https://github.com/openai/codex/issues/23095) reports
  workers starting in the coordinator checkout and accidentally editing the
  wrong tree when an explicit subagent work directory cannot be bound.
- [Codex issue #31572](https://github.com/openai/codex/issues/31572) reports
  branch drift and shared mutable state across desktop subagents.
- [Codex issue #8643](https://github.com/openai/codex/issues/8643) documents
  loss of uncommitted work after a destructive restore operation.
- [Codex discussion #3898](https://github.com/openai/codex/discussions/3898)
  recommends one worktree per write-capable worker and sequential supervisor
  integration rather than multiple writers in one checkout.

These reports support two controls: isolate writers before editing, and retain
a deterministic recovery path for already-durable model events.

## Operator contract

First validate the exact thread and ordinal range without changing files:

```console
make replay-codex-file-changes \
  CODEX_REPLAY_DB=/path/to/thread_history_1.sqlite \
  CODEX_REPLAY_RECORDED_REPO=/path/to/original/checkout \
  CODEX_REPLAY_THREAD_ID=<thread-uuid> \
  CODEX_REPLAY_START=<first-ordinal> \
  CODEX_REPLAY_END=<last-ordinal> \
  CODEX_REPLAY_APPLY=0 \
  CODEX_REPLAY_VALIDATE_ONLY=0
```

Review the bounded event and file counts, then repeat with
`CODEX_REPLAY_APPLY=1`. The target refuses paths outside the recorded repository,
symlink targets, incomplete events, unsupported change kinds, invalid patch
context, and empty ranges.

## Safety properties

- SQLite opens in read-only mode.
- Thread ID and inclusive ordinal bounds select the exact recovery batch.
- Recorded absolute paths are mapped relative to the original checkout, then
  confined to the destination worktree.
- All adds, updates, and deletes replay in a temporary tree first.
- Destination files are replaced only after the whole batch validates.
- Validation mode performs no destination writes.
- The receipt lists the event count and every affected relative path.

The unit suite pins path confinement, alternate-checkout mapping, dry-run
behavior, ordered add/update replay, and no-write behavior after a late patch
failure.
