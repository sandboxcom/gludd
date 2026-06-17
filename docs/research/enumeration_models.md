# Enumeration Models: Cheap Reliable Structured Extraction

**Research date:** 2026-06-16
**Scope:** Candidate models, constrained-decoding mechanics, reliability techniques, and gludd wiring.

---

## 1. Why Enumeration Needs Its Own Model Role

Enumeration and structured extraction tasks (entity lists, key-value pairs, JSON object population, NER) have a fundamentally different cost/quality profile than generation tasks:

- Output token count is small and bounded by schema.
- Semantic richness matters less than structural fidelity.
- Failure mode is almost always schema violation or missing keys, not hallucination in the traditional sense.
- Downstream consumers are code, not humans — a missing brace crashes a parser.

LLMStructBench (2602.14743) found that "prompting strategy is more important than model size" for structured JSON generation, and that a 12B model (Gemma3-12B) outscored several 70B models on schema compliance. This unlocks a strong cost argument: a small constrained model wired to the right backend can match or beat a large unconstrained frontier model on extraction fidelity while costing 10-50x less.

---

## 2. Candidate Models

### 2.1 Hosted API tier (no ops overhead)

| Model | Provider | Input $/1M | Output $/1M | Context | Structured output | Latency class |
|---|---|---|---|---|---|---|
| GPT-4.1 nano | OpenAI | $0.10 | $0.40 | 128K | `response_format: json_schema` (native) | ultra-fast |
| GPT-4.1 mini | OpenAI | $0.40 | $1.60 | 128K | `response_format: json_schema` (native) | fast |
| Claude Haiku 4.5 | Anthropic | $0.80 | $4.00 | 200K | `output_config.format` (GA as of Nov 2025) | fast |
| Gemini 3.1 Flash-Lite | Google | $0.25 | $1.50 | 1M | `response_mime_type` + `response_schema` | fast |
| Gemini 3.1 Flash | Google | $0.35 | $1.05 | 1M | `response_mime_type` + `response_schema` | fast |
| Mistral Small | Mistral | $0.20 | $0.60 | 32K | `response_format: json_object` | fast |
| DeepSeek V3.2 / V4 Flash | DeepSeek/inference.net | $0.14 | $0.28 | 1M | OpenAI-compatible `response_format` | fast |
| Llama 4 Scout (Groq LPU) | Groq | $0.11 | $0.34 | — | `response_format: json_object` | ultra-fast |

Cost leader for pure extraction throughput: **GPT-4.1 nano ($0.10/$0.40)** and **DeepSeek V4 Flash ($0.14/$0.28)**. On a 500-token input + 200-token extraction output, a million calls costs roughly $0.18 (nano) vs $0.19 (DeepSeek). Gemini Flash-Lite ($0.25/$1.50) is competitive on input but loses on output-heavy schemas.

### 2.2 Open-weight self-hosted (with constrained decoding)

| Model | Params | VRAM (BF16) | Strong at | Constrained decoding backend |
|---|---|---|---|---|
| Qwen3-7B-Instruct | 7B | ~14 GB | JSON schema compliance (LLMStructBench top-3 at 7B tier) | vLLM xgrammar or Outlines; llama.cpp GBNF |
| Qwen3-14B-Instruct | 14B | ~28 GB | Best accuracy/cost open-weight sweet spot | vLLM xgrammar or Outlines |
| Gemma3-12B-Instruct | 12B | ~24 GB | Outscored several 70B models on LLMStructBench | vLLM Outlines (xgrammar fallback) |
| Llama3-8B-Instruct | 8B | ~16 GB | Entity extraction with constrained grammar; known to gludd deployment_optimizer | vLLM xgrammar / llama.cpp GBNF |
| Phi-4-mini (3.8B) | 3.8B | ~7 GB | Ultra-cheap extraction on CPU/small GPU | llama.cpp GBNF; Ollama |

