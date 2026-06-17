# Model Routing Prior Art: Purpose/Task-Based Model Selection in Agentic Coding Systems

**Produced:** 2026-06-16
**Scope:** Research for gludd's `AdaptiveRouter` (`src/general_ludd/scoring/router.py`) and a forthcoming model-weights DB. Covers OpenHands, Devin/Cognition, LiteLLM Router, RouteLLM, NotDiamond, Aider, OpenRouter, and related research. Distinguishes stated-fact (cited) from inference.

---

## 1. OpenHands / All-Hands-AI

### 1.1 Single-model default; named configs for multi-model

By default OpenHands connects to one primary LLM configured through the UI or `config.toml`. However, the **Custom LLM Configurations** system ([docs](https://docs.openhands.dev/modules/usage/llms/custom-llm-configs)) allows multiple named configs in TOML that different subsystems reference by name:

```toml
[llm]                        # default / main agent model
model = "claude-sonnet-4-5"
api_key = "..."

[llm.haiku]                  # cheap named config
model = "claude-haiku-4-5"
api_key = "..."

[llm.draft_editor]           # reserved name: code editing/refinement
model = "claude-haiku-4-5"
```

Configurable fields per named config: `model`, `api_key`, `base_url`, `temperature`, `top_p`, `presence_penalty`, `frequency_penalty`, `num_retries`, `retry_multiplier`, `max_input_tokens`, `max_output_tokens`. **[Stated]**

### 1.2 Reserved config: `draft_editor`

The `[llm.draft_editor]` section is a **purpose-reserved** config for "preliminary drafting of code edits." This is a distinct model role: the main agent reasons about what to change; the draft_editor model handles code-edit formatting and refinement. **[Stated, docs.openhands.dev/modules/usage/llms/custom-llm-configs]**

### 1.3 Per-agent model override

Agents can specify their own model config via:

```toml
[agent.MySpecialAgent]
llm_config = 'haiku'
```

This allows a sub-agent to run on a cheaper/faster model than the orchestrator. **[Stated, same doc]**

Custom configurations are only available in development mode (via `main.py` or `cli.py`), not through the hosted UI. **[Stated]**

### 1.4 Condenser model (separate from main LLM)

The history condenser/summarizer system uses its own LLM config. In `config.template.toml` ([GitHub](https://github.com/OpenHands/OpenHands/blob/main/config.template.toml)):

```toml
[condenser]
type = "llm"
llm_config = "haiku"     # reference a named [llm.*] config

[llm.condenser]          # or define inline
model = "claude-haiku-4-5"
temperature = 0.0
max_input_tokens = 8192
```

Condenser types include: `noop`, `observation_masking`, `recent`, `llm`, `amortized`, `llm_attention`. The `llm` and `llm_attention` types accept a `llm_config` field pointing to any named LLM. **[Stated, config.template.toml]**

A [PR #6597](https://github.com/OpenHands/OpenHands/pull/6597) ("Improve performance of LLM summarizing condenser") and [PR #5306](https://github.com/OpenHands/OpenHands/pull/5306) ("Condenser Interface and Defaults") establish this as an intentional architecture: the condenser is explicitly designed to run on a **different (typically cheaper) model** than the main agent.

### 1.5 SDK-level routing (stated in SDK paper)

The [OpenHands SDK paper (arXiv 2511.03690)](https://arxiv.org/pdf/2511.03690) states the SDK includes `MultimodalRouter` (routes by image presence and token limits) and `RandomRouter`. These are code-path routing helpers, not purpose-based task routers. **[Stated]**

### 1.6 What is NOT present in OpenHands

No automatic routing by task type or difficulty. No runtime model-switching based on prompt classification. The user/developer manually assigns models to roles via config. **[Stated, from absence in docs and config template]**

---

## 2. Devin / Cognition AI

Cognition is closed-source. The following is drawn from their public blog ([cognition.ai/blog](https://cognition.ai/blog)) and third-party reporting.

### 2.1 What is stated

- Devin uses "long-term reasoning and planning" to handle "tasks requiring thousands of decisions." **[Stated, cognition.ai/blog/introducing-devin]**
- Cognition has stated publicly that **"smaller specialized models can match or beat frontier generalists on the tasks they're trained for"** and that they use RL-trained specialized models like `SWE-check` (bug detection), which "matches Opus 4.6 while running ~10x faster." **[Stated, Cognition blog, reported 2024-2025]**
- Devin has an "Interactive Planning" phase (plan approval before execution), suggesting a planning/execution separation at the workflow level. **[Stated, cognition.ai]**
- A Cognition engineering post on multi-agent systems notes that "a narrower class of multi-agent systems works where agents contribute intelligence while writes stay single-threaded." **[Stated, Cognition blog]**

### 2.2 Inference (not stated)

- Given the SWE-check disclosure, it is **reasonable to infer** that Devin routes different pipeline stages (e.g., bug detection, code generation, review) to different specialized models — but this architecture is not publicly documented.
- The planning/execution split (Interactive Planning mode) likely uses different inference configurations for the planning pass vs. the execution loop, but this is not confirmed.

### 2.3 What is NOT stated

No public documentation of Devin's internal model routing table, model tier assignments, or task-category-to-model mapping.

---

## 3. LiteLLM Router

LiteLLM Router ([docs.litellm.ai/docs/routing](https://docs.litellm.ai/docs/routing)) is a production load-balancer and router for LLM APIs. OpenHands uses it as its LLM call layer.

### 3.1 Routing strategies

| Strategy | Signal | Description |
|---|---|---|
| `simple-shuffle` (default) | RPM/TPM limits or weights | Random selection within limits; minimal overhead |
| `latency-based-routing` | Historical response time | Routes to lowest-latency deployment (cached TTL) |
| `cost-based-routing` | Token pricing per deployment | Routes to cheapest deployment meeting rate limits |
| `usage-based-routing` | Current TPM usage | Routes to deployment with lowest current TPM |
| `least-busy` | Concurrent call count | Routes to deployment handling fewest active requests |
| Custom (`CustomRoutingStrategyBase`) | Any | Extensible via `async_get_available_deployment()` |

**[Stated, docs.litellm.ai/docs/routing]**

### 3.2 Tag-based routing (purpose routing)

Tags enable **semantic / purpose-based routing** ([docs.litellm.ai/docs/proxy/tag_routing](https://docs.litellm.ai/docs/proxy/tag_routing)):

```yaml
model_list:
  - model_name: gpt-4
    litellm_params:
      model: openai/gpt-4
    tags: ["planning", "premium"]

  - model_name: gpt-4
    litellm_params:
      model: openai/gpt-3.5-turbo
    tags: ["editing", "cheap"]

router_settings:
  enable_tag_filtering: true
```

Requests include `"tags": ["planning"]` in the JSON body (or `x-litellm-tags` header). A `["default"]` tag on a model catches untagged requests. The system also supports `tag_regex` matching against request headers (e.g., `User-Agent`) for automatic classification without client-supplied tags. **[Stated]**

Tag-based routing is the closest LiteLLM analogue to gludd's `TaskType`→model mapping. In LiteLLM, tags are statically assigned at config time; in gludd's design, the `AdaptiveRouter` could dynamically assign tags from benchmark-learned `TaskType` preferences.

### 3.3 Routing groups (per-group strategies)

Different model groups can use different routing strategies simultaneously ([docs.litellm.ai/docs/proxy/ui/routing_groups](https://docs.litellm.ai/docs/proxy/ui/routing_groups)): e.g., `latency-based` for gpt-4o group, `simple-shuffle` for cheaper models. Each model name belongs to at most one routing group. **[Stated]**

### 3.4 Fallbacks

Priority ordering via `order` values: a failed `order=1` deployment falls back to `order=2`. With `enable_weighted_failover=True`, failed deployments are excluded and weights renormalized across remaining healthy peers before cross-group escalation. **[Stated, docs.litellm.ai/docs/routing]**

---

## 4. RouteLLM (LMSYS)

**Paper:** [arXiv:2406.18665](https://arxiv.org/abs/2406.18665)
**Blog:** [lmsys.org/blog/2024-07-01-routellm/](https://www.lmsys.org/blog/2024-07-01-routellm/)
**Code:** [github.com/lm-sys/routellm](https://github.com/lm-sys/routellm)

### 4.1 Core concept

Every request carries an implicit **difficulty**. A lightweight router estimates that difficulty and dispatches to a strong (expensive) or weak (cheap) model accordingly. The framework is trained on Chatbot Arena human preference data. **[Stated]**

### 4.2 Router models

Four router architectures, each learning to predict which of two models will be preferred:

1. **Similarity-weighted (SW) ranking** — weighted Elo based on query similarity to training examples
2. **Matrix factorization** — learns scoring functions for model-prompt compatibility (best performer)
3. **BERT classifier** — fine-tuned to predict which model wins
4. **Causal LLM classifier** — LLM-based prediction

**[Stated]**

### 4.3 Training signals

Trained on preference data (one model preferred over another per query). Two augmentation strategies:
- LLM judge augmentation (significantly improved MT Bench results)
- Golden-label benchmark data (MMLU validation splits; "<2% of overall training data")

Routers generalize to **unseen model pairs** without retraining. **[Stated]**

### 4.4 Benchmarks

Routing between GPT-4 Turbo (strong) and Mixtral 8x7B (weak):

| Benchmark | Cost reduction | Quality retained |
|---|---|---|
| MT Bench | 85% | 95% of GPT-4 quality |
| MMLU | 45% | 95% |
| GSM8K | 35% | 95% |

Matrix factorization achieved best results, requiring only 14% of total calls on augmented MT Bench data. Outperformed commercial routers (Martian, Unify AI) by >40% on costs at matched quality. **[Stated]**

---

## 5. NotDiamond

**Docs:** [docs.notdiamond.ai](https://docs.notdiamond.ai/docs/key-concepts)

### 5.1 Routing mechanism

NotDiamond analyzes each query and predicts which model from a candidate set will provide the highest quality response at the lowest cost. Uses **Pareto optimization** across cost, latency, and quality axes. **[Stated]**

Routing signal: described broadly as "prompt complexity, task type, and model capabilities" — the internal methodology is not publicly documented. **[Partially stated]**

### 5.2 Router types

- **Pre-trained router**: general-purpose, ready to use out of the box
- **Custom router**: trained on user-supplied evaluation data for domain-specific optimization

**[Stated]**

### 5.3 Cost/quality tradeoff

`cost_quality_tradeoff` parameter (0–10 scale). At intermediate values "a cheap mid-rank model can outrank an expensive top-rank model." Three preset modes: cost, latency, quality. **[Stated]**

### 5.4 OpenRouter integration

OpenRouter's `openrouter/auto` endpoint ([openrouter.ai/docs/guides/routing/routers/auto-router](https://openrouter.ai/docs/guides/routing/routers/auto-router)) is powered by NotDiamond. The Auto Router:
- Selects one model per prompt from a curated pool (Claude, GPT, Gemini, DeepSeek, etc.)
- Pins the selected model for the rest of the conversation for cache coherence
- Returns the selected model name in the `response.model` field
- Can be restricted to a model subset via `plugins` parameter
- No additional routing fee; user pays standard rate for the selected model

**[Stated]**

---

## 6. Aider: Architect/Editor Two-Model Split

**Docs:** [aider.chat/docs/usage/modes.html](https://aider.chat/docs/usage/modes.html)
**Design post:** [aider.chat/2024/09/26/architect.html](https://aider.chat/2024/09/26/architect.html)

### 6.1 Architecture

Aider's architect mode uses two sequential inference passes:

1. **Architect model**: Reasons about the coding problem; produces a natural-language description of the solution. No formatting constraints. Optimized for reasoning.
2. **Editor model**: Converts the architect's solution into properly formatted file-edit instructions. Optimized for formatting precision, not reasoning.

"The Architect/Editor approach allows the Architect to focus on solving the coding problem and describe the solution however comes naturally to it, while the Editor can focus all of its attention on properly formatting the edits without needing to reason much." **[Stated]**

### 6.2 Configuration

- Architect: `--model` or `--architect` flag
- Editor: `--editor-model` flag; or Aider selects a default editor model based on the architect model
- Presets: `aider --sonnet --architect`, `aider --o1-preview --architect`

**[Stated]**

### 6.3 Benchmark results

| Architect | Editor | Pass Rate |
|---|---|---|
| o1-preview | o1-mini or DeepSeek | 85.0% (SOTA at time) |
| o1-preview | Claude 3.5 Sonnet | 82.7% |
| Claude 3.5 Sonnet | Claude 3.5 Sonnet | 80.5% |

The two-pass design measurably improves quality on real-world refactors and often reduces cost vs. running a single frontier model end-to-end. **[Stated]**

### 6.4 Design rationale

Motivated by OpenAI o1 excelling at reasoning but struggling with formatted output requirements. Separating concerns lets each model specialize. This is the clearest published example of **purpose-based model routing at the task-step level** in a coding agent. **[Stated]**

---

## 7. Martian

**GitHub:** [github.com/martianprotocol/martianrouter](https://github.com/martianprotocol/martianrouter)

Martian routes each query to the cheapest LLM likely to produce a response of sufficient quality, given a user-specified cost ceiling or model list. Accepts a list of candidate models and a maximum spend per query. **[Stated]**

**Caveat:** As of November 2024, independent research found that Martian's router "appears to ignore the list of models provided by the user, and forwards the input to the same LLM regardless." It was excluded from recent comparison benchmarks due to non-functional model selection. **[Stated, research paper]** Status as of 2026 is unverified.

---

## 8. Claude Code Model Tiers

Claude Code (Anthropic's CLI agent) routes tasks across three model tiers. This is primarily a cost/capability segmentation, not a task-type-based router, but the stated heuristics map onto purpose:

| Tier | Model | Use case | Approximate cost (per MTok, input/output) |
|---|---|---|---|
| Haiku | claude-haiku-4-5 | High-volume, classification, routing, front-line tool calls, simple tasks | ~$1/$5 |
| Sonnet | claude-sonnet-4-5 | Default workhorse: code, tool use, interactive tasks | ~$3/$15 |
| Opus | claude-opus-4-6 | Heavy reasoning, long-horizon agent behavior | ~$5/$25 |

"Claude Code's smart model switching automatically routed simple tasks to Haiku while reserving Sonnet and Opus for heavier work." **[Stated, multiple secondary sources citing Anthropic docs]**

The routing signal is **task complexity / interaction cost**, not an explicit task-type taxonomy. **[Inference from available docs; Anthropic has not published the internal routing logic]**

---

## 9. Comparison Table: Routing Signals Across Systems

| System | Primary routing signal | Secondary signals | Static vs. dynamic | Open/closed |
|---|---|---|---|---|
| OpenHands | Manual assignment by role (config) | None (no auto-routing) | Static (config-time) | Open |
| Aider architect/editor | Task step (reason vs. format) | Model capability defaults | Static (flag) | Open |
| LiteLLM tag routing | Request tag (purpose label) | Regex on headers | Static labels, dynamic dispatch | Open |
| LiteLLM latency/cost | Observed latency / token cost | Rate limits, health | Dynamic | Open |
| RouteLLM | Prompt difficulty (preference-learned) | None | Dynamic (learned) | Open |
| NotDiamond | Prompt complexity + task type (opaque) | Cost/latency tradeoff param | Dynamic (ML) | Closed |
| OpenRouter Auto | Prompt analysis (NotDiamond) | Conversation coherence | Dynamic (ML) | Closed |
| Martian | Cost ceiling vs. quality model | Model list | Dynamic (claimed) | Closed (non-functional as of 2024) |
| Claude Code | Task complexity / interaction cost | None explicit | Dynamic (heuristic) | Closed |
| Cognition Devin | Specialized model per skill domain (SWE-check) | Inferred planning vs. execution split | Inferred dynamic | Closed |
| **gludd AdaptiveRouter** | **Historical benchmark score per TaskType** | **Cost cap, quantization penalty, health** | **Dynamic (data-driven)** | Open |

---

## 10. Synthesis for Gludd

### 10.1 Current gludd state (`src/general_ludd/scoring/router.py`)

`AdaptiveRouter` already implements a data-driven quality+cost router:
- Routing key: `TaskType` enum (10 values: `bug_fix`, `feature`, `refactor`, `test_write`, `code_review`, `documentation`, `debugging`, `optimization`, `security_fix`, `integration`)
- Composite score: `completion` (0.35) + `code_quality` (0.25) + `instruction_adherence` (0.25) + `token_efficiency` (0.15)
- Cost constraint: `max_cost_usd` with fail-closed semantics (no over-cap model is ever returned)
- Health gating: `HealthTracker.is_healthy()` filters candidates pre-selection
- Quantization penalty: confidence < 0.5 → ×0.6 score; < 0.7 → ×0.8
- Fallback reason codes: `best_historical_score`, `cost_constrained`, `cost_cap_no_fit`, `insufficient_historical_data`

### 10.2 What gludd should adopt from prior art

#### A. Aider's architect/editor split → gludd planner/compactor role model

**Adopt:** Introduce `TaskRole` alongside `TaskType`. At minimum two roles:
- `PLANNER` — reasoning-heavy: solution design, diagnosis, architectural decisions. Maps to high-quality (Opus/Sonnet) models.
- `EDITOR` / `FORMATTER` — formatting-heavy: patch generation, structured output, template fill. Maps to cheaper fast models (Haiku, flash).

In gludd terms, the `draft_editor` slot from OpenHands is directly applicable. `AdaptiveRouter.route()` could accept an optional `task_role: TaskRole` parameter that biases the model selection even before historical data accumulates. The benchmark scores would then be stored per `(TaskType, TaskRole)` pair, not just `TaskType`.

**Concrete change:** Add `TaskRole` to `BenchmarkResult` and to the `route()` signature. The router's composite score lookup becomes `get_aggregate_scores(task_type=..., task_role=...)`. Cold-start defaults: `PLANNER → sonnet`, `EDITOR → haiku`.

#### B. OpenHands condenser/summarizer model → gludd compactor role

**Adopt:** The compactor (context compression, history summarization) should run on a **separate, cheaper model** than the main task model. OpenHands proves this pattern is production-viable (PR #6597 improved summarization quality specifically by tuning the condenser's LLM config independently).

In gludd, `AdaptiveRouter` should support a `TaskRole.COMPACTOR` that maps to a fast/cheap model (Haiku or equivalent). Benchmark it separately since compaction quality metrics differ from code-task metrics (summary fidelity, compression ratio, not code correctness).

#### C. LiteLLM tag routing → model-weights DB `task_category → best_for`

**Adopt:** LiteLLM's tag system is the static precursor to gludd's data-driven routing. The model-weights DB (which a sibling agent is building with `best_for` fields) maps directly to LiteLLM's `tags: ["planning", "cheap"]` pattern — but gludd's version is learned from benchmarks rather than hardcoded.

**Concrete use:** Use the model-weights DB as the **cold-start prior** when `AdaptiveRouter` has fewer than `min_samples` observations. Currently the router returns `fallback=True, reason="insufficient_historical_data"` and uses a generic default. Replace that fallback with a model-weights DB lookup: `model_weights.get_best_for(task_type)` → returns the a priori recommended model for that `TaskType`.

This mirrors how LiteLLM's tag routing works — the operator declares which models are good for which purpose — but gludd will eventually graduate those static declarations to data-learned routing once enough benchmark data exists.

#### D. RouteLLM difficulty escalation → gludd quality-tier escalation

**Adopt:** RouteLLM shows that ~85% cost reduction is achievable by routing "easy" prompts to cheap models. Gludd can implement this without ML training by using the `composite_score` as a difficulty proxy in reverse: if the best historical model for a `TaskType` achieves very high `composite_score` even with a cheap model, allow routing that task to the cheap model regardless of the `task_role` prior.

More directly: add a **quality-floor** parameter to `route()`. If `max_cost_usd` is not set but a quality floor is (e.g., `min_quality=0.85`), the router should select the cheapest model whose historical `composite_score >= min_quality` — this is RouteLLM's core insight implemented over gludd's benchmark DB rather than a trained classifier.

#### E. NotDiamond/OpenRouter `cost_quality_tradeoff` → gludd `cost_weight` / `quality_weight` tuning

`AdaptiveRouter` already has `cost_weight=0.2, quality_weight=0.8` constructor params. The model-weights DB should expose these as per-`TaskType` recommendations rather than a single global setting, since quality matters more for `security_fix` than for `documentation`. NotDiamond's `cost_quality_tradeoff` (0–10 scale) is essentially this same knob; the difference is gludd has per-task data to set it empirically.

**Concrete change:** The model-weights DB entry for each `TaskType` should include recommended `(cost_weight, quality_weight)` values reflecting the task's sensitivity to quality errors. Example: `security_fix → (0.05, 0.95)`, `documentation → (0.4, 0.6)`.

#### F. LiteLLM fallback ordering → gludd ranked fallback list

Currently gludd falls back to a single `default_model_profile`. LiteLLM's `order=1,2,3` fallback chains suggest maintaining a **ranked fallback list** per `TaskType` in the model-weights DB. When the best candidate is over budget or unhealthy, try the second-best, then the third — not just the single global default.

This is especially relevant for `security_fix` and `code_review` where degrading to the generic default may be worse than a known-good second-choice model.

#### G. OpenHands per-agent model override → gludd per-role model config

The `[agent.AgentName] llm_config = 'haiku'` pattern in OpenHands is the most deployable pattern for gludd's near term. Before the benchmark DB is populated, gludd operators should be able to declare a static model-weights config (TOML or YAML) that pins models per `TaskType` or `TaskRole`. The `AdaptiveRouter` checks the DB first; if insufficient data, falls back to the static config; if no static config, uses the global default.

### 10.3 What gludd should NOT copy

- **RouteLLM's trained classifier approach**: requires labeled preference data gludd doesn't have. The benchmark DB approach is more appropriate — gludd generates its own ground truth through actual task execution, making RouteLLM's training data dependency unnecessary.
- **Martian's approach**: non-functional as of 2024 per independent testing; no stable design reference.
- **LiteLLM regex tag routing**: complex header-matching is unnecessary overhead; gludd's `TaskType` enum already gives explicit purpose labels at call time.
- **OpenRouter's conversation model pinning**: relevant for stateful chat sessions; gludd's task-based architecture is stateless per task, so conversation coherence constraints don't apply.

---

## Sources

- [OpenHands Custom LLM Configs](https://docs.openhands.dev/modules/usage/llms/custom-llm-configs)
- [OpenHands config.template.toml](https://github.com/OpenHands/OpenHands/blob/main/config.template.toml)
- [OpenHands LLM Settings](https://docs.openhands.dev/openhands/usage/settings/llm-settings)
- [OpenHands LLM Overview](https://docs.openhands.dev/openhands/usage/llms/llms)
- [OpenHands PR #6597: Improve LLM summarizing condenser](https://github.com/OpenHands/OpenHands/pull/6597)
- [OpenHands PR #5306: Condenser Interface and Defaults](https://github.com/OpenHands/OpenHands/pull/5306)
- [OpenHands SDK Paper (arXiv 2511.03690)](https://arxiv.org/pdf/2511.03690)
- [Cognition AI Blog](https://cognition.ai/blog)
- [Cognition: Introducing Devin](https://cognition.ai/blog/introducing-devin)
- [LiteLLM Router — Load Balancing](https://docs.litellm.ai/docs/routing)
- [LiteLLM Tag-Based Routing](https://docs.litellm.ai/docs/proxy/tag_routing)
- [LiteLLM Routing Groups](https://docs.litellm.ai/docs/proxy/ui/routing_groups)
- [LiteLLM Tag Management Tutorial](https://docs.litellm.ai/docs/tutorials/tag_management)
- [RouteLLM LMSYS Blog Post](https://www.lmsys.org/blog/2024-07-01-routellm/)
- [RouteLLM Paper (arXiv:2406.18665)](https://arxiv.org/abs/2406.18665)
- [RouteLLM GitHub](https://github.com/lm-sys/routellm)
- [NotDiamond Key Concepts](https://docs.notdiamond.ai/docs/key-concepts)
- [NotDiamond What is Model Routing](https://docs.notdiamond.ai/docs/what-is-model-routing)
- [NotDiamond Custom Routing (W&B)](https://docs.wandb.ai/weave/cookbooks/notdiamond_custom_routing)
- [NotDiamond Awesome AI Model Routing](https://github.com/Not-Diamond/awesome-ai-model-routing)
- [OpenRouter Auto Router Docs](https://openrouter.ai/docs/guides/routing/routers/auto-router)
- [OpenRouter Auto Router Blog](https://openrouter.ai/announcements/happy-new-year-introducing-a-new-auto-router)
- [OpenRouter Model Routing Insights](https://openrouter.ai/blog/insights/model-routing/)
- [Aider Chat Modes (architect/editor)](https://aider.chat/docs/usage/modes.html)
- [Aider: Separating code reasoning and editing](https://aider.chat/2024/09/26/architect.html)
- [Martian Protocol Router GitHub](https://github.com/martianprotocol/martianrouter)
- [Rerouting LLM Routers (COLM 2025)](https://openreview.net/pdf?id=U6C7odo5SX)
- [Dynamic Model Routing Survey (arXiv:2603.04445)](https://arxiv.org/pdf/2603.04445)
- [LLMRouterBench (arXiv:2601.07206)](https://arxiv.org/pdf/2601.07206)
- [DeepWiki: OpenHands LLM Configuration](https://deepwiki.com/All-Hands-AI/OpenHands/5.1-llm-configuration-and-provider-support)
- [Anyscale: Building an LLM Router](https://www.anyscale.com/blog/building-an-llm-router-for-high-quality-and-cost-effective-responses)
