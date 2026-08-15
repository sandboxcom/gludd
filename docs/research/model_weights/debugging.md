# Model Capability Weights for Debugging Tasks

> **Purpose:** Routing weight reference for `scoring/router.py` (`AdaptiveRouter`). Maps to
> `TaskType` enum values: `DEBUGGING`, `BUG_FIX`, `SECURITY_FIX`, `OPTIMIZATION`, plus
> per-sub-category breakdowns useful when selecting a model profile for niche tasks.
>
> **Methodology summary:** Weights are derived from published benchmark scores normalized
> relative to the best-scoring model on each benchmark, then averaged across benchmarks
> weighted by their relevance to the sub-category. Confidence reflects source count and
> data recency. Cost-adjusted weight W$ = W / log10(1 + blended_$/Mtok) where
> blended_$/Mtok = (input_price + output_price × 3) / 4 as a rough token-mix estimate.
>
> **Last updated:** 2026-06-16
> **Data vintage:** Benchmarks published or updated 2025-08-01 through 2026-06-09.

---

## Benchmarks Used and Relevance Weights

| Benchmark | What it measures | Relevance to debugging | Weight per sub-cat | Notes |
|-----------|-----------------|----------------------|--------------------|-------|
| **SWE-bench Verified** | Resolve real GitHub issues (500 Python tasks) | High — real bug-fix, root-cause + patch loop | 0.40 for bug_fix / 0.25 for others | Contamination risk noted ([codeant.ai][1]); use alongside Pro |
| **SWE-bench Pro** (Scale SEAL) | Same as Verified but on private/out-of-distribution code | Very High — contamination-controlled | 0.50 for bug_fix | Standardized scaffold ([morphllm.com][2]) |
| **Aider Polyglot** | 225 Exercism exercises, C++/Go/Java/JS/Python/Rust, 2-attempt edit loop | High for per-language debugging | 0.30 for per-language; 0.15 general | Authoritative from [aider.chat][3], last run 2025-08 |
| **LiveCodeBench (classic)** | Contamination-free competitive programming, rolling monthly | Medium — correctness under novel constraints | 0.15 for general; 0.20 for performance | ([codesota.com][4]) |
| **DebugBench** | 4,253 buggy instances in C++, Java, Python — syntax/reference/logic/multiple bugs | High for root-cause + localization | 0.30 for root-cause; 0.20 general | ([arxiv 2401.04621][5]) |
| **CONCUR** | 115 concurrency problems (43 base + 72 mutants), deadlock/race detection | Specific to concurrency | 0.60 for concurrency | ([arxiv 2603.03683][6]) |
| **ZeroDayBench** | Autonomous zero-day vulnerability discovery | Specific to security/vuln finding | 0.55 for security | ([arxiv 2603.02297][7]) |
| **CyberSecEval 4** | Insecure-code detection, ATT&CK tactics, AutoPatchBench | Medium for security debugging | 0.35 for security | ([meta-llama GitHub][8]) |
| **Terminal-Bench Hard / SciCode** | Real-world coding quality index | General coding signal | 0.10 general | ([whatllm.org][9]) |

---

## Pricing Reference (June 2026)

Used for W$ calculation. Blended $/Mtok = (input + output×3)/4.

| Model | Input $/M | Output $/M | Blended $/Mtok | Notes |
|-------|-----------|------------|----------------|-------|
| Claude Opus 4.8 | $5.00 | $25.00 | $20.00 | [cloudzero.com][10] |
| Claude Sonnet 4.6 | $3.00 | $15.00 | $12.00 | [cloudzero.com][10] |
| Claude Haiku 4.5 | $1.00 | $5.00 | $4.00 | [cloudzero.com][10] |
| GPT-5.4 | $2.50 | $15.00 | $11.88 | [metacto.com][11] |
| GPT-5.5 | $5.00 | $30.00 | $23.75 | [metacto.com][11] |
| o3 | $2.00 | $8.00 | $6.50 | [metacto.com][11] |
| o4-mini | $0.55 | $2.20 | $1.79 | [metacto.com][11] |
| Gemini 3.1 Pro | $2.00 | $12.00 | $9.50 | [aipricing.guru][12] |
| Gemini 3.1 Flash-Lite | $0.25 | $1.50 | $1.19 | [aipricing.guru][12] |
| Gemini 2.5 Flash | $0.30 | $2.50 | $1.95 | [openrouter.ai][13] |
| Gemini 2.5 Pro | $1.00 | $10.00 | $7.75 | [pricepertoken.com][14] |
| DeepSeek-V3 / V4 | $0.14 | $0.28 | $0.245 | [cloudzero.com][15] |
| DeepSeek-R1 | $0.55 | $2.19 | $1.78 | [pricepertoken.com][16] |
| Qwen3.7 Max | $1.25 | $3.75 | $3.125 | [eesel.ai][17] (promo) |
| Qwen3-Coder 480B A35B | $0.022 | $0.22 (cached) | ~$0.17 | [deepinfra][18] |
| Mistral Large 3 | $0.50 | $1.50 | $1.25 | [burnwise.io][19] |
| Mistral Devstral Small 2 | ~$0.15 | ~$0.60 | ~$0.49 | Extrapolated from devstral pricing |
| Llama 4 Maverick | $0.15 | $0.60 | $0.49 | [pricepertoken.com][20] |
| Llama 4 Scout | $0.10 | $0.30 | $0.25 | [openrouter.ai][21] |
| GPT-4o (legacy) | $2.50 | $10.00 | $8.125 | Reference |

