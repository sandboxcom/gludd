# Multi-Model Pipeline Architecture

> Auto-generated 2026-08-10 from live source. Covers model routing, the PLANNER→CODER→REVIEWER pipeline, model scoring, small-model capability gating, and cost-aware source selection. Source files: `cloud/multi_model_game_pipeline.py`, `models/gateway.py`, `models/model_scoring.py`, `models/cost_router.py`, `routing_roles/small_model_policy.py`, `small_models/recommender.py`.

## 1. Component Diagram

```text
┌─────────────────────────────────────────────────────────────────────┐
│                        INBOUND API LAYER                            │
│                                                                     │
│  POST /api/game/generate-multi   POST /api/generate/create          │
│  (routers/game.py:39)            (routers/generate.py:65)           │
└──────────────┬──────────────────────┬───────────────────────────────┘
               │                      │
               ▼                      ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    SoftwareGenerator                                  │
│              (cloud/software_generator.py:62)                         │
│                                                                      │
│  generate()          → single model dispatch (call_model)            │
│  generate_multi()    → MultiModelGamePipeline dispatch               │
│  _authorize_dispatch → SmallModelTaskPolicy gate (optional)          │
│  validate_code()     → AST parse + project-type validation rules     │
└──────────────┬──────────────────────┬────────────────────────────────┘
               │                      │
               ▼                      ▼
┌──────────────────────────┐  ┌────────────────────────────────────────┐
│  MultiModelGamePipeline  │  │      SmallModelTaskPolicy               │
│  (cloud/multi_model_     │  │  (routing_roles/small_model_policy.py)  │
│   game_pipeline.py:130)  │  │                                        │
│                          │  │  authorize(task, identity, evidence)    │
│  plan()  → DesignSpec    │  │    → DispatchAction (LOCAL/DENY)        │
│  code()  → str           │  │  FORBIDDEN_IMPACTS: MUTATE_REPO,       │
│  review()→ ReviewResult  │  │    EXEC_CMD, CRED_ACCESS, DEPLOY ...   │
│  generate() → iter loop  │  │  DEFAULT_TASK_CONTRACTS: 8 contracts   │
└──────────┬───────────────┘  └────────────────────────────────────────┘
           │
           ▼  (every stage calls gateway.call_model / route_for_task)
┌──────────────────────────────────────────────────────────────────────┐
│                        ModelGateway                                   │
│                  (models/gateway.py)                                  │
│                                                                      │
│  ┌─────────────────┐  ┌──────────────────┐  ┌─────────────────────┐ │
│  │ route_for_task() │  │ route_for_task_  │  │ call_model()        │ │
│  │                  │  │ with_cost()      │  │ call_model_cost_    │ │
│  │ _TASK_MODEL_     │  │                  │  │ aware()             │ │
│  │ PREFERENCES:     │  │ → CostAwareRouter │  │                     │ │
│  │  code    → deep  │  │ → route_by_cost  │  │ 24 providers via    │ │
│  │  ansible → qwen  │  │ → budget check   │  │ ProviderRegistry    │ │
│  │  general → ds-v3 │  │ → peak/off-peak  │  │ LangChain backends  │ │
│  │  game    → claude│  │                  │  │ failover chain      │ │
│  └─────────────────┘  └──────────────────┘  └─────────────────────┘ │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  _best_profile_for(name)  — traverses enabled profiles by name  │ │
│  │  _profiles dict           — all registered ModelProfile entries │ │
│  │  _cost_router             — optional CostAwareRouter wiring     │ │
│  └────────────────────────────────────────────────────────────────┘ │
└──────────┬───────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     Provider Layer                                    │
│                                                                      │
│  ┌──────────────────────┐    ┌────────────────────────────────────┐ │
│  │ 24 Cloud Providers   │    │ 3 Local GGUF Models                 │ │
│  │ (provider_presets.py │    │ (_local_model_configs.py:15)         │ │
│  │  :289)               │    │                                    │ │
│  │                      │    │ qwen-0.5b       Q4_K_M              │ │
│  │ openrouter, openai,  │    │ tinyllama-1.1b  Q4_K_M              │ │
│  │ anthropic, groq,     │    │ smollm2-135m    Q4_K_M              │ │
│  │ deepseek, google,    │    │                                    │ │
│  │ mistral, cohere, ... │    │ Served via vLLM / llama.cpp        │ │
│  └──────────────────────┘    └────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

## 2. Data Flow

### 2.1 Request to Response (end-to-end)

```text
User request (JSON)
    │
    ├── project_type validated against PROJECT_TYPE_REGISTRY (project_types.py)
    ├── model_profiles resolved: explicit model_id OR "default"
    │
    ▼
