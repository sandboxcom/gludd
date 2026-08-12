# SPEC — Budget Enforcement & Model-Routing Telemetry

**Status:** DRAFT (turnkey). Verify-first; every claim below was re-checked against
the tree on 2026-07-14. Verdicts: CONFIRMED / REFUTED / CORRECTED (claim was
directionally right but the cited mechanism/line was wrong) / UNVERIFIED.

**Through-line:** gludd's spend enforcement and its quality-based model routing
are both fed by data that is structurally **zero or fake**. Model cost rates are
`$0`, so every budget reservation reconciles to zero; model-performance telemetry
raises-and-swallows on every live call; and the `code_quality` signal that drives
live model selection is a hardcoded `0.5` constant. Fixing anything downstream is
cosmetic until the cost rates are real — **do the KEYSTONE first.**

> Note on paths: the mission brief cited `scoring/gateway.py`; the real module is
> **`src/general_ludd/models/gateway.py`**. All line numbers below are from the
> current tree.

---

## Verify-first summary table

| ID | Claim | Verdict | Anchor |
|----|-------|---------|--------|
| KEYSTONE | Cost rates default `$0`, no prod path seeds them | **CONFIRMED (corrected nuance)** | `models/gateway.py:186-187`, `models/auto_configurator.py:87-88` |
| REFUTED-RESERVE | Daemon reserve path already correct; don't rebuild | **CONFIRMED (it is correct)** | `daemon.py:2041-2114` |
| PRIMARY-BYPASS | "Primary code-gen path bypasses BudgetManager" via engine/langgraph | **CORRECTED** — those paths are **dormant**, not primary | `daemon.py:1388/2947`, grep |
| F1 | `budget_pre_check` called with 0.0 projection at 3 sites | **CONFIRMED** | `models/job_invocation.py:121`, `execution/tool_loop.py:213`, `review/reviewer.py:221` |
| F1-GW | `gateway.check_budget` real but fed by $0 rates | **CONFIRMED (inert)** | `models/gateway.py:590-638`, callers `:730`, `:1594` |
| S28 | `record_call_sync` raises-and-swallows every live call | **CONFIRMED** | `db/repository.py:2389/2455/2684-2697`, `daemon.py:1972-1974` |
| S19 | `code_quality_score=0.5` constant drives live routing (2 paths) | **CONFIRMED** | `observability/recorder.py:25`, `event_loop/benchmark.py:31` |
| C-ENGINE | `shutdown()` + sync `execute()` have zero prod callers | **CONFIRMED (stronger: whole execute surface dormant)** | `execution/engine.py:476/491/647` |
| C-ENGINE-RACE | `defer_commit` race | **REFUTED — do not touch** | `execution/engine.py:595` |
| C-LANGGRAPH | `make_langgraph_tool_loop` landmine (no guard, chat_model=None) | **CONFIRMED (zero prod callers)** | `agents/capabilities.py:230-236`, `execution/langgraph_agent.py:234-243` |
| C-EVENTLOOP | one shared to-thread semaphore + bare unbounded sites | **CONFIRMED (exact raw line cites UNVERIFIED)** | `event_loop/loop.py:437-438/834-836` |

---

## KEYSTONE — Model cost rates are `$0`. **FIX THIS FIRST.**

### Verified findings
- `ModelProfile.cost_per_input_token` / `cost_per_output_token` default to `0.0`
  — `src/general_ludd/models/gateway.py:186-187`.
- The billing chokepoint `_invoke_and_bill` computes
  `cost = input_tokens*profile.cost_per_input_token + output_tokens*profile.cost_per_output_token`
  — `models/gateway.py:1034-1037`. With `$0` rates this is **always `0.0`**, and
  `_budget_guard.record_spend(cost)` (`:1048`) records nothing.
