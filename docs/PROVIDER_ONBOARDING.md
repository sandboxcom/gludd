# Provider Onboarding Playbook

**Purpose.** A mechanical, repeatable process for adding a new model (or compute) provider to gludd. Source of truth for the integration surface: `src/general_ludd/models/provider_presets.py`.

**Scope.** Hosted LLM inference providers that expose an OpenAI-compatible `/v1/chat/completions` (or equivalent) REST API. Self-hosted backends (vLLM, llama.cpp, Ollama) are configured as local endpoints, not presets — see the "Local backends" note in README.md.

---

## 1. When to add a provider

Add a new provider preset when ALL three are true:

| Criterion | Why it matters |
|---|---|
| **OpenAI-compatible API** | gludd's default adapter is `langchain-openai`'s `ChatOpenAI`. A provider exposing `POST <base>/chat/completions` with `{model, messages, ...}` shape drops in with zero custom code. Non-OpenAI APIs require a custom connector (Step 2 below) — much higher cost. |
| **Market presence** | Funded, documented, has an SDK in the OpenAI client ecosystem. Avoid defunct/pivoted providers (see audit skip list — Banana.dev, Anyscale Endpoints, LightOn's chat API, Aleph Alpha's public API). |
| **User demand or strategic gap** | Either a user has asked for it, or it fills a coverage gap (e.g. sovereign-EU hosting, edge inference, free-tier availability for testing). |

**Decision shortcut:** if the provider is in the "INCLUDE" column of `docs/audit/GPU_PROVIDER_AUDIT_2026-07-06.md`, the criteria are already met — proceed to Step 1.

**Skip if:** the provider only rents GPUs (Vast.ai, Hyperstack, OVHcloud AI), only sells a data platform (Scale AI), only exposes per-model self-deployed endpoints (SageMaker JumpStart), or is a duplicate surface for an existing provider (Azure OpenAI is covered by `openai`).

---

## 2. Currently-supported providers (19)

Every entry below is a key in `PROVIDER_PRESETS` and is auto-discovered by `ProviderRegistry.from_presets()` at daemon startup (`src/general_ludd/models/provider_registry.py`). Adding a preset entry is the minimum viable integration — no daemon code changes required.

| Provider key | Display name | Env var | Adapter (provider_class) | Free-models endpoint? |
|---|---|---|---|---|
| `anthropic` | Anthropic | `ANTHROPIC_API_KEY` | `langchain-anthropic` / `ChatAnthropic` | no |
| `baseten` | Baseten | `BASETEN_API_KEY` | `langchain-openai` / `ChatOpenAI` | no |
| `cohere` | Cohere | `CO_API_KEY` | `langchain-openai` / `ChatOpenAI` | no |
| `coreweave` | CoreWeave | `COREWEAVE_API_KEY` | `langchain-openai` / `ChatOpenAI` | no |
| `deepseek` | DeepSeek | `DEEPSEEK_API_KEY` | `langchain-openai` / `ChatOpenAI` | no |
| `fireworks` | Fireworks AI | `FIREWORKS_API_KEY` | `langchain-openai` / `ChatOpenAI` | no |
| `groq` | Groq | `GROQ_API_KEY` | `langchain-openai` / `ChatOpenAI` | no |
| `huggingface` | Hugging Face | `HF_TOKEN` | `langchain-huggingface` / `HuggingFaceEndpoint` | no |
| `lambdalabs` | Lambda Labs | `LAMBDALABS_API_KEY` | `langchain-openai` / `ChatOpenAI` | no |
| `modal` | Modal | `MODAL_API_TOKEN` | `langchain-openai` / `ChatOpenAI` | no |
| `mistral` | Mistral AI | `MISTRAL_API_KEY` | `langchain-openai` / `ChatOpenAI` | no |
| `nvidia` | NVIDIA NIM | `NVIDIA_API_KEY` | `langchain-openai` / `ChatOpenAI` | no |
| `openai` | OpenAI | `OPENAI_API_KEY` | `langchain-openai` / `ChatOpenAI` | no |
| `openrouter` | OpenRouter | `OPENROUTER_API_KEY` | `langchain-openai` / `ChatOpenAI` | yes (`/v1/models`) |
| `perplexity` | Perplexity | `PERPLEXITY_API_KEY` | `langchain-openai` / `ChatOpenAI` | no |
| `replicate` | Replicate | `REPLICATE_API_TOKEN` | `langchain-openai` / `ChatOpenAI` | no |
| `runpod` | RunPod | `RUNPOD_API_KEY` | `langchain-openai` / `ChatOpenAI` | no |
| `together` | Together AI | `TOGETHER_API_KEY` | `langchain-openai` / `ChatOpenAI` | yes (`/v1/models`) |
| `zai` | Z.AI / GLM | `ZAI_API_KEY` | `langchain-openai` / `ChatOpenAI` | no |

