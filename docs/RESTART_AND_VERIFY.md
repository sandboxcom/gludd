# Restart and Verify Checklist

Run this checklist after restarting opencode to confirm all plugins activate correctly.

## 1. Restart opencode

Restart your opencode session so that newly installed/updated plugins load.

## 2. Verify plugin heartbeats

```bash
make check-plugin-heartbeats
```

Confirm all plugins are firing. Expected: each registered plugin reports a heartbeat line.

## 3. Verify repository state

```bash
make verify-state
```

Confirm:
- Clean working tree (no uncommitted changes)
- Remote is synced with local HEAD

## 4. Verify `enforce-multitask.ts` blocks 1-9 dispatches

Attempt a message with 1-4 task/agent/workflow dispatches when 3+ work items are known pending. The plugin should block the message.

## 5. Verify `enforce-verified-claims.ts` blocks unverified done-words

Attempt a response containing a done-word (e.g. "done", "fixed", "green") without machine-produced evidence (commit hash, `=== GATE: PASSED ===`, `N passed`, etc.). The plugin should block the text emission.

## 6. Verify `enforce-clean-tree.ts` blocks dispatch on dirty tree

With an uncommitted change present, attempt to dispatch a subagent (Task/agent/workflow). The plugin should deny the dispatch.

## 7. Check CI state

```bash
make ci-verdict-safe
```

Review the current CI verdict for master.

## 8. Release cut (once CI is green)

Once CI returns green on master:

```bash
make release-cut TAG='v0.1.0-beta.2'
```