SoftwareGenerator.generate_multi(spec, model_profiles, model_identity, evidence)
    │
    ├── [OPTIONAL] SmallModelTaskPolicy.authorize():
    │       task_spec = SmallModelTaskSpec(task_kind="coding", role=CODER,
    │           impacts={READ_SOURCE, WRITE_ARTIFACT}, acceptance_checks=(...))
    │       → DispatchAction.LOCAL (allow) or PermissionError (deny)
    │
    ▼
MultiModelGamePipeline.generate(description, planner_model, coder_model, reviewer_model)
    │
    ├── Stage 1: plan(description, model_id=planner_model)
    │       gateway.call_model(model_id, messages=[system_prompt, user_desc])
    │       → LLM response parsed by _PLANNER_RESPONSE_RE → DesignSpec
    │
    ├── Stage 2: code(spec, model_id=coder_model)
    │       gateway.call_model(model_id, messages=[system_prompt, spec.to_prompt()])
    │       → LLM response → raw Python code string
    │
    └── Stage 3+ (loop): review(code, spec, model_id=reviewer_model)
            gateway.call_model(model_id, messages=[system, spec, code])
            → LLM response parsed by _REVIEWER_RESPONSE_RE → ReviewResult
            │
            if result.passed → return code
            else → code(spec, previous_code, feedback) [fix round]
                   repeat up to max_review_rounds (default 3)
```

### 2.2 Model Resolution Flow (when model_id="default")

```text
gateway.route_for_task(task_kind)
    │
    ├── lookup _TASK_MODEL_PREFERENCES[kind]
    │       e.g. "code" → ["deepseek-coder", "glm-4", "deepseek-v3", ...]
    │
    ├── for each preference_name in list:
    │       _best_profile_for(name) → first enabled profile matching
    │
    ├── fallback: any enabled profile
    │
    └── ValueError if no enabled profile exists

Cost-aware variant:
gateway.route_for_task_with_cost(task_kind, budget_remaining, now)
    │
    ├── [if cost_router wired]
    │       CostAwareRouter.route_by_cost(task_kind, budget_remaining, now)
    │       → peak/off-peak pricing adjustment
    │       → selects cheapest model within budget
    │       → returns ModelRoute (model_id, estimated_cost, peak_status, hourly_rate)
    │       → maps provider/model_name → gateway profile_id
    │
    └── [if cost_router absent]
            → fallback to route_for_task(task_kind)
```

## 3. Model Selection Algorithm (recommend_model)

The recommender (`small_models/recommender.py:192`) provides a parallel selection path
for small/local models, based on capability evidence rather than provider presets.

### 3.1 Algorithm

```text
recommend_model(task_description, hardware, store, urgent=False)

1. PARSE: _map_task_to_capabilities(description)
   → regex keyword match against _TASK_KEYWORD_MAP
   → returns list of (task_kind, TaskRole) pairs
   e.g. "compact this summary" → [("context_compaction", COMPACTOR)]

2. QUERY: for each (task_kind, role):
       store.query_by_task_kind(task_kind) → capability evidence records
       filter: records with collection_ok=True
       group by model_profile_id

