# E2E Harness Audit — Model Discovery, Weights, Cost

**Date:** 2026-06-16
**Scope:** Audit the five gludd subsystems the E2E harness must exercise, so the
tests assert on *real* behavior, not vacuous defaults (every metric reading 0.0 /
empty). Read-only audit. Repo root for source: `/Users/shawnwilson/gludd/src/general_ludd/`.

**Headline:** The *schemas and the read-side machinery are largely built and
correct* (provider preset for z.ai, benchmark table with score+cost columns,
cost-aware AdaptiveRouter, real token×rate cost formula in the gateway). The
gap is almost entirely on the **write/wire side**: the real cost the gateway
computes never reaches the DB, no autodiscovery hits the GLM endpoint, profile
cost rates default to 0.0, and there is no skill-effectiveness store. An E2E
test run against the system as-is would see structurally-zero cost, an empty
benchmark table, and no autodetected models — i.e. the assertions would pass
vacuously or have nothing to assert on.

---

## Per-subsystem verdict table

| # | Subsystem | Status | Evidence (file:line) |
|---|-----------|--------|----------------------|
| 1 | z.ai / GLM provider + OpenAI-compat connection | **WORKS (config) / PARTIAL (live-unproven)** | `models/provider_presets.py:45-55` (zai preset, `https://open.bigmodel.cn/api/paas/v4`, `ChatOpenAI`); `models/gateway.py:52-97` (ModelProfile), `:254`,`:262-272` (provider class + base_url alias resolve, SSRF-guarded); `secrets/env.py:20`; `tui/config_editor.py:177-181` |
| 2 | Model autodiscovery from a service (GET /models) | **ABSENT (for GLM/z.ai)** | `models/model_registry.py:44-47,66` (`HfApi.list_models` — HuggingFace only); `routers/models.py:237-242` (`/admin/models` lists *local* profiles); zai preset `free_models_endpoint=None` (`provider_presets.py:53`) and nothing fetches it |
| 3 | Model-usefulness weights as tasks complete | **PARTIAL — schema+router built, write path inert** | Table: `db/models.py:491-522` (score cols + `cost_usd`); aggregation: `db/repository.py:636-683`; router: `scoring/router.py:40-220` (cost-adjusted rank, wired in `daemon.py:932-936`). BUT live writer `execution/engine.py:338-354`→`event_loop/benchmark.py:25` passes **no cost_usd / no real tokens**; trace writer `observability/recorder.py:50` is **dead in production** |
| 4 | Skill / system-prompt usefulness per model | **PARTIAL (prompt) / ABSENT (skill)** | Prompt effectiveness *is* representable via `benchmark_results.(prompt_profile_id, model_profile_id, task_type)` (`db/models.py:495-502`), aggregated by `get_aggregate_scores` (`repository.py:636-683`). BUT `PromptProfileModel` carries no score (`db/models.py:385-401`); **no `skill_effectiveness` table, no `BehaviorRenderer` class exists anywhere**; `Skill.model_profile` is a routing hint, not a score |
| 5 | Usage cost calculations | **PARTIAL — real formula, but feeds only in-memory; rates default 0.0** | Real `token×rate`: `models/gateway.py:319-322`; flows to budget guard `:332-333` + in-memory metrics `:345-354` + `ModelResponse.cost_estimate :359`. NOT persisted to `benchmark_results` or `spend_records`. Rates default 0.0: `gateway.py:64-65`. `SpendLimiter` does not compute cost and is **not wired to dispatch** (`controllers/spend_limiter.py:17-28` TODO) |

Legend: **WORKS** = production-functional today · **PARTIAL** = present but a key
link is inert/unwired · **ABSENT** = not built.

---

## Subsystem detail

### 1. z.ai / GLM provider + connection — WORKS (config) / PARTIAL (live-unproven)

gludd treats z.ai/GLM as a generic **OpenAI-compatible** provider — there is no
dedicated GLM SDK, and there does not need to be.

- Source of truth is the `"zai"` preset (`models/provider_presets.py:45-55`):
  `api_base_url = https://open.bigmodel.cn/api/paas/v4`,
  `provider_package = langchain-openai`, `provider_class = ChatOpenAI`,
  credential env `ZAI_API_KEY` (aliases `zai_api_key`, `zai_api_base`).