**18 of 19** use the OpenAI-compatible stack (`langchain-openai` / `ChatOpenAI`). Only `anthropic` (native SDK) and `huggingface` (TGI endpoint) deviate, and both are still wired purely via the preset's `provider_package` + `provider_class` fields — no per-provider Python module exists in `src/general_ludd/models/` for any of them.

---

## 3. The 5-step onboarding process

### Step 1 — Add the preset entry (REQUIRED, always)

Edit `src/general_ludd/models/provider_presets.py` and add a new key to `PROVIDER_PRESETS` with all 9 fields populated. Use an existing OpenAI-compatible provider (e.g. `together`) as the template.

```python
"newprovider": {
    "api_base_url": "https://api.newprovider.com/v1",
    "provider_package": "langchain-openai",        # pip name; auto-normalized to import name
    "provider_class": "ChatOpenAI",                # class in provider_package
    "credential_env_var": "NEWPROVIDER_API_KEY",   # conventional env var name
    "credential_alias": "newprovider_api_key",     # internal alias key
    "api_base_alias": "newprovider_api_base",      # internal alias key
    "display_name": "NewProvider",                 # human-facing label
    "free_models_endpoint": None,                  # URL or None
    "supports_free_models": False,                 # True only if a /models listing exists
},
```

**What this step alone unlocks:**
- `ProviderRegistry.from_presets()` auto-discovers the provider at daemon boot — no daemon code change.
- `get_provider_preset("newprovider")` returns the config.
- `detect_credential_alias("newprovider")` and `list_configured_providers()` recognise the env var.
- `AutoConfigurator.generate_profiles("newprovider", scraped_models)` can build model profiles from any scraped data.

### Step 2 — Custom connector module (RARE — only for non-OpenAI APIs)

> **Current state:** none of the 19 supported providers ship a custom connector module. The `connectors/` directory under `src/general_ludd/connectors/` is for **observability** ingest (Datadog, Splunk, Prometheus, etc.) — NOT for model providers. Do not add model-provider code there.

A custom connector is only required when the provider's API is NOT OpenAI-compatible AND no langchain adapter exists. Concretely, you need a connector module at `src/general_ludd/models/connectors/<name>.py` (note: this directory does not exist yet — create it) only if:

- The provider exposes a proprietary request/response shape (e.g. raw WebSocket streaming, custom tool-call format).
- The provider requires request signing or auth flows that `ChatOpenAI` cannot express via `api_key` + `api_base`.
- The provider's "model" abstraction is not a chat completion (e.g. raw completions, embeddings-only, image generation).

If you need this, the connector must expose a `from_preset(preset: dict, api_key: str) -> Callable` factory returning a langchain-compatible runnable, and `ProviderRegistry` must be extended to dispatch to it via a new preset field (e.g. `"custom_connector": "newprovider"`). This is a non-trivial design change — open a design doc in `docs/` before implementing.

**If unsure:** assume Step 2 is not needed. Every provider added since 2026-06 has used Step 1 alone.

### Step 3 — Flagship model registration (RECOMMENDED — currently a gap)

> **Known gap.** As of 2026-07-06 there is **no `PROVIDER_FLAGSHIP_MODELS` constant** in the codebase. The user-facing prompt for this playbook assumed one exists; it does not. This step describes the recommended target state. Until it exists, role/quality assignment falls back to name heuristics in `AutoConfigurator._assign_roles` and `_assign_quality` (`src/general_ludd/models/auto_configurator.py`).

**Recommended addition:** create `PROVIDER_FLAGSHIP_MODELS` in `src/general_ludd/models/provider_presets.py` (or a sibling file) mapping each provider to its default flagship model — the model a smoke test or auto-config should invoke when no model is specified:

```python
PROVIDER_FLAGSHIP_MODELS: dict[str, str] = {
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-5",
    "zai": "glm-5.2",
    "deepseek": "deepseek-chat",
    "groq": "llama-3.3-70b-versatile",
    "together": "meta-llama/Llama-3-70b-chat-hf",
    "mistral": "mistral-large-latest",
    "cohere": "command-r-plus",
    "nvidia": "meta/llama-3.1-70b-instruct",
    # ... etc.
}
```

This unlocks:
- A canonical model for smoke tests (Step 6 below) — no per-test hardcoded model IDs.
- A fallback `model_name` when `AutoConfigurator` has no scraped data for a provider.
- A documented "try this provider" default surfaced in the daemon `/api/models` endpoint.

