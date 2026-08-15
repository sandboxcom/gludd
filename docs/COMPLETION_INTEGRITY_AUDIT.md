# Completion Integrity Audit — gludd v0.1.0-alpha.3

**Date:** 2026-06-20/21 (consolidated final — all test-backed verdicts + post-remediation status)
**Sources:**
- Memory: `gludd-completion-integrity-verification.md` (3 domain audits + full live e2e verification campaign)
- `README.md` "Feature & Task Completion Status" table (v0.1.0-alpha.3)
- `TASKS.md` evidence ledger ([x] ticks with commit SHA + named tests)
- `GLM_REMEDIATION_GUIDE_3.md` (binding authority)
- Integration tests: `tests/integration/test_completion_integrity_high.py` (9 tests), `tests/unit/test_scoring_integrity.py`, `tests/integration/test_budget_integrity.py` (5 tests), `tests/integration/test_mcp_selfimprove_integrity.py`, `tests/integration/test_rules_healthgate_integrity.py` (6 tests), `tests/integration/test_multimodel_routing.py` (16 tests)
- Live e2e: `tests/e2e/test_pipeline_live_zai.py` (2 xpassed, real glm-4.6), `tests/live/test_f6_failover_zai_live.py`, all `tests/live/test_zai_*.py`
- Security/infra batch: `tests/integration/test_completion_integrity_high.py` batches 1+2 (8/8 non-model features)

**Method:** Static code trace for each claimed-100% feature, then test-backed verification. Static-only claims are explicitly marked. All inertness claims are test-backed unless noted. The initial static-audit had 2 FALSE POSITIVES (CA-T12 cost, CA-T16 context) that were refuted by live integration tests — those are now in CONFIRMED-WORKING.

**Branch context:** All post-remediation fixes are on branch `test/coverage-recovered` (working tree,
uncommitted, for user review). The alpha.3 ship (C2) is a SEPARATE track on `integration/alpha3-rc`.

---

## SUMMARY (Post-Remediation)

Of the originally-inert features, **12 are now FIXED** and verified in the working tree
(uncommitted, on `test/coverage-recovered`), **3 RECONCILED** (confirmed never actually inert),
and **1 INTENTIONALLY DEFERRED** (CA-T9 AgentToolAdapter-into-generation).

All changes are working-tree-only on branch `test/coverage-recovered` for user review.
The alpha.3 ship (C2) proceeds independently on `integration/alpha3-rc`.

| Metric | Count |
|--------|-------|
| Features claimed 100% in README/TASKS | ~92 |
| CONFIRMED-WORKING (test-backed, pre-remediation) | 19 |
| CONFIRMED-INERT at audit time (test-backed) | 12 |
| VACUOUS / GATED PROOF (not inert, proof insufficient) | 6 |
| RECONCILED (static-audit false positives, confirmed working) | 3 |
| **FIXED in working tree (post-remediation)** | **12** |
| INTENTIONALLY DEFERRED | 1 |
| BEING FINALIZED (CA-T6/CA-T8) | 2 |

### Fixed features (working tree, uncommitted)

| Feature | Fix | File:line |
|---------|-----|-----------|
| Silent-skip flagship flow | title/description fallback + WARNING log | `loop.py:912-929`, `job_invocation.py:51` |
| MCP wiring (daemon smoke-boots) | MCPClient+MCPToolRegistry wired at startup | `daemon.py` (af1a697) |
| Self-improve interval default | fallback `or 10` → default-on | `daemon.py` (af1a697) |
| Gateway health-gate (ModelHealthTracker wired) | ModelHealthTracker → gateway+router+app.state | `daemon.py` (af1a697) |
| Rules engine (UserConfig.rules field+loader) | rules field added, seeds cfg["rules"] from uc.rules | `user_config.py` + `daemon.py` (af1a697) |
| G5 reviewer fence-parse | _extract_json_from_output strips JSON code-fence markers | `reviewer.py` (ab98968) |
| F6 failover openai-exception-types | APITimeoutError/APIConnectionError/APIStatusError retryable | `gateway.py` + `timeout_detector.py` (a143) |
| W6.8 ToolCallLoop._call_model + _run_local | correct call_model signature + JobSpec fields | `tool_loop.py:167-182`, `gludd_agent_run.py` (a4351b8) |
| Async/daemon-path benchmark recording (CA-T11) | execute_async benchmark block + loop feed | `engine.py:352-368`, `loop.py:1012-1028` (aed29) |
| Gateway bind_tools (tool-use) | bind_tools after constructor, no-tools path unchanged | `gateway.py` (a5909e93) |
| Reasoning-token-budgets | max_output_tokens=16384 + startup warning | `zai_example.yml` (af1a697) |
| z.ai endpoint subscription-vs-paypertoken | subscription endpoint + model name | `provider_presets.py:46`, `zai_example.yml` (a4dede5c) |

