# Service Discovery Search-Term Schema

## Status

Implemented for the `0.1.0-beta.4` release train. The built-in discovery
queries have stable labels, legacy plain-string caller input remains accepted,
and malformed entries fail before any search request or catalog mutation.

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

## Schema and compatibility contract

- Each built-in entry is exactly `(identifier, query)`, with two non-blank
  strings. Identifiers are stable, lowercase, descriptive names; only the query
  is sent to SearXNG.
- `DEFAULT_SEARCH_TERMS` remains a non-empty list and the infrastructure re-export
  remains the identical object for compatibility.
- Callers may continue to pass a sequence of plain query strings. They may also
  pass labeled two-string tuples, including `DEFAULT_SEARCH_TERMS` itself.
- An empty caller sequence retains the historical built-in-default fallback.
- Tuple entries with the wrong arity, non-string values, blank labels, blank
  queries, and non-string/non-tuple entries raise at construction. Errors name
  only the entry index and expected shape; they do not echo query contents.
- Input order is preserved exactly. Construction performs no search, catalog
  write, process start, or network request.
- Service names containing two non-blank characters remain valid; blank and
  single-character titles remain excluded from automatic catalog registration.

## Practitioner and upstream evidence

The long-lived
[Python typing issue 256](https://github.com/python/typing/issues/256), open
since 2016, records repeated practitioner concern that a single `str` satisfies
`Iterable[str]` even when an API intended a sequence of complete strings. That
is the exact runtime ambiguity avoided here by validating every entry's concrete
shape rather than relying on iteration or annotations alone.

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

## Zero-downtime rollout and rollback

The change has no persisted-schema, catalog-format, wire-format, or migration
impact. Existing processes keep their normalized in-memory query list; newly
constructed pipelines accept both the legacy and labeled forms. Development is
promoted only after focused coverage, the full gate, and CI are green, so old
and new workers may overlap without sharing mutable search state.

Rollback reverts the constant, normalizer, tests, and this contract together.
Catalog files created before or after the rollout remain readable, no data
conversion is needed, and in-flight queries complete under the code that
started them.

## Verification contract

The authoritative regression first reproduces the three exported tuple-shape
failures. Additional tests prove deterministic query order, labeled-tuple and
plain-string compatibility, the empty-sequence fallback, malformed-input
rejection before I/O, and two-character service-name preservation. The focused
family runs with warnings treated as errors. Touched-source aggregate coverage
must be at least 85 percent and each touched source file must retain at least 75
percent line and branch coverage; Ruff, strict mypy, docstrings, Markdown,
feature-spec, task-ledger, collection, and the full release gate remain
mandatory.
