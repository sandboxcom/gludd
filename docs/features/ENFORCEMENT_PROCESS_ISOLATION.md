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

## Shared-state test isolation

Some compatibility paths still use machine-global `/tmp/gludd-*` state. Tests
that snapshot and restore those paths are safe relative to a live OpenCode
session, but independent xdist workers were not safe relative to one another:
one worker could restore absence by unlinking a file after another worker's
existence check and before its read. The resulting stepwise failure was a
`FileNotFoundError`, even though each individual suite passed in isolation.

Collection now scans each test source once. A source containing an absolute
`/tmp/gludd-*` path, or any test using `hook_plugin_env`, is assigned to the
single `enforcement-shared-state` xdist group. Historical enforcement group
names normalize to that group. Explicit groups for independent resources such
as port 8000 and namespaced hot-reload modules remain unchanged, preserving
parallelism where shared-state serialization is unnecessary.

Snapshots use `read_optional_bytes()` from `tests/unit/_hook_fixtures.py`. It
attempts the read directly and treats `FileNotFoundError` as an absent optional
file, eliminating the `exists()`/`read_bytes()` time-of-check/time-of-use
window. Other I/O failures remain visible rather than being silently converted
to absence.

### Hot-module and hook-harness isolation

Every hook-harness invocation must override all state it can read, not only the
state the individual assertion expects to touch. In particular, post-results,
text-only, release, watchdog-CI, and false-done state are per-test paths. A live
session's `lastToolWasShipping=true` must never turn a pending-work assertion
into a post-ship response.

Hot-module tests likewise build, scan, load, and remove only files under a
PID-namespaced `GLUDD_HOT_MODULE_PREFIX`. They never scan the machine-global
`/tmp/gludd-hot-*.js` set. Read-only Make-prefix tests use a non-test target;
invoking `make test-count` from inside pytest correctly exercises the independent
concurrency guard and is not an allow-list probe.

This is a durable parallel-test failure class. The seven-year-old
[pytest race report #5524](https://github.com/pytest-dev/pytest/issues/5524)
documents concurrent workers colliding while creating shared base temporary
state. The pytest-xdist maintainer's explanation in
[xdist discussion #981](https://github.com/pytest-dev/pytest-xdist/issues/981)
confirms that an `xdist_group` exists to force resource-sharing tests onto one
worker. Gludd uses both controls: namespace independent artifacts, and serialize
only genuinely shared compatibility paths.

## Project-ledger root isolation

Enforcement is also a filesystem trust boundary. A previous fallback in
`getProjectRoot()` named one developer's checkout directly. When a plugin ran
from a markerless temporary directory, it could read that unrelated checkout's
`TASKS.md`, `BUGS.md`, `config/ratchet.yml`, and gate state. Besides being
non-portable, this produced false stop decisions and exposed another project's
work metadata to the current session.

The resolver now has one portable order:

| Condition | Result |
|---|---|
| `GLUDD_PROJECT_ROOT` names an existing directory | Use it as the authoritative root, even when it contains no ledger or project marker. Relative values are resolved against the current directory. |
| The override is absent or invalid | Walk only the current directory and its ancestors for `TASKS.md`, or for the `opencode.json` + `Makefile` pair. |
| No marker is found | Stay at the current directory. Never probe a sibling, home-directory checkout, or developer-specific path. |

The cache remains keyed by the raw override and current directory, so either
changing in-process invalidates the earlier decision. An invalid override is
not permission to broaden the search: a missing path or non-directory falls
through to the same ancestor-only walk as an unset value.

### Configuration and trust boundary

`GLUDD_PROJECT_ROOT` is trusted launcher configuration. Whoever can set the
OpenCode process environment can intentionally select a root outside `cwd`, so
operators should set it to the exact worktree being controlled and should not
copy it from repository content. The override is authoritative without
requiring `TASKS.md`; an empty project must mean “no project ledger,” not
“borrow a different ledger.” Without that explicit capability, repository
markers can only widen scope to an ancestor that already contains the process.

This contract matches the direction of the current
[OpenCode plugin API](https://dev.opencode.ai/docs/plugins/), which exposes
`directory` and `worktree` as distinct plugin context rather than implying one
machine-global checkout. It also addresses a durable class of operator reports:

- A year-old [RooCode multi-root workspace report](https://www.reddit.com/r/RooCode/comments/1jsv9ne/roocode_path_issues_with_multiroot_vs_code/)
  describes cwd switching causing edits to target duplicated or parent paths.
- Microsoft’s [VS Code Python issue #18207](https://github.com/microsoft/vscode-python/issues/18207),
  opened in 2021, records a workspace-root token being interpreted from the
  wrong location in a multi-root configuration.
- The official [VS Code multi-root guide](https://code.visualstudio.com/docs/editing/workspaces/multi-root-workspaces)
  notes that extensions without multi-folder support can operate on the first
  folder, reinforcing that an implicit “default checkout” is not project
  isolation.

These reports are analogous path-scope failures, not claims that upstream tools
contained Gludd's hardcoded fallback. The design conclusion is local: project
identity must come from explicit configuration or bounded discovery, never a
developer pathname compiled into policy code.

### ZDD deployment and rollback

The change performs no state migration and starts no process. New OpenCode
sessions pick it up independently, so operators can preserve zero downtime by
restarting sessions one at a time while other sessions continue. Existing
sessions keep their loaded plugin generation until restart; editing the source
does not make the running session safe, and this change must not trigger an
automatic restart.

For rollout, set `GLUDD_PROJECT_ROOT` explicitly in each launcher when the
plugin worker's cwd may not be inside the worktree, then restart that session
and run the live hook verification. For rollback, revert the resolver commit
and restart affected sessions one at a time. No ledger or `/tmp/gludd-*` state
needs conversion; keeping a valid explicit root is the safe configuration
mitigation during either rollout or rollback.

## Verification

The acceptance sequence is:

1. Run `make test-files TESTFILES='tests/e2e/test_enforce_stop_live.py' PYTEST_ARGS=-q`.
2. Run `make test-files TESTFILES='tests/unit/test_enforcement_gate_spawn_safety.py' PYTEST_ARGS=-q`.
3. Run `make check-plugin-hooks`.
4. Run `make test-multitask-node`.
5. Run `make test-hook-runtime`.
6. Run `make verify-enforcement`.
7. Run `make ps` and confirm that verification left no project process behind.

Because OpenCode loads plugin entrypoints at startup, editing this plugin requires
an OpenCode restart before the running session uses the new implementation.
