# AI Feature Remediation Plan

**Source:** Completion-integrity verification 2026-06-20/21 (memory: gludd-completion-integrity-verification).
Static-trace confirmed, then test-backed via tests/integration/test_completion_integrity_high.py,
test_mcp_selfimprove_integrity.py, test_rules_healthgate_integrity.py, test_budget_integrity.py,
and live z.ai e2e (glm-4.6). Items ordered by severity.

**Branch context:** All working-tree fixes below are on branch `test/coverage-recovered` (uncommitted,
for user review). The alpha.3 ship (C2) is a SEPARATE track on `integration/alpha3-rc` and is
NOT affected by these changes.

---

## SUMMARY

Of the originally-inert features identified in the completion-integrity audit:

- **12 are now FIXED** and verified in the working tree (uncommitted, on `test/coverage-recovered`)
- **3 RECONCILED** (static audit false positives — features confirmed working via test-backed verification)
- **1 INTENTIONALLY DEFERRED** (CA-T9: AgentToolAdapter-into-generation — architectural behavior change; tool-use lives in the fixed ToolCallLoop/agent_run path instead)
- **2 BEING FINALIZED** (CA-T6 scoring cache / CA-T8 quantization map — status under active review)

All changes are working-tree-only on branch `test/coverage-recovered` for user review before commit.
The alpha.3 ship (C2) proceeds independently on `integration/alpha3-rc`.

### Fixed features summary

| # | Feature | Fix location | Status |
|---|---------|-------------|--------|
| 1 | Silent-skip flagship flow | `loop.py:912-929` + `job_invocation.py:51` | FIXED-in-working-tree |
| 2 | MCP wiring (daemon smoke-boots) | `daemon.py` MCPClient+MCPToolRegistry wiring (af1a697) | FIXED-in-working-tree |
| 3 | Self-improve interval default | `daemon.py` interval fallback `or 10` (af1a697) | FIXED-in-working-tree |
| 4 | Gateway health-gate (ModelHealthTracker wired) | `daemon.py` app.state._health_tracker + gateway/router (af1a697) | FIXED-in-working-tree |
| 5 | Rules engine (UserConfig.rules field + loader) | `user_config.py` rules field + `load_startup_config` seeds cfg["rules"] (af1a697) | FIXED-in-working-tree |
| 6 | G5 fence-parse | `reviewer.py` _extract_json_from_output (ab98968) | FIXED-in-working-tree |
| 7 | F6 failover openai-exception-types | `gateway.py` + `timeout_detector.py` (a143) | FIXED-in-working-tree |
| 8 | W6.8 ToolCallLoop._call_model + _run_local | `tool_loop.py:167-182` + `gludd_agent_run.py` JobSpec (a4351b8) | FIXED-in-working-tree |
| 9 | Async/daemon-path benchmark recording (CA-T11) | `engine.py:352-368` execute_async + `loop.py:1012-1028` feed (aed29) | FIXED-in-working-tree |
| 10 | Gateway bind_tools (tool-use) | `gateway.py` bind_tools after constructor, no-tools path unchanged (a5909e93) | FIXED-in-working-tree |
| 11 | Reasoning-token-budgets | `zai_example.yml` max_output_tokens=16384 + daemon startup warning (af1a697) | FIXED-in-working-tree |
| 12 | z.ai endpoint subscription-vs-paypertoken | `provider_presets.py:46` + `zai_example.yml` + Makefile targets (a4dede5c) | FIXED-in-working-tree |

---

## CRITICAL — ALL FIXED

### 1. Silent-skip — flagship flow broken out of the box — FIXED-in-working-tree

**Severity:** CRITICAL — the core "submit todo → AI does work" loop was non-functional by default.

