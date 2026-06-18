# Batch 4 Deferred Items — Safety Notes

Date: 2026-06-17

Six items deferred from batch-4 integration. Each entry: risk, recommended safe approach, test impact.

---

## 1. Connector SSRF Init-Guard

**Risk:** Raising at `__init__` unconditionally breaks all test scenarios that supply cilium/nomad/k8s/docker private-network endpoints marked as allowed (e.g. `allow_private=True` or executor-injected fakes). An init-time raise fires before the caller has a chance to opt in, turning a runtime policy into a construction failure — meaning the allow-list path is never reachable.

**Safe approach:** Move the SSRF check to query-time (i.e. inside `_get_executor`, `_fetch`, or equivalent send path). Apply it only when `allow_private` is absent or `False` AND the resolved host falls in RFC-1918/loopback space. Gate on the real-network default; let callers with `allow_private=True` or injected executors pass through unblocked.

**Tests:** Existing `test_connector_guard_failclosed.py` tests for `allow_private` must continue to pass. Add a query-time block test that asserts the raise occurs on the first network call, not on construction.

---

## 2. Redirect Guards — Tempo / Zipkin / NewRelic

**Risk:** `urllib` and `requests` both follow HTTP 3xx redirects by default. An attacker-controlled Tempo/Zipkin/NewRelic endpoint can redirect to an internal host, bypassing the SSRF check that validated the original URL.

**Safe approach:** Set `allow_redirects=False` (requests) or use a custom `HTTPRedirectHandler` that raises (urllib) in every connector that calls these backends. If redirects are legitimately needed, re-validate the `Location` header through the same SSRF check before following.

**Tests:** Add a test per connector (tempo, zipkin, newrelic) that stubs a 301 → internal-host response and asserts the connector raises rather than following the redirect.

---

## 3. Secrets `resolve_required()` Opt-In

**Risk:** If `resolve_required()` is called unconditionally at startup, connectors or features that legitimately run without a secret (e.g. unauthenticated health probes, local-dev mode) will fail hard. Conversely, if it is never enforced, callers silently get `None` where a secret was expected, causing downstream auth failures that are hard to trace.

**Safe approach:** Make `resolve_required()` an explicit opt-in per call site. The caller declares intent (`secret = secrets.resolve_required("MY_KEY")`) and receives a clear `SecretMissingError` if unset. Do not call it in `__init__` or module-level code; call it at the point of use inside the method that needs the credential.

**Tests:** Existing secret-resolution tests should be unaffected. Add tests verifying that a connector initializes without error when the secret is absent, and that the first authenticated call raises `SecretMissingError`.

---

## 4. Cassandra / ClickHouse `is_safe_endpoint` in `_get_executor`

**Risk:** When an executor is injected by tests (fake/stub), calling `is_safe_endpoint` against the default localhost address produces `ok=False` because the check inspects the raw host string (`localhost` / `127.0.0.1` is treated as a loopback/private address). This causes health checks to return failure even for injected-safe test executors.

**Safe approach:** Run `is_safe_endpoint` only when no executor override is provided (i.e. `self._executor is None`). Injected executors have already passed caller-level validation; skip the check for them. For the default-localhost path, document that `ok=False` is correct behavior in a production context unless the caller explicitly opts into local mode.

**Tests:** Executor-injected tests must remain green (no `is_safe_endpoint` call path hit). Add a test asserting that the default-localhost health path yields `ok=False`, and a separate test that an injected executor yields `ok=True` without triggering `is_safe_endpoint`.

---

## 5. `variable_store` `_DOT_` / `_DASH_` Encoding

**Risk:** Existing tests already assert that keys are stored with underscore normalization (dots and dashes converted to underscores). Changing the encoding form (e.g. switching to `_DOT_` / `_DASH_` sentinel strings) will break those tests and any persisted stores that rely on the current underscore key shape.

**Safe approach:** Before changing the key encoding, grep for all test assertions on key form (`test_variable_store*.py`) and audit the stored-key contract. If the existing underscore normalization is load-bearing for callers, keep it and do not introduce `_DOT_`/`_DASH_` sentinels unless a round-trip requirement (encode → decode losslessly) is proven necessary. If sentinels are required, write a migration path and update all affected test fixtures atomically.

**Tests:** Run `make test-unit TESTFILE='tests/unit/test_variable_store*'` before and after any change. All current normalization assertions must stay green or be explicitly migrated.

---

## 6. `git_automation` `_run_git` Routing — Remaining Bypass Call Sites

**Risk:** Several call sites in `git_automation` invoke git subprocesses directly rather than routing through `_run_git`. This bypasses the timeout + non-interactive env enforcement (no `GIT_TERMINAL_PROMPT`, no TTY inheritance) and the `to_thread` async offload, meaning those paths can block the event loop or hang indefinitely on credential prompts.

**Safe approach:** Audit every `subprocess.run` / `subprocess.check_output` / `asyncio.create_subprocess_exec` call in `git_automation.py` that is not already delegating to `_run_git`. Replace each with `await self._run_git(...)`. Confirm `_run_git` sets `GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS=true`, and enforces the configured timeout.

**Tests:** Add a test per bypass site that stubs a slow git operation (e.g. `time.sleep` mock) and asserts the call times out within the configured window rather than blocking. Existing `_run_git` timeout tests can serve as templates.

---

*End of deferred items. 6 sections total.*