**Current workaround** (until `PROVIDER_FLAGSHIP_MODELS` exists): smoke tests and live tests hardcode the model ID. Search `tests/live/` for the pattern.

### Step 4 — Add tests (REQUIRED)

Edit `tests/unit/test_provider_presets.py`. Three changes:

1. **Add the provider key** to `NEW_GPU_PROVIDERS` (or a sibling list — the constant name is historical; today it covers all non-legacy providers).
2. **Add expected values** to the three registries:
   - `EXPECTED_URLS[provider] = "https://..."`
   - `EXPECTED_CREDENTIALS[provider] = "ENV_VAR_NAME"`
   - `EXPECTED_DISPLAY[provider] = "Display Name"`
3. The parametrised tests (`test_provider_has_all_required_fields`, `test_provider_uses_openai_compatible_stack`, `test_provider_api_base_url`, `test_provider_credential_env_var`, `test_provider_display_name`) pick up the new provider automatically.

For a non-OpenAI provider (Step 2 path), also override `test_provider_uses_openai_compatible_stack` for that provider — do not weaken the assertion for everyone.

Then run the gate (targeted, not main-thread):

```text
make test TESTFILE='tests/unit/test_provider_presets.py'
```

### Step 5 — Document in README (REQUIRED)

Update the provider table in `README.md` (currently around line 248) — add a row sorted alphabetically by display name with the env var. This is enforced as a hard gate by `scripts/check_readme_status_current.py` at release-cut time: a stale README status table blocks the release.

Also update the "Adding a New Provider" section in README.md (currently around line 275) if the new provider introduced a new integration pattern (custom connector, non-standard auth, etc.).

---

## 4. Schema reference — the 9 preset fields

| Field | Type | Example | Meaning |
|---|---|---|---|
| `api_base_url` | `str` | `"https://api.together.xyz/v1"` | Base URL the langchain adapter POSTs to. For OpenAI-compat providers this is the `/v1` root; `ChatOpenAI` appends `/chat/completions`. |
| `provider_package` | `str` | `"langchain-openai"` | **pip distribution name.** `ProviderRegistry._normalize_package` converts hyphens to underscores for the import (`langchain_openai`). Empty/None defaults to `langchain_openai`. |
| `provider_class` | `str` | `"ChatOpenAI"` | Class name imported from `provider_package`. `ProviderRegistry.get_provider_class()` does `getattr(import_module(pkg), class_name)`. |
| `credential_env_var` | `str` | `"TOGETHER_API_KEY"` | Conventional environment variable name. Use the provider's documented convention (e.g. Cohere is `CO_API_KEY`, not `COHERE_API_KEY`; Replicate is `REPLICATE_API_TOKEN`, not `_KEY`). |
| `credential_alias` | `str` | `"together_api_key"` | Internal alias key used in profile YAML and OpenBao secret paths. Convention: `<provider>_api_key`. |
| `api_base_alias` | `str` | `"together_api_base"` | Internal alias for the base URL in profile YAML. Convention: `<provider>_api_base`. |
| `display_name` | `str` | `"Together AI"` | Human-facing label shown in CLI, TUI, and `/api/models` responses. Use the provider's brand spelling. |
| `free_models_endpoint` | `str \| None` | `"https://api.together.xyz/v1/models"` | URL that returns a JSON model listing for auto-discovery. `None` if the provider has no public catalog. Currently only `openrouter` and `together` set this. |
| `supports_free_models` | `bool` | `True` | Whether `free_models_endpoint` should be polled. Must be `False` when `free_models_endpoint is None`. Inverted: must be `True` when endpoint is set. |

All 9 fields are enforced as required by `REQUIRED_FIELDS` in `tests/unit/test_provider_presets.py`. A preset missing any field fails the gate.

---

## 5. Provider audit — evaluating new candidates

The canonical evaluation lives at:

**[`docs/audit/GPU_PROVIDER_AUDIT_2026-07-06.md`](audit/GPU_PROVIDER_AUDIT_2026-07-06.md)**

It covers 22 candidates with: OpenAI-compat endpoint URL, env var, free tier availability, popularity signal, and an INCLUDE/SKIP recommendation with rationale. The top-10 INCLUDE list (Mistral, Cohere, NVIDIA NIM, Google Vertex, Hugging Face, Perplexity, Cloudflare Workers AI, Databricks, Azure AI Foundry, AI21) is the work queue — providers there but not yet in `PROVIDER_PRESETS` are pending integration.

**Re-running the audit for a new candidate:**