- `ModelGateway` is provider-agnostic: it resolves the provider class via the
  registry (`gateway.py:254`) and the base URL via a secrets alias with an SSRF
  guard (`gateway.py:262-272`). So the gateway *can* talk to `/api/paas/v4`
  given a profile + `ZAI_API_KEY`/`ZAI_BASE_URL`.
- `ModelProfile` lives in `gateway.py:52-97` (there is **no** standalone
  `model_profile.py`).
- The TUI exposes Z.AI config (`tui/config_editor.py:177-181`,
  `ZAI_BASE_URL` default `https://open.bigmodel.cn/api/paas/v4`, `ZAI_MODEL=auto`).

**For the E2E assertion "zai connects" to be meaningful:** the harness must
build a `ModelProfile` from the `zai` preset, supply `ZAI_API_KEY`, and make a
real call (or a faithful OpenAI-compat mock at `/api/paas/v4` returning
`usage` with `prompt_tokens`/`completion_tokens`). There is a live test corpus
(`tests/live/test_zai_live*.py`, `ZAI_MODEL=glm-5.1`) to model on. The 93-file
`connectors/` tree is an **observability ingest layer, unrelated to model
routing** — do not assert through it.

### 2. Model autodiscovery from a service — ABSENT for GLM/z.ai

- The only `list_models` call is **HuggingFace Hub** (`model_registry.py:66`,
  `HfApi`), for local/self-hosted model download — not a `GET /models` against
  z.ai.
- `/admin/models` (`routers/models.py:237-242`) returns
  `gateway.list_profiles()` — **locally registered profiles only**, no remote
  enumeration.
- The preset schema *anticipates* discovery (`free_models_endpoint`,
  e.g. OpenRouter `.../models` at `provider_presets.py:20`) but zai's is `None`
  (`:53`) and **no code path fetches `free_models_endpoint`** for any provider.

**For "models autodetected" to be meaningful:** this must be **built**. Minimal
version: a function that does `GET {api_base}/models` on an OpenAI-compatible
endpoint (z.ai exposes one), parses the `data[].id` list, and registers/returns
discovered model ids. Wire it to `free_models_endpoint` (or `{api_base}/models`)
and surface via `/admin/models`. Without this, an autodiscovery assertion has
nothing real to check.

### 3. Model-usefulness weights as tasks complete — PARTIAL

The README's flags are **partly outdated**: the AdaptiveRouter *does* do
cost-constrained routing and *does* read `avg_cost`.

- Read side is real: `scoring/router.py` ranks by
  `weights.quality*quality − weights.cost*cost_norm` (`:157-170`), honors a
  `max_cost_usd` cap with fail-closed semantics (`:49-75`, `:96-107`), and is
  wired in production (`daemon.py:932-936`, with a real `BenchmarkRepository`).
  `get_aggregate_scores` (`repository.py:636-683`) groups by
  `(prompt_profile_id, model_profile_id, task_type)` and emits `avg_cost` +
  `composite_score`.
- **The write side is inert.** Production records benchmark rows only via
  `execution/engine.py:338-354` → `event_loop/benchmark.py:25`
  (`record_job_benchmark`), which **passes no `cost_usd` and no real token
  counts** (`input_tokens` is a `len//4` estimate); scores are fixed heuristics
  (`event_loop/benchmark.py:30-32`: completion 1.0/0.0, code_quality 0.5). The
  richer trace-based writer `observability/recorder.py:record_from_trace`
  (which *would* carry `trace.total_cost_usd` and graded scores) has **no
  production caller** — `event_loop/loop.py:189-190` declares
  `_active_traces`/`_benchmark_recorder` but never uses them.
- Net: the leaderboard/router rank on `composite_score` from heuristic scores
  and on `avg_cost = 0.0` everywhere. So "weights per-model-per-task" exists
  structurally but is fed near-vacuous data.

