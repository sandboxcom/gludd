# Enforcement Process Isolation

## Contract

OpenCode enforcement hooks are control-plane policy checks. Importing a plugin,
constructing its hooks, or evaluating a hook must never start a gate, test suite,
daemon, watcher, or other child process.

Long-running project work must instead be started through an explicit namespaced
Make target that:

- prints continuous progress or heartbeats;
- records ownership and parent/child process identity;
- has a bounded timeout and cleanup path; and
- can be distinguished from work belonging to another checkout or project.

This boundary keeps policy evaluation cheap and deterministic, prevents a plugin
reload from multiplying work, and lets operators decide when system capacity is
available for a gate.

## Incident and fix

Both `enforce-multitask.ts` and `enforce_stop_impl.ts` previously called
`spawnGateRefresh()` from their plugin factories. Every import could therefore
launch `make gate-refresh` as a detached, ignored-stdio child and immediately call
`unref()`. Repeated runtime verification imports accumulated independent gate
trees. Because their output was discarded, the trees were invisible until host
load exceeded available CPU capacity.

The plugins now perform no process creation. Gate freshness is observed from
existing state; refreshing it remains an explicit, observable Make operation.
`test_enforcement_gate_spawn_safety.py` scans every TypeScript enforcement source
and structurally pins this rule. The live hook-runtime test pins the adaptive
configured-minimum message that replaced the old fixed-floor wording.

## Operator evidence

The behavior was not a Gludd-specific Node quirk. Long-lived operator discussions
describe the same lifecycle boundary:

- [NodeJS child process not exiting after calling unref](https://stackoverflow.com/questions/72457656/nodejs-child-process-not-exiting-after-calling-unref)
  documents the surprise that an unreferenced detached child can continue after
  its launcher has otherwise finished.
- [How to test that a spawned Node child process is detached and unrefed](https://stackoverflow.com/questions/69860108/how-to-test-that-spawned-node-child-process-is-detached-and-unrefed)
  illustrates why independence from the parent is difficult to test through
  ordinary parent-process assertions.
- [NodeJS execute command in background and forget](https://stackoverflow.com/questions/25323703/nodejs-execute-command-in-background-and-forget)
  shows that `detached` plus `unref()` is the established recipe for deliberately
  making a child outlive its launcher—the opposite of enforcement-hook ownership.
- [nodejs/node#21825](https://github.com/nodejs/node/issues/21825) records
  platform-specific detached-child behavior, reinforcing that policy hooks should
  avoid relying on detached process semantics.

The design conclusion is deliberately stronger than trying to tune process flags:
plugin evaluation is process-pure, while project orchestration remains explicit
and observable.

## Verification

The acceptance sequence is:

1. Run `make test-files TESTFILES='tests/unit/test_enforcement_gate_spawn_safety.py'`.
2. Run `make test-hook-runtime`.
3. Run `make verify-enforcement`.
4. Run `make ps` and confirm that verification left no project process behind.

Because OpenCode loads plugin entrypoints at startup, editing this plugin requires
an OpenCode restart before the running session uses the new implementation.