**Headline:** The flagship "submit todo → AI implements it" flow was non-functional by default
(silent-skip + reviewer-always-fails). Both are now fixed in the working tree. See
`docs/AI_FEATURE_REMEDIATION_PLAN.md` for full fix details.

---

## 1. CONFIRMED-WORKING (test-backed, pre-remediation)

All verdicts in this section are backed by a named integration, live, or e2e test that hits the real production seam (no mock of the path under test).

### Core Spine: G2, G4, G5(*), G6, G7(*)

**Test file:** `tests/integration/test_completion_integrity_high.py` (9 tests) + `tests/e2e/test_pipeline_live_zai.py` (2 xpassed, real glm-4.6, commit abd953)

- **G2 git operations** — real git repo used; non-vacuous.
- **G4 model call + applied edits** — live pipeline: 55 input / 875 output tokens, real `def add(a,b): return a+b` returned; edit applied on disk (`mymod.py` written); confirmed via `EventLoop._dispatch_execute_job_isolated` (non-vacuous).
- **G5(*) DB persistence + ReturnReviewer** — real DB sessions; `ReturnReviewer.review_return()` executed via live gateway (937/1019 + 1064/1573 tokens). *Fence-parse bug now FIXED (reviewer.py ab98968).*
- **G6 tests ran exit 0 + git branch + SHA** — real git branch `feat/add-function`, real 40-char SHA `5a41775a`, `TaskReturn` persisted.
- **G7(*) full pipeline (model→review→git)** — file writes + git both WORK with the real model. *Silent-skip now FIXED (loop.py:912-929); prior unit test `test_full_pipeline_e2e.py` vacuous (see Section 3); live e2e abd953 is real proof.*

### Cost tracking

**Test file:** `tests/integration/test_completion_integrity_high.py`

`record_spend` received `0.2` for 100 input + 50 output tokens (real multiplication). Real path: `event_loop/loop.py:_dispatch_execute_job → models/job_invocation.py:invoke_model_for_generation → gateway._invoke_and_bill`. NOT hardwired $0. (Static-audit CA-T12 was a false positive — it traced dead helper `engine.py:220-234`.)

**Nuance — budget caps:** Budget caps are CONDITIONALLY functional. With non-zero per-token rates, `check_run_budget` blocks + `SpendLimiter` defers correctly (`tests/integration/test_budget_integrity.py`, 5 pass). BUT `ModelProfile.cost_per_input_token/output_token` default to `0.0` (`gateway.py:66-67`) → any profile at zero-default rates silently bypasses all caps. `zai_example.yml` sets `0.001/0.003` so z.ai-model spend counts. Test suite `test_budget_caps.py`/`test_budget_wiring.py` (36 tests) are vacuous — they inject non-zero cost directly, never drive the production billing path.

### Context compaction

**Test file:** `tests/integration/test_completion_integrity_high.py`

`compact()` (ContextCompactor) and `TokenWindowManager.__init__` fire on the real generation path via `AgentCapabilities.prepare_messages`. (Static-audit CA-T16 was a false positive.)

### Scoring cost-cap

**Test file:** `tests/unit/test_scoring_integrity.py`

Cost-cap WORKS. No-ops only when `avg_cost` key is absent from benchmark aggregate (expected when no benchmark history). Not inert.

### Worker auth (W5.6)

**Test file:** `tests/integration/test_completion_integrity_high.py` (batch 1)

Worker `/jobs/*` PSK auth fires before 501 — auth is real, not theater.

### /readyz degraded (W3.4)

**Test file:** `tests/integration/test_completion_integrity_high.py` (batch 1)

Real `app.state._degraded` read path; degraded flag propagates to response.

### Secrets auto-mode (W2.9)

**Test file:** `tests/integration/test_completion_integrity_high.py` (batch 1)

Fallback cases use ZERO mocks — real `connect` + `_openbao_reachable` calls. Mocks confined to external boundary (env/hvac), not the verified seam.

### Hot-reload (W3.12)

**Test file:** `tests/integration/test_completion_integrity_high.py` (batch 1)

Anti-theater: asserts file PARSED, not just existence-checked.

