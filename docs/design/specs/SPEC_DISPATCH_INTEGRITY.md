# SPEC — Dispatch Integrity (S1, S2, S10)

Status: READY-TO-IMPLEMENT (2026-07-14). Turnkey. Assume the implementer has
**not** read any prior audit. Every claim below was re-verified against the
working tree on 2026-07-14; line numbers are current as of that date (the parent
audit `docs/design/STUB_CLOSURE_SPEC.md` had drifted by a few lines in places —
this spec supersedes it for S1/S2/S10).

This is the "dispatch integrity" batch from `STUB_CLOSURE_SPEC.md` §"Closure
protocol": S1, S2, S10 are grouped because **S1 and S10 both edit
`agents/dispatcher.py` + `daemon.py`'s `_gateway_executor` block**, so they must
land in a defined order (see §7). S2 is independent of those two files but ships
in the same batch.

Bash in this repo is **make-only**. Use the Read/Edit/Write tools for all file
edits. For searches use `make grep Q='pattern' PATH_='dir'` (note the trailing
underscore on `PATH_`; plain `PATH=` is silently ignored because it shadows the
shell `$PATH`). Iterate tests with `make test-iso TESTFILE='path::Class::test'`.

---

## 0. TL;DR of the three bugs

| # | One-line symptom | Fix shape | Naive-fix trap |
|---|---|---|---|
| **S1** | With no `model_profiles/` on the config path, every dispatched subagent returns `status="completed", output=""` silently; `/healthz`+`/readyz` still green; hot-reload can't recover it. | Make the dispatcher's default executor **raise** (fail closed), report `not-ready` on `/readyz`, and make `/admin/config/reload` truthfully report `restart_required`. | Logging a warning is useless: **nothing in `src/` reads `AgentTaskResult.status`**, so a log has no receiver. Must fail closed. |
| **S2** | Review returns dispatched over the worker HTTP path (and the Ansible runner path) are **stranded forever** at `claimed_for_review`; self-improve training-data collection always gets zero rows. | Worker → honest `501`; the loop's HTTP + runner branches must **release the claim** instead of trusting a fake ack. | Converting the worker to `501` **alone changes nothing** — the loop never inspects `resp.status_code`, so the claim stays stranded while the worker test goes green. And "just run the playbook" **rubber-stamps every return complete** (the playbook hardcodes `decision: "complete"`). |
| **S10** | Cost/perf routing is built but dead: `_gateway_executor` hardcodes `profile_id="default"` for every dispatched subagent. | Route via `ModelGateway.select_cost_effective_profile()` (returns a `ModelProfile` with a usable `profile_id`). | `ModelPerformanceRouter.select_model()` returns `{"service","model_name"}` — a **different keyspace** than `profile_id`. Wiring it naively hands `_gateway_executor` a value it cannot pass to `call_model_with_retry`. |

---

## 1. VERIFY-FIRST (run these before touching anything)

The implementer runs each check and confirms the quoted current state still
holds. If any drifts, re-read the surrounding function before editing.

### S1 verify

1. `agents/dispatcher.py` — the no-op default executor:
   - Read `src/general_ludd/agents/dispatcher.py:46-47`. Expect:
     ```python
     async def _noop_executor(task: AgentTask) -> str:
         return ""
     ```
   - Read line `65`. Expect: `self._executor: ExecutorFn = executor or _noop_executor`
   - `dispatch_one` at `238`; the success path builds `status="completed"` at
     `384-390`; the `except Exception` path at `400-422` returns
     `status="failed", output=sanitize_error_message(str(exc))` and logs
     `logger.exception("Task %s failed", ...)` at `406`. **This is the fail-closed
     conversion S1 relies on: an executor that raises becomes a `failed` result.**
2. `daemon.py` — the missing `else`:
   - `src/general_ludd/daemon.py:1358` → `model_gateway = None`.
   - `:1360` → `if model_profiles:` builds the gateway (`ModelGateway(...)`
     at `1368-1385`).
   - `:2007` → `dispatcher_executor = None`.
   - `:2019` → `if model_gateway is not None:` builds `_gateway_executor`
     (2056-2108), wraps it (`make_spend_guarded_executor`, 2110-2114), and there
     is **no `else:`**.
   - `:2116-2122` → `AgentDispatcher(registry=registry, executor=dispatcher_executor, ...)`
     — so when the gateway is None, `executor=None` reaches the dispatcher.
3. `load_model_profiles` returns `[]` on a missing dir — `daemon.py:598-616`
   (`if profiles_dir is None: return []`, `if not pdir.is_dir(): return []`).
4. Health endpoints ignore the gateway:
   - `/readyz` — `daemon.py:2856-2883`. Checks only `_degraded` and
     `_event_loop_task`; returns `{"status": "ready"}` regardless of gateway.
   - `/healthz` — `daemon.py:2794-2845`. Same; no gateway check. Both are in
     `_PUBLIC_PATHS` (`daemon.py:114`, `:2652`) → unauthenticated.
5. Reload never rebuilds the gateway — `routers/reload.py:193-235`
   (`admin_config_reload`). It overwrites `app.state._startup_config`
   (`:204`) and merges live-reloadable values into `event_loop.config`
   (`:216-228`), but never touches `app.state._model_gateway`.
6. No consumer reads `AgentTaskResult.status`. Run:
   ```text
   make grep Q='dispatch_one' PATH_='src'
   ```
   Expect exactly three real call sites, none of which inspect `.status`:
   - `routers/pause.py:161` — `await dispatcher.dispatch_one(task)` (result
     **discarded** — not even assigned).
   - `pipeline/daemon_adapters.py:94` — `return await dispatcher.dispatch_one(task)`
     (returns to the pipeline; pipeline is default-OFF, so no live traffic).
   - `daemon_wiring.py:155` — `result = await agent_dispatcher.dispatch_one(task)`
     then `return result.output or ""` (reads `.output`, never `.status`).
   `dispatch_many` (`dispatcher.py:428`) has **no external `src/` caller** — the
   only reference outside its own body is a docstring; do not spend effort on it.