- **What actually loads profiles in prod:** the daemon
  (`daemon.py:1227` `startup_config.get("model_profiles", [])` +
  `daemon.py:1238` `AutoConfigurator().auto_configure_profiles()`) and the worker
  (`worker/app.py:81`). `auto_configure_profiles` → `auto_configure_from_env`
  **hardcodes `"cost_per_input_token": 0.0` / `0.0`** at
  `models/auto_configurator.py:87-88`. So every env-synthesized profile is `$0`.
- **CORRECTED nuance (docs drifted):** it is NOT true that *no* production code
  ever computes a non-zero rate. `AutoConfigurator.generate_profiles`
  (`models/auto_configurator.py:147-176`) DOES seed
  `cost_per_input_token = input_cost` from scraped provider pricing
  (`pricing.prompt`). **BUT** its only caller is the admin discovery endpoint
  `routers/models.py:190`, which stashes the result in
  `app.state._discovered_profiles` (`routers/models.py:196`) and **never persists
  it or feeds it into the live `ModelGateway`.** An operator *could* also hand-write
  `cost_per_input_token` into a config `model_profiles` entry, but nothing seeds it
  automatically. Net: in any default/typical deployment the active gateway profiles
  carry `$0` rates and `cost_estimate == $0` on every call. Claim CONFIRMED with
  this precision.
- Known to the suite as **CA-T12** — `tests/integration/test_budget_integrity.py`
  (see `:26` docstring and `:126` "the SHIPPED default").
- **A real pricing catalog already exists and is already trusted elsewhere.** The
  daemon's budget *projection* uses `infra/pricing.py:85 token_cost_usd` and
  `controllers/spend_limiter.py:138-176 SpendLimiter.token_cost_usd` (PricingCatalog
  primary via `pricing_intel/catalog.py model_price`, static table fallback). The
  fix is to point profile-rate seeding at that same catalog.

### Design — seed per-token rates from the pricing catalog at profile-load time
Add a seeding step wherever `ModelProfile`s are materialized for the live gateway
(both `daemon.py` ~1227-1262 and `worker/app.py:81`), and make `generate_profiles`
already-correct rates survive. Concretely, introduce a single helper:

```python
# models/pricing_seed.py  (new)
from general_ludd.pricing_intel.catalog import get_catalog

def seed_profile_rates(profile: ModelProfile) -> ModelProfile:
    """If a profile has $0 rates, fill them from the pricing catalog.

    Never overwrites an operator-set non-zero rate. Falls back to the static
    infra.pricing table when the catalog has no entry for the model.
    """
    if profile.cost_per_input_token > 0.0 or profile.cost_per_output_token > 0.0:
        return profile
    price = get_catalog().model_price(profile.provider, profile.model_name)
    if price is None:
        return profile  # leave $0; projection layer still uses static fallback
    return profile.model_copy(update={
        "cost_per_input_token": price.input_per_token,
        "cost_per_output_token": price.output_per_token,
    })
```

**Before** (`daemon.py` ~1250, after auto-config append):
```python
model_profiles = list(model_profiles) + _added
```
**After:**
```python
model_profiles = [seed_profile_rates(_coerce(p)) for p in (list(model_profiles) + _added)]
```
(and the equivalent at `worker/app.py:81`).

### Failing tests to write (RED today)
- `tests/integration/test_budget_integrity.py::test_ca_t12_reservation_reconciles_nonzero`
  — build a gateway from an env-synthesized default profile, invoke, assert
  `budget_guard` recorded `> 0`. **Fails today** (records `0.0`).
- `tests/unit/test_pricing_seed.py::test_seed_fills_zero_rate_from_catalog` — new.
- `tests/unit/test_pricing_seed.py::test_seed_never_overwrites_operator_rate` — new.

### Effort / risk
- Effort: **M** (helper + 2 wiring sites + catalog signature check).
- Risk: low. Rollback: delete the two wiring lines; profiles revert to `$0`.
  Guardrail: seeding never lowers an operator-set rate, so it cannot *loosen* a cap.

---

## Billing-clock determinism — one rate snapshot per call

