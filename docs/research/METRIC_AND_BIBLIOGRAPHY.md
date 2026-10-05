# Scoring Metrics & Bibliography — Model Routing in gludd

**Date:** 2026-07-13
**Scope:** Documents the scoring formulas, composite weights, routing decision flow, and academic/industry underpinnings of the `AdaptiveRouter` and `ParetoRouter` subsystems (`src/general_ludd/scoring/`).

---

## 1. The W$ Formula — Worth Per Dollar

The core cost-efficiency metric used to evaluate model candidates:

```text
W$ = W / log10(1 + median_$/Mtok)
```

**Where:**

| Term | Meaning |
|---|---|
| `W` | Raw worth (quality) score — typically the composite benchmark score on [0,1]. Captures how well the model solves tasks of this type. |
| `median_$/Mtok` | Median cost in US dollars per million tokens across input + output. Derived from the model's pricing profile (`cost_per_input_token`, `cost_per_output_token`). Weighted toward the expected input/output split for the task type. |
| `log10(1 + ...)` | Logarithmic cost penalty — compresses extreme cost differences. A model 10× more expensive is penalised ~2× (not 10×), reflecting diminishing marginal utility of cost savings above a baseline. The `1 +` shift ensures the denominator is ≥ 0.30 even for free models. |
| `W$` | Worth-per-dollar — higher is better. Normalised to [0, ~3.3] (a perfect-quality free model scores ~3.3). |

**Design rationale:** Linear cost-weighting (`W − k·cost`) over-penalises expensive models, pushing the router toward the cheapest option even when quality differences are large. The log-denominator preserves separation in the quality dimension while still preferring cheaper models among quality-equivalent candidates (the "prefer the cheapest quality-EQUIVALENT candidate" principle in `_select_cheapest_equivalent`, `router.py:605`).

---

## 2. Composite Score — Sub-Score Aggregation

Two composite score variants exist in the codebase:

### 2.1 DB Aggregate (`repository.py:1000–1005`)

Computed via SQL `AVG()` over successful benchmark results. Weights reflect the relative importance of each dimension for agentic coding tasks:

| Sub-Score | Weight | Rationale |
|---|---|---|
| `completion_score` | **0.40** | Did the model produce a working solution? Most important axis. |
| `code_quality_score` | **0.30** | Is the output well-structured, idiomatic, testable? |
| `instruction_adherence_score` | **0.20** | Did the model follow the prompt's constraints? |
| `token_efficiency_score` | **0.10** | Did it avoid wasted output tokens (hallucinated boilerplate, circular edits)? |

```text
composite = completion × 0.4 + quality × 0.3 + instruction × 0.2 + efficiency × 0.1
```

### 2.2 Pydantic Property (`schemas/benchmark.py:60–73`)

The `BenchmarkScores` model exposes a `composite_score` property with slightly different weights, used at collection time (pre-persistence):

```text
composite = completion × 0.35 + quality × 0.25 + instruction × 0.25 + efficiency × 0.15
```

The Pydantic variant gives marginally more weight to instruction adherence and token efficiency, under-weighting raw completion relative to the DB aggregate. Both variants are normalised to [0, 1].

---

## 3. Per-Task Role Weights — Cost vs. Quality

The `RoleWeights` table in `src/general_ludd/routing_roles/weights.py` defines (cost, quality) pairs per `TaskType`. These feed into the `_cost_adjusted_rank` method and determine how aggressively the router trades cost for quality on each task category:

| Task Type | Cost Weight | Quality Weight | Routing Philosophy |
|---|---|---|---|
| `SECURITY_FIX` | 0.05 | 0.95 | Quality-critical; never sacrifice correctness for savings. |
| `BUG_FIX`, `DEBUGGING`, `CODE_REVIEW` | 0.15 | 0.85 | Strong quality bias; tolerate minor cost increases. |
| `FEATURE`, `TEST_WRITE`, `INTEGRATION` | 0.20 | 0.80 | Balanced with quality preference (default). |
| `OPTIMIZATION`, `REFACTOR` | 0.25 | 0.75 | Moderate cost sensitivity; cheaper models often adequate. |
| `DOCUMENTATION` | 0.40 | 0.60 | Cost-sensitive; documentation benefits less from frontier models. |

All pairs sum to 1.0 (enforced by an assertion in `weights.py:41–43`). Unknown task types default to `(0.20, 0.80)`.

---

## 4. Routing Decision Flow

### 4.1 AdaptiveRouter (`router.py`)

The `AdaptiveRouter.route()` method executes these stages:

1. **Cache check** — returns cached decision if within TTL (300s default) and selected model is healthy.

