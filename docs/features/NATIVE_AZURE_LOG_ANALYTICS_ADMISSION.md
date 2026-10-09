# Native Azure Log Analytics admission

S49 replaces the `log_analytics_query` role's JSON-file stub and its claim of
successful REST execution with one controller-side Ansible action backed by the
official `azure-monitor-query==2.0.0` SDK. The legacy Python entry points now
return an explicit validation-only plan; only the collection action may report
`executed: true`.

## Trust boundary

The action accepts one canonical Log Analytics workspace UUID and one exact KQL
string. The caller must provide the lowercase SHA-256 of the query's UTF-8
bytes. The query is non-empty and at most 32 KiB. A caller cannot override the
two server-side truncation directives.

Only these endpoint names are accepted:

| Name | SDK endpoint |
|---|---|
| `public` | `https://api.loganalytics.io` |
| `government` | `https://api.loganalytics.us` |
| `china` | `https://api.loganalytics.azure.cn` |

Credential selection is likewise closed: `managed` creates only
`ManagedIdentityCredential`, `workload` creates only
`WorkloadIdentityCredential` from an exact tenant/client/token-path tuple, and
`environment` creates only `EnvironmentCredential`. The implementation never
uses `DefaultAzureCredential`, Azure CLI credentials, browser credentials, or
another ambient developer chain.

The requested timespan is 1 through 1,440 minutes and the server timeout is 1
through 30 seconds. The client runs with zero connection, read, status, and
total retries. Statistics, visualization, and additional workspaces are always
disabled. No raw HTTP/REST/`uri` fallback exists.

## Atomic response admission

The action prepends these exact Kusto properties:

```kusto
set truncationmaxrecords=1001;
set truncationmaxsize=4194304;
```

The one-row gap between 1,001 provider rows and the 1,000-row local limit, and
the gap between the 4 MiB provider byte limit and the 2 MiB canonical local
limit, turn server truncation into a local rejection boundary. Any
`LogsQueryPartialResult`, partial error/data marker, continuation/truncation
marker, or ambiguous response shape fails atomically. No partial table or row
is returned.

Successful output is limited to eight uniquely named tables, 64 uniquely named
columns per table, 1,000 total rows, 16 KiB per canonical JSON cell, and 2 MiB
for the complete canonical result. Rows must exactly match their column width.
Non-finite numbers, unsupported objects, excessive nesting, and excessive item
counts fail closed. Provider exception text is redacted, and both the client
and credential are closed in `finally` on success and failure.

The action has no artifact, subprocess, listener, worker, cache, or durable
state. Check mode and `validate_only` perform complete input admission without
constructing a credential or client. Query text and credential selectors are
`no_log` by default and are never copied into the returned result.

## Practitioner evidence

- The [Azure Monitor Query SDK guide][sdk] documents the maintained
  `LogsQueryClient`, regional endpoint configuration, server timeouts, and the
  separate `LogsQueryPartialResult` failure surface. This is why S49 uses the
  official library rather than a custom REST client.
- Azure SDK issue [#25137][partial] is a long-lived practitioner report about
  confusing partial Log Analytics failures. S49 treats partial data as a
  failure even when rows are present and does not expose the provider's error
  body.
- A Stack Overflow [InsufficientAccessError report][access] demonstrates that
  token acquisition and workspace query authorization are separate. S49 never
  converts authentication or RBAC rejection into an empty successful table.
- Microsoft's [Kusto query-limit reference][limits] documents truncation as a
  correctness concern rather than merely a performance concern. The local
  limits are deliberately lower than the submitted server properties.
- Microsoft's [granular Log Analytics RBAC guidance][rbac] supports binding
  the selected identity to only the admitted workspace/query scope.

## Zero-downtime rollout and rollback

Build the controller execution environment from its lock and address it by
immutable image digest. First run `print gludd_canary=1` and its exact digest in
`validate_only` mode. Route new jobs to one canary controller using the new
digest, run the same query against an authorized canary workspace, and require
one complete non-partial row. Drain jobs on the prior controller before
promoting the new digest.

Rollback disables S49 for new jobs and routes them back to the prior execution
environment digest. In-flight jobs finish on their original controller, and no
query state or artifact needs migration. An unavailable, partial, or rejected
query remains a failure; rollback never synthesizes output.

[sdk]: https://learn.microsoft.com/python/api/overview/azure/monitor-query-readme
[partial]: https://github.com/Azure/azure-sdk-for-python/issues/25137
[access]: https://stackoverflow.com/questions/78567796/query-logs-from-application-insights-in-python-insufficientaccesserror
[limits]: https://learn.microsoft.com/en-us/kusto/concepts/query-limits?view=microsoft-fabric
[rbac]: https://learn.microsoft.com/en-us/azure/azure-monitor/logs/granular-rbac-log-analytics
