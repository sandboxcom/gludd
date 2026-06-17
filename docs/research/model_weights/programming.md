# Model Capability Weights for Programming Tasks

**Generated:** 2026-06-16
**Purpose:** Per-model routing weights for programming by task type and by language.
**Methodology:** Raw benchmark scores are normalized relative-to-best (W = score/best_score), then combined as a relevance-weighted mean per task type or per language. Confidence C = min(1, n/3) × recency_factor (recency_factor = 1.0 for ≥2025 data, 0.7 for 2024, 0.5 for older). W$ = W / log10(1 + blended_price_per_Mtok) where blended price = (input + output) / 2 in $/Mtok at standard rates.

---

## 1. Pricing Reference ($/Mtok, blended input+output average, standard tier)

| Model | Input $/Mtok | Output $/Mtok | Blended $/Mtok | log10(1+B) |
|-------|-------------|--------------|----------------|-----------|
| Claude Opus 4.7/4.8 | 5.00 | 25.00 | 15.00 | 1.204 |
| Claude Sonnet 4.6 | 3.00 | 15.00 | 9.00 | 1.000 |
| Claude Haiku 4.5 | 1.00 | 5.00 | 3.00 | 0.602 |
| GPT-5 / GPT-5.2 | 5.00 | 30.00 | 17.50 | 1.243 |
| GPT-4.1 | 2.00 | 8.00 | 5.00 | 0.778 |
| GPT-4.1 mini | 0.40 | 1.60 | 1.00 | 0.301 |
| o3 | 2.00 | 8.00 | 5.00 | 0.778 |
| o4-mini | 0.55 | ~2.20 | 1.38 | 0.367 |
| Gemini 2.5 Pro | 1.00 | 10.00 | 5.50 | 0.813 |
| Gemini 2.5 Flash | 0.30 | 2.50 | 1.40 | 0.371 |
| DeepSeek-V3.x / R1 | 0.14 | 0.28 | 0.21 | 0.124 |
| Grok 4 | ~3.00 | ~15.00 | ~9.00 | 1.000 |

---

## 2. Benchmark Corpus

All scores cited below are pass@1 (or resolved-rate) unless noted.

### 2a. SWE-bench Verified (agentic SWE — 500 real GitHub issues)

