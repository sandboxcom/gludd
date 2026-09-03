# Self-Improvement Complexity Refactor

## Decision

The self-improvement refactor is an internal decomposition, not a behavior
change. It replaces oversized orchestration bodies with small, named helpers
while preserving the public API, serialized artifacts, trust boundaries,
state transitions, event stream, cleanup order, and Make-only execution
contract.

The extraction has two coordinated scopes:

- `ModelLeaseManager` keeps its public methods and signatures while private
  layers separate configuration and observation, plan reservations,
  reclamation, acquisition, and persistence.
- Runtime and comparison code separates compact-v4 initialization, candidate
  execution, finalization, evaluation apply/quality/cleanup/result handling,
  the managed-runner adapter factory, the CLI validate-only path, identity
  payload construction, manifest edit parsing, compact-span materialization,
  and structured decoding.

Private helpers receive explicit inputs and return typed results. The existing
facades remain the only public orchestration entry points. A helper extraction
must not move an `await`, state mutation, event, or resource transfer across a
`try`, `finally`, lock, context-manager, or cancellation boundary unless a
characterization test first proves the old and new behavior identical.

## Compatibility contract

The refactor must leave all externally observable contracts unchanged:

- public imports, call signatures, defaults, injected factories, protocols,
  and return types;
- exception types, messages, precedence, and ordering;
- proposal JSON, canonical ordering, digests, attempt identities, manifests,
  cache metadata, lease files, and plan reservations;
- canonical Make commands and their order;
- compact-v4 grammar, bounds, validation, and retry classification; and
- event names, bounded payloads, correlation identities, and transition order.

No database, configuration, cache, reservation, proposal, or event-schema
migration is part of this change. A private helper is not a new extension
surface. Callers continue to use the facade instead of importing an internal
stage directly.

## Stateful and asynchronous invariants

One owner remains responsible for each resource from acquisition through
terminal cleanup. Splitting code does not split ownership:

- A model artifact is protected before use, leased once, and released once.
  Live leases and planned candidates remain ineligible for reclamation.
- A plan reservation reaches the same monotonic state for the same model
  identity. Cancellation, timeout, or failure cannot strand a reservation.
- Worktrees, proposal exchange directories, process groups, files, locks, and
  event sinks keep their current creation, handoff, and teardown order.
- A cancellation raised by the caller is never reclassified as a child-stage
  failure or swallowed by a cleanup helper. Cleanup runs and cancellation is
  re-raised with its original semantics.
- A successful child stage cannot publish a terminal run result before all
  parent-owned quality checks, persistence, and cleanup complete.
- Partial parsing, application, persistence, or cache deletion remains
  fail-closed. Helpers cannot widen a path, scope, digest, or deletion plan.

These rules apply at every newly introduced call boundary, including the
instant immediately before and after each `await` and durable write.

## Trace observability

Decomposition must make ownership easier to follow without making the runtime
quieter. Every long operation keeps its existing observer, heartbeat, and
namespaced process identity. Existing typed events remain in the same order and
carry the same run, attempt, model, proposal, and resource correlation values
through every helper.

Tests compare complete event sequences for success, rejection, cancellation,
timeout, and injected sink failure. A terminal trace must prove both the result
and the resource end state: worker joined, process group absent, lease released,
reservation removed or terminal, temporary exchange removed, and worktree
cleaned. `make ps`, `make active-work-status`, `make observed-status`, and
`make observed-tail` remain the operator views for the corresponding live
process and retained trace evidence.

New stage-detail fields may be additive only. They must be bounded and must not
contain source text, raw model output, credentials, environment values, raw
repository identifiers, or unrestricted paths. Event publication failure must
remain unable to change resource ownership or the run result.

## Test and coverage proof

Characterization tests land before or with each extraction and pin the behavior
that is easy to lose in a mechanical refactor:

- exact public signatures, outputs, exception text/order, digests, Make command
  order, and event sequences;
- success and every existing typed rejection path;
- cancellation and failure immediately before and after each async/resource
  boundary, including nested cancellation;
- idempotent cleanup and zero leaked leases, reservations, workers, processes,
  worktrees, or temporary artifacts; and
- old serialized fixtures replaying without reinterpretation.

An AST structural test keeps each scoped orchestration function at no more than
100 lines and the public `ModelLeaseManager` facade below 500 lines. The size
test supplements behavioral tests; it cannot justify deleting cases or changing
observable behavior. Warnings-strict focused suites, lint, strict typing,
collection, the canonical self-improvement coverage run, and the full gate must
all pass. Branch coverage remains at least 85 percent aggregate and at least
75 percent in every measured file.

## Gate-driven maintainability tranche

The unchanged repository inventory exposed a second, independent regression:
228 source files were below maintainability index 20 while the enforced ceiling
was 220. The repair did not raise that ceiling. Failing-first structural tests
selected eleven files immediately below the boundary, and production
simplification removed duplicated branches, temporary state, and repeated
normalization without adding a source module to absorb the debt.

