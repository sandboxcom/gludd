# Provider Smoke Harness

`make azure-harness` and `make runpod-harness` validate provider inputs without
creating or deleting infrastructure. Add `LIVE=1` for read-only credential and
account checks. Secrets are read from the environment and are never included in
the JSON result.

## Azure

Required for `LIVE=1`: `AZURE_SUBSCRIPTION_ID`, `AZURE_TENANT_ID`,
`AZURE_CLIENT_ID`, and `AZURE_CLIENT_SECRET`. Optional billing identifiers are
`AZURE_BILLING_ACCOUNT_ID`, `AZURE_BILLING_PROFILE_ID`, and
`AZURE_INVOICE_SECTION_ID`. The harness also accepts `AZURE_RESOURCE_GROUP` for
the event guard and can publish normalized events/logs with
`GLUDD_INGEST_URL` plus `GLUDD_INGEST_TOKEN`.

Azure Activity Log is a control-plane feed and can lag by several minutes; the
harness therefore reports credential/subscription validation separately from
telemetry delivery. The Activity Log API and event schema are documented by
[Azure Monitor](https://learn.microsoft.com/en-us/azure/azure-monitor/platform/activity-log)
and [the event schema reference](https://learn.microsoft.com/en-us/azure/azure-monitor/platform/activity-log-schema).
Billing access is scope-sensitive: Microsoft Q&A documents that subscription
creator access must be assigned at the invoice-section scope, not only at the
billing-account scope. The harness preserves all three billing identifiers so a
test can assert the intended scope before any Terraform apply.

## RunPod

Required for `LIVE=1`: `RUNPOD_API_KEY`. Optional safety inputs are
`RUNPOD_ENDPOINT_ID`, `RUNPOD_GPU_TYPE`, `RUNPOD_BUDGET_USD`, and
`RUNPOD_ACCOUNT_ID`. The live check calls the read-only GraphQL identity query;
it does not create a pod. Dry-run mode is suitable for CI and accepts placeholder
keys, endpoint IDs, and budget values.

## Telemetry contract

When `GLUDD_INGEST_URL` is set, the harness posts an event and a log record to
`/ingest/webhook` using the dedicated ingest token. This is intentionally
separate from the administrative `GLUDD_AUTH_PSK`; missing telemetry credentials is
reported as a configuration error rather than silently sending unauthenticated
data. The request body is a JSON array containing two independent records so
Gludd's webhook parser stores the provider event and completion log separately.
