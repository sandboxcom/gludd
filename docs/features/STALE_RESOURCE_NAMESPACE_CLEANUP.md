# Stale resource namespace cleanup

Gludd stores checkout-scoped scratch state below a `gludd-resources` root. The
`clean-stale-resource-namespaces` target inventories those directories and can
reclaim only namespaces that are old enough and proven inactive. Inventory is
the default; deletion is an explicit second mode.

## Safety and ZDD contract

The cleaner treats a namespace and its optional `-toolchain` directory as one
ownership unit. It protects the invoking checkout, every Git worktree, and all
worktrees in the active-workstream registry. It then requires all of these
conditions before deletion:

- the root is an exact, owned, non-symlink directory named `gludd-resources`;
- the candidate is a canonical direct child with a 12-hex checkout digest;
- every descendant is owned, readable, non-symlink, and not a hard link;
- the newest descendant is older than the bounded grace period;
- kernel lock inspection proves that no lock is held;
- every PID marker is dead or has a start identity proving PID reuse; and
- a second inventory has the same root, directory inodes, and tree digests.

Missing process census data, corrupt registries, live owners, ambiguous PID
records, changed paths, excessive inventory size, and inspection errors all
preserve data. Apply mode emits progress while recursive deletion is active and
appends one fsynced JSON receipt per removed path outside the cleanup root.

These rules preserve zero-downtime development: cleanup never signals a
process, removes a registered/current namespace, or interrupts an active gate.
It only removes isolated scratch state after activity has been disproved twice.

## Operator workflow

Run a validate-only inventory first:

```console
make clean-stale-resource-namespaces STALE_RESOURCE_NAMESPACE_ROOT=$HOME/tmp/gludd-resources STALE_RESOURCE_NAMESPACE_PROJECT_ROOT=. STALE_RESOURCE_NAMESPACE_REGISTRY= STALE_RESOURCE_NAMESPACE_RECEIPT=$HOME/tmp/gludd-resources.cleanup-receipts.jsonl STALE_RESOURCE_NAMESPACE_GRACE_SECONDS=86400 STALE_RESOURCE_NAMESPACE_MAX_CANDIDATES=1000 STALE_RESOURCE_NAMESPACE_MAX_ENTRIES=100000 STALE_RESOURCE_NAMESPACE_HEARTBEAT_SECONDS=5 STALE_RESOURCE_NAMESPACE_VALIDATE_ONLY=1
```

After reviewing every `reclaim` decision, repeat the exact inputs with
`STALE_RESOURCE_NAMESPACE_VALIDATE_ONLY=0`. Apply mode re-runs ownership and
identity checks before each deletion. A nonzero result means a race or unsafe
observation was preserved and should be investigated, not manually bypassed.

## Long-lived operator reports considered

- A [GitHub Actions runner report](https://github.com/actions/runner/issues/4357)
  shows cleanup from an exiting worker deleting the next worker's live shared
  temp files. That failure motivates per-checkout namespaces, paired ownership,
  and protection of every current worktree before considering age.
- An [OpenClaw cleanup report](https://github.com/openclaw/openclaw/issues/165035)
  demonstrates live files being unlinked when process census failed. Gludd
  therefore treats an unavailable census as a global cleanup refusal.
- A [Codex sandbox report](https://github.com/openai/codex/issues/31599) describes
  stale owner markers after abrupt termination and calls out inode-replacement
  races. Gludd records device/inode/tree identities and revalidates them at
  apply time.
- A [Hermes gateway report](https://github.com/NousResearch/hermes-agent/issues/13655)
  documents both stale PID records and PID reuse. Gludd compares PID start
  identity and preserves live PIDs when the marker lacks enough identity data.
- The older [systemd tmpfiles hard-link discussion](https://github.com/systemd/systemd/issues/7736)
  documents recursive cleanup hazards beyond symlinks. Gludd refuses linked
  regular files instead of changing or removing them.

The reports span several cleanup implementations and years, so the feature
encodes their recurring failure modes as tests rather than relying on operator
judgment during disk pressure.