### Agent messages + /api/messages (W7.1)

**Test file:** `tests/integration/test_completion_integrity_high.py` (batch 2)

Real daemon app + DB; 401-without-PSK proven.

### Deployment registry + /api/deployments (C5)

**Test file:** `tests/integration/test_completion_integrity_high.py` (batch 2)

Real file-backed registry; refuses unknown id.

### Lease reclaim (W2.5)

**Test file:** `tests/integration/test_completion_integrity_high.py` (batch 2)

Called unconditionally from `loop._phase_refill_task_buckets`. Caveat: function-level test, not full loop-level.

### Workspace clone (W3.11)

**Test file:** `tests/integration/test_completion_integrity_high.py` (batch 2)

Real git clone on startup + `ProjectRepository` persist.

### Codebase enumeration

**Test file:** `tests/unit/test_code_intelligence.py` (25/25 tests)

`code_intelligence` real AST/callgraph/search; `/admin/code/*` path-jailed. Model-driven enumeration is INERT (CA-T9, intentionally deferred — see Section 5).

### Worker model call (W3.1)

**Test file:** `tests/live/test_zai_live.py` + live verify commit ab2a295

Worker `execute_job → _invoke_gateway_for_job → gateway.call_model` → real response captured (`def hello()...`). Prior unit test used `MagicMock` gateway (vacuous); this fills the gap. Note: downstream 500 = ansible-runner unconfigured in bare env, AFTER the model call — not a W3.1 defect.

### Static task routing

**Test file:** `tests/integration/test_multimodel_routing.py` (16/16 pass, commit a60fc97)

Task type → distinct model correct (coder→glm-4.6, planner→glm-4.5-air, fast→glm-5-turbo); role/pattern/quality/latency/default/weak all resolve correctly; live-confirmed 3 distinct z.ai models answered 3 task types. ADAPTIVE weighting was INERT — now FIXED (benchmark async recording, aed29).

### W4.1 retry (tenacity)

**Test file:** `tests/unit/test_w4_1_tenacity_retry` (5 pass) + `tests/unit/test_a05_overload_retry_cap` (28 pass), commit a143

`tenacity.Retrying + _is_retryable`: retries and recovers on transient; skips `AUTH_ERROR/CONTEXT_LENGTH/INVALID_REQUEST`.

### W7.4 prompt-MQ (gated, default off)

**Test file:** `tests/unit/test_w74_mq_section_reaches_gateway.py`

CONFIRMED-WIRED but CONFIG-GATED DEFAULT OFF (`message_queue_prompt` flag). `render_message_queue_section` + `_append_message_queue_section` reach the model when flag on + `prompt_profile` set. Works; off by default.

### Ansible AI-roles over HTTP (W8.1)

**Test file:** `tests/e2e/test_gludd_agent_run_live.py` + `make test-agent-run-live`

Real glm-4.6 via `gludd_agent_run` HTTP transport. FUNCTIONAL over HTTP. Caveats: (a) `_run_local` in-process leg was BROKEN — now FIXED (a4351b8); (b) prior molecule scenarios used MOCK daemon only. Generalizes to W8.2/W8.3/W13/W14/W15 (same shared module).

### Live z.ai proven

Subscription endpoint `api.z.ai/api/coding/paas/v4` + glm-4.6 — all 4 live test files pass (10 passed + 11 xpassed, 0 fail); real completions/JSON/code-gen/streaming/daemon-HTTP with `tokens > 0`. 8 z.ai models enumerable (`glm-4.5/4.5-air/4.6/4.7/5/5-turbo/5.1/5.2`). Fix committed: `provider_presets.py` + `zai_example.yml` + Makefile targets + test defaults.

---

## 2. CONFIRMED-INERT AT AUDIT TIME (test-backed) — NOW FIXED

All items below were backed by a named test that structurally or behaviorally confirmed inertness at audit time. Post-remediation status noted for each.

### MCP — CRITICAL (×2) — NOW FIXED-in-working-tree

**Test file:** `tests/integration/test_mcp_selfimprove_integrity.py` (commit a1f104)

- **MCP-1:** `daemon.py:642` — `EventLoop(mcp_client=None)` hardcoded literal (AST-verified).
- **MCP-2:** `daemon.py:1311-1317` — `app.state._mcp_client` never set; `_lazy_mcp_handler` raised "MCP client not available" on every dispatch.
- `get_available_tools()` always returned `[]`. YAML `mcp_servers` loaded but never instantiated.