2. **Historical lookup** (`_get_best_from_history`) — queries aggregated benchmark scores for the task type. Candidates are filtered by:
   - Minimum sample count (default ≥ 3 runs).
   - Model health (`HealthTracker.is_healthy()`).
   - Quantisation penalty — confidence < 0.5 → score × 0.6; confidence < 0.7 → score × 0.8.

3. **Cross-type bootstrapping** (when `TaskEmbeddingStore` is configured) — neighbours the query task against stored embeddings via cosine similarity. Candidates from similar task types borrow evidence weighted by `similarity_floor + similarity_alpha × similarity` (default: alpha=1.0, floor=0.0).

4. **Cross-project borrowing** (when `enable_cross_project_borrowing=True` and own history is below `min_samples`) — BFS-traverses the declared project graph (max depth 3). Borrowed candidates are weighted by `_composite_similarity_weight` = task_similarity × project_relationship_weight, where project weight = `REL_BASE[relation] × edge_decay^(distance−1) × control_factor`.

5. **Pareto filter** (`_apply_pareto_filter`) — when a `ParetoRouter` is configured, non-dominated candidates are selected: a candidate is dominated if another has both lower cost and higher quality.

6. **Ranking** — each survivor is scored by:
   ```text
   rank = weights.quality × quality − weights.cost × (cost / max_cost)
   ```
   where `weights` comes from `weights_for(task_type)`. Cost is normalised to [0,1] within the candidate set.

7. **Cheapest-equivalent tie-break** (`_select_cheapest_equivalent`) — among candidates whose effective quality is within `adequacy_margin` (default 0.02) of the best quality, the cheapest wins. Reason: `"cheaper_equivalent"`.

8. **Cost-cap enforcement** — if the winner exceeds `max_cost_usd`, the router finds the cheapest candidate under cap. If none fits, it falls back to the safe default model (reason: `"cost_cap_no_fit"`).

9. **Cache and return**.

### 4.2 ParetoRouter (`pareto.py`)

The `ParetoRouter.route_by_pareto_frontier()` operates independently:

1. Filters out NaN/Inf candidates.
2. Identifies the Pareto frontier — candidates where no other has strictly lower cost AND strictly higher quality.
3. Returns frontier candidates in quality-descending order.
4. `pick_winner()` normalises cost and quality to [0,1] within the frontier and scores each as:
   ```text
   score = quality_norm × quality_weight − cost_norm × cost_weight
   ```
   (Default: `cost_weight=0.5`, `quality_weight=0.5`).

---

## 5. Trade-Offs Between Cost and Quality

### 5.1 The Adequacy Band

The `adequacy_margin` parameter (`router.py:81`) is the primary cost–quality trade-off mechanism. Set to 0.02 by default, it defines a narrow quality band around the best candidate. Within this band, the router picks the cheapest — effectively saying: "any model within 2% of the best quality is adequate; prefer the cheapest among them."

| Margin | Behaviour |
|---|---|
| `0.0` (disabled) | Pure quality-maximisation; cost is irrelevant in winner selection. |
| `0.02` (default) | Narrow band — cost savings captured only when quality is near-best. |
| `0.05+` (wide) | Aggressive cost-bias — cheaper models chosen even with moderate quality gaps. |

### 5.2 Task-Type Sensitivity

Per-task weights (Section 3) encode the project's stance on which tasks justify premium models. Security fixes at (0.05, 0.95) will almost always route to the highest-quality model regardless of cost; documentation at (0.40, 0.60) will route to cheaper models unless quality gaps are extreme.

### 5.3 The Log-Denominator in W$

See Section 1. A linear cost penalty (e.g. `W − k·cost`) would collapse toward the cheapest model on a 10× cost spread. The logarithmic penalty preserves quality differentiation while still preferring cheaper models among near-equals.

### 5.4 Pareto Frontier as a Pre-Filter

The Pareto filter eliminates strictly dominated candidates before ranking, so a model that is both more expensive and lower quality than another can never win — even if per-task weights are cost-tolerant. This guarantees the router never picks a strictly inferior option.

### 5.5 Cross-Project Borrowing Risk

When own-project history is thin (< `min_samples`), borrowed candidates enter the pool with diluted weights. The `external_penalty` (default 0.5) and `edge_decay` (default 0.5) ensure that evidence from a distant uncontrolled project is heavily discounted, preventing low-quality borrows from displacing true own-project bests.

---

## 6. Citations & References

### 6.1 RouteLLM — Cost-Efficient LLM Routing

Ong, I., Almahairi, A., Wu, V., Chiang, W.-L., Wu, T., Gonzalez, J. E., Kadous, M. W., & Stoica, I. (2024). **RouteLLM: An Open-Source Framework for Cost-Effective LLM Routing.** *arXiv preprint arXiv:2406.18665*.

