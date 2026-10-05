# Compaction Model Research — Text Summarization as a Preprocessing Step

*Generated 2026-06-16. Prices verified via web sources; flag notes in-line where
a figure is inferred or unverified.*

---

## 1. Why Compaction Matters

gludd's `ContextCompactor.compact()` (`src/general_ludd/agents/context.py`) fires when
the token ratio crosses `compaction_threshold` (default 0.8). The default fallback
simply truncates at 500 characters — useful for tests, useless for production. The
hook is already there: `compact(messages, summary_fn=<callable>)` accepts any
`Callable[[str], str]`. Wiring a real LLM here is the only change needed on the
call-site.

The downstream models in gludd (`zai_example.yml`, `deepseek_coder.yml`,
`qwen_coder.yml`) are all mid-to-high-quality coders billed at $0.27–$3.00/M input
tokens. Any compaction that halves the context passed to them pays for itself
several times over if the compactor costs less than ~15 % of the downstream rate.

---

## 2. Candidate Compactor Models

All prices are per-million tokens (input / output) as of June 2026 unless noted.
Latency figures are representative p50 TTFT + generation speed from public
benchmarks; treat as relative guides, not SLAs.

### 2a. Hosted API models

| Model | Input $/M | Output $/M | Context | Latency class | Notes |
|---|---|---|---|---|---|
| **Gemini 2.5 Flash-Lite** | $0.10 | $0.40 | 1 M | Fast | Best cost/context ratio in this tier. Previously "2.0 Flash-Lite" (deprecated June 1 2026); identical pricing on 2.5 generation. [1][2] |
| **Gemini 2.5 Flash** | $0.30 | $2.50 | 1 M | Fast | More capable; 1 M ctx window is useful for very long compaction inputs. [2][3] |
| **Gemini 3.1 Flash-Lite** | $0.25 | $1.50 | ~1 M | Fast | Next-gen replacement; slightly pricier than 2.5 Flash-Lite but same positioning. [2] |
| **Claude Haiku 4.5** | $1.00 | $5.00 | 200 K | Fast | Best faithfulness among small Anthropic models; prompt caching reduces effective input cost ~70 % on repeated system prompts. [4][5] |
| **Claude Haiku 3.5** | $0.80 | $4.00 | 200 K | Fast | Legacy; superseded by 4.5 in quality; cheaper output. [4] |
| **GPT-4o mini** | $0.15 | $0.60 | 128 K | Fast | Strong instruction following; batch API halves both figures ($0.075/$0.30). [6] |
| **Mistral Small 3.1** | $0.20 | $0.60 | 128 K | Fast | 24B open-weight, available on la-plateforme; best cost among commercial small models. [7] |
| **Phi-4 Mini Instruct** | $0.08 | $0.35 | 131 K | Moderate | Cheapest hosted option; 21 tokens/sec on Microsoft API — adequate for batch compaction, tight for latency-sensitive paths. [8] |

### 2b. Open-weight models (self-hosted / serverless via Together / Fireworks)

| Model | Hosted $/M (in/out) | Params | Context | Faithfulness notes |
|---|---|---|---|---|
| **Qwen3-8B** | $0.10 / $0.24 (OpenRouter) | 8 B | 128 K | Instruction-following strong; non-thinking mode ideal for summarization. [9] |
| **Qwen3-14B** | ~$0.15 / ~$0.45 (est.) | 14 B | 128 K | Better coverage of long docs than 8B; pricing not independently verified — flagged. |
| **Llama 3.1 8B** | ~$0.20 / $0.20 flat (Fireworks) | 8 B | 128 K | MLPerf CNN-DailyMail baseline; good ROUGE/BERTScore in clinical summarization. Context length (128 K) is a step up from GPT-J. [10] |
| **Llama 3.3 70B** | $0.10 / $0.32 (various) | 70 B | 128 K | Higher quality than 8B; justified for high-sensitivity compaction. [11] |
| **Mistral Small 24B** | $0.03–$0.10 / $0.11–$0.30 | 24 B | 128 K | Wide price range by provider; strong faithfulness-to-context ratio for size. [7] |

*Self-hosted at scale*: Running Qwen3-14B or Llama-3.3 70B on owned H100s changes
the economics entirely — GPU-hour cost (2x H100 ≈ $4.80/hr for 14B) is below any
hosted per-token price past ~5 M tokens/day. Not relevant unless gludd operates at
that volume.

---

## 3. Faithfulness — Least Likely to Drop or Hallucinate Facts

### Why it matters for gludd