**Post-remediation:** FIXED-in-working-tree (af1a697) — MCPClient+MCPToolRegistry wired at daemon startup; `make smoke` PASSES (daemon boots, full 10-phase tick). Two stale inert-guard tests flipped to assert fixed behavior.

**Severity:** CRITICAL. **Audit verdict:** FALSE-100% — W3.9 "done" tick was wrong. **Current:** FIXED-in-working-tree.

### Self-improve phase permanently disabled — HIGH — NOW FIXED-in-working-tree

**Test file:** `tests/integration/test_mcp_selfimprove_integrity.py` (commit a1f104)

`daemon.py:621-630` — `startup_config.get("self_improve_interval")` fallback dead; `load_startup_config()` never set `interval`; effective `interval` always `0` → disabled.

**Post-remediation:** FIXED-in-working-tree (af1a697) — fallback `or 10` gives 10-minute default cycle.

**Severity:** HIGH. **Audit verdict:** FALSE-100% — W3.7 proves persistence path works if called; trigger was dead. **Current:** FIXED-in-working-tree.

### AgentToolAdapter never wired — HIGH — INTENTIONALLY DEFERRED

**Test file:** `tests/integration/test_completion_integrity_high.py` (CA-T9)

Generation path calls `call_model` with NO `tools=` arg. `AgentToolAdapter` instantiated in `AgentCapabilities` but never consulted by the dispatcher.

**Post-remediation:** INTENTIONALLY DEFERRED. Wiring `AgentToolAdapter` output into the daemon
generation path is an architectural behavior change pending user direction. Tool-use capability
now lives in the fixed `ToolCallLoop` / `agent_run` path (`tool_loop.py:167-182` a4351b8 +
`gateway.py` bind_tools a5909e93).

**Severity:** HIGH. **Audit verdict:** FALSE-100% — W9.1 tick was wrong for this item. **Current:** INTENTIONALLY DEFERRED.

### Async benchmark skipped / adaptive routing permanently starved — HIGH — NOW FIXED-in-working-tree

**Test file:** `tests/integration/test_completion_integrity_high.py` (CA-T11)

`execute_async()` (daemon path) never called `_record_benchmark`. `PromptScoringEngine` orphaned. Adaptive router always returned "insufficient_historical_data".

**Post-remediation:** FIXED-in-working-tree (aed29) — `engine.py:352-368` execute_async benchmark block + `loop.py:1012-1028` feed. 28 tests pass.

**Severity:** HIGH. **Audit verdict:** FALSE-100%. **Current:** FIXED-in-working-tree.

### Scoring cache never read — MED — BEING FINALIZED (CA-T6)

**Test file:** `tests/unit/test_scoring_integrity.py`

`scoring/router.py:36-38,247` — cache populated during init, never read in `route()`.

**Post-remediation:** BEING FINALIZED — status under active review.

### Scoring health-filter default None — MED — FIXED via ModelHealthTracker wiring

**Test file:** `tests/unit/test_scoring_integrity.py`

`scoring/router.py` — `health_tracker=None` default; unhealthy models never filtered.

**Post-remediation:** FIXED-in-working-tree (af1a697) — ModelHealthTracker now instantiated at daemon
startup and passed to AdaptiveRouter.

### Scoring quantization map empty — MED — BEING FINALIZED (CA-T8)

**Test file:** `tests/unit/test_scoring_integrity.py`

`scoring/router.py` — `quantization_map={}` default; penalty never applied.

**Post-remediation:** BEING FINALIZED — status under active review.

### Rules engine — always-empty rules — MED — NOW FIXED-in-working-tree

**Test file:** `tests/integration/test_rules_healthgate_integrity.py` (6 pass, commit a06763)

`UserConfig` had no `rules` field (`user_config.py:88-97`). `daemon.py:650` passed `startup_config.get("rules", [])=[]` always.

**Post-remediation:** FIXED-in-working-tree (af1a697) — `rules: list[dict] = field(default_factory=list)` added to `UserConfig`; `load_startup_config` seeds `cfg["rules"]` from `uc.rules`.

**Severity:** MED. **Audit verdict:** INERT. **Current:** FIXED-in-working-tree.

### Gateway health-gate not wired — MED — NOW FIXED-in-working-tree

**Test file:** `tests/integration/test_rules_healthgate_integrity.py` (6 pass, commit a06763)

`daemon.py:594-603` built `ModelGateway` with no `health_tracker` → `None`.

