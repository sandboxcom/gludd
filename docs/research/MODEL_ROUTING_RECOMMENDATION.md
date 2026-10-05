# Model Routing Recommendation — gludd Executive Summary

**Generated:** 2026-06-16
**Supersedes:** Reading 8 source docs individually.
**Sources synthesised:** `docs/research/compaction_models.md`, `enumeration_models.md`,
`model_routing_prior_art.md`, `model_weights/{debugging,enumeration_compaction,programming,research_reporting}.md`,
`docs/design/per_model_prompt_adapter.md`, `src/general_ludd/scoring/router.py`,
`src/general_ludd/schemas/benchmark.py`.

---

## 1. The Standard Weight Metric

One formula governs every routing decision in gludd. It is defined in
`src/general_ludd/schemas/benchmark.py::BenchmarkScores.composite_score`:

```text
composite = completion_score × 0.35
          + code_quality_score × 0.25
          + instruction_adherence_score × 0.25
          + token_efficiency_score × 0.15
```

This is the **only** composite score the runtime (`AdaptiveRouter`) ever reads or ranks on.
All model_weights research documents derive their benchmark-level `W` using a parallel but
separate formula (`W = relevance-weighted mean of normalised benchmark scores`) that feeds
the cold-start prior, not the runtime scorer. The two formulas serve different purposes:

| Formula | Where it lives | Purpose |
|---------|---------------|---------|
| `composite_score` (benchmark.py) | Runtime — stored in `BenchmarkResult`, ranked by `AdaptiveRouter` | Live empirical routing once tasks run |
| `W` / `W$` (model_weights docs) | Seed prior — operator config, cold-start defaults | Bootstraps routing before 3 samples exist |

The `W$` (cost-adjusted weight) used in research is:

```text
W$ = W / log10(1 + median_$/Mtok)
```

where `median_$/Mtok = (input_price + output_price) / 2` in USD per million tokens.
Higher `W$` = better quality per dollar. All source documents use this formula identically.
[Source: `enumeration_compaction.md` §Notation; `debugging.md` §Weight Derivation; `programming.md` §Methodology]

---

## 2. Recommended Initial Model per Task Category

Cold-start picks (before 3 benchmark samples accumulate). Every cell is the recommended
`model_profile_id` to wire in config. The "Top pick" maximises quality `W`; the "Cheap pick
(W$)" maximises value per dollar.

### 2.1 Debugging

| Sub-category | TaskType key | Top pick | W | Cheap pick (W$) | W$ | Confidence |
|---|---|---|---|---|---|---|
| General bug-fix | `bug_fix` | `claude_opus_48` | 0.946 | `deepseek_v3` | 1.330 | High |
| Security / vuln fix | `security_fix` | `gpt54` | 0.920 | `o3` | 0.905 | Medium |
| Performance / optimization | `optimization` | `gpt54` | 0.930 | `deepseek_v4_pro` | 1.566 | Medium |
| Root-cause from stacktrace | `debugging` | `claude_opus_48` | 0.934 | `deepseek_v3` | 1.265 | Medium |
| Concurrency / race | `debugging` | `gpt54` | 0.87 | `deepseek_r1` | 1.543 | LOW_CONF |
| Memory / leak | `debugging` | `gpt5_high` | 0.928 | `deepseek_v3` | 1.395 | LOW_CONF |

Notes from `debugging.md`:
- For security **patching** given a known CVE, Claude Sonnet 4.6 reaches 95.7% at full-info (ZeroDayBench). GPT-5.4 leads autonomous zero-day discovery at 14.4%.
- Claude Opus 4.8's 1M context is structurally advantageous for multi-service distributed traces.
- SWE-bench Verified scores overstate real capability by ~35 pp vs SWE-bench Pro (Scale). Use SWE-Pro as primary.

### 2.2 Enumeration / Structured Extraction

| Scenario | TaskType key | Top pick | W | Cheap pick (W$) | W$ | Confidence |
|---|---|---|---|---|---|---|
| High-quality extraction | `integration` (or add `extraction`) | `claude_opus_46` | 0.837 | `qwen3_235b` | 1.497 | High |
| Cost-sensitive extraction | same | `qwen3_32b` | 0.811 | `deepseek_v32` | 1.682 | Medium |
| Self-hosted + constrained decoding | same | `qwen3_7b_local` | — | free | — | Medium |
| Long-document (>128K) | same | `gemini_25_flash` | 0.782 | `gemini_25_flash` | 1.118 | High |

