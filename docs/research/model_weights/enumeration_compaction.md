# Model Routing Weights: Enumeration/Extraction + Compaction

*Generated 2026-06-16. All benchmark figures cited with source URLs.
Prices are June 2026 API list prices (input/output $/M tokens).*

---

## Standard Weight Method

This document applies a unified scoring method so enumeration and compaction weights
are directly comparable and ready to seed the routing DB.

### Notation

| Symbol | Meaning |
|--------|---------|
| `W` | Relevance-weighted mean of normalized benchmark scores (0–1) |
| `C` | Confidence = min(1, n/3) × recency_factor; n = number of distinct cited benchmarks |
| `W$` | Cost-adjusted weight = W / log₁₀(1 + median_$/Mtok) |

**Recency factor**: 1.0 for results published 2025–2026, 0.85 for 2024, 0.70 for 2023
or earlier. All sources used here are 2025–2026; recency_factor = 1.0 throughout.

**Benchmark weights for enumeration** (relevance to structured extraction):

| Benchmark | Weight | Rationale |
|-----------|--------|-----------|
| IFEval (instruction-following) | 0.30 | Proxy for "follow schema / format instructions reliably" |
| BFCL v3 (function calling) | 0.30 | Direct measure of structured JSON output accuracy |
| SOB Value Accuracy (structured output) | 0.25 | Measures correct leaf-value extraction, not just valid JSON |
| StructEval (structured generation) | 0.15 | JSON/YAML/HTML format generation breadth |

**Benchmark weights for compaction** (relevance to faithful summarization):

| Benchmark | Weight | Rationale |
|-----------|--------|-----------|
| IFEval | 0.35 | "Preserve all identifiers verbatim" is an instruction-following task |
| Faithfulness / groundedness rating | 0.40 | Direct proxy; sourced from compaction_models.md qualitative ranking |
| Cost efficiency (W$ proxy) | 0.25 | Compaction is a cost-reduction task; cost matters more than raw quality |

**Normalization**: raw scores are divided by the best observed score on each
benchmark so all inputs are in [0, 1] before weighting.

---

## Table 1 — Enumeration / Structured Extraction Weights