7. The bug-enshrining test:
   - `tests/unit/test_h5_gateway_executor.py:73-82` `test_dispatcher_falls_back_to_noop`
     asserts `r.status == "completed"` and `r.output == ""`. **This test blesses
     the bug and MUST be rewritten** (§2.1).
   - Same file `:27-31` `test_noop_executor_returns_empty_string`, and the import
     at `:8` (`... _noop_executor`), also change.
8. No existing exception base in the agents package (so the new error is
   module-local):
   ```text
   make grep Q='class.*Error' PATH_='src/general_ludd/agents'
   ```
   Expect: no `Exception` subclass. `ExecutorNotConfiguredError` will be defined
   in `dispatcher.py`.
9. Enumerate tests that build a dispatcher with **no** executor (they rely on the
   silent-noop default and may change behavior once it fails closed):
   ```text
   make grep Q='AgentDispatcher(' PATH_='tests'
   ```
   There are ~44 such construction sites. **Two traps when triaging them:**
   - `executor` is the **2nd positional** parameter of
     `AgentDispatcher.__init__(self, registry, executor=None, ...)`, so a
     positionally-passed executor (`AgentDispatcher(reg, my_exec)`) will NOT show
     an `executor=` token — grepping for `executor=` alone yields false positives.
     Read each multi-line call and check for a 2nd positional arg too.
   - A site with truly no executor only changes behavior if its dispatched task
     **passes every guard** (agent found + enabled + not paused + `can_invoke` +
     nesting/rate/spiral) and thus actually reaches the executor. Most sites
     short-circuit earlier to `status="failed"` (agent-not-found / permission /
     depth guards) and are UNAFFECTED — e.g.
     `tests/unit/test_agent_dispatcher_construction.py:33`
     `test_dispatch_noop_executor` dispatches to a nonexistent agent, so the
     not-found guard returns `failed` before the executor runs.
   The only confirmed behavior-locking test is
   `tests/unit/test_h5_gateway_executor.py:73` `test_dispatcher_falls_back_to_noop`
   (§2.5). Pay particular attention when triaging to the sites that dispatch a
   *valid* task and observe the executor —
   `tests/unit/test_dispatcher_semaphore.py`, `tests/unit/test_d7_3_quiesce_resume.py`,
   `tests/unit/test_run_recorder_dispatch.py:150`, `tests/e2e/test_e2e_dispatcher.py`
   — but note these generally DO pass an executor (often positionally, hence the
   grep false-positive). Any that both (a) omit an executor entirely and (b)
   assert `status == "completed"` on a passing dispatch must be given an explicit
   executor or updated. Do this triage before editing so §6 landing step 1 has the
   full list.

### S2 verify

1. Worker fake-ack — `src/general_ludd/worker/app.py:537-539`:
   ```python
   @application.post("/jobs/return-review")
   async def return_review_job(job: JobSpec) -> dict[str, Any]:
       return {"status": "ack", "job_id": job.job_id, "detail": "Return review queued for daemon reviewer"}
   ```
   The three siblings immediately below (`/jobs/validate` 541-556,
   `/jobs/policy-validate` 558-571, `/jobs/reload-request` 573-586) each
   `raise HTTPException(status_code=501, detail={...})`. `return-review` is the
   only one that lies about doing work — copy the sibling shape.
2. `claim_unreviewed` selects only `created` — `db/repository.py:725-772`.
   The `select` filters `TaskReturnModel.status == "created"` (`:736`); rows are
   flipped to `claimed_for_review` at `:751` (guarded UPDATE) and `:768`
   (in-memory). **Nothing re-claims a `claimed_for_review` row** — there is no
   reaper for that status.
3. `TaskReturnStatus.REVIEWED` is never assigned in `src/` —
   `schemas/task_return.py:11-15`:
   ```python
   class TaskReturnStatus(enum.StrEnum):
       CREATED = "created"
       CLAIMED_FOR_REVIEW = "claimed_for_review"
       REVIEWED = "reviewed"
       ARCHIVED = "archived"
   ```
   Run `make grep Q='reviewed' PATH_='src'`: the only place that **reads**
   `status == "reviewed"` is `event_loop/loop.py:4248` inside
   `_collect_training_data_from_returns` (4222-4253); no `src/` line ever
   **assigns** `"reviewed"`/`REVIEWED` to a `.status`. So that self-improve query
   structurally returns zero rows.
4. The review-dispatch method — `event_loop/loop.py:1073-1224` `_dispatch_review_job`:
   - In-process branch (`1091-1125`): when a reviewer is wired, calls
     `_review_in_process(tr)` (the **real, working** reviewer — `1255-1350`,
     applies the decision via `apply_decision`). Only the `TimeoutError` handler
     releases the claim. This path is correct; do not break it.
   - Runner branch (`1140-1188`): `self._runner.run_playbook("return_review.yml", ...)`
     — the `await asyncio.wait_for(...)` **result is discarded**; only
     `except TimeoutError` (`1157`) releases the claim.
   - HTTP branch (`1189-1223`): POSTs to the worker `/jobs/return-review`
     (`1193-1199`), then `await self._persist_review_response(tr, resp)` (`1200`).
     **`resp.status_code` is never inspected.** Only `except TimeoutError`
     (`1201`) releases the claim.
5. `_persist_review_response` — `event_loop/loop.py:1392-1425`. Only acts if
   `data.get("decision")` is truthy (`:1410-1411`); creates a `TaskDecisionModel`;
   **never sets `tr.status`**. The fake-ack body has no `decision` key, so this is
   a no-op against the real worker response — the claim is left at
   `claimed_for_review`.
6. Release logic is duplicated **3×** (all `tr.status = "created"` + `updated_at`
   bump + `flush()` + todo → `BLOCKED`): `1110-1124`, `1173-1187`, `1209-1223`.
7. Playbook is a hardcoded stub — `playbooks/return_review.yml:30-40`:
   ```yaml
   - name: Run return review through model gateway
     ansible.builtin.set_fact:
       review_result:
         decision: "complete"        # line 35 — no model call, always "complete"
         confidence: 0.8
   ```
   Confirms: "just run the playbook" would rubber-stamp every return complete.