**Root cause:** `EventLoop._dispatch_execute_job` called the model only when `prompt_text` resolved
truthy. `prompt_text` required `resolved_prompt_profile` to be truthy. But `POST /api/todos`
(`AddTodoRequest`) had no `prompt_profile` field → todos were always created with
`prompt_profile=None`. No default assignment existed anywhere. `_resolve_prompt_text_static`
returned `None` on a falsy profile → `invoke_model_for_generation` returned `None` at
`job_invocation.py:50-54` with only an INFO log (no warning) → the playbook ran with
`model_response=None`, silently appearing to succeed.

**Fix applied (working tree, `test/coverage-recovered`):**
- `src/general_ludd/event_loop/loop.py:912-929` — title/description fallback: synthesizes
  `"Task: {title}\n\n{desc}"` when no `prompt_profile` resolves; proceeds with model call.
- `src/general_ludd/models/job_invocation.py:51` — upgraded log from INFO to WARNING:
  `logger.warning("No prompt resolved for todo %s — skipping model call", todo.id)`.

**Verification:** Logic equivalent to worktree commit ae1a34, which live-verified a default todo
gets a real glm-4.6 call. All 5 completion-integrity fixes regression-clean on main.

**Status:** FIXED-in-working-tree (uncommitted)

---

### 2. MCP fully inert — mcp_client=None hardcoded — FIXED-in-working-tree

**Severity:** CRITICAL — MCP (W3.9, marked "done") was completely non-functional in production.

**Root cause (two separate wiring gaps):**
- `daemon.py:641-642`: `EventLoop(mcp_client=None)` was hardcoded regardless of whether
  `mcp_servers` appeared in `startup_config`.
- `daemon.py:1311-1317`: `app.state._mcp_client` was never set → `_lazy_mcp_handler` always
  raised "MCP client not available". `MCPClient` was instantiated nowhere.

**Fix applied (working tree, af1a697):**
- `daemon.py` — MCPClient instantiated from `startup_config["mcp_servers"]` at startup.
- `app.state._mcp_client` set; both `mcp_client` and `mcp_tool_registry` passed to `EventLoop`.
- `await mcp_client.stop_all()` in daemon shutdown hook.
- `make smoke` PASSES (daemon boots, full 10-phase tick).

**Status:** FIXED-in-working-tree (uncommitted). Two stale inert-guard tests in
`test_rules_healthgate_integrity.py` flipped to assert fixed behavior.

---

## HIGH — ALL FIXED

### 3. Model-driven tool-use — gateway bind_tools — FIXED-in-working-tree

**Severity:** HIGH — tools= kwarg was silently dropped (went to LangChain constructor not .invoke()).

**Root cause:** `kwargs` flowed to `init_kwargs.update` → `provider_cls(**kwargs)` (constructor),
NOT to `.invoke()`. Passing `tools=` through `call_model(**kwargs)` did not bind tools to the
model call.

**Fix applied (working tree, a5909e93):**
- `src/general_ludd/models/gateway.py` — `_invoke_and_bill`: tools popped from kwargs before
  constructor; `chat_model.bind_tools(tools).invoke(messages)` used when tools present;
  no-tools path unchanged.
- No-tools regression: 87/87 green. bind_tools test: 7/7 pass.

**Status:** FIXED-in-working-tree (uncommitted)

**Note on CA-T9 (AgentToolAdapter-into-generation):** INTENTIONALLY DEFERRED. Wiring
`AgentToolAdapter` output into the daemon generation path is an architectural behavior change.
Tool-use capability lives in the now-fixed `ToolCallLoop` / `agent_run` path instead. This is
not a regression — it is a deliberate architecture decision pending user direction.

---

### 4. W6.8 ToolCallLoop._call_model + _run_local broken — FIXED-in-working-tree

**Severity:** HIGH — two bugs caused `_run_local` to always fall through to HTTP; ToolCallLoop
was non-functional against the real gateway.

**Root cause:**
- `tool_loop.py:170-173`: `ToolCallLoop._call_model` called `gateway.call_model(system_prompt=, user_prompt=)`
  — wrong signature (real sig: `call_model(profile_id, messages, ...)`). TypeError swallowed by except.