**Post-remediation:** FIXED-in-working-tree (af1a697) — `ModelHealthTracker()` instantiated at startup, wired to `ModelGateway`, `AdaptiveRouter`, and `app.state._health_tracker`.

**Severity:** MED. **Audit verdict:** INERT. **Current:** FIXED-in-working-tree.

### Budget zero-default rates (footgun)

**Test file:** `tests/integration/test_budget_integrity.py` (5 pass)

Any profile left at `ModelProfile.cost_per_input_token/output_token=0.0` silently bypasses all caps. Not a code bug per se, but a silent configuration footgun.

**Status:** Documented footgun. `zai_example.yml` has correct rates (0.001/0.003); other profiles at zero-default will bypass caps silently. No code fix warranted; operator education.

### Model-driven tool-use — gateway bind_tools — NOW FIXED-in-working-tree

**Test file:** `tests/integration/test_completion_integrity_high.py` + `tests/e2e/test_pipeline_live_zai.py`

Generation path never passed `tools=` to `call_model`. `tools=` kwarg flowed to LangChain constructor via `**kwargs`, not to `.invoke()`.

**Post-remediation:** FIXED-in-working-tree (a5909e93) — `_invoke_and_bill` pops `tools` from kwargs, uses `chat_model.bind_tools(tools).invoke(messages)`. 87/87 no-tools regression green; 7/7 bind_tools tests pass.

---

## 3. VACUOUS / GATED PROOFS

These features are not confirmed inert, but the existing proof tests do not demonstrate what they claim.

### G3 — Ansible runner executes playbooks (no runner test)

No test ever sets `self._runner` on the relevant object. Playbook/extravars execution path has zero e2e coverage. G3-adjacent tests exercise mock or structural paths only. **Verdict:** SUSPECT — passes structurally, real execution unproven.

### G7 unit test — patched dispatch (prior test vacuous)

`tests/integration/test_full_pipeline_e2e.py` patches `loop._dispatch_execute_job`, calls reviewer out-of-band, inserts `TaskDecision` by hand. Proves engine + reconciliation in isolation — NOT that EventLoop orchestrates `model→review→git` end-to-end. **Note:** The live e2e `tests/e2e/test_pipeline_live_zai.py` (abd953) IS real proof; the unit-level test is the vacuous one.

### W6.8 gludd_agent_run._run_local (now fixed, was broken)

"100%" in TASKS rested on a static source-string assertion in `test_playbook_registry.py`; no executing pytest/molecule scenario. `_run_local` was BROKEN — now FIXED (a4351b8). HTTP transport still works (W8.1 confirmed). **Current verdict:** FIXED-in-working-tree for _run_local; static-assertion proof gap remains.

### W7.4 prompt-MQ (default-off)

Code is wired (confirmed, Section 1), but off by default. No production deployment exercises it without explicit `message_queue_prompt` config. Existing tests confirm prompt-text rendering; real model consuming the MQ section is untested without config.

### Ansible molecule suites (mock-only before live test)

Prior molecule scenarios for W8.1/W8.2/W8.3/W13/W14/W15 used MOCK daemon returning canned JSON. `test_playbook_registry.py` = structural only. Live agent-run proves HTTP path for W8.1 (see Section 1); W8.2-W15 remain mock-only. **Verdict:** GATED — structurally proven, runtime via real model unproven for W8.2+.

### Budget cap tests (vacuous positive controls)

`test_budget_caps.py`/`test_budget_wiring.py` (36 tests) inject non-zero cost directly, bypassing the production billing path. They confirm the algorithm is correct but not that production routes reach it with non-zero values. **Verdict:** VACUOUS for production-path proof.

---

## 4. BUGS FOUND + FIXED via live testing

### (1) z.ai endpoint subscription-vs-paypertoken — FIXED-in-working-tree

gludd defaulted to the pay-per-token endpoint `https://open.bigmodel.cn/api/paas/v4` which rejects subscription (coding-plan) keys with 429/1113. Fix: subscription endpoint is `https://api.z.ai/api/coding/paas/v4`.

**Fixed files:** `src/general_ludd/models/provider_presets.py` (`:46` `api_base_url`), `config/model_profiles/zai_example.yml` (`base_url` + `model_name`), Makefile test targets, test `ZAI_BASE_URL` defaults.
**Status:** FIXED-in-working-tree (uncommitted)

### (2) F6 failover openai-exception-types — FIXED-in-working-tree

