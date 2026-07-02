# Remediation proposals — 2026-07-01

Ready-to-apply fix proposals for CONFIRMED findings in
`BACKLOG_FINDINGS_2026-07-01.md`. **None applied** — these are drafts for review.
Each was produced by a read-only architect agent; re-pin line numbers at apply time.

---

## 1. Ansible isolation silent no-op → fail closed (RECOMMENDED, minimal)

**File:** `src/general_ludd/ansible/core_runner.py:305-319` (`_isolation_supported`).

**Fix (one method):** `_isolation_supported` currently returns `True` whenever
`shutil.which(executable)` finds podman on PATH — but the native in-process
`PlaybookExecutor` (`:604-610`) applies NO confinement, so an isolation-required
play runs unconfined. Make it return `False` unconditionally (native path can't
confine), so the existing guard at `:238-251` fails closed. The `hide_paths`/
`ro_paths` kwargs builders (`isolation.py:59,74`) are dead code; a real backend
(ansible-runner subprocess or bwrap/podman wrapper) is a separate follow-up
feature, at which point re-gate on that concrete capability instead of `False`.

```diff
     @staticmethod
     def _isolation_supported(iso: Any) -> bool:
-        import shutil
-        executable = getattr(iso, "executable", None)
-        if not executable:
-            return False
-        return shutil.which(executable) is not None
+        # Native PlaybookExecutor cannot confine; hide_paths/ro_paths are only
+        # meaningful to the ansible-runner subprocess wrapper, which this runner
+        # does NOT use. A podman-on-PATH hit does not mean the play is confined.
+        # Until a real backend is wired into _execute_with_core, no isolation
+        # request can be honored -> return False so run_playbook fails closed.
+        return False
```

**Tests** (add to `tests/security/test_ansible_redteam.py`, mirrors
`TestProcessIsolationFailClosed`): the load-bearing regression patches
`shutil.which` → present AND asserts `status=="failed"` + `_execute_with_core`
never called; plus missing-executable-still-fails-closed and
disabled-isolation-runs-normally. No existing green test forces the "supported"
branch, so nothing breaks.

---

## 2. Filestore: SHA-256 verification + size caps before chmod+execute

**File:** `src/general_ludd/filestore/bootstrap.py`.

