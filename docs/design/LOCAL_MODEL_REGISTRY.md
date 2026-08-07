# Local Model Registry

**Source:** `tests/e2e/_local_model_configs.py` (E2E model entries),
`src/general_ludd/local_model/_local_model_configs.py` (production `LocalModelConfig`).

24 GGUF models registered for local inference, categorized by capability and
gated for CI safety.  Every entry is an `E2EModelEntry` dataclass carrying
name, HuggingFace repo, filename, size (MB), category, context size, CI-safe
flag, and searchable aliases.

## Model Schema (`E2EModelEntry`)

```python
@dataclass(frozen=True)
class E2EModelEntry:
    name: str              # Display name (e.g. "Qwen2.5-Coder-0.5B")
    repo: str              # HuggingFace repo (e.g. "bartowski/Qwen2.5-Coder-0.5B-Instruct-GGUF")
    filename: str          # GGUF filename (e.g. "Qwen2.5-Coder-0.5B-Instruct-Q4_K_M.gguf")
    size_mb: int           # Estimated file size in MB
    category: Category     # "coding" | "general"
    context_size: int      # Max context window (default 2048)
    ci_safe: bool          # True → under ~500 MB, safe for CI runners
    aliases: tuple[str, ...]  # Short names for lookup (e.g. ("qwen-coder-0.5b",))
```

The `to_local_model_config()` method strips the entry down to the production
`LocalModelConfig` (name, repo, filename, context_size).

## Category: coding / general

| Category | Count | Purpose |
|----------|-------|---------|
| `coding` | 8 | Code generation models (Qwen2.5-Coder, DeepSeek-Coder, StarCoder2, CodeLlama, Phi-3-mini, SmolLM2) |
| `general` | 16 | General-purpose reasoning / chat models (Qwen2.5, Llama-3.2, Gemma-2, Mistral-7B, Phi-2/3.5, OLMoE, InternLM3, StableLM) |

## CI-Safe vs Dev-Only

**CI-safe** models (6 total, all under 500 MB):

| Model | Size |
|-------|------|
| SmolLM2-135M | 88 MB |
| SmolLM2-360M | 224 MB |
| Qwen2.5-Coder-0.5B | 312 MB |
| Qwen2.5-0.5B | 316 MB |
| Phi-2 | 487 MB |
| TinyLlama-1.1B | 496 MB |

All other models (18) are `ci_safe=False` — intended for development workstations
with more disk and memory.

## Role-Based Model Suggestions

`get_models_by_role()` assigns models to three agent roles:

| Role | Selection Rule | Count |
|------|---------------|-------|
| **CODER** | All `category == "coding"` models | 8 |
| **PLANNER** | All `category == "general"` except SmolLM2-135M (too small); fallback: general models ≥500 MB | 15+ |
| **REVIEWER** | General models with `context_size >= 8192`; fallback: general models ≥1000 MB | 12+ |

Fallbacks kick in when the primary criteria produce an empty list (e.g. if the
registry were filtered down to tiny models).

## Environment Variable Filters

### `LOCAL_MODEL_FILTER`

Comma-separated tokens applied by `_apply_filters()`.  Each token narrows the
model list cumulatively:

| Token | Effect |
|-------|--------|
| `coding` | Keep only `category == "coding"` |
| `general` | Keep only `category == "general"` |
| `ci-safe` | Keep only `ci_safe == True` |
| `<500mb` | Alias for `ci-safe` (same filter) |
| `<model-name-or-alias>` | Keep only the named model |

Tokens are ANDed together: `LOCAL_MODEL_FILTER=coding,ci-safe` returns only
coding models that are CI-safe (i.e. Qwen2.5-Coder-0.5B).

### `E2E_LOCAL_MODEL`

Single model name filter used by `get_e2e_configs()`.  Returns configs for that
one model only.  If unset, returns all 24.

## Adding a New Model

1. Add an `E2EModelEntry` to the `_MODELS` list in `tests/e2e/_local_model_configs.py`.
2. Choose `ci_safe=True` only if the GGUF file is under ~500 MB.
3. Provide at least one short alias for `LOCAL_MODEL_FILTER` lookup.
4. Add a `LocalModelConfig` entry to the production list in
   `src/general_ludd/local_model/_local_model_configs.py` if the model should
   be available in non-E2E paths.
5. Run `make test-unit TESTFILE=tests/e2e/_local_model_configs.py` to verify
   the registry index (`_MODEL_BY_NAME`, `_MODEL_BY_ALIAS`, `_ALIAS_MAP`) stays
   consistent.

## Key Functions

| Function | Returns |
|----------|---------|
| `list_models(category, ci_safe)` | Filtered `list[E2EModelEntry]` with `LOCAL_MODEL_FILTER` applied |
| `get_all_configs()` | `list[LocalModelConfig]` for all filtered models |
| `get_e2e_configs()` | `list[LocalModelConfig]` filtered by `E2E_LOCAL_MODEL` env var |
| `get_models_by_role()` | `dict[str, list[E2EModelEntry]]` keyed by `PLANNER`/`CODER`/`REVIEWER` |
| `model_count()` | Total model count (24) |
| `category_counts()` | `dict` with `total`, `coding`, `general`, `ci_safe` counts |