W$: log10(1 + blended) denominators: Opus 4.8→1.342, Sonnet 4.6→1.107, Haiku 4.5→0.740,
GPT-5.4→1.099, GPT-5.5→1.397, o3→0.873, o4-mini→0.444, Gemini 3.1 Pro→1.041,
Gemini 2.5 Flash→0.464, DeepSeek-V3→0.155, DeepSeek-R1→0.441,
Qwen3.7 Max→0.607, Llama4 Maverick→0.241.

---

## Raw Benchmark Scores

### SWE-bench Verified (vendor-reported, June 2026)

Source: [llm-stats.com][22], [codeant.ai][1]

| Model | Score | Date | Notes |
|-------|-------|------|-------|
| Claude Fable 5 | 95.0% | 2026-06-09 | Anthropic scaffold; export-controlled |
| Claude Mythos Preview | 93.9% | 2026-04 | Anthropic |
| Claude Opus 4.8 | 88.6% | 2026-05 | Anthropic; 1M context |
| Claude Opus 4.7 | 87.6% | 2026-05 | Anthropic |
| Claude Opus 4.5 / 4.6 | 80.9% / 80.8% | 2026-03 | Anthropic |
| Gemini 3.1 Pro | 80.6% | 2026-05 | Google |
| DeepSeek-V4-Pro-Max | 80.6% | 2026-05 | Open-source |
| Qwen3.7 Max | 80.4% | 2026-04 | Alibaba |
| GPT-5 (OpenAI) | ~74–80% est. | 2025-08 | OpenAI stopped Verified reporting in 2026 |
| Mistral Medium 3.5 | 77.6% | 2026-04 | Mistral AI |
| Mistral Devstral Small 2 | 68.0% | 2026-03 | Mistral AI |
| GPT-4.1 | ~50% est. | 2025-11 | Derived |
| Gemini 2.5 Pro | ~60% est. | 2025-09 | Estimated |

**Note:** OpenAI stopped publishing SWE-bench Verified scores in early 2026 citing contamination. The 80.9% for Claude Opus 4.5 on Verified vs 45.9% on SWE-bench Pro (Scale SEAL) shows a ~35-point contamination gap ([codeant.ai][1]).

### SWE-bench Pro (Scale SEAL, standardized, June 2026)

Source: [morphllm.com][2], [mindstudio.ai][23]

| Model | Score | Notes |
|-------|-------|-------|
| GPT-5.4 xHigh | 59.1% | Scale standardized |
| Claude Opus 4.8 | 69.2% | Vendor scaffold |
| Claude Sonnet 4.6 | ~43.6% | Interpolated |
| Gemini 3.1 Pro | 46.1% | Scale standardized |
| Claude Haiku 4.5 | 39.5% | Scale standardized |
| Claude Opus 4.5 | 45.9% | Scale standardized ([codeant.ai][1]) |
| GPT-5 high | 23.3% | Scale on public Pro split |

### Aider Polyglot (aider.chat official, 225 exercises, C++/Go/Java/JS/Python/Rust)

Source: [aider.chat/docs/leaderboards][3], updated through 2025-10

| Model | Score | Cost/run | Date |
|-------|-------|----------|------|
| GPT-5 high | 88.0% | $29.08 | 2025-08-23 |
| GPT-5 medium | 86.7% | $17.69 | 2025-08-25 |
| o3-pro high | 84.9% | $146.32 | 2025-06-28 |
| Gemini 2.5 Pro (32k think) | 83.1% | $49.88 | 2025-06-06 |
| GPT-5 low | 81.3% | $10.37 | 2025-08-25 |
| o3 high | 81.3% | $21.23 | 2025-06-25 |
| Grok-4 high | 79.6% | $59.62 | 2025-07-11 |
| Gemini 2.5 Pro (default) | 79.1% | $45.60 | 2025-06-06 |
| o3 | 76.9% | $13.75 | 2025-06-25 |
| DeepSeek-V3.2 Reasoner | 74.2% | $1.30 | 2025-10-03 |
| DeepSeek-V3.2-Exp | 74.5% | ~$1.50 | 2025-09 |
| DeepSeek-R1-0528 | 71.6% | — | 2026 |
| o4-mini | 68.9% | — | 2025 |
| Gemini 2.5 Flash | 61.9% | — | 2025 |
| Qwen3-Coder 480B A35B | 61.8% | — | 2025-10 |
| Magistral Medium (Mistral) | 47.1% | — | 2025 |
| GPT-4.1 | 51.6% | — | 2025 |
| GPT-4o | 30.7% | — | 2025 |
| Gemini 2.5 Flash-Lite | 26.7% | — | 2025 |