LLMStructBench (2602.14743) tested 22 open-source models 0.6B–70B: "Choosing the right prompting strategy is more important than standard attributes such as model size," and "model scale alone does not ensure schema-compliant JSON generation." Missing Keys errors vanish at 8B+ under constrained decoding; Wrong Value errors persist and require the validate/repair backstop.

---

## 3. Constrained Decoding: How It Works

### 3.1 Concept

Constrained decoding intercepts the token logits at each generation step and masks tokens that would produce output violating the enforced constraint (JSON schema, regex, grammar). The model cannot produce malformed output because invalid continuations get probability 0 before sampling. This is fundamentally different from prompting ("please return JSON"), JSON mode (valid JSON syntax, any schema), and function calling (schema enforced by re-prompting on failure).

Benchmarks (2501.10868) show constrained decoding is ~50% faster than unconstrained for the same schema-valid output (fewer recovery retries), and yields 3–4 pp accuracy gains on reasoning tasks where format matters. The SciDC framework (2604.06603) found 12% average improvement over vanilla methods using local small models with constrained generation.

### 3.2 vLLM: `guided_json` / `guided_grammar` / `guided_choice`

vLLM exposes four guided decoding parameters in `extra_body` of any OpenAI-compatible call:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="none")

# Option A: JSON schema constraint
extraction_schema = {
    "type": "object",
    "properties": {
        "entities": {"type": "array", "items": {"type": "string"}},
        "labels": {"type": "array", "items": {"type": "string"}},
        "kvs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                "required": ["key", "value"],
                "additionalProperties": False
            }
        }
    },
    "required": ["entities", "labels", "kvs"],
    "additionalProperties": False
}

response = client.chat.completions.create(
    model="qwen3-7b-instruct",
    messages=[{"role": "user", "content": prompt}],
    extra_body={"guided_json": extraction_schema},
)

# Option B: GBNF grammar (llama.cpp style, also supported via xgrammar)
ENTITY_GRAMMAR = r"""
root   ::= "{" ws "\"entities\"" ws ":" ws entity-list "}"
entity-list ::= "[" ws (string ("," ws string)*)? ws "]"
string ::= "\"" ([^"\\] | "\\" .)* "\""
ws     ::= [ \t\n]*
"""
response = client.chat.completions.create(
    model="qwen3-7b-instruct",
    messages=[{"role": "user", "content": prompt}],
    extra_body={"guided_grammar": ENTITY_GRAMMAR},
)

# Option C: fixed-choice classification (zero overhead)
response = client.chat.completions.create(
    model="qwen3-7b-instruct",
    messages=[{"role": "user", "content": "Classify: " + text}],
    extra_body={"guided_choice": ["PERSON", "ORG", "LOCATION", "OTHER"]},
)

# Override backend explicitly if needed:
extra_body={"guided_json": schema, "guided_decoding_backend": "outlines"}
```

**Backend selection in vLLM:**

| Backend | Constraint types | Notes |
|---|---|---|
| xgrammar (default) | JSON schema, GBNF grammar | C-threaded PDA; up to 5x faster than Outlines under load; gaps: no regex, no non-GBNF grammars; falls back to Outlines automatically |
| Outlines | JSON schema, regex, GBNF, CFG | FSM-based; slower at batch scale but broader coverage; use `"guided_decoding_backend": "outlines"` to force |
| lm-format-enforcer | JSON schema, regex | Not recommended: fails in long-context cases, inferior to Outlines |

Overhead: xgrammar adds minimal latency on subsequent calls (schema compilation cached in C); first compile may add 50–200 ms. Outlines FSM compilation can be pre-warmed. No overhead on guided_choice.

### 3.3 llama.cpp: GBNF grammars

llama.cpp supports GBNF grammars natively via `--grammar-file` (CLI) or the `grammar` field in its HTTP API (`/completion`):

```bash
# CLI
llama-cli -m qwen3-7b-q4.gguf \
    --grammar-file entity_extract.gbnf \
    -p "Extract entities from: ..."