The tranche covers the self-update apply and applier paths, local-file connector,
cross-conversation memory, SSRF validation, writer supervision, entropy coding,
quadtree operations, two-phase commit, Unicode data handling, and semantic
versioning. Each file moved from MI 19.25-19.96 to MI 20.03-21.32. The combined
inventory fell to 217, leaving three files of headroom while the complete,
unchanged complexity suite passed 19/19. The structural metric remains a
regression signal rather than a substitute for the 1,095 focused behavior tests,
strict typing, warnings-as-errors, and per-file coverage evidence.

## Zero-downtime rollout and rollback

The rollout uses the existing facade as a compatibility seam:

1. Land characterization and structural limits on the feature branch.
2. Route one internal stage at a time through private helpers, keeping the old
   public entry point and persistent formats unchanged.
3. Require focused fault-injection tests, canonical coverage, and the full gate
   before promotion.
4. Use a rolling replacement. Existing self-improvement attempts drain on the
   old process; newly admitted attempts use the new process. Do not transfer
   live in-memory locks or process ownership between versions.
5. Compare typed event sequences, terminal resource evidence, latency, and
   failure classifications during the rollout before completing promotion.

Rollback replaces new processes with the prior commit and drains in-flight new
attempts under their owning process. Because the refactor changes no durable
schema or identity, the prior version can read every retained artifact. Rollback
must not delete the model cache, leases, reservations, proposals, or comparison
evidence. A missing/reordered event, stuck worker, leaked resource, changed
digest, or changed exception is a rollback signal even when the final return
value looks correct.

## Security boundaries

Helper extraction grants no new authority. Model-authored content still crosses
the same bounded schema decoder, scope checks, immutable-baseline checks, syntax
preflight, and quality gates before application. Only canonical Make commands
may execute. Filesystem operations remain confined to validated repository,
cache, exchange, and worktree roots; symlink and containment checks stay at the
last responsible boundary. Cleanup and reclamation continue to require exact
identity, ownership, and digest evidence.

Secrets and untrusted source never become helper names, process arguments,
trace labels, exception text, or diagnostics. Internal helpers remain private
so they cannot become an alternate route around admission, approval, resource,
or promotion checks.

## Practitioner evidence

- The open, long-running [Trio cancel-scope discussion #264](https://github.com/python-trio/trio/issues/264)
  dates to 2017 and demonstrates that yielding while a cancel scope is active
  can leave the scope stack inconsistent after the generator is abandoned.
  Gludd therefore does not extract an iterator or callback across an active
  cancellation/context boundary; the facade retains the complete lexical
  ownership region.
- The practitioner thread [httpcore discussion #280](https://github.com/encode/httpcore/discussions/280)
  began in 2021 and was still receiving follow-up in 2025. Maintainers describe
  connection selection as an atomic pool operation and debate moving socket
  creation while retaining the public transport surface and deterministic
  `aclose()` ownership. Gludd likewise extracts private stages behind its
  existing facade and keeps acquisition paired with its original cleanup owner.
- [httpcore issue #785](https://github.com/encode/httpcore/issues/785) reproduces
  a narrow cancellation window that degraded connection-pool state and then
  deadlocked later requests. Gludd converts that lesson into cancellation
  injection on both sides of every new await and asserts the next attempt can
  still acquire resources.
- [CPython issue #116720](https://github.com/python/cpython/issues/116720) shows
  nested `asyncio.TaskGroup` shutdown swallowing a parent's cancellation when
  an inner exception is handled. The resulting fix had to distinguish internal
  wake-up from external cancellation. Gludd therefore pins nested cancellation,
  exception precedence, and caller-cancellation propagation rather than treating
  every `CancelledError` as an interchangeable stage failure.
- The open [pipreqs issue #127](https://github.com/bndr/pipreqs/issues/127) has
  recorded since 2018 that source import names such as `cv2` do not necessarily
  identify their distribution (`opencv-python`). A generated dependency list is
  therefore not sufficient ownership evidence: Gludd keeps the reviewed
  distribution-to-import-root mapping and compares its consumer paths exactly.
- The recurring reports in [pipreqs issue #360](https://github.com/bndr/pipreqs/issues/360)
  show both sides of the same ambiguity: one import can resolve to the wrong
  public distribution, while a private module can be mistaken for an unrelated
  package. Gludd responds by refreshing only mechanically observed consumers for
  an already reviewed dependency; helper extraction cannot silently broaden a
  package mapping or turn an indirect dependency into a direct one.

Together these reports show why lowering line count alone is not success. The
refactor is complete only when smaller units preserve lexical resource lifetime,
atomic state transitions, cancellation origin, public compatibility, and the
full observable trace.
