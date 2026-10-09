# Azure Log Analytics query

This role runs one SHA-256-bound KQL query through the official
`azure-monitor-query==2.0.0` `LogsQueryClient`. The query and all credential
selectors stay on the Ansible controller. The role creates no output file,
process, listener, cache, worker, or persistent state, and it never converts a
partial provider response into success.

## Inputs

All role variables are namespaced with `azure_log_analytics_query_`. The role is
disabled by default and requires a canonical workspace UUID, a non-empty query
of at most 32 KiB, and the lowercase SHA-256 of its exact UTF-8 bytes.

| Variable suffix | Default | Contract |
|---|---:|---|
| `enabled` | `false` | Explicit opt-in |
| `workspace_id` | empty | Canonical UUID |
| `query` | empty | Exact digest-bound KQL, at most 32 KiB |
| `query_sha256` | empty | Lowercase SHA-256 |
| `timespan_minutes` | `5` | 1 through 1,440 |
| `endpoint` | `public` | `public`, `government`, or `china` |
| `credential_mode` | `managed` | `managed`, `workload`, or `environment` |
| `server_timeout_seconds` | `15` | 1 through 30; client retries are zero |
| `validate_only` | `false` | Validate without creating credentials or a client |
| `no_log` | `true` | Protect the query and credential selectors by default |

Managed identity accepts only an optional canonical client UUID. Workload
identity requires an exact tenant UUID, client UUID, and absolute projected
token path. Environment mode uses only `EnvironmentCredential`; there is no
`DefaultAzureCredential` or developer-tool fallback.

The controller prepends `truncationmaxrecords=1001` and
`truncationmaxsize=4194304`, disables statistics, visualization, additional
workspaces, and retries, then accepts at most 1,000 rows, eight tables, 64
columns per table, 16 KiB per canonical cell, and 2 MiB of canonical output.
Partial, truncated, malformed, ambiguous, or oversized results fail atomically.
The client and credential are closed in `finally` on success and failure.

## Operational evidence

The [Azure Monitor Query SDK documentation][sdk] establishes
`LogsQueryClient`, endpoint selection, server timeout, and the distinct partial
result type. Azure SDK issue [#25137][partial] records a long-lived practitioner
failure in which partial Log Analytics responses were difficult to diagnose;
this role rejects that response class instead of returning its partial rows.
The [InsufficientAccessError operator report][permission] shows that a valid
token is not proof of workspace query permission, so authentication or RBAC
failures remain failures and provider text is not returned. Microsoft's
[query-limit documentation][limits] motivates the deliberately lower local
row/output caps, while [granular Log Analytics RBAC guidance][rbac] defines the
least-privilege workspace boundary.

## Zero-downtime deployment

1. Build the controller execution environment from the locked inputs and
   address it by image digest.
2. In `validate_only` mode, admit the exact deterministic query
   `print gludd_canary=1` and its SHA-256 without opening transport.
3. Route new automation jobs to a canary controller using that digest, execute
   the same one-row query against an authorized canary workspace, and verify an
   exact non-partial result.
4. Drain prior-controller jobs before promoting the new digest. Existing jobs
   continue on the prior execution environment and no query state migrates.
5. Rollback disables S49 and routes new jobs to the prior digest. Never
   synthesize rows or mark an unavailable/partial query successful.

[sdk]: https://learn.microsoft.com/python/api/overview/azure/monitor-query-readme
[partial]: https://github.com/Azure/azure-sdk-for-python/issues/25137
[permission]: https://stackoverflow.com/questions/78567796/query-logs-from-application-insights-in-python-insufficientaccesserror
[limits]: https://learn.microsoft.com/en-us/kusto/concepts/query-limits?view=microsoft-fabric
[rbac]: https://learn.microsoft.com/en-us/azure/azure-monitor/logs/granular-rbac-log-analytics