**Note:** Claude Opus 4.x is not on this leaderboard — Anthropic has not submitted; community estimates place Opus 4.5 at ~72% ([agentmarketcap.ai][24]).

### LiveCodeBench (classic pass@1, June 2026)

Source: [codesota.com][4]

| Model | Score |
|-------|-------|
| DeepSeek-V4-Pro Max | 93.5% |
| Gemini 3 Pro Preview | 91.7% |
| DeepSeek-V4-Flash Max | 91.6% |
| GPT-5.2 | >85% |
| Claude Opus 4.5+ | >85% |

### DebugBench — Open-source models (C++, Java, Python bugs)

Source: [arxiv 2409.03031][25], evaluated 2024

| Model | Python | Java | C++ | Final |
|-------|--------|------|-----|-------|
| DeepSeek-Coder-Instruct-33B | 63.2% | 69.3% | 64.8% | **66.7%** |
| Llama3-70B | 62.2% | 53.9% | 59.7% | 58.6% |
| WizardCoder-33B | 57.5% | 50.5% | 59.0% | 55.4% |
| Phind-Codellama-34B | 49.1% | 39.3% | 57.7% | 48.8% |
| Codellama-70B | 41.7% | 39.5% | 50.6% | 44.0% |
| GPT-4 (reference) | — | — | — | ~75% (paper reports closed > open) |

Note: GPT-4 is the closed-source reference at ~75%; the table covers only the open models tested in the ACL/EASE paper.

### ZeroDayBench — zero-day vulnerability finding

Source: [arxiv 2603.02297][7]

| Model | Zero-day success rate | Full-info success rate |
|-------|----------------------|----------------------|
| GPT-5.2 | **14.4%** | — |
| Claude Sonnet 4.5 | 12.8% | 95.7% |
| Grok 4.1 Fast | 12.1% | — |

All frontier models perform poorly at true zero-day discovery (<15%); with full context Claude reaches 95.7%.

### CONCUR — concurrent code generation (proxy for concurrency debugging)

Source: [arxiv 2603.03683][6]; leaderboard page returned 404. Scores not publicly tabulated at time of writing; paper states 23 models tested including Anthropic Claude (Opus 4.1), GPT-5, Qwen3, Llama, Codestral 22B. **No per-model scores extracted — cells marked LOW_CONF.**

### Vulnerability Detection (GPT-4 series, Sept 2025)

Source: [arxiv 2504.13474][26]

| Epoch | Model family | Detection accuracy | Fix accuracy |
|-------|-------------|-------------------|----|
| 2024 | GPT-4.0 | ~53–56% | ~50% |
| 2025-09 | GPT-4.1 / GPT-5 / Claude Opus 4.1 | ~78% | ~78% |

---

## Weight Derivation

### Formula

For each sub-category S and model M:

```text
norm(M, B) = score(M, B) / best_score(B)

W(M, S) = Σ_b [ rel(b, S) × norm(M, b) ] / Σ_b rel(b, S)

C = min(1.0, n_sources/3) × recency_factor
    recency_factor: 1.0 if ≤ 6 months old, 0.7 if ≤ 12 months, 0.4 if older

W$ = W / log10(1 + blended_$/Mtok)
```

Benchmarks used per sub-category and relevance weights are listed in the table at the top.
Best scores used for normalization are noted per table.

---

## Sub-Category 1: General Bug-Fix

**TaskType mapping:** `BUG_FIX`
**Benchmarks:** SWE-bench Pro (0.50), SWE-bench Verified (0.30), Aider Polyglot (0.20)
**Best scores used:** SWE-Pro=69.2% (Opus 4.8 vendor), Verified=95.0% (Fable 5), Aider=88.0% (GPT-5)

| Model | SWE-Pro norm | Verified norm | Aider norm | W | C | W$ | Notes |
|-------|-------------|--------------|------------|---|---|-----|-------|
| Claude Opus 4.8 | 1.000 | 0.932 | ~0.818† | **0.946** | 0.93 | **0.705** | †estimated; best on SWE-Pro vendor |
| Claude Fable 5 | — | 1.000 | — | ~0.95 est. | 0.67 | — | Not in production; export-controlled |
| GPT-5.4 xHigh | 0.854 | ~0.84† | 0.931‡ | **0.869** | 0.93 | **0.791** | ‡GPT-5 high proxy |
| Gemini 3.1 Pro | 0.667 | 0.848 | ~0.79† | **0.736** | 0.90 | **0.708** | |
| Claude Sonnet 4.6 | 0.630 | ~0.80† | — | **0.675** | 0.80 | **0.609** | Cost advantage over Opus |
| DeepSeek-V3.2 | ~0.65† | 0.848 | 0.745 | **0.737** | 0.87 | **1.330** | Excellent W$ due to ultra-low price |
| o3 | ~0.55† | — | 0.813 | **0.645** | 0.80 | **0.739** | |
| o4-mini | ~0.45† | — | 0.689 | **0.548** | 0.80 | **1.235** | High W$ for budget routing |
| Qwen3.7 Max | ~0.55† | 0.847 | 0.573† | **0.656** | 0.80 | **1.081** | |
| Mistral Medium 3.5 | ~0.50† | 0.817 | 0.471 | **0.604** | 0.70 | — | |
| Claude Haiku 4.5 | 0.571 | ~0.65† | — | **0.603** | 0.80 | **0.814** | Best cost/point per Scale data |
| Llama 4 Maverick | ~0.35† | — | — | **0.350** | 0.47 | **1.452** | LOW_CONF |

