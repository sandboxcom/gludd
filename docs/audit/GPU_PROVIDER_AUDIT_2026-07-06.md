# GPU / Compute Provider Audit — OpenAI-Compatible LLM Hosts

**Date:** 2026-07-06
**Scope:** Providers offering hosted LLM inference with an OpenAI-compatible REST API, candidates for gludd's "service options" provider list.
**Method:** Each candidate verified via live doc/site fetch where possible (sources cited inline). Popularity judged by market presence, funding, GitHub/SDK traction, and developer mindshare.

---

## Already integrated (no action — listed for context)

`openrouter`, `openai`, `anthropic`, `zai`, `groq`, `deepseek`, `baseten`, `lambdalabs`, `together`, `fireworks`, `replicate`, `runpod`, `modal`, `coreweave`.

---

## Full candidate table

| # | Provider | URL | OpenAI-compatible endpoint | Env var | Free tier | Popular? | Recommendation |
|---|---|---|---|---|---|---|---|
| 1 | **Mistral (La Plateforme)** | https://console.mistral.ai | `https://api.mistral.ai/v1/chat/completions` (native OpenAI shape) | `MISTRAL_API_KEY` | Yes (free tier on Studio) | Very — €1B+ funding, frontier open-weight models (Magistral, Mistral Large), first-class OpenAI SDK docs | **INCLUDE** |
| 2 | **Cohere** | https://cohere.com | `https://api.cohere.ai/compatibility/v1/chat/completions` (OpenAI-compat layer; also native `/v2/chat`) | `CO_API_KEY` | Yes (developer trial keys) | Very — command-r family, enterprise RAG/embeddings leader, SDK in most OpenAI client libs | **INCLUDE** |
| 3 | **NVIDIA NIM (build.nvidia.com)** | https://build.nvidia.com | `https://integrate.api.nvidia.com/v1/chat/completions` | `NVIDIA_API_KEY` (or `NVIDIA_BUILD_API_KEY`) | Yes (free inference on hosted models) | Very — Nemotron, Llama, DeepSeek, GLM hosted; NIM is the reference inference server for the industry | **INCLUDE** |
| 4 | **Google Vertex AI / Gemini API** | https://cloud.google.com/vertex-ai | `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions` (Gemini API OpenAI mode); Vertex also exposes partner models (Claude, Llama, Mistral) | `GOOGLE_API_KEY` (Gemini) / `GCP_PROJECT_ID` + ADC (Vertex) | Yes (Gemini free tier) | Massive — Gemini 3, Llama 4, Claude, Mistral all callable from one surface | **INCLUDE** |
| 5 | **Hugging Face Inference Endpoints** | https://huggingface.co/inference-endpoints | `<endpoint-url>/v1/chat/completions` when deploying with TGI/vLLM OpenAI-compatible container | `HF_TOKEN` | Limited (free Serverless Inference API for small models; Endpoints are paid) | Massive — 1M+ models on the Hub, default open-source inference destination | **INCLUDE** |
| 6 | **Perplexity API** | https://docs.perplexity.ai | `https://api.perplexity.ai/chat/completions` (Sonar / online models) | `PERPLEXITY_API_KEY` | Yes ($5 free credit on signup) | High — Sonar models widely used; OpenAI SDK works drop-in | **INCLUDE** |
| 7 | **Cloudflare Workers AI** | https://developers.cloudflare.com/workers-ai | `https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1` (OpenAI-compat REST surface) | `CLOUDFLARE_API_TOKEN` (acct id also needed) | Yes (10k neurons/day free) | High — global edge inference, pay-per-use, 50+ open models | **INCLUDE** |
| 8 | **Databricks Foundation Model APIs** | https://docs.databricks.com/aws/en/generative-ai/foundation-model-apis | `<workspace>.cloud.databricks.com/serving-endpoints` (OpenAI client compat via `databricks-sdk`) | `DATABRICKS_TOKEN` + `DATABRICKS_HOST` | No (workspace-bound; consumption-billed) | High in enterprise — DBRX, Llama, Mixtral; OpenAI client compatibility is documented | **INCLUDE** |
| 9 | **Microsoft Azure AI Foundry (non-OpenAI models)** | https://ai.azure.com | `https://<region>.models.ai.azure.com` (serverless API for Llama/Mistral/Phi/BlackwellLab) | `AZURE_AI_API_KEY` | Free on some models (Phi etc.) | Massive — Microsoft's multi-model gateway distinct from Azure OpenAI | **INCLUDE** |
| 10 | **AI21 Labs (Jamba / Maestro)** | https://www.ai21.com | `https://api.ai21.com/studio/v1/chat/completions` (OpenAI-compat) | `AI21_API_KEY` | Yes (free trial credits) | Moderate — Jamba SSM-Transformer hybrid, decent dev adoption; smaller than Mistral/Cohere | **INCLUDE** (lower priority) |
| 11 | Vast.ai | https://vast.ai | None first-class — Serverless + Model Library are marketplace-focused, not a hosted chat API | `VAST_API_KEY` | No | Moderate popularity, but **wrong product shape** (GPU rental, not hosted OpenAI-compat inference) | **SKIP** |
| 12 | Amazon SageMaker JumpStart | https://aws.amazon.com/sagemaker/jumpstart | Only via LMI/DJL/vLLM container on a SageMaker endpoint you deploy yourself (per-model URL, not a flat `/v1/chat/completions` host) | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` | No (consumption) | Very popular infra, but **not a drop-in OpenAI-compat host** without extra work | **SKIP** (optional: document as BYO-endpoint pattern) |
| 13 | Microsoft Azure OpenAI | https://azure.microsoft.com/en-us/products/ai-services/openai-service | `https://<resource>.openai.azure.com/openai/deployments/<dep>/chat/completions?api-version=...` | `AZURE_OPENAI_API_KEY` | No | Massive, but **not a distinct provider** from `openai` semantically (same models, same gateway shape) | **SKIP** (covered by `openai`) |
| 14 | Anyscale | https://anyscale.com | Anyscale Endpoints (public LLM API) deprecated 2024; now Ray/agent infra only | n/a | n/a | Was popular for LLM API; pivoted away from public model hosting | **SKIP** |
| 15 | LightOn | https://lighton.ai | Now a **RAG/OCR API** (`/parse`, `/extract`, `/search`), not an OpenAI-compat chat completions host | `LIGHTON_API_KEY` | Yes | Niche FR provider; pivoted away from LLM hosting | **SKIP** |
| 16 | OVHcloud AI | https://www.ovhcloud.com/en/public-cloud/ai/ | Infra-only (AI Deploy / AI Training) — no hosted chat-completions API | n/a | No | EU sovereign GPU rental, **not an LLM API service** | **SKIP** |
| 17 | Scale AI | https://scale.com | No public OpenAI-compat model API — they sell data engine + GenAI platform (host-agnostic) | n/a | No | Huge company, wrong product (data + integration, not model hosting) | **SKIP** |
| 18 | Aleph Alpha | https://www.aleph-alpha.com | No public OpenAI-compat API — pivoted to private sovereign SLLMs for EU enterprises/govt | n/a | No | Niche; Luminous public API effectively deprecated | **SKIP** |
| 19 | Banana.dev | https://www.banana.dev | **DEFUNCT** — site shows "Sunset" banner; no longer accepting customers | n/a | n/a | Was a YC GPU-hosting startup; shut down | **SKIP** |
| 20 | Petals | https://petals.dev | Decentralized BitTorrent-style P2P inference — **not a hosted API service** | n/a | Free (community-run) | Academic project, not commercial | **SKIP** |
| 21 | Hyperstack (formerly Paperspace/Crucial) | https://www.hyperstack.co | GPU cloud rental (VM/cluster), no hosted chat-completions API | n/a | No | Niche infra | **SKIP** |
| 22 | Lambda Labs (already integrated) | https://lambdalabs.com | `https://api.lambdalabs.com/v1` (Lambda Inference API, OpenAI-compat) | `LL_API_KEY` | — | Already in gludd | **CONFIRMED** |

