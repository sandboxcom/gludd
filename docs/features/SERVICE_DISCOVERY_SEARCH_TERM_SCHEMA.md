# Service Discovery Search-Term Schema

## Status

Implemented for the `0.1.0-beta.4` release train. The built-in discovery
queries have stable labels, legacy plain-string caller input remains accepted,
malformed entries fail before any search request or catalog mutation, and
concurrent projects may opt into separate catalog namespaces.

## Problem

`DEFAULT_SEARCH_TERMS` was exported as a list of bare strings even though the
infrastructure compatibility surface required two-string tuples. Treating a
string as a tuple-like value is especially dangerous in Python because a string
is itself iterable: structural consumers could unpack characters or reject a
query only after the pipeline had already been constructed. The built-in list
also could not give a query a stable identifier independent of its mutable
search wording.

The adjacent pipeline contract already recognizes valid two-character service
acronyms such as `GE`, `HP`, and `AI`. Discovery must retain those names instead
of silently dropping them while applying the search-schema repair.

The historical default catalog path is process-global. Two concurrent projects
that intentionally share a working directory could therefore reconcile the
same health-socket records: one project's absent result could retire another
project's still-healthy service. Isolation must be explicit without changing
the path used by existing callers.

## Schema and compatibility contract

- Each built-in entry is exactly `(identifier, query)`, with two non-blank
  strings. Identifiers are stable, lowercase, descriptive names; only the query
  is sent to SearXNG.
- `DEFAULT_SEARCH_TERMS` remains a non-empty list and the infrastructure re-export
  remains the identical object for compatibility.
- Callers may continue to pass a sequence of plain query strings. They may also
  pass labeled two-string tuples, including `DEFAULT_SEARCH_TERMS` itself.
- A scalar string is rejected before connector construction instead of being
  interpreted as one outbound query per character. This includes the empty
  string; only an empty sequence selects the built-in defaults.
- An empty caller sequence retains the historical built-in-default fallback.
- Tuple entries with the wrong arity, non-string values, blank labels, blank
  queries, and non-string/non-tuple entries raise at construction. Errors name
  only the entry index and expected shape; they do not echo query contents.
- Input order is preserved exactly. Construction performs no search, catalog
  write, process start, or network request.
- A completed search batch with zero results is inconclusive. It reports any
  isolated query errors and preserves the existing catalog; an empty upstream
  response is not evidence that every known service disappeared.
- Supplying `project_namespace` places the existing catalog filename beneath a
  sanitized project directory. A blank namespace fails before connector
  construction, while omitting the option preserves the exact legacy path.
- Concurrent project namespaces never load, save, discover, or retire each
  other's health-socket state. Reconciliation remains local to one
  project-scoped catalog even when both pipelines search at the same time.
- Service names containing two non-blank characters remain valid; blank and
  single-character titles remain excluded from automatic catalog registration.

## Practitioner and upstream evidence

The long-lived
[Python typing issue 256](https://github.com/python/typing/issues/256), open
since 2016, records repeated practitioner concern that a single `str` satisfies
`Iterable[str]` even when an API intended a sequence of complete strings. That
is the exact runtime ambiguity avoided here by validating every entry's concrete
shape rather than relying on iteration or annotations alone.

The related long-lived
[mypy issue 11001](https://github.com/python/mypy/issues/11001) describes this
as a broad class of real API bugs and requests an opt-in checker rule because a
`str` still satisfies `Sequence[str]` statically. Until typing tools can express
"a sequence of strings, but not a string" portably, Gludd must enforce that
container boundary at runtime before constructing the network connector.

The SearXNG community's
[JSON API discussion 1789](https://github.com/searxng/searxng/discussions/1789)
documents that SearXNG passes the entire search term to selected engines and
that engine-specific query syntax is not portable. Gludd therefore keeps the
human query as an opaque string and does not concatenate the stable identifier
or reinterpret query syntax.

The multi-year
[SearXNG JSON/category failure report 2505](https://github.com/searxng/searxng/issues/2505)
also shows that an upstream search request can fail for an otherwise valid
query depending on instance format and engine configuration. Schema validation
is deliberately separate from the existing per-query error isolation: valid
queries still report remote failures without crashing the whole batch.
The same evidence makes destructive empty-snapshot reconciliation unsafe: a
temporarily empty or incompatible upstream response must not mass-retire known
services.

The long-running
[Consul health-check deregistration issue 5842](https://github.com/hashicorp/consul/issues/5842)
records a practitioner finding that deregistering one service removed health
checks from unrelated services when their identifiers shared agent-level
scope. Gludd avoids the analogous ownership ambiguity by putting each opt-in
project's discovered health state in a separate catalog before any retire or
save operation can run.

The pytest-xdist community's
[cross-worker identity issue 524](https://github.com/pytest-dev/pytest-xdist/issues/524),
opened in 2020, documents the need for one stable identity that distinguishes a
parallel run's shared resources from other runs. Gludd uses the caller's stable
project namespace—not thread timing or a transient worker number—to derive the
catalog boundary exercised by the concurrent regression.

## Security and resource boundaries

Search text is untrusted outbound input. Shape validation occurs before the
connector can receive it, and validation errors do not reflect the query or
label into logs. The existing SearX connector remains responsible for SSRF
protection, bounded HTTP timeouts, disabled redirects, and response parsing;
this change grants no new network destination or credential access.

The default list remains five bounded requests in deterministic order. No
thread, daemon, subprocess, port, cache, database table, or temporary artifact
is added. Rejecting malformed entries at construction avoids partial batches
and wasted outbound calls. Per-query upstream errors retain their current
isolation and are accumulated in the discovery report.

Namespace text is treated as an untrusted path component and normalized by the
existing sandbox-state component allocator; separators and traversal syntax
cannot escape the catalog parent. Opting in adds at most one existing-format
catalog file and parent directory per active project. It adds no worker,
socket, network call, cleanup daemon, or cross-project delete permission.

## Zero-downtime rollout and rollback

The change has no persisted-schema, catalog-format, or wire-format impact.
Existing callers that omit `project_namespace` keep the historical path.
Namespaced workers write only their new project path, so old and new workers can
overlap during a rolling deployment without sharing the new mutable state.
Development is promoted only after focused coverage, the full gate, and CI are
green.

Rollback stops passing `project_namespace`; namespaced catalog files remain
readable and recoverable but are no longer selected automatically. No data
conversion or destructive cleanup is required, and in-flight queries complete
under the code and catalog path that started them.

## Verification contract

The authoritative regression first reproduces the three exported tuple-shape
failures. Additional tests prove deterministic query order, labeled-tuple and
plain-string compatibility, the empty-sequence fallback, non-destructive empty
search results, malformed-input rejection before I/O, and two-character
service-name preservation. The concurrent namespace regression synchronizes
two searches at a barrier, proves their catalog paths and services differ, then
retires one project's health-socket record while requiring the other catalog to
remain byte-identical and active. The focused family runs with warnings treated
as errors. Touched-source aggregate coverage must be at least 85 percent and
each touched source file must retain at least 75 percent line and branch
coverage; Ruff, strict mypy, docstrings, Markdown, feature-spec, task-ledger,
collection, and the full release gate remain mandatory.