# HTTP server
curl http://localhost:8080/completion \
  -d '{
    "prompt": "Extract entities from: ...",
    "grammar": "root ::= \"[\" ws (entity (\",\" ws entity)*)? \"]\" ..."
  }'
```

GBNF is a subset of Backus-Naur Form. XGrammar (used by vLLM as default) also accepts GBNF format, so grammars written for llama.cpp port directly.

### 3.4 Hosted API providers: constrained decoding surface

| Provider | Parameter | Mechanism | Schema features not supported |
|---|---|---|---|
| OpenAI | `response_format: {"type": "json_schema", "json_schema": {...}}` | Constrained decoding (grammar compiled server-side) | Recursive schemas; numerical `minimum`/`maximum`; `additionalProperties` != false |
| Anthropic Claude | `output_config: {"format": {"type": "json_schema", "schema": {...}}}` + optional `strict: true` on tools | Constrained decoding (GA Nov 2025); requires `additionalProperties: false`, all fields in `required` | Recursive schemas; numerical constraints; complex regex assertions; max 20 strict tools; max 24 optional params |
| Google Gemini | `response_mime_type: "application/json"` + `response_schema: {...}` | Schema-guided generation; supports `anyOf`, `$ref` (Nov 2025 update) | See Gemini API docs |
| Mistral | `response_format: {"type": "json_object"}` | JSON mode (valid JSON, no schema enforcement); use function calling for schema enforcement | No native `json_schema` mode as of mid-2026 |
| vLLM (self-hosted) | `extra_body: {"guided_json": schema}` | xgrammar/Outlines constrained decoding | See backend-specific limits above |
| llama.cpp | `grammar` field in `/completion` | GBNF grammar | Only GBNF, not arbitrary JSON schema; must write grammar manually |

---

## 4. Reliability Techniques

### 4.1 Schema + validate/repair

Constrained decoding guarantees structural compliance but not semantic correctness. LLMStructBench found Wrong Value errors persist in ~50% of cases even for large models. The remedy is a two-layer approach:

1. **Constrained decoding** ensures the output is schema-valid JSON.
2. **Validate + repair** catches semantic errors (wrong type cast to string, out-of-range enum, empty required field).

The Instructor library (`instructor` Python package) implements automatic repair: it sends Pydantic validation errors back to the model as a follow-up prompt and retries up to N times. This is effective for minor issues but can loop on fundamentally broken schemas. Recommended: `max_retries=2` with a hard fallback on the third failure.

### 4.2 Self-consistency / voting for enumeration

For entity extraction where completeness matters (a missed entity is a bug), run the same extraction 3–5 times and take the union (or majority vote for classification). Since extraction output tokens are cheap, 3 passes at $0.10/1M is still cheaper than 1 pass at $0.80/1M. Practical recipe:

```python
results = [extract(chunk) for _ in range(3)]
# Union for lists:
entities = list({e for r in results for e in r["entities"]})
# Majority vote for single-value fields:
labels = Counter(r["label"] for r in results).most_common(1)[0][0]
```

### 4.3 Chunked extract + merge

Long documents exceed small model context windows (32K for Mistral Small, 128K for GPT-4.1 nano). Chunking strategy:

- Split on paragraph/sentence boundaries with 10% overlap.
- Extract independently from each chunk.
- Deduplicate entities by exact match or fuzzy match (Levenshtein < 0.15).
- Merge key-value dicts by key, keeping the first or highest-confidence value.

A 7B model on 500-token chunks at 50 chunks = 25K input tokens total per document, well within any model's context.

### 4.4 Schema discipline

LLMStructBench's highest-reliability prompting strategy ("P" = schema + one example in prompt) combined with schema enforcement in the API parameter consistently outperformed prompting alone by a large margin. Practically:

- Include the schema as a `### Output schema` block in the system prompt.
- Provide one concrete example (input → expected JSON output).
- Enable native constrained decoding via the API parameter.
- Set `additionalProperties: false` everywhere.
- Make every field `required`; use `"type": ["string", "null"]` for optional fields.