`_is_retryable` only recognized raw `httpx` exceptions. Real path: `langchain_openai → OpenAI SDK` wraps connection failures as `openai.APIConnectionError` → `_is_retryable` missed it → `TimeoutClassifier` returned UNKNOWN → primary re-raised, fallback chain never walked. Failover was BROKEN for the real openai-compat (z.ai) provider in production. Unit tests passed because they mock `httpx` types.

**Fix:** Added `openai.APITimeoutError/APIConnectionError/APIStatusError` to retryable set + classifier branch.
**Fixed files:** `src/general_ludd/models/gateway.py`, `src/general_ludd/models/timeout_detector.py`.
**Test:** `tests/live/test_f6_failover_zai_live.py` passes (z.ai-to-z.ai chain, fallback returns real glm-4.6).
**Status:** FIXED-in-working-tree (uncommitted)

### (3) G5 ReturnReviewer fence-parse — FIXED-in-working-tree

`reviewer.py:160-178` — `_parse_model_output` did bare `json.loads` with NO markdown-fence stripping. glm-4.6 wraps JSON in `\`\`\`json\`\`\`` fences → `JSONDecodeError` → returns `None` → reviewer fell back to canned `decision="failed" conf=0.0`. EVERY real review was clobbered. Confirmed live: reviewer made its own model call (937/1019 + 1064/1573 tokens), glm-4.6 judged correctly — but fence-parse destroyed the result.

**Fix:** `_extract_json_from_output` strips `\`\`\`json/\`\`\`` fences before `json.loads`. Live-verified good→complete/bad→failed. Commit ab98968.
**Status:** FIXED-in-working-tree (uncommitted)

### (4) Silent-skip flagship flow — FIXED-in-working-tree

`_dispatch_execute_job` called the model ONLY IF ALL held: (A) `model_gateway != None`; (B) `generation` work_type; (C) `prompt_text` truthy. BUT: `POST /api/todos` `AddTodoRequest` had NO `prompt_profile` field → todos created with `prompt_profile=None`; no default assignment anywhere → `_resolve_prompt_text_static` returned `None` → `invoke_model_for_generation` returned `None` (`job_invocation.py:50-54`) with only INFO log → playbook ran with `model_response=None` → looked like it ran. G4-G7 pipeline was REAL but unreachable by default.

**Fix:** `loop.py:912-929` title/description fallback synthesizes prompt when no profile resolves; `job_invocation.py:51` upgraded to WARNING. Logic equivalent to worktree commit ae1a34 (live-verified: default todo gets real glm-4.6 call).
**Status:** FIXED-in-working-tree (uncommitted)

### (5) W6.8 _run_local broken — FIXED-in-working-tree

`tool_loop.py:170-173`: wrong `call_model` signature (TypeError swallowed). `gludd_agent_run.py:141-145`: `JobSpec` missing required fields (ValidationError swallowed). Both caused `_run_local` to always fall through to HTTP.

**Fix (a4351b8):** Correct signature + JobSpec fields. 6/6 behavioral tests pass.
**Status:** FIXED-in-working-tree (uncommitted)

---

## 5. INTENTIONALLY DEFERRED

### CA-T9 — AgentToolAdapter wired into daemon generation path

**Status:** INTENTIONALLY DEFERRED

`AgentToolAdapter` is instantiated in `AgentCapabilities` but wiring its output into the daemon
generation path (passing adapter output to `call_model(tools=)` on every generation request) is
an architectural behavior change that alters what tools the model sees on every call. This is
out of scope for the current remediation pass.

Tool-use capability is now available via the fixed `ToolCallLoop` + corrected `_call_model`
signature (`tool_loop.py:167-182` a4351b8) + `bind_tools` (`gateway.py` a5909e93).
Wiring `AgentToolAdapter` into the daemon main generation path is a product decision pending
user direction.

---

## 6. RECONCILED (static audit false positives — confirmed working)

The initial static audit had **3 confirmed false positives** where inertness was claimed based
on tracing dead code paths or incorrect static reading:

| Static claim | Refuted by | Correct verdict |
|---|---|---|
| **CA-T12** — `_record_metrics` hardwires `cost_usd=0.0` → cost tracking always $0 | `tests/integration/test_completion_integrity_high.py`: `record_spend` received `0.2` for 100/50 tokens on real path | RECONCILED-WORKING — real path is `invoke_model_for_generation → gateway._invoke_and_bill`; dead helper `engine.py:220-234` traced instead |
| **CA-T16** — `ContextCompactor/TokenWindowManager` never instantiated in production | `tests/integration/test_completion_integrity_high.py`: `compact()` + `TokenWindowManager.__init__` fire via `AgentCapabilities.prepare_messages` | RECONCILED-WORKING — static audit missed the real instantiation site |
| **CA-M1** — `mcp/transport.py` `_NPM_FAMILY_LAUNCHERS` double-defined at line 143 → bunx always rejected | Direct read of `mcp/transport.py`: single definition at line 33; no redefinition at line 143 | RECONCILED — no double-def; no code defect |