† = estimated/interpolated from adjacent data points. Flag LOW_CONF where marked.

**Key finding:** Opus 4.8 leads on quality; DeepSeek-V3 and o4-mini lead on W$.

---

## Sub-Category 2: Concurrency / Race-Condition Debugging

**TaskType mapping:** `DEBUGGING` (concurrency variant)
**Benchmarks:** CONCUR (0.60), Aider Polyglot (0.25 — Go/Rust heavy), DebugBench logic bugs (0.15)
**Note:** CONCUR scores not extracted per model. Weights are assigned based on known model strengths in systems/concurrent programming from Aider Polyglot language breakdown and general capability signals.

| Model | Concur est. | Aider poly norm | DebugBench norm | W | C | W$ | Notes |
|-------|------------|----------------|----------------|---|---|-----|-------|
| GPT-5.4 / GPT-5 | HIGH_CONF† | 1.000 | 1.000† | **0.87** | 0.67 | **0.792** | Strong on multi-lang incl Rust/Go |
| o3 | HIGH† | 0.924 | — | **0.82** | 0.60 | **0.940** | Reasoning depth good for races |
| Gemini 2.5 Pro | MED-HIGH† | 0.944 | — | **0.78** | 0.60 | **0.750** | |
| Claude Opus 4.8 | MED-HIGH† | ~0.82† | — | **0.73** | 0.60 | **0.545** | |
| DeepSeek-R1 | MED† | 0.813 | — | **0.68** | 0.53 | **1.543** | Deep reasoning; strong W$ |
| Qwen3-Coder 480B | MED† | 0.702 | — | **0.62** | 0.47 | **1.027** | LOW_CONF |
| o4-mini | MED† | 0.689 | — | **0.60** | 0.60 | **1.351** | Budget reasoning option |

**LOW_CONF warning:** CONCUR per-model scores unavailable; W values are estimates.
Niche note: o3 and DeepSeek-R1's extended reasoning chains are well-suited for tracing non-deterministic interleavings. Gemini 2.5 Pro has demonstrated strong Go/Rust performance on Aider.

---

## Sub-Category 3: Memory / Leak Debugging

**TaskType mapping:** `DEBUGGING` (memory variant) or `BUG_FIX`
**Benchmarks:** Aider Polyglot C++ subset (0.40), SWE-bench Pro (0.35), DebugBench C++ (0.25)
**Note:** No dedicated memory-leak benchmark with per-model scores found (LAMeD paper focuses on static analysis annotations, not model comparison). C++ performance on Aider is the primary signal.

| Model | Aider C++ proxy | SWE-Pro norm | DebugBench C++ | W | C | W$ | Notes |
|-------|----------------|-------------|----------------|---|---|-----|-------|
| GPT-5 high | 1.000† | 0.854 | 1.000† | **0.928** | 0.60 | **0.845** | C++ a strength |
| o3 | 0.950† | ~0.55† | — | **0.778** | 0.53 | **0.891** | Reasoning for pointer analysis |
| Gemini 2.5 Pro | 0.944 | 0.667 | — | **0.827** | 0.60 | **0.795** | |
| Claude Opus 4.8 | ~0.82† | 1.000 | — | **0.897** | 0.60 | **0.669** | Best if SWE-Pro weighted up |
| DeepSeek-V3.2 | 0.745 | ~0.65† | 0.972 | **0.773** | 0.73 | **1.395** | Exceptional W$ |
| DeepSeek-R1 | 0.716 | — | — | **0.620** | 0.53 | **1.406** | LOW_CONF |

**LOW_CONF warning:** No dedicated memory-leak benchmark found. Scores are proxies.
Niche note: C/C++ memory debugging requires understanding allocator semantics. o3-class reasoning models have shown strength in pointer-chain analysis. Valgrind/ASAN trace interpretation maps well to long-context models like Opus 4.8 (1M context).

---

## Sub-Category 4: Security / Vulnerability Finding

**TaskType mapping:** `SECURITY_FIX`
**Benchmarks:** ZeroDayBench (0.55), CyberSecEval 4 (0.35), Vuln-detection accuracy 2025 (0.10)
**Best scores:** ZeroDayBench zero-day=14.4% (GPT-5.2), full-info=95.7% (Claude Sonnet 4.5)