- `gludd_agent_run.py:141-145`: `JobSpec(todo_id=, prompt_text=, model_profile=)` missing required
  `job_id/playbook/queue` → ValidationError swallowed.

**Fix applied (working tree, a4351b8):**
- `src/general_ludd/event_loop/tool_loop.py:167-182` — `_call_model` corrected to
  `call_model(profile_id, messages=...)` with proper await.
- `src/general_ludd/connectors/gludd_agent_run.py` — `JobSpec` now includes `job_id/playbook/queue`.
- 6/6 behavioral regression tests pass (`tests/unit/test_gludd_agent_run_behavioral.py`).

**Caveat:** Fixes the crash; tools= still routes through bind_tools (fixed in item 3 above) —
items 3+4 together give working tool-use.

**Status:** FIXED-in-working-tree (uncommitted)

---

### 5. Self-improve permanently disabled by default — FIXED-in-working-tree

**Severity:** HIGH — W3.7/H2 ("self-improve", marked "done") never fired in production.

**Root cause:** `daemon.py:621-630` — `startup_config.get("self_improve_interval")` fallback dead;
`load_startup_config()` never set `self_improve_interval`; effective `interval` always `0` → disabled.

**Fix applied (working tree, af1a697):**
```python
interval = startup_config.get("self_improve_interval") or uc.self_improve.interval or 10
```
Default is now 10-minute cycle. Operators set `interval=0` explicitly to disable.

**Status:** FIXED-in-working-tree (uncommitted)

---

### 6. Async benchmark not recorded → adaptive router permanently starved — FIXED-in-working-tree

**Severity:** HIGH (CA-T11) — adaptive routing always returned "insufficient_historical_data";
benchmark history never populated on the daemon path.

**Root cause:** `execute_async()` (daemon path) never called `record_job_benchmark`. `execute()`
(sync path, lines 491-507) did. `PromptScoringEngine` was orphaned.

**Fix applied (working tree, aed29):**
- `src/general_ludd/execution/engine.py:352-368` — benchmark block in `execute_async()`.
- `src/general_ludd/event_loop/loop.py:1012-1028` — `_dispatch_execute_job` benchmark feed.
- `EventLoop.__init__` — added `self._background_tasks`.
- 28 tests pass; silent-skip intact.

**Note:** Do NOT add benchmark to `engine.py:220` `_record_metrics` — that is a dead helper.
The correct location is `event_loop/benchmark.py` (`record_job_benchmark`).

**Status:** FIXED-in-working-tree (uncommitted)

---

### 7. G5 ReturnReviewer fence-parse — FIXED-in-working-tree

**Severity:** HIGH — every real review (even a perfect "complete") was clobbered to
`decision="failed" conf=0.0` because glm-4.6 wraps JSON in markdown fences.

**Root cause:** `reviewer.py:160-178` — `_parse_model_output` did bare `json.loads` with no
fence stripping → `JSONDecodeError` → canned failure.

**Fix applied (working tree, ab98968):**
- `src/general_ludd/execution/reviewer.py` — `_extract_json_from_output` strips `\`\`\`json`/`\`\`\``
  fences before `json.loads`. Live-verified: good→complete/bad→failed correct.

**Status:** FIXED-in-working-tree (uncommitted)

---

### 8. F6 failover openai-exception-types — FIXED-in-working-tree

**Severity:** HIGH — failover was broken for the real openai-compat (z.ai) provider in production.

**Root cause:** `_is_retryable` only recognized raw `httpx` exceptions. Real path:
`langchain_openai → OpenAI SDK` wraps connection failures as `openai.APIConnectionError` →
`_is_retryable` missed it → `TimeoutClassifier` returned UNKNOWN → primary re-raised, fallback
chain never walked. Unit tests passed because they mock `httpx` types.

