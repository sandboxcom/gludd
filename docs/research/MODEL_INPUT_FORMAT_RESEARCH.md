# Model Input Format Research

**Date:** 2026-07-25
**Purpose:** Document the preferred input formats, context structuring conventions,
and behavioral quirks of 6 major AI model providers for use by gludd's prompt
rendering and model routing systems.

Each format profile is stored as a YAML file in `config/model_profiles/formats/`.

---

## 1. Anthropic Claude

### Official Documentation
- [Prompt Engineering Overview](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview)
- [Use XML Tags](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/use-xml-tags)
- [Prompting Best Practices](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/claude-prompting-best-practices)

### Preferred System Prompt Format
**XML-structured.** Claude's official documentation explicitly recommends XML
tags for structuring prompts. The pattern is:

```xml
<instructions>Clear, direct instructions</instructions>
<context>Background information</context>
<examples>
  <example>Few-shot examples</example>
</examples>
<input>Variable user input</input>
```

The system prompt is a **first-class API parameter** (`system`), not a message role.

### Context Structuring
- **Documents at TOP, query at BOTTOM:** Tests show up to 30% quality improvement
  when long documents are placed at the top of the prompt, with the query at the
  bottom.
- **XML-wrapped documents:** Each document in `<document index="N">` with
  `<source>` and `<document_content>` subtags.
- **Quote extraction:** When working with long documents, ask Claude to extract
  relevant quotes into `<quotes>` tags before producing the answer.

### Role/Separator Tokens
- Standard `system` / `user` / `assistant` roles.
- System prompt is NOT a message — it's a separate API parameter.
- Content blocks: text, tool_use, tool_result, thinking, image.

### Maximum Effective Prompt Length
| Parameter | Value |
|-----------|-------|
| Context window | 200,000 tokens |
| Practical input limit | ~175,000 tokens |
| Max output tokens | 16,000 (Sonnet/Opus) |
| Prompt caching | Supported (prefix-based) |

### Known Quirks
1. **Prefilled responses deprecated** in Claude 4.6+ — use structured outputs or
   direct instructions instead.
2. **Long data at top improves quality** by up to 30%.
3. **Opus 4.6 over-engineers** — creates extra files, unnecessary abstractions.
   Mitigate with anti-overengineering guard prompts.
4. **Newer models skip verbal summaries** after tool calls — add "provide a quick
   summary of the work" if visibility is needed.
5. **Opus 5 verbosity** is higher than prior models; prompt for conciseness.
6. **"think" word** triggers extended thinking on Opus 4.5; use "consider" or
   "evaluate" instead.
7. **Adaptive thinking** is default on Opus 5+ (not budget_tokens).
8. **3-5 examples** wrapped in `<example>` / `<examples>` tags is the optimal
   few-shot pattern.
9. **Positive framing:** Claude responds better to "do X" than "don't do Y."

### Citations
Claude supports **quote-grounded responses** via `<quotes>` XML tags. The
recommended pattern:
1. Ask Claude to find relevant quotes from documents.
2. Have Claude place quotes in `<quotes>` tags.
3. Then produce the answer based on those quotes.

### Multi-Turn Conversation
Standard system/user/assistant format. System prompt is persistent (not repeated
each turn). Claude has context awareness on latest models — it knows its token
budget and can manage context compression.

### Profile
- `config/model_profiles/formats/anthropic_claude.yml`

---

## 2. OpenAI GPT

