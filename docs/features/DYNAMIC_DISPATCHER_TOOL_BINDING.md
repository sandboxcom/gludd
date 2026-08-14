# Dynamic Dispatcher Tool Binding

## Status

Implemented for the `0.1.0-beta.4` release train. Dispatcher-created tool
payloads are observable, isolated snapshots and the fail-loud unconfigured
executor remains the canonical default.

## Problem

The dynamic-tool regression suite still assumed that a dispatcher without an
executor completed successfully. That contradicted the production safety
contract: an absent model gateway selects the noop executor and must return a
failed result instead of silently claiming work was done. The stale assertion
also obscured two real binding gaps. A task received the registry's mutable
input-schema dictionary by reference, and registry discovery failures were
silently discarded.

A task-local tool payload must not be able to change the shared catalog used by
later tasks. At the same time, discovery is optional metadata enrichment, so a
registry outage must not replace a configured executor's result or expose
credentials from the exception.

## Binding contract

- Tests and embedding callers that require successful execution provide an
  explicit executor. The default noop path continues to return `failed`.
- Binding runs only when `bind_tools_on_dispatch` is enabled, a registry is
  present, and the task does not already carry tools.
- An empty registry, absent registry, or disabled binding leaves `task.tools`
  unchanged. Explicitly supplied task tools always win.
- The dispatcher preserves registry order and maps each tool to `name`,
  `description`, and `parameters`.
- Each `parameters` value is a deep task-local snapshot. Executor-side mutation
  cannot alter the MCP registry or a later dispatch.
- A registry exception emits a warning with sanitized exception text and then
  continues without dynamic tools. It never logs credentials or changes the
  configured executor's success/failure result.
- Discovery occurs at dispatch time. There is no process-global tool cache, so
  registry additions and removals are visible to subsequent tasks.

## Practitioner and upstream evidence

The long-lived
[MCP Python SDK issue 100](https://github.com/modelcontextprotocol/python-sdk/issues/100)
records a practitioner seeing fewer than ten tools from `list_tools()` when the
servers exposed larger catalogs. It demonstrates why the dispatcher must bind
the registry's current result faithfully instead of assuming a fixed catalog
size or silently inventing entries.

[MCP Python SDK issue 710](https://github.com/modelcontextprotocol/python-sdk/issues/710)
captures sustained user confusion about when changing resource and tool lists
become visible. The current MCP tools specification makes the set explicitly
changeable and recommends deterministic ordering; fetching the local registry
at each dispatch gives Gludd a simple, cache-free visibility boundary.

The later
[dynamic discovery proposal 1821](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1821)
documents the bandwidth and context costs of large tool catalogs. Gludd does
not implement speculative filtering in this change, but the task-local
snapshot boundary leaves filtering or pagination free to evolve inside the
registry without changing executor ownership.

## Security and resource boundaries

Tool schemas are untrusted descriptive data. Deep-copying them prevents one
executor from mutating shared authorization-adjacent metadata for another
task. Tool-name collision checks, dispatch permissions, and actual tool-call
authorization remain in their existing layers; binding a schema does not grant
permission to execute it. Discovery exceptions pass through the standard
credential and internal-address sanitizer before logging.

The copy cost is linear in the schemas selected for one task and is released
with that task. The change creates no daemon, thread, port, cache, temporary
file, or database record. Fetching from the in-process registry once per
dispatch avoids stale-cache lifetime and cross-project cleanup concerns.

## Zero-downtime rollout and rollback

The change is additive and requires no migration or wire-format change.
In-flight tasks retain the tool payload they already received; new tasks get an
isolated payload on their normal dispatch path. Empty or unavailable registries
remain non-blocking when an executor is configured, while an unconfigured
executor remains fail-loud.

Rollback reverts the dispatcher snapshot/logging change together with its
regressions. No persisted state needs conversion. Promotion still requires the
development gate and CI to be green before `development` advances to `master`.

## Verification contract

The authoritative regression first reproduces the five stale noop-success
failures, the shared-schema mutation, and the silent registry exception. The
repaired suite must pass with warnings treated as errors and prove disabled,
absent, empty, pre-populated, successful, mutation-isolated, and sanitized
failure paths. Dispatcher coverage must remain at least 85 percent aggregate
and at least 75 percent line and branch coverage for the touched source file;
Ruff, strict mypy, docstrings, Markdown, feature-spec, task-ledger, collection,
and the full release gate remain mandatory.
