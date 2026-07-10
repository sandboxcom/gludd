# E2E Harness Designs

End-to-end test harness designs for dogfood and multi-provider testing.

## Contents

| Document | Description |
|----------|-------------|
| [Native Dogfood Harness](DESIGN_native_dogfood_harness.md) | Self-host + greenfield todo website scenarios |
| [Local Cloud Providers E2E](DESIGN_local_cloud_providers_e2e.md) | Ollama, vLLM, llama.cpp, Slurm, Azure E2E |
| [Build Plan](BUILD_PLAN.md) | Ordered, dependency-aware build plan |
| [Model Discovery Weights Cost Audit](AUDIT_model_discovery_weights_cost.md) | Model discovery, weights, cost audit |

## Quick Reference

### Dogfood Scenarios
1. **Self-host** — gludd makes a meaningful change to its own repo
2. **Greenfield** — gludd builds a todo website from scratch

### Provider E2E Tests
| Backend | Env Var | Skip Gate | Notes |
|---------|---------|-----------|-------|
| Ollama | `OLLAMA_BASE_URL` | Config + HTTP `/v1/models` | Native `/api/tags` = new adapter |
| vLLM | `VLLM_BASE_URL` | Config + HTTP `/v1/models` | `VLLM_E2E_SPAWN=1` for real spawn |
| llama.cpp | `LLAMACPP_BASE_URL` | Config + HTTP `/v1/models` | `LLAMACPP_E2E_SPAWN=1` for real spawn |
| Slurm | `SLURM_E2E=1` / `SLURM_REST_URL` | `SlurmAdapter.available()` | Job submit/poll/cancel |
| Azure (env) | `AZURE_BASE_URL` | Config + HTTP `/v1/models` | Full SSRF path, metered billing |
| Azure (provision) | `AZURE_PROVISION_E2E=1` | Cost-gated, manual | Full terraform up/serve/destroy |

### Make Targets
```bash
make test-e2e-dogfood        # Dogfood scenarios (mock by default)
make dogfood-live            # Dogfood with live ZAI key
make dogfood-site            # Greenfield todo website only
make test-e2e-providers      # All provider tests (skip if not configured)
make test-e2e-ollama         # Single backend
make test-e2e-vllm
make test-e2e-llamacpp
make test-e2e-slurm
make test-e2e-azure
make test-e2e-providers-local  # Local backends with GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS=1
make test-e2e-azure-provision  # Full provision (scheduled only)
```

---

[Back to Documentation Index](../index.md)