RouteLLM frames model routing as a cost–quality trade-off: a lightweight router decides whether to send each query to a cheap model or an expensive one, using preference data from Chatbot Arena. Their matrix factorisation router learns embeddings for queries and models, selecting the model whose embedding best matches the query. gludd's `TaskEmbeddingStore` + cosine-similarity weighting in `_get_best_with_embeddings` follows the same principle — embedding similarity as a soft signal for model-task fit.

**Key takeaway for gludd:** Routing accuracy improves when router parameters are calibrated on real preference data rather than heuristic weights. The `BenchmarkResult` pipeline (`scoring/engine.py`) serves this role — live benchmark runs against actual gate outcomes provide the preference signal.

---

### 6.2 NotDiamond — Multi-Model Router with Preference Optimisation

NotDiamond (2024). **NotDiamond: The World's First Multi-Model LLM Router.** [notdiamond.ai](https://notdiamond.ai).

NotDiamond routes each prompt to the best-fit model using a trained router that learns from pairwise preference judgments. It handles >20 models across Anthropic, OpenAI, Google, and Meta. The core insight gludd borrows: **task-type is the strongest routing signal**, and per-task quality scores (Section 3) are the training-data analogue — they encode observed performance on representative tasks, and the router generalises to new inputs via task similarity.

---

### 6.3 Pareto Optimality in Multi-Objective Optimisation

Marler, R. T., & Arora, J. S. (2004). **Survey of multi-objective optimization methods for engineering.** *Structural and Multidisciplinary Optimization*, 26(6), 369–395.

The ParetoRouter (`pareto.py`) applies canonical non-dominated sorting: candidate A dominates candidate B iff A is both cheaper AND higher-quality than B. The frontier is the set of non-dominated candidates. This is a standard technique in multi-criteria decision making (MCDM). The gludd implementation (`pareto.py:66–80`) uses O(n²) pairwise comparison, adequate for the typical candidate pool size (≤ 20 models).

---

### 6.4 Chatbot Arena & Elo-Based Model Ranking

Chiang, W.-L., Zheng, L., Sheng, Y., Angelopoulos, A. N., Li, T., Li, D., Zhang, H., Zhu, B., Jordan, M., Gonzalez, J. E., & Stoica, I. (2024). **Chatbot Arena: An Open Platform for Evaluating LLMs by Human Preference.** *arXiv preprint arXiv:2403.04132*.

Chatbot Arena introduced Bradley-Terry / Elo-based ranking for LLMs from pairwise human preference judgments. gludd's benchmark pipeline replaces human preference with automated gate outcomes (tests pass/fail, lint pass/fail, typecheck pass/fail), but the conceptual structure is identical: repeated pairwise comparisons → aggregate score → routing decision. The composite score (Section 2) is gludd's Elo analogue — a scalar summary of multi-dimensional performance.

---

### 6.5 LiteLLM Router — Production Model Routing

BerriAI (2024). **LiteLLM — Call 100+ LLMs using the OpenAI format.** [github.com/BerriAI/litellm](https://github.com/BerriAI/litellm).

LiteLLM's router supports cost-based, latency-based, and least-busy routing strategies across providers. Its cost-tracking infrastructure and per-model pricing profiles are the operational layer that gludd's `cost_per_input_token` / `cost_per_output_token` fields (in `ModelProfile`, `gateway.py:186`) mirror. LiteLLM's "budget" concept is analogous to gludd's `max_cost_usd` cap in `AdaptiveRouter.route()`.

---

## 7. Summary of Weight Stack

| Layer | Where Defined | Cost Weight | Quality Weight | Notes |
|---|---|---|---|---|
| **Composite score** | `repository.py:1000` | — | — | Weighted avg of 4 sub-scores; not a cost/quality split. |
| **AdaptiveRouter defaults** | `router.py:38–39` | 0.20 | 0.80 | Constructor defaults; overridden at ranking time by per-task weights. |
| **Per-task role weights** | `routing_roles/weights.py:15–26` | 0.05–0.40 | 0.60–0.95 | Task-type-specific; used in `_cost_adjusted_rank`. |
| **ParetoRouter weights** | `pareto.py:21–25` | 0.50 | 0.50 | Equal weighting for frontier selection. |
| **Adequacy margin** | `router.py:81` | — | — | Quality band width for cheapest-equivalent tie-break (default 0.02). |
| **Quantisation penalty** | `router.py:678–683` | — | — | Confidence < 0.5 → ×0.6; < 0.7 → ×0.8. |
| **Cross-project decay** | `router.py:48–49` | — | — | `edge_decay=0.5`, `external_penalty=0.5`, `min_borrow_weight=0.05`. |
| **Similarity weighting** | `router.py:43` | — | — | `similarity_alpha=1.0`, `similarity_floor=0.0`. |
