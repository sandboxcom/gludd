# Source Overload Placement Contract

## Status

Implemented for beta.4. Runtime modules contain one executable declaration for
each public callable. Static overload-only declarations are not shipped in
source modules when a single union signature expresses the supported forms.

## Contract

`register_project_type` accepts both supported registration forms through one
runtime declaration:

1. A validated `ProjectType` instance may be registered directly.
2. A legacy string identifier may be paired with a mapping definition.
3. A string without a definition and every unsupported value still fail
   closed through the existing validation paths.
4. The public callable has one source declaration and no `@overload`
   decorators. The union signature remains visible to runtime introspection and
   static analysis.

The repository-wide structural audit enforces the general placement rule, and
a registration-specific AST regression pins the single executable declaration.
The runtime regression invokes both forms through the same callable and verifies
their independently registered results.

## Practitioner evidence

The long-running
[Python typing issue about overloads outside stubs](https://github.com/python/typing/issues/72)
records the core ambiguity practitioners have faced since 2015: overload
declarations describe static variants, while runtime behavior still requires one
ordinary implementation. A later
[Python.org multiple-dispatch discussion](https://discuss.python.org/t/multiple-dispatch-based-on-typing-overload/26197)
shows the confusion remains operationally relevant: repeated Python definitions
replace earlier callables unless a runtime dispatcher is deliberately built.

Gludd therefore follows the maintained
[Python typing contract](https://docs.python.org/3/library/typing.html#typing.overload):
when overload declarations are used, they precede a single implementation and
are not themselves the runtime implementation. In this case they add no type
precision beyond the implementation's union signature, so keeping only the
executable declaration gives runtime introspection, source structure, and static
typing one canonical truth.

## Security and resource boundaries

This change does not widen accepted input. Existing `ProjectType` validation,
legacy mapping validation, duplicate handling, and unsupported-value rejection
remain in the one implementation and continue to fail closed. It adds no dynamic
dispatch, reflection-based invocation, deserialization, network access, secret
handling, or authorization path.

Removing declaration-only functions avoids constructing redundant function and
typing-overload registry objects at import time. The repair adds no processes,
threads, locks, files, dependencies, background work, persistent state, or
cleanup obligation. The runtime registry and its lifecycle are unchanged.

## Zero-downtime delivery and rollback

The callable name, accepted arguments, return type, registry semantics, and
errors are unchanged. There is no database, configuration, API, wire-format, or
artifact migration. Old and new workers may overlap safely during a rolling
deployment because their project-type registries are process-local and expose
the same runtime behavior. Promote after focused tests, coverage, static checks,
and the full gate are green. Rollback is a source revert or traffic shift to the
previous worker set; no state repair or compatibility window is required.

## Verification

- The generic source audit finds no `@overload` declaration in shipped Python
  source.
- The registration-specific AST regression finds exactly one undecorated source
  declaration, and the runtime regression exercises both supported forms through
  that callable.
- Aggregate line-and-branch coverage remains at least 85 percent, and every
  touched production file remains at least 75 percent for line and branch
  coverage.
- Ruff, strict mypy, docstrings, Markdown, feature-spec, and task-ledger checks
  remain warning-free and suppression-free.