Notes from `enumeration_models.md` and `enumeration_compaction.md`:
- **Constrained decoding is a cross-cutting multiplier**: a model scoring W=0.668 unconstrained with XGrammar will match unconstrained W=0.80–0.85. Always enable native structured output (OpenAI `json_schema`, Anthropic `output_config`, Gemini `response_schema`, vLLM `guided_json`).
- LLMStructBench (arXiv 2602.14743): Gemma3-12B with schema enforcement outscored several unconstrained 70B models. Model size alone does not ensure schema compliance.
- Reliability stack: constrained decoding (structural) + Pydantic validate/repair (semantic) + 3-pass union voting (completeness).

### 2.3 Compaction

| Scenario | TaskType key | Top pick | W | Cheap pick (W$) | W$ | Confidence |
|---|---|---|---|---|---|---|
| Primary (hosted) | add `compaction` | `gemini_compactor` (2.5 Flash-Lite) | 0.916 | `gemini_compactor` | 1.692 | Medium |
| Highest faithfulness | same | `gemini_25_flash` | 0.940 | `gpt4o_mini_compactor` | 1.445 | Medium |
| Self-hosted / no API key | same | `qwen3_8b_local` | 0.808 | `qwen3_8b_local` | 1.543 | Medium |

Notes from `compaction_models.md` and `enumeration_compaction.md`:
- Gemini 2.5 Flash-Lite: $0.10/$0.40 per M tokens, 1M context — 3–10x cheaper than gludd's downstream models. This is the primary recommendation.
- Fallback: GPT-4o mini ($0.15/$0.60) — OpenAI-compatible, already supported by gateway.
- Break-even rule: compact when downstream is 3x+ more expensive than the compactor.
- Do NOT compact: exact error traces (pin instead), history < 5K tokens, downstream is already cheap.
- Faithfulness prompt invariant: "Preserve all function names, variable names, error messages, and numeric values verbatim. Do not infer or add information."

### 2.4 Programming by Language

Source: `programming.md` §4 (Model × Language Matrix). H/M/L = confidence level.

| Language | Top pick | W | Cheap pick (W$) | W$ | Confidence |
|---|---|---|---|---|---|
| Python | `claude_opus_48` | 0.97 | `deepseek_v3x` | high W$ | H |
| JavaScript | `claude_opus_4x` / `gpt5` | 0.81–0.82 | `deepseek_v3x` | high | M |
| TypeScript | `gpt5` / `claude_opus_4x` | 0.44–0.46 | `deepseek_v3x` | — | L† |
| Java | `claude_opus_4x` / `o3` | 0.59–0.60 | `deepseek_v3x` | — | M |
| Go | `claude_opus_4x` | 0.42 | `deepseek_r1` | 1.634 | M |
| Rust | `gpt5` / `gemini_25_pro` | 0.87–0.92 | `deepseek_v32` | 1.506 | M |
| C/C++ | `gpt5` | 0.97 | `deepseek_v32` | 1.472 | M |
| SQL | `o3` / `gemini_25_pro` | — | `deepseek_v3x` | — | L |
| Bash/Shell | `claude` / `gpt5` (est.) | 0.60–0.65 | `deepseek_v3x` | — | L |

†TypeScript: Multi-SWE-bench shows 2.2% vs 52.2% Python — this reflects difficult enterprise repos, not typical TS code-gen. All frontier models perform at ~60–80% of Python level on typical TS tasks.

**Key finding from `programming.md` §Methodology Note 4**: Opus→Sonnet→Haiku three-tier routing achieves 51% cost reduction vs uniform Opus on typical agent sessions. Sonnet 4.6 W$=0.86–1.00 typically exceeds Opus 4.8 W$=0.79–0.81 (AugmentCode empirical study).

### 2.5 Research

