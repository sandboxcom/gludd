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

## Loader-surface isolation

An enforcement plugin is also a loader boundary. OpenCode's legacy discovery
iterates the runtime exports of auto-discovered plugin modules, so raw named
constants or objects can be mistaken for plugin factories and prevent startup.
`enforce-multitask.ts` therefore exports only its default factory. Its validated,
test-visible configuration lives in `.opencode/lib/multitask_config.ts`, outside
the auto-discovered directory, and runtime tests exercise the public factory
rather than exporting an internal implementation or reset hook.

This failure class is visible in long-lived operator reports even when the exact
bad export differs. [OpenCode issue #8006](https://github.com/anomalyco/opencode/issues/8006)
records plugin packaging/ESM exports making installed plugins unloadable, while
[OpenCode issue #7810](https://github.com/anomalyco/opencode/issues/7810)
records malformed extension configuration preventing launch without a useful
diagnostic. These reports support a fail-fast build-time loader check and a
minimal runtime export surface; they do not claim those upstream reports had
Gludd's exact named-constant cause.

## Adaptive delegation contract

Delegation has no implicit mandatory minimum. A minimum becomes active only when
an operator explicitly sets `GLUDD_MIN_DISPATCHES` or
`GLUDD_MULTITASK_MIN_DISPATCHES`; the first variable takes precedence when both
are present. The shared parser recommends ten for an explicit setting without a
valid integer, but the runtime requirement is zero when neither variable exists.

Configuration is bounded before enforcement:

- `GLUDD_MULTITASK_MAX_DISPATCHES` can lower the per-message ceiling, but cannot
  raise it above the absolute project ceiling of ten or lower it below one.
- An explicit minimum is clamped to the configured ceiling, so contradictory
  settings cannot create an impossible dispatch requirement.
- `GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD`,
  `GLUDD_CONSECUTIVE_NON_DISPATCH_WINDOW_MS`, and `GLUDD_MSG_GAP_MS` tune the
  mutation-grinding and message-boundary detectors.
- Read-only `read`, `grep`, and `glob` operations do not increment the mutation
  streak. Dispatches reset that streak, while edit/write/bash mutations remain
  subject to an active configured minimum and pending-work checks.
- `GLUDD_MULTITASK_FLOOR_ENFORCE=0` disables minimum and grinding policy, but
  never disables the absolute dispatch ceiling or the independent stop and
  security plugins.

The implementation source of truth is
`.opencode/lib/multitask_config.ts`. Tests combine that module with the plugin
entrypoint so loader isolation is retained without duplicating configuration
values in the runtime-discovered module.

## Verification

The acceptance sequence is:

1. Run `make test-files TESTFILES='tests/unit/test_enforcement_gate_spawn_safety.py'`.
2. Run `make check-plugin-hooks`.
3. Run `make test-multitask-node`.
4. Run `make test-hook-runtime`.
5. Run `make verify-enforcement`.
6. Run `make ps` and confirm that verification left no project process behind.

Because OpenCode loads plugin entrypoints at startup, editing this plugin requires
an OpenCode restart before the running session uses the new implementation.