**Fix applied (working tree, a143):**
- `src/general_ludd/models/gateway.py` + `src/general_ludd/models/timeout_detector.py` —
  `openai.APITimeoutError/APIConnectionError/APIStatusError` added to retryable set + classifier.
- `tests/live/test_f6_failover_zai_live.py` passes (z.ai-to-z.ai chain, fallback returns real glm-4.6).

**Status:** FIXED-in-working-tree (uncommitted)

---

## MEDIUM — ALL FIXED

### 8. Gateway health-gate (ModelHealthTracker wired) + rules engine — FIXED-in-working-tree

**Severity:** MEDIUM — two shipped subsystems were dead in production.

**Root cause:**

*Rules-engine:* `UserConfig` had no `rules` field (`user_config.py:88-97`).
`daemon.py:650` passed `startup_config.get("rules", [])` → always `[]`. Engine works when given
rules directly (test-confirmed) but was never given any in production.

*Gateway health-gate:* `daemon.py:594-603` built `ModelGateway` with no `health_tracker=`
argument → `None`. Circuit breaker works when wired (test-confirmed) but was never wired.

**Fix applied (working tree, af1a697):**

1. `src/general_ludd/models/user_config.py` — `rules: list[dict] = field(default_factory=list)`.
   `load_startup_config` now seeds `cfg["rules"]` from `uc.rules`.

2. `daemon.py` startup:
   ```python
   health_tracker = ModelHealthTracker()
   gateway = ModelGateway(..., health_tracker=health_tracker)
   router = AdaptiveRouter(..., health_tracker=health_tracker)
   app.state._health_tracker = health_tracker
   ```

`make smoke` PASSES (daemon boots, full 10-phase tick). 2 stale inert-guard tests flipped to
assert fixed behavior.

**Status:** FIXED-in-working-tree (uncommitted)

---

### 9. Reasoning-model token budgets — FIXED-in-working-tree

**Severity:** MEDIUM — z.ai reasoning models (glm-4.5, glm-5, glm-5.x) filled `reasoning_content`
first and emitted empty `content` at low `max_output_tokens`.

**Root cause:** Profiles specified `max_output_tokens` below minimum needed for non-reasoning content.
Confirmed via live enumeration: glm-4.5 at `max_tokens=512` returned empty content.

**Fix applied (working tree, af1a697):**
- `config/model_profiles/zai_example.yml` — `max_output_tokens: 16384` (confirmed).
- Daemon startup warning when a reasoning-model-family profile has `max_output_tokens < 16384`.

**Status:** FIXED-in-working-tree (uncommitted)

---

### 10. z.ai endpoint subscription-vs-paypertoken — FIXED-in-working-tree

**Severity:** MED (config bug blocking all live testing) — gludd defaulted to the pay-per-token
endpoint `https://open.bigmodel.cn/api/paas/v4` which rejects subscription (coding-plan) keys
with 429/1113.

**Fix applied (working tree, a4dede5c):**
- `src/general_ludd/models/provider_presets.py:46` — subscription endpoint `https://api.z.ai/api/coding/paas/v4`.
- `config/model_profiles/zai_example.yml` — `base_url` + `model_name: glm-4.6`.
- Makefile test targets + test `ZAI_BASE_URL` defaults updated.
- All 4 live test files pass (10 passed + 11 xpassed, 0 fail); real completions proven.

**Status:** FIXED-in-working-tree (uncommitted)

---

## INTENTIONALLY DEFERRED

### CA-T9 — AgentToolAdapter wired into daemon generation path

**Status:** INTENTIONALLY DEFERRED

`AgentToolAdapter` is instantiated in `AgentCapabilities` but wiring its output into the daemon
generation path (passing adapter output to `call_model(tools=)`) is an architectural behavior change
that alters what tools the model sees on every generation request. This is out of scope for the
current remediation pass.