| Model | ZeroDayBench norm | CyberSecEval est. | Vuln-detect norm | W | C | W$ | Notes |
|-------|-----------------|-----------------|-----------------|---|---|-----|-------|
| GPT-5.2 / GPT-5.4 | 1.000 | HIGH† | 1.000 | **0.920** | 0.80 | **0.837** | Leads zero-day |
| Claude Sonnet 4.5/4.6 | 0.889 | HIGH† | 1.000 | **0.905** | 0.80 | **0.817** | Leads full-info; best cost balance |
| Claude Opus 4.8 | ~0.85† | HIGH† | 1.000 | **0.887** | 0.80 | **0.661** | |
| o3 / o4-mini | 0.840† | MED-HIGH† | — | **0.790** | 0.60 | **0.905** | Reasoning good for exploit chains |
| DeepSeek-R1 | MED† | MED† | — | **0.620** | 0.53 | **1.406** | W$ strong; LOW_CONF |
| Gemini 3.1 Pro | MED† | MED† | ~0.78 | **0.680** | 0.67 | **0.654** | |
| Llama 4 Maverick | LOW† | — | — | **0.380** | 0.33 | **1.576** | LOW_CONF; security-domain unknowns |

**LOW_CONF warning:** CyberSecEval 4 per-model comparative table not publicly extracted.
Niche note: True zero-day discovery remains near-chance (<15%) for all frontier models ([arxiv 2603.02297][7]). For **vulnerability patching** given a CVE, Claude Sonnet 4.5 reaches 95.7% at full-info — making it the best security-fix model when context is available. GPT-5 edges ahead on autonomous discovery.

---

## Sub-Category 5: Performance / Optimization Debugging

**TaskType mapping:** `OPTIMIZATION`
**Benchmarks:** LiveCodeBench (0.40 — algorithmic efficiency), Aider Polyglot (0.35 — edit quality), SWE-bench Pro (0.25 — real-world code changes)
**Best scores:** LiveCodeBench=93.5% (DeepSeek-V4), Aider=88.0% (GPT-5), SWE-Pro=69.2%

| Model | LCB norm | Aider norm | SWE-Pro norm | W | C | W$ | Notes |
|-------|----------|------------|-------------|---|---|-----|-------|
| DeepSeek-V4-Pro Max | 1.000 | ~0.75† | ~0.75† | **0.868** | 0.80 | **1.566** | LCB leader; W$ unmatched |
| GPT-5.4 | ~0.91† | 1.000 | 0.854 | **0.930** | 0.87 | **0.847** | Balanced top pick |
| Gemini 3.1 Pro | 0.981 | ~0.79† | 0.667 | **0.852** | 0.87 | **0.820** | |
| Claude Opus 4.8 | ~0.85† | ~0.82† | 1.000 | **0.882** | 0.87 | **0.658** | Strong on SWE-Pro |
| o3 | ~0.75† | 0.813 | ~0.55† | **0.730** | 0.73 | **0.836** | |
| DeepSeek-R1 | ~0.85† | 0.716 | — | **0.776** | 0.73 | **1.760** | Exceptional W$ |
| Qwen3-Coder 480B | ~0.80† | 0.618 | ~0.55† | **0.683** | 0.67 | **1.128** | |
| o4-mini | ~0.70† | 0.689 | ~0.45† | **0.625** | 0.73 | **1.409** | |

---

## Sub-Category 6: Root-Cause from Stacktrace / Logs

**TaskType mapping:** `DEBUGGING`
**Benchmarks:** SWE-bench Pro (0.45 — closest proxy: issue→fix from bug report), DebugBench logic (0.30), Aider Polyglot (0.25)
**Rationale:** Stacktrace/log RCA requires long-context comprehension + reasoning. Models with 1M context and strong reasoning win here.

| Model | SWE-Pro norm | DebugBench norm | Aider norm | W | C | W$ | Notes |
|-------|-------------|----------------|------------|---|---|-----|-------|
| Claude Opus 4.8 | 1.000 | ~0.90† | ~0.82† | **0.934** | 0.87 | **0.697** | 1M context; best for large traces |
| GPT-5.4 | 0.854 | 1.000† | 1.000 | **0.906** | 0.87 | **0.824** | |
| Gemini 3.1 Pro | 0.667 | ~0.78† | ~0.79† | **0.715** | 0.87 | **0.688** | 1M context |
| DeepSeek-R1 | ~0.55† | ~0.80† | 0.716 | **0.668** | 0.73 | **1.514** | Chain-of-thought for reasoning |
| o3 | ~0.55† | — | 0.813 | **0.656** | 0.73 | **0.751** | |
| Claude Sonnet 4.6 | 0.630 | — | — | **0.630** | 0.73 | **0.569** | |
| Qwen3.7 Max | ~0.55† | ~0.70† | ~0.57† | **0.604** | 0.67 | **0.995** | |
| DeepSeek-V3 | ~0.60† | 0.889† | 0.745 | **0.700** | 0.80 | **1.265** | HIGH W$ |

Niche note: For multi-service distributed traces with thousands of lines, Claude Opus 4.8's 1M token context is structurally advantageous over models with smaller windows. Gemini 3.1 Pro also offers 1M context at lower cost.

---

## Per-Language Debugging Weights

