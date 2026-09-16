# Universal Task Core Boundary

Status: accepted

## Context

Gludd is a general task system. Self-improvement is one capability that uses
Gludd's task intake, model selection, scheduling, execution, review, approval,
and observability services. It is not the product's organizing layer.

Putting reusable provider, model, scheduler, or execution code below
`general_ludd.self_improve` reverses that relationship. Other capabilities then
have to depend on self-improvement vocabulary, and generic changes appear to be
self-improvement features even when they apply equally to research, deployment,
operations, testing, or user-defined work.

## Decision

Dependencies have one direction:

```text
self_improve -> universal core
universal core -/> self_improve
```

The universal core defines task envelopes, capability contracts, provider and
model abstractions, scheduling, execution, review, approval, resource policy,
and observability. A capability supplies handlers and policy through those
contracts. Application composition may register a capability, but core code
must neither construct it nor import its package.

`tests/unit/test_universal_task_core_boundary.py` enforces this rule for the
reusable `agents`, `dispatch`, `execution`, `models`, `runtime`, and
`scheduling` packages and for modules named as core, runtime, provider, model,
or scheduler components. Both normal and function-local imports are parsed
with the standard-library AST, so moving an import inside a method cannot evade
the check.

The daemon and HTTP/CLI composition surfaces may import a capability adapter in
order to register it. They must pass only core-owned protocols or callables into
the runtime. This is a composition-root exception, not permission to place
generic behavior in the capability.

## Layer ownership

The following remains owned by `self_improve` because it expresses that
capability's policy:

- gap and recurring-failure interpretation;
- generation of improvement proposals;
- improvement-specific approval, privacy, evaluation, and promotion rules;
- self-improvement routes, commands, and presentation;
- adapters that translate a proposal to and from the universal task contract.

The following belongs in the universal core and must be usable without enabling
or importing self-improvement:

- provider discovery, health, credentials, and invocation;
- model inventory, lifecycle, selection, scoring, and fallback;
- task classification, capability matching, and resource routing;
- scheduling, leases, retries, cancellation, and concurrency limits;
- candidate execution, calibration, artifacts, evidence, and result storage;
- approval primitives, review primitives, policy hooks, and telemetry.

Existing generic implementations found under `self_improve` move to their
corresponding core package before another capability reuses them. A compatibility
re-export may remain temporarily in `self_improve`, but the implementation and
tests have one core owner. Feature-specific names, defaults, and policy stay in
the adapter.

The current event-loop self-improvement phase is transitional coupling. It will
become a registered periodic capability hook; persistence will consume a
core-owned task proposal and approval protocol. Until that extraction lands,
new provider, model, runtime, or scheduler behavior must not be added to the
phase handler.

## Migration sequence

1. Keep the dependency test green while new general task features land. This
   prevents the current self-improvement package from becoming a default home
   for reusable code.
2. Introduce narrow core-owned protocols for capability execution, periodic
   hooks, proposal admission, and result artifacts. Use ordinary constructor
   injection; do not add a second orchestration framework.
3. Move generic provider/model selection, candidate execution, task diversity,
   and lifecycle code to the existing core packages one concern at a time.
   Leave compatibility imports for callers, then remove them after migration.
4. Move the event-loop phase and todo persistence behind registered capability
   hooks wired by the daemon composition root. The event loop invokes a core
   protocol and no longer knows the `self_improve` work type.
5. Add at least one non-self-improvement capability through the same contract.
   That acceptance case proves the abstraction is task-universal rather than a
   renamed self-improvement API.

Each extraction is independently releasable: add the core implementation and
adapter first, switch callers second, remove the compatibility surface last.
No database or wire-format migration is required for the boundary itself.

## Practitioner evidence

- A long-running [Stack Overflow plugin-architecture discussion](https://stackoverflow.com/questions/2768104/how-to-create-a-flexible-plug-in-architecture)
  calls out one-way dependencies as something that must be designed carefully.
  That maps directly to the import rule enforced here.
- The widely viewed [minimal Python plugin thread](https://stackoverflow.com/questions/932069/building-a-minimal-plugin-architecture-in-python)
  records a recurring practitioner failure: a supposedly lightweight plugin
  mechanism gradually reimplements a mature framework. Gludd therefore starts
  with protocols and constructor injection, not a new loader.
- A [Python plugin separation question](https://stackoverflow.com/questions/21122723/how-to-design-plugin-architecture-where-plugins-populate-different-parts-of-a-we)
  recommends a plugin interface plus dependency injection so implementations
  register through the controller instead of mixing plugin details into it.
- The maintained [PyPA plugin discovery guide](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/)
  documents entry points and namespace packages if independently distributed
  capabilities are later required. In-process Gludd capabilities do not need
  that distribution machinery yet.

## Risks and controls

- An interface can merely rename self-improvement concepts. The non-self-
  improvement acceptance case in step 5 guards against that false abstraction.
- Compatibility re-exports can become permanent. Every re-export needs a named
  removal milestone and core-owned tests before callers switch.
- Composition roots can accumulate business logic. They may construct and
  register adapters only; execution and policy remain in owned modules.
- Static import checks cannot detect arbitrary dynamic imports. Capability
  registration must use reviewed tables or standard package entry points, not
  configuration-driven module strings.
