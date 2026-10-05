# Import-State Isolation

## Incident

The beta4 full-order trace exposed failures that did not reproduce when each
test file ran alone. Running the core Ansible runner before the stream and
language collections left Ansible's collection finder and CLI state installed
in the pytest process. Running router registration before radio tests also
exposed two test-only assumptions: an incomplete lazy-router mock inventory and
short-name radio imports that could resolve compatibility command wrappers
instead of the packaged runtime modules.

The deterministic reproducer is deliberately bounded. It runs the core runner,
stream, and language files serially, followed by router registration and the
three radio files. The same file inventory is then run with two xdist workers.
Neither check relies on the order chosen by a larger suite.

## Isolation contract

The shared pytest guard now snapshots and restores the import registries that a
test may mutate: `sys.path`, `sys.meta_path`, `sys.path_hooks`,
`sys.path_importer_cache`, and `sys.argv`. Its module restoration is narrow: it
restores replaced module objects and removes only explicitly isolated aliases.
It does not unload every production module imported during a test.

Path-based compatibility tests use one isolated loader that registers a unique
temporary module name only while its module body executes. Radio runtime tests
otherwise import packaged collection modules by FQCN, and CLI argument changes
use pytest's scoped monkeypatch support. A structural self-test rejects new
direct path mutation, short-name dynamic imports, and unscoped `sys.argv`
assignment in the affected collection tests.

Inline Ansible execution has a matching production boundary. Each invocation
restores collection-finder, import-hook, CLI, and Ansible environment state in a
`finally` path. It also closes the task queue manager's connection-lock file and
closes and joins the timeout queue, preventing descriptor leaks in long-lived
workers. Cleanup failures are surfaced instead of reporting a successful run.

## Practitioner evidence

Pytest discussion
[#13353](https://github.com/pytest-dev/pytest/discussions/13353), opened
2025-04-04, records an import that fails only after another plugin test runs.
The maintainer response explains that pytester itself uses localized
`sys.modules` snapshots, and the reporter confirms that extending the isolated
module set repairs the failure. Reviewed 2026-08-20, this closely matches the
ordered-import symptom while supporting targeted restoration rather than a
blanket module purge.

Pytest issue [#4576](https://github.com/pytest-dev/pytest/issues/4576), opened
2018-12-25, documents longstanding practitioner confusion in suites that mix
`mock.patch` and pytest monkeypatch lifecycle styles. Gludd therefore centralizes
the process-state lifecycle in one autouse guard and uses scoped monkeypatching
at call sites instead of relying on each test author to restore globals by hand.

Pytest-xdist issue
[#981](https://github.com/pytest-dev/pytest-xdist/issues/981), opened
2023-12-05, includes a maintainer clarification that grouping forces tests onto
the same worker and serializes them. It does not clean shared interpreter state.
Gludd keeps grouping as a scheduling tool, but correctness comes from explicit
state ownership and is verified both serially and across workers. All three
threads were reviewed on 2026-08-20.

## Verification and resources

The original router/radio sequence failed 74 of 171 affected nodes after the
router tests loaded real lazy modules and radio tests resolved short names. The
repaired router/radio inventory passes all 216 nodes with warnings treated as
errors. The strict core-runner, stream, and language sequence passes all 190
nodes under the same warning policy. A focused 15-test guard matrix covers the
snapshot/restore contract, isolated loader, timeout queue cleanup, and Ansible
process-state restoration.

All checks are bounded to the named files and at most two workers. They create
no services, model processes, or network listeners and do not touch the running
Qwen process. Temporary aliases and file descriptors are removed by `finally`
paths even when module execution or Ansible execution fails.

## ZDD and rollback

The production change affects only per-invocation Ansible process globals and
temporary resources; it changes no API, persisted state, inventory, or managed
host artifact. Existing workers finish with their loaded code while new workers
adopt the isolation boundary, so deployment requires no downtime or migration.
Rollback is a code-only revert. The test guard itself is transaction-like: it
captures state before every test and restores it on every teardown path.