### Official Documentation
- [Prompt Engineering](https://platform.openai.com/docs/guides/prompt-engineering)
- [Structured Outputs](https://platform.openai.com/docs/guides/structured-outputs)
- [Model Spec — Chain of Command](https://model-spec.openai.com/2025-02-12.html#chain_of_command)

### Preferred System Prompt Format
**Markdown + XML hybrid.** OpenAI's official examples use a combination:

```markdown
# Identity
You are a coding assistant that helps with Python.

# Instructions
* When defining variables, use snake_case.
* Use type annotations wherever possible.

# Examples
<user_query>How do I declare a string?</user_query>
<assistant_response>var name: str = "Alice"</assistant_response>
```

OpenAI uses the **`developer` role** (not `system`) for highest-priority
instructions. The `instructions` parameter in the Responses API is equivalent
to a `developer` message.

### Context Structuring
- Markdown headers (`### Section`) for major organizational sections.
- XML tags for content block demarcation within sections.
- Context typically goes near the **end** of the developer message.
- Content at the **start** of the prompt is eligible for prompt caching.

### Role/Separator Tokens
- `developer`: Highest-priority instructions (replaces `system`).
- `user`: End-user input (lower priority than `developer`).
- `assistant`: Model responses.
- Chain of command: developer > user > assistant.

### Maximum Effective Prompt Length
| Parameter | Value |
|-----------|-------|
| Context window | 128,000 (GPT-4), up to 1M (GPT-4.1) |
| Practical input limit | ~110,000 tokens |
| Max output tokens | 8,000 (GPT-4), 32,768 (GPT-4.1) |
| Prompt caching | Supported (automatic, prefix-based) |

### Known Quirks
1. **Lost in the middle:** GPT models attend less to content in the middle of
   very long prompts. Place critical instructions at the beginning or end.
2. **`developer` > `system`:** The `developer` role replaced `system` as the
   authoritative role. `system` still works but is deprecated.
3. **Reasoning models (o-series)** need different prompting — less explicit
   instruction, more objective description of the task.
4. **GPT-3.5 focuses on last portion** of prompt; GPT-4+ has more even attention
   but the recency bias still exists.
5. **Structured Outputs** (json_schema) is preferred over JSON mode for schema
   adherence. Reliable type-safety.
6. **Few-shot examples:** 3-5 diverse examples in XML-wrapped format.

### Citations
OpenAI supports **text annotations** with citation data in the Responses API.
In standard chat format, use inline `[source]` markers or `<citation>` XML tags.
There is a dedicated [Citation Formatting](https://platform.openai.com/docs/guides/citation-formatting) guide.

### Multi-Turn Conversation
- Chat Completions API: Full message array with alternating user/assistant.
- Responses API: `previous_response_id` for conversation state management.
- `instructions` apply per-request, not across turns.

### Profile
- `config/model_profiles/formats/openai_gpt.yml`

---

## 3. DeepSeek

### Official Documentation
- [API Docs — First Call](https://api-docs.deepseek.com/)
- [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode)
- [Anthropic API Compatibility](https://api-docs.deepseek.com/guides/anthropic_api)

### Preferred System Prompt Format
**OpenAI-compatible with thinking mode.** DeepSeek uses standard OpenAI chat
format with `system` / `user` / `assistant` roles. Also supports Anthropic API
format. Both protocols are first-class.

The defining feature is **thinking mode** (`reasoning_content`), which generates
a chain-of-thought before the final answer.

### Context Structuring
- OpenAI-compatible messages array.
- System prompt via `system` role at messages[0].
- No official XML tag preference — standard markdown works well.
- Context blocks in user messages with markdown section headers.

### Role/Separator Tokens
- `system`: System-level instructions.
- `user`: User input.
- `assistant`: Model response (contains `content` + optional `reasoning_content`).
- `tool`: Tool call results.

### Maximum Effective Prompt Length
| Parameter | Value |
|-----------|-------|
| Context window | 65,536 tokens |
| Practical input limit | ~56,000 tokens (budget for thinking overhead) |
| Max output tokens | 8,192 tokens |
| Prompt caching | Supported (KV cache, prefix-based) |

### Known Quirks
1. **Thinking mode defaults to ENABLED** on DeepSeek-V4. You must explicitly
   disable it if not wanted.
2. **CoT from prior turns is IGNORED** by the API (unless tool calls occurred)
   — this simplifies multi-turn context management.
3. **CoT WITH tool calls MUST be passed back** in subsequent requests, or the
   API returns a 400 error.
4. **Thinking mode disables** temperature, top_p, presence_penalty, and
   frequency_penalty.
5. **Dual protocol:** Supports both OpenAI and Anthropic API formats.
6. **Model mapping:** claude-opus → deepseek-v4-pro, claude-sonnet/haiku →
   deepseek-v4-flash.
7. **reasoning_effort mapping:** low/medium → high, xhigh → max.
8. **Chinese-friendly:** Mixed CN/EN prompts handled naturally.

### Citations
No built-in citation system. Use inline `[source]` markers.

### Multi-Turn Conversation
**Critical difference:** In thinking mode without tool calls, historical
`reasoning_content` is NOT concatenated into subsequent turns. The API
automatically ignores it. This means:
- Multi-turn chats are cheaper (no thinking token accumulation).
- But the model may lose deep reasoning context between turns.

With tool calls, `reasoning_content` must be preserved and passed back.

### Profile
- `config/model_profiles/formats/deepseek.yml`

---

## 4. Qwen (Alibaba)

### Official Documentation
- [Qwen3 Model Card](https://huggingface.co/Qwen/Qwen3-235B-A22B)
- [Qwen Documentation](https://qwen.readthedocs.io/en/latest/)
- [Qwen3 Technical Report](https://arxiv.org/abs/2505.09388)

### Preferred System Prompt Format
**Markdown with chat template.** Qwen3 uses a HuggingFace Jinja2 chat template
with OpenAI-compatible API. Markdown formatting works well; Chinese-optimized.

Qwen3 uniquely supports **thinking/non-thinking mode switching** within a single
model via `enable_thinking` parameter and `/think` / `/no_think` user commands.

### Context Structuring
- OpenAI-compatible messages array.
- System prompt via `system` role.
- `<think>...</think>` blocks for reasoning content (when thinking enabled).
- Context blocks with markdown sections and optional XML tags.

### Role/Separator Tokens
- `system`: System instructions.
- `user`: User input (can include `/think` or `/no_think` commands).
- `assistant`: Model response, optionally wrapped in `<think>...</think>`.

### Maximum Effective Prompt Length
| Parameter | Value |
|-----------|-------|
| Context window (native) | 32,768 tokens |
| Context window (YaRN) | 131,072 tokens |
| Practical input limit | ~28,000 tokens |
| Max output tokens | 4,096 (default), 32,768 (recommended) |
| Prompt caching | Not documented |

### Known Quirks
1. **Greedy decoding is BROKEN in thinking mode** — causes performance
   degradation and endless repetitions. Must use sampling.
2. **Thinking mode sampling:** Temp=0.6, TopP=0.95, TopK=20, MinP=0.
3. **Non-thinking mode sampling:** Temp=0.7, TopP=0.8, TopK=20, MinP=0.
4. **`/think` and `/no_think`** in user prompts provide per-turn thinking
   control when `enable_thinking=True`.
5. **When `enable_thinking=True`, output ALWAYS has `<think>...</think>`** —
   even if the thinking block is empty.
6. **When `enable_thinking=False`, `/think` and `/no_think` have NO effect.**
7. **YaRN degrades short-text performance** — only enable for long contexts.
8. **presence_penalty 0-2** can reduce repetition but may cause language mixing.
9. **For math:** Add "Please reason step by step, and put your final answer
   within \boxed{}."
10. **Multi-turn:** Historical thinking content should NOT be included
    (handled automatically by the Jinja2 template).

### Citations
No built-in citations. Use inline references. Qwen3 technical report provides
BibTeX citation format.

### Multi-Turn Conversation
Jinja2 chat template handles all formatting. In thinking mode, exclude historical
thinking content from subsequent turns.

### Profile
- `config/model_profiles/formats/qwen.yml`

---

## 5. Llama (Meta)

### Official Documentation
- [Meta Llama](https://www.llama.com/)
- HuggingFace Llama model cards
- Llama 3.1: 128K context, native tool calling

### Preferred System Prompt Format
**Natural language with chat template.** Llama 2 used a distinct format with
special tokens. Llama 3+ uses standard OpenAI-compatible chat template.

**Llama 2 format (legacy):**
```text
<s>[INST] <<SYS>>
{system_prompt}
<</SYS>>

{user_message} [/INST]
```

**Llama 3/3.1 format (current):**
```text
<|begin_of_text|><|start_header_id|>system<|end_header_id|>

{system_prompt}<|eot_id|>
<|start_header_id|>user<|end_header_id|>

{user_message}<|eot_id|>
<|start_header_id|>assistant<|end_header_id|>

{response}
```

HuggingFace's `apply_chat_template()` handles both formats automatically.

### Context Structuring
- Natural language with markdown section headers.
- Llama responds well to clear, direct prompts rather than verbose
  multi-paragraph instructions.
- Horizontal rules (`---`) as section separators work across all versions.

### Role/Separator Tokens
- Llama 3+: `system`, `user`, `assistant` (standard OpenAI-compatible).
- Llama 2: `[INST]`, `<<SYS>>`, `[/INST]` special token blocks.

### Maximum Effective Prompt Length
| Parameter | Value |
|-----------|-------|
| Context window (Llama 3.1) | 128,000 tokens |
| Context window (Llama 3) | 8,192 tokens |
| Context window (Llama 2) | 4,096 tokens |
| Practical input limit | ~110,000 tokens (3.1), ~6,500 (3), ~3,200 (2) |
| Prompt caching | Not built-in (vLLM prefix caching in deployment) |

### Known Quirks
1. **Version-dependent formats:** Llama 2 `<<SYS>>` vs Llama 3 chat template.
   Applying the wrong format can severely degrade quality.
2. **Llama 2 `[INST]` blocks** do not support system prompts inside the first
   user message in some deployment configurations.
3. **Natural language > special formatting:** Llama was trained heavily on
   natural language. Overly-structured XML may not help.
4. **Politeness matters:** Llama responds better to "please" and polite
   phrasing than terse commands (training data bias).
5. **Model size matters for code:** 70B+ significantly better at code
   generation than 8B.
6. **Chain-of-thought:** "Let's think step by step" improves reasoning.
7. **GGUF quantized versions:** Same prompt format, minor quality loss.
8. **Open-weight fine-tunes:** Behavior varies by fine-tune. Check model card.

### Citations
No built-in citation system. Use inline `[source]` markers.

### Multi-Turn Conversation
Standard chat template with all prior messages included. Llama 3.1 handles
long multi-turn conversations well within the 128K window.

### Profile
- `config/model_profiles/formats/llama.yml`

---

## 6. GLM (ZhipuAI)

### Official Documentation
- [ZhipuAI Platform](https://open.bigmodel.cn/)
- [GLM Documentation](https://docs.bigmodel.cn/)

### Preferred System Prompt Format
**Markdown + Structured.** GLM uses OpenAI-compatible API format. Plain text,
markdown, and structured formats all work. Chinese-optimized with natural
bilingual support.

### Context Structuring
- Standard OpenAI-compatible `system` / `user` / `assistant` messages.
- Markdown section headers for organization.
- **Tables are particularly effective** — GLM excels at structured data in table
  format.
- Bilingual labels (CN/EN) work naturally.

### Role/Separator Tokens
- `system`: System instructions.
- `user`: User input.
- `assistant`: Model response.

### Maximum Effective Prompt Length
| Parameter | Value |
|-----------|-------|
| Context window | 128,000 tokens (GLM-4) |
| Practical input limit | ~115,000 tokens |
| Token ratio | ~1 CN char ≈ 1 token; ~1 EN word ≈ 1 token |
| Max output tokens | 4,096 |
| Prompt caching | Not documented |

### Known Quirks
1. **Chinese-first optimization:** Chinese prompts yield better quality for
   Chinese content. English prompts work well for English content.
2. **Bilingual handling:** Code-switching (CN+EN in same prompt) is natural.
3. **Token ratio:** ~1.6 tokens per Chinese character (verify per model via
   `response.usage`).
4. **OpenAI SDK compatible:** Use with `langchain-openai` or direct OpenAI
   package pointing to Zhipu base URL.
5. **GLM-4 model family:** Plus (best), Air (balanced), Flash (fast/cheap),
   Long (extended context).
6. **Web search and knowledge base** available through Zhipu platform APIs
   (not standard function calling).

### Citations
No built-in citation system. Use inline references.

### Multi-Turn Conversation
Standard OpenAI-compatible messages array. All prior messages in context.
Check `response.usage.total_tokens` for window monitoring.

### Profile
- `config/model_profiles/formats/glm.yml`

---

## Cross-Model Comparison

### System Prompt Format Preference

| Model | Primary | Secondary | Notes |
|-------|---------|-----------|-------|
| Claude | XML | Markdown | XML is the documented first preference |
| GPT | Markdown + XML | Plain text | Hybrid approach recommended |
| DeepSeek | Plain/Markdown | XML | Concise works best |
| Qwen | Markdown | XML | Chinese-optimized markdown |
| Llama | Natural language | Markdown | Meta trained on natural language |
| GLM | Markdown + Tables | XML | Tables are a standout strength |

### Context Window (technical max)

| Model | Max Tokens | Effective Limit |
|-------|-----------|-----------------|
| Claude (Sonnet 4) | 200,000 | ~175,000 |
| GPT-4 | 128,000 | ~110,000 |
| GPT-4.1 | 1,000,000 | ~850,000 |
| DeepSeek V4 | 65,536 | ~56,000 |
| Qwen3 | 32,768 (131K w/ YaRN) | ~28,000 |
| Llama 3.1 405B | 128,000 | ~110,000 |
| GLM-4 | 128,000 | ~115,000 |

### Code Generation

| Model | Code Block Style | Language ID Importance | Notes |
|-------|-----------------|----------------------|-------|
| Claude | Fenced | High | Language ID improves quality |
| GPT | Fenced | High | Heavily GitHub-trained |
| DeepSeek | Fenced | High | Code-focused model family |
| Qwen | Fenced | High | Qwen3-Coder variant available |
| Llama | Fenced | Medium | 70B+ for best code quality |
| GLM | Fenced | Medium | Bilingual code+comments OK |

### Chinese Language Support

| Model | Native Chinese | CN+EN Mix | Notes |
|-------|---------------|-----------|-------|
| Claude | Second-tier | Good | English-primary |
| GPT | Second-tier | Good | English-primary |
| DeepSeek | **First-tier** | **Excellent** | Chinese-engineered |
| Qwen | **First-tier** | **Excellent** | Alibaba, 100+ languages |
| Llama | Second-tier | Fair | English-primary |
| GLM | **First-tier** | **Excellent** | Chinese-first, ZhipuAI |

---

## Sources

1. Anthropic. "Prompt Engineering Overview." https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview
2. Anthropic. "Use XML Tags." https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/use-xml-tags
3. Anthropic. "Prompting Best Practices." https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/claude-prompting-best-practices
4. OpenAI. "Prompt Engineering." https://platform.openai.com/docs/guides/prompt-engineering
5. OpenAI. "Structured Outputs." https://platform.openai.com/docs/guides/structured-outputs
6. DeepSeek. "API Documentation." https://api-docs.deepseek.com/
7. DeepSeek. "Thinking Mode." https://api-docs.deepseek.com/guides/thinking_mode
8. DeepSeek. "Anthropic API Compatibility." https://api-docs.deepseek.com/guides/anthropic_api
9. Qwen Team. "Qwen3 Technical Report." arXiv:2505.09388, 2025.
10. Qwen. "Qwen3-235B-A22B Model Card." https://huggingface.co/Qwen/Qwen3-235B-A22B
11. Meta. "Llama." https://www.llama.com/
12. ZhipuAI. "GLM Platform Documentation." https://docs.bigmodel.cn/