The compacted output becomes `[prior context] <summary>` injected as a system
message. If the summary invents or omits a tool-call result, the downstream coder
model can silently act on stale or wrong state.

### What the literature says (2025–2026)

1. **Extractive > abstractive for faithfulness** — extractive summarization copies
   verbatim spans, so hallucination is structurally impossible. The trade-off is
   verbosity: extractive summaries are longer for the same semantic coverage. [12]

2. **Map-reduce for long histories** — split old messages into overlapping chunks,
   summarize each with a cheap model (the "map" step), then merge chunk summaries
   into one (the "reduce" step). This respects model context limits and avoids
   the attention-dilution that degrades faithfulness on very long inputs. [13]

3. **Pinned spans** — the compactor already preserves `is_system=True` messages
   verbatim. The same logic should apply to any message tagged as "pinned" (e.g.
   a tool-call result the agent explicitly depends on). The current implementation
   does this correctly — system messages are kept outside the compaction window.

4. **Model-specific faithfulness ranking** (based on 2025 RAG benchmarks and
   summarization evals):
   - **Gemini 2.5 Flash / Flash-Lite** — strong grounding; Google trains explicitly
     on groundedness with RLHF feedback. Good for technical content.
   - **Claude Haiku 4.5** — Anthropic's Constitutional AI training reduces invented
     facts; strong on code-context summarization specifically.
   - **GPT-4o mini** — solid instruction adherence but slightly more prone to
     abstractive paraphrase drift than Claude models at this size tier.
   - **Qwen3 series** — non-thinking mode has lower hallucination rate than thinking
     mode for factual summarization; suitable when faithfulness matters more than
     reasoning.
   - **Llama 3.1 8B** — adequate for general text; higher hallucination rate on
     technical/code content vs. Qwen3-8B based on community evals. [10]

5. **Practical mitigations** regardless of model:
   - Prompt: "Summarize only what is stated in the input. Do not infer or add
     information. Preserve all function names, variable names, error messages,
     and numeric values verbatim."
   - Use a higher `temperature=0` or `top_p=0.1` for determinism.
   - For critical pipelines, run the summary through a lightweight
     entailment/groundedness check before injecting (e.g. NLI classifier or
     another cheap LLM call).

---

## 4. When Compaction Pays Off vs. When It Hurts

### Decision rule

```text
Let:
  C_down   = downstream cost per input token ($)
  C_comp   = compactor cost per output token ($)  [output = what goes to downstream]
  R        = compression ratio (output tokens / input tokens), typically 0.1–0.4
  S        = sensitivity weight [0.0 = lossy OK, 1.0 = must preserve all facts]

Compaction saves money when:
  C_down * (1 - R) > C_comp * R + C_comp_input * 1.0

Simplified threshold (ignoring compactor input cost as usually small):
  C_down / C_comp > R / (1 - R)

For R=0.25 (75% compression):   need C_down > 0.33 * C_comp
For R=0.50 (50% compression):   need C_down > 1.0  * C_comp  (break-even)
For R=0.15 (85% compression):   need C_down > 0.18 * C_comp  (very favorable)
```

**Practical rule of thumb**: if the downstream model costs 3x or more than the
compactor, compaction almost always pays — even at moderate compression ratios.

### When it pays

| Scenario | Verdict |
|---|---|
| Long agent history (> 20 K tokens) + expensive downstream (Claude Sonnet, GPT-4o, Gemini 2.5 Pro) | Strongly worth it; cost delta is 5–20x |
| Batch mode (offline summarization of many conversations) | Batch API pricing (50% off) makes even modest compression profitable |
| Repeated system prompt injection (caching applies to compactor too) | Cache the compactor system prompt; gludd already benefits from prompt caching on downstream |
| Downstream is a cheap small model (GPT-4o mini, Haiku 4.5) | Marginal — compaction overhead may exceed savings unless history is very long |

### When it hurts

| Scenario | Risk |
|---|---|
| Short histories (< 5 K tokens, under threshold anyway) | Compaction won't trigger; no issue |
| Task requires exact reproduction of prior tool output (debugging sessions, exact error traces) | Summary may lose a stack-frame or numeric value; use extractive or pin the relevant messages |
| Downstream is another compactor (nested summarization) | Double-lossy; avoid chaining two abstractive passes |
| Context is mostly code with exact identifiers | Abstractive models rewrite variable names; use extractive chunks or literal preservation prompt |
| Very low token budgets on the compactor itself | If compactor input > compactor context window, need chunking (map-reduce) |

---

## 5. Recommendations for gludd

