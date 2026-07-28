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

## Zero-downtime deployment

The Buildkite callback and Entra class spelling are additive. Existing callers
keep their current behavior during a rolling deployment. OpenShift behavior is
unchanged; only the workflow contract was corrected. There are no database,
state, or configuration migrations, so mixed-version instances can coexist.

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

## Verification

The compatibility nodes use deterministic injected transports and perform no
live network calls.

| Area | Unit result | Source coverage | Workflow result |
| --- | --- | --- | --- |
| Buildkite | 22 passed | 89.29% | 4 passed |
| Entra sign-ins | 24 passed | 89.17% | 6 passed |
| OpenShift | 37 passed | 94.71% | 7 passed |

The three unit modules collect 83 tests, and the three targeted workflow classes
collect 17 tests. Source, unit, and workflow files pass Ruff. Repository test
collection completes without collection errors.

[bk-page]: https://forum.buildkite.community/t/support-graphql-pagination-in-pipeline-builds/575
[bk-go]: https://forum.buildkite.community/t/should-we-rely-on-go-buildkite/2256
[entra-403]: https://learn.microsoft.com/en-us/answers/questions/832261/microsoft-graph-api-auditlog-signin-throwing-403-f
[entra-token]: https://learn.microsoft.com/en-us/answers/questions/1410687/auditlogs-signins-api-fails-with-error-authenticat
[k8s-namespace]: https://stackoverflow.com/questions/47973570/kubernetes-log-user-systemserviceaccountdefaultdefault-cannot-get-services
[k8s-pods]: https://stackoverflow.com/questions/51136346/service-account-failed-to-get-pods-although-it-has-permissions-error-from-serve