---

## 5. When a Small Constrained Model Beats a Large Unconstrained One

The LLMStructBench finding is the key proof point: Gemma3-12B with schema enforcement outscored several unconstrained 70B models. The conditions under which small + constrained wins:

1. **Output is schema-bounded.** The extraction target is a JSON object with a fixed schema. The model's job is fill-in-the-blank, not open-ended generation.
2. **Semantic content is extractable by pattern.** Entity names, dates, codes, enums — not multi-step reasoning.
3. **Volume is high.** At $0.10/1M input tokens, you can run 100M extraction calls for $10 + $40 output. The same job at GPT-4o ($2.50/$10.00) costs $12,500.
4. **Latency matters.** Small models on local hardware (7B @ 4-bit on consumer GPU) have 20–50 ms time-to-first-token vs 500+ ms for frontier API calls.
5. **Schema is fully specified.** If the schema is ambiguous or requires judgment calls (e.g., "extract the most relevant entities"), larger models win. For schema-precise tasks, constrained decoding removes the advantage.

---

## 6. Gludd Wiring Recommendation

### 6.1 New `enumerator` role in ModelProfile

Add a YAML profile `config/model_profiles/enumerator.yml` (or `config/model_profiles/enumerator-hosted.yml` for API and `enumerator-local.yml` for self-hosted):

```yaml
# config/model_profiles/enumerator-hosted.yml
model_profile_id: enumerator
provider: openai
provider_package: langchain-openai
provider_class_hint: ChatOpenAI
model_name: gpt-4o-mini            # or gpt-4.1-nano when available on your tier
role_names:
  - enumerator
  - extraction
latency_class: fast
quality_class: extraction
context_window: 128000
max_input_tokens: 120000
max_output_tokens: 4096
cost_per_input_token: 0.00000040   # $0.40/1M
cost_per_output_token: 0.0000016   # $1.60/1M
api_metered: true
run_budget_usd: 50.0
enabled: true
fallback_profiles:
  - enumerator-fallback

---
# config/model_profiles/enumerator-fallback.yml
model_profile_id: enumerator-fallback
provider: anthropic
provider_package: langchain-anthropic
provider_class_hint: ChatAnthropic
model_name: claude-haiku-4-5
role_names:
  - enumerator-fallback
latency_class: fast
quality_class: extraction
context_window: 200000
max_input_tokens: 190000
max_output_tokens: 4096
cost_per_input_token: 0.0000008    # $0.80/1M
cost_per_output_token: 0.000004    # $4.00/1M
api_metered: true
run_budget_usd: 50.0
enabled: true
```

For self-hosted vLLM (no ops cost per call):

```yaml
# config/model_profiles/enumerator-local.yml
model_profile_id: enumerator-local
provider: openai                   # vLLM is OpenAI-compatible
provider_package: langchain-openai
provider_class_hint: ChatOpenAI
model_name: qwen3-7b-instruct
api_base_alias: VLLM_BASE_URL      # resolves to http://localhost:8000/v1
role_names:
  - enumerator
  - extraction
latency_class: fast
quality_class: extraction
context_window: 32768
max_input_tokens: 30000
max_output_tokens: 4096
cost_per_input_token: 0.0          # self-hosted
cost_per_output_token: 0.0
api_metered: false
run_budget_usd: 0.0
enabled: true
```

### 6.2 Router wiring

The `ModelRouter.build_from_profiles()` method at `src/general_ludd/models/router.py:67` already populates the `role_mapping` from `profile.role_names`. Adding `role_names: [enumerator]` to a profile is all that is needed for:

```python
router.resolve_role("enumerator")  # → "enumerator" profile_id
```

Callers that need extraction then request it explicitly:

