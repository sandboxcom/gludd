# Compute Discovery Reconciliation

Status: implemented by S83.126 from the unique behavior in `c886e998`, adapted
to the current single-module probe API.

## Gap and scope

The current project already had offline-safe local, Kubernetes, and vSphere
probes in `general_ludd.infra.discovery`. It did not have the source branch's
budget gate, structured refresh result, bounded concurrent orchestration,
last-good fallback, circuit breaker, or safe handoff to
`UtilizationTracker`.

S83.126 adds those missing contracts without copying the obsolete package
layout. It deliberately excludes the source branch's `pyproject.toml`,
`uv.lock`, daemon, router, and AWS connector edits. The current probe transport
contract lets a deployment inject a mature provider client without making all
cloud SDKs mandatory or changing shared infrastructure.

## User-reported failure evidence

The design addresses recurring failures reported by users over several years:

- The Kubernetes Python client [issue #1546](https://github.com/kubernetes-client/python/issues/1546)
  reports a stream that works and then gets stuck; a maintainer notes that an
  interrupted connection may not be handled. Discovery therefore uses bounded
  point-in-time calls rather than an unbounded watch.
- Botocore [issue #704](https://github.com/boto/botocore/issues/704), opened in
  2015, documents cached sessions continuing to use expired credentials.
  Discovery therefore does not own or mutate credential state; an injected
  provider owns its current SDK session and secret resolver.
- Azure SDK for Python [issue #26177](https://github.com/Azure/azure-sdk-for-python/issues/26177)
  reports concurrent managed-identity requests causing rate limits and auth
  failures. Each refresh invokes a provider at most once, and repeated failures
  trip a provider-local circuit breaker.
- Kubernetes Python client [issue #2458](https://github.com/kubernetes-client/python/issues/2458)
  records a dependency pin that blocked newer urllib3 packages, including
  versions with security fixes. Google Cloud Python [issue #11184](https://github.com/googleapis/google-cloud-python/issues/11184)
  likewise reports fragile version interactions and difficult static analysis.
  The orchestration layer therefore adds no provider SDK or lock-file changes.

These reports are not claims that Gludd reproduces every upstream defect. They
are durable operational evidence for timeouts, cache bounds, credential
ownership, and optional provider dependencies.

## Public contract

### Candidate model

`DiscoveredResource` retains its existing provider, kind, CPU, memory, GPU, and
cost fields. Optional identity, availability, endpoint, region, and label
fields let an injected probe describe a live candidate without breaking older
callers.

### Selection

`select_resource()` applies the following gates in order:

1. Reject unavailable candidates and candidates below CPU, memory, or GPU
   requirements.
2. Reject endpoint URLs blocked by the canonical `is_safe_endpoint()` guard.
3. Project hourly cost over the requested runtime.
4. Fail closed for missing, negative, NaN, or infinite cost whenever either
   the work cap or remaining headroom is finite.
5. Rank only fitting candidates by capacity quality and normalized cost.
6. If a `SpendLimiter`-compatible object is supplied, atomically charge in
   rank order and retry the next candidate when a concurrent charge loses.

The function returns `None` when no candidate satisfies every hard gate. It
never silently chooses an over-budget resource.

### Refresh service

`DiscoveryService` accepts a finite mapping of provider names to injected
probes. A probe may expose the current synchronous `probe()` API or an async
`discover()` API returning `DiscoveryResult`.

- Calls run concurrently across providers and have an explicit timeout.
- Raised exceptions become non-secret status codes based on exception type.
- Successful or partial results populate a last-good cache.
- Cache entries expire after a monotonic TTL and are never served afterward.
- Repeated failures open a provider-local circuit breaker; one half-open probe
  is admitted after cooldown.
- Each full refresh emits one aggregate heartbeat with provider, success, and
  cached-result counts.

The service does not create a scheduler, thread pool, daemon, or background
loop. The caller decides when to refresh.

### Registration

`register_discovered()` is the only handoff to `UtilizationTracker`:

- no URL or an unsafe URL returns `needs_deploy` and changes nothing;
- endpoint IDs use the `disc-<provider>-<resource>` namespace;
- an active endpoint is deduplicated without resetting live request counters;
- new endpoints derive bounded concurrency slots from GPU or CPU capacity.

Registration is explicit and occurs only after selection. Refreshing a catalog
never makes every discovered machine routable.

## Security and privacy

- Provider SDKs and credential resolvers remain outside this module.
- Exception messages are not logged or returned because they can contain
  tokens, account IDs, endpoints, or request material.
- Endpoint admission reuses the project's canonical literal-host SSRF guard.
- A provider that returns an unexpected object is isolated as an error rather
  than trusted through the routing boundary.
- No new environment mutation, credential file, network allowlist, or package
  dependency is introduced.

## Resource and observability bounds

- One refresh creates at most one in-flight call per configured provider.
- Every call is bounded by `timeout_s`; the minimum effective timeout is one
  millisecond.
- Cache growth is bounded to one result per configured provider.
- Circuit-breaker state is bounded to one small record per provider.
- Cache and cooldown ages use a monotonic clock.
- No system process is launched, so there is no unnamed daemon or cross-project
  process namespace to collide with another checkout.
- Full refreshes log one heartbeat; individual failures log provider and
  exception type only.

## Zero-downtime deployment

The change is additive. Existing processes continue using their current probe
and tracker behavior. A new process may construct a service and warm its cache
before selecting or registering anything. A failed refresh preserves a valid
last-good result, while an expired result is dropped instead of routing to a
ghost resource. Deployments never require a global pause or tracker reset.

## Rollback

Rollback removes `general_ludd.infra.compute_discovery` and the optional fields
added to `DiscoveredResource`. No migration or persistent state is involved.
Already registered tracker entries remain process-local and disappear on the
normal process restart; no paid resource is created or destroyed by discovery.
Rolling back one process does not require stopping another process.

## Verification

The offline test contract covers:

- capacity, GPU, availability, SSRF, cost, runtime, and headroom gates;
- atomic charge refusal and next-candidate retry;
- explicit registration, namespacing, and live-counter deduplication;
- structured success, timeout, exception, circuit-breaker, and stale-cache
  outcomes;
- synchronous and asynchronous injected probes and concurrent fan-out;
- non-secret failure logs and the aggregate refresh heartbeat;
- backward compatibility with the pre-existing probe tests.

Acceptance requires warnings-strict focused tests, Ruff, scoped mypy,
docstrings, Markdown/spec validation, at least 85 percent aggregate coverage,
and at least 75 percent line and branch coverage for every measured file.