---

## Top 10 recommendations (ranked by popularity × OpenAI-compat × ease of integration)

| Rank | Provider | Why |
|---|---|---|
| 1 | **Mistral (La Plateforme)** | Frontier open-weight models, first-class OpenAI SDK compat, free tier, huge dev mindshare. |
| 2 | **Cohere** | Command-R family, RAG/embeddings leadership, explicit OpenAI-compat endpoint, mature SDK. |
| 3 | **NVIDIA NIM** | Industry-reference inference server, free hosted inference for Llama/DeepSeek/GLM/Nemotron, true OpenAI shape. |
| 4 | **Google Vertex AI / Gemini API** | Gemini 3 + Llama 4 + Claude + Mistral from one OpenAI-compat surface; massive reach. |
| 5 | **Hugging Face Inference Endpoints** | Default open-source model hub; TGI/vLLM containers expose native `/v1/chat/completions`. |
| 6 | **Perplexity API** | Online/Sonar models, drop-in OpenAI SDK, generous free credits. |
| 7 | **Cloudflare Workers AI** | Global edge inference, real free tier (10k neurons/day), OpenAI-compat REST surface. |
| 8 | **Databricks Foundation Model APIs** | Enterprise standard, DBRX + Llama + Mixtral, documented OpenAI client compatibility. |
| 9 | **Microsoft Azure AI Foundry** | Multi-model gateway distinct from Azure OpenAI; hosts Llama/Mistral/Phi with OpenAI shape. |
| 10 | **AI21 Labs** | Jamba hybrid SSM-Transformer, OpenAI-compat endpoint, smaller but differentiated architecture. |