| Use case | Top pick | W | Cheap pick (W$) | W$ | Confidence |
|---|---|---|---|---|---|
| Multi-document synthesis / long papers | `gemini_25_pro` | 0.95 | `gemini_25_flash` | 2.49 | High |
| Science Q&A / GPQA-type (grad-level) | `claude_opus_46` | 0.84 | `o4_mini` | 1.66 | High |
| Factual recall / low-hallucination | `gemini_25_pro` | 0.95 (SimpleQA 55.6%) | `gemini_25_flash` | 2.49 | High |
| Hard math / STEM calculations | `o3` or `o4_mini` | 0.84 | `o4_mini` | 1.66 | High |
| Agentic web research (BrowseComp/GAIA) | `gemini_25_pro` + tools | 0.95 | `gemini_25_flash` | 2.49 | Low (framework-dependent) |

Notes from `research_reporting.md`:
- **Hallucination paradox**: o4-mini scores 92.7% AIME but 79% PersonQA hallucination rate. Never use o-series reasoning models for factual recall tasks without grounding. Use Gemini 2.5 Pro (SimpleQA F1 55.6% — best by far vs. Claude Opus 4 at 28.3% F1).
- o3 MRCR 128k = 61.4%; Gemini 2.5 Pro = 94.5%. For long-document retrieval, Gemini is structurally superior.

### 2.6 Reporting / Data Manipulation

| Use case | Top pick | W | Cheap pick (W$) | W$ | Confidence |
|---|---|---|---|---|---|
| Text-to-SQL / BIRD-class | `gemini_25_pro` (Gemini-SQL2 80.04% BIRD) | 0.97 | `gemini_25_flash` | 1.99 | High |
| Pandas / NumPy / data-science code | `gpt4o` or `o3` | 0.95 | `o4_mini` | 1.85 | Low (DS-1000 stale) |
| Report generation + table formatting | `gemini_25_pro` | 0.97 | `claude_sonnet_46` | 0.84 | High |
| Summarisation + low-hallucination | `gemini_25_pro` | 0.97 | `gemini_25_flash` | 1.99 | High |

---

## 3. Routing Architecture for gludd

### 3.1 The Three-Layer Stack

```text
Layer 1: model_weights DB (cold-start prior)
         ↓ feeds ↓
Layer 2: AdaptiveRouter learns from BenchmarkResult rows
         ↓ dispatches via ↓
Layer 3: ModelGateway with PromptAdapter + constrained_decoding in profile
```

**Layer 1 — Cold-start prior** (`model_weights` DB, not yet built in src):
Maps each `TaskType` to a ranked list of `model_profile_id` values with their research `W` and
`W$` scores (from the docs read above). When `AdaptiveRouter.route()` returns
`reason="insufficient_historical_data"` (fewer than `min_samples=3` runs), the gateway should
fall back to this lookup instead of the single global `default_model_profile`.
[Pattern from `model_routing_prior_art.md` §10.2C — LiteLLM tag routing as static prior]

**Layer 2 — AdaptiveRouter** (`src/general_ludd/scoring/router.py`):
Already implemented. Routes by `TaskType` StrEnum (10 values). Composite score =
`completion×0.35 + code_quality×0.25 + instruction×0.25 + token_efficiency×0.15`.
Fail-closed: over-cap or unhealthy candidates are excluded before selection. Will graduate
from cold-start to data-driven routing after 3 benchmark samples per TaskType per model.
[Code: `scoring/router.py`; schema: `schemas/benchmark.py`]

**Layer 3 — ModelGateway + PromptAdapter** (`src/general_ludd/models/`):
Existing gateway handles provider dispatch. The `per_model_prompt_adapter.md` design specifies
a new `prompt_adapter.py` module that intercepts every call between `call_model()` and the
wire, handling: system-prompt placement, tool-call wire format, stop sequences, reasoning model
parameters, multimodal transcoding. This layer is designed but not yet implemented.

### 3.2 TaskRole: Planner/Editor Split (Aider pattern)

Adopt `TaskRole` alongside `TaskType` — two dimensions for routing:

```python
class TaskRole(StrEnum):
    PLANNER   = "planner"    # reasoning-heavy: solution design, diagnosis, architecture
    EDITOR    = "editor"     # formatting-heavy: patch gen, structured output, template fill
    COMPACTOR = "compactor"  # context compression — separate cheap model
    ENUMERATOR = "enumerator"  # structured extraction — constrained small model
```

