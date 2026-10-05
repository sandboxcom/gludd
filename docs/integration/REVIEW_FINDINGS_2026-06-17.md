# Integration Review Findings — 2026-06-17

Findings from the parallel read-only review sweep run **after** the integration
commit `e982a81` landed (gate green). **None of these blocked the commit** — the
full `make gate` passed with all of this code in place. They are queued for a
follow-up batch. Grouped by priority; each cites `file:line` and the fix.

These were surfaced by ~30 independent reviewer agents (Sonnet + Opus). Where a
finding was confirmed by more than one reviewer it is marked **(2x)**.

---

## P1 — real correctness bugs (fix next batch)

1. **Circuit-breaker double-count on non-retryable failures (2x).**
   `models/gateway.py:529-535` — for `AUTH_ERROR` / `CONTEXT_LENGTH` /
   `INVALID_REQUEST`, `tracker.record_event(...)` runs in the non-retryable
   `except` branch *after* `_invoke_and_bill` → `record_timeout_on_failure`
   already recorded the same event (`gateway.py:284`). `_consecutive` increments
   by 2, so the breaker trips at half the configured threshold. The retryable
   path was already de-duped (comment at L481-493); this branch was missed.
   **Fix:** delete the `record_event` block at L529-535 (keep the `raise`).

2. **Budget kill-switch is stuck-on past the daily window.**
   `controllers/budget_manager.py:91-94` — `check_daily_budget` returns early on
   `if self._paused:` *before* calling `_reset_daily_if_needed()`. Once paused
   (real breach **or** NaN-induced pause), the daily reset never runs and the
   limiter stays paused forever. **Fix:** call `_reset_daily_if_needed()` before
   the `_paused` check.

3. **BudgetManager pre-gate is blind to projected cost.**
   `daemon.py:717,724` — `check_daily_budget(0.0)` / `check_todo_budget(..., 0.0)`
   pass `0.0`, so these gates only fire *after* accumulated spend already exceeds
   the limit (one overage call slips through at the boundary). The real
   projection is computed separately for the `SpendLimiter` path (L755-758), so
   the kill-switch still works overall — but the two gates are disjoint. **Fix:**
   feed the `token_cost_usd` projection into the `BudgetManager` pre-checks too.

4. **C1 can miss generation invocation for malformed `work_type`.**
   `event_loop/loop.py:942` — the C1 guard re-reads `_safe_str(todo, "work_type")`
   with no default, whereas L878 already resolved `work_type` with a `"code"`
   fallback. A todo with absent/non-string `work_type` yields `None` →
   `is_generation_work_type(None)` is `False` → model call silently skipped.
   **Fix:** reuse the already-resolved `work_type` local at L942.

---

## P2 — security hardening (mostly pre-existing; consistency)

5. **`gcp_asset_inventory.py:70-73` accepts single-label internal hostnames.**
   `_host_is_internal` only blocklist-checks non-IP hosts; a dot-less host like
   `https://internal/` passes. `azure_monitor.py:89` already has the
   `"." not in host` guard. **Fix:** add `if "." not in h: return True`.
   The shared advisory guard `connectors/base.py is_safe_endpoint` has the same
   gap (defense-in-depth only — each connector enforces its own).

6. **`elastic_apm.py` does not name-block `localhost`** (only IP literals);
   `http://localhost/...` passes the guard. Every sibling has an explicit
   hostname denylist. Same weaker-default note for `victoriametrics.py`,
   `travis.py`, `buildkite.py`, `argo_workflows.py` (literal-IP-only guards).

7. **DB-driver connectors lack a host guard.** `postgres/mysql/mongodb/redis/
   cassandra/clickhouse` `_stats` pass their connection target to the driver with
   no `_assert_public_base_url`. Most exploitable: `cassandra_stats.py:82-85`
   (config `jmx_url` fetched **with the bearer token attached**) and
   `clickhouse_stats.py:97-102` (config `url` fetched **with HTTP Basic auth**) —
   a malicious config URL exfiltrates the credential / hits metadata endpoints.
   `influxdb`/`graphite` already guard correctly. **Fix:** reuse
   `_assert_public_base_url` for `jmx_url` and `url`.

8. **`git_automation/repo.py` hardening gaps.** `_reject_escaping_path`
   (L423-452) confines via `normpath`+prefix only — **no realpath**, so a symlink
   worktree-placement escape is possible (the worktree engine `core.py` already
   has the realpath jail). Also `init_repo`, `create_local_bare_mirror`,
   `merge_branch`, `push_to_remote` **bypass `_run_git`** → no timeout, no
   non-interactive env; `create_local_bare_mirror` also lacks a `--`/leading-dash
   guard. **Fix:** route all call sites through a hardened helper; delegate the
   jail to `confine_worktree_path`.

9. **Redirect-following not re-guarded.** `jaeger/tempo/zipkin/newrelic/
   elastic_apm` use default transports (`httpx`/`urllib`/`requests`) that follow
   3xx without re-validating the `Location` host — a trusted host can 302 to
   `169.254.169.254`. `sentry.py` (re-validates every URL) and the
   transport-injected connectors (`honeycomb`, `datadog`) are immune.
   **Fix:** `follow_redirects=False` or re-validate the final URL.