3. FILTER: for each model_id:
       can_run_model(hardware, model_id) → hardware fit check
       exclude if can_run=False AND model is known (not "unknown model")
       skip models that cannot physically run

4. SCORE: _compute_score(records, hardware, model_id, radar_profile, urgent)
       (see scoring formula below)

5. RANK: sort by score descending, return list of ModelRecommendation
```

## 4. Scoring Formula

### 4.1 Model Scorer (models/model_scoring.py)

Used by `rank_models()` for cloud model selection. Three-component weighted score:

```text
score = success_rate * 60.0                             [capability: 60%]
      + (1.0 / (adjusted_cost * 1000)) * 20.0           [cost efficiency: 20%]
      + (1.0 / (latency_ms / 1000)) * 20.0              [speed: 20%]
      + (5.0 if budget.prefer_local else 0)              [local bias]
      × (0.3 if latency > budget.max_latency_ms else 1.0)  [latency penalty]
      × (0.4 if cost > budget.max_cost_usd else 1.0)       [budget penalty]
```

Adjusted cost = `base_cost * cost_multiplier` where multiplier comes from CostAwareRouter's peak/off-peak schedule. GPU hardware reduces latency estimate by ×0.6 when detected (≥1 GPU, ≥4GB VRAM).

### 4.2 Recommender Score (small_models/recommender.py)

Used by `recommend_model()` for small/local model selection. Weighted composite:

```text
Standard weights (off-peak or urgent):
  score = 0.30 * avg_pass_rate         [evaluation suite pass rate]
        + 0.20 * collection_ok_rate    [test collection success]
        + 0.15 * evidence_count_score  [evidence depth, capped at 3 records]
        + 0.10 * hw_score              [hardware fit: fits=1.0, marginal=0.6, insufficient=0.2]
        + 0.15 * radar_breadth         [profile breadth: nonzero_dim_ratio*0.6 + avg*0.4]
        + 0.10 * cost_score            [inverse cost ranking]

Peak-time, non-urgent — cost weight doubled (0.25), quality weights reduced:
  score = 0.25 * avg_pass_rate
        + 0.15 * collection_ok_rate
        + 0.12 * evidence_count_score
        + 0.10 * hw_score
        + 0.13 * radar_breadth
        + 0.25 * cost_score