Cold-start defaults by role:
- `PLANNER` → `claude_opus_48` or `claude_sonnet_46` (quality priority)
- `EDITOR` → `claude_haiku_45` or `gpt4o_mini` (speed + formatting precision)
- `COMPACTOR` → `gemini_compactor` (2.5 Flash-Lite, $0.10/$0.40 per M)
- `ENUMERATOR` → `gpt41_nano` or `qwen3_32b` (constrained decoding required)

[Pattern from `model_routing_prior_art.md` §6 — Aider architect/editor split; §10.2A; OpenHands `draft_editor` config]

Implementation: add `TaskRole` to `BenchmarkResult` and the `route()` signature. The router's
composite score lookup becomes keyed on `(task_type, task_role)`. Until TaskRole is wired, the
role prior is a config-time operator declaration, not a runtime routing decision.

### 3.3 Compactor and Enumerator as Infrastructure Roles

Both roles bypass `AdaptiveRouter` initially (Option B from `compaction_models.md` §5d):

**Compactor**: call `gateway.call_model_with_fallback("gemini_compactor", ...)` directly in
`AgentCapabilities._make_summary_fn()`. The profile's `fallback_profiles: [gpt4o_mini_compactor]`
handles failover. Wiring point: `AgentCapabilities.prepare_messages()` currently calls
`self.compactor.compact(msgs)` with no `summary_fn`; add one.
[`compaction_models.md` §5c–5d]

**Enumerator**: call `gateway.call_model("enumerator", messages=extraction_messages)` with
constrained decoding parameters embedded in the profile's new `constrained_decoding` field.
The `output_schema` validate/repair module is the second defense layer (semantic errors after
structural compliance is guaranteed by constrained decoding).
[`enumeration_models.md` §6.3; `enumeration_compaction.md` §Constrained Decoding Effect]

Both roles should eventually get their own `TaskType` variants (`COMPACTION`, `EXTRACTION`)
so `AdaptiveRouter` can learn empirically once volume is sufficient.

### 3.4 Per-TaskType Cost/Quality Weights

`AdaptiveRouter.__init__` accepts `cost_weight=0.2, quality_weight=0.8` globally. Per-task
tuning (from `model_routing_prior_art.md` §10.2E):

| TaskType | Recommended cost_weight | quality_weight | Rationale |
|---|---|---|---|
| `security_fix` | 0.05 | 0.95 | A missed CVE is catastrophic |
| `bug_fix` | 0.15 | 0.85 | Correctness critical |
| `debugging` | 0.15 | 0.85 | Root-cause error costly |
| `optimization` | 0.25 | 0.75 | Suboptimal but functional is OK |
| `test_write` | 0.20 | 0.80 | Quality matters for coverage |
| `code_review` | 0.15 | 0.85 | Missed issues are expensive |
| `documentation` | 0.40 | 0.60 | Acceptable to use cheaper model |
| `feature` | 0.20 | 0.80 | Balanced |
| `refactor` | 0.25 | 0.75 | Lower risk than bug-fix |
| `integration` | 0.20 | 0.80 | Balanced |

Implementation: store per-TaskType `(cost_weight, quality_weight)` in the model_weights DB
and apply them in `AdaptiveRouter.route()` when computing the composite+cost tradeoff. This
mirrors NotDiamond's `cost_quality_tradeoff` parameter but with task-specific empirical tuning.

### 3.5 Per-Model Prompt Adapter

The `per_model_prompt_adapter.md` design specifies a `prompt_adapter.py` module. Its role in
routing: different model families require different wire formats — without the adapter, routing
to a local Qwen3 or Mistral model silently degrades output quality. Key transforms per family:

| Family | System prompt | Tool format | Stop sequences | Reasoning params |
|---|---|---|---|---|
| OpenAI / compatible | `role:system` first message | `tools[].function` | vendor-handled | `reasoning_effort` (o-series) |
| Anthropic | top-level `system` field | `tools[].input_schema` | vendor-handled | `thinking: {type:adaptive}`, `effort` |
| Gemini | `systemInstruction` object | `functionDeclarations` | vendor-handled | none exposed |
| ChatML (Qwen, Hermes) | kept as-is (server applies) | Hermes JSON in system prompt | `<|im_end|>` | `/no_think` suffix for non-thinking |
| Llama-3 | kept as-is | vLLM `llama3_json` parser | `<|eot_id|>` | none |
| Mistral | fold into first user turn | `[AVAILABLE_TOOLS]` tokens | `[INST]`, `</s>` | none |
| Gemma | prepend to first user turn | plain-text tool desc. | `<end_of_turn>` | none |