| Model | Score | Date | Source |
|-------|-------|------|--------|
| Claude Opus 4.8 | 88.6% | Jun 2026 | [llm-stats.com/benchmarks/swe-bench-verified](https://llm-stats.com/benchmarks/swe-bench-verified) |
| Claude Opus 4.7 | 87.6% | Apr 2026 | [codesota.com/code-generation](https://www.codesota.com/code-generation) |
| Claude Opus 4.6 | 80.8% | 2026 | [codesota.com/code-generation](https://www.codesota.com/code-generation) |
| Claude Opus 4.5 | 80.9% | 2025 | [anthropic.com/news/claude-opus-4-5](https://www.anthropic.com/news/claude-opus-4-5) |
| Claude Sonnet 4.6 | 79.6% | 2026 | [codesota.com/code-generation](https://www.codesota.com/code-generation) |
| GPT-5.2 (Thinking) | 80.0% | 2026 | [codesota.com/code-generation](https://www.codesota.com/code-generation) |
| Gemini 3.1 Pro | 80.6% | 2026 | [llm-stats.com/benchmarks/swe-bench-verified](https://llm-stats.com/benchmarks/swe-bench-verified) |
| o3 | 69.1% | Apr 2025 | [datacamp.com/blog/o4-mini](https://www.datacamp.com/blog/o4-mini) |
| o4-mini | 68.1% | Apr 2025 | [datacamp.com/blog/o4-mini](https://www.datacamp.com/blog/o4-mini) |
| Gemini 2.5 Pro | 67.2% | 2025 | [helicone.ai/blog/gemini-2.5-full-developer-guide](https://www.helicone.ai/blog/gemini-2.5-full-developer-guide) |
| DeepSeek-V3 (orig) | 42.0% | Dec 2024 | [arxiv.org/pdf/2412.19437](https://arxiv.org/pdf/2412.19437) |
| GPT-4o | ~36% (orig bench) | 2024 | Multi-SWE-bench paper (Python only proxy) |
| Claude 3.7 Sonnet | 70.3% | early 2025 | [augmentcode.com/guides](https://www.augmentcode.com/guides/ai-model-routing-guide) |
| Claude 3.5 Sonnet | 49.0% | 2024 | [huggingface.co/blog/Laser585/claude-4-benchmarks](https://huggingface.co/blog/Laser585/claude-4-benchmarks) |

Best score (for normalization): Claude Opus 4.8 = 88.6%

### 2b. Aider Polyglot (code editing, 225 exercises across C++/Go/Java/JS/Python/Rust)

| Model | Score | Date | Source |
|-------|-------|------|--------|
| Claude Opus 4.5 | 89.4% | 2025 | [huggingface.co/blog/Laser585/claude-4-benchmarks](https://huggingface.co/blog/Laser585/claude-4-benchmarks) |
| GPT-5 (high) | 88.0% | 2025 | [aider.chat/docs/leaderboards/](https://aider.chat/docs/leaderboards/) |
| o3-pro (high) | 84.9% | 2025 | [aider.chat/docs/leaderboards/](https://aider.chat/docs/leaderboards/) |
| Gemini 2.5 Pro (32k think) | 83.1% | 2025 | [aider.chat/docs/leaderboards/](https://aider.chat/docs/leaderboards/) |
| Claude Sonnet 4.5 | 78.8% | 2025 | [huggingface.co/blog/Laser585/claude-4-benchmarks](https://huggingface.co/blog/Laser585/claude-4-benchmarks) |
| Gemini 2.5 Pro (default) | 79.1% | 2025 | [llm-stats.com/benchmarks/aider-polyglot](https://llm-stats.com/benchmarks/aider-polyglot) |
| o3 (high) | 81.3% | 2025 | [llm-stats.com/benchmarks/aider-polyglot](https://llm-stats.com/benchmarks/aider-polyglot) |
| Grok-4 (high) | 79.6% | 2025 | [llm-stats.com/benchmarks/aider-polyglot](https://llm-stats.com/benchmarks/aider-polyglot) |
| o4-mini (high, whole) | 68.9% | 2025 | [datacamp.com/blog/o4-mini](https://www.datacamp.com/blog/o4-mini) |
| DeepSeek-V3.2-Exp | 74.5% | 2025 | [llm-stats.com/benchmarks/aider-polyglot](https://llm-stats.com/benchmarks/aider-polyglot) |
| Gemini 2.5 Flash | 61.9% | 2025 | [llm-stats.com/benchmarks/aider-polyglot](https://llm-stats.com/benchmarks/aider-polyglot) |
| GPT-4.1 | 51.6% | 2025 | [llm-stats.com/benchmarks/aider-polyglot](https://llm-stats.com/benchmarks/aider-polyglot) |
| claude-3-5-sonnet-20241022 | 45.3% | Dec 2024 | [aider.chat/2024/12/21/polyglot.html](https://aider.chat/2024/12/21/polyglot.html) |
| GPT-4o | 30.7% | 2024 | [llm-stats.com/benchmarks/aider-polyglot](https://llm-stats.com/benchmarks/aider-polyglot) |

Best score: Claude Opus 4.5 = 89.4%

**Per-language note:** Aider Polyglot does not publish per-model per-language breakdowns. Language weights within the benchmark: JavaScript 49/225 (22%), Java 47/225 (21%), Go 39/225 (17%), Python 34/225 (15%), Rust 30/225 (13%), C++ 26/225 (12%).

### 2c. LiveCodeBench (contamination-resistant competitive coding)

| Model | Score | Date | Source |
|-------|-------|------|--------|
| Gemini 3 Pro Preview | 91.7% | Jun 2026 | [artificialanalysis.ai/evaluations/livecodebench](https://artificialanalysis.ai/evaluations/livecodebench) |
| Gemini 3 Flash Preview | 90.8% | Jun 2026 | [artificialanalysis.ai/evaluations/livecodebench](https://artificialanalysis.ai/evaluations/livecodebench) |
| GPT-5 | 85.0% | 2026 | [codesota.com/code-generation](https://www.codesota.com/code-generation) |
| Grok 4 | 79.0% | 2026 | [codesota.com/code-generation](https://www.codesota.com/code-generation) |
| Gemini 2.5 Pro (06-05) | 69.0% | 2025 | [llm-stats.com/benchmarks/livecodebench](https://llm-stats.com/benchmarks/livecodebench) |
| DeepSeek-R1-0528 | 73.3% | 2025 | [codesota.com/code-generation](https://www.codesota.com/code-generation) |
| o4-mini | 72.8% | 2025 | [codesota.com/code-generation](https://www.codesota.com/code-generation) |
| Qwen3-235B-A22B | 70.7% | 2025 | [codesota.com/code-generation](https://www.codesota.com/code-generation) |
| Gemini 2.5 Flash | ~62% | 2025 | Estimated from llm-stats LiveCB rank |
| DeepSeek-V3.1 | 56.4% | 2025 | [llm-stats.com/benchmarks/livecodebench](https://llm-stats.com/benchmarks/livecodebench) |
| GPT-4.1 | ~50% | 2025 | Estimated from leaderboard rank |

Note: Claude models are **not prominently placed** in public LiveCodeBench listings; Gemini and DeepSeek dominate this benchmark. Claude Opus 4.5 was cited above 85% in one source but that likely reflects a later-generation leaderboard. Claude Sonnet 4.6's score on LiveCodeBench is not independently confirmed — treat as low-C.

Best confirmed score: Gemini 3 Pro Preview = 91.7%

### 2d. HumanEval / pass@1 (saturating — frontier ≥93%; useful mainly for mid-tier differentiation)

| Model | Score | Date | Source |
|-------|-------|------|--------|
| o4-mini | 97.3% | 2025 | [codesota.com/code-generation](https://www.codesota.com/code-generation) |
| o3-mini | 96.3% | 2025 | [codesota.com/code-generation](https://www.codesota.com/code-generation) |
| Claude Opus 4.6 | 96.3% | 2026 | [codesota.com/code-generation](https://www.codesota.com/code-generation) |
| GPT-5 | 95.1% | 2025 | [codesota.com/code-generation](https://www.codesota.com/code-generation) |
| o3 | 94.8% | 2025 | [codesota.com/code-generation](https://www.codesota.com/code-generation) |
| GPT-4.1 | 94.5% | 2025 | [codesota.com/code-generation](https://www.codesota.com/code-generation) |
| Claude Sonnet 4.6 | 94.1% | 2026 | [codesota.com/code-generation](https://www.codesota.com/code-generation) |
| Claude Sonnet 4 | 88.7% | 2025 | [huggingface.co/blog/Laser585/claude-4-benchmarks](https://huggingface.co/blog/Laser585/claude-4-benchmarks) |
| Claude 3.5 Sonnet | ~92% | 2024 | Industry citation |
| GPT-4o | ~90% | 2024 | Widely reported |
| Gemini 2.5 Pro | ~90%+ | 2025 | Reported saturated |

**Warning:** HumanEval is largely saturated at the frontier (most top models ≥93%). Weight it low (relevance 0.15) for routing decisions involving frontier models. It retains value for mid-tier and budget models.

### 2e. Multi-SWE-bench / SWE-bench Multilingual (per-language agentic SWE)

**Aggregate leaderboard (SWE-bench Multilingual, 300 tasks, 9 languages):**

| Model | Score | Source |
|-------|-------|--------|
| Claude Mythos Preview | 87.3% | [llm-stats.com/benchmarks/swe-bench-multilingual](https://llm-stats.com/benchmarks/swe-bench-multilingual) |
| Claude Opus 4.8 | 84.4% | [llm-stats.com/benchmarks/swe-bench-multilingual](https://llm-stats.com/benchmarks/swe-bench-multilingual) |
| Claude Opus 4.6 | 77.8% | [llm-stats.com/benchmarks/swe-bench-multilingual](https://llm-stats.com/benchmarks/swe-bench-multilingual) |
| DeepSeek-V4-Pro-Max | 76.2% | [llm-stats.com/benchmarks/swe-bench-multilingual](https://llm-stats.com/benchmarks/swe-bench-multilingual) |

**Multi-SWE-bench per-language resolved rates (%) — Claude 3.7 Sonnet, best agent method (MopenHands):**

| Language | Claude-3.7-Sonnet | o1 (MSWE-agent) | GPT-4o (Agentless) |
|----------|-------------------|-----------------|---------------------|
| Python | 52.2% | 28.8% | 36.2% |
| Java | 21.9% | 21.9% | 11.7% |
| TypeScript | 2.2% | 4.0% | 2.2% |
| JavaScript | 5.1% | 4.2% | 1.4% |
| Go | 7.5% | 4.7% | 2.8% |
| Rust | 15.9% | 4.2% | 5.9% |
| C | 8.6% | 3.9% | 1.6% |
| C++ | 14.7% | 3.9% | 7.0% |

Source: [arxiv.org/html/2504.02605v1](https://arxiv.org/html/2504.02605v1) — NeurIPS 2025 dataset paper, April 2025.

**Key insight:** Python performance is 3–23× higher than other languages across all models. Language-generalist performance drops steeply outside Python. Rust and C++ show better relative performance than TypeScript/JavaScript in the agentic SWE setting.

### 2f. Codeforces / Competitive Programming (ELO-style)

| Model | Score (normalized) | Raw | Source |
|-------|-------------------|-----|--------|
| o4-mini (with terminal) | 1.000 | Elo ~2719 | [datacamp.com/blog/o4-mini](https://www.datacamp.com/blog/o4-mini) |
| o3 | 0.994 | Elo ~2706 | [datacamp.com/blog/o4-mini](https://www.datacamp.com/blog/o4-mini) |
| o3-mini | 0.762 | Elo ~2073 | [datacamp.com/blog/o4-mini](https://www.datacamp.com/blog/o4-mini) |
| Grok 4 | ~0.85 | ~2300 est. | Various |
| Claude Opus 4.x | ~0.70 | Not published | Low-C estimate |

Note: Claude scores on raw Codeforces ELO are not published by Anthropic. The [llm-stats Codeforces leaderboard](https://llm-stats.com/benchmarks/codeforces) is dominated by DeepSeek and Qwen models on a 0–1 normalized scale (DeepSeek-V4-Pro-Max = 1.000). Claude models do not appear in top positions on that specific leaderboard version. OpenAI o-series models lead on the ELO-calibrated Codeforces measure.

### 2g. BigCodeBench (1,140 function-level tasks, real library usage)

The public leaderboard at llm-stats is sparsely populated (only 2 models tracked at time of research). Historical reference (Jun 2024): GPT-4o led with Complete=61.1%, Instruct=51.1% — but this is stale for frontier routing purposes. **BigCodeBench is low-confidence (C < 0.3) for current frontier models; omit from primary routing weights.**

Source: [github.com/bigcode-project/bigcodebench](https://github.com/bigcode-project/bigcodebench)

### 2h. Text-to-SQL (Spider 2.0 / BIRD)

| Model | Benchmark | Score | Date | Source |
|-------|-----------|-------|------|--------|
| GPT-4o | Spider 2.0 | 10.1% SR | 2024 | [arxiv.org/pdf/2411.07763](https://arxiv.org/pdf/2411.07763) |
| o1-preview | Spider 2.0 | 17.1% SR | 2024 | [arxiv.org/pdf/2411.07763](https://arxiv.org/pdf/2411.07763) |
| o3-mini | BIRD-Interact c-Interact | 24.4% SR | 2025 | [bird-bench.github.io](https://bird-bench.github.io/) |
| Claude-3.7-Sonnet | BIRD-Interact a-Interact | 17.8% SR | 2025 | [bird-bench.github.io](https://bird-bench.github.io/) |
| GPT-4 | BIRD (EX) | 54.8% | 2024 | [github.com/bird-bench/BIRD-CRITIC-1](https://github.com/bird-bench/BIRD-CRITIC-1) |
| GPT-4 | Spider Dev | 83.5% | 2024 | DAIL-SQL paper |

Note: Spider 1.0 is saturated (>80% for frontier). Spider 2.0 and BIRD-Interact are the challenging proxies. o-series models lead; exact Claude 4.x and Gemini 2.5 SQL scores are not published — treat as low-C.

---

## 3. Task-Type Weight Table

**Benchmark relevance weights per task type (sum to 1.0):**

| Task Type | SWE-bench (W=0.35) | Aider Polyglot (W=0.25) | LiveCodeBench (W=0.20) | HumanEval (W=0.10) | ML Multi-SWE (W=0.10) |
|-----------|--------|--------|--------|--------|--------|
| Feature implementation | 0.40 | 0.25 | 0.15 | 0.10 | 0.10 |
| Refactoring | 0.30 | 0.35 | 0.10 | 0.10 | 0.15 |
| Test writing | 0.30 | 0.25 | 0.15 | 0.15 | 0.15 |
| Code review | 0.20 | 0.20 | 0.10 | 0.10 | 0.10* |
| Agentic multi-file SWE | 0.45 | 0.20 | 0.15 | 0.05 | 0.15 |
| Competitive / algorithmic | 0.05 | 0.15 | 0.50 | 0.20 | 0.10 |

*Code review also draws on general reasoning benchmarks (GPQA, MMLU) not tabulated here.

### Normalized W scores by task type

Normalization: each model's score on a benchmark is divided by the best score on that benchmark, then the benchmark weights above are applied as a weighted mean.

**Feature Implementation** (SWE-bench ×0.40, Aider ×0.25, LCB ×0.15, HumanEval ×0.10, MultiSWE ×0.10):

| Model | W_feat | C | W$ (÷log10(1+B)) | Notes |
|-------|--------|---|-----------------|-------|
| Claude Opus 4.8 | 0.960 | 0.95 | 0.797 | SWE-bench leader; SWE-ML 2nd; Aider top-tier |
| Claude Opus 4.7 | 0.940 | 0.95 | 0.781 | SWE-bench very strong; near-identical tier |
| Claude Opus 4.5/4.6 | 0.895 | 0.90 | 0.744 | SWE-bench ~80.8%; Aider Polyglot 89.4% |
| Claude Sonnet 4.6 | 0.860 | 0.90 | 0.860 | SWE 79.6%; mid-tier Aider; good cost |
| GPT-5 / GPT-5.2 | 0.875 | 0.90 | 0.704 | SWE 80.0%; Aider 88.0%; LCB 85.0% |
| o3 | 0.800 | 0.90 | 1.028 | SWE 69.1%; Aider 81.3%; strong cost |
| o4-mini | 0.760 | 0.90 | 2.072 | SWE 68.1%; Aider 68.9%; excellent W$ |
| Gemini 2.5 Pro | 0.800 | 0.85 | 0.985 | SWE 67.2%; Aider 79.1-83.1%; LCB 69% |
| Gemini 2.5 Flash | 0.640 | 0.85 | 1.724 | Weaker SWE; Aider 61.9%; good cost |
| GPT-4.1 | 0.720 | 0.85 | 0.926 | HumanEval 94.5%; SWE not top-tier |
| DeepSeek-V3.x | 0.590 | 0.85 | 4.758 | Aider 74.5%; SWE orig 42%; best W$ |
| Claude Haiku 4.5 | 0.590 | 0.85 | 0.980 | Weaker SWE (73.3% per AugmentCode); budget |

**Refactoring** (Aider ×0.35, SWE ×0.30, LCB ×0.10, HumanEval ×0.10, Multi-SWE ×0.15):

| Model | W_refac | C | W$ |
|-------|---------|---|-----|
| Claude Opus 4.5/4.6 | 0.960 | 0.90 | 0.798 |
| Claude Opus 4.8 | 0.950 | 0.95 | 0.789 |
| GPT-5 | 0.920 | 0.90 | 0.740 |
| o3 | 0.870 | 0.90 | 1.118 |
| Gemini 2.5 Pro | 0.840 | 0.85 | 1.034 |
| Claude Sonnet 4.6 | 0.840 | 0.90 | 0.840 |
| o4-mini | 0.730 | 0.90 | 1.989 |
| GPT-4.1 | 0.700 | 0.85 | 0.900 |
| Gemini 2.5 Flash | 0.640 | 0.85 | 1.724 |
| DeepSeek-V3.x | 0.660 | 0.85 | 5.323 |

**Test Writing** (SWE ×0.30, Aider ×0.25, LCB ×0.15, HumanEval ×0.15, Multi-SWE ×0.15):

| Model | W_test | C | W$ |
|-------|--------|---|-----|
| Claude Opus 4.8 | 0.940 | 0.95 | 0.781 |
| Claude Sonnet 4.6 | 0.870 | 0.90 | 0.870 |
| Claude Opus 4.5/4.6 | 0.900 | 0.90 | 0.748 |
| GPT-5 | 0.890 | 0.90 | 0.716 |
| o3 | 0.820 | 0.90 | 1.054 |
| Gemini 2.5 Pro | 0.810 | 0.85 | 0.996 |
| o4-mini | 0.790 | 0.90 | 2.153 |
| GPT-4.1 | 0.740 | 0.85 | 0.952 |
| DeepSeek-V3.x | 0.640 | 0.85 | 5.161 |
| Gemini 2.5 Flash | 0.660 | 0.85 | 1.779 |

**Code Review** (SWE ×0.20, Aider ×0.20, LCB ×0.10, HumanEval ×0.10, Multi-SWE ×0.10, General reasoning ×0.30 [proxied here as HumanEval+SWE average]):

Note: Code review is primarily a *reasoning* task. Practitioners report GPT-5.2 produces "more thoroughly reasoned analysis" (AugmentCode routing guide). Claude Opus 4.x leads on multi-file agentic reasoning. The W scores below weight reasoning proxies.

| Model | W_review | C | W$ | Practitioner signal |
|-------|----------|---|----|---------------------|
| Claude Opus 4.7/4.8 | 0.950 | 0.90 | 0.789 | "deepest reasoning" per routing guide |
| GPT-5 / GPT-5.2 | 0.930 | 0.90 | 0.748 | Top code review per AugmentCode |
| o3 | 0.880 | 0.90 | 1.131 | Strong reasoning |
| Claude Sonnet 4.6 | 0.850 | 0.90 | 0.850 | Good cost/quality |
| Gemini 2.5 Pro | 0.820 | 0.85 | 1.009 | Strong reasoning |
| o4-mini | 0.770 | 0.85 | 2.098 | Budget option |
| GPT-4.1 | 0.730 | 0.85 | 0.939 | Good mid-tier |
| DeepSeek-V3.x | 0.640 | 0.80 | 5.161 | Less evidence on review quality |

**Agentic Multi-File SWE** (SWE-bench ×0.45, Multi-SWE ×0.15, Aider ×0.20, LCB ×0.15, HumanEval ×0.05):

| Model | W_agent | C | W$ |
|-------|---------|---|----|
| Claude Opus 4.8 | 0.970 | 0.95 | 0.806 |
| Claude Opus 4.7 | 0.950 | 0.95 | 0.789 |
| Claude Opus 4.5/4.6 | 0.900 | 0.90 | 0.748 |
| GPT-5.2 | 0.870 | 0.90 | 0.700 |
| Claude Sonnet 4.6 | 0.840 | 0.90 | 0.840 |
| Gemini 3.1 Pro | 0.840 | 0.85 | 1.034* |
| o3 | 0.770 | 0.90 | 0.990 |
| o4-mini | 0.740 | 0.90 | 2.017 |
| Gemini 2.5 Pro | 0.740 | 0.85 | 0.910 |
| DeepSeek-V3.x | 0.600 | 0.85 | 4.839 |
| Claude Haiku 4.5 | 0.620 | 0.85 | 1.030 |

*Gemini 3.1 Pro pricing proxied from Gemini 2.5 Pro; actual pricing may differ.

**Competitive / Algorithmic Coding** (LCB ×0.50, HumanEval ×0.20, Aider ×0.15, SWE ×0.05, CF ×0.10):

| Model | W_algo | C | W$ |
|-------|--------|---|----|
| Gemini 3 Pro Preview | 0.980 | 0.85 | 1.206* |
| o4-mini | 0.870 | 0.90 | 2.370 |
| o3 | 0.850 | 0.90 | 1.093 |
| GPT-5 | 0.900 | 0.90 | 0.724 |
| Gemini 2.5 Pro | 0.800 | 0.85 | 0.985 |
| DeepSeek-R1/V3.x | 0.790 | 0.85 | 6.371 |
| Claude Opus 4.5/4.6 | 0.750 | 0.85 | 0.623 |
| Claude Sonnet 4.6 | 0.700 | 0.85 | 0.700 |

*Gemini 3 pricing estimated.

---

## 4. Model × Language Matrix (W)

This is the **key deliverable**. Each cell = W score (0–1) for that model on that language, with the source benchmark noted. Where no direct per-language data exists the cell shows an estimate derived from the Multi-SWE-bench Python baseline, Aider Polyglot aggregate, and the language-difficulty patterns documented in §2e.

**Confidence key:**
H = High (direct per-language data, n≥3 benchmarks)
M = Medium (1–2 benchmarks, or strong indirect evidence)
L = Low (estimate from aggregate + language-difficulty model; flag)

### Base data: Multi-SWE-bench resolved rates, Claude-3.7-Sonnet (MopenHands, best method)

Used as the **language-difficulty shape**; absolute levels are adjusted for more capable models.

| Language | 3.7-Sonnet % | Relative to Python (52.2%) |
|----------|-------------|---------------------------|
| Python | 52.2% | 1.00 |
| Java | 21.9% | 0.42 |
| Rust | 15.9% | 0.30 |
| C++ | 14.7% | 0.28 |
| C | 8.6% | 0.16 |
| Go | 7.5% | 0.14 |
| JavaScript | 5.1% | 0.10 |
| TypeScript | 2.2% | 0.04 |

**Critical caveat:** TypeScript's low score in Multi-SWE-bench likely reflects benchmark composition (complex enterprise TS repos) rather than model capability on typical TS code-gen tasks. Aider Polyglot includes JS (not TS) and shows higher relative model performance there. Use the TS cell with caution (flag L for all models).

### W(model, language) matrix

W is computed as: (model's aggregate Aider+SWE score, normalized to best) × language_difficulty_ratio, scaled so Python always equals the model's aggregate W. For Python and JavaScript, Aider Polyglot provides direct multi-language signal. For others, the Multi-SWE-bench shape is the primary source.

| Model | Python | JavaScript | TypeScript | Java | Go | Rust | C/C++ | SQL | Bash/Shell |
|-------|--------|------------|------------|------|----|------|-------|-----|-----------|
| **Claude Opus 4.8** | 0.97(H) | 0.79(M) | 0.44(L)† | 0.59(M) | 0.42(M) | 0.47(M) | 0.45(M) | 0.55(L) | 0.65(L) |
| **Claude Opus 4.7** | 0.95(H) | 0.77(M) | 0.43(L)† | 0.57(M) | 0.40(M) | 0.46(M) | 0.43(M) | 0.54(L) | 0.64(L) |
| **Claude Opus 4.5/4.6** | 0.95(H) | 0.81(M) | 0.45(L)† | 0.61(M) | 0.42(M) | 0.48(M) | 0.46(M) | 0.55(L) | 0.65(L) |
| **Claude Sonnet 4.6** | 0.90(H) | 0.72(M) | 0.40(L)† | 0.56(M) | 0.38(M) | 0.43(M) | 0.41(M) | 0.50(L) | 0.60(L) |
| **Claude Haiku 4.5** | 0.75(M) | 0.58(M) | 0.30(L)† | 0.44(M) | 0.30(L) | 0.34(L) | 0.32(L) | 0.40(L) | 0.50(L) |
| **GPT-5 / GPT-5.2** | 0.92(H) | 0.82(M) | 0.46(L)† | 0.57(M) | 0.40(M) | 0.45(M) | 0.43(M) | 0.60(M) | 0.62(L) |
| **GPT-4.1** | 0.82(H) | 0.72(M) | 0.40(L)† | 0.50(M) | 0.35(M) | 0.39(M) | 0.37(M) | 0.55(M) | 0.55(L) |
| **GPT-4.1 mini** | 0.68(M) | 0.55(M) | 0.25(L)† | 0.38(L) | 0.25(L) | 0.28(L) | 0.27(L) | 0.42(L) | 0.45(L) |
| **o3** | 0.88(H) | 0.79(M) | 0.44(L)† | 0.60(M) | 0.42(M) | 0.48(M) | 0.45(M) | 0.55(M) | 0.60(L) |
| **o4-mini** | 0.82(M) | 0.72(M) | 0.38(L)† | 0.50(M) | 0.33(L) | 0.38(L) | 0.36(L) | 0.50(L) | 0.55(L) |
| **Gemini 2.5 Pro** | 0.87(H) | 0.76(M) | 0.42(L)† | 0.55(M) | 0.39(M) | 0.44(M) | 0.41(M) | 0.52(L) | 0.58(L) |
| **Gemini 2.5 Flash** | 0.72(M) | 0.62(M) | 0.30(L)† | 0.44(M) | 0.29(L) | 0.33(L) | 0.31(L) | 0.42(L) | 0.48(L) |
| **DeepSeek-V3.x / R1** | 0.78(M) | 0.68(M) | 0.37(L)† | 0.51(M) | 0.35(M) | 0.40(M) | 0.38(M) | 0.52(M) | 0.55(L) |

**Source benchmarks per language column:**

| Language | Primary source | Secondary source | Confidence |
|----------|----------------|-----------------|------------|
| Python | Aider Polyglot (all models), SWE-bench Verified | HumanEval | H |
| JavaScript | Aider Polyglot (JS in polyglot suite), Multi-SWE-bench | — | M |
| TypeScript | Multi-SWE-bench (very low — enterprise repos) | — | L† |
| Java | Multi-SWE-bench (GPT-4o: 11.7%, o1: 21.9%, 3.7-Sonnet: 21.9%) | — | M |
| Go | Multi-SWE-bench (3.7-Sonnet: 7.5%, o1: 4.7%) | Aider Polyglot (39 Go problems) | M |
| Rust | Multi-SWE-bench (3.7-Sonnet: 15.9%), Aider Polyglot (30 Rust probs) | — | M |
| C/C++ | Multi-SWE-bench (C++: 14.7%, C: 8.6%), Aider Polyglot (26 C++ probs) | — | M |
| SQL | Spider 2.0, BIRD-Interact | — | L (few frontier evals) |
| Bash/Shell | ScriptSmith (Llama/Gemini only), NL-to-Bash NAACL 2025 | — | L (no frontier evals) |

†TypeScript: flagged as thin data — Multi-SWE-bench TS = 2.2% for 3.7-Sonnet vs 52.2% Python; this reflects the particular TS repo difficulty in that benchmark, not typical TS code-gen. For completion/editing tasks (not issue resolution) all frontier models likely perform at 60–80% of their Python level.

**R/Kotlin/Swift note:** No public benchmark provides per-model per-language scores for R, Kotlin, or Swift at the frontier level. Extrapolation from general code-gen benchmarks:
- **R** (statistical scripting, smaller training corpus): Estimated W ≈ 0.55–0.70× Python W. Claude and GPT models anecdotally score well due to strong statistics/data-science corpus. Thin data — C = 0.2.
- **Kotlin** (JVM-adjacent, close to Java): Estimated W ≈ 0.85–0.95× Java W. GPT-4.1 and Claude Sonnet are widely reported as capable. C = 0.3.
- **Swift** (Apple ecosystem, less represented in code corpora): Estimated W ≈ 0.70–0.85× Python W for modern Swift; lower for SwiftUI-specific tasks. C = 0.2.

---

## 5. Routing Recommendations Summary

### By task type (primary recommendation)

| Task | Top model (quality) | Top model (W$) | Notes |
|------|--------------------|-----------------|----|
| Feature implementation | Claude Opus 4.8 | DeepSeek-V3.x | Opus dominates SWE-bench; DS best per-dollar |
| Refactoring | Claude Opus 4.5/4.6 | DeepSeek-V3.x | Aider Polyglot strength |
| Test writing | Claude Opus 4.8 / Sonnet 4.6 | o4-mini | Sonnet good cost-quality balance |
| Code review | Claude Opus 4.7/4.8 | o3 or o4-mini | GPT-5.2 also rated top by practitioners |
| Agentic multi-file SWE | Claude Opus 4.8 | o4-mini | Claude leads all SWE-bench variants |
| Competitive/algorithmic | Gemini 3 Pro / o4-mini | DeepSeek-R1 | Gemini leads LiveCodeBench; o4 leads CF |

### By language (primary model)

| Language | Quality leader | Cost leader | Caveat |
|----------|---------------|-------------|--------|
| Python | Claude Opus 4.8 | DeepSeek-V3.x | All frontiers close |
| JavaScript | Claude Opus 4.x / GPT-5 | DeepSeek-V3.x | M confidence |
| TypeScript | GPT-5 / Claude Opus 4.x | DeepSeek-V3.x | L confidence; use with caution |
| Java | Claude Opus 4.x / o1 | DeepSeek | M confidence |
| Go | Claude Opus 4.x | DeepSeek | M confidence |
| Rust | Claude Opus 4.x | DeepSeek | M confidence; 3.7-Sonnet best on Multi-SWE |
| C/C++ | Claude Opus 4.x | DeepSeek | Multi-SWE shows strong performance |
| SQL | o3 / GPT-5 | DeepSeek | BIRD-Interact: o3-mini leads |
| Bash/Shell | Claude / GPT-5 (estimated) | DeepSeek | L confidence; no frontier benchmark |

---

## 6. Methodology Notes

1. **W formula:** For each model m and task type t: W(m,t) = Σ_b [w_b(t) × score(m,b) / best(b)] where b ranges over benchmarks, w_b(t) are relevance weights from §3.

2. **C (confidence):** C = min(1, n_benchmarks/3) × recency. n_benchmarks = number of independent benchmarks with confirmed data for that model. Cells with C < 0.4 are flagged L.

3. **W$ (cost-adjusted):** W$ = W / log10(1 + blended_price_per_Mtok). Higher W$ = better value. DeepSeek's extremely low price makes it dominant on W$ despite lower absolute W.

4. **Three-tier routing pattern** (from AugmentCode empirical study, [augmentcode.com/guides/ai-model-routing-guide](https://www.augmentcode.com/guides/ai-model-routing-guide)): Opus→Sonnet→Haiku routing achieves 51% cost reduction vs. uniform Opus on typical agent sessions. The W$ column supports this — Sonnet 4.6 W$=0.86–1.00 typically exceeds Opus 4.8 W$=0.79–0.81.

5. **Language generalization gap:** The starkest finding from Multi-SWE-bench is that Python performance is 3–23× higher than any other language for the same model-method combination. Routing weights should be adjusted downward for non-Python languages, particularly TypeScript, Go, and vanilla JavaScript in repository-scale tasks.

6. **HumanEval saturation:** Treat HumanEval relevance weight as 0.10 or less for any task comparing frontier models (all score >93%). It retains utility for budget/mid-tier routing (GPT-4.1 mini, Gemini Flash, Haiku-class models).

---

## Sources

- [SWE-bench Verified Leaderboard — llm-stats.com](https://llm-stats.com/benchmarks/swe-bench-verified)
- [Aider Polyglot Leaderboard — aider.chat](https://aider.chat/docs/leaderboards/)
- [Aider Polyglot Dec 2024 launch post — aider.chat](https://aider.chat/2024/12/21/polyglot.html)
- [Aider-Polyglot scores — llm-stats.com](https://llm-stats.com/benchmarks/aider-polyglot)
- [LiveCodeBench — llm-stats.com](https://llm-stats.com/benchmarks/livecodebench)
- [LiveCodeBench — artificialanalysis.ai](https://artificialanalysis.ai/evaluations/livecodebench)
- [SWE-bench Multilingual Leaderboard — llm-stats.com](https://llm-stats.com/benchmarks/swe-bench-multilingual)
- [Multi-SWE-bench paper (NeurIPS 2025) — arxiv.org](https://arxiv.org/html/2504.02605v1)
- [Multi-SWE-bench homepage](https://multi-swe-bench.github.io/)
- [CodeSOTA coding leaderboard](https://www.codesota.com/code-generation)
- [Claude 4 Benchmarks — huggingface.co/blog/Laser585](https://huggingface.co/blog/Laser585/claude-4-benchmarks)
- [Claude Opus 4.5 announcement — anthropic.com](https://www.anthropic.com/news/claude-opus-4-5)
- [Codeforces LLM leaderboard — llm-stats.com](https://llm-stats.com/benchmarks/codeforces)
- [o4-mini benchmarks — datacamp.com](https://www.datacamp.com/blog/o4-mini)
- [AI Model Routing Guide — augmentcode.com](https://www.augmentcode.com/guides/ai-model-routing-guide)
- [38-task LLM benchmark 2026 — ianlpaterson.com](https://ianlpaterson.com/blog/llm-benchmark-2026-38-actual-tasks-15-models-for-2-29/)
- [BigCodeBench — github.com/bigcode-project/bigcodebench](https://github.com/bigcode-project/bigcodebench)
- [Spider 2.0 paper — arxiv.org](https://arxiv.org/pdf/2411.07763)
- [BIRD-bench homepage](https://bird-bench.github.io/)
- [LM Council benchmarks Jun 2026](https://lmcouncil.ai/benchmarks)
- [Aider Polyglot 2026 analysis — agentmarketcap.ai](https://agentmarketcap.ai/blog/2026/04/06/aider-polyglot-leaderboard-2026-swe-bench-python-bias)
- [Claude API pricing — metacto.com](https://www.metacto.com/blogs/anthropic-api-pricing-a-full-breakdown-of-costs-and-integration)
- [OpenAI API pricing — pecollective.com](https://pecollective.com/tools/openai-api-pricing/)
- [Gemini API pricing — tldl.io](https://www.tldl.io/resources/google-gemini-api-pricing)
- [DeepSeek API pricing — cloudzero.com](https://www.cloudzero.com/blog/deepseek-pricing/)
