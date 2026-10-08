# Model Profiles Reference

Configuration guide for model profiles, routing, and versioning in General Ludd.

## Contents

- [Profile Configuration Guide](../profiles.md) — Complete guide to configuring model profiles, routing, and versioning

## Quick Reference

### Profile File Location
```text
config/model_profiles/
  ├── strong_coder.yml
  ├── cheap_coder.yml
  └── openrouter_reviewer.yml
```

### Routing File Location
```text
config/model_routing.yml
```

### Profile Schema (from `ModelProfile`)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model_profile_id` | str | Yes | Unique ID used everywhere |
| `role_names` | list[str] | No | Roles this profile serves |
| `provider` | str | No | Provider type (default: "openai") |
| `provider_package` | str | No | LangChain package |
| `provider_class_hint` | str | No | LangChain class (default: "ChatOpenAI") |
| `model_name` | str | Yes | Model identifier for API |
| `api_base_alias` | str\|None | No | Secret alias for base URL |
| `credential_alias` | str\|None | No | Secret alias for API key |
| `context_window` | int | No | Token context window |
| `cost_per_input_token` | float | No | USD per input token |
| `cost_per_output_token` | float | No | USD per output token |
| `run_budget_usd` | float | No | Per-run spending cap |
| `enabled` | bool | Yes | Must be true for gateway to call |
| `fallback_profiles` | list[str] | No | Fallback profile IDs |

### Routing Schema (from `ModelRoutingConfig`)

| Field | Type | Description |
|-------|------|-------------|
| `default_profile` | str\|None | Fallback when no rule matches |
| `weak_model_profile` | str\|None | Used when role == "weak" |
| `role_routing` | dict[str,str] | Role name → profile ID |
| `quality_routing` | dict[str,str] | Quality class → profile ID |
| `latency_routing` | dict[str,str] | Latency class → profile ID |
| `pattern_routing` | dict[str,str] | Pattern name → role name |
| `fallback_chain` | list[str] | Ordered fallback profile IDs |

---

[Back to Documentation Index](../index.md)