10. **`secrets/manager.py:96-99` — `resolve()` returns `None` (warn-only) when
    `_client is None`.** A never-`connect()`-ed manager with a registered alias
    yields `None` instead of `SecretsUnavailableError` — a fail-open footgun if
    callers treat `None` as "not configured." **Fix:** raise when an alias exists
    but no client is configured.

11. **Secrets auto-mode swallows `http://`.** `connect()` raises on plaintext URL
    (`manager.py:145-149`), but in `mode=auto` `build_secrets_resolver` catches it
    and silently falls back to env (`test_secrets_auto_mode.py:32-39` pins this).
    The https-enforcement intent is defeated for auto-mode. **Fix:** log the
    rejection at ERROR, or hard-fail.

12. **`mcp/secrets.py:106` — `scrub_mcp_config` env-block exit bug.** The
    `stripped and` guard means a blank line never exits the `env:` block, so a
    sibling block after a blank line may be scrubbed against `SENSITIVE_ENV_KEYS`.
    Line-based (not YAML-parsed) scrubbing also misses block scalars. Real but
    minor (the actual boundary is the env allowlist + alias resolution).

---

## P3 — correctness nits / metrics / dead code

13. **`routers/accounting.py` `tokens_used = total_calls * 1000`** — a proxy, not
    real tokens; reports wildly wrong counts (metrics inaccuracy, not security).
14. **`events/bus.py:59-60`** — dead `elif iscoroutinefunction(callback)` branch;
    unreachable for normal async callbacks but would double-invoke if reached.
    Safe to delete.
15. **`dispatch/variable_store.py:102`** — `safe_name` normalization collapses
    `foo.bar` and `foo_bar` to the same key; second `apply_results` write silently
    overwrites the first. Also L56-57 docstring promises bare-name aliases that
    are never emitted (safe — actual behavior is stricter).
16. **`issue_sources/github_issues.py`** — no pagination; `fetch`/`fetch_issues`
    silently return ≤30 issues. Correctness gap.
17. **`issue_sources/markdown_todo.py:177-178`** — caller `comment` embedded
    verbatim in `&lt;!--gludd:{comment}--&gt;` (no `--&gt;` escaping); dedup check uses
    `marker.strip()` but appends with a leading space (double-annotation on
    re-parse).
18. **`is_path_within` footgun** — two definitions with **swapped arg order**
    (`sanitize.py:117` `(candidate, root)` vs `auth.py:114` `(base, candidate)`).
    Both individually correct; importing the wrong one inverts the check.
    **Fix:** rename one (e.g. `auth.py`'s → `is_join_within`).
19. **`runtime/release.py:37-63`** — `_check_pip_bundle` does not assert LICENSE
    is present in the wheel, so the new LICENSE-packaging guarantee is unguarded
    by the release gate (coverage gap, not an inconsistency).
20. **`agents/capabilities.py:68` / `context.py:63-86`** — `prepare_messages`
    docstring claims it bounds the prompt to the token window, but an oversized
    system or preserved message passes through uncapped. Either tighten the
    implementation or soften the docstring.
21. **`pipeline/__init__.py` + `issue_sources/__init__.py`** — empty stubs whose
    docstrings imply exports; `from general_ludd.pipeline import X` would
    `ImportError`. Wire up or trim the docstrings.
22. **`code_intelligence/rg_search.py:110`** — the `flags` param is appended
    verbatim *before* the `--` separator, so a caller passing untrusted strings
    can inject arbitrary `rg` flags (`--passthru`, `-e`, etc.); the `--` guard
    only protects `query`/`root`. Also `rg_search.py:201` (`returncode >= 2`)
    lets a signal-killed rg (negative rc) fall into the match-parse path with no
    error surfaced. **Fix:** allowlist `flags`; use `proc.returncode not in (0,1)`
    for the error branch.

---

## Confirmed SOUND (no action)

Alembic 005 ↔ ORM **exact parity** (all 5 tables + `prompt_profiles.updated_at`);
all 9 previously-failing test assertions **MATCH** their fixed state; LICENSE
packaging **consistent** across pyproject/spec/Containerfile; `routing_roles`
weights table (sum-to-1, full TaskType coverage, import-time asserts);
`scoring/router` cost-adjusted ranking (divide-by-zero guarded, fail-closed on
non-finite); dispatch capability gate (deny-by-default, all privileged kinds
covered); `SpendLimiter` (atomic check-and-record, correct `>` boundary);
`security/sanitize` `confine_path*` (realpath+commonpath fail-closed);
`capability_lattice` (deny-all baseline); `execution/engine` git hardening
(timeout + non-interactive env + killpg + realpath jail); `proc_sys` confinement
(normpath+realpath, `/proc/1/root` blocked); subprocess connectors (argv-list,
metachar+leading-dash validation; **note** `dmesg`/`macos_log` delegate the
timeout to an injected runner); observability connectors SSRF guards (sentry
best-in-class); `feature_verifier` path jail; `worker/app.py` M9 offload;
`db/repository` optimistic-concurrency guards + `avg_cost` label/key alignment;
`grafana_oncall` getaddrinfo guard; `elasticsearch` guard + structured-query
safety; `routers/dispatch` no auth bypass; docs coherent (CHANGELOG ↔ commit ↔
FOLLOWUP, both P0 blockers listed).