**Fix:** add a hardcoded `KNOWN_SHA256` map keyed by asset filename (digest over
the RAW archive bytes, before extraction); switch `download()` from buffered
`client.get()` to `client.stream()` with a `MAX_DOWNLOAD_BYTES` cap; verify the
sha256 BEFORE `_store_binary_and_chmod`; add a `MAX_MEMBER_BYTES` decompression-
bomb guard in `_extract_executable_member`. **Fail closed**: an asset with no
pinned digest is refused (so an unpinned version bump can't silently execute).
Full unified diff captured in the session transcript (agent a555cf8e) — imports
`hashlib` + `urlsplit`, adds `_verify_sha256`, rewrites `download()`.

**Sourcing the hashes:** OpenBao/OpenTofu ship `*_SHA256SUMS` release assets;
osquery/codebase-memory need `sha256sum <asset>` from a trusted download. Ship
the map commented-out with a `TODO(security)` — fail-closed means downloads are
refused until a maintainer fills real digests (bundled binaries remain the
offline path). ~11 assets total.

**Tests** (`tests/unit/test_bootstrap_integrity.py`, async, MockTransport):
good-hash→stored+chmod; bad-hash→refused/no-write/no-chmod (core regression);
missing-pin→refused; oversize body/Content-Length→rejected; bomb member→skipped;
hash-over-archive-not-member. NOTE: existing `test_filestore.py::TestBinaryBootstrapper`
tests store unverified fake bytes and will need valid hashes or expect-rejection.

---

## 3. Budget "unknown cost" fail-closed (re-scoped after verification)

**Correction:** the headline "estimate_call_cost → gates see 0.0" chain is DEAD
CODE; the wired daemon path passes a positive projection. The REAL residual:
literal `0.0` in `execution/engine.py:200/210` (`_budget_pre_check`) and
`budget_guard_check.py:72` make those pre-checks reactive-only, plus daemon
`_projected_cost_usd` stays 0.0 if no "default" profile exists.

**Fix:** make `estimate_call_cost` (`controllers/pid.py:143`) return `None` for
unknown tokens/price; widen gate signatures to `float | None` and route `None`
to the existing fail-closed branches (`SpendLimiter.try_charge` already
fail-closes on `None`); add a `check_per_call` non-finite guard (F2 — no test
currently asserts this). Keep the many legitimate explicit-`0.0` callers working
(0.0 = genuinely free, distinct from None = unknown).

**Tests:** extend `test_budget_manager_adversarial.py` (has NaN cases),
`test_budget_guard.py` (`TestCompositeFailClosed`), `test_spend_limiter.py`,
`test_budget_wiring.py` (no-default-profile integration); net-new
`test_check_per_call_fails_closed_on_nan` + a `test_budget_guard_check.py`.

---

## 4. XT-3/4 traces cross-tenant scoping (full diff drafted)

Add `project_id` to `ExecutionTrace` (set at producer `event_loop/loop.py`), filter
in `RecentTracesBuffer.recent()/snapshot()`, thread through `_traces_facet` +
`GET /api/traces` — mirrors the shipped XT-1/XT-2 `FeatureRepository.scoped`
pattern. Tests: extend `test_trace_store.py::TestRecentTracesBuffer` (buffer
filter) + new `tests/unit/test_facts_traces_isolation.py` modeled on
`test_todos_cross_tenant.py`; clone the `_xt8` embeddings-router tests for
`search_traces`. 5-file minimal diff (agent a6d39296 transcript).

## 5. Connectors redirect-SSRF: shared safe opener (full diff drafted)

New `src/general_ludd/security/http.py` — `_SsrfRedirectHandler` re-applies
`is_url_blocked` to every redirect target (or hard-blocks all, monday.py-style) +
`safe_urlopen` drop-in. Mechanical 2-line swap (`urlopen`→`safe_urlopen` + import)
across **25 connectors** (list in agent a3ed2e17 transcript) + 2 issue_sources +
mcp/catalog + secrets/manager. Tests: `tests/security/test_safe_opener.py`
(302→metadata-IP blocked; public redirect followed; strict mode) + per-connector
regression. Low blast radius (only 3xx path changes; happy paths untouched).

## 6. Security deny-list drift (full diff drafted)
One shared `GUARDRAIL_DIR_SEGMENTS` constant + `path_hits_guardrail_segment()` in
capability_lattice.py matched on whole path SEGMENTS against lexical AND realpath
forms, fail-closed; apply.py imports it (drops its leading-slash entries). Hardens
the third site hot_reloader.py:183 too. Tests lift the already-checked-in
`test_self_update_applier.py::test_protected_path_is_denied_and_not_written`
parametrized paths into all three matchers. (agent a7c75c1e)

## 7. Worker fail-open auth (full diff drafted)
Move the daemon's `require_auth = no_auth and not allow_no_auth` into
`security/auth.py::load_auth_posture` (single source of truth) + add
`allow_no_auth` to `AuthPosture`. Worker middleware needs ZERO change. **Key
test caveat**: `conftest.py:108-123` sets `GLUDD_ALLOW_NO_AUTH=1` suite-wide,
which masks the bug — new tests must `delenv` it to exercise the default-closed
path. (agent aa2c202a)

## 8. Git merge_branch lock-routing + squash fail-open (full diff drafted)
Add `_run_git_at(repo_path, *args)` (lock+timeout+non-interactive env keyed on
the passed path); `_run_git` delegates with self.repo_path; route merge_branch
(wrapped in one re-entrant `git_repo_lock` for atomicity) + the GA-1 family
through it; check the squash commit returncode (fixes fail-open). (agent a58384df)

## 9. Reload worker-registration auth (full diff drafted)
Validate `WorkerInfo.address` at register() (require https, reject
loopback/link-local/metadata/RFC-1918 via `is_url_blocked`) + guard before each
broadcast POST. NOTE: register() is currently unreachable over HTTP (no route) —
land the primitive fix now; attach allowlist + PSK-auth as acceptance criteria to
the future `POST /admin/workers/register` route. (agent a7f5538c)

---

## Re-verified OPEN findings (2026-07-01)

Re-confirmed **still open** on this session's dirty tree by direct source read.
All three are **POST-SHIP backlog** — they land on a SEPARATE branch, NOT the
current CI-green push, because their fixes flip bug-locking tests (applying them
now would turn currently-green tests red and add CI failures). Line numbers below
were read against the dirty tree and **MUST be re-pinned at apply time**.

### RV-1. XT-3/4 `/api/traces` cross-tenant leak — **CRITICAL**

**Evidence:**
- `src/general_ludd/observability/tracer.py:81-100` — `ExecutionTrace` carries no
  `project_id` field at all (todo_id only), so a trace has no tenant identity.
- `src/general_ludd/observability/trace_store.py:53-64` — the global ring buffer
  `recent()`/`snapshot()` filter only by `todo_id`; any caller reads any tenant's
  traces.
- `src/general_ludd/daemon/routers/facts.py:138,417,441` — `_traces_facet` and the
  `GET /api/traces` route return traces unscoped by the caller's project.
- `src/general_ludd/embeddings.py:658` — `_search_traces` searches the trace corpus
  unscoped, a second read path for the same leak.

**Fix (in-memory, NO migration):** thread `project_id` end-to-end —
1. add `project_id` to `ExecutionTrace` + its `to_dict()` (`tracer.py:81-100`);
2. tag the trace at build time in `event_loop/loop.py`;
3. filter on `project_id` in `RecentTracesBuffer.recent()`/`snapshot()`
   (`trace_store.py:53-64`);
4. scope `_traces_facet` and the `GET /api/traces` route by the request's project
   (`routers/facts.py:138,417,441`);
5. scope `_search_traces` (`embeddings.py:658`).
Mirrors the shipped XT-1/XT-2 `FeatureRepository.scoped` tenant pattern. See the
fuller draft in **§4** above; this entry confirms the leak is still live and the
route + embeddings read paths are both unscoped.

### RV-2. Filestore download → chmod+exec RCE — **HIGH**

**Evidence:** `src/general_ludd/filestore/bootstrap.py:296-308` — the download path
fetches with `follow_redirects=True`, then `chmod`s and executes `resp.content`
with **no checksum, no signature, and no size check**. A redirect to attacker
content, or an unpinned/tampered release asset, is fetched and run as an
executable.

**Fix (fail-closed):**
1. add a hardcoded `KNOWN_SHA256` map keyed by asset filename (digest over the RAW
   downloaded bytes);
2. add `_verify_download()` that computes the sha256 and **rejects any asset whose
   filename is not in the map OR whose digest mismatches** — call it BEFORE
   `_store_binary_and_chmod` so unknown/unpinned assets never reach chmod+exec;
3. follow-ups: a download size cap (reject oversize body / `Content-Length`) and
   atomic writes.
Fail-closed means an unpinned version bump is refused rather than silently
executed. See the fuller draft in **§2** above (stream + bomb-guard extensions).

### RV-3. Worker fails auth-OPEN by default — **HIGH**

**Evidence:** `src/general_ludd/security/auth.py:64` — `load_auth_posture` sets
`require_auth` from the environment flag ONLY. Result: the worker serves
`POST /jobs/execute` **unauthenticated when no PSK is configured**, while the
daemon (same posture question) is fail-closed. The worker is the asymmetry.

**Fix (fail-closed by default):**
`require_auth = require_auth_env(source) or (no_psk and not GLUDD_ALLOW_NO_AUTH)`
— i.e. require auth when the env demands it, OR when no PSK is set and the operator
has not explicitly opted into no-auth via `GLUDD_ALLOW_NO_AUTH`. Then flip the
bug-locking test `test_w5_6_worker_auth.py` to expect **503** (auth-required)
instead of the current open-serve expectation. See **§7** above for the
single-source-of-truth `AuthPosture.allow_no_auth` refactor and the
`conftest.py:108-123` suite-wide `GLUDD_ALLOW_NO_AUTH=1` masking caveat (new tests
must `delenv` it).

---

## Session 2026-07-01 re-verified findings (post-ship branch)

Prioritized table of CONFIRMED findings re-verified by direct source read this
session. These land on the POST-SHIP branch (several flip bug-locking tests).
Line numbers were read against the dirty tree and **MUST be re-pinned at apply time**.

### Top 4 — apply first (highest severity, exact fix mechanism)

- [ ] **(1) XT-3/4 `/api/traces` cross-tenant leak — CRITICAL.**
  `observability/tracer.py:81-100` (`ExecutionTrace` has no `project_id`),
  `observability/trace_store.py:53-64` (`recent()`/`snapshot()` filter by
  `todo_id` only), `daemon/routers/facts.py:138,417,441` (`_traces_facet` +
  `GET /api/traces` unscoped), `embeddings.py:658` (`_search_traces` unscoped).
  **Fix:** thread `project_id` end-to-end — add the field to `ExecutionTrace`
  (+`to_dict()`), tag at build in `event_loop/loop.py`, filter in
  `RecentTracesBuffer.recent()/snapshot()`, scope `_traces_facet` +
  `GET /api/traces` by the request's project, and scope `_search_traces`.
  In-memory, NO migration. Mirrors the shipped XT-1/XT-2 `FeatureRepository.scoped`
  pattern. Full detail in **RV-1** and **§4**.
- [ ] **(2) Filestore download → chmod+exec RCE — HIGH.**
  `filestore/bootstrap.py:296-308` fetches (`follow_redirects=True`), then chmods
  and executes `resp.content` with no checksum/signature/size check.
  **Fix:** add a hardcoded `KNOWN_SHA256` map keyed by asset filename (digest over
  the RAW bytes) + `_verify_download()` that computes sha256 and **rejects any
  asset not in the map OR whose digest mismatches**, called BEFORE
  `_store_binary_and_chmod` so unpinned/tampered assets never reach chmod+exec
  (fail-closed). Follow-ups: `MAX_DOWNLOAD_BYTES` stream cap + bomb guard. Full
  detail in **RV-2** and **§2**.
- [ ] **(3) Worker fails auth-OPEN by default — HIGH.**
  `security/auth.py:64` — `load_auth_posture` sets `require_auth` from the env flag
  ONLY, so the worker serves `POST /jobs/execute` unauthenticated when no PSK is
  set (the daemon is fail-closed; the worker is the asymmetry).
  **Fix:** `require_auth = require_auth_env(source) or (no_psk and not
  GLUDD_ALLOW_NO_AUTH)`, then flip the bug-locking `test_w5_6_worker_auth.py` to
  expect **503** (auth-required). New tests must `delenv GLUDD_ALLOW_NO_AUTH`
  (conftest.py:108-123 sets it suite-wide and masks the bug). Full detail in
  **RV-3** and **§7**.
- [ ] **(4) `auto_queue` auto-applies self-improvement by default — HIGH.**
  `self_improve/gate.py:25` + `event_loop/loop.py:~2659` — `auto_queue` defaults
  `True`, so self-improvement is applied without opt-in.
  **Fix:** flip the default to `False` in **both** sites (fail-closed / opt-in);
  update `test_self_improve_slice.py:103` to expect opt-in behavior.

### Full re-verified findings table

| Severity | File:line | Fix |
|----------|-----------|-----|
| HIGH | `controllers/budget.py:82` | `check_per_call` fails OPEN on `NaN` cost → add `math.isfinite` guard so a non-finite cost fails **closed** (reject the call) instead of passing the limit comparison. |
| HIGH | `security/permissions.py:514` | `_intersect_constraints` **widens** disjoint prefixes (returns a broader set) → return `None` (empty/deny) unless one prefix `startswith` the other; only then keep the more specific. |
| HIGH | `self_improve/gate.py:25` + `event_loop/loop.py:~2659` | `auto_queue` defaults `True` (auto-applies self-improvement) → flip default to `False` in **both** sites; update `test_self_improve_slice.py:103` to expect opt-in. |
| HIGH | `models/gateway.py:561` | per-call `kwargs` can override the SSRF-validated `base_url`/`api_key` → refuse override, or re-run URL validation on any overriding value before use. |
| HIGH | `capability_lattice.py:58` + `self_update/apply.py:51` | leading-slash deny-list **drift** (each matches differently) → normalize via `realpath` + whole-segment match in a shared matcher so both sites deny identically. |
| HIGH | `execution/tool_loop.py:129` | no role gate before dispatching MCP tool calls → add `check_dispatch(role, mcp)` so tool execution is capability-checked against the caller's role. |
| HIGH | `reload/worker_broadcast.py:55` (latent) | PSK-Bearer accepted from **any** address → validate `WorkerInfo.address` (https, reject loopback/link-local/metadata/RFC-1918) at register(). |
| HIGH | `integrity/scanner.py:97` | baseline is **unsigned** + silently rebaselines → HMAC-sign the baseline and **fail closed** on missing/invalid signature (no silent re-accept). |
| HIGH | `ansible/core_runner.py:239` | isolation **fails open** when podman is on PATH but native executor can't confine → `_isolation_supported` returns `False` so the guard fails closed. |
| HIGH | `ansible/core_runner.py:626` | `extra_env` bypasses the env allowlist → filter `extra_env` through the allowlist before it reaches the play. |
| HIGH | `secrets/manager.py:188` | `resolve()` skips `_enforce_permission` → add the permission check to `resolve()` (parity with the other accessors). |
| MEDIUM | ~26 urllib connectors | follow redirects with **no SSRF re-check** → route through a shared re-validating opener (`security/http.py`, `monday.py` template) that re-applies `is_url_blocked` on every 3xx target. |
| MEDIUM | `mcp/transport.py:55` | accepts `pkg@2` (partial version) and **skips `uvx`** → require full semver and support the `uvx` runner. |
| MEDIUM | `git_automation/repo.py:594` (latent) | `merge_branch` bypasses the repo lock → route through a locked `_run_git` (lock + timeout + non-interactive env). |
| MEDIUM | remediation retry loop | retry-**storm** (no backoff/state) → add a cooldown + explicit state transition so failed remediations don't hot-loop. |

---
**SECURITY REMEDIATION PACKAGE COMPLETE** — 9 findings have drafted unified-diff
fixes + test plans (ansible isolation, filestore checksum, budget residual,
traces scoping, connectors redirect, deny-list drift, worker auth, git merge
lock, reload registration). ToolCallLoop capability gate + per-project secrets +
TodoRepository allowlist proposals also drafted (transcripts). None applied —
these are the SECURITY backlog, DISTINCT from the CI-green failing-test work
(which fixes currently-FAILING tests; applying these security fixes would break
their bug-locking tests and ADD failures, so they must be sequenced AFTER green CI
with coordinated test updates).