8. Tests to invert:
   - `tests/unit/test_worker.py:197-205` `test_worker_return_review_endpoint`
     (asserts only `resp.status_code == 200`).
   - `tests/e2e/test_obj03_worker.py:67-78` `test_return_review_endpoint`
     (asserts `resp.status_code in (200, 202)`).
   - `tests/unit/test_event_loop.py:143-168`
     `test_dispatch_review_job_runner_path_completes_within_timeout` — asserts
     `tr.status == TaskReturnStatus.CLAIMED_FOR_REVIEW` stays unchanged on the
     runner happy path, with the comment *"Happy path must not touch/release the
     claim."* After the S2 fix the runner path **does** release → this test
     inverts (§2.2).
   - NOTE: `tests/unit/test_event_loop.py:170-181`
     `test_event_loop_skips_reviewed_return` exercises a **different, vestigial**
     method `EventLoop.dispatch_return_review` (`loop.py:4660-4669`), NOT
     `_dispatch_review_job`. It is not part of the live path and does not need to
     change for S2 (but see §2.4 note).

### S10 verify

1. `_gateway_executor` hardcodes the profile — `daemon.py:2056-2057`:
   ```python
   async def _gateway_executor(task: AgentTask) -> str:
       profile_id = "default"
   ```
   used at `:2091-2095` (`model_gateway.call_model_with_retry(profile_id, ...)`).
2. `ModelGateway.select_cost_effective_profile` is dead — `models/gateway.py:1848-1880`
   (`@staticmethod`, signature `(profiles: list[ModelProfile], budget_remaining: float) -> ModelProfile | None`).
   `make grep Q='select_cost_effective_profile' PATH_='src'` → the only non-def
   hit is a **docstring** at `scheduling/scheduler.py:34`. Zero real callers.
3. `ModelPerformanceRouter.select_model` is dead **and wrong-keyspace** —
   `models/performance_router.py:105`. It returns
   `{"service", "model_name", "score", "strategy", "fallback", "reason"}`, NOT a
   `profile_id`. `make grep Q='select_model(' PATH_='src'` → only its own
   definition. **Do not wire this one** (see §3, the trap).
4. Profile accessors exist — `gateway.py:513-514` `get_profile(profile_id)`,
   `:690-691` `list_profiles() -> list[ModelProfile]`. A `ModelProfile` carries
   `.model_profile_id`.
5. The todo path already routes correctly (the model to imitate, but NOT to
   reuse verbatim — it needs a `task_type` the dispatch path lacks):
   `event_loop/loop.py:597-613` `_resolve_adaptive_prompt` →
   `self._adaptive_router.route(task_type=..., default_model_profile="default")`
   returns `decision.selected_model_profile_id` (a real `profile_id`).

---

## 2. S1 — dispatcher silently falls back to a no-op executor

### Root cause
`AgentDispatcher.__init__` (`dispatcher.py:65`) coalesces a `None` executor to
`_noop_executor`, which returns `""`. The daemon passes `executor=None` whenever
`model_gateway is None` (`daemon.py:2007` default + missing `else` at `:2019` +
`:2118`). `model_gateway` is `None` whenever `load_model_profiles` returns `[]`,
which happens when no `model_profiles/` dir is on the discovery path
(`$GLUDD_CONFIG_DIR` → `~/.config/general-ludd` → `/etc/general-ludd`). **The
repo's own `config/` is NOT on that path**, so simply running the daemon from a
checkout triggers it. Every dispatched subagent then returns
`status="completed", output=""` with no warning, and both health probes stay
green.

**Why "just log a warning" is wrong:** verify-step S1.6 proves **nothing in
`src/` reads `AgentTaskResult.status`** — the two live consumers read `.output`
(`daemon_wiring.py:156`) or discard the result (`pause.py:161`). A log line has
no receiver that can act on it. The fix must **fail closed** so the failure
surfaces as (a) a non-empty error in `.output`, (b) a red `/readyz`, and (c) a
truthful reload response.

### 2.1 Fix — dispatcher raises instead of no-op

File: `src/general_ludd/agents/dispatcher.py`

**Before** (`46-47`):
```python
async def _noop_executor(task: AgentTask) -> str:
    return ""
```
**After**:
```python
class ExecutorNotConfiguredError(RuntimeError):
    """Raised when the dispatcher has no real executor wired.

    Happens when the daemon booted with no ModelGateway (no model_profiles on the
    config discovery path). Raising here makes dispatch FAIL CLOSED: dispatch_one's
    ``except Exception`` handler turns it into ``status="failed"`` with this
    message, instead of the old silent ``status="completed", output=""`` no-op
    that no caller could detect.
    """


async def _unconfigured_executor(task: AgentTask) -> str:
    raise ExecutorNotConfiguredError(
        "AgentDispatcher has no model-backed executor: the daemon booted without "
        "a ModelGateway (no model_profiles discovered on the config path). Set "
        "GLUDD_CONFIG_DIR or populate config/model_profiles/ and restart."
    )
```

**Before** (`65`):
```python
        self._executor: ExecutorFn = executor or _noop_executor
```
**After**:
```python
        self._executor: ExecutorFn = executor or _unconfigured_executor
```

No other change is needed in `dispatch_one`: the raise propagates out of
`await self._executor(task)` (`:370`) into the existing `except Exception`
(`:400`), which logs `logger.exception(...)` and returns
`status="failed", output=sanitize_error_message(str(exc))`. `_role_handler`
(`daemon_wiring.py:156`) then returns that error string as the tool output
instead of `""`, surfacing the failure into the event-loop tool-call.

### 2.2 Fix — daemon sets a not-ready flag (the missing `else`)

File: `src/general_ludd/daemon.py`