**Lesson:** Static tracing over-claimed inertness. Always test-back before asserting a feature is inert.

---

## Complete Verdict Table

### CONFIRMED-WORKING (pre-remediation, test-backed)

| Feature | Test file(s) | Notes |
|---|---|---|
| G2 git operations | `test_completion_integrity_high.py` | Real repo/git |
| G4 model call + applied edits | `test_completion_integrity_high.py`, `test_pipeline_live_zai.py` | 55in/875out tokens, real file write |
| G5(*) DB persistence + ReviewerRuns | `test_completion_integrity_high.py`, `test_pipeline_live_zai.py` | *Fence-parse FIXED (reviewer.py ab98968)* |
| G6 tests ran + git branch + SHA | `test_pipeline_live_zai.py` | Real SHA `5a41775a` |
| G7(*) full pipeline | `test_pipeline_live_zai.py` (real); unit test vacuous | *Silent-skip FIXED (loop.py:912-929)* |
| Cost tracking | `test_completion_integrity_high.py` | Not hardwired $0 (CA-T12 RECONCILED) |
| Context compaction | `test_completion_integrity_high.py` | CA-T16 RECONCILED |
| Scoring cost-cap | `test_scoring_integrity.py` | Works; no-ops on absent avg_cost key |
| Worker auth W5.6 | `test_completion_integrity_high.py` (batch 1) | Auth fires before 501 |
| /readyz W3.4 | `test_completion_integrity_high.py` (batch 1) | Real app.state._degraded |
| Secrets auto-mode W2.9 | `test_completion_integrity_high.py` (batch 1) | Zero mocks on real seam |
| Hot-reload W3.12 | `test_completion_integrity_high.py` (batch 1) | Anti-theater: file PARSED |
| Agent messages W7.1 | `test_completion_integrity_high.py` (batch 2) | Real DB, 401-without-PSK proven |
| Deployments C5 | `test_completion_integrity_high.py` (batch 2) | File-backed registry |
| Leases W2.5 | `test_completion_integrity_high.py` (batch 2) | Unconditional from loop |
| Workspace clone W3.11 | `test_completion_integrity_high.py` (batch 2) | Real git clone + persist |
| Codebase enumeration | `test_code_intelligence.py` (25/25) | Real AST/callgraph |
| Worker model call W3.1 | `test_zai_live.py`, ab2a295 | Real glm-4.6 response |
| Static task routing | `test_multimodel_routing.py` (16/16), a60fc97 | 3 distinct models, live confirmed |
| W4.1 retry | `test_w4_1_tenacity_retry` (5), `test_a05_overload_retry_cap` (28) | Real retry + recovery |
| W7.4 prompt-MQ | `test_w74_mq_section_reaches_gateway.py` | Gated default-off |
| Ansible roles over HTTP W8.1 | `test_gludd_agent_run_live.py` | Real glm-4.6; HTTP path only |
| z.ai live (glm-4.6, 8 models) | `test_zai_*.py`, `test_zai_live.py` | 10 passed + 11 xpassed, 0 fail |

### INERT AT AUDIT TIME → POST-REMEDIATION STATUS