Peak/off-peak billing is intentionally time-dependent, but a completed call must
use one UTC timestamp for its multiplier, rate label, and savings ledger. The
gateway therefore accepts an optional `billing_clock` and samples it once in the
shared billing helper used by buffered and streaming responses. Production keeps
the live UTC clock; tests and replay tools pin a timestamp instead of depending
on the hour at which CI happens to run.

This guards two practical failure modes: a test that changes expected cost at
09:00/17:00 UTC, and a call finishing on that boundary being charged with one
rate while being labelled or recorded with another. Exact integration assertions
pin an off-peak Sunday while preserving the production calculation and the
`0.75` expectation.

A long-lived practitioner report, [LiteLLM issue #4965](https://github.com/BerriAI/litellm/issues/4965),
documents inconsistent LLM cost results between streaming and non-streaming
paths even when usage appears equivalent, plus custom rates not being honored
uniformly. The operational lesson applied here is to keep both gateway paths on
one billing primitive and make every policy input—including time—explicit and
replayable. The rate-boundary unit tests and the two integration suites are the
regression proof.

---

## REFUTED — Do NOT rebuild the daemon reserve path

`daemon.py:2056-2114` `_gateway_executor` is **already correct** and tested
(`tests/unit/test_budget_guards.py:237`, `test_c4_budget_fixes`):
- Real non-zero projection `_projected_cost_usd` computed at `daemon.py:2041-2054`
  via `spend_limiter.token_cost_usd(...)` / `infra.pricing.token_cost_usd(...)`.
- `check_daily_budget_reserved(task_id, _projected_cost_usd)` (`:2060`) +
  `check_todo_budget(...)` (`:2069`) + `record_spend(...)` on success (`:2097`) +
  `release_reservation(...)` on failure (`:2107`).

**Do not touch this logic.** Two facts to record in the spec so nobody "fixes" it:
1. It is wired **only at the AgentDispatcher layer** (`dispatcher_executor`,
   `daemon.py:2110-2118`).
2. Its `record_spend` uses `result.cost_estimate` (`daemon.py:2099`), which is
   profile-rate-based → `$0` until the KEYSTONE lands. So even this correct path
   reserves real budget on the projection but **reconciles to zero** on record —
   further proof the KEYSTONE is the root fix, not the reserve logic.

### CORRECTION — the "primary code-gen path" is dormant, not primary
The brief said the primary code-gen path bypasses `BudgetManager` via
`execution/engine.py` and `execution/langgraph_agent.py`. Re-verification:
- `ExecutionEngine` is constructed at `daemon.py:1388` and stored as
  `app.state._execution_engine`, but that attribute is **only ever read** at
  `daemon.py:2947` (the `/admin/execution-engine/status` endpoint). Neither
  `execute_async` (`engine.py:491`) nor `execute` (`engine.py:647`) has **any**
  non-test caller (grep: all hits are `tests/`).
- `make_langgraph_tool_loop` (`agents/capabilities.py:199`) likewise has **zero
  prod callers**.

So the accurate framing for the spec: **`BudgetManager` (daily/per-todo
reservation) is reachable only through the daemon `_gateway_executor`.** The live
generation/review paths (`models/job_invocation.py`, `event_loop/loop.py`,
`review/reviewer.py`) call the gateway with a `budget_guard` (SpendLimiter /
RunBudgetGuard) pre-check but **never** enter `BudgetManager`. Whether to route the
live generation path through `BudgetManager` is a genuine open design question — but
it is NOT "restore a bypassed primary path"; the engine path was never live.

---

## F1 — projected-cost is `0.0` at 3 live pre-check sites

### Verified findings
`budget_pre_check(guard, projected_cost=0.0)` (`budget_guard_check.py:38-39`) is
reactive-only when `projected_cost` is `0.0`: it can block a call *after* prior
cumulative spend crosses the cap, but never the call whose own projection would
breach it. The helper's docstring states this explicitly
(`budget_guard_check.py:44-51`: "reviewer / job_invocation / tool_loop retain the
0.0 default until their own fixes land"). Confirmed call sites, none passing a
projection:
- `models/job_invocation.py:121` — `denial = budget_pre_check(budget_guard)` (generation path).
- `execution/tool_loop.py:213` — `denial = budget_pre_check(self._budget_guard)`.
- `review/reviewer.py:221` — `denial = budget_pre_check(self._budget_guard)`.
- (also `execution/langgraph_agent.py:113`, but that whole module is dormant.)

`ExecutionEngine._budget_pre_check` (`engine.py:378`, projection via
`engine.py:354-365`) *does* thread a positive projection — but the engine is
dormant (above), so it is not a live counter-example.

### F1-GW — `gateway.check_budget` is real but inert
`models/gateway.py:590-638` is a genuine, D-21-hardened check (re-estimates
server-side and takes `max(caller, server)`). Its only callers are internal:
`gateway.py:730` (inside `call_model`) and `gateway.py:1594` (fallback loop). But
the server re-estimate (`estimate_cost`, `:627-628`) multiplies by
`profile.cost_per_input_token` — `$0` — so `effective_cost` is `0.0` and the gate
passes any finite/inf `budget_remaining`. **It becomes live the moment the KEYSTONE
lands** — no separate fix needed beyond real rates.

### Design
Thread a real projection into each of the three live sites, reusing the same
catalog helper the daemon already uses:

**Before** (`models/job_invocation.py:121`):
```python
denial = budget_pre_check(budget_guard)
```
**After:**
```python
from general_ludd.infra.pricing import token_cost_usd
projected = token_cost_usd(profile_id, in_tokens_est, out_tokens_est)
denial = budget_pre_check(budget_guard, projected_cost=projected)
```
(`in_tokens_est` from the bounded prompt, `out_tokens_est` from the profile's
`max_output_tokens`; identical shape to `daemon.py:2044-2046`.) Apply the same at
`tool_loop.py:213` and `reviewer.py:221`.

### Failing tests to write (RED today)
- `tests/unit/test_job_invocation.py::test_projection_blocks_self_breaching_call`
  — guard with headroom < projected cost; assert denial BEFORE any model call.
- `tests/unit/test_tool_loop_guards.py::test_toolloop_projection_denies_over_cap`.
- `tests/unit/test_reviewer_budget.py::test_reviewer_projection_denies_over_cap`.

### Effort / risk
- Effort: **S** per site (**S–M** total). Depends on KEYSTONE for the rates to
  be meaningful, but the plumbing is independent.
- Risk: low; a bad estimate can only make the guard *more* conservative (it maxes
  with server-side). Rollback: drop `projected_cost=` args.

---

## S28 — model-call telemetry is a silent no-op

### Verified findings (all CONFIRMED)
- Live caller: `worker/app.py:400` `_model_perf_repo.record_call_sync(...)`, gated
  by `_model_perf_repo is not None and is_generation_work_type(...)` (`:382-383`).
- `record_call_sync` does `asyncio.run(self.record_call(..., session=None))`
  (`db/repository.py:2455`).
- `record_call` resolves `eff_session = session or self._resolve_session()`
  (`db/repository.py:2389`).
- `_resolve_session` (`db/repository.py:2684-2697`) **raises** `RuntimeError` when
  `self._session_factory is not None and self._session is None` — it never lazily
  opens a session despite historical docstrings elsewhere implying it does. (Read
  directly; verbatim at `:2686-2691`.)
- The daemon constructs the repo exactly into that raising branch:
  `daemon.py:1972-1974` `ModelPerformanceRepository(session_factory=session_factory)`
  (no `session=`).
- The `RuntimeError` propagates back through `asyncio.run` into
  `record_call_sync`'s `except Exception` (`db/repository.py:2456-2463`), which only
  logs at `warning`. **Net: every production `record_call_sync()` raises and is
  swallowed; no `ModelCallLogModel` row is ever written.**

### Design — make `_resolve_session` actually lazy (fail-open to a real session)
The constructor is *given* a `session_factory` precisely so it can open a session
in a `to_thread`/`asyncio.run` worker. Honor that:

**Before** (`db/repository.py:2684-2697`):
```python
def _resolve_session(self) -> AsyncSession:
    if self._session_factory is not None and self._session is None:
        raise RuntimeError("... requires a concrete AsyncSession ...")
    if self._session is None:
        raise RuntimeError("... no session configured ...")
    return self._session
```
**After** — provide an async lazy path and call it from `record_call`:
```python
@contextlib.asynccontextmanager
async def _session_scope(self, session: AsyncSession | None):
    if session is not None:
        yield session; return
    if self._session is not None:
        yield self._session; return
    if self._session_factory is not None:
        async with self._session_factory() as s:   # open + commit + close here
            yield s; return
    raise RuntimeError("ModelPerformanceRepository: no session available")
```
and in `record_call` replace `eff_session = session or self._resolve_session()`
with `async with self._session_scope(session) as eff_session:` wrapping the body
(committing before exit). This keeps the `session=` override semantics and makes the
`session_factory`-only daemon construction functional.

### Failing tests to write (RED today)
- `tests/unit/test_model_perf_repo.py::test_record_call_sync_persists_with_factory_only`
  — construct with a real `session_factory`, `session=None`; call
  `record_call_sync(...)`; assert a `ModelCallLogModel` row exists. **Fails today**
  (raises, swallowed, zero rows).
- `tests/unit/test_model_perf_repo.py::test_record_call_sync_does_not_raise` — assert
  no warning-log escape hatch is hit (spy the logger).

### Effort / risk
- Effort: **M** (async context manager + call-site refactor + commit semantics).
- Risk: medium — opening a session per call in a `to_thread` worker; ensure the
  factory is the async-session factory and that the scope commits. Rollback: revert
  `_resolve_session`; telemetry returns to no-op (no data-loss regression).

---

## S19 — `code_quality_score = 0.5` constant reaches LIVE model routing (two paths)

### Verified findings (all CONFIRMED)
- **Path (a)** `observability/recorder.py:22-25`
  `code_quality_score = 0.5`, overridden only when `test_results` has `total > 0`
  (`:71-76`). Sole prod caller `event_loop/loop.py:2804`
  `self._benchmark_recorder.record_from_trace(_trace, success=True)` passes **no**
  `test_results` → always `0.5`.
- **Path (b)** `event_loop/benchmark.py:31` `record_job_benchmark` hardcodes
  `"code_quality_score": 0.5` and has **no** `test_results` parameter at all.
  Callers: `event_loop/loop.py:2745-2755`, `execution/engine.py:604-614` and
  `:786-796`.
- Both feed `composite_score` at **30% weight**:
  `db/repository.py:1000-1005`
  (`completion*0.4 + code_quality*0.3 + instruction*0.2 + token_eff*0.1`).
- `composite_score` drives live selection: `scoring/router.py:752`
  `max(candidates, key=lambda c: c.composite_score)` and leaderboard
  `scoring/router.py:778`.
- **Cheap fix already half-built:** `execution/engine.py:597-599` already computes
  real `test_exit_code, test_summary` from `_run_tests(...)`; the sync path does the
  same at `engine.py:752`. The data is simply discarded rather than threaded into
  `record_job_benchmark`.

### Design — thread real pass/fail into the benchmark score
1. Add a `test_results: dict | None = None` parameter to
   `event_loop/benchmark.py record_job_benchmark`; when provided with `total > 0`,
   set `code_quality_score = passed / total` (mirror `recorder.py:71-76`).
2. Update the live callers to pass it:
   - `engine.py:606`/`:788` — pass
     `{"total": test_summary.total, "passed": test_summary.passed}` from the
     already-computed `_run_tests` result. (Engine is dormant, but fix for
     correctness/future-wire.)
   - `event_loop/loop.py:2745-2755` and `:2804` — thread the loop's own test
     outcome for the generated todo (the loop runs the gate for completed work; wire
     that summary in). Where the loop genuinely has no test outcome, keep `0.5` but
     record `code_quality_source="unmeasured"` so routing can down-weight it.

**Before** (`event_loop/benchmark.py:12-40`, dict at `:31`):
```python
"code_quality_score": 0.5,
```
**After:**
```python
"code_quality_score": (test_results["passed"] / test_results["total"]
                       if test_results and test_results.get("total", 0) > 0
                       else 0.5),
```

### Failing tests to write (RED today)
- `tests/unit/test_benchmark_recorder.py::test_record_job_benchmark_uses_test_results`
  — pass `{"total":10,"passed":3}`; assert stored `code_quality_score == 0.3`.
  **Fails today** (no param; always `0.5`).
- `tests/unit/test_event_loop.py::test_loop_threads_test_outcome_into_benchmark`.
- `tests/integration/test_budget_integrity.py`-style routing test: two profiles,
  one with real failing tests, assert the failing one loses `composite_score`
  ranking (fails today — both get `0.5`).

### Effort / risk
- Effort: **M** (param + ~4 call sites + loop test-outcome plumbing).
- Risk: medium — changes live routing rankings; land behind observation (log old vs
  new `composite_score`) before trusting argmax. Rollback: revert the param default.

---

## C-ENGINE — dormant execute surface + orphaned shutdown

### Verified findings
- `ExecutionEngine.shutdown` (`engine.py:476-489`) cancels+awaits background tasks
  but has **zero prod callers** — the daemon shutdown block (`daemon.py:2246-2351`)
  never calls it. In-flight `defer_commit` tasks would be abandoned on SIGTERM.
- `execute` (sync, `engine.py:647-811`) **and** `execute_async`
  (`engine.py:491`) both have **zero non-test callers** — the whole engine execute
  surface is dormant (only the status endpoint `daemon.py:2947` reads the instance).
- **REFUTED — do NOT "fix" the `defer_commit` race.** It is a deliberate
  fire-and-forget with a lock + done-callback; files are written BEFORE
  `defer_commit` at `engine.py:595`, so `_run_tests` reads disk, not git. Leave it.

### Design
Because the engine is dormant, this is a **latent-code hygiene** item, not a live
bug. Two options — pick ONE and state it:
- **(Recommended) Wire the engine's async lifecycle into daemon shutdown** if/when
  the engine becomes live: add `await engine.shutdown()` to the `daemon.py`
  shutdown block guarded by `getattr(app.state, "_execution_engine", None)`.
- **Or** delete the dead sync `execute()` + `shutdown()` if the decision is that the
  engine will never be the live path (reduces surface + misleading tests).

### Failing test (already exists / to extend)
- `tests/unit/test_c10_engine_fixes.py::test_background_tasks_drained_on_shutdown`
  passes in isolation but there is **no** test asserting the *daemon* drains the
  engine — add `tests/unit/test_daemon_async.py::test_daemon_shutdown_drains_engine`
  (fails today; daemon never calls `engine.shutdown`).

### Effort / risk
- Effort: **S**. Risk: low (guarded, shutdown-only). Rollback trivial.

---

## C-LANGGRAPH — landmine factory (not a live bug)

### Verified findings
`agents/capabilities.py:230-236 make_langgraph_tool_loop` builds
`LangGraphAgentLoop(model_gateway=..., chat_model=None, ...)` — passes **no**
`budget_guard` and `chat_model=None`. `execution/langgraph_agent.py:234-243
_resolve_chat_model` returns `self._chat_model` (None) and **never** calls
`get_chat_model`, despite its docstring (`:238`). Zero prod callers today.

### Design
Guard against future activation: (1) make `_resolve_chat_model` actually resolve via
`get_chat_model(self._model_gateway, ...)` when `_chat_model is None`; (2) accept and
thread a `budget_guard` through `make_langgraph_tool_loop` →
`LangGraphAgentLoop` → its `budget_pre_check` (`langgraph_agent.py:113`, with a real
projection per F1). Ship together with F1 or when langgraph is wired live.

### Failing tests to write
- `tests/unit/test_langgraph_tool_loop.py::test_resolve_chat_model_calls_get_chat_model`
  (fails today — returns None).
- `tests/unit/test_h6_langgraph_factory.py::test_factory_threads_budget_guard`.

### Effort / risk
- Effort: **S–M**. Risk: low (dormant). Rollback trivial.

---

## C-EVENTLOOP — single shared to-thread semaphore + unbounded bare sites

### Verified findings
- **No `concurrency/executors.py` exists** (grep: no matches).
- One shared `self._to_thread_semaphore = asyncio.Semaphore(max_to_thread)` with
  default **32** (`event_loop/loop.py:437-438`, config key
  `event_loop.max_to_thread_concurrency`), fronted by
  `_bounded_to_thread` (`loop.py:834-836`). `tests/unit/test_c11_event_loop.py:222`
  pins that loop methods route through it.
- **CONFIRMED pattern, exact cite UNVERIFIED:** bare unbounded `asyncio.to_thread`
  is widespread *outside* the bounded wrapper — dozens of sites across
  `routers/*`, `worker/app.py:362/474/487/505/535`, `models/gateway.py:1390/1428`,
  `execution/engine.py:528/597`, `execution/tool_loop.py:495/509`,
  `execution/langgraph_agent.py:225`, `agents/hibernation.py`, `daemon.py:1080/1220/2148`,
  etc. The single shared semaphore therefore does **not** bound cross-subsystem
  thread fan-out (git/gate/test-runs/model-calls all compete or bypass it).
  **The specific raw sites named in the brief
  (`infra/local_inference.py:271`, `git_automation/locking.py:306`,
  `execution/engine.py:275`, `daemon.py:2154`) did NOT resolve at those exact lines**
  via `make grep asyncio.to_thread` (closest hits: `daemon.py:2148`, `engine.py:528/597`;
  no hit in `infra/local_inference` or `git_automation/locking`). Mark those four
  line cites **UNVERIFIED** — but the broader "one shared cap + many bare sites"
  claim is CONFIRMED.

### Design
Introduce `concurrency/executors.py` with **named, separately-bounded** semaphores
by work class (`GIT`, `GATE`, `MODEL`, `IO`) and a `bounded_to_thread(pool, fn, ...)`
helper; migrate the bare sites to the appropriate pool. Keep the loop's existing
semaphore as the `IO` default for backward compatibility.

### Failing tests to write
- `tests/unit/test_executors.py::test_named_pools_bound_independently`.
- `tests/unit/test_c11_event_loop.py::test_no_bare_to_thread_in_hot_paths` (extend
  the existing structural check across modules, not just loop.py).

### Effort / risk
- Effort: **L** (broad migration). Risk: medium (concurrency behavior change); land
  incrementally, pool-by-pool. Rollback per-pool.

---

## Landing order (dependency-first)

1. **KEYSTONE** — seed profile rates from the pricing catalog. *(Everything below is
   cosmetic until this lands; also silently activates F1-GW and the daemon reserve
   reconciliation.)*
2. **S28** — telemetry `_resolve_session` lazy path (independent; unblocks any data
   the routing work wants to trust).
3. **S19** — thread real test outcomes into `code_quality_score` (independent of 1;
   pair with observation before trusting argmax).
4. **F1** — projection into the 3 live pre-check sites (meaningful only after 1).
5. **C-LANGGRAPH** — harden the dormant factory (bundle with F1).
6. **C-ENGINE** — shutdown wiring or dead-code deletion (decision item).
7. **C-EVENTLOOP** — named executor pools (largest, least urgent; incremental).

## Global risk / rollback posture
- Every fix is **fail-safe toward tighter budgets / more data**, never looser: rate
  seeding never lowers an operator rate; projections only raise the pre-check cost;
  telemetry/score fixes only add rows/real signals. No fix can *weaken* an existing
  cap.
- Each item is independently revertible (single wiring line or single method).
- CI is the gate (local gate OOMs). Land each item with its RED-first test turning
  GREEN and a CI run id as evidence per `AGENTS.md`.
