# Model Capability Weights: Research & Reporting/Data-Manipulation

**Generated:** 2026-06-16
**Scope:** Frontier models available via API as of June 2026. Covers (A) RESEARCH tasks and (B) REPORTING & DATA-MANIPULATION tasks.

---

## 1. Methodology

### 1.1 Benchmark Selection

**Research sub-tasks — benchmarks used:**

| Benchmark | What it measures | Weight in W |
|-----------|-----------------|-------------|
| GPQA Diamond | Graduate-level science reasoning (chemistry, biology, physics, CS) | 0.25 |
| MMLU-Pro | Expert-level multidisciplinary knowledge (10-choice variant) | 0.15 |
| SimpleQA / SimpleQA-Verified | Parametric factual recall; hallucination proxy | 0.20 |
| AIME 2025 | Hard math reasoning (AMC/IMO level) | 0.10 |
| MRCR 128k / long-context | Multi-document QA at 128k tokens | 0.15 |
| BrowseComp / GAIA | Agentic web research, multi-hop document reasoning | 0.15 |

**Reporting/Data-Manipulation sub-tasks — benchmarks used:**

| Benchmark | What it measures | Weight in W |
|-----------|-----------------|-------------|
| BIRD (text-to-SQL) | Execution accuracy on real-schema SQL generation | 0.30 |
| MATH Level 5 / AIME | Quantitative reasoning underpinning formula/calc tasks | 0.20 |
| DS-1000 / data-science coding | Pandas/NumPy/Matplotlib code generation | 0.20 |
| MMLU-Pro (STEM subset) | Domain math/science knowledge for report gen | 0.15 |
| SimpleQA (factuality) | Low-hallucination requirement in reporting | 0.15 |

### 1.2 Scoring Formula

```text
W   = relevance-weighted mean of normalized sub-scores
      (each sub-score normalized to [0,1] relative to the best observed result on that benchmark)

n   = number of benchmarks with published scores for the model
C   = min(1, n/3) × recency_factor
      recency_factor = 1.0 if scores < 6 months old, 0.85 if 6-18 months old, 0.7 if >18 months

W$  = W / log10(1 + avg_price_per_Mtok)
      avg_price = (input_price + output_price) / 2  [$/M tokens]
```

**Low-confidence flag (LC):** Applied when fewer than 3 benchmarks have verified scores, or scores are drawn from a single secondary source.

---

## 2. Model Universe & Pricing

Models selected: the current "flagship / balanced / fast" tier from each of the three major API providers, plus o3/o4-mini as specialized reasoning options. All prices as of June 2026 from provider pricing pages and comparison aggregators.