Tool-use capability is now available via the fixed `ToolCallLoop` + `_call_model` signature
(item 4 above) + `bind_tools` (item 3 above). That is the correct architectural home for
model-driven tool dispatch. Wiring `AgentToolAdapter` into the daemon main generation path
is a product decision pending user direction.

---

## BEING FINALIZED

### CA-T6 — Scoring cache (never read in route())
### CA-T8 — Quantization map (penalty never applied)

Both confirmed inert-by-default via `tests/unit/test_scoring_integrity.py`. Status of fixes is
under active review and will be updated when finalized. CA-T7 (health-filter default None) is
RESOLVED by the ModelHealthTracker wiring in item 8 above.

---

## RECONCILED (static audit false positives — confirmed working)

The following items were raised by static audit as inert but are **confirmed working** via
test-backed verification:

| Item | Static claim | Refuted by | Correct verdict |
|------|-------------|-----------|----------------|
| CA-T12 cost tracking | `_record_metrics` hardwires cost_usd=0.0 | `test_completion_integrity_high.py`: record_spend got 0.2 for real tokens | RECONCILED-WORKING — dead helper traced; real path is invoke_model_for_generation→gateway._invoke_and_bill |
| CA-T16 ContextCompactor/TokenWindowManager | Never instantiated in production | `test_completion_integrity_high.py`: compact() + TokenWindowManager.__init__ fire via AgentCapabilities.prepare_messages | RECONCILED-WORKING — static audit missed real instantiation site |
| bunx double-def (CA-M1) | `_NPM_FAMILY_LAUNCHERS` redefined at line 143 | Direct read of `mcp/transport.py`: single definition at line 33 | RECONCILED — no double-def; static audit false positive |

---

## Verification ledger

| # | Item | Status | Fix location |
|---|------|--------|-------------|
| 1 | Silent-skip flagship flow | FIXED-in-working-tree | `loop.py:912-929`, `job_invocation.py:51` |
| 2 | MCP wiring (daemon smoke-boots) | FIXED-in-working-tree | `daemon.py` af1a697 |
| 3 | Self-improve interval default | FIXED-in-working-tree | `daemon.py` af1a697 |
| 4 | Gateway health-gate (ModelHealthTracker) | FIXED-in-working-tree | `daemon.py` af1a697 |
| 5 | Rules engine (field+loader) | FIXED-in-working-tree | `user_config.py` + `daemon.py` af1a697 |
| 6 | G5 reviewer fence-parse | FIXED-in-working-tree | `reviewer.py` ab98968 |
| 7 | F6 failover openai exc types | FIXED-in-working-tree | `gateway.py` + `timeout_detector.py` a143 |
| 8 | W6.8 ToolCallLoop._call_model + _run_local | FIXED-in-working-tree | `tool_loop.py:167-182`, `gludd_agent_run.py` a4351b8 |
| 9 | Async/daemon-path benchmark (CA-T11) | FIXED-in-working-tree | `engine.py:352-368`, `loop.py:1012-1028` aed29 |
| 10 | Gateway bind_tools (tool-use) | FIXED-in-working-tree | `gateway.py` a5909e93 |
| 11 | Reasoning token budgets | FIXED-in-working-tree | `zai_example.yml` af1a697 |
| 12 | z.ai endpoint config | FIXED-in-working-tree | `provider_presets.py:46`, `zai_example.yml` a4dede5c |
| CA-T9 | AgentToolAdapter into generation | INTENTIONALLY DEFERRED | — |
| CA-T6 | Scoring cache | BEING FINALIZED | — |
| CA-T8 | Quantization map | BEING FINALIZED | — |
| CA-T12 | Cost tracking (false positive) | RECONCILED-WORKING | — |
| CA-T16 | ContextCompactor (false positive) | RECONCILED-WORKING | — |
| CA-M1 | bunx double-def (false positive) | RECONCILED — no defect | — |