```python
gateway.call_model("enumerator", messages=extraction_messages)
# or via router:
profile_id = router.resolve_role("enumerator")
gateway.call_model(profile_id, messages=extraction_messages)
```

The `AdaptiveRouter` in `src/general_ludd/scoring/router.py` currently routes by `TaskType` (bug_fix, feature, etc.). Consider adding `TaskType.EXTRACTION = "extraction"` to `src/general_ludd/schemas/benchmark.py` to allow performance-based routing of extraction tasks: the `AdaptiveRouter` will prefer whichever enumerator profile has the highest `composite_score` in the benchmark repo at runtime.

### 6.3 Passing constrained-decoding params through the gateway

The gateway's `_invoke_and_bill` at `src/general_ludd/models/gateway.py:229` passes `**kwargs` through to `provider_cls(**init_kwargs)` and then calls `chat_model.invoke(lc_messages)`. The constrained-decoding surface differs by provider and LangChain class:

**OpenAI (`ChatOpenAI`) — `response_format` via `model_kwargs`:**

```python
from langchain_openai import ChatOpenAI

chat = ChatOpenAI(
    model="gpt-4o-mini",
    model_kwargs={
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "extraction_result",
                "strict": True,
                "schema": extraction_schema
            }
        }
    }
)
```

**Anthropic (`ChatAnthropic`) — `output_config` via extra headers / `model_kwargs`:**

Claude's structured output is exposed via the `output_config` field in the raw API request. In LangChain's `ChatAnthropic`, pass it via `model_kwargs`:

```python
from langchain_anthropic import ChatAnthropic

chat = ChatAnthropic(
    model="claude-haiku-4-5",
    model_kwargs={
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": extraction_schema
            }
        }
    }
)
```

**vLLM self-hosted (`ChatOpenAI` pointed at vLLM) — `extra_body`:**

```python
from langchain_openai import ChatOpenAI

chat = ChatOpenAI(
    model="qwen3-7b-instruct",
    base_url="http://localhost:8000/v1",
    api_key="none",
    model_kwargs={
        "extra_body": {
            "guided_json": extraction_schema,
            # or "guided_choice": [...] for classification
        }
    }
)
```

**Practical gateway integration path:**

The cleanest approach is to add an optional `constrained_decoding` dict field to `ModelProfile` (in `src/general_ludd/models/gateway.py`) that the gateway merges into `init_kwargs` at construct time. This keeps constraint parameters co-located with the model profile rather than at every call site:

```python
# Proposed addition to ModelProfile (gateway.py lines 48–93):
constrained_decoding: dict[str, Any] = Field(default_factory=dict)
# e.g. {"model_kwargs": {"extra_body": {"guided_json": {...}}}}
# or   {"model_kwargs": {"response_format": {"type": "json_schema", ...}}}
```

In `_invoke_and_bill` (line 255):

```python
init_kwargs: dict[str, Any] = {"model": profile.model_name}
# Merge constrained_decoding overrides
for k, v in profile.constrained_decoding.items():
    if k == "model_kwargs" and "model_kwargs" in init_kwargs:
        init_kwargs["model_kwargs"].update(v)
    else:
        init_kwargs[k] = v
```

### 6.4 output_schema as validate/repair backstop

The existing `output_schema` module (G14 validate/repair) is the right second layer. The call pattern for extraction should be:

1. Call the enumerator profile with constrained decoding (guaranteed schema-valid JSON from the model).
2. Parse and validate with the Pydantic schema — catches semantic errors (wrong enum value, empty required field).
3. On validation failure, run the repair path (re-prompt with validation errors) up to 2 times.
4. On persistent failure, log and return a partial result or raise with structured error.

This gives defense in depth: constrained decoding prevents parse failures; output_schema repair patches semantic gaps.