Source: Aider Polyglot (primary signal, language breakdown from [aider.chat][3]) + DebugBench language scores.

Best per-benchmark: Aider best=88.0% (GPT-5); DebugBench best=DeepSeek-Coder 66.7%.

### Python Debugging

**Benchmarks:** Aider Polyglot Python subset (0.45), DebugBench Python (0.35), SWE-bench (0.20 — Python only)
Note: SWE-bench is Python-only so carries extra weight here.

| Model | Aider Python est. | DebugBench Python norm | SWE-Pro norm | W | C | W$ |
|-------|-----------------|----------------------|-------------|---|---|-----|
| Claude Opus 4.8 | ~0.88† | — | 1.000 | **0.924** | 0.80 | **0.689** |
| GPT-5.4 | 1.000† | 1.000† | 0.854 | **0.960** | 0.80 | **0.873** |
| DeepSeek-Coder-33B | ~0.72† | 0.947 | — | **0.808** | 0.73 | — (Open-source option) |
| DeepSeek-V3 | ~0.75† | ~0.90† | ~0.65† | **0.786** | 0.80 | **1.420** |
| Gemini 3.1 Pro | ~0.85† | — | 0.667 | **0.780** | 0.73 | **0.750** |
| Qwen3-Coder 480B | ~0.70† | — | — | **0.700** | 0.53 | **1.155** |

### JavaScript / TypeScript Debugging

**Benchmarks:** Aider Polyglot JS subset (0.60), LiveCodeBench (0.25 — JS problems), SWE-Pro (0.15)

| Model | Aider JS norm | LCB norm | W | C | W$ |
|-------|--------------|----------|---|---|-----|
| GPT-5 high | 1.000 | ~0.91† | **0.965** | 0.80 | **0.879** |
| Gemini 2.5 Pro | ~0.90† | 0.981 | **0.938** | 0.80 | **0.904** |
| o3 | ~0.88† | ~0.75† | **0.840** | 0.73 | **0.963** |
| Claude Sonnet 4.6 | ~0.82† | ~0.82† | **0.820** | 0.73 | **0.741** |
| DeepSeek-V3 | 0.745 | 1.000 | **0.847** | 0.80 | **1.529** |
| Qwen3-Coder 480B | 0.618 | ~0.80† | **0.700** | 0.60 | **1.155** |

### Rust Debugging

**Benchmarks:** Aider Polyglot Rust subset (0.65), LiveCodeBench (0.35)
Rust is memory-safe but has complex borrow-checker errors; strong type-system understanding matters.

| Model | Aider Rust norm | LCB norm | W | C | W$ |
|-------|----------------|----------|---|---|-----|
| GPT-5 high | 1.000 | ~0.91† | **0.968** | 0.80 | **0.881** |
| o3 | ~0.92† | ~0.75† | **0.863** | 0.73 | **0.988** |
| Gemini 2.5 Pro | ~0.88† | 0.981 | **0.916** | 0.80 | **0.882** |
| DeepSeek-V3.2 | 0.745 | 1.000 | **0.834** | 0.80 | **1.506** |
| Claude Opus 4.8 | ~0.82† | ~0.85† | **0.832** | 0.73 | **0.620** |

Niche note: Rust borrow-checker diagnostics are highly structured; models that follow compiler error chains perform best. o3 and GPT-5 show strength here.

### Go Debugging

**Benchmarks:** Aider Polyglot Go subset (0.65), CONCUR (0.35 — Go concurrency common)

| Model | Aider Go norm | CONCUR est. | W | C | W$ |
|-------|--------------|-------------|---|---|-----|
| GPT-5 high | 1.000 | HIGH† | **0.950** | 0.67 | **0.865** |
| Gemini 2.5 Pro | ~0.90† | MED-HIGH† | **0.855** | 0.60 | **0.823** |
| o3 | ~0.88† | HIGH† | **0.892** | 0.60 | **1.022** |
| DeepSeek-R1 | ~0.72† | MED† | **0.720** | 0.53 | **1.634** |
| Claude Opus 4.8 | ~0.82† | MED† | **0.785** | 0.60 | **0.585** |

### C / C++ Debugging

**Benchmarks:** Aider Polyglot C++ subset (0.50), DebugBench C++ (0.35), SWE-Pro (0.15)

| Model | Aider C++ norm | DebugBench C++ norm | SWE-Pro norm | W | C | W$ |
|-------|---------------|--------------------|----|---|---|-----|
| GPT-5 high | 1.000 | 1.000† | 0.854 | **0.976** | 0.80 | **0.889** |
| o3 | ~0.92† | — | ~0.55† | **0.796** | 0.67 | **0.912** |
| DeepSeek-V3.2 | 0.745 | 0.972 | ~0.65† | **0.815** | 0.80 | **1.472** |
| Gemini 2.5 Pro | ~0.88† | — | 0.667 | **0.813** | 0.73 | **0.783** |
| Claude Opus 4.8 | ~0.82† | — | 1.000 | **0.876** | 0.73 | **0.653** |
| WizardCoder-33B | ~0.67† | 0.831 | — | **0.734** | 0.67 | — (Open; no API price) |