**Before** (`2019`, opening of the gateway-executor block):
```python
        if model_gateway is not None:
            logger.info(
                "Gateway-backed executor enabled with %d model profile(s)",
                len(model_profiles),
            )
```
Insert a flag assignment inside this branch, and add the `else`. Concretely,
set `app.state._model_unconfigured = False` at the top of the `if` branch, and
append after the `make_spend_guarded_executor(...)` wrap (i.e. immediately before
`app.state._agent_dispatcher = AgentDispatcher(` at `2116`):
```python
        else:
            logger.error(
                "No ModelGateway configured (no model_profiles on the config "
                "discovery path: $GLUDD_CONFIG_DIR -> ~/.config/general-ludd -> "
                "/etc/general-ludd). Subagent dispatch will FAIL CLOSED "
                "(ExecutorNotConfiguredError) and /readyz will report not-ready. "
                "Set GLUDD_CONFIG_DIR or add config/model_profiles/*.yml."
            )
            app.state._model_unconfigured = True
```
Put `app.state._model_unconfigured = False` as the first line inside the
`if model_gateway is not None:` branch (right after the `logger.info(...)`), so
the flag is always defined regardless of branch.

### 2.3 Fix — `/readyz` reports not-ready when the gateway is unconfigured

File: `src/general_ludd/daemon.py`, `readyz()` (`2856-2883`).

**Before** (the tail of the handler, `2877-2883`):
```python
        if el_task.done():
            reason = "event_loop_cancelled" if el_task.cancelled() else "event_loop_done"
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": reason},
            )
        return {"status": "ready"}
```
**After** (insert the gateway check just before the final `return`):
```python
        if el_task.done():
            reason = "event_loop_cancelled" if el_task.cancelled() else "event_loop_done"
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": reason},
            )
        # S1: a daemon with no ModelGateway cannot execute a single dispatched
        # subagent (the dispatcher's executor raises ExecutorNotConfiguredError).
        # Report not-ready so probes/operators see the degraded capability instead
        # of a green /readyz over a no-op dispatch path.
        if getattr(app.state, "_model_unconfigured", False):
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": "model_gateway_unconfigured"},
            )
        return {"status": "ready"}
```

Optional (non-breaking observability nicety, mirrors the existing `no_auth` etc.
flags): in `healthz()` (`2838-2845`), add
`"model_unconfigured": bool(getattr(app.state, "_model_unconfigured", False))`
to both the `degraded` and `healthy` return dicts. `/healthz` keeps returning
`200` (it is a liveness probe); only `/readyz` (readiness) flips to `503`.

### 2.4 Fix — reload truthfully reports `restart_required`

File: `src/general_ludd/routers/reload.py`, `admin_config_reload` (`193-235`).

A gateway that booted `None` **cannot be hot-rebuilt from this handler**:
`ModelGateway(...)` construction (`daemon.py:1368-1385`) and the downstream
`_gateway_executor` + `AgentDispatcher` wiring depend on ~15 lifespan-local
subsystems (`provider_registry`, `secrets_resolver`, `budget_guard`,
`pause_controller`, `spend_limiter`, `health_tracker`, `metrics_collector`, …)
that are not reachable from the router. So the honest, low-risk fix is to
**detect the condition and report it**, not to fake success.

**Before** (`230-235`):
```python
        subsys = _get_or_create_subsystems(app)
        bus = subsys.get("bus")
        if bus is not None:
            bus.publish(ConfigReloadedEvent(scope="config"))

        return {"success": True, "merged": merged}
```
**After**:
```python
        subsys = _get_or_create_subsystems(app)
        bus = subsys.get("bus")
        if bus is not None:
            bus.publish(ConfigReloadedEvent(scope="config"))

        # S1: a ModelGateway that booted None is not rebuilt here (its
        # construction + dispatcher-executor wiring live in lifespan startup with
        # subsystems this handler cannot reach). Tell the operator the truth
        # instead of returning success over a still-no-op dispatch path.
        gateway_unconfigured = getattr(app.state, "_model_gateway", None) is None
        now_has_profiles = bool(live_reloadable["model_profiles"])
        restart_required = gateway_unconfigured and now_has_profiles
        return {
            "success": True,
            "merged": merged,
            "restart_required": restart_required,
            "restart_reason": (
                "model gateway was unconfigured at startup; restart the daemon to "
                "apply the newly-discovered model_profiles"
                if restart_required
                else None
            ),
        }
```
(A full hot-rebuild — factoring a `build_model_gateway(...)` helper called from
both startup and reload, then rebuilding the dispatcher executor and flipping
`_model_unconfigured`/`_model_gateway` — is a larger, higher-risk follow-up. It
is explicitly OUT OF SCOPE for this spec; the `restart_required` signal satisfies
the "or explicitly report restart required" clause of the requirement.)

### 2.5 S1 tests (test-first)

File: `tests/unit/test_h5_gateway_executor.py`

- **Import (`:8`)** — change
  `from general_ludd.agents.dispatcher import AgentDispatcher, AgentTask, _noop_executor`
  to add `ExecutorNotConfiguredError, _unconfigured_executor` and drop
  `_noop_executor`.
- **Rewrite `test_noop_executor_returns_empty_string` (`27-31`)** →
  ```python
  def test_unconfigured_executor_raises(self):
      task = AgentTask(task_id="t1", agent_name="c", description="d", prompt="p")
      import asyncio
      with pytest.raises(ExecutorNotConfiguredError):
          asyncio.run(_unconfigured_executor(task))
  ```
- **Rewrite the bug-enshrining `test_dispatcher_falls_back_to_noop` (`73-82`)** →
  ```python
  @pytest.mark.asyncio
  async def test_dispatcher_without_executor_fails_closed(self):
      reg = AgentRegistry()
      reg.register(AgentConfig(name="g", type=AgentType.SUBAGENT, description="d"))
      invoker = _register_invoker(reg)
      d = AgentDispatcher(registry=reg)  # no executor -> must fail closed
      r = await d.dispatch_one(AgentTask(
          task_id="t4", agent_name="g", description="d", prompt="p", invoker_name=invoker,
      ))
      assert r.status == "failed"                 # FAILS TODAY (currently "completed")
      assert r.output != ""                       # FAILS TODAY (currently "")
      assert "executor" in r.output.lower() or "gateway" in r.output.lower()
  ```
  The two assertions `r.status == "failed"` and `r.output != ""` fail against
  the current code — that is the test-first proof.