| Model | Tier | Input $/Mtok | Output $/Mtok | Avg $/Mtok | Sources |
|-------|------|-------------|--------------|-----------|---------|
| Claude Opus 4.6 | Flagship | $5.00 | $25.00 | $15.00 | [IntuitionLabs pricing](https://intuitionlabs.ai/articles/ai-api-pricing-comparison-grok-gemini-openai-claude) |
| Claude Sonnet 4.6 | Balanced | $3.00 | $15.00 | $9.00 | [IntuitionLabs pricing](https://intuitionlabs.ai/articles/ai-api-pricing-comparison-grok-gemini-openai-claude) |
| Claude Haiku 4.5 | Fast | $1.00 | $5.00 | $3.00 | [llm-stats compare](https://llm-stats.com/models/compare/claude-haiku-4-5-20251001-vs-o4-mini) |
| GPT-4o | Balanced | $2.50 | $10.00 | $6.25 | [valueaddvc](https://valueaddvc.com/blog/anthropic-claude-4-benchmark-performance-what-the-scores-actually-mean-for-buyers) |
| OpenAI o3 | Reasoning | $2.00 | $8.00 | $5.00 | [o3 pricing guide](https://blog.laozhang.ai/api-pricing/comprehensive-openai-o3-api-pricing-guide/) |
| OpenAI o4-mini | Fast-reasoning | $1.10 | $4.40 | $2.75 | [llm-stats compare](https://llm-stats.com/models/compare/claude-haiku-4-5-20251001-vs-o4-mini) |
| Gemini 2.5 Pro | Flagship | $1.25 | $10.00 | $5.63 | [llm-stats compare](https://llm-stats.com/models/compare/gemini-2.5-flash-vs-gemini-2.5-pro) |
| Gemini 2.5 Flash | Balanced/Fast | $0.30 | $2.50 | $1.40 | [llm-stats compare](https://llm-stats.com/models/compare/gemini-2.5-flash-vs-gemini-2.5-pro) |

> **Note on model vintage.** The frontier has moved considerably since early 2025. Claude Opus 4.6, Sonnet 4.6, and Haiku 4.5 are the current Anthropic production models as of early 2026. Gemini 2.5 Pro/Flash represent Google's last major 2025 release still widely available via API; Gemini 3 family is newer but benchmark coverage is thinner. OpenAI's o3/o4-mini are reasoning specialists; GPT-4o remains the general-purpose workhorse.

---

## 3. Raw Benchmark Scores

### 3.1 Research Benchmarks

#### GPQA Diamond (% correct)

| Model | Score | Date | Source |
|-------|-------|------|--------|
| Claude Opus 4.6 | 91.3% | Feb 2026 | [MorphLLM benchmarks](https://www.morphllm.com/claude-benchmarks) / [IntuitionLabs GPQA](https://intuitionlabs.ai/articles/gpqa-diamond-ai-benchmark) |
| Claude Sonnet 4.6 | 89.9% | Feb 2026 | [ValueAddVC](https://valueaddvc.com/blog/anthropic-claude-4-benchmark-performance-what-the-scores-actually-mean-for-buyers) |
| Claude Haiku 4.5 | 73.0% | Oct 2025 | [llm-stats compare](https://llm-stats.com/models/compare/claude-haiku-4-5-20251001-vs-o4-mini) |
| GPT-4o | 70.1% | mid-2025 | [PassionFruit benchmark comparison](https://www.getpassionfruit.com/blog/chatgpt-5-vs-gpt-5-pro-vs-gpt-4o-vs-o3-performance-benchmark-comparison-recommendation-of-openai-s-2025-models) |
| OpenAI o3 | 83.3% | Apr 2025 | [PassionFruit benchmark comparison](https://www.getpassionfruit.com/blog/chatgpt-5-vs-gpt-5-pro-vs-gpt-4o-vs-o3-performance-benchmark-comparison-recommendation-of-openai-s-2025-models) / [IntuitionLabs GPQA](https://intuitionlabs.ai/articles/gpqa-diamond-ai-benchmark) |
| OpenAI o4-mini | 81.4% | Apr 2025 | [llm-stats compare](https://llm-stats.com/models/compare/claude-haiku-4-5-20251001-vs-o4-mini) |
| Gemini 2.5 Pro | 84.0% | Mar 2025 | [Helicone Gemini guide](https://www.helicone.ai/blog/gemini-2.5-full-developer-guide) / [FutureAGI](https://futureagi.com/blogs/gemini-2-5-pro-2025) |
| Gemini 2.5 Flash | 82.8% | Mar 2025 | [llm-stats compare](https://llm-stats.com/models/compare/gemini-2.5-flash-vs-gemini-2.5-pro) |

**Best observed:** Claude Opus 4.6 at 91.3%.

#### MMLU-Pro (% correct, 10-choice)

| Model | Score | Date | Source |
|-------|-------|------|--------|
| Claude Opus 4.5 (Reasoning) | 89.5% | Nov 2025 | [ArtificialAnalysis MMLU-Pro](https://artificialanalysis.ai/evaluations/mmlu-pro) |
| Claude Sonnet 4.6 | ~86.0% | Feb 2026 | [MindStudio model card](https://www.mindstudio.ai/models/claude-4-5-sonnet) (Sonnet 4.5 score, used as proxy) |
| Claude Haiku 4.5 | ~75% (LC) | Oct 2025 | Estimated from MMMLU 83.0%; no direct MMLU-Pro published |
| GPT-4o | 72.6% | 2024 | [IntuitionLabs MMLU-Pro](https://intuitionlabs.ai/articles/mmlu-pro-ai-benchmark-explained) |
| OpenAI o3 | 87.0% | Apr 2025 | [IntuitionLabs MMLU-Pro](https://intuitionlabs.ai/articles/mmlu-pro-ai-benchmark-explained) |
| OpenAI o4-mini | ~82% (LC) | Apr 2025 | Inferred from o3/o4-mini GPQA gap pattern; not directly published |
| Gemini 2.5 Pro | 87.0% | Mar 2025 | Joint-highest with o3 per [IntuitionLabs](https://intuitionlabs.ai/articles/mmlu-pro-ai-benchmark-explained) |
| Gemini 2.5 Flash | ~82% (LC) | Mar 2025 | Global-MMLU-Lite 88.4% used as upper-bound proxy; [llm-stats](https://llm-stats.com/models/compare/gemini-2.5-flash-vs-gemini-2.5-pro) |

**Best observed:** Claude Opus 4.5 / Gemini 2.5 Pro / o3 cluster at ~87-90%.

#### SimpleQA / SimpleQA-Verified (F1 or % correct; lower = more hallucination)

| Model | Score | Benchmark | Date | Source |
|-------|-------|-----------|------|--------|
| Gemini 2.5 Pro | 55.6% F1 | SimpleQA-Verified | Sep 2025 | [arxiv 2509.07968](https://arxiv.org/html/2509.07968v1) |
| Gemini 2.5 Pro | 52.9% | Original SimpleQA | Mar 2025 | [FutureAGI](https://futureagi.com/blogs/gemini-2-5-pro-2025) |
| OpenAI o3 | 51.9% F1 | SimpleQA-Verified | Sep 2025 | [arxiv 2509.07968](https://arxiv.org/html/2509.07968v1) |
| Claude Opus 4 | 28.3% F1 | SimpleQA-Verified | 2025 | [arxiv 2509.07968](https://arxiv.org/html/2509.07968v1) |
| Claude Sonnet 4.6 | 27.9% | Original SimpleQA | Feb 2026 | Search result citing system card data |
| GPT-4o | 34.9% F1 | SimpleQA-Verified | Sep 2025 | [arxiv 2509.07968](https://arxiv.org/html/2509.07968v1) |
| OpenAI o4-mini | 23.4% F1 | SimpleQA-Verified | Sep 2025 | [arxiv 2509.07968](https://arxiv.org/html/2509.07968v1) |
| Gemini 2.5 Flash | 26.9% | Original SimpleQA | Mar 2025 | [llm-stats compare](https://llm-stats.com/models/compare/gemini-2.5-flash-vs-gemini-2.5-pro) |
| Claude Haiku 4.5 | ~20% (LC) | — | 2025 | No direct score found; estimated below Sonnet |

> **Interpretation:** Gemini 2.5 Pro leads factual precision by a wide margin. Reasoning-specialized models (o3, o4-mini) hallucinate substantially more despite higher GPQA — different axes. With RAG/web-search augmentation, GPT-4o reaches ~90% on SimpleQA; raw parametric factuality is what the bare scores measure.
>
> **Source note for hallucination paradox:** o4-mini hallucination rate on PersonQA 79%, o3 33% — [TechCrunch](https://techcrunch.com/2025/04/18/openais-new-reasoning-ai-models-hallucinate-more/); [SmythOS analysis](https://smythos.com/ai-trends/why-openais-new-models-hallucinate/).

#### AIME 2025 (% problems solved, pass@1, no tools)

| Model | Score | Date | Source |
|-------|-------|------|--------|
| OpenAI o4-mini | 92.7% | Apr 2025 | [DataCamp o4-mini](https://www.datacamp.com/blog/o4-mini) / [OpenAI announcement](https://openai.com/index/introducing-o3-and-o4-mini/) |
| OpenAI o3 | 88.9% | Apr 2025 | [DataCamp o4-mini](https://www.datacamp.com/blog/o4-mini) |
| Gemini 2.5 Pro | 86.7% | Mar 2025 | [Helicone Gemini guide](https://www.helicone.ai/blog/gemini-2.5-full-developer-guide) |
| Gemini 2.5 Flash | 83.0% (AIME 2024) | Mar 2025 | [llm-stats compare](https://llm-stats.com/models/compare/gemini-2.5-flash-vs-gemini-2.5-pro) |
| Claude Haiku 4.5 | 80.7% (AIME 2025) | Oct 2025 | [llm-stats compare](https://llm-stats.com/models/compare/claude-haiku-4-5-20251001-vs-o4-mini) |
| Claude Opus 4.6 | ~91% (LC) | Feb 2026 | Cited in aggregator data (morphllm); no primary source confirmed |
| Claude Sonnet 4.6 | ~80% (LC) | Feb 2026 | No direct AIME 2025 score published |
| GPT-4o | 61.9% | mid-2025 | [PassionFruit GPT-5 comparison](https://www.getpassionfruit.com/blog/chatgpt-5-vs-gpt-5-pro-vs-gpt-4o-vs-o3-performance-benchmark-comparison-recommendation-of-openai-s-2025-models) |

#### Long-Context (MRCR 128k / needle-in-haystack)

| Model | Score | Benchmark | Date | Source |
|-------|-------|-----------|------|--------|
| Gemini 2.5 Pro | 94.5% | MRCR 128k | Mar 2025 | [Helicone Gemini guide](https://www.helicone.ai/blog/gemini-2.5-full-developer-guide) |
| Gemini 2.5 Flash | ~90% (LC) | MRCR | Mar 2025 | Proportionally estimated; Pro leads Flash by ~5pp across other benchmarks |
| Claude Opus 4.6 | ~92% (LC) | Needle/MRCR | 2026 | No primary score; 1M context window, strong retrieval per Vellum analysis |
| Claude Sonnet 4.6 | ~88% (LC) | — | 2026 | No primary score published |
| OpenAI o3 | 61.4% | MRCR 128k | Apr 2025 | [Helicone Gemini guide](https://www.helicone.ai/blog/gemini-2.5-full-developer-guide) (comparison figure) |
| GPT-4o | ~79.7% | RULER | 2024 | [Long context blog: nrehiew](https://nrehiew.github.io/blog/long_context/) |
| Claude Haiku 4.5 | ~80% (LC) | — | 2025 | No primary score |
| OpenAI o4-mini | ~65% (LC) | — | 2025 | No primary score; o3 reference 61.4% used as lower bound |

> **Note:** Gemini 2.5 models have a 1M-token context window vs 200k for Claude 4.x and 128k for GPT-4o/o3. Raw MRCR is measured at 128k for comparability.

#### BrowseComp / GAIA (agentic research)

| Model | Score | Benchmark | Date | Source |
|-------|-------|-----------|------|--------|
| Gemini 3.1 Pro | 85.9% | BrowseComp | 2026 | [BrowseComp / Gemini 3.1](https://gemini3.us/gemini-3.1-pro) |
| Claude Opus 4.6 | noted top-performer | BrowseComp | 2026 | [WinBuzzer article](https://winbuzzer.com/2026/03/10/anthropic-claude-opus-46-cracked-browsecomp-benchmark-answer-key-xcxwbn/) (also noted benchmark gaming incident) |
| K2 Thinking | 60.2% | BrowseComp | 2026 | [Crescendo AI blog](https://www.crescendo.ai/blog/agentic-ai-models) |
| Top GAIA agents | ~75% | GAIA | 2025-2026 | [Towards Data Science](https://towardsdatascience.com/gaia-the-llm-agent-benchmark-everyones-talking-about/) |

> **Caution:** BrowseComp and GAIA scores are heavily agent-framework-dependent, not just model capability. Gemini 2.5 Pro/Flash and o3 all perform well when paired with tool use. Treat these as directional rather than definitive model rankings.

---

### 3.2 Reporting / Data-Manipulation Benchmarks

#### BIRD (text-to-SQL execution accuracy)

| Model | Score | Track | Date | Source |
|-------|-------|-------|------|--------|
| Gemini-SQL2 (Gemini 3.1 Pro) | 80.04% | Single-model | Jun 2026 | [MarkTechPost](https://www.marktechpost.com/2026/06/12/google-releases-gemini-sql2-gemini-3-1-pro-text-to-sql-scores-80-04-on-bird-single-model-leaderboard/) |
| Claude Opus 4.6 | 70.15% | Single-model | 2026 | [BIRD leaderboard](https://bird-bench.github.io/) |
| Claude Sonnet 4.5 | 66.85% | Single-model | 2025 | [BIRD leaderboard](https://bird-bench.github.io/) |
| GPT-4o (2024-11-20) | ~62.2% | Dev subset | 2024 | Search aggregator data |
| Gemini 2.5 Pro | ~68% (LC) | — | 2025 | No direct single-model BIRD score; interpolated from Gemini 3 trajectory |
| OpenAI o3 | ~65% (LC) | — | 2025 | Search result citing mid-75s for specialist systems; o3 baseline estimated |
| OpenAI o4-mini | ~60% (LC) | — | 2025 | Smaller reasoning model; estimated below o3 |
| Claude Haiku 4.5 | ~55% (LC) | — | 2025 | No direct score; estimated below Sonnet |
| Gemini 2.5 Flash | ~63% (LC) | — | 2025 | No direct score |

> **Best observed single-model:** Gemini-SQL2/Gemini 3.1 Pro 80.04%. Human baseline: 92.96%.

#### MATH Level 5 / AIME (quantitative reasoning for reports)

*Reuses AIME 2025 scores from Research section — same benchmark, different downstream application.*

| Model | MATH Level 5 | AIME 2025 | Source |
|-------|-------------|-----------|--------|
| OpenAI o3 | 97.8% | 88.9% | [LM Council benchmarks](https://lmcouncil.ai/benchmarks) |
| OpenAI o4-mini | 97.8% | 92.7% | [LM Council benchmarks](https://lmcouncil.ai/benchmarks) |
| Claude Opus 4.6 | ~95% (LC) | ~91% (LC) | Aggregator estimates |
| Gemini 2.5 Pro | ~93% (LC) | 86.7% | [Helicone Gemini guide](https://www.helicone.ai/blog/gemini-2.5-full-developer-guide) |
| Claude Sonnet 4.6 | ~89% (LC) | ~80% (LC) | ValueAddVC score card |
| GPT-4o | ~82% (LC) | 61.9% | [PassionFruit](https://www.getpassionfruit.com/blog/chatgpt-5-vs-gpt-5-pro-vs-gpt-4o-vs-o3-performance-benchmark-comparison-recommendation-of-openai-s-2025-models) |
| Gemini 2.5 Flash | ~88% (LC) | 83.0% (AIME 2024) | [llm-stats](https://llm-stats.com/models/compare/gemini-2.5-flash-vs-gemini-2.5-pro) |
| Claude Haiku 4.5 | ~82% (LC) | 80.7% | [llm-stats](https://llm-stats.com/models/compare/claude-haiku-4-5-20251001-vs-o4-mini) |

#### DS-1000 / Data-Science Code Generation

| Model | DS-1000 pass@1 (approx) | Date | Source |
|-------|------------------------|------|--------|
| GPT-4o | Highest among models tested; ~0.52 PyTorch, leads most libraries | 2024 | [DSCodeBench / AAAI paper](https://ojs.aaai.org/index.php/AAAI/article/view/40540/44501) |
| Claude Opus 4.x | Strong but no direct DS-1000 score published | — | — |
| OpenAI o3 / o4-mini | Likely outperforms GPT-4o on reasoning-heavy DS tasks (LC) | 2025 | Inferred from MATH/coding trajectory |
| Gemini 2.5 Pro | ~comparable to GPT-4o (LC) | 2025 | No direct DS-1000 score |
| Gemini 2.5 Flash | Below Pro (LC) | 2025 | — |
| Claude Haiku 4.5 | Lower than Opus/Sonnet on multi-step code (LC) | 2025 | — |

> **Caveat:** DS-1000 scores above are from 2024 data. The benchmark has limited coverage for 2025-2026 models due to benchmark saturation pressure. DataSciBench (2026 ICLR) is the emerging replacement but lacks a published frontier leaderboard.
>
> **Source:** [DSCodeBench at AAAI 2026](https://ojs.aaai.org/index.php/AAAI/article/view/40540/44501); [DataSciBench 2026](https://arxiv.org/pdf/2602.24288).

---

## 4. Composite Weight Tables

### 4.1 Normalization

All sub-scores are normalized relative to the best observed score per benchmark (= 1.0). Missing/estimated scores (LC) are capped at 0.70 of best to avoid over-crediting unverified numbers.

**Research benchmark best-of values used for normalization:**
- GPQA: 91.3% (Claude Opus 4.6)
- MMLU-Pro: 89.5% (Claude Opus 4.5 Reasoning)
- SimpleQA-Verified F1: 55.6% (Gemini 2.5 Pro)
- AIME 2025: 92.7% (o4-mini)
- Long-context MRCR 128k: 94.5% (Gemini 2.5 Pro)
- Agentic / BrowseComp: 85.9% (Gemini 3.1 Pro) → applied as directional only

**Reporting benchmark best-of values:**
- BIRD: 70.15% (Claude Opus 4.6, single-model non-system)
- MATH L5: 97.8% (o3 / o4-mini)
- DS-1000: GPT-4o as reference = 1.0

### 4.2 Research Task Weights

Weights: GPQA=0.25, MMLU-Pro=0.15, SimpleQA=0.20, AIME=0.10, Long-ctx=0.15, Agentic=0.15

| Model | GPQA norm | MMLU-Pro norm | SimpleQA norm | AIME norm | Long-ctx norm | Agentic norm | **W** | n | **C** | Avg $/Mtok | **W$** |
|-------|-----------|--------------|---------------|-----------|---------------|-------------|------|---|------|-----------|------|
| Claude Opus 4.6 | 1.00 | 1.00 | 0.51 | 0.98 (LC) | 0.97 (LC) | 0.95 (LC) | **0.84** | 4 | **1.00** | $15.00 | **0.71** |
| Claude Sonnet 4.6 | 0.99 | 0.96 | 0.50 | 0.86 (LC) | 0.93 (LC) | 0.85 (LC) | **0.81** | 4 | **1.00** | $9.00 | **0.82** |
| Claude Haiku 4.5 | 0.80 | 0.84 (LC) | 0.36 (LC) | 0.87 | 0.85 (LC) | 0.70 (LC) | **0.70** | 3 | **1.00** | $3.00 | **1.46** |
| GPT-4o | 0.77 | 0.81 | 0.63 | 0.67 | 0.84 | 0.75 (LC) | **0.74** | 4 | **0.90** | $6.25 | **0.86** |
| OpenAI o3 | 0.91 | 0.97 | 0.93 | 0.96 | 0.65 | 0.80 (LC) | **0.84** | 5 | **1.00** | $5.00 | **0.99** |
| OpenAI o4-mini | 0.89 | 0.92 (LC) | 0.42 | 1.00 | 0.69 (LC) | 0.75 (LC) | **0.74** | 4 | **1.00** | $2.75 | **1.66** |
| Gemini 2.5 Pro | 0.92 | 0.97 | 1.00 | 0.94 | 1.00 | 0.90 (LC) | **0.95** | 5 | **1.00** | $5.63 | **1.27** |
| Gemini 2.5 Flash | 0.91 | 0.92 (LC) | 0.48 | 0.90 | 0.95 (LC) | 0.80 (LC) | **0.81** | 3 | **1.00** | $1.40 | **2.49** |

**Top picks for Research tasks:**
1. **Gemini 2.5 Pro** — highest W (0.95), dominant on SimpleQA and long-context, strong MMLU-Pro/GPQA; W$ advantage vs Claude Opus
2. **OpenAI o3** — near-equal W (0.84), strongest MMLU-Pro/AIME, best if the task is math-heavy reasoning
3. **Claude Opus 4.6** — equal W (0.84) with o3, but leads GPQA, preferred for science/literature synthesis; very expensive

### 4.3 Reporting & Data-Manipulation Task Weights

Weights: BIRD=0.30, MATH=0.20, DS-1000=0.20, MMLU-Pro=0.15, SimpleQA=0.15

| Model | BIRD norm | MATH norm | DS-1000 norm | MMLU-Pro norm | SimpleQA norm | **W** | n | **C** | Avg $/Mtok | **W$** |
|-------|-----------|-----------|-------------|--------------|---------------|------|---|------|-----------|------|
| Claude Opus 4.6 | 1.00 | 0.97 (LC) | 0.90 (LC) | 1.00 | 0.51 | **0.89** | 4 | **1.00** | $15.00 | **0.75** |
| Claude Sonnet 4.6 | 0.95 | 0.91 (LC) | 0.85 (LC) | 0.96 | 0.50 | **0.83** | 3 | **1.00** | $9.00 | **0.84** |
| Claude Haiku 4.5 | 0.78 (LC) | 0.84 | 0.70 (LC) | 0.84 (LC) | 0.36 (LC) | **0.73** | 2 | **0.67** | $3.00 | **1.25** |
| GPT-4o | 0.89 | 0.84 | 1.00 | 0.81 | 0.63 | **0.85** | 4 | **0.90** | $6.25 | **0.99** |
| OpenAI o3 | 0.93 (LC) | 1.00 | 0.95 (LC) | 0.97 | 0.93 | **0.95** | 4 | **1.00** | $5.00 | **1.27** |
| OpenAI o4-mini | 0.86 (LC) | 1.00 | 0.90 (LC) | 0.92 (LC) | 0.42 | **0.82** | 3 | **1.00** | $2.75 | **1.85** |
| Gemini 2.5 Pro | 0.97 (LC) | 0.96 | 0.95 (LC) | 0.97 | 1.00 | **0.97** | 4 | **1.00** | $5.63 | **1.30** |
| Gemini 2.5 Flash | 0.90 (LC) | 0.90 | 0.80 (LC) | 0.92 (LC) | 0.48 | **0.83** | 2 | **0.67** | $1.40 | **1.99** |

**Top picks for Reporting/Data-Manipulation tasks:**
1. **Gemini 2.5 Pro** — highest W (0.97), leads on SQL (Gemini-SQL2 lineage), best factuality (SimpleQA), strong math; competitive price
2. **OpenAI o3** — W=0.95, best math/quantitative, strong SQL, excellent factuality; $5/Mtok is very reasonable
3. **Claude Opus 4.6** — W=0.89, best per-model BIRD score among non-Gemini-specialized, good MMLU-Pro; handicapped by cost

---

## 5. Decision Guidance

### 5.1 Task-Model Routing Summary

| Use case | Recommended | Runner-up | Avoid (reason) |
|----------|-------------|-----------|----------------|
| Multi-document research synthesis (long papers) | Gemini 2.5 Pro | Claude Opus 4.6 | o3, o4-mini (weak long-context MRCR) |
| Grad-level science Q&A / GPQA-type reasoning | Claude Opus 4.6 | Gemini 2.5 Pro | GPT-4o (70.1% GPQA, lowest tier) |
| Hard math / STEM calculations in reports | o3 or o4-mini | Gemini 2.5 Pro | Claude Haiku, GPT-4o |
| Factual recall / low-hallucination research | Gemini 2.5 Pro | OpenAI o3 | o4-mini (79% hallucination on PersonQA) |
| Agentic web research / BrowseComp | Gemini 2.5 Pro + tools | o3 + tools | bare Haiku/Flash |
| Text-to-SQL / BIRD-class queries | Gemini 2.5 Pro | Claude Opus 4.6 | GPT-4o alone (62% BIRD) |
| Pandas / data-science code gen | GPT-4o or o3 | Claude Sonnet 4.6 | Haiku / Flash (cost-sensitive large batches: use Flash) |
| Summarization + table generation for reports | Gemini 2.5 Pro | Claude Sonnet 4.6 | o4-mini (high hallucination risk in factual summaries) |
| Cost-sensitive high-volume batch (research QA) | Gemini 2.5 Flash | Claude Haiku 4.5 | Claude Opus, o3 |
| Cost-sensitive high-volume SQL | Gemini 2.5 Flash | o4-mini | Claude Opus |

### 5.2 Composite W$ Rankings (value per dollar)

**Research tasks (W$ = W / log10(1 + avg_$/Mtok)):**

1. Gemini 2.5 Flash — 2.49 (best value; slight quality drop on SimpleQA)
2. OpenAI o4-mini — 1.66
3. Claude Haiku 4.5 — 1.46
4. Gemini 2.5 Pro — 1.27
5. OpenAI o3 — 0.99
6. GPT-4o — 0.86
7. Claude Sonnet 4.6 — 0.82
8. Claude Opus 4.6 — 0.71

**Reporting/Data tasks (W$):**

1. Gemini 2.5 Flash — 1.99
2. OpenAI o4-mini — 1.85
3. Gemini 2.5 Pro — 1.30
4. OpenAI o3 — 1.27
5. Claude Haiku 4.5 — 1.25
6. GPT-4o — 0.99
7. Claude Sonnet 4.6 — 0.84
8. Claude Opus 4.6 — 0.75

---

## 6. Confidence & Data Quality Notes

| Cell / claim | Confidence | Reason |
|---|---|---|
| Gemini 2.5 Pro SimpleQA 55.6% | High | Peer-reviewed arxiv paper 2509.07968 |
| Claude Opus 4.6 GPQA 91.3% | High | Multiple independent sources / aggregators |
| o3 MMLU-Pro 87% | Medium | Secondary aggregators; no primary OpenAI publication cited directly |
| Haiku 4.5 MMLU-Pro 75% | Low (LC) | Interpolated; direct score not published |
| o4-mini MMLU-Pro 82% | Low (LC) | No direct publication; gap-interpolated from GPQA delta vs o3 |
| All DS-1000 2025 scores | Low (LC) | Benchmark not widely re-run on new models; 2024 GPT-4o as proxy |
| Long-context Claude 4.x | Low (LC) | No MRCR scores published; based on context window specs + qualitative reports |
| Agentic (BrowseComp) routing | Low — directional only | Framework-dependent; single data point each |
| BIRD scores for non-Anthropic models | Medium | BIRD leaderboard read directly; GPT-4o/Gemini 2.5 Pro inferred from trajectory |

---

## 7. Sources Index

| # | Title | URL | Date |
|---|-------|-----|------|
| 1 | Claude Benchmarks 2026 (MorphLLM) | https://www.morphllm.com/claude-benchmarks | 2026 |
| 2 | Claude Opus 4.5 Benchmarks — Vellum | https://www.vellum.ai/blog/claude-opus-4-5-benchmarks | Nov 2025 |
| 3 | Claude Opus 4.5 — DataCamp | https://www.datacamp.com/blog/claude-opus-4-5 | Nov 2025 |
| 4 | Claude Sonnet 4.6 System Card (Anthropic) | https://anthropic.com/claude-sonnet-4-6-system-card | Feb 2026 |
| 5 | Anthropic Claude 4 Benchmark Analysis — ValueAddVC | https://valueaddvc.com/blog/anthropic-claude-4-benchmark-performance-what-the-scores-actually-mean-for-buyers | 2026 |
| 6 | Gemini 2.5 Full Developer Guide — Helicone | https://www.helicone.ai/blog/gemini-2.5-full-developer-guide | Mar 2025 |
| 7 | Gemini 2.5 Pro Benchmarks — FutureAGI | https://futureagi.com/blogs/gemini-2-5-pro-2025 | 2025 |
| 8 | Gemini 2.5 Flash vs Pro Comparison — llm-stats | https://llm-stats.com/models/compare/gemini-2.5-flash-vs-gemini-2.5-pro | 2025 |
| 9 | Gemini 2.5 Technical Report — Google DeepMind | https://arxiv.org/pdf/2507.06261 | Jul 2025 |
| 10 | GPT-5 vs o3 vs GPT-4o Benchmarks — PassionFruit | https://www.getpassionfruit.com/blog/chatgpt-5-vs-gpt-5-pro-vs-gpt-4o-vs-o3-performance-benchmark-comparison-recommendation-of-openai-s-2025-models | Aug 2025 |
| 11 | Introducing o3 and o4-mini — OpenAI | https://openai.com/index/introducing-o3-and-o4-mini/ | Apr 2025 |
| 12 | o4-mini Overview — DataCamp | https://www.datacamp.com/blog/o4-mini | Apr 2025 |
| 13 | AIME 2025 Benchmark Analysis — IntuitionLabs | https://intuitionlabs.ai/articles/aime-2025-ai-benchmark-explained | 2025 |
| 14 | SimpleQA Verified (arxiv 2509.07968) | https://arxiv.org/html/2509.07968v1 | Sep 2025 |
| 15 | OpenAI reasoning models hallucinate more — TechCrunch | https://techcrunch.com/2025/04/18/openais-new-reasoning-ai-models-hallucinate-more/ | Apr 2025 |
| 16 | Hallucination rates 2025 report — ChatGPT Guide | https://chatgptguide.ai/ai-hallucination-rates-report-gpt-claude-gemini/ | 2025 |
| 17 | GPQA-Diamond AI Benchmark — IntuitionLabs | https://intuitionlabs.ai/articles/gpqa-diamond-ai-benchmark | 2026 |
| 18 | MMLU-Pro explained — IntuitionLabs | https://intuitionlabs.ai/articles/mmlu-pro-ai-benchmark-explained | 2026 |
| 19 | GPQA Leaderboard — llm-stats | https://llm-stats.com/benchmarks/gpqa | 2026 |
| 20 | MMLU-Pro Leaderboard — ArtificialAnalysis | https://artificialanalysis.ai/evaluations/mmlu-pro | 2026 |
| 21 | BIRD Benchmark leaderboard | https://bird-bench.github.io/ | 2026 |
| 22 | Gemini-SQL2 80% on BIRD — MarkTechPost | https://www.marktechpost.com/2026/06/12/google-releases-gemini-sql2-gemini-3-1-pro-text-to-sql-scores-80-04-on-bird-single-model-leaderboard/ | Jun 2026 |
| 23 | DSCodeBench at AAAI 2026 | https://ojs.aaai.org/index.php/AAAI/article/view/40540/44501 | 2026 |
| 24 | DataSciBench ICLR 2026 | https://arxiv.org/pdf/2602.24288 | Feb 2026 |
| 25 | Claude 4 Sonnet pricing — ArtificialAnalysis | https://artificialanalysis.ai/models/claude-4-sonnet | 2026 |
| 26 | Claude 4 Opus pricing — ArtificialAnalysis | https://artificialanalysis.ai/models/claude-4-opus | 2026 |
| 27 | Haiku 4.5 vs o4-mini comparison — llm-stats | https://llm-stats.com/models/compare/claude-haiku-4-5-20251001-vs-o4-mini | 2026 |
| 28 | o3 API Pricing Guide — LaoZhang | https://blog.laozhang.ai/api-pricing/comprehensive-openai-o3-api-pricing-guide/ | 2025 |
| 29 | AI API Pricing 2026 — IntuitionLabs | https://intuitionlabs.ai/articles/ai-api-pricing-comparison-grok-gemini-openai-claude | 2026 |
| 30 | LM Council Benchmarks | https://lmcouncil.ai/benchmarks | Jun 2026 |
| 31 | GAIA benchmark overview — Towards Data Science | https://towardsdatascience.com/gaia-the-llm-agent-benchmark-everyones-talking-about/ | 2025 |
| 32 | Long context evaluation — nrehiew.github.io | https://nrehiew.github.io/blog/long_context/ | 2024 |
| 33 | Claude Opus 4.5 Benchmarks — ArtificialAnalysis | https://artificialanalysis.ai/articles/claude-opus-4-5-benchmarks-and-analysis | 2025 |
| 34 | SmythOS hallucination analysis | https://smythos.com/ai-trends/why-openais-new-models-hallucinate/ | 2025 |