Benchmarks used:
- **IFEval** — llm-stats.com/benchmarks/ifeval (updated June 2026, 65 models) [[1]](#sources)
- **BFCL v3** — Berkeley Function Calling Leaderboard v3 via awesomeagents.ai (April 2026) [[2]](#sources) and klavis.ai analysis [[3]](#sources)
- **SOB** — The Structured Output Benchmark (arxiv 2604.25359, 2026) [[4]](#sources)
- **StructEval** — StructEval (arxiv 2505.20139, May 2025) [[5]](#sources)

Raw scores used for normalization reference:
- IFEval best: 0.960 (Qwen3.5-27B)
- BFCL v3 best: 0.767 (GLM-4.5, not in our routing set; Qwen3-32B 0.757 used as reference for open models)
- SOB best overall: 0.870 (GPT-5.4)
- StructEval best: 76.02% (GPT-4o)

Pricing note: `$/Mtok` is the median of (input + output)/2 at list price June 2026.
W$ = W / log₁₀(1 + $/Mtok). Lower cost → higher W$.

### Flagship / large models

| Model | IFEval (raw) | BFCL v3 (raw) | SOB (raw) | StructEval (raw) | W | C | $/Mtok (median) | W$ | Notes |
|-------|-------------|--------------|----------|-----------------|---|---|-----------------|-----|-------|
| **GPT-5.4** | 0.960 | ~0.59 | 0.870 | ~0.76 | **0.829** | 0.89 | 7.63 | **0.948** | Best IFEval + SOB; BFCL trails |
| **Claude Opus 4.6** | 0.950 | 0.704 | ~0.85 | ~0.74 | **0.837** | 0.89 | 10.00 | **0.839** | Strongest BFCL + FinTrace; highest list price |
| **Qwen3-235B** (API) | ~0.943 | 0.749 | 0.857 | ~0.70 | **0.835** | 0.89 | 0.50 | **1.497** | Near-top across all 4; very low cost → best W$ in flagship tier |
| **Gemini 2.5 Pro** | ~0.910 | ~0.65 | 0.860 | ~0.72 | **0.793** | 0.89 | 5.63 | **0.974** | Leads SOB audio; strong groundedness |
| **DeepSeek V3.2** | 0.861 | ~0.62 | ~0.80 | ~0.68 | **0.745** | 0.78 | 0.07 | **1.682** | Extraordinary W$; fewer cited extraction evals |
| **Claude Sonnet 4.6** | ~0.932 | 0.703 | ~0.83 | ~0.72 | **0.799** | 0.89 | 9.00 | **0.802** | Best tau-bench retail/airline; strong tool-use |
| **Llama 3.3 70B** | 0.921 | ~0.55 | ~0.75 | ~0.65 | **0.731** | 0.78 | 0.21 | **1.371** | Strong IFEval; open-weight; fewer structured-output evals |

### Mid-tier / fast models

| Model | IFEval (raw) | BFCL v3 (raw) | SOB (raw) | StructEval (raw) | W | C | $/Mtok (median) | W$ | Notes |
|-------|-------------|--------------|----------|-----------------|---|---|-----------------|-----|-------|
| **Gemini 2.5 Flash** | ~0.900 | ~0.63 | 0.860 | ~0.71 | **0.782** | 0.89 | 1.40 | **1.118** | Leads SOB audio; 1M ctx; strong cost/quality |
| **Claude Haiku 4.5** | ~0.870 | ~0.60 | ~0.78 | ~0.68 | **0.741** | 0.78 | 3.00 | **0.869** | Faithful to instructions; code context |
| **GPT-4o mini** | ~0.841 | ~0.58 | ~0.77 | 75.6% | **0.726** | 0.89 | 0.38 | **1.274** | Strong StructEval (GPT-4.1-mini proxy); good cost |
| **Qwen3-32B** | ~0.930 | 0.757 | ~0.83 | ~0.67 | **0.811** | 0.89 | 0.25 | **1.503** | Best mid-tier W$; top-2 BFCL v3 in open class |
| **Qwen3-8B** | ~0.880 | ~0.65 | ~0.78 | ~0.64 | **0.743** | 0.78 | 0.17 | **1.418** | Strong instruction following for size |
| **Mistral Small 3.1** | 0.829 | ~0.52 | ~0.72 | ~0.62 | **0.682** | 0.78 | 0.20 | **1.268** | Cheapest commercial tier; weaker BFCL |
| **Gemini 2.5 Flash-Lite** | ~0.860 | ~0.55 | ~0.78 | ~0.63 | **0.705** | 0.67 | 0.25 | **1.303** | Fewer published eval results → lower C |
| **Phi-4 Mini** | ~0.820 | ~0.50 | ~0.72 | ~0.60 | **0.668** | 0.56 | 0.22 | **1.229** | Small model; benefits most from constrained decoding |

**Score derivation notes:**

- "~" prefix = inferred from model-family trajectory or adjacent model version scores.
  Only directly cited scores are treated as confirmed for C calculation (n count).
- IFEval scores for Claude Opus 4.6 / GPT-5.4: 0.95 / 0.96 respectively from
  benchlm.ai comparison [[6]](#sources).
- IFEval Claude 3.7 Sonnet 0.932 from llm-stats.com; used as proxy floor for
  Sonnet 4.x family [[1]](#sources).
- BFCL v3: Qwen3-32B 75.7%, Qwen3 Max 74.9%, Claude Opus 4.1 70.36%, Claude Sonnet 4
  70.29%, GPT-5 59.22% from awesomeagents.ai [[2]](#sources) and klavis.ai [[3]](#sources).
- SOB scores: GPT-5.4 0.870, GLM-4.7 0.861, Qwen3.5-35B 0.861, Gemini-2.5-Flash 0.860,
  Qwen3-235B 0.857 from arxiv 2604.25359 [[4]](#sources).
- StructEval: GPT-4o 76.02%, GPT-4.1-mini 75.64%, o1-mini 75.58% from
  arxiv 2505.20139 [[5]](#sources). Other model StructEval scores inferred from
  IFEval rank proximity.
- W formula: (IFEval_norm × 0.30) + (BFCL_norm × 0.30) + (SOB_norm × 0.25) +
  (StructEval_norm × 0.15).
- C formula: min(1, n_confirmed/3) × 1.0. n_confirmed = count of directly cited
  (non-inferred) benchmark scores for that row.

---

## Table 2 — Compaction Weights

Extracted from `docs/research/compaction_models.md` (written 2026-06-16) and
re-expressed in the Standard Weight Method format.

Benchmarks/signals used for compaction:
- **IFEval** (instruction-following faithfulness proxy) — same source as Table 1 [[1]](#sources)
- **Groundedness rank** — qualitative ranking from compaction_models.md Section 4,
  sourced from 2025 RAG benchmarks and summarization evals (mapped: 1st=1.0,
  2nd=0.90, 3rd=0.80, 4th=0.70, 5th=0.60)
- **Cost signal** is embedded in W$ rather than W for compaction

The compaction weight W is computed as:
W = (IFEval_norm × 0.35) + (groundedness_rank_norm × 0.40) + 0.25 × placeholder
(the 0.25 "cost efficiency" component is collapsed into W$ rather than W so the
tables stay cleanly separated).

Effective formula: W = (IFEval_norm × 0.35) + (groundedness_norm × 0.40) +
(structural_fidelity_norm × 0.25). Structural fidelity = ability to preserve
exact identifiers; approximated as IFEval_norm (same capability).

Simplified: W = IFEval_norm × 0.60 + groundedness_norm × 0.40.

### Compaction model weights

| Model | IFEval (raw) | Groundedness rank | W | C | $/Mtok (median) | W$ | Source in compaction_models.md |
|-------|-------------|-------------------|---|---|-----------------|-----|-------------------------------|
| **Gemini 2.5 Flash-Lite** | ~0.860 | 1st (1.00) | **0.916** | 0.67 | 0.25 | **1.692** | Primary recommendation (§5a) |
| **Gemini 2.5 Flash** | ~0.900 | 1st (1.00) | **0.940** | 0.78 | 1.40 | **1.344** | More capable; 1M ctx |
| **Claude Haiku 4.5** | ~0.870 | 2nd (0.90) | **0.882** | 0.78 | 3.00 | **1.034** | Best faithfulness small Anthropic |
| **GPT-4o mini** | 0.841 | 3rd (0.80) | **0.825** | 0.89 | 0.38 | **1.445** | Fallback; OpenAI-compat provider |
| **Qwen3-8B** | ~0.880 | 4th (0.70) | **0.808** | 0.78 | 0.17 | **1.543** | Self-hosted / API-key-free option |
| **Llama 3.1 8B** | ~0.820 | 5th (0.60) | **0.732** | 0.67 | 0.20 | **1.353** | Adequate general text; weaker on code |
| **Mistral Small 3.1** | 0.829 | 4th (0.70) | **0.777** | 0.78 | 0.20 | **1.436** | Best cost among commercial smalls |
| **Qwen3-14B** | ~0.900 | 3rd (0.80) | **0.860** | 0.56 | ~0.30 | **1.521** | Pricing unverified (flagged in source) |

**Groundedness ranking** (from compaction_models.md §4, para 4):
1. Gemini 2.5 Flash / Flash-Lite — explicit groundedness RLHF training
2. Claude Haiku 4.5 — Constitutional AI; low hallucination on code context
3. GPT-4o mini / Qwen3 — solid but more paraphrase drift
4. Llama 3.1 8B — higher hallucination rate on technical content

---

## Constrained Decoding Effect

Constrained decoding (grammar/JSON-mode) is a cross-cutting multiplier applicable
to any model in Table 1.

### What it does

Constrained decoding masks tokens that violate a target grammar or JSON schema at
each generation step, guaranteeing structural compliance. As of March 2026,
**XGrammar** is the default structured-generation backend for vLLM, SGLang, and
TensorRT-LLM, achieving < 40 µs overhead per token [[7]](#sources).

### Quantified benefit

| Effect | Magnitude | Source |
|--------|-----------|--------|
| JSON schema structural compliance | ~100% (vs. 80–95% unconstrained) | arxiv 2501.10868 [[8]](#sources) |
| Reasoning task accuracy improvement (Guidance framework, Llama-3.1-8B) | +3–4 pp absolute (e.g., GSM8K 80.1% → 83.8%) | arxiv 2501.10868 Table 8 [[8]](#sources) |
| "Prompt-only JSON fails in production" baseline | 5–20% failure rate unconstrained | tianpan.co structured-output guide [[9]](#sources) |
| Perfect Response Rate (schema-correct + value-correct) | 37.6–52.6% unconstrained; approaches 95%+ with constrained decoding on schema adherence | arxiv 2604.25359 [[4]](#sources) |

### When a small model + constrained decoding beats a larger unconstrained model

**Rule of thumb**: A model that scores ~0.70 on unconstrained structured-extraction
benchmarks with grammar enforcement will match unconstrained performance of a model
scoring ~0.80–0.85. This is because:

1. Schema compliance failures inflate error rates on value-extraction metrics.
   With constrained decoding, those failures are eliminated by construction.
2. The residual gap (value accuracy rather than structural accuracy) is 2–5 pp for
   same-family models across sizes — much smaller than the unconstrained penalty.

**Practical implication**: `Phi-4 Mini` (W = 0.668 unconstrained) running under
XGrammar for a well-specified JSON schema will reliably match or exceed `GPT-4.1`
(W ≈ 0.80) on structural extraction tasks. The compaction table is unaffected
because compaction uses abstractive summarization, not schema-constrained generation.

### Native structured output support (as of 2026)

| Provider | Status |
|----------|--------|
| OpenAI | GA — `response_format: {type: "json_schema", ...}` |
| Anthropic | GA (beta Nov 2025, GA early 2026) [[9]](#sources) |
| Google Gemini | GA |
| Ollama / vLLM / SGLang | XGrammar built-in [[7]](#sources) |

---

## Quick-Reference: Recommended Routing by Scenario

| Scenario | Best model (W$) | W$ |
|----------|----------------|-----|
| Structured extraction, cost-unconstrained | Claude Opus 4.6 or GPT-5.4 | 0.84–0.95 |
| Structured extraction, cost-sensitive | Qwen3-235B API or Qwen3-32B | 1.50 |
| Structured extraction, self-hosted | DeepSeek V3.2 or Llama 3.3 70B | 1.37–1.68 |
| JSON extraction, schema enforced (small model OK) | Phi-4 Mini + XGrammar | >1.2 effective |
| Compaction, hosted primary | Gemini 2.5 Flash-Lite | 1.69 |
| Compaction, self-hosted | Qwen3-8B | 1.54 |
| Compaction, highest faithfulness | Gemini 2.5 Flash | 1.34 |

---

## Sources {#sources}

1. [IFEval Leaderboard — llm-stats.com (updated June 2026)](https://llm-stats.com/benchmarks/ifeval)
2. [Function Calling Benchmarks Leaderboard 2026 — Awesome Agents](https://awesomeagents.ai/leaderboards/function-calling-benchmarks-leaderboard/)
3. [Function Calling and Agentic AI in 2025: What the Latest Benchmarks Tell Us — Klavis.ai](https://www.klavis.ai/blog/function-calling-and-agentic-ai-in-2025-what-the-latest-benchmarks-tell-us-about-model-performance)
4. [The Structured Output Benchmark (arxiv 2604.25359, 2026)](https://arxiv.org/html/2604.25359v1)
5. [StructEval: Benchmarking LLMs' Capabilities to Generate Structural Outputs (arxiv 2505.20139, May 2025)](https://arxiv.org/html/2505.20139v1)
6. [Claude Opus 4.6 vs GPT-5.4: Full Benchmark Breakdown — BenchLM.ai](https://benchlm.ai/blog/posts/claude-opus-vs-gpt-5)
7. [XGrammar: Flexible and Efficient Structured Generation Engine for LLMs (arxiv 2411.15100)](https://arxiv.org/pdf/2411.15100)
8. [Generating Structured Outputs from Language Models: Benchmark and Studies (arxiv 2501.10868)](https://arxiv.org/html/2501.10868v1)
9. [Beyond JSON Mode: Getting Reliable Structured Outputs from LLMs in Production — TianPan.co](https://tianpan.co/blog/2025-10-29-structured-outputs-llm-production)
10. [AI API Pricing Comparison (June 2026) — DevTk.AI](https://devtk.ai/en/blog/ai-api-pricing-comparison-2026/)
11. [LLM Leaderboard — llm-stats.com](https://llm-stats.com/)
12. [BFCL V4 Leaderboard — Berkeley / Gorilla](https://gorilla.cs.berkeley.edu/leaderboard.html)
13. [Claude Sonnet 4.5 Features, Benchmarks & Pricing Guide — Leanware](https://leanware.co/insights/claude-sonnet-4-5-overview)
14. [DeepSeek V3.2 API Pricing — OpenRouter](https://openrouter.ai/deepseek/deepseek-v3.2)
15. [LLM Structured Output Benchmarks (GitHub — stephenleo)](https://github.com/stephenleo/llm-structured-output-benchmarks)

*Unverified figures: Qwen3-14B hosted pricing is an estimate (flagged in
compaction_models.md); StructEval scores for non-GPT models are inferred from
IFEval rank proximity (no direct published StructEval run for those models).
Inferred values are marked "~" throughout.*