New health test — File: `tests/unit/test_readyz_model_unconfigured.py` (new):
```python
import pytest
from starlette.testclient import TestClient
# Build the app via the project's existing test harness for daemon endpoints;
# mirror the setup in tests/unit/test_h3_readyz.py / test_w3_4_readyz.py.

def test_readyz_503_when_model_unconfigured(daemon_app):
    daemon_app.state._model_unconfigured = True
    daemon_app.state._degraded = None
    # event loop task present + not done (reuse the fixture's live-task setup)
    with TestClient(daemon_app) as c:
        resp = c.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["reason"] == "model_gateway_unconfigured"

def test_readyz_200_when_model_configured(daemon_app):
    daemon_app.state._model_unconfigured = False
    with TestClient(daemon_app) as c:
        resp = c.get("/readyz")
    assert resp.status_code == 200
```
(Adopt the exact app fixture used by the existing readyz tests — see
`tests/unit/test_h3_readyz.py` and `tests/unit/test_w3_4_readyz.py`, which
already stand up the daemon app and set `_event_loop_task`.)

New reload test — File: `tests/unit/test_config_reload_restart_required.py` (new):
```python
def test_reload_reports_restart_required_when_gateway_unconfigured(daemon_app, tmp_path):
    # Gateway booted None; new config dir now has a model_profiles/*.yml.
    daemon_app.state._model_gateway = None
    # point _config_dir at a dir containing model_profiles/default.yml (see the
    # config fixtures used by tests/unit/test_w3_12_reload.py)
    with TestClient(daemon_app) as c:
        resp = c.post("/admin/config/reload")
    body = resp.json()
    assert body["success"] is True
    assert body["restart_required"] is True            # FAILS TODAY (key absent)
    assert "restart" in (body["restart_reason"] or "").lower()
```

---

## 3. S10 — cost/perf routing built but never invoked on dispatch

### Root cause
`_gateway_executor` (`daemon.py:2056-2057`) hardcodes `profile_id = "default"`
for every dispatched subagent, so `ModelGateway.select_cost_effective_profile`
(`gateway.py:1848`) is never called — cheaper-equivalent routing is defeated for
all subagent dispatch. (The EventLoop **todo** path routes correctly via
`_adaptive_router` — `loop.py:608` — so only subagent dispatch is unwired.)

**Why the naive fix is wrong:** the other dead router,
`ModelPerformanceRouter.select_model()` (`performance_router.py:105`), returns
`{"service","model_name",...}` — a **service/model_name keyspace**, not a
`ModelGateway` `profile_id`. Passing its `model_name` to
`model_gateway.call_model_with_retry(profile_id, ...)` fails (no such profile).
`AdaptiveRouter.route()` returns a real `profile_id` but requires a `task_type`
derived from a todo's `work_type`, which an `AgentTask` does not carry
(`agents/types.py:42-54` has `agent_name`, `estimated_effort` — no `work_type`).
The correct, minimal, correctly-keyed choice is
`select_cost_effective_profile(list_profiles(), budget_remaining)`, which returns
a `ModelProfile` whose `.model_profile_id` feeds `call_model_with_retry`
directly.

### 3.1 Fix — a small unit-testable selector helper

`_gateway_executor` is a closure inside the daemon lifespan (hard to unit-test in
isolation), so factor the selection into `daemon_wiring.py` next to
`make_spend_guarded_executor`.

File: `src/general_ludd/daemon_wiring.py` — append:
```python
def select_dispatch_profile_id(
    gateway: Any,
    spend_limiter: Any | None,
    *,
    default: str = "default",
) -> str:
    """Pick the cheapest budget-eligible ModelGateway profile_id for a dispatch.

    S10: subagent dispatch previously hardcoded ``profile_id="default"``. Route to
    the cheapest profile that fits the remaining spend window, falling back to
    ``default`` when routing yields nothing (empty profile set, or all over
    budget). ``select_cost_effective_profile`` returns a ``ModelProfile`` (not a
    ``{service, model_name}`` dict), so ``.model_profile_id`` is directly usable
    with ``call_model_with_retry``.
    """
    budget_remaining = (
        spend_limiter.remaining() if spend_limiter is not None else float("inf")
    )
    try:
        selected = gateway.select_cost_effective_profile(
            gateway.list_profiles(), budget_remaining
        )
    except Exception:
        logger.warning("select_dispatch_profile_id: routing failed; using %r", default, exc_info=True)
        return default
    if selected is None:
        return default
    return selected.model_profile_id
```

File: `src/general_ludd/daemon.py`, `_gateway_executor` (`2056-2101`).

**Before** (`2056-2057`):
```python
            async def _gateway_executor(task: AgentTask) -> str:
                profile_id = "default"
```
**After** — remove the hardcoded assignment from the top, and compute the
`profile_id` **after** the early-returning `research` branch (`2081-2086`) and
just before the `try:` at `2087`, so the research short-circuit does not pay for
routing:
```python
            async def _gateway_executor(task: AgentTask) -> str:
                budget_manager = getattr(app.state, "_budget_manager", None)
                # ... (unchanged budget_manager daily/per-todo checks 2058-2080) ...
                if task.agent_name == "research":
                    # ... unchanged research branch, returns early ...
                    ...
                from general_ludd.daemon_wiring import select_dispatch_profile_id
                profile_id = select_dispatch_profile_id(model_gateway, spend_limiter)
                try:
                    call_kwargs: dict[str, Any] = {}
                    # ... unchanged from 2088 onward, now using the routed profile_id ...
```
The existing budget-manager checks (`2058-2080`) stay where they are (they run
before the research branch and must keep doing so). Only the `profile_id`
binding moves.

Known limitation to record in the commit message: `_projected_cost_usd`
(`daemon.py:2041-2054`) is still computed from the `"default"` profile, so the
BudgetManager/SpendLimiter pre-checks project against `default` even when a
cheaper profile is chosen — the pre-check is therefore conservative (never
under-charges). Threading the routed profile into the projection is a follow-up,
not required here.