### 5a. Recommended compactor model

**Primary: Gemini 2.5 Flash-Lite** (`gemini-2.5-flash-lite-001`)
- $0.10/$0.40 per M tokens — 3–10x cheaper than gludd's current downstream models
- 1 M token context — handles even the most bloated agent histories in a single pass
- Fast TTFT; no quality or faithfulness penalty vs. Flash on summarization tasks
- Fallback: **GPT-4o mini** ($0.15/$0.60) — already uses OpenAI-compatible provider
  class that gludd's gateway supports natively via `langchain_openai.ChatOpenAI`

For API-key-free / self-hosted deployments: **Qwen3-8B** via Fireworks or local
ollama ($0.10/$0.24 hosted; $0 self-hosted). Faithfulness is adequate for code
context when paired with a faithfulness-preserving prompt.

### 5b. Model profile YAML

Add `config/model_profiles/gemini_compactor.yml`:

```yaml
model_profile_id: gemini_compactor
role_names:
  - compactor
provider: openai          # Gemini exposes an OpenAI-compatible endpoint
provider_package: langchain_openai
provider_class_hint: ChatOpenAI
model_name: gemini-2.5-flash-lite-001
credential_alias: GEMINI_API_KEY
api_base_alias: GEMINI_BASE_URL   # https://generativelanguage.googleapis.com/v1beta/openai/
context_window: 1000000
max_input_tokens: 900000
max_output_tokens: 8192
cost_per_input_token: 0.0000001   # $0.10/M
cost_per_output_token: 0.0000004  # $0.40/M
api_metered: true
run_budget_usd: 5.0
enabled: true
resource_profile: ai_light
roles:
  - compactor
latency_class: fast
quality_class: medium
fallback_profiles:
  - gpt4o_mini_compactor
probe_enabled: false
```

Add `config/model_profiles/gpt4o_mini_compactor.yml` as the fallback:

```yaml
model_profile_id: gpt4o_mini_compactor
role_names:
  - compactor
provider: openai
provider_package: langchain_openai
provider_class_hint: ChatOpenAI
model_name: gpt-4o-mini
credential_alias: OPENAI_API_KEY
context_window: 128000
max_input_tokens: 120000
max_output_tokens: 4096
cost_per_input_token: 0.00000015   # $0.15/M
cost_per_output_token: 0.0000006   # $0.60/M
api_metered: true
run_budget_usd: 5.0
enabled: true
resource_profile: ai_light
roles:
  - compactor
latency_class: fast
quality_class: medium
fallback_profiles: []
probe_enabled: false
```

### 5c. Wiring into `ContextCompactor.compact()`

The `compact()` method already accepts `summary_fn: Callable[[str], str] | None`.
The wiring point is `AgentCapabilities.prepare_messages()` in
`src/general_ludd/agents/capabilities.py` — it calls `self.compactor.compact(msgs)`
with no `summary_fn`. Add one:

```python
# In AgentCapabilities.__init__, add:
#   compactor_gateway: ModelGateway | None = None
#   compactor_profile_id: str = "gemini_compactor"

def _make_summary_fn(self) -> Callable[[str], str] | None:
    if self._compactor_gateway is None:
        return None
    profile_id = self._compactor_profile_id

    SYSTEM_PROMPT = (
        "You are a context compactor. Summarize the conversation history below "
        "into a compact but complete summary. Preserve all function names, "
        "variable names, error messages, file paths, and numeric values verbatim. "
        "Do not infer or add information. Output only the summary, no preamble."
    )

    def summary_fn(text: str) -> str:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]
        resp = self._compactor_gateway.call_model_with_fallback(
            profile_id, messages
        )
        return resp.content

    return summary_fn
```

Then in `prepare_messages`:

```python
compacted = self.compactor.compact(msgs, summary_fn=self._make_summary_fn())
```

### 5d. Routing via AdaptiveRouter

The `AdaptiveRouter` routes by `TaskType` (a StrEnum in `schemas/benchmark.py`).
`TaskType` does not currently have a `COMPACTION` variant. Two options:

**Option A (preferred)**: Add `COMPACTION = "compaction"` to `TaskType`. Then
register benchmark results for the compactor profiles under this task type. The
router will learn — after `min_samples=3` runs — which compactor has the best
composite score (faithfulness proxy: `instruction_adherence_score`, efficiency:
`token_efficiency_score`).