New `ModelProfile` fields: `model_family: str | None`, `reasoning_model: bool`.
These are backward-compatible (default `None`/`False` = current behaviour).
[`per_model_prompt_adapter.md` §2–3]

---

## 4. Implementation Steps: In-Flight vs. Remaining

### 4.1 Already implemented (verified in codebase)

| Component | Location | Status |
|---|---|---|
| `AdaptiveRouter` — composite scoring, cost cap, health gating, quantization penalty | `src/general_ludd/scoring/router.py` | Done |
| `BenchmarkScores.composite_score` formula | `src/general_ludd/schemas/benchmark.py` | Done |
| `TaskType` enum (10 values) | `src/general_ludd/schemas/benchmark.py` | Done |
| `ModelGateway` + `ModelProfile` + provider dispatch | `src/general_ludd/models/gateway.py` | Done |
| `ContextCompactor.compact(summary_fn=...)` hook | `src/general_ludd/agents/context.py` | Done (hook exists, not wired to LLM) |
| `AutoBenchmarkRecorder` wired in `daemon.py:443–446` | `daemon.py` | Done (per GLM_REMEDIATION_GUIDE_3.md H7) |
| `output_schema` validate/repair | existing module | Done |

### 4.2 Remaining: Prioritised

**P0 — Unblock cold-start routing (no empirical data yet)**

1. **Compactor LLM wiring** — add `gemini_compactor` and `gpt4o_mini_compactor` profiles to
   `config/model_profiles/`; wire `_make_summary_fn()` in `AgentCapabilities.prepare_messages()`.
   One file + one method. Unblocks context compression in production immediately.
   [Spec: `compaction_models.md` §5b–5c]

2. **Enumerator profile + constrained_decoding field** — add `constrained_decoding: dict` to
   `ModelProfile`; add `enumerator` profile YAML with `constrained_decoding` set; add
   `enumerator` callers in extraction paths. Unblocks structured extraction reliability.
   [Spec: `enumeration_models.md` §6.1–6.3]

3. **model_weights cold-start DB** — create `src/general_ludd/models/weights.py` with a static
   dict mapping `TaskType → list[(model_profile_id, W, W$)]` seeded from the research tables
   above. Hook into `AdaptiveRouter.route()` as the fallback when
   `reason="insufficient_historical_data"`. Replaces the generic `default_model_profile` single
   fallback with a task-aware ranked list.
   [Design: `model_routing_prior_art.md` §10.2C]

**P1 — Architectural improvements**

4. **`TaskRole` enum + router extension** — add `TaskRole` to `schemas/benchmark.py`; extend
   `BenchmarkResult` with `task_role: TaskRole | None`; extend `AdaptiveRouter.route()` to
   accept optional `task_role`; configure cold-start role→model defaults.
   [Design: `model_routing_prior_art.md` §10.2A]

5. **Per-TaskType cost/quality weights** — store the table in §3.4 above in the model_weights
   DB; apply in `AdaptiveRouter` constructor (or per-call override).

6. **Ranked fallback list per TaskType** — replace single `default_model_profile` fallback with
   an ordered list. When best candidate is unhealthy or over-budget, try second-best before
   falling back to global default.
   [Pattern: `model_routing_prior_art.md` §10.2F]

**P2 — Quality multipliers**

7. **Per-model `PromptAdapter`** — implement `src/general_ludd/models/prompt_adapter.py` per
   the design in `per_model_prompt_adapter.md`. Unblocks routing to local open-weight models
   (Qwen3, Llama-3, Mistral) without silent quality degradation. Required before P3.

8. **`TaskType.COMPACTION` and `TaskType.EXTRACTION`** — add variants to `TaskType` enum;
   register benchmark results under them; let `AdaptiveRouter` learn empirically once volume
   exists. Graduates Option B (static fallback chain) to Option A (data-driven routing) for
   infrastructure roles.

**P3 — Local model support**

9. **`LocalServerConfig.model_family` + `tool_call_parser`** — extend
   `src/general_ludd/infra/local_inference.py` to set vLLM `--tool-call-parser` and
   `--chat-template` flags from the profile's `model_family`. Unblocks reliable tool-calling
   on local Qwen3, Llama-3, Mistral models.
   [Spec: `per_model_prompt_adapter.md` §4.3]