### 3.2 S10 tests (test-first)

File: `tests/unit/test_daemon_wiring.py` (add to the existing module):
```python
def _profile(pid, cin, cout, run_budget=0.0, metered=False, enabled=True):
    from general_ludd.models.gateway import ModelProfile
    return ModelProfile(
        model_profile_id=pid, provider="openai", provider_package="lp",
        provider_class_hint="COAI", model_name=pid, enabled=enabled,
        cost_per_input_token=cin, cost_per_output_token=cout,
        run_budget_usd=run_budget, api_metered=metered,
    )

def test_select_dispatch_profile_id_picks_cheapest():
    from general_ludd.daemon_wiring import select_dispatch_profile_id
    from general_ludd.models.gateway import ModelGateway
    gw = ModelGateway(profiles=[
        _profile("default", 1e-5, 3e-5),   # expensive
        _profile("cheap", 1e-7, 3e-7),     # cheapest
    ])
    assert select_dispatch_profile_id(gw, spend_limiter=None) == "cheap"
    # FAILS TODAY: nothing calls select_cost_effective_profile; dispatch is "default".

def test_select_dispatch_profile_id_falls_back_when_no_eligible():
    from general_ludd.daemon_wiring import select_dispatch_profile_id
    from general_ludd.models.gateway import ModelGateway
    # metered profile whose run_budget exceeds the (tiny) remaining budget
    gw = ModelGateway(profiles=[_profile("pricey", 1e-3, 1e-3, run_budget=100.0, metered=True)])
    class _SL:
        def remaining(self): return 0.0
    assert select_dispatch_profile_id(gw, _SL()) == "default"
```
Also add an integration assertion that the executor actually uses the routed
profile — File: `tests/unit/test_h5_gateway_executor.py`, new test that builds an
executor calling `select_dispatch_profile_id` and asserts the routed id is passed
to a mocked `call_model_with_retry` (mirror the existing
`test_dispatcher_with_gateway_executor` structure, but assert the first
positional arg of `call_model_with_retry` equals the cheap profile id, not
`"default"`).

---

## 4. S2 — review returns stranded forever (3 paths; the obvious fix is a trap)

### Root cause
When the review is dispatched over the worker HTTP path or the Ansible runner
path (rather than the in-process reviewer), the claimed row is left at
`claimed_for_review` forever:
- The worker's `/jobs/return-review` returns a fake `{"status":"ack"}`
  (`worker/app.py:537-539`) and runs nothing.
- The loop's HTTP branch never inspects `resp.status_code` — it hands any
  response to `_persist_review_response` (`loop.py:1200`), which only creates a
  `TaskDecisionModel` if `decision` is present and **never sets `tr.status`**.
  The ack has no `decision`, so nothing happens and the claim strands.
- The loop's runner branch discards the playbook result entirely
  (`loop.py:1148-1156`); only `TimeoutError` releases the claim.
- `claim_unreviewed` only re-selects `status == "created"` (`repository.py:736`),
  so a stranded `claimed_for_review` row is never re-claimed.
- Downstream, `_collect_training_data_from_returns` queries `status == "reviewed"`
  (`loop.py:4248`), a value never written anywhere in `src/`, so self-improve
  training data collection always gets zero rows.

**The trap (call it out in the PR):** converting the worker to `501` **alone**
does not fix anything. The loop's HTTP branch ignores `resp.status_code`; a `501`
body has no `decision`; `_persist_review_response` no-ops; the claim strands
exactly as before — while `tests/unit/test_worker.py:197-205` (which asserts only
the status code) goes green. And "just wire the worker to run
`return_review.yml`" is worse than the strand: the playbook hardcodes
`decision: "complete"` (`return_review.yml:35`) with no model call, so it would
**rubber-stamp every return complete**. `501` + a loop that releases the claim is
the correct combination.

### 4.1 Fix (a) — worker returns an honest 501

File: `src/general_ludd/worker/app.py:537-539`

**Before**:
```python
    @application.post("/jobs/return-review")
    async def return_review_job(job: JobSpec) -> dict[str, Any]:
        return {"status": "ack", "job_id": job.job_id, "detail": "Return review queued for daemon reviewer"}
```
**After** (match the sibling `501` shape at `541-586`):
```python
    @application.post("/jobs/return-review")
    async def return_review_job(job: JobSpec) -> dict[str, Any]:
        # S2: the worker has no backing reviewer. Returning a fake "ack" made the
        # daemon believe review ran and left the task-return stranded at
        # 'claimed_for_review' forever. Return 501 so the daemon's dispatch branch
        # releases the claim (see event_loop/loop.py _dispatch_review_job HTTP
        # branch). Real review runs in-process in the daemon (_review_in_process).
        raise HTTPException(
            status_code=501,
            detail={
                "reason": "not_implemented",
                "description": (
                    "/jobs/return-review has no backing reviewer in the worker. "
                    "Review runs in-process in the daemon; this path only exists "
                    "for a future out-of-process reviewer."
                ),
                "job_id": job.job_id,
            },
        )
```

### 4.2 Fix (b) — factor the release logic, and make the HTTP + runner branches release

File: `src/general_ludd/event_loop/loop.py`

**Add a helper** (dedups the 3 copies at `1110-1124`, `1173-1187`, `1209-1223`).
Place it as a method on the same class, e.g. right above `_dispatch_review_job`
(`1073`):
```python
    async def _release_review_claim(self, tr: Any, reason: str) -> None:
        """Release a claimed-for-review task-return back to 'created' and block
        its todo. Used on every non-success review path (timeout, HTTP >=400,
        runner returns no real decision) so a claim is never stranded at
        'claimed_for_review' — nothing re-claims that status.
        """
        logger.warning("Releasing review claim for return %s: %s", getattr(tr, "return_id", "?"), reason)
        if self._active_session is not None:
            with contextlib.suppress(Exception):
                tr.status = "created"
            if hasattr(tr, "updated_at"):
                with contextlib.suppress(Exception):
                    tr.updated_at = datetime.now(UTC)
            with contextlib.suppress(Exception):
                await self._active_session.flush()
        if self._todo_repo is not None:
            with contextlib.suppress(Exception):
                todo = await self._todo_repo.get_by_id(tr.todo_id)
                if todo is not None:
                    await self._todo_repo.transition(
                        tr.todo_id, TodoStatus.BLOCKED, todo.version
                    )
```
Then replace each of the three inline `TimeoutError` release blocks with a call
to `await self._release_review_claim(tr, "<reason>")` (in-process timeout,
runner timeout, HTTP timeout), preserving the existing distinct log reasons.