1. **Verify the API shape.** Fetch the provider's docs; confirm a `POST <base>/chat/completions` endpoint accepting `{model, messages, ...}` and returning `{choices: [{message: {content}}]}`. Cite the doc URL.
2. **Check the env var convention.** Find the canonical credential name from the provider's quickstart. Note any non-`_API_KEY` conventions (`_TOKEN`, `_API_TOKEN`).
3. **Confirm market signal.** Funding round, SDK presence in `openai-python` compatibility list, GitHub stars on their SDK, developer-mindshare markers (HN threads, conference talks).
4. **Check free tier.** Free inference credit? Free models endpoint? This determines `supports_free_models`.
5. **Decide INCLUDE / SKIP.** Record the decision in a new row in the audit table, or in a new audit file at `docs/audit/GPU_PROVIDER_AUDIT_<date>.md` if doing a quarterly refresh.
6. **If INCLUDE**, proceed to the 5-step process above.

**Skip-list anti-patterns to recognise quickly:** GPU rental (no hosted API), data platforms (no model hosting), pivoted providers (public LLM API deprecated), academic projects (no SLA), duplicate surfaces (covered by an existing provider key).

---

## 6. Testing a provider — smoke test pattern

A smoke test verifies the credential works end-to-end against the live provider API with the minimum-cost request. It belongs in `tests/live/` (the `live/` directory is the convention for tests that hit real external APIs and are skipped in CI by default).

**Pre-`PROVIDER_FLAGSHIP_MODELS` pattern** (works today):

```python
# tests/live/test_newprovider_live.py
"""Live smoke test for <provider>. Skipped unless NEWPROVIDER_API_KEY is set."""

from __future__ import annotations

import os
import pytest

from general_ludd.models.provider_presets import get_provider_preset


def _client():
    preset = get_provider_preset("newprovider")
    from langchain_openai import ChatOpenAI  # provider_package + provider_class
    return ChatOpenAI(
        model="newprovider-flagship-model-id",  # TODO: replace with PROVIDER_FLAGSHIP_MODELS lookup
        api_key=os.environ[preset["credential_env_var"]],
        base_url=preset["api_base_url"],
        max_tokens=1,           # 1-token completion = minimum-cost smoke probe
        timeout=15,
    )


@pytest.mark.skipif(
    not os.environ.get("NEWPROVIDER_API_KEY"),
    reason="NEWPROVIDER_API_KEY not set; live smoke test skipped",
)
def test_newprovider_flagship_responds():
    resp = _client().invoke([{"role": "user", "content": "ping"}])
    assert resp.content, "empty response from flagship model"
```

**Target pattern** (after Step 3 lands):

```python
from general_ludd.models.provider_presets import PROVIDER_FLAGSHIP_MODELS, get_provider_preset

model_id = PROVIDER_FLAGSHIP_MODELS["newprovider"]
# ... rest unchanged, with model=model_id
```

**Run locally** (never in CI — live tests hit real APIs and cost money):

```text
make test TESTFILE='tests/live/test_newprovider_live.py'
```

Or export the key and invoke directly:

```text
export NEWPROVIDER_API_KEY=...
make test TESTFILE='tests/live/test_newprovider_live.py'
```

**What the smoke test must prove:**
1. The credential env var resolves.
2. The `api_base_url` accepts the request (no 404, no auth redirect).
3. The flagship model returns a non-empty completion within the timeout.
4. The response shape is parseable by `langchain-openai` (no schema drift).

A failure on (2) usually means the URL is wrong or the provider changed it. A failure on (3) means the model ID is wrong or the provider's free tier is exhausted. A failure on (4) means the provider's OpenAI-compat layer diverged from the spec — file a bug and consider Step 2 (custom connector).

---

## Appendix — File touch-list for a standard onboarding

For the common case (OpenAI-compatible provider, no custom connector), a complete onboarding touches exactly four files:

| File | Change |
|---|---|
| `src/general_ludd/models/provider_presets.py` | Add 9-field entry to `PROVIDER_PRESETS` (Step 1). Optionally add to `PROVIDER_FLAGSHIP_MODELS` once it exists (Step 3). |
| `tests/unit/test_provider_presets.py` | Add provider key to `NEW_GPU_PROVIDERS` + entries to `EXPECTED_URLS`, `EXPECTED_CREDENTIALS`, `EXPECTED_DISPLAY` (Step 4). |
| `tests/live/test_<provider>_live.py` | New file — smoke test gated on the env var (Step 6). |
| `README.md` | Add row to the provider table in the "Supported providers" section (Step 5). |

No daemon code change. No router code change. No migration. `ProviderRegistry.from_presets()` discovers the new provider automatically on the next daemon boot.