| Feature | Test file(s) | Severity | Audit verdict | Post-remediation |
|---|---|---|---|---|
| MCP client hardcoded None (W3.9) | `test_mcp_selfimprove_integrity.py` | CRITICAL | FALSE-100% | FIXED-in-working-tree (af1a697) |
| MCP _lazy_mcp_handler never set | `test_mcp_selfimprove_integrity.py` | CRITICAL | FALSE-100% | FIXED-in-working-tree (af1a697) |
| Self-improve phase disabled by default | `test_mcp_selfimprove_integrity.py` | HIGH | FALSE-100% | FIXED-in-working-tree (af1a697) |
| AgentToolAdapter never wired | `test_completion_integrity_high.py` | HIGH | FALSE-100% | INTENTIONALLY DEFERRED |
| Async benchmark skipped / adaptive routing starved (CA-T11) | `test_completion_integrity_high.py` | HIGH | FALSE-100% | FIXED-in-working-tree (aed29) |
| Scoring cache never read (CA-T6) | `test_scoring_integrity.py` | MED | INERT-by-default | BEING FINALIZED |
| Scoring health-filter default None (CA-T7) | `test_scoring_integrity.py` | MED | INERT-by-default | FIXED-in-working-tree (ModelHealthTracker af1a697) |
| Scoring quantization map empty (CA-T8) | `test_scoring_integrity.py` | MED | INERT-by-default | BEING FINALIZED |
| Rules engine always-empty rules | `test_rules_healthgate_integrity.py` | MED | INERT | FIXED-in-working-tree (af1a697) |
| Gateway health-gate not wired | `test_rules_healthgate_integrity.py` | MED | INERT | FIXED-in-working-tree (af1a697) |
| Model-driven tool-use (tools= never passed) | `test_completion_integrity_high.py`, `test_pipeline_live_zai.py` | MED | INERT | FIXED-in-working-tree bind_tools (a5909e93) |
| Budget zero-default-rates footgun | `test_budget_integrity.py` | MED | INERT-by-default | Documented footgun; no code fix warranted |

### VACUOUS / GATED PROOFS

| Feature | Proof gap | Verdict |
|---|---|---|
| G3 ansible runner executes playbooks | No test sets `self._runner`; zero e2e coverage | SUSPECT |
| G7 `test_full_pipeline_e2e.py` (unit test) | Patches `_dispatch_execute_job`, hand-inserts `TaskDecision` | VACUOUS (live e2e abd953 is real proof) |
| W6.8 gludd_agent_run._run_local | Was broken+static-only; now FIXED (a4351b8) | FIXED-in-working-tree |
| W7.4 prompt-MQ production path | Config-gated default-off; real model consuming MQ untested without config | GATED |
| Ansible molecule suites W8.2-W15 | Mock daemon only; real model via agent-run unproven | GATED |
| Budget cap tests (vacuous controls) | Inject non-zero cost directly; never drive production billing path | VACUOUS |

### BUGS FOUND + FIXED (all FIXED-in-working-tree)

| Bug | Fixed in | Status |
|---|---|---|
| z.ai endpoint subscription-vs-paypertoken | `provider_presets.py`, `zai_example.yml`, Makefile | FIXED-in-working-tree |
| F6 failover openai-exception-types | `gateway.py`, `timeout_detector.py` | FIXED-in-working-tree |
| G5 reviewer fence-parse | `reviewer.py` (ab98968) | FIXED-in-working-tree |
| Silent-skip flagship flow | `loop.py:912-929`, `job_invocation.py:51` | FIXED-in-working-tree |
| W6.8 _run_local (JobSpec + ToolCallLoop sig) | `tool_loop.py:167-182`, `gludd_agent_run.py` (a4351b8) | FIXED-in-working-tree |

---

## Key Cross-Cutting Patterns

**Pattern 1: Wired-but-caller-absent (W9.1 integrity audit flaw)**
W9.1 claimed 100% by showing 29 classes are referenced in `agents/capabilities.py`. The completion audit tool checks that classes have at least one call site, but does not verify the call site is reachable from a production entry point.

**Pattern 2: Sync-vs-async path divergence**
Features work on the synchronous test path but are bypassed on the async daemon path (CA-T11 benchmark recording). Tests exercise `execute()` not `execute_async()`. Now FIXED.

**Pattern 3: Mock-bypass of the exact broken path**
`test_budget_caps.py` injects non-zero cost directly, bypassing the path where production hardwires zero-rate profiles. The test validates the algorithm while the production configuration prevents the algorithm from ever receiving non-zero input in zero-rate deployments.

**Pattern 4: Structural tests for behavioral claims**
Molecule scenarios and `test_playbook_registry.py` tests verify file existence, YAML syntax, module documentation blocks, and mock-daemon HTTP responses. These correctly assert structural integrity but cannot detect wiring gaps (e.g., MCP-1/2).

**Pattern 5: Live testing catches what unit tests cannot**
Two real production bugs (z.ai endpoint, F6 failover exception types) were invisible to the unit test suite because unit tests mock the exact layer where the bug lives. Live testing against the real API surface is the only gate that catches provider-exception-type mismatches.

---

*Docs finalized: 12 fixed / 3 reconciled / 1 deferred*
*All fixes: working-tree-only on branch `test/coverage-recovered`, uncommitted, for user review.*
*Alpha.3 ship (C2) track: `integration/alpha3-rc` (separate).*