**HTTP branch** — File `loop.py:1189-1223`. **Before** (`1192-1200`):
```python
        try:
            resp = await asyncio.wait_for(
                self._http_client.post(
                    f"{self.worker_base_url}/jobs/return-review",
                    json=job.model_dump(mode="json"),
                ),
                timeout=review_http_timeout,
            )
            await self._persist_review_response(tr, resp)
        except TimeoutError:
            # ... inline release ...
```
**After**:
```python
        try:
            resp = await asyncio.wait_for(
                self._http_client.post(
                    f"{self.worker_base_url}/jobs/return-review",
                    json=job.model_dump(mode="json"),
                ),
                timeout=review_http_timeout,
            )
            status_code = int(getattr(resp, "status_code", 0) or 0)
            if status_code >= 400:
                # S2 trap: the worker returns 501 (no backing reviewer). Without
                # this check the claim strands at 'claimed_for_review' forever.
                await self._release_review_claim(
                    tr, f"worker /jobs/return-review returned HTTP {status_code}"
                )
            else:
                await self._persist_review_response(tr, resp)
        except TimeoutError:
            await self._release_review_claim(tr, "HTTP review dispatch timed out")
```

**Runner branch** — File `loop.py:1140-1188`. The playbook is a rubber-stamp
(`decision: "complete"`, no model call) and no code honors its result, so treat
the runner path as unimplemented: capture the run, then release the claim.
**Before** (`1148-1156`):
```python
            try:
                await asyncio.wait_for(
                    self._bounded_to_thread(
                        self._runner.run_playbook,
                        playbook_name="return_review.yml",
                        private_data_dir=dirs["root"],
                    ),
                    timeout=review_playbook_timeout,
                )
            except TimeoutError:
                # ... inline release ...
```
**After**:
```python
            try:
                await asyncio.wait_for(
                    self._bounded_to_thread(
                        self._runner.run_playbook,
                        playbook_name="return_review.yml",
                        private_data_dir=dirs["root"],
                    ),
                    timeout=review_playbook_timeout,
                )
                # S2: return_review.yml hardcodes decision:"complete" with no model
                # call — honoring it would rubber-stamp every return. Nothing here
                # consumes the runner result, so treat the runner path as
                # unimplemented and release the claim (todo -> BLOCKED) rather than
                # strand it or fake-complete it.
                await self._release_review_claim(
                    tr, "runner review path unimplemented (playbook is a rubber-stamp stub)"
                )
            except TimeoutError:
                await self._release_review_claim(tr, "runner review playbook timed out")
```

> Note: in production with a `ModelGateway`, the **in-process** branch
> (`1091-1125`) is taken and works correctly — the runner/HTTP branches are
> fallbacks reached only when no reviewer is wired. This fix makes those
> fallbacks fail-closed (release + block) instead of stranding/rubber-stamping.
> Do NOT touch `_review_in_process` (`1255-1350`).

### 4.3 (Optional) Fix (c) — make `REVIEWED` reachable so self-improve training data flows

This is the "second casualty" (`_collect_training_data_from_returns` always gets
zero rows). It is **not required** to close the strand bug, but it is cheap and
turns a dead self-improve query live. Mark it clearly as an optional sub-fix in
its own commit.

In `_review_in_process` (`loop.py:1255-1350`), after `apply_decision` succeeds
and the audit event is written (i.e. after `1348`), set the return's status:
```python
        with contextlib.suppress(Exception):
            tr.status = TaskReturnStatus.REVIEWED.value  # "reviewed"
            if hasattr(tr, "updated_at"):
                tr.updated_at = datetime.now(UTC)
            if self._active_session is not None:
                await self._active_session.flush()
```
Add a test asserting `tr.status == "reviewed"` after an in-process review with a
stub reviewer, and that `_collect_training_data_from_returns` then returns >0.
Ship this ONLY if the in-process review being terminal (rows never re-claimed,
which is correct — they are reviewed) is acceptable; do not ship it in the same
commit as 4.1/4.2.

### 4.4 S2 tests (test-first)

- **Invert** `tests/unit/test_worker.py:197-205`
  `test_worker_return_review_endpoint` → assert `resp.status_code == 501`
  (fails today; currently `200`).
- **Invert** `tests/e2e/test_obj03_worker.py:67-78` `test_return_review_endpoint`
  → assert `resp.status_code == 501` (fails today; currently in `(200, 202)`).
- **Invert** `tests/unit/test_event_loop.py:143-168`
  `test_dispatch_review_job_runner_path_completes_within_timeout` → the runner
  happy path now releases, so assert `tr.status == "created"` (released) and the
  todo transitioned to `BLOCKED` (fails today; currently asserts
  `CLAIMED_FOR_REVIEW` unchanged). Update the comment away from "Happy path must
  not touch/release the claim."
- **Add** `test_dispatch_review_job_http_501_releases_claim` in
  `tests/unit/test_event_loop.py`: stub `self._http_client.post` to return an
  object with `status_code=501` (no `decision`), start `tr` at
  `claimed_for_review`, run `_dispatch_review_job(tr)`, assert
  `tr.status == "created"` and the linked todo is `BLOCKED`. This fails today
  because the HTTP branch never inspects `status_code` — the claim stays
  `claimed_for_review`.
- Keep the existing `test_dispatch_review_job_playbook_timeout_releases_claim`
  (`test_event_loop.py:101-140`) green (behavior unchanged — it already asserts
  the timeout release).