---

## Skip list (with one-line rationale)

- **Banana.dev** — defunct (sunset announced).
- **Petals** — P2P academic project, not a hosted API.
- **Vast.ai / Hyperstack / OVHcloud AI** — GPU rental / infra, not OpenAI-compat model APIs.
- **Scale AI** — sells data + integration platform, not model hosting.
- **Aleph Alpha / LightOn** — pivoted away from public LLM APIs (sovereign enterprise SLLM / RAG-only).
- **Anyscale** — public LLM Endpoints deprecated 2024; now Ray infra.
- **SageMaker JumpStart** — powerful but requires self-deploy per model; not drop-in OpenAI-compat. Document as a BYO-endpoint pattern if a user asks.
- **Azure OpenAI** — same models as `openai`; covered by the existing `openai` provider.

---

## Sources (live fetches, 2026-07-06)

- Hugging Face Inference Endpoints: https://huggingface.co/docs/inference-endpoints/index
- Vast.ai pricing/product page: https://vast.ai/pricing
- Mistral docs (chat completions): https://docs.mistral.ai/capabilities/completion/
- Cohere Chat API (v2 + compat layer): https://docs.cohere.com/docs/chat-api
- Perplexity API overview: https://docs.perplexity.ai
- AI21 Labs (product pivot to Maestro/Jamba): https://www.ai21.com
- Together AI OpenAI-compat reference (already integrated; included as control): https://docs.together.ai/docs/openai-api-compatibility
- Banana.dev sunset page: https://www.banana.dev/blog/sunset
- Petals homepage: https://petals.dev
- AWS SageMaker JumpStart: https://docs.aws.amazon.com/sagemaker/latest/dg/jumpstart-foundation-models.html
- LightOn (RAG/OCR pivot): https://lighton.ai
- Aleph Alpha (sovereign SLLM pivot, German-only site): https://www.aleph-alpha.com
- Anyscale docs (Ray platform, no public LLM API): https://docs.anyscale.com
- Cloudflare Workers AI: https://developers.cloudflare.com/workers-ai
- NVIDIA build.nvidia.com (live, free inference for GLM-5.2 / Nemotron / DeepSeek): https://build.nvidia.com
- Google Vertex AI / Gemini (OpenAI library migration path confirmed in docs nav): https://cloud.google.com/vertex-ai/docs/start/explore-models
- Scale AI (data engine, no public model API): https://www.scale.com

---

*Research-only deliverable. No source files modified.*
