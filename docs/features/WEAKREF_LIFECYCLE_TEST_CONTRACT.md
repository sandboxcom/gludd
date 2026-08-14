# Weak-Reference Lifecycle Test Contract

Status: beta4 release contract

## Contract

Weak-reference leak probes must distinguish a weak observer from every strong
owner. Calling `gc.collect()` does not make a reachable object unreachable: a
function local, collection entry, closure, or loop target remains an owner until
that reference is explicitly released or its scope ends.

The deep cleanup suite therefore creates the observed object in a helper scope.
For the retained-object case, the helper returns one explicit owner collection;
clearing that collection removes the last strong reference. For cyclic objects,
each cycle is built in a helper invocation so the caller never retains the final
loop pair through Python's loop-target names. A separate characterization proves
that a named local remains alive across forced collection and dies only after
`del` removes that owner.

The out-of-scope probe has two parametrized test items. Under the repository's
default two-worker pytest profile, the items execute as process-local probes and
demonstrate that no module-global owner or cross-worker cleanup is required. The
same contract remains valid in a serial run. Tests do not depend on collection
timing, worker order, sleeps, retries, or implementation-private refcounts.

## Root cause and compatibility

The prior assertions retained the objects they expected to disappear. One test
cleared a list but kept the same object in the local name `obj`; another left the
last cyclic pair in the function locals `a` and `b`. Both failures reproduce in
separate xdist workers, so they are deterministic ownership mistakes rather than
worker pollution or a product resource leak.

This change is test-only. It does not alter product lifetime, public APIs,
serialization, schemas, process topology, or the garbage collector. The probes
use the documented `weakref` and `gc` APIs and remain portable to Python
implementations that defer cyclic collection until an explicit `gc.collect()`.
The module's pipeline-controller fakes also conform to the current asynchronous
merge and zero-argument gate protocols so strict type checking remains useful;
their behavior is unchanged for these lifecycle tests.

## Security, resources, and observability

- Probe objects contain no credentials, user data, file handles, or network
  state; weak references cannot disclose an object after its last strong owner
  is gone.
- Each item allocates a bounded number of tiny Python objects. Explicit
  collection is synchronous; no daemon, thread, child process, timer, sleep,
  retry, temporary file, or untracked helper is introduced.
- Failure output identifies the surviving weak reference and exact test item.
  Parametrized item IDs make independent worker execution visible in gate logs.
- The autouse cleanup fixture performs a final collection after each item, but
  correctness never relies on cleanup from another item or worker.

## Zero-downtime delivery and rollback

The repair has no runtime deployment surface, so a rolling beta4 promotion mixes
identical application behavior. Promote after the focused cleanup suite passes
under the normal two-worker gate and the documentation/spec/task checks are
green. No migration, drain, cache invalidation, or process restart is required.

Rollback is a source revert of the test and this contract document. It changes no
runtime state and needs no cleanup. A rollback reintroduces false leak alarms, so
the release gate should remain closed until the ownership-correct probes are
restored.

## Verification

- `tests/unit/test_resource_cleanup_deep.py::TestWeakrefLeakDetection` proves
  out-of-scope death, explicit-owner survival and release, cyclic collection,
  finalizer execution, and independent parametrized worker probes.
- The complete resource-cleanup module must pass with warnings treated as errors.
- Documentation, spec, and task-ledger validation must remain green. Because no
  production file changes, production line/branch coverage floors are unchanged.

## Practitioner evidence

- A long-lived [Stack Overflow report about a generator surviving forced
  collection](https://stackoverflow.com/questions/15490127/will-a-python-generator-be-garbage-collected-if-it-will-not-be-used-any-more-but)
  identifies a live local and an accidentally dereferenced weakref as strong
  owners. The accepted remedy is to end the scope or explicitly delete the name.
- A [2018 Stack Overflow discussion of function-local
  lifetime](https://stackoverflow.com/questions/53949272/does-python-garbage-collect-variables-that-are-no-longer-referenced-while-within)
  explains why a defined local remains reachable even after its last textual use.
- A [2021 practitioner question about local reference-count
  release](https://stackoverflow.com/questions/70321290/when-is-the-reference-count-for-a-local-variable-in-a-python-function-decreased)
  documents `del` as the explicit boundary when weakref-sensitive code must end
  a local owner's lifetime before function return.
- The [pytest-xdist project](https://github.com/pytest-dev/pytest-xdist) documents
  that distributed tests run in separate worker processes. The regressions keep
  all ownership inside each item rather than relying on cross-worker state.