- `tests/unit/test_event_loop.py:170-181` `test_event_loop_skips_reviewed_return`
  needs no change (it exercises the vestigial `dispatch_return_review`).

---

## 5. Files touched (summary)

| Item | Files |
|---|---|
| S1 | `agents/dispatcher.py`, `daemon.py` (`else` + flag, `readyz`, opt `healthz`), `routers/reload.py`, `tests/unit/test_h5_gateway_executor.py`, new `tests/unit/test_readyz_model_unconfigured.py`, new `tests/unit/test_config_reload_restart_required.py` |
| S10 | `daemon_wiring.py` (new helper), `daemon.py` (`_gateway_executor` profile binding), `tests/unit/test_daemon_wiring.py`, `tests/unit/test_h5_gateway_executor.py` |
| S2 | `worker/app.py`, `event_loop/loop.py` (helper + HTTP + runner branches), `tests/unit/test_worker.py`, `tests/e2e/test_obj03_worker.py`, `tests/unit/test_event_loop.py`; opt `_review_in_process` + a REVIEWED test |

`daemon.py` is touched by **both** S1 (§2.2/2.3) and S10 (§3.1). `dispatcher.py`
is S1-only. `daemon_wiring.py` is S10-only. `loop.py`/`worker/app.py` are S2-only.

---

## 6. Landing order

The only real coupling is `daemon.py` between S1 and S10, and the shared test
file `test_h5_gateway_executor.py`. Land in this order, each as its own commit
with a failing-test-first sub-step and a `| evidence:` line in `TASKS.md`:

1. **S1-a (dispatcher fail-closed).** Edit `agents/dispatcher.py` (§2.1). First
   run the verify-step S1.9 grep and fix every test that built a dispatcher
   without `executor=` and asserted the old `completed`/`""` no-op. Rewrite
   `test_h5_gateway_executor.py` (§2.5). Land.
   - `make test-iso TESTFILE='tests/unit/test_h5_gateway_executor.py'`
2. **S1-b (daemon flag + health + reload).** Edit `daemon.py` §2.2/§2.3 and
   `routers/reload.py` §2.4; add the two new health/reload tests. Land.
   - `make test-iso TESTFILE='tests/unit/test_readyz_model_unconfigured.py'`
   - `make test-iso TESTFILE='tests/unit/test_config_reload_restart_required.py'`
3. **S10 (routing).** Add `select_dispatch_profile_id` to `daemon_wiring.py` and
   rebind `profile_id` in `_gateway_executor` (§3.1); add the wiring tests
   (§3.2). This edits `daemon.py` again — do it **after** S1-b so the two
   `daemon.py` diffs do not overlap (S1-b edits the `else`/health region; S10
   edits the `_gateway_executor` body — different regions, but sequencing avoids
   a merge headache and keeps each commit's test story clean).
   - `make test-iso TESTFILE='tests/unit/test_daemon_wiring.py'`
4. **S2-a (worker 501 + loop release).** Edit `worker/app.py` §4.1 and
   `event_loop/loop.py` §4.2 together (they are the trap pair — do NOT land the
   worker change without the loop change). Invert/ add the S2 tests §4.4. Land.
   - `make test-iso TESTFILE='tests/unit/test_worker.py'`
   - `make test-iso TESTFILE='tests/unit/test_event_loop.py'`
   - `make test-iso TESTFILE='tests/e2e/test_obj03_worker.py'`
5. **S2-c (optional REVIEWED).** Only if adopting §4.3 — separate commit.
6. Full gate: `make gate-async` then `make gate-status`.

---

## 7. Risk / rollback

- **S1-a blast radius (LOW, but check).** Making the default executor raise flips
  behavior only for dispatchers constructed with no `executor=`. Production
  always passes one when a gateway exists, and passes `None` only when the
  gateway is genuinely absent (exactly the case we want to fail closed). The
  risk is **tests** that relied on the silent no-op default — enumerate and fix
  them up front (verify-step S1.9). Rollback: revert `dispatcher.py:65` to
  `executor or _noop_executor` (keep the new class unused) — one line.
- **S1 fail-closed vs. fail-open trade.** A daemon with no model profiles now
  reports `/readyz: 503`. If any orchestration/deploy tooling treats `503` as
  "kill the pod", operators running intentionally model-less (e.g. a pure-review
  or pure-API deployment) would see restarts. This is the intended signal
  (a model-less daemon cannot dispatch), but call it out in the changelog. The
  `restart_required` reload field is purely additive (new JSON key) — no client
  breaks.
- **S10 (LOW).** `select_dispatch_profile_id` falls back to `"default"` on empty
  profiles, all-over-budget, or any exception, so it can never route to a
  non-existent profile. Worst case it behaves exactly like today (`"default"`).
  The projection-vs-routed-profile mismatch (§3.1) only makes the budget
  pre-check more conservative, never less. Rollback: restore
  `profile_id = "default"` at the top of `_gateway_executor` — one line; leave
  the helper unused.
- **S2 (MEDIUM — semantics change).** The runner and HTTP fallback review paths
  now **release the claim and block the todo** instead of stranding/faking
  completion. For an operator who wired a *real* out-of-process reviewer (there
  is none shipped), the HTTP branch would now release on any `>=400` — which is
  correct — but the runner branch releases even on a successful playbook run
  (because no code honors a runner-returned decision, and the shipped playbook is
  a rubber-stamp). If a real runner reviewer is ever built, its result must be
  parsed and `_persist_review_response`-equivalent logic added **before**
  reintroducing a non-release success path. Document this in the runner-branch
  comment (done in §4.2). Rollback: revert `worker/app.py` and the `loop.py`
  branches together; keep `_release_review_claim` (harmless dedup helper) or
  revert it too.
- **General.** All three are behind the same dispatch/review machinery that has
  **no live consumer reading `.status`** today, so the observable surface that
  changes is: dispatched-subagent `.output` (now carries an error instead of
  `""` when unconfigured), `/readyz` status code, the reload response body, the
  worker `/jobs/return-review` status code, and `task_return.status` transitions
  on the fallback review paths. Verify each with the named tests before the gate.
```text
```
