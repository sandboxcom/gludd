# Beta.3 connector compatibility contracts

Status: implemented and verified on 2026-07-28.

## Scope

This release makes the generated beta.3 connector workflows match the public
connector contracts without weakening authentication or namespace boundaries.
The changes are additive and require no data migration.

### Buildkite

`BuildkiteSource` continues to accept the full legacy `transport` callback:

```text
(method, url, headers, timeout) -> (status, response_bytes)
```

It also accepts the compact `http_get` callback used by generated workflows:

```text
(url, headers) -> (status, decoded_response)
```

The adapter preserves byte and text responses and JSON-encodes decoded objects
before handing them to the existing response parser. Supplying both callback
forms is an error, so the active network boundary is always unambiguous.

### Microsoft Entra sign-ins

`EntraSignInSource` remains the canonical class. `EntraSigninSource` is an
additive compatibility spelling for generated workflows and has the same
constructor and behavior.

Both names deliberately require `token_env`. The environment value is an
externally minted Microsoft Graph bearer token. Tenant IDs, client IDs, and
client secrets are not accepted as bearer tokens, and the connector does not
silently introduce an OAuth client-credentials exchange.

### OpenShift

`OpenShiftSource` continues to require an explicit namespace. Health checks
probe the configured namespace, and pod queries use an explicit `pods` mode.
The generated workflow tests now prove that a missing namespace is rejected
and that both health and query requests retain the configured boundary.

### Callback transports and response shapes

The remaining generated workflows can use their compact callable HTTP
boundaries while existing object transports remain supported. The adapters
cover Jenkins, Argo Workflows, AWS pipelines, Travis, Slack, PagerDuty,
Opsgenie, Sentry, Grafana OnCall, Honeycomb, SigNoz, AppDynamics, Splunk,
Elastic APM, Zipkin, and Tempo.

Callbacks return an HTTP status and a decoded body. Each connector adapts that
pair to its existing parser rather than bypassing authentication, URL
validation, or error handling. Tests use service-shaped payloads, including
Jenkins's `jobs` envelope, Honeycomb's create/result/fetch sequence, Elastic
hits, nested Zipkin spans, and Tempo trace summaries.

### Database cursors

MySQL, Cassandra, and ClickHouse accept an injected DB-API-style cursor in
addition to their existing connection boundaries. This is constructor-level
compatibility only: SQL text, parameter binding, row normalization, and error
handling remain owned by the connector. Tests pin mapping and tuple rows so a
generated workflow does not require a live database to prove the contract.

### Cloud inventory scopes

Cloudflare accepts the compact tuple-returning transport. GCP Asset Inventory
accepts `project_id` and normalizes it to the service's `projects/...` scope.
Azure Resource Graph accepts `subscription_id` and sends it as the API's
subscription list. AWS Config and CloudTrail accept the generated
`aws_client(service_name)` factory without changing service selection.

### Time-series query compatibility

VictoriaMetrics, OpenTSDB, InfluxDB, Graphite, and Thanos accept generated
callable transports and query aliases while retaining their established public
names. `InfluxDBSource` is an additive public alias, and `query` is accepted
where generated workflows previously used a connector-specific query key.
Fixtures preserve real result envelopes and scalar values instead of replacing
them with test-only shortcuts.

## Zero-downtime deployment

The callback, cursor, scope, query, and public-name compatibility paths are
additive. Existing callers keep their current behavior during a rolling
deployment. OpenShift behavior is unchanged; only the workflow contract was
corrected. There are no database, state, or configuration migrations, so
mixed-version instances can coexist. Rollback consists only of restoring the
previous application version; no state handoff or cleanup is required.

## Long-lived user reports considered

- A Buildkite user reported GraphQL build pagination problems in June 2019 and
  followed the issue through March 2020. This history supports keeping an
  injectable HTTP boundary so pagination and response-shape behavior can be
  reproduced without a live service. See [Buildkite pagination report][bk-page].
- Buildkite users have also asked whether client libraries and schemas are
  stable, maintained integration surfaces. Buildkite explained in September
  2022 that maintenance of `go-buildkite` is driven by issues and pull requests.
  This supports preserving the existing transport while adding the generated
  workflow's smaller adapter. See [go-buildkite maintenance question][bk-go].
- A Microsoft Graph user reported persistent `403` responses from the sign-in
  audit endpoint in April 2022 despite adding API permissions. The accepted
  guidance distinguishes Graph consent from the directory role required for
  this endpoint. That distinction is why the compatibility name retains the
  explicit external-token contract instead of treating client credentials as
  a token. See [Microsoft Graph sign-in audit permissions][entra-403].