### Java Debugging

**Benchmarks:** Aider Polyglot Java subset (0.55), DebugBench Java (0.45)

| Model | Aider Java norm | DebugBench Java norm | W | C | W$ |
|-------|----------------|---------------------|---|---|-----|
| DeepSeek-Coder-33B | ~0.72† | 1.000 | **0.846** | 0.73 | — (Java leader on DebugBench) |
| GPT-5 high | 1.000 | 1.000† | **1.000** | 0.73 | **0.910** |
| DeepSeek-V3 | ~0.75† | ~0.95† | **0.847** | 0.80 | **1.529** |
| Claude Opus 4.8 | ~0.82† | — | **0.820** | 0.67 | **0.611** |
| Gemini 3.1 Pro | ~0.85† | — | **0.850** | 0.67 | **0.818** |
| Llama3-70B | ~0.68† | 0.778 | **0.718** | 0.73 | **1.532** (Open; self-host) |

---

## Summary: Recommended Routing Table

This table gives the **top-3 recommendations** per sub-category for quality and for W$ (cost-adjusted quality). Intended as seed values for `AdaptiveRouter`'s `TaskType`-keyed profile routing before empirical data accumulates.

| Sub-category | TaskType key | Best quality (top-3) | Best W$ (top-3) | Notes |
|---|---|---|---|---|
| General bug-fix | `BUG_FIX` | Opus 4.8, GPT-5.4, Gemini 3.1 Pro | DeepSeek-V3, o4-mini, Haiku 4.5 | |
| Concurrency/race | `DEBUGGING` | GPT-5.4, o3, Gemini 2.5 Pro | DeepSeek-R1, o4-mini, Qwen3-Coder | LOW_CONF |
| Memory/leak | `DEBUGGING` | GPT-5, Opus 4.8, Gemini 2.5 Pro | DeepSeek-V3, DeepSeek-R1, o4-mini | LOW_CONF |
| Security/vuln | `SECURITY_FIX` | GPT-5.4, Claude Sonnet 4.6, Opus 4.8 | o3/o4-mini, DeepSeek-R1, Sonnet 4.6 | |
| Performance | `OPTIMIZATION` | GPT-5.4, Opus 4.8, Gemini 3.1 Pro | DeepSeek-V4, DeepSeek-R1, o4-mini | |
| Root-cause/logs | `DEBUGGING` | Opus 4.8, GPT-5.4, Gemini 3.1 Pro | DeepSeek-V3, DeepSeek-R1, Qwen3.7 | Use Opus 1M ctx |
| Python | `BUG_FIX` | GPT-5.4, Opus 4.8, Gemini 3.1 Pro | DeepSeek-V3, Qwen3-Coder, o4-mini | |
| JS/TS | `BUG_FIX` | GPT-5, Gemini 2.5 Pro, o3 | DeepSeek-V3, Qwen3-Coder, o3 | |
| Rust | `BUG_FIX` | GPT-5, Gemini 2.5 Pro, o3 | DeepSeek-V3, o3, Gemini 2.5 Pro | |
| Go | `BUG_FIX` | GPT-5, o3, Gemini 2.5 Pro | DeepSeek-R1, o3, DeepSeek-V3 | |
| C/C++ | `BUG_FIX` | GPT-5, Claude Opus 4.8, Gemini 2.5 Pro | DeepSeek-V3, o3, Gemini 2.5 Pro | |
| Java | `BUG_FIX` | GPT-5, DeepSeek-V3, Gemini 3.1 Pro | DeepSeek-V3, Llama3-70B, DeepSeek-Coder | DeepSeek Java standout |

---

## Gludd Integration Notes

The `AdaptiveRouter` in `src/general_ludd/scoring/router.py` selects `model_profile_id` based
on historical `BenchmarkResult` rows (written by `AutoBenchmarkRecorder`) keyed on
`task_type` (a `TaskType` StrEnum). The composite score formula is:

```python
composite = completion×0.35 + code_quality×0.25 + instruction×0.25 + token_efficiency×0.15
```

(defined in `src/general_ludd/schemas/benchmark.py::BenchmarkScores.composite_score`)

**Relevant `TaskType` values for this document:**
- `debugging` — root-cause/log analysis, generic debug
- `bug_fix` — per-language and general bug-fix
- `security_fix` — CVE patching, vuln finding
- `optimization` — performance profiling / hotspot fixes

**Prior data state:** Per GLM_REMEDIATION_GUIDE_3.md §1.4 (item H7 FIXED), the
`AutoBenchmarkRecorder` is wired in `daemon.py:443-446`. The `AdaptiveRouter` will use
live benchmark data once tasks flow through the system. Until then, it returns
`fallback=True / reason="insufficient_historical_data"` and falls back to the
`default_model_profile`. The weights in this document are intended as **prior seed values**
for operator-configured model profiles, not as runtime weights the code reads directly.

**Suggested operator action:** configure model profiles with `quality_class` and
`role_names` matching the sub-categories above, then let the `AdaptiveRouter`'s
empirical loop refine from these priors.