```python
from general_ludd.models.gateway import ModelGateway

async def extract_entities(
    gateway: ModelGateway,
    text: str,
    schema: dict,
    max_repair_attempts: int = 2,
) -> dict:
    profile_id = "enumerator"
    messages = [
        {"role": "system", "content": f"Extract entities. Return JSON matching this schema:\n{schema}"},
        {"role": "user", "content": text},
    ]
    for attempt in range(max_repair_attempts + 1):
        response = gateway.call_model(profile_id, messages=messages)
        try:
            import json
            result = json.loads(response.content)
            # Pydantic validate here against output_schema
            return result
        except Exception as e:
            if attempt < max_repair_attempts:
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": f"Validation failed: {e}. Fix and return corrected JSON."})
            else:
                raise
```

---

## 7. Decision Matrix

| Situation | Recommended approach |
|---|---|
| High-volume extraction, cost-sensitive, API-only | GPT-4.1 nano + `response_format: json_schema` |
| Extraction + long documents (>128K tokens) | Gemini 3.1 Flash-Lite + `response_schema` (1M context) |
| Self-hosted, GPU available, zero marginal cost | Qwen3-7B-Instruct + vLLM xgrammar `guided_json` |
| Self-hosted, CPU-only / edge | Phi-4-mini + llama.cpp GBNF grammar |
| High reliability required (medical, legal) | Gemma3-12B or Qwen3-14B + Outlines constrained + 3x voting |
| Classification only (fixed enum output) | Any model + `guided_choice` (zero schema overhead) |
| Already using Anthropic and want schema enforcement | Claude Haiku 4.5 + `output_config.format` + output_schema repair |
| Unknown extraction quality, adaptive routing wanted | Add `TaskType.EXTRACTION`, let AdaptiveRouter learn from benchmark results |

---

## Sources

- [LLM API Providers 2026 — morphllm.com](https://www.morphllm.com/llm-api)
- [LLM API Pricing Comparison 2026 — inference.net](https://inference.net/content/llm-api-pricing-comparison/)
- [LLMStructBench: Benchmarking LLM Structured Data Extraction (arXiv 2602.14743)](https://arxiv.org/html/2602.14743v1)
- [Generating Structured Outputs from Language Models: Benchmark and Studies (arXiv 2501.10868)](https://arxiv.org/html/2501.10868v1)
- [PARSE: LLM Driven Schema Optimization for Reliable Entity Extraction (arXiv 2510.08623)](https://arxiv.org/html/2510.08623v1)
- [Scientific Knowledge-driven Decoding Constraints Improving Reliability of LLMs (arXiv 2604.06603)](https://arxiv.org/html/2604.06603)
- [Structured Decoding in vLLM: A Gentle Introduction — vLLM Blog (Jan 2025)](https://vllm.ai/blog/2025-01-14-struct-decode-intro)
- [vLLM Structured Outputs Documentation v0.8.4](https://docs.vllm.ai/en/v0.8.4/features/structured_outputs.html)
- [Structured Outputs in vLLM — Red Hat Developer (Jun 2025)](https://developers.redhat.com/articles/2025/06/03/structured-outputs-vllm-guiding-ai-responses)
- [How Structured Outputs and Constrained Decoding Work — letsdatascience.com](https://letsdatascience.com/blog/structured-outputs-making-llms-return-reliable-json)
- [Claude Structured Outputs — Anthropic Platform Docs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- [Anthropic Launches Structured Outputs (Nov 2025) — techbytes.app](https://techbytes.app/posts/claude-structured-outputs-json-schema-api/)
- [lm-format-enforcer — GitHub](https://github.com/noamgat/lm-format-enforcer)
- [Structured Outputs Guide — agenta.ai](https://agenta.ai/blog/the-guide-to-structured-outputs-and-function-calling-with-llms)
- [Gemini 3.5 Flash vs Claude Haiku vs GPT-4o mini — dev.to](https://dev.to/alanwest/gemini-35-flash-vs-claude-haiku-vs-gpt-4o-mini-picking-a-small-model-52n4)
