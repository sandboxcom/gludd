# Per-Model Prompt Adapter — Design Specification

> **Status:** Design draft — 2026-06-16
> **Scope:** Feature-needs matrix, adapter architecture, gludd wiring, config schema, test plan.
> No code is written here; this is the spec a future implementation must satisfy.

---

## 1. Why a prompt adapter layer is necessary

The gateway today (`src/general_ludd/models/gateway.py`) holds a `ModelProfile` per registered
model and passes the caller's `messages` list (a plain list of role/content dicts) directly to the
LangChain provider class, which in turn serialises them to whatever wire format the chosen
provider happens to expect.  That works fine while every model is accessed through a cloud
API whose vendor owns the serialisation (OpenAI, Anthropic, etc.).  It breaks silently — or
produces garbage output — the moment a local model with its own tokeniser and chat template
is added, or when a cloud model requires a non-standard tool format, or when a reasoning model
needs thinking budget injection.

The sections below document the exact features that must be filtered or transformed before a
message list reaches the wire, and then design the adapter that performs that work.

---

## 2. Feature-needs matrix

### 2.1 Chat templates and special tokens (local / open-weight models)

Every fine-tuned open-weight model is trained on a specific prompt format encoded as a Jinja2
template in `tokenizer_config.json`.  When vLLM or llama.cpp serves the model it applies that
template during tokenisation.  When gludd calls the model through the OpenAI-compatible
`/v1/chat/completions` endpoint the server applies the template server-side, so the gateway
does not have to serialise tokens itself.  **However**, if gludd ever constructs a raw-text
prompt (the "raw completion" path used by some llama.cpp integrations, or any path that
bypasses the chat endpoint), it must apply the correct template itself.

Even on the chat endpoint, the gateway must know which **family** the model belongs to in order
to:
- set correct stop sequences (see 2.4);
- fold or refuse a `system` role for models that do not have one (Gemma, early Mistral);
- pass the right tool-call format (2.3).

The families and their distinguishing tokens are:

| Family | Turn-open token | Turn-close / EOS | System role? | Source |
|--------|----------------|------------------|--------------|--------|
| **ChatML** (Qwen, Hermes, OpenHermes) | `<\|im_start\|>role\n` | `<\|im_end\|>` | yes, explicit | [Unsloth chat templates](https://unsloth.ai/docs/basics/chat-templates); [chujiezheng/chat_templates](https://github.com/chujiezheng/chat_templates) |
| **Llama-3** (`meta-llama/Meta-Llama-3*`) | `<\|start_header_id\|>role<\|end_header_id\|>\n\n` | `<\|eot_id\|>` | yes | [Llama-3 tokenizer_config](https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct) |
| **Llama-4** (`meta-llama/Llama-4-*`) | `<\|header_start\|>role<\|header_end\|>` | `<\|eot\|>` | yes | Web search result (2025) |
| **Mistral v1/v2** | `[INST]` / `[/INST]` | `</s>` | no — fold into first user turn | [HuggingFace forums](https://discuss.huggingface.co/t/endpoint-not-returning-stop-token-on-mistral-models/60100); [deepwiki llama.cpp](https://deepwiki.com/jina-ai/llama.cpp/6.1-chat-templates-and-conversation-system) |
| **Mistral v3+** | same `[INST]` tokens promoted to control IDs | `</s>` | limited | web search 2025 |
| **Gemma** | `<start_of_turn>role\n` | `<end_of_turn>\n` | no — merge into user | [Unsloth docs](https://unsloth.ai/docs/basics/chat-templates) |
| **OpenAI / cloud** | N/A — vendor-side | N/A | yes, dedicated field | wire format |
| **Anthropic / Claude** | N/A — vendor-side | N/A | yes, top-level `system` field | [Anthropic API](https://platform.claude.com/docs/en/build-with-claude/extended-thinking) |

**Consequence for the gateway:** for any `ModelProfile` whose `provider` is `local` (vLLM or
llama.cpp), the adapter must know the family so it can:
1. Fold a `system` message into the first user turn for Gemma / early Mistral.
2. Add the right stop sequences to the request (2.4).
3. Choose the correct tool format (2.3).
4. On raw-text paths, apply the Jinja2 template directly.

Using the wrong template degrades quality measurably and can cause runaway generation (model
never emits EOS because it was trained on a different stop-token format).

### 2.2 System-prompt conventions

Cloud APIs differ on where the system prompt lives:

| Provider | Mechanism |
|----------|-----------|
| **OpenAI / compatible** | `{"role":"system","content":"..."}` as first message in `messages[]` |
| **Anthropic Claude** | Top-level `system` string field, outside `messages[]`; gludd currently passes it as the first message via LangChain's `ChatAnthropic` which handles the conversion — but any direct HTTP path must split it |
| **Gemini (Google)** | `systemInstruction` top-level object, not inside `contents[]` |
| **Gemma (local)** | No `system` role — the system text must be prepended to the first user message, optionally wrapped as `<start_of_turn>user\n<SYSTEM>...</SYSTEM>\n{user_text}<end_of_turn>` |
| **Mistral (local v1/v2)** | System text folded into the first `[INST]...[/INST]` block |

The adapter must normalise: take a canonical `(system: str | None, messages: list[dict])` input
and emit the correct provider-specific shape.

### 2.3 Tool / function-call formats

The same logical tool schema must be re-rendered per provider.  The four wire formats in use:

**OpenAI** (`tools` array, `tool_calls` in response):
```json
{
  "tools": [{
    "type": "function",
    "function": {
      "name": "get_weather",
      "description": "...",
      "parameters": { "type":"object", "properties": {...}, "required": [...] }
    }
  }],
  "tool_choice": "auto"
}
```
Response: `choices[0].message.tool_calls[].function.{name, arguments (JSON string)}`

**Anthropic Claude** (`tools` array, `tool_use` content blocks):
```json
{
  "tools": [{
    "name": "get_weather",
    "description": "...",
    "input_schema": { "type":"object", ... }
  }]
}
```
Response: content block `{"type":"tool_use","id":"...","name":"...","input":{...}}` — `input` is
already a parsed object, not a JSON string.  Sources: [digitalapplied.com tool guide](https://www.digitalapplied.com/blog/ai-function-calling-guide-openai-anthropic-google);
[eesel.ai comparison](https://www.eesel.ai/blog/openai-api-vs-anthropic-api-vs-gemini-api).

**Gemini** (`tools` wrapping `functionDeclarations`):
```json
{
  "tools": [{
    "functionDeclarations": [{
      "name": "get_weather",
      "description": "...",
      "parameters": { "type":"OBJECT", ... }
    }]
  }]
}
```
Response: `candidates[0].content.parts[].functionCall.{name, args}`.  Types use
Protocol-Buffer-style strings (`"OBJECT"` not `"object"`).  Source: [Gemini image understanding
API](https://ai.google.dev/gemini-api/docs/interactions/image-understanding).

**Local / Hermes / Mistral v3** (token-embedded JSON in the text stream):

Hermes 2 / Hermes 3 models use `<tool_call>{"name":"...","arguments":{...}}</tool_call>` in
the assistant turn; the tool schema is injected as JSON in the system prompt:
```text
You have access to these tools: [{"name":"get_weather","description":"...","parameters":{...}}]
```
Mistral v3 adds dedicated control tokens: `[AVAILABLE_TOOLS]...[/AVAILABLE_TOOLS]` for the
schema and `[TOOL_CALLS]...[/TOOL_CALLS]` in the response, plus `[TOOL_RESULTS]` for the result
turn.  Sources: [NousResearch Hermes Function-Calling](https://github.com/NousResearch/Hermes-Function-Calling);
web search 2025.

**vLLM tool_call_parser** selects the right parser at serve time
(`--tool-call-parser llama3_json|mistral|hermes`) and requires a matching
`--chat-template` override for Llama 3.1/Mistral/InternLM (the built-in tokenizer template
does not include tool-call handling).  Source: [vLLM tool calling docs v0.8.1](https://docs.vllm.ai/en/v0.8.1/features/tool_calling.html).

The adapter must convert gludd's internal tool schema list (currently `{name, description,
input_schema}` as produced in `tool_loop.py`) into the correct wire shape before the provider
class sees it, and must parse the response's tool invocations back into the same canonical shape
before returning.

### 2.4 Stop sequences and special-token hygiene

Stop sequences are the secondary halting mechanism (the primary is EOS).  Using the wrong
set causes two failure modes:
- **Runaway generation** — model trained on `<|im_end|>` as its EOS token but the server is
  not told to stop on it; output continues past the intended turn boundary with repetition or
  garbage.  Real vLLM bug filed: [EOS token inserted into sequence #558](https://github.com/vllm-project/vllm/issues/558).
- **Premature stop** — a stop string that is a common phrase in the model's domain language
  causes early truncation.

Per-family stop sequences to register (in addition to the model's native EOS):

| Family | Additional stop sequences |
|--------|--------------------------|
| ChatML / Qwen | `<\|im_end\|>` |
| Llama-3 | `<\|eot_id\|>`, `<\|end_of_text\|>` |
| Llama-4 | `<\|eot\|>` |
| Gemma | `<end_of_turn>` |
| Mistral v1/v2 | `[INST]` (prevent re-opening a user turn) |
| Hermes tool mode | `</tool_call>` (mark tool call complete) |

The adapter appends these to the `stop` (or `stop_token_ids`) field of the provider request.

For **cloud providers** the vendor handles stop tokens; the adapter passes no stop sequences
unless the caller explicitly provided them.

### 2.5 Reasoning / thinking models

Several model families now expose a separate "thinking" or "reasoning" chain that requires
different request parameters:

**Anthropic Claude extended thinking** (Claude Sonnet 4.x, Opus 4.x):
```json
{
  "thinking": {"type": "adaptive"},
  "effort": "high"
}
```
`budget_tokens` is deprecated as of Opus 4.6 / Sonnet 4.6; `effort` + `thinking.type:adaptive`
is the current form.  At `effort:high` or `effort:max` the model will nearly always emit a
thinking block before the visible answer.  Sources: [Claude extended thinking docs](https://platform.claude.com/docs/en/build-with-claude/extended-thinking);
[Claude adaptive thinking](https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking);
[Claude effort docs](https://platform.claude.com/docs/en/build-with-claude/effort).

**OpenAI o1/o3/o4-mini** (reasoning series):
- `reasoning_effort: "low" | "medium" | "high"` (not a `thinking` block).
- Does not accept a `system` role; the system instruction must be injected as the first `user`
  message or via `developer` role.
- Does not support streaming in some configurations.
- `max_completion_tokens` replaces `max_tokens`.

**Qwen3 thinking mode**:
Local Qwen3 models expose `<think>` (token ID 151667) / `</think>` (token ID 151668) delimiters.
To enable thinking mode send `enable_thinking: true` (Qwen3 API) or prefix the system prompt
with the appropriate instruction.  To **disable** thinking append `/no_think` to the user
prompt.  Source: web search 2025.

**DeepSeek-R1 / DeepSeek V3**:
Use `<think>...</think>` delimiters in output.  No special request parameter needed; just
include them in stop sequences if you want only the final answer.

The adapter must:
1. Detect that a `ModelProfile` is a reasoning model (via a `reasoning_model: bool` flag and
   optionally a `thinking_effort: str` field in the profile).
2. Inject the correct thinking-enable parameter for the target provider.
3. Adjust stop handling to strip or surface the thinking section as the caller requests.

### 2.6 Long-context, code, and multimodal feature handling

**Long-context / prefix caching:**
For models with large context windows (Gemini 2.x with 1M tokens; Claude with 200k; local
models at `max_model_len`), some providers offer explicit prefix-caching hints.  Anthropic
accepts `cache_control: {type: "ephemeral"}` on message content blocks to pin a prefix to the
cache.  vLLM prefix caching is server-side and transparent if `--enable-prefix-caching` is
enabled at serve time (see `docs/MODEL_DEPLOYMENT_TUNING.md` section 1.6).  The adapter can
annotate long stable system prompts with the cache hint on Anthropic.

**Multimodal (vision) inputs:**
The three cloud providers diverge on how image bytes are embedded:

| Provider | Format |
|----------|--------|
| OpenAI | `{"type":"image_url","image_url":{"url":"data:image/png;base64,...","detail":"auto"}}` inside a `content` array |
| Anthropic | `{"type":"image","source":{"type":"base64","media_type":"image/png","data":"..."}}` inside a `content` array |
| Gemini | `{"inline_data":{"mime_type":"image/png","data":"..."}}` inside a `parts` array |

Source: [OpenAI vision API](https://developers.openai.com/api/docs/guides/images-vision);
[Gemini image understanding](https://ai.google.dev/gemini-api/docs/interactions/image-understanding);
[vLLM multimodal inputs](https://docs.vllm.ai/en/stable/features/multimodal_inputs/).

The adapter must transcode image content blocks from a canonical gludd format to the
provider-specific embedding.

**Code / structured output:**
Some providers (OpenAI, Anthropic) support `response_format: {type:"json_object"}` or
`response_format: {type:"json_schema", json_schema:{...}}` for structured output.  The adapter
must translate a gludd-level `output_schema` field into the correct provider parameter, or omit
it entirely for providers that do not support it.

---

## 3. Adapter design

### 3.1 Canonical input/output types

```python
# src/general_ludd/models/prompt_adapter.py (new module)

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass
class CanonicalRequest:
    """Normalised input produced by all callers before adapter dispatch."""
    system: str | None
    messages: list[dict[str, Any]]          # role/content; content may be str or list (multimodal)
    tools: list[dict[str, Any]] = field(default_factory=list)   # gludd tool schema: {name, description, input_schema}
    output_schema: dict[str, Any] | None = None
    stop: list[str] = field(default_factory=list)
    # Reasoning hints (callers may set; adapter translates per provider)
    thinking_enabled: bool = False
    thinking_effort: str = "high"           # "low" | "medium" | "high" | "max"

@dataclass
class WireRequest:
    """Provider-specific wire payload emitted by the adapter."""
    messages: list[dict[str, Any]]          # may be restructured (system folded in, etc.)
    extra_kwargs: dict[str, Any] = field(default_factory=dict)  # tools, stop, thinking, etc.
    raw_prompt: str | None = None           # populated only for raw-text (non-chat) paths
```

### 3.2 Adapter interface and registry

```python
from abc import ABC, abstractmethod

class PromptAdapter(ABC):
    """One per model family.  Converts a CanonicalRequest to WireRequest."""

    @abstractmethod
    def adapt(self, req: CanonicalRequest, profile: "ModelProfile") -> WireRequest: ...

    @abstractmethod
    def parse_tool_response(self, raw_response: Any) -> list[dict[str, Any]]:
        """Extract tool invocations from a raw provider response into canonical form."""
        ...

# Registry: keyed by (provider_name, model_family) with fallback to (provider_name,)
_REGISTRY: dict[tuple[str, str | None], type[PromptAdapter]] = {}

def register_adapter(
    provider: str,
    family: str | None = None,
) -> "Callable[[type[PromptAdapter]], type[PromptAdapter]]":
    def decorator(cls: type[PromptAdapter]) -> type[PromptAdapter]:
        _REGISTRY[(provider, family)] = cls
        return cls
    return decorator

def get_adapter(provider: str, family: str | None) -> PromptAdapter:
    cls = _REGISTRY.get((provider, family)) or _REGISTRY.get((provider, None))
    if cls is None:
        cls = _REGISTRY[("openai", None)]   # safe default for OpenAI-compatible APIs
    return cls()
```

### 3.3 Concrete adapter implementations

The following classes live in `src/general_ludd/models/prompt_adapter.py`.

#### `OpenAIAdapter` — provider `openai`, `openrouter`, `groq`, `deepseek`, `zai`, `local/vllm`
- Passes `system` as `{"role":"system","content":system}` prepended to `messages`.
- Converts gludd tools to OpenAI `tools` array with `"type":"function"` wrapper.
- Appends family stop sequences (for `local` provider with `model_family` set).
- For `o1`/`o3`/`o4` reasoning models: removes `system` role, injects it as first `user` turn,
  sets `reasoning_effort`, translates `max_tokens` → `max_completion_tokens`.
- For multimodal: transcodes `image` content blocks to `image_url` format.

#### `AnthropicAdapter` — provider `anthropic`
- Splits `system` out of `messages` into `extra_kwargs["system"]` (top-level string).
- Converts gludd tools to Anthropic `tools` array with `"input_schema"` key.
- For thinking models: injects `thinking: {type:"adaptive"}`, maps `thinking_effort` to
  `effort` field.
- For multimodal: transcodes `image` blocks to Anthropic `source.base64` shape.
- Applies `cache_control: {type:"ephemeral"}` to system prompt when
  `profile.context_window > 64000` (prefix caching hint).
- Parses `tool_use` content blocks in response back to canonical `{id, name, input}`.

#### `GeminiAdapter` — provider `gemini` (future; not yet in `provider_presets.py`)
- Converts `messages` → `contents` with `role: user|model` (no `system` role in contents).
- Moves `system` → `extra_kwargs["systemInstruction"]["parts"][0]["text"]`.
- Converts tools → `tools[0]["functionDeclarations"]` with Proto-style `"OBJECT"` type strings.
- Transcodes image blocks to `inline_data` shape.

#### `LocalChatMLAdapter` — provider `local`, family `chatml` (Qwen, Hermes, OpenHermes)
- Passes messages as-is to the OpenAI-compatible chat endpoint (server applies template).
- Appends stop sequences `["<|im_end|>"]`.
- For Hermes tool mode: if `tools` is non-empty, renders the tool schema JSON into the system
  prompt using the Hermes convention, and parses `<tool_call>...</tool_call>` blocks from the
  assistant response.

#### `LocalLlama3Adapter` — provider `local`, family `llama3`
- Passes messages to chat endpoint.
- Appends stop sequences `["<|eot_id|>", "<|end_of_text|>"]`.
- Tool calling: sets `--tool-call-parser llama3_json` at serve time (config validation, not
  runtime); the adapter emits OpenAI-style `tools` and trusts the vLLM parser.

#### `LocalMistralAdapter` — provider `local`, family `mistral`
- Folds `system` content into the first `user` turn: prepend as `<<SYS>>\n{system}\n<</SYS>>\n\n`
  (Mistral v1/v2 convention) or omit wrapper for v3+.
- Appends stop sequences `["[INST]", "</s>"]`.
- For v3 tool mode: renders tool schema as `[AVAILABLE_TOOLS]...[/AVAILABLE_TOOLS]` text in
  the first user turn; parses `[TOOL_CALLS]...[/TOOL_CALLS]` from the response.

#### `LocalGemmaAdapter` — provider `local`, family `gemma`
- No `system` role: prepend system content to the first user message as plain text.
- Appends stop sequences `["<end_of_turn>"]`.
- Tool calling not natively supported: render tool descriptions in plain text and parse output
  heuristically (warn on registration that tool calling is degraded).

### 3.4 `ModelProfile` additions

Two new optional fields on the existing `ModelProfile` Pydantic model
(`src/general_ludd/models/gateway.py`):

```python
model_family: str | None = None
# Recognised values: "chatml", "llama3", "llama4", "mistral", "gemma"
# None means "use provider default" (cloud APIs don't need a family)

reasoning_model: bool = False
# When True, adapter injects thinking/effort params and adjusts system-role handling
```

These are optional and backward-compatible (defaults keep existing behaviour).

### 3.5 Placement in the gateway call path

The adapter sits between `call_model()` receiving its `messages` argument and the point where
`init_kwargs` is assembled and the provider class is instantiated.  Concretely, in
`ModelGateway._invoke_and_bill`:

```text
caller → call_model(profile_id, messages, tools=..., system=...)
       → _invoke_and_bill(profile, messages, ...)
           adapter = get_adapter(profile.provider, profile.model_family)
           wire = adapter.adapt(
               CanonicalRequest(
                   system=kwargs.pop("system", None),
                   messages=messages,
                   tools=kwargs.pop("tools", []),
                   thinking_enabled=profile.reasoning_model,
                   thinking_effort=kwargs.pop("thinking_effort", "high"),
               ),
               profile,
           )
           chat_model = provider_cls(**{**init_kwargs, **wire.extra_kwargs})
           raw_response = chat_model.invoke(wire.messages)
```

The adapter is invoked once per call.  No caching of adapted prompts is needed (the response
cache already operates on the raw `(profile_id, messages, kwargs)` input key via
`_make_cache_key`; that key is computed before adaptation, which is correct: two callers with
the same canonical request should share the same cache entry regardless of which adapter
transformation produces the same wire form).

### 3.6 Config schema — per-profile template/family fields

New fields in `ModelProfile` are set when the profile is registered.  In the REST API
(`/admin/models/add`), the caller may include:

```json
{
  "model_id": "qwen3-8b-local",
  "provider": "local",
  "model": "Qwen/Qwen3-8B-Instruct",
  "model_family": "chatml",
  "reasoning_model": false,
  "api_base_alias": "local_vllm_qwen3_base"
}
```

For the existing cloud presets in `provider_presets.py` the adapter is determined solely by
`provider`; `model_family` defaults to `None`.

A future enhancement may auto-detect `model_family` from the `model_name` string by checking it
against a table of known prefixes (e.g. `"meta-llama/Meta-Llama-3"` → `"llama3"`,
`"Qwen/"` → `"chatml"`).  Until then it is an explicit operator-supplied field.

---

## 4. Gludd wiring

### 4.1 Module location

```text
src/general_ludd/models/
    gateway.py          (existing — add model_family, reasoning_model to ModelProfile;
                         call get_adapter in _invoke_and_bill)
    prompt_adapter.py   (new — CanonicalRequest, WireRequest, PromptAdapter ABC,
                         registry, all concrete adapters)
```

No new top-level package; `prompt_adapter` is a sibling of `gateway.py`.

### 4.2 ToolCallLoop integration

`src/general_ludd/execution/tool_loop.py` currently builds `tool_schemas` as a list of
`{name, description, input_schema}` dicts and passes them as a `tools` kwarg to
`gateway.call_model`.  This is already the canonical gludd tool format that
`CanonicalRequest.tools` expects.  No changes needed in `tool_loop.py`; the adapter layer
absorbs the translation.

The response parsing (`tool_calls = getattr(response, "tool_calls", None)`) currently reads the
LangChain-normalised `tool_calls` attribute.  For local models whose tool calls are embedded in
text the `PromptAdapter.parse_tool_response` method must be called before returning the
response object, and the result stored in a synthetic `tool_calls` attribute on `ModelResponse`
(or a new field on that dataclass).

### 4.3 `LocalServerConfig` / `LocalInferenceManager`

`LocalServerConfig` (`src/general_ludd/infra/local_inference.py`) must be extended to carry
`model_family` and `tool_call_parser` fields so that the correct vLLM `--tool-call-parser` and
`--chat-template` arguments are injected into the `vllm serve` command by `_build_command`.
The `LocalInferenceManager` already uses `extra_args` for additional flags; the cleaner path
is to add explicit optional fields and generate the flags from them:

```python
@dataclass
class LocalServerConfig:
    ...
    model_family: str | None = None          # passed to prompt adapter; also sets --tool-call-parser
    tool_call_parser: str | None = None      # override e.g. "llama3_json", "mistral", "hermes"
    chat_template_path: str | None = None    # path to custom .jinja file for vllm --chat-template
```

When `tool_call_parser` is set, `_build_command` appends
`["--enable-auto-tool-choice", "--tool-call-parser", tool_call_parser]` to the vLLM argv, and
optionally `["--chat-template", chat_template_path]` if provided.

---

## 5. Test plan

Tests live in `tests/unit/test_prompt_adapter.py` (new file).  No existing test is modified.

### 5.1 Per-adapter unit tests (parametrised)

For each concrete adapter, test all of:

| Test | Assertion |
|------|-----------|
| `system` in canonical request appears in the right wire position | OpenAI: first message with role `system`; Anthropic: `extra_kwargs["system"]`; Gemma/Mistral: prepended to first user message content |
| `system=None` produces no system message | No `system` key in extra_kwargs; no extra prepended text |
| Tools non-empty → correct wire format | OpenAI: `extra_kwargs["tools"][0]["type"] == "function"`; Anthropic: `extra_kwargs["tools"][0]["input_schema"]`; Hermes: system prompt contains JSON schema; Mistral v3: first user message contains `[AVAILABLE_TOOLS]` |
| Tools empty → no `tools` key in extra_kwargs | |
| `model_family` stop sequences appended | `extra_kwargs["stop"]` contains expected token |
| Multimodal message with `type:"image"` content block transcoded correctly | Per-provider shape |
| `reasoning_model=True` on Anthropic adapter → `thinking` + `effort` in extra_kwargs | |
| `reasoning_model=True` on OpenAI o1 → system role removed, `reasoning_effort` set | |

### 5.2 Registry tests

- `get_adapter("openai", None)` returns `OpenAIAdapter`.
- `get_adapter("local", "chatml")` returns `LocalChatMLAdapter`.
- `get_adapter("local", "unknownfamily")` falls back to `OpenAIAdapter` (safe default).
- `get_adapter("anthropic", None)` returns `AnthropicAdapter`.

### 5.3 Tool-response parsing

- `AnthropicAdapter.parse_tool_response` extracts `tool_use` content blocks into
  `[{id, name, input}]`.
- `LocalChatMLAdapter.parse_tool_response` extracts `<tool_call>{"name":...}</tool_call>`
  from a raw text string.
- Both return `[]` on a response with no tool calls.

### 5.4 Gateway integration smoke test

Add one parametrised test in `tests/unit/test_model_gateway_adapter.py` that mocks the
provider class and asserts `_invoke_and_bill` calls `adapter.adapt` once with the correct
`CanonicalRequest` fields extracted from `kwargs`.

### 5.5 `LocalServerConfig` command-builder tests

Extend `tests/unit/test_local_inference_command.py` with:
- Config with `model_family="llama3"` and `tool_call_parser="llama3_json"` → argv contains
  `["--enable-auto-tool-choice", "--tool-call-parser", "llama3_json"]`.
- Config with `chat_template_path="/templates/llama3.jinja"` → argv contains
  `["--chat-template", "/templates/llama3.jinja"]`.
- Existing tests must not be broken (all new fields are optional with `None` defaults).

---

## 6. Open questions / future work

1. **Auto-detect family from model name.** A heuristic table mapping model-name prefixes to
   families would remove the need for operators to specify `model_family` explicitly.  Risks:
   naming is not stable; safest to keep it explicit for now and add the heuristic behind a
   `--auto-detect-family` flag.

2. **Raw-text path (non-chat endpoint).** `llama.cpp` can be used without the chat endpoint
   (direct text completion), in which case gludd must apply the Jinja2 template in-process
   using `transformers.AutoTokenizer.apply_chat_template`.  That brings in the
   `transformers` dependency, which is heavy; gate this behind an optional extra.

3. **Gemini adapter.** Not yet in `provider_presets.py`.  The design above is complete;
   implementation should wait until a Gemini credential flow is added.

4. **Streaming.** `call_model` today returns a single `ModelResponse`; streaming is a future
   path.  Adapters must be stream-aware when that lands.

5. **Tool-result message format.** The current `tool_loop.py` appends
   `{"role":"tool","tool_call_id":"...","content":str(result)}`.  Anthropic uses
   `{"role":"user","content":[{"type":"tool_result","tool_use_id":"...","content":"..."}]}`.
   The adapter must also normalise tool-result messages on their way **in** to the next
   model call, not just the tool-call on the way **out**.

---

## Sources

- Chat templates and special tokens: [Unsloth chat templates](https://unsloth.ai/docs/basics/chat-templates); [HuggingFace LLM course ch.11](https://huggingface.co/learn/llm-course/en/chapter11/2); [chujiezheng/chat_templates](https://github.com/chujiezheng/chat_templates); [jndiogo/LLM-chat-templates](https://github.com/jndiogo/LLM-chat-templates); [deepwiki llama.cpp chat templates](https://deepwiki.com/jina-ai/llama.cpp/6.1-chat-templates-and-conversation-system)
- Special-token attack surface: [Towards AI — special tokens jailbreak](https://towardsai.net/p/machine-learning/the-hidden-attack-surface-in-every-llm-how-special-tokens-enable-96-jailbreak-success-rates)
- Tool / function-call formats: [digitalapplied.com tool guide](https://www.digitalapplied.com/blog/ai-function-calling-guide-openai-anthropic-google); [eesel.ai API comparison](https://www.eesel.ai/blog/openai-api-vs-anthropic-api-vs-gemini-api); [ruh.ai function calling](https://www.ruh.ai/blogs/function-calling); [tokenmix.ai 2026 guide](https://tokenmix.ai/blog/function-calling-guide); [ofox.ai complete guide 2026](https://ofox.ai/blog/function-calling-tool-use-complete-guide-2026/)
- Hermes tool calling: [NousResearch/Hermes-Function-Calling](https://github.com/NousResearch/Hermes-Function-Calling); [NousResearch/Hermes-2-Pro-Llama-3-8B](https://huggingface.co/NousResearch/Hermes-2-Pro-Llama-3-8B)
- vLLM tool calling / chat templates: [vLLM tool calling v0.8.1](https://docs.vllm.ai/en/v0.8.1/features/tool_calling.html); [vLLM OpenAI-compatible server](https://docs.vllm.ai/en/v0.6.4/serving/openai_compatible_server.html); [thinkingconcurrently.com inference server tool calling 2025](https://thinkingconcurrently.com/posts/2025-06-21-configuring-inference-servers-for-tool-calling/)
- Stop sequences / EOS hygiene: [vLLM EOS token issue #558](https://github.com/vllm-project/vllm/issues/558); [HuggingFace Mistral stop token forum](https://discuss.huggingface.co/t/endpoint-not-returning-stop-token-on-mistral-models/60100); [kaitchup — LLM can't stop generating](https://kaitchup.substack.com/p/my-llm-cant-stop-generating-how-to); [ahmet_celebi — chat templates llama-cpp](https://medium.com/@ahmet_celebi/demystifying-chat-templates-of-llm-using-llama-cpp-and-ctransformers-f17871569cd6)
- Reasoning / thinking models: [Anthropic extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking); [Anthropic adaptive thinking](https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking); [Anthropic effort](https://platform.claude.com/docs/en/build-with-claude/effort)
- Multimodal: [OpenAI vision API](https://developers.openai.com/api/docs/guides/images-vision); [Gemini image understanding](https://ai.google.dev/gemini-api/docs/interactions/image-understanding); [vLLM multimodal inputs](https://docs.vllm.ai/en/stable/features/multimodal_inputs/)
- Gludd deployment tuning (prefix caching, chat-template flags): `docs/MODEL_DEPLOYMENT_TUNING.md` sections 1.6, 2.3
