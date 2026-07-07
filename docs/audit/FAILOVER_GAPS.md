# Failover / Fallback / Retry — Capability Gaps

Audit date: 2026-07-06
Source: `tests/e2e/test_failover_e2e.py` (14 end-to-end failover tests)

This document records gaps discovered while writing e2e coverage for the model
failover / timeout / graceful-degradation machinery in
`src/general_ludd/models/gateway.py` + `src/general_ludd/models/timeout_detector.py`.
Each gap is pinned by an `xfail(strict=False)` test with a matching reason.

## Summary

| # | Capability | Status | Test |
|---|---|---|---|
| 1 | Primary timeout -> secondary | **PASS** | `test_primary_timeout_falls_back_to_secondary` |
| 2 | Primary 500 -> secondary | **PASS** | `test_primary_500_falls_back_to_secondary` |
| 3 | Primary DNS failure -> secondary | **PASS** | `test_primary_dns_failure_falls_back` |
| 4 | 429 Retry-After honored (no failover) | **PASS** | `test_primary_429_respects_retry_after` |
| 5 | 429 exhausted -> failover | **PASS** | `test_primary_429_after_max_retries_falls_back` |
| 6a | All-down raises typed error | **PASS** | `test_all_providers_down_raises_typed_error` |
| 6b | All-down structured error w/ provider names | **GAP** (xfail) | `test_all_providers_down_error_includes_provider_names` |
| 7 | Circuit opens -> fast-fail | **PASS** | `test_circuit_opens_then_fast_fails_primary` |
| 8 | Circuit half-open recovery | **PASS** | `test_half_open_probe_recovers_on_success` |
| 9 | Budget exhausted -> graceful reject | **PASS** | `test_budget_exhausted_raises_budget_exceeded` |
| 10 | Partial stream drop -> clean failover | **PASS** | `test_partial_drop_failovers_cleanly` |
| 11 | Exponential backoff | **PASS** | `test_retry_backoff_is_exponential` |
| 12 | Correlation-ID propagation | **GAP** (xfail) | `test_failover_preserves_correlation_id` |
| 13a | Fallback concurrency cap (no thundering herd) | **GAP** (xfail) | `test_concurrent_failover_caps_secondary_inflight` |
| 13b | Half-open single-flight (primary) | **PASS** | `test_half_open_probe_single_flight_prevents_primary_stampede` |
| 14a | Secondary success recorded in metrics | **PASS** | `test_secondary_success_is_recorded_in_metrics` |
| 14b | `failover_count` / per-profile `error_count` facet | **GAP** (xfail) | `test_failover_count_incremented` |

**Net: 13 passing, 4 xfail (documented gaps). 0 failures.**

Verified via `make test-specific TESTFILE='tests/e2e/test_failover_e2e.py'`
(`13 passed, 4 xfailed`), `make lint` (`All checks passed!`), and
`make typecheck` (`Success: no issues found in 582 source files`).

## Gaps in detail

### 1. structured-all-down-error (test 6b)

**Symptom.** When every provider in the chain is down, `call_model_with_retry`
propagates the *last* provider's raw exception (an `httpx.HTTPStatusError`). It
does NOT surface a structured error body enumerating which providers were tried
and why each failed.

**Why it matters.** Operators debugging an all-down incident see one status code
and must read logs to reconstruct the chain. A structured error
(`{"error": "all_providers_down", "providers": [{"id": "primary", "reason":
"500"}, ...]}`) would let the HTTP layer (routers/models.py `/admin/models/call`)
return HTTP 503 with a diagnostic body instead of the current 502 "model call
failed".

**Fix sketch.** Catch the exhaustion path in `call_model_with_retry` /
`_walk_fallbacks` and raise a new `AllProvidersDownError` carrying a per-profile
reason list. Map to HTTP 503 in `routers/models.py`.

### 2. correlation-id-propagation (test 12)

**Symptom.** `ModelGateway.call_model` / `call_model_with_retry` accept no
correlation ID and thread none through to logs or the response.

**Why it matters.** Failover chains cross provider boundaries; without a stable
correlation ID, a request that fails primary -> secondary cannot be traced as
one logical operation in the logs.

**Fix sketch.** Accept an optional `correlation_id` kwarg on `call_model*`,
attach it to the log `extra` for every provider attempt, and surface it on
`ModelResponse.correlation_id`. Plumb from `X-Correlation-ID` in
`routers/models.py`.

### 3. fallback-concurrency-limit (test 13a)

**Symptom.** When the primary circuit is open, every concurrent caller routes to
the secondary with no cap. Anti-thundering-herd protection exists ONLY for the
primary's half-open probe (single-flight via `ModelHealthTracker.__probe_in_flight`).

**Why it matters.** A primary outage can cascade into a secondary outage: N
concurrent clients all hit the secondary simultaneously. A bounded fallback
semaphore (e.g. max 2 in-flight to any one fallback) would prevent the cascade.

**Note.** The primary's half-open single-flight (`test 13b`) is the REAL,
working anti-herd mechanism and is covered by a passing test. The gap is
specifically a *fallback* (secondary/tertiary) concurrency limiter.

**Fix sketch.** Add a per-profile `asyncio.Semaphore` / `threading.Semaphore`
gating `_call_fallback`, sized from a new `fallback_max_concurrency` field on
`ModelProfile`.

### 4. failover-metrics-facets (test 14b)

**Symptom.** `MetricsCollector.record_model_call` records per-call token/success
data, but there is no `failover_count` counter and no per-profile `error_count`
surfaced. `ModelFailoverChain.record_failover` exists in
`models/failover.py` and logs events, but it is NOT wired into the gateway's
failover path (`_walk_fallbacks`) nor into the metrics collector. Primary
failures are recorded on the `ModelHealthTracker` only, not the metrics
collector.

**Why it matters.** An operator cannot answer "how many requests failed over
in the last hour?" or "which primary is erroring most?" from `/api/facts`.

**Fix sketch.**
- Wire `ModelFailoverChain.record_failover` into `_walk_fallbacks` (currently the
  gateway walks `profile.fallback_profiles` directly and never instantiates a
  `ModelFailoverChain`).
- Add `MetricsCollector.record_failover(from_profile, to_profile, error)` that
  increments a per-(from,to) counter; surface in the `/api/facts` `model` facet.
- Call `record_model_call(success=False, error=...)` on the gateway failure path
  so primary `error_count` is visible in metrics, not just the health tracker.

## What works well (no gap)

- Retry classification (`TimeoutClassifier`) correctly distinguishes
  transient / overload / non-retryable kinds.
- `Retry-After` header is honored for 429s (test 4).
- Exponential backoff with equal-jitter (test 11) — deterministic when jitter is
  forced to max.
- Circuit breaker opens at exactly `failure_threshold` (no double-count — the
  previously-fixed bug stays fixed) and fast-fails subsequent calls (test 7).
- Half-open single-flight probe prevents primary stampede during recovery
  (test 13b) — this is a genuine anti-thundering-herd success.
- Budget gate fails closed with a typed `BudgetExceededError` (test 9).
- Synchronous `.invoke()` means a mid-stream transport drop raises before any
  content is returned, so no partial state can leak into the failover response
  (test 10).