- A second Microsoft Graph report from October 2023 shows that adding
  permissions alone did not correct authentication when token claims and tenant
  context were wrong. Tests therefore pin the token environment variable and
  authorization header behavior. See [Microsoft Graph token context][entra-token].
- A Kubernetes user report opened in December 2017 and maintained over
  subsequent years shows that creating a namespace does not grant a service
  account access inside it; namespace-scoped role bindings are still required.
  OpenShift tests therefore retain the namespace as a required, observable
  contract. See [namespace-scoped service-account access][k8s-namespace].
- A related 2018 report documents a service account that could not list pods
  even when its permissions appeared correct. This reinforces testing the exact
  namespace path and pod mode rather than a cluster-wide root probe. See
  [service-account pod access][k8s-pods].
- A Jenkins user reported in July 2024 that root job enumeration mixed jobs
  with jobs nested in folders and asked how to address one folder. The response
  points to the folder's `jobs` tree. Tests therefore pin both the root `jobs`
  envelope and root-job normalization without pretending that nested jobs have
  the same address. See [Jenkins folder job enumeration][jenkins-folders].
- An InfluxDB user reported in May 2021 that Flux queries returned CSV where
  InfluxQL returned JSON. The response confirmed that CSV was the Flux v2 wire
  schema. Tests consequently inject normalized Flux table records at the
  client boundary and exercise both query-key spellings; they do not assume
  that the raw Flux HTTP response is JSON. See [InfluxDB Flux response
  schema][influx-flux].
- A Grafana Tempo user reported in December 2023 that a search response exposed
  search metrics yet returned fewer traces than the requested time range.
  Connector tests therefore distinguish the `traces` result list from the
  `metrics` object and pin realistic trace summaries. They do not interpret
  backend search completeness as a response-parsing guarantee. See [Tempo API
  search report][tempo-search].
- A Splunk user reported in March 2013 that fetching an asynchronous search too
  early returned no results and that fixed sleeps were unreliable. The
  connector retains `exec_mode=oneshot`, and tests pin that request together
  with the callable transport, avoiding a new polling race in the compatibility
  path. See [Splunk search completion report][splunk-search].

## Verification

The compatibility nodes use deterministic injected transports and perform no
live network calls.

| Area | Unit result | Source coverage | Workflow result |
| --- | --- | --- | --- |
| Buildkite | 22 passed | 89.29% | 4 passed |
| Entra sign-ins | 24 passed | 89.17% | 6 passed |
| OpenShift | 37 passed | 94.71% | 7 passed |
| Callback adapters and payloads | 383 passed | 85.19%-96.84% | 42 passed |
| Database cursors | 134 passed | 86.81%-92.62% | 7 passed |
| Cloud inventory | 99 passed | 86.39%-93.23% | 11 passed |
| Time-series queries | 120 passed | 86.43%-94.57% | 10 passed |
| Complete batch workflow file | - | - | 103 passed |

The focused unit modules collect 819 tests. Every touched connector source file
is above 85% coverage, and the changed workflow classes collect 87 targeted
tests. The complete batch workflow file runs serially to avoid hiding
order-dependent behavior and collects 103 tests. Source, unit, and workflow
files pass Ruff. Repository test collection completes without collection
errors.

[bk-page]: https://forum.buildkite.community/t/support-graphql-pagination-in-pipeline-builds/575
[bk-go]: https://forum.buildkite.community/t/should-we-rely-on-go-buildkite/2256
[entra-403]: https://learn.microsoft.com/en-us/answers/questions/832261/microsoft-graph-api-auditlog-signin-throwing-403-f
[entra-token]: https://learn.microsoft.com/en-us/answers/questions/1410687/auditlogs-signins-api-fails-with-error-authenticat
[k8s-namespace]: https://stackoverflow.com/questions/47973570/kubernetes-log-user-systemserviceaccountdefaultdefault-cannot-get-services
[k8s-pods]: https://stackoverflow.com/questions/51136346/service-account-failed-to-get-pods-although-it-has-permissions-error-from-serve
[jenkins-folders]: https://community.jenkins.io/t/get-jobs-from-specific-folder/16615
[influx-flux]: https://community.influxdata.com/t/getting-json-response-instead-of-csv-response-for-flux-query-rest-call/19735
[tempo-search]: https://community.grafana.com/t/grafana-tempo-api-search-issues/109429
[splunk-search]: https://community.splunk.com/t5/Getting-Data-In/REST-API-Python-Issue-with-pulling-results-before-search-job-is/m-p/69717