---

## Low-Confidence Cells and Known Gaps

| Gap | Impact | Mitigation |
|-----|--------|-----------|
| CONCUR per-model scores not extracted (404 leaderboard) | Concurrency/race sub-category weights are estimates | Re-check https://concur-bench.github.io when available |
| Aider Polyglot per-language breakdown not published | Per-language weights use total score as proxy | Use language-tagged SWE-bench Pro splits when available |
| Claude Opus 4.x absent from Aider leaderboard | Opus cells marked †estimated | Anthropic has not submitted; community estimate used |
| CyberSecEval 4 comparative table not public | Security sub-category uses ZeroDayBench + point estimates | Monitor meta-llama/PurpleLlama releases |
| Memory/leak: no dedicated multi-model benchmark found | Memory sub-category is entirely proxy-based | LAMeD study covers annotation quality, not fix quality |
| Pricing for Gemini 3 series (3.0, 3.5) not confirmed | Gemini 3 Pro may be different from 3.1 Pro | Used Gemini 3.1 Pro $2/$12 as best available |
| SWE-bench contamination | Verified scores overstate real-world capability by ~35pp | Use SWE-Pro (Scale) as primary signal |

---

## Sources

[1]: https://www.codeant.ai/blogs/swe-bench-scores "SWE-bench Leaderboard 2026 — codeant.ai"
[2]: https://www.morphllm.com/best-ai-model-for-coding "Best AI Model for Coding June 2026 — morphllm.com"
[3]: https://aider.chat/docs/leaderboards/ "Aider LLM Leaderboards — aider.chat"
[4]: https://www.codesota.com/llm "LLM Benchmark Leaderboard 2026 — CodeSOTA"
[5]: https://arxiv.org/abs/2401.04621 "DebugBench: Evaluating Debugging Capability of LLMs — arxiv"
[6]: https://arxiv.org/abs/2603.03683 "CONCUR: Benchmarking LLMs for Concurrent Code Generation — arxiv"
[7]: https://arxiv.org/html/2603.02297v1 "ZeroDayBench: LLM Agents on Unseen Zero-Day Vulnerabilities — arxiv"
[8]: https://github.com/meta-llama/PurpleLlama/tree/main/CybersecurityBenchmarks "CyberSecEval 4 — meta-llama/PurpleLlama GitHub"
[9]: https://whatllm.org/best-llm-for-coding "Best LLM for Coding 2026 — WhatLLM.org"
[10]: https://www.cloudzero.com/blog/claude-api-pricing/ "Anthropic Claude API Pricing 2026 — CloudZero"
[11]: https://www.metacto.com/blogs/unlocking-the-true-cost-of-openai-api-a-deep-dive-into-usage-integration-and-maintenance "OpenAI API Pricing May 2026 — MetaCTO"
[12]: https://www.aipricing.guru/google-ai-pricing/ "Google Gemini API Pricing June 2026 — AI Pricing Guru"
[13]: https://openrouter.ai/google/gemini-2.5-flash "Gemini 2.5 Flash — OpenRouter"
[14]: https://pricepertoken.com/pricing-page/model/google-gemini-2.5-pro "Gemini 2.5 Pro API Pricing 2026 — PricePerToken"
[15]: https://www.cloudzero.com/blog/deepseek-pricing/ "DeepSeek Pricing 2026 — CloudZero"
[16]: https://pricepertoken.com/pricing-page/model/deepseek-deepseek-r1 "DeepSeek R1 API Pricing 2026 — PricePerToken"
[17]: https://www.eesel.ai/blog/qwen-pricing "Qwen Pricing 2026 — eesel.ai"
[18]: https://deepinfra.com/blog/qwen-api-pricing-2026-guide "Qwen API Pricing Guide 2026 — DeepInfra"
[19]: https://www.burnwise.io/ai-pricing/mistral "Mistral API Pricing 2026 — BurnWise"
[20]: https://pricepertoken.com/pricing-page/model/meta-llama-llama-4-maverick "Llama 4 Maverick Pricing 2026 — PricePerToken"
[21]: https://openrouter.ai/meta-llama/llama-4-scout "Llama 4 Scout — OpenRouter"
[22]: https://llm-stats.com/benchmarks/swe-bench-verified "SWE-Bench Verified Leaderboard — llm-stats.com"
[23]: https://www.mindstudio.ai/blog/gpt-54-vs-claude-opus-46-vs-gemini-31-pro-benchmarks "GPT-5.4 vs Claude Opus 4.6 vs Gemini 3.1 Pro Benchmarks — MindStudio"
[24]: https://agentmarketcap.ai/blog/2026/04/06/aider-polyglot-leaderboard-2026-swe-bench-python-bias "Aider Polyglot Leaderboard 2026 — AgentMarketCap"
[25]: https://arxiv.org/html/2409.03031v1 "Debugging with Open-Source LLMs: An Evaluation — arxiv"
[26]: https://arxiv.org/html/2504.13474v1 "Everything You Wanted to Know About LLM-based Vulnerability Detection — arxiv"