10. **Map-reduce compaction for GPT-4o mini fallback** — add chunked summarization path in the
    compactor for histories exceeding 120K tokens (the fallback model's context limit).
    [Spec: `compaction_models.md` §5e]

---

## 5. Consolidated Bibliography Pointer

The research docs aggregate ~80 primary sources. The authoritative per-category lists are:

| Category | Bibliography location |
|---|---|
| Compaction model prices, faithfulness rankings | `docs/research/compaction_models.md` §Sources [1–13] |
| Enumeration: constrained decoding, structured output benchmarks | `docs/research/enumeration_models.md` §Sources |
| Routing prior art: OpenHands, Aider, LiteLLM, RouteLLM, NotDiamond | `docs/research/model_routing_prior_art.md` §Sources |
| Debugging / bug-fix benchmark scores | `docs/research/model_weights/debugging.md` §Sources [1–26] |
| Enumeration + compaction W scores | `docs/research/model_weights/enumeration_compaction.md` §Sources [1–15] |
| Programming by task type and by language | `docs/research/model_weights/programming.md` §Sources |
| Research and reporting benchmark scores | `docs/research/model_weights/research_reporting.md` §Sources [1–34] |
| Prompt adapter: chat templates, tool formats, stop sequences | `docs/design/per_model_prompt_adapter.md` §Sources |

**Key benchmarks and what to trust:**

| Benchmark | Use for | Caveat |
|---|---|---|
| SWE-bench Pro (Scale SEAL) | Bug-fix quality — primary | Contamination-controlled; use over Verified |
| SWE-bench Verified | Bug-fix quality — secondary | ~35 pp inflation over Pro; still useful for relative ranking |
| Aider Polyglot | Code editing, per-language proxy | No per-model per-language breakdown published |
| Multi-SWE-bench | Per-language agentic SWE | Python 3–23x higher than other languages — adjust expectations |
| GPQA Diamond | Science/research reasoning | Claude Opus 4.6 = 91.3% (best); o3 = 83.3% |
| SimpleQA / SimpleQA-Verified | Factual hallucination rate | Gemini 2.5 Pro far ahead (55.6% F1 vs Claude Opus 4 28.3%) |
| BIRD | Text-to-SQL | Gemini-SQL2/Gemini 3.1 Pro = 80.04% (June 2026 best) |
| IFEval | Instruction following / schema adherence | Good proxy for structured output reliability |
| BFCL v3 | Function calling / tool use accuracy | Qwen3-32B 0.757, Claude Opus 4.1 0.704 |
| LLMStructBench (arXiv 2602.14743) | Structured extraction — small model vs large | Key finding: prompting strategy > model size |
| LiveCodeBench | Algorithmic coding (contamination-resistant) | Gemini 3 Pro Preview leads (91.7%); Claude not prominently placed |
| HumanEval | Basic coding | Saturated at frontier (>93%); only useful for mid-tier differentiation |

**Low-confidence areas (do not over-index):**
- CONCUR per-model scores (404 leaderboard) — concurrency sub-category weights are estimates
- Aider per-language breakdown — not published; total score used as proxy
- DS-1000 2025 scores — 2024 GPT-4o as reference; frontier not re-run
- Long-context Claude 4.x MRCR — no primary published score; qualitative only
- TypeScript agentic SWE — Multi-SWE-bench 2.2% reflects hard enterprise repos, not typical TS

---

## Quick Reference: The Five Numbers to Memorise

1. **Composite score formula**: `0.35×completion + 0.25×code_quality + 0.25×instruction + 0.15×token_efficiency`
2. **Cold-start fallback**: Planner → `claude_opus_48`; Editor → `claude_haiku_45`; Compactor → `gemini_compactor`; Enumerator → `gpt41_nano` + constrained decoding
3. **Compaction break-even**: Downstream 3x+ more expensive than compactor → compact. Below that, measure.
4. **Constrained decoding multiplier**: W=0.67 + XGrammar ≈ W=0.80 unconstrained — always enable for extraction.
5. **Opus→Sonnet→Haiku tier routing**: 51% cost reduction on typical agent sessions (AugmentCode empirical).
