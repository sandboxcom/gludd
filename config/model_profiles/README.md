# Model Profiles

Model profiles configure how gludd talks to LLM providers (cloud APIs, local
servers, or routing services). Each `.yml` file in this directory is one
profile; the daemon loads all enabled profiles at startup and the
`model_routing` config maps agent roles (`coder`, `reviewer`, `planner`,
`compactor`) to profile IDs.

## Choosing the right profile

| Situation | Recommended profile | Why |
|---|---|---|
| Highest-quality coding, cost no object | `anthropic_claude` | Claude Sonnet 4, 200K context, top-tier coding |
| Cost-sensitive cloud coding | `deepseek_coder` | ~10× cheaper than Sonnet, strong on code |
| Multi-provider routing, one key | `openrouter_coder` | Swap models without re-issuing keys |
| OpenAI ecosystem, GPT-4 features | `openai_gpt4` | Function calling, JSON mode, 128K context |
| ZhipuAI / GLM ecosystem | `zai_coder` | OpenAI-compatible, low latency in CN |
| Local GPU (24GB+ VRAM) | `vllm_local` | High-throughput local inference, no per-token cost |
| Local CPU / low-RAM workstation | `llamacpp_local` | GGUF quantized models, runs anywhere |
| Background compaction / summaries | `compactor` | Tiny SLM, near-zero cost, fast |

**Selection order of operations:**

1. Pick the **role** the profile will serve (coder, reviewer, planner,
   compactor). Compactor should always use `compactor.yml`.
2. Decide **cloud vs local** (see tradeoffs below).
3. Within that tier, pick by **cost** then **quality** then **latency**.
4. Wire the chosen `model_profile_id` into `config/model_routing.yml` under
   the role name.
5. Set the credentials listed in the profile header comment.

## Adding a custom provider

1. **Copy a close match.** Cloud API → copy `openai_example.yml`; local
   server → copy `vllm_example.yml`. Rename the file to match the provider.
2. **Edit the header comment** to describe what the profile does and which
   environment variables are required.
3. **Set the required fields:**

   | Field | What to put |
   |---|---|
   | `model_profile_id` | `<provider>_<role>` (e.g. `mistral_coder`) — must be unique across all profiles |
   | `provider` | LangChain provider key (e.g. `mistralai`, `openai`, `anthropic`) |
   | `provider_package` | pip package (e.g. `langchain-mistralai`) |
   | `provider_class_hint` | LangChain chat class (e.g. `ChatMistralAI`) |
   | `model_name` | Exact string the provider's API expects |
   | `credential_alias` | Env var name holding the API key (omit for local) |
   | `api_base_alias` | Env var name holding the API base URL (OpenAI-compatible only) |
   | `context_window` | Total tokens (input + output) the model supports |
   | `max_input_tokens` | ~95% of `context_window` minus max output |
   | `cost_per_input_token` | USD per 1M input tokens (use `0.0` for local) |
   | `cost_per_output_token` | USD per 1M output tokens (use `0.0` for local) |

4. **Set the optional fields** (`run_budget_usd`, `latency_class`,
   `quality_class`, `fallback_profiles`, `probe_enabled`).
5. **Validate syntax:** `make ansible-syntax` (profiles are loaded as YAML
   by the daemon; any parse error will block startup).
6. **Test with probe:** temporarily set `probe_enabled: true` and boot the
   daemon — the health check will hit the endpoint and surface auth/network
   errors before any agent tries to use the profile.
7. **Wire it in:** add the `model_profile_id` to `config/model_routing.yml`.

## Cost comparison

Costs are USD per 1M tokens (input / output). Local profiles have zero
per-token cost but consume hardware. Values reflect provider pricing at
profile-creation time — verify current pricing before relying on budget
calculations.

| Profile | Input $/1M | Output $/1M | Run budget | Metered | Enabled |
|---|---|---|---|---|---|
| `anthropic_claude`    | $3.00   | $15.00  | $50.00 | yes | yes |
| `openai_gpt4`         | $30.00  | $60.00  | $50.00 | yes | yes |
| `openrouter_coder`    | $3.00   | $15.00  | $30.00 | yes | yes |
| `zai_coder`           | $1.00   | $3.00   | $1.00  | no  | yes |
| `deepseek_coder`      | $0.27   | $1.10   | $5.00  | yes | yes |
| `qwen_coder`          | $0.50   | $1.50   | $1.00  | yes | no  |
| `vllm_local`          | $0.00   | $0.00   | $0.00  | no  | no  |
| `llamacpp_local`      | $0.00   | $0.00   | $0.00  | no  | no  |
| `compactor`           | $0.00   | $0.00   | $0.00  | no  | yes |

**Cheapest cloud:** `deepseek_coder` (~100× cheaper than `openai_gpt4`).
**Cheapest overall:** any local profile ($0/token, but you pay in hardware).

## Local vs cloud tradeoffs

| Dimension | Cloud (API) | Local (vLLM / llama.cpp) |
|---|---|---|
| **Setup** | Get an API key, set env var | Provision GPU/CPU, install server, download weights |
| **Per-token cost** | $0.27 – $30 / 1M input | $0 |
| **Fixed cost** | $0 | Hardware + electricity |
| **Latency** | Network-bound (50–500ms TTFT) | GPU: 20–100ms; CPU: 1–10s |
| **Quality ceiling** | State-of-the-art (Claude, GPT-4) | Capped by what fits in VRAM/RAM |
| **Context window** | Up to 200K+ | Limited by VRAM (8K – 32K typical) |
| **Privacy** | Data leaves your machine | Data stays local |
| **Reliability** | Provider uptime, rate limits | Your hardware uptime |
| **Offline use** | No | Yes |

**Rule of thumb:** use cloud for the coder/reviewer/planner roles (quality
matters most); use local for the compactor role (cheap summaries of data
you already have) and for air-gapped / privacy-sensitive deployments.

## Field reference

See the header comment at the top of any `.yml` file in this directory for
the canonical field list. The full schema is enforced by
`src/general_ludd/config/model_profile.py` — invalid or missing required
fields will fail daemon startup with a clear error message.

**Required fields:** `model_profile_id`, `provider`, `model_name`,
`context_window`. Cloud providers additionally require `credential_alias`.
OpenAI-compatible endpoints additionally accept `api_base_alias`.
