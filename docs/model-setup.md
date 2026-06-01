# Model Configuration Guide

## Overview

General Ludd Agent supports multiple model providers including OpenAI, Z.AI (ZAI), and local inference engines (llama.cpp, vllm). Models can be added/removed at runtime via the admin API or CLI.

## OpenAI Setup

### 1. Get an API Key

Sign up at https://platform.openai.com and create an API key.

### 2. Configure via Environment Variable

```bash
export OPENAI_API_KEY="sk-..."
```

### 3. Add Model Profile via API

```bash
# Add GPT-4o
curl -X POST http://localhost:8000/admin/models \
  -H "Content-Type: application/json" \
  -d '{
    "model_id": "openai-gpt4o",
    "provider": "openai",
    "model": "gpt-4o",
    "api_key_env": "OPENAI_API_KEY"
  }'

# Add GPT-4o-mini
curl -X POST http://localhost:8000/admin/models \
  -H "Content-Type: application/json" \
  -d '{
    "model_id": "openai-gpt4o-mini",
    "provider": "openai",
    "model": "gpt-4o-mini",
    "api_key_env": "OPENAI_API_KEY"
  }'

# Add o3-mini
curl -X POST http://localhost:8000/admin/models \
  -H "Content-Type: application/json" \
  -d '{
    "model_id": "openai-o3-mini",
    "provider": "openai",
    "model": "o3-mini",
    "api_key_env": "OPENAI_API_KEY"
  }'
```

### 4. Verify

```bash
curl http://localhost:8000/admin/models
```

### 5. OpenAI Subscription Cost Reference

| Plan | Monthly Cost | Tokens/Week (approx) | Notes |
|------|-------------|---------------------|-------|
| Free | $0 | Limited | Rate-limited |
| Tier 1 | $5/mo | ~500K/week | Usage capped |
| Tier 2 | $50/mo | ~5M/week | Higher limits |
| Usage-based | Pay-per-token | Unlimited | GPT-4o: $2.50/1M input, $10/1M output |

Use the cost estimation endpoint to track spending:
```bash
curl "http://localhost:8000/admin/metrics/cost?subscription_name=OpenAI%20Tier%202&subscription_cost_per_month=50&tokens_per_week=5000000"
```

## Z.AI Setup

### 1. Get an API Key

Sign up at https://z.ai and create a subscription plan.

### 2. Configure via Environment Variable

```bash
export ZAI_API_KEY="zai-..."
```

### 3. Add Model Profile via API

Z.AI provides an OpenAI-compatible API. Configure it as an OpenAI provider with a custom base URL:

```bash
# Add ZAI GLM-5
curl -X POST http://localhost:8000/admin/models \
  -H "Content-Type: application/json" \
  -d '{
    "model_id": "zai-glm5",
    "provider": "openai",
    "model": "glm-5.1",
    "api_key_env": "ZAI_API_KEY",
    "api_base_alias": "ZAI_BASE_URL"
  }'
```

You also need to set the base URL:
```bash
export ZAI_BASE_URL="https://api.z.ai/v1"
```

### 4. Z.AI Subscription Cost Reference

| Plan | Monthly Cost | Tokens/Week (approx) | Notes |
|------|-------------|---------------------|-------|
| Free | $0 | Limited | Rate-limited |
| Pro | $20/mo | ~2M/week | Higher throughput |
| Team | $50/mo | ~10M/week | Priority access |
| Enterprise | Custom | Custom | SLA guaranteed |

### 5. Cost Tracking

```bash
curl "http://localhost:8000/admin/metrics/cost?subscription_name=Z.AI%20Pro&subscription_cost_per_month=20&tokens_per_week=2000000"
```

## Local Inference (llama.cpp)

### 1. Install llama-cpp-python

```bash
# With CUDA support
CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python

# With Metal (macOS)
CMAKE_ARGS="-DGGML_METAL=on" pip install llama-cpp-python
```

### 2. Download a Model

```bash
gludd models search "llama gguf" --limit 10
gludd models download "TheBloke/Llama-2-7B-GGUF" --filename "llama-2-7b.Q4_K_M.gguf"
```

### 3. Start Local Server

```bash
gludd local-serve --engine llamacpp --model ~/.cache/general-ludd/models/llama-2-7b.Q4_K_M.gguf --port 8001
```

### 4. Add as Model Profile

```bash
curl -X POST http://localhost:8000/admin/models \
  -H "Content-Type: application/json" \
  -d '{
    "model_id": "local-llama2-7b",
    "provider": "openai",
    "model": "llama-2-7b",
    "api_key_env": "NONE",
    "api_base_alias": "LOCAL_LLAMA_URL"
  }'

export LOCAL_LLAMA_URL="http://localhost:8001/v1"
```

## Local Inference (vllm)

### 1. Install vllm

```bash
pip install vllm
```

### 2. Download a Model

```bash
gludd models download "meta-llama/Llama-3.1-8B-Instruct"
```

### 3. Start Local Server

```bash
gludd local-serve --engine vllm --model meta-llama/Llama-3.1-8B-Instruct --port 8002
```

### 4. Add as Model Profile

```bash
curl -X POST http://localhost:8000/admin/models \
  -H "Content-Type: application/json" \
  -d '{
    "model_id": "local-llama3-8b",
    "provider": "openai",
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "api_key_env": "NONE",
    "api_base_alias": "LOCAL_VLLM_URL"
  }'

export LOCAL_VLLM_URL="http://localhost:8002/v1"
```

## Removing a Model

```bash
curl -X DELETE http://localhost:8000/admin/models/openai-gpt4o
```

## Hot-Reloading Configuration

After changing config files on disk:

```bash
curl -X POST http://localhost:8000/admin/reload -H "Content-Type: application/json" -d '{"scope": "models"}'
```

## Webhook Notifications

Register a webhook to get notified when models change:

```bash
curl -X POST http://localhost:8000/admin/hooks \
  -H "Content-Type: application/json" \
  -d '{
    "event_name": "on_model_added",
    "url": "https://your-service.example.com/webhook",
    "headers": {"Authorization": "Bearer your-token"}
  }'
```

## Model Routing

Configure role-based routing in `config/model_routing.yml`:

```yaml
default_profile: openai-gpt4o
role_routing:
  coder: openai-gpt4o
  reviewer: zai-glm5
  weak: openai-gpt4o-mini
pattern_routing:
  code_generation: coder
  code_review: reviewer
  quick_check: weak
```

After editing, reload:
```bash
curl -X POST http://localhost:8000/admin/reload -d '{"scope": "models"}'
```