**For "weights computed per-model-per-task" to be meaningful:** close the write
loop — when a task completes, write a `benchmark_results` row carrying (a) real
`completion_score`/`code_quality_score` (the graded path in `scoring/engine.py:223-224`
or `recorder.py` exists; wire it), (b) real `input_tokens`/`output_tokens` from
the gateway `usage`, and (c) real `cost_usd` (see #5). Easiest path: have the
execution/event-loop completion handler call `record_from_trace` with a span
that the gateway populated, OR extend `record_job_benchmark` to accept and pass
the gateway's `ModelResponse.cost_estimate` + token counts.

### 4. Skill / system-prompt usefulness per model — PARTIAL (prompt) / ABSENT (skill)

- **Prompt** usefulness is representable: every `benchmark_results` row keys on
  both `prompt_profile_id` and `model_profile_id` (`db/models.py:495-502`), and
  `get_aggregate_scores` aggregates per (prompt, model, task). So "which prompt
  works best with which model" is answerable *once rows are populated* (#3).
  Note `PromptProfileModel` itself has **no score column** (`db/models.py:385-401`)
  — the effectiveness lives only in the benchmark join.
- **Skill** usefulness is **absent**: no `skill_effectiveness` table/column,
  no scoring of skills per model. `Skill.model_profile` is a "use this model
  for this skill" hint, not feedback. There is **no `BehaviorRenderer` class**
  anywhere in the source tree (the prompt's reference to it does not match
  current code).

**For "skill/prompt usefulness tracked" to be meaningful:** prompt-vs-model
tracking only needs the #3 write loop closed. Skill-vs-model tracking must be
**built** — either add a `skill` dimension to benchmark rows (a `skill_id`
column + include it in the group-by), or a dedicated `skill_effectiveness`
table. Pick the benchmark-column extension (cheapest, reuses the aggregation).

### 5. Usage cost calculations — PARTIAL

- The formula is **real and correct**: `cost = input_tokens*cost_per_input_token
  + output_tokens*cost_per_output_token` (`gateway.py:319-322`), with hardened
  token coercion (rejects bool, clamps ≥0, accepts OpenAI key names).
- But the computed cost goes **only in-memory**: budget guard
  `record_spend(cost)` (`gateway.py:332-333`), in-memory metrics
  `record_model_call` (`:345-354`, updates `ModelUsage.total_cost_usd`,
  `metrics/collector.py:35-38`), and `ModelResponse.cost_estimate` (`:359`).
  **No `benchmark_results.cost_usd` and no `spend_records` row is written from
  the call path.** `execution/engine.py:212` even hardcodes `cost_usd=0.0`.
- `ModelProfile.cost_per_input_token`/`cost_per_output_token` **default to 0.0**
  (`gateway.py:64-65`) — so even the in-memory cost is 0.0 unless a profile is
  configured with real rates.
- `SpendLimiter` (`controllers/spend_limiter.py`) **does not compute cost** (it
  accepts a caller-supplied float) and is **not wired into dispatch**
  (`:17-28` explicit TODO). `spend_records` table exists (`db/models.py:440-468`)
  but is not fed from the model-call path.

**For "real cost calculated" to be meaningful:** (a) configure the test profile
(esp. the `zai` profile) with **non-zero** `cost_per_input_token` /
`cost_per_output_token` so the formula produces dollars; (b) **persist** the
computed `cost_estimate` — write it into the `benchmark_results.cost_usd` of the
task's row and/or a `spend_records` row at call time; (c) optionally wire
`SpendLimiter.would_exceed()` into dispatch per its TODO so the limiter sees
real spend. Without (a)+(b), every cost assertion is checking 0.0 == 0.0.

---

## Prioritized minimal fixes (so E2E assertions are non-vacuous)

Ordered by leverage; each is the smallest change that turns a vacuous assertion
into a real one.

**P0 — make cost real and persisted (unblocks #3 and #5 together)**
1. Set non-zero `cost_per_input_token`/`cost_per_output_token` on the test
   profiles (at minimum the `zai` profile). One-line config; without it the
   correct formula at `gateway.py:319-322` always yields 0.0.
2. Persist the gateway's computed cost. In the task-completion path
   (`execution/engine.py:338-354`), thread the `ModelResponse.cost_estimate` +
   real `input_tokens`/`output_tokens` into `record_job_benchmark`
   (`event_loop/benchmark.py:20-32`), replacing the `cost_usd=0.0` default and
   the `len//4` token estimate. Now `benchmark_results.cost_usd` is real and the
   AdaptiveRouter's `avg_cost` ranking (`scoring/router.py:169`) operates on
   real data.

**P1 — make per-model-per-task scores real (completes #3, #4-prompt)**
3. Write real graded scores into the benchmark row instead of the fixed
   heuristics (`event_loop/benchmark.py:30-32`). Either call the existing
   graded scorer (`scoring/engine.py:223-224`) or activate the dead trace path
   (`observability/recorder.py:record_from_trace`) by having the event loop
   actually create/complete spans (`event_loop/loop.py:189-190` declares but
   never uses `_active_traces`/`_benchmark_recorder`). Once rows carry real
   `composite_score`, the leaderboard and per-(prompt,model) aggregation are
   meaningful.

**P2 — build z.ai model autodiscovery (unblocks #2)**
4. Add a `discover_models(api_base, api_key)` that does
   `GET {api_base}/models` (OpenAI-compat; z.ai supports it), parses `data[].id`,
   and registers/returns the ids. Wire it behind `free_models_endpoint` (or
   default to `{api_base}/models`) and expose through `/admin/models`
   (`routers/models.py:237-242`). This is net-new code; there is no existing
   GLM-endpoint enumeration to assert against today.

**P3 — build skill-vs-model effectiveness (completes #4-skill)**
5. Add a `skill_id` dimension to benchmark recording (new column on
   `benchmark_results` + include in `get_aggregate_scores` group-by at
   `repository.py:636-683`), OR a small `skill_effectiveness` table. Prefer the
   column extension — it reuses the existing aggregation and the P1 write loop.

**P4 — wire SpendLimiter into dispatch (hardening for #5)**
6. Honor the TODO at `controllers/spend_limiter.py:17-28`: call
   `would_exceed()` before model calls and feed real per-call cost into the
   limiter, and persist to `spend_records` (`db/models.py:440-468`). Lets the
   harness assert budget-cap behavior end to end.

**Do-not-assert-through (avoid vacuous coverage):**
- The 93-file `connectors/` tree — observability ingest, not model routing.
- `model_registry.py` `list_models` — HuggingFace Hub, not z.ai discovery.
- `/admin/models` as a discovery oracle — it only lists local profiles.
- Any cost/score assertion before P0/P1 land — it checks 0.0/heuristic constants.

---

## Evidence index (production files, all under `src/general_ludd/`)

- `models/provider_presets.py:11-55` — provider presets incl. `zai`
- `models/gateway.py:52-97` (ModelProfile, rates default 0.0 at :64-65),
  `:254,:262-272` (provider/base_url resolve), `:319-333,:345-359` (cost compute + sinks)
- `models/model_registry.py:44-47,66` — HuggingFace `list_models`
- `routers/models.py:237-242` — `/admin/models` lists local profiles
- `db/models.py:385-401` (PromptProfile, no score), `:440-468` (spend_records),
  `:491-522` (benchmark_results score+cost cols)
- `db/repository.py:628-634` (record_result passthrough), `:636-683` (get_aggregate_scores)
- `scoring/router.py:40-220` (AdaptiveRouter cost-aware ranking + caps)
- `scoring/engine.py:223-224` — graded score setters (graded path)
- `execution/engine.py:212` (cost_usd=0.0), `:338-354` (completion → record_job_benchmark)
- `event_loop/benchmark.py:20-32` — record_job_benchmark (cost default 0.0, heuristic scores)
- `event_loop/loop.py:189-190` — declares but never uses traces/recorder
- `observability/recorder.py:50,90-91,97,103` — record_from_trace (dead in prod)
- `observability/tracer.py:33,43` — span cost defaults 0.0
- `controllers/spend_limiter.py:17-28` — SpendLimiter not wired (TODO)
- `daemon.py:790-793` (budget record_spend), `:932-936` (AdaptiveRouter wiring)
- `secrets/env.py:20` (ZAI_BASE_URL alias), `tui/config_editor.py:177-181` (Z.AI config)