**Option B (simpler, no schema change)**: Bypass `AdaptiveRouter` for compaction
entirely. Use `call_model_with_fallback("gemini_compactor", ...)` directly in
`_make_summary_fn`. The gateway's built-in `fallback_profiles` list handles the
`gpt4o_mini_compactor` fallback. This is lower overhead and appropriate given
compaction is infrastructure, not a task that needs cross-task scoring.

**Recommendation**: Option B initially. Option A once compaction runs at volume
and you want data-driven model selection.

### 5e. Map-reduce for very long histories

gludd's current compactor does a single-pass concatenation of old messages. For
histories that exceed the compactor's context window (unlikely with Gemini 2.5
Flash-Lite's 1 M ctx, but possible with the GPT-4o mini fallback at 128 K), add
chunked map-reduce:

```python
def _map_reduce_summary(text: str, gateway, profile_id, chunk_tokens=50000) -> str:
    chars_per_chunk = chunk_tokens * 4   # rough token estimate
    chunks = [text[i:i+chars_per_chunk] for i in range(0, len(text), chars_per_chunk)]
    if len(chunks) == 1:
        return _call_compactor(text, gateway, profile_id)
    chunk_summaries = [_call_compactor(c, gateway, profile_id) for c in chunks]
    merged = "\n---\n".join(chunk_summaries)
    return _call_compactor(merged, gateway, profile_id)
```

This is the standard "refine" variant of map-reduce for summarization and prevents
context overflow in the compactor itself.

---

## 6. Summary Recommendations Table

| Criterion | Recommendation |
|---|---|
| Primary compactor | Gemini 2.5 Flash-Lite (`gemini_compactor` profile) |
| Fallback compactor | GPT-4o mini (`gpt4o_mini_compactor` profile) |
| Self-hosted / no API key | Qwen3-8B (ollama or Fireworks) |
| Compaction strategy | Single-pass abstractive for < 800 K tokens; map-reduce above that |
| Faithfulness prompt | Preserve identifiers/numbers verbatim; no inference |
| Wiring point | `AgentCapabilities._make_summary_fn()` injected into `compact()` |
| Router integration | Option B (direct fallback chain) initially; add `TaskType.COMPACTION` later |
| When NOT to compact | History < 5 K tokens; exact error traces pinned; downstream is already cheap |
| Break-even rule | Downstream 3x+ more expensive than compactor → compact. Below that, measure. |

---

## Sources

1. [Gemini 2.0 Flash Lite deprecated — migration guide 2026 (TokenCost)](https://tokencost.app/blog/gemini-2-0-flash-deprecated-migration-cost)
2. [Gemini Developer API Pricing — Google AI for Developers](https://ai.google.dev/gemini-api/docs/pricing)
3. [Gemini 2.5 Flash — OpenRouter](https://openrouter.ai/google/gemini-2.5-flash)
4. [Introducing Claude Haiku 4.5 — Anthropic](https://www.anthropic.com/news/claude-haiku-4-5)
5. [Claude API Pricing Calculator 2026 (InvertedStone)](https://invertedstone.com/calculators/claude-pricing)
6. [GPT-4o Mini Pricing — PECollective](https://pecollective.com/tools/gpt-4o-mini-pricing/)
7. [Mistral Small 3.1 API Pricing (DevTK)](https://devtk.ai/en/models/mistral-small-3-1/)
8. [Phi-4 Mini Instruct — OpenRouter](https://openrouter.ai/microsoft/phi-4-mini-instruct)
9. [Qwen3-8B API Pricing 2026 (PricePerToken)](https://pricepertoken.com/pricing-page/model/qwen-qwen3-8b)
10. [MLPerf Inference 5.1: Benchmarking Small LLMs with Llama3.1-8B (MLCommons)](https://mlcommons.org/2025/09/small-llm-inference-5-1/)
11. [Llama 3.3 70B Instruct API Pricing 2026 (PricePerToken)](https://pricepertoken.com/pricing-page/model/meta-llama-llama-3.3-70b-instruct)
12. [A hallucination detection and mitigation framework for faithful text summarization using LLMs (Nature/Scientific Reports)](https://www.nature.com/articles/s41598-025-31075-1)
13. [Master LLM Summarization Strategies (Galileo.ai)](https://galileo.ai/blog/llm-summarization-strategies)

*Unverified figures: Qwen3-14B hosted pricing is estimated from Qwen3-8B and
Qwen3-30B bracket prices; no direct source found. Mistral Small 3 context window
(128 K vs 33 K) shows conflicting sources — use 128 K from official Mistral docs
as authoritative. Gemini 3.1 Flash-Lite specs are preliminary (fetched from live
Google pricing page).*