```

All scores clamped to [0.0, 1.0]. Models are ranked by descending score.

## 5. Source Routing

The system routes to one of two sources per model invocation:

| Source | Trigger | Characteristics |
|--------|---------|-----------------|
| `cloud` (default) | Provider API keys set in env | 24 providers via ModelGateway; full model power; pay-per-token |
| `local` | `GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS=1` + `LOCAL_MODEL_BASE_URL` set | 3 GGUF models via vLLM/llama.cpp; free inference; constrained capability |

Source is determined in `model_scoring.py:69` by checking whether `LOCAL_MODEL_BASE_URL` points to localhost and the allow flag is set. The `SmallModelTaskPolicy` gates ALL local-model dispatching through capability evidence — a local model cannot execute high-impact tasks (mutate repo, execute commands, credential access, deployment, release, security decisions).

## 6. Configuration

### 6.1 Gateway-Level Configuration

| Config Point | Location | Effect |
|-------------|----------|--------|
| `_TASK_MODEL_PREFERENCES` | gateway.py:1173 | Per-task-kind model preference chains |
| `_DEFAULT_MODEL_PREFERENCE` | gateway.py:1180 | Fallback model chain |
| `_cost_router` | gateway.py (wired at init) | Enables cost-aware routing |
| `_profiles` | gateway.py (loaded from config dir) | Registered ModelProfile entries |
| Provider API keys | Environment variables | Auto-configured by `AutoConfigurator` |
| Budget via `BudgetProfile` | model_scoring.py:28 | max_cost_usd, prefer_local, max_latency_ms |

### 6.2 Small-Model Policy Configuration

| Config Point | Location | Default | Effect |
|-------------|----------|---------|--------|
| `PolicyConfig.max_attempts` | small_model_policy.py:187 | 2 | Max retries for a bounded task (1–3) |
| `PolicyConfig.min_evaluation_cases` | small_model_policy.py:188 | 20 | Minimum evaluation cases required for evidence |
| `DEFAULT_TASK_CONTRACTS` | small_model_policy.py:120 | 8 contracts | Task-kind → allowed roles, impacts, acceptance checks |
| `FORBIDDEN_IMPACTS` | small_model_policy.py:65 | 7 impacts | Always-denied impact categories for small models |

### 6.3 Cost Router Configuration

| Config Point | Location | Default | Effect |
|-------------|----------|---------|--------|
| `PeakPricingSchedule.peak_start_hour` | cost_router.py:39 | 8 (UTC) | Peak pricing window start |
| `PeakPricingSchedule.peak_end_hour` | cost_router.py:39 | 20 (UTC) | Peak pricing window end |
| `peak_multiplier` | cost_router.py:39 | 1.5 | Cost multiplier during peak |
| `off_peak_multiplier` | cost_router.py:39 | 0.7 | Cost multiplier during off-peak |
| `peak_days` | cost_router.py:39 | Mon–Fri | Days peak pricing applies |

### 6.4 Pipeline-Level Configuration

| Config Point | Location | Default | Effect |
|-------------|----------|---------|--------|
| `max_review_rounds` | pipeline.py generate() | 3 | Max review-fix iterations |
| `planner_model` / `coder_model` / `reviewer_model` | API parameter | "default" | Per-stage model override |
| `_MODEL_PROFILES` | project_types.py:860 | 4 entries | Role→capability mapping per project type |

## 7. Extension Points

### 7.1 Adding a New Project Type

1. Create a `ProjectType` dataclass with type_id, display_name, prompt templates, validation rules.
2. Register via `register_project_type()` at runtime, or add to `_BASE_DEFINITIONS` for persistence.
3. Add to `VALID_PROJECT_TYPES` if legacy validation is needed.
4. Optionally add to `_MODEL_PROFILES` for role→capability hints.

### 7.2 Adding a New Cloud Provider

1. Add provider entry to `PROVIDER_FLAGSHIP_MODELS` in `provider_presets.py:289`.
2. Ensure the credential env var (e.g. `NEWPROVIDER_API_KEY`) is read by `AutoConfigurator.auto_configure_from_env()`.
3. Add to the LangChain provider registry if the backend is new.

### 7.3 Adding a New Local Model

1. Add config entry to `_local_model_configs.py:15` with HuggingFace repo, quant, and model name.
2. Pull and serve the GGUF via vllm or llama.cpp.
3. Run the capability evaluation suite to produce evidence records for the `CapabilityEvidenceStore`.
4. Add a `TaskContract` if the model targets a new task_kind.

### 7.4 Adding a New Task Kind (for routing)

1. Add entry to `_TASK_MODEL_PREFERENCES` in `gateway.py:1173`.
2. Add keyword regex mapping to `_TASK_KEYWORD_MAP` in `recommender.py:54`.
3. Add a `TaskContract` to `DEFAULT_TASK_CONTRACTS` in `small_model_policy.py:120`.
4. Optionally add capability data to `_MODEL_CAPABILITIES` in `model_scoring.py:132`.

### 7.5 Adding a New Scoring Dimension

- **Cloud models**: extend `ModelScore` fields, update `_MODEL_CAPABILITIES` with new metrics, modify `_compute_score()` weights.
- **Small models**: extend `ModelRecommendation` fields, update `_compute_score()` weights, add new radar dimension to `ModelRadarProfile`.

### 7.6 Custom Budget Routing

Wire a `CostAwareRouter` into the `ModelGateway` with a custom `PeakPricingSchedule` and optional `budget_guard`/`cost_tracker`/`deferred_queue`. The router automatically adjusts model selection by peak/off-peak multipliers, defers tasks when waiting saves ≥20% of peak cost, and enforces hard budget limits.
