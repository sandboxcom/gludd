# OpenCode enforcement executable modes

## Contract

Every active OpenCode enforcement plugin and each source surface loaded by its
runtime harness is committed with Git mode `100755`. The inventory is derived
from the `opencode.json` plugin array plus these supporting groups:

- `.opencode/lib/*.ts`
- `.opencode/plugin/impl/*.ts`
- `.opencode/plugin/*.test.node.mjs`

`tests/unit/test_enforcement_executable_modes.py` reads the candidate Git index,
not `os.access()` or the current filesystem mode. This pins the metadata that a
commit and a fresh checkout will carry even when the local filesystem cannot
report Unix execute bits reliably.

The mode is deployment metadata, not a substitute for runtime verification.
OpenCode still imports TypeScript through its loader, and `make test-hook-runtime`
plus `make verify-plugin-manifest` remain the behavioral and registration proofs.

## Practitioner evidence

This is a long-lived cross-platform failure mode rather than a one-host anomaly:

- A [Windows/Linux Git user report](https://stackoverflow.com/questions/29900526/git-how-to-maintain-executable-bit-between-linux-and-windows)
  describes an executable bit disappearing after a Windows edit and asks whether
  `core.fileMode=false` preserves it. It does not create the missing committed
  mode; the repository index remains the durable boundary.
- A [2025 Git user report](https://stackoverflow.com/questions/79696189/core-filemode-is-true-but-git-still-not-tracking-executable-bit)
  shows how staging and then using an all-changes commit path can overwrite the
  intended mode. Checking the candidate index catches that exact class of drift.
- An [OpenCode plugin issue](https://github.com/anomalyco/opencode/issues/7006)
  reports a plugin that was detected, loaded, and initialized without its hook
  behavior firing. That is why this metadata check deliberately does not claim
  functional enforcement; the runtime hook suite remains mandatory.

## ZDD rollout and rollback

The normalization changes only Git mode metadata. Existing OpenCode processes
continue using the already loaded source while the commit is prepared and
validated. A new checkout receives the complete mode set atomically, without a
partially rewritten plugin graph or a service interruption. Because source
bytes do not change, no OpenCode restart is required for this mode-only rollout.

Rollback is a normal revert of the mode-only commit. The prior process remains
available throughout, and the focused mode test makes any partial rollback
visible before promotion.
