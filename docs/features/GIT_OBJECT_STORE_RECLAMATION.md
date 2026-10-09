# Git object-store reclamation

Gludd provides a validate-first, branch-neutral path for measuring and
repacking the current repository's shared Git object store. It delegates object
interpretation and maintenance to Git itself; it does not parse packs, rewrite
history, move branches, edit files, or delete `.git` paths directly.

## Operator contract

Inventory the canonical checkout without mutation:

```text
make git-object-store-reclaim GIT_OBJECT_STORE_REPOSITORY=. GIT_OBJECT_STORE_CONFIRM= GIT_OBJECT_STORE_PRUNE_DAYS=30 GIT_OBJECT_STORE_TIMEOUT_SECS=900 GIT_OBJECT_STORE_KILL_AFTER_SECS=10 GIT_OBJECT_STORE_HEARTBEAT_SECS=5 GIT_OBJECT_STORE_VALIDATE_ONLY=1
```

The target resolves the repository top level and shared Git directory, reports
the object-directory allocation, and emits Git's own `count-objects -vH`
inventory. Validate-only mode exits before taking logical-state fingerprints or
starting maintenance.

Apply mode changes only two inputs:

```text
GIT_OBJECT_STORE_CONFIRM=RECLAIM-GIT-OBJECTS GIT_OBJECT_STORE_VALIDATE_ONLY=0
```

The prune grace must remain at least 30 days. Maintenance runs as a foreground
`git gc --no-detach` child with periodic PID, elapsed-time, and object-store
size heartbeats. The target never uses `--aggressive`, `--force`, or
`--prune=now`. A deadline sends TERM and then, after a bounded grace, KILL only
to the owned Git process; the child is always reaped.

Before maintenance, the target fingerprints HEAD, its symbolic attachment,
all logical refs, index changes, tracked worktree changes, porcelain status,
and every registered worktree. Reflog expiry, physical ref packing, worktree
metadata pruning, and rerere expiry are disabled for the operation. Apply mode
is successful only when every fingerprint is unchanged and Git's
`fsck --connectivity-only --no-dangling` post-check passes. The final inventory
reports before, after, and reclaimed KiB without claiming that packed-object
layout is stable.

## Zero-downtime operation and rollback

Git object maintenance is control-plane storage work and does not restart or
replace Gludd services. Run validate-only inventory while normal work
continues. Schedule apply mode after active Git writers and release mutations
have quiesced: Git's age grace reduces concurrent-write risk but cannot
eliminate it. Existing worktrees stay usable, and dirty or untracked operator
content is accepted and proved unchanged.

The 30-day floor and disabled reflog expiration retain recent recovery material
instead of maximizing reclaimed bytes. Reachable history is not a rollback
mechanism: it is an invariant. If Git reports corruption or any logical-state
change, stop and restore the repository from its remote or independent backup;
do not rerun with a shorter grace or force flags. Objects that were genuinely
unreachable and older than the configured grace are intentionally
irrecoverable after successful reclamation.

## Upstream and practitioner evidence

Evidence reviewed 2026-10-09:

- Git's official [`git count-objects` documentation](https://git-scm.com/docs/git-count-objects.html)
  defines the mature loose/packed object and garbage inventory used here.
- Git's official [`git gc` documentation](https://git-scm.com/docs/git-gc)
  warns that immediate pruning increases concurrent-writer corruption risk,
  documents a two-week default grace, and explains that refs, the index, and
  reflogs retain objects. Gludd uses a longer 30-day floor, keeps reflogs, and
  explicitly warns operators to quiesce writers.
- Git's official [`git fsck` documentation](https://git-scm.com/docs/git-fsck)
  defines the object connectivity and validity post-check used after apply.
- A long-lived [Codex practitioner report](https://github.com/openai/codex/issues/29388)
  describes checkpoint blobs growing a Git object store beyond 100 GB and
  recommends `count-objects` as an early tripwire. The report also suggests
  immediate pruning as a workaround; Gludd deliberately rejects that risky
  form and preserves a conservative recovery window.
- An [OpenCode practitioner report](https://github.com/anomalyco/opencode/issues/17397)
  records temporary disk amplification during a multi-gigabyte Git GC. This is
  why apply mode exposes live object-store size, remains foreground and
  bounded, and must not be started without adequate headroom.
