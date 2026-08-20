# Local Model Testing & E2E Validation

**Status:** beta4 validated (2026-08-20)
**Source:** `tests/e2e/test_ci_multi_model_pipeline.py`, `tests/e2e/test_model_matrix_pipeline.py`,
`tests/e2e/_local_model_configs.py`, `src/general_ludd/local_model/_local_model_configs.py`

How to run the full model validation suite: cloud API models (DeepSeek, OpenRouter, Anthropic,
self-hosted), local GGUF models via llama.cpp, and the unified model matrix report.

## 1. Cloud API Models (API-key gated)

### Key loading (env → file fallback)

| Provider | Env var | Key file (repo root) | Base URL |
|----------|---------|-----------------------|----------|
| DeepSeek | `DEEPSEEK_API_KEY` | `.deepseek.key` | `https://api.deepseek.com/v1` |
| OpenRouter | `OPENROUTER_API_KEY` | `.openrouter.key` | `https://openrouter.ai/api/v1` |
| Anthropic | `ANTHROPIC_API_KEY` | `.anthropic.key` | `https://api.anthropic.com/v1` |
| Self-hosted (Ollama) | `LOCAL_MODEL_KEY` | — | `http://localhost:11434/v1` |

Keys read from environment first; if absent, from `<project-root>/.<provider>.key` (one line, no
trailing whitespace). If neither exists, that tier is skipped — tests pass trivially so CI stays
green.

### Running cloud E2E tests

```bash
# Single-model pipeline: DeepSeek only (4 games: snake, pong, tetris, breakout)
make test-e2e-multi-model

# Full model matrix: all available cloud tiers (DeepSeek, OpenRouter, Anthropic, self-hosted)
make test-e2e  TESTFILE=tests/e2e/test_model_matrix_pipeline.py

# Filter to a single game
CI_GAME=snake make test-e2e-multi-model
```

Cloud test tiers (from `_cloud_tiers_for_matrix()` in `test_model_matrix_pipeline.py`):

| Tier key | Display | Model config |
|----------|---------|-------------|
| `deepseek` | DeepSeek (PaaS) | `deepseek-chat` via OpenAI-compat API at api.deepseek.com |
| `openrouter` | OpenRouter (multi-model) | Planner: `deepseek/deepseek-chat`, Coder: `qwen/qwen2.5-coder-7b-instruct`, Reviewer: `meta-llama/llama-3.3-70b-instruct` |
| `anthropic` | Anthropic Direct | Native Anthropic SDK path (when `langchain_anthropic` installed) |
| `local` | Self-hosted (Ollama) | `LOCAL_MODEL_NAME` env var (default `qwen2.5:0.5b`), probed at `LOCAL_MODEL_BASE_URL` |

### OpenRouter multi-model pipeline

OpenRouter dispatches a **three-phase planner→coder→reviewer** pipeline per game
(see `_run_pipeline()` in `test_ci_multi_model_pipeline.py`):

1. **Planner:** receives system prompt + game description → outputs structured design spec
2. **Coder:** receives design spec → outputs self-contained Python game code
3. **Reviewer:** receives design spec + code → structured review (issues, fixes, score, pass/fail)

Output: `/tmp/gludd-multi-model-results.json` (per-game metrics: AST validity, runnability,
lines of code, per-phase latency, token counts, feature/lifecycle failures).

## 2. Local GGUF Models (llama.cpp)

### Prerequisites

```bash
# Install llama.cpp Python bindings + server support
make sync-llama-cpp SYNC_LLAMA_CPP_VALIDATE_ONLY=0

# Download the pinned, already-quantized Qwen2.5 0.5B test artifact (~398 MB)
make e2e-download-small-model

# Prove that the artifact loads and produces tokens in-process
make test-local-model-inference

# Build llama-quantize CLI (optional, for quantizing models)
make build-llamacpp-tools
```

`e2e-download-small-model` does not require `llama-quantize`: its source GGUF is
already Q4_K_M. It pins the Hugging Face revision and atomically hard-links the
download cache into `/tmp/gludd-qwen-e2e-model` (falling back to a copy only when
the cache and temporary directory are on different filesystems). Repeated setup is
therefore deterministic and avoids an unnecessary second 398 MB allocation.

Dependency check (`_deps_reason()` in `test_model_matrix_pipeline.py`):
- `llama-cpp-python` must be importable
- `huggingface_hub` must be importable (GGUF download from HuggingFace)
- >= 1 GB free RAM
- >= 2 GB free disk

### Model registry

24 GGUF models in `tests/e2e/_local_model_configs.py` — the `E2EModelEntry` table:

| Model | Category | Size | CI-safe? | Context |
|-------|----------|------|----------|---------|
| SmolLM2-135M | general | 88 MB | yes | 8192 |
| SmolLM2-360M | general | 224 MB | yes | 8192 |
| Qwen2.5-Coder-0.5B | coding | 312 MB | yes | 32768 |
| Qwen2.5-0.5B | general | 316 MB | yes | 32768 |
| Phi-2 | general | 487 MB | yes | 2048 |
| TinyLlama-1.1B | general | 496 MB | yes | 2048 |
| DeepSeek-Coder-1.3B | coding | 792 MB | no | 16384 |
| Qwen2.5-Coder-1.5B | coding | 936 MB | no | 32768 |
| Qwen2.5-1.5B | general | 940 MB | no | 32768 |
| SmolLM2-1.7B | coding | 1064 MB | no | 8192 |
| Gemma-2-2B | general | 1380 MB | no | 8192 |
| OLMoE-1B-7B | general | 1386 MB | no | 32768 |
| StarCoder2-3B | coding | 1808 MB | no | 16384 |
| StableLM-3B | general | 1856 MB | no | 32768 |
| Qwen2.5-Coder-3B | coding | 1892 MB | no | 32768 |
| Qwen2.5-3B | general | 1896 MB | no | 32768 |
| Llama-3.2-3B | general | 1964 MB | no | 131072 |
| Phi-3-mini-4k | coding | 2172 MB | no | 4096 |
| Phi-3.5-mini | general | 2176 MB | no | 131072 |
| CodeLlama-7B | coding | 4084 MB | no | 16384 |
| Mistral-7B | general | 4368 MB | no | 32768 |
| Qwen2.5-7B | general | 4372 MB | no | 131072 |
| InternLM3-8B | general | 4892 MB | no | 131072 |
| Llama-3.2-1B | general | 712 MB | no | 131072 |

**CI-safe** models (6 total, <500 MB) are the only ones that run in CI. All others require
a development workstation.

### Running local model tests

```bash
# Single CI-safe model (fastest path for dev)
E2E_LOCAL_MODEL=Qwen2.5-Coder-0.5B make test-e2e TESTFILE=tests/e2e/test_model_matrix_pipeline.py

# All CI-safe models (6 models, runs in CI)
CI_SAFE_ONLY=1 make test-e2e TESTFILE=tests/e2e/test_model_matrix_pipeline.py

# All 24 local models (development workstation only)
make test-e2e TESTFILE=tests/e2e/test_model_matrix_pipeline.py

# Single model by alias
E2E_LOCAL_MODEL=qwen-coder-0.5b make test-e2e TESTFILE=tests/e2e/test_model_matrix_pipeline.py

# Filter with LOCAL_MODEL_FILTER (AND logic)
LOCAL_MODEL_FILTER=coding,ci-safe make test-e2e TESTFILE=tests/e2e/test_model_matrix_pipeline.py

# Ollama-based local model (self-hosted endpoint)
make test-e2e-games-local-model

# Fail fast — stop on first model failure
MATRIX_FAIL_FAST=1 make test-e2e TESTFILE=tests/e2e/test_model_matrix_pipeline.py
```

### Beta4 endpoint and game E2E layers

The default endpoint lifecycle suite is hermetic and has no external model or API
cost. It binds an OpenAI-compatible server to `127.0.0.1` on an OS-assigned port,
tests chat and text completions through the production `ChatSession`, covers auth,
malformed requests, invalid response contracts, HTTP 503 propagation, early server
exit, and then proves idempotent teardown. It never assumes a fixed port and its
named server thread must be stopped before the fixture returns.

The heavyweight transformer pipeline is explicitly opt-in to prevent ordinary
collection from downloading a model:

```bash
GLUDD_LIVE_MODEL_E2E=1 make test-files \
  TESTFILES=tests/e2e/test_small_model_pipeline_real.py \
  PYTEST_ARGS='-q -W error'
```

For the real OpenAI-compatible game path, start a local endpoint separately and
pass its loopback URL explicitly:

```bash
make test-e2e-games-local-model \
  LOCAL_MODEL_BASE_URL=http://127.0.0.1:9999/v1 \
  LOCAL_MODEL_NAME=qwen2.5:0.5b \
  LOCAL_MODEL_KEY=local-only \
  LOCAL_MODEL_GAME=snake \
  PYTEST_ARGS='-q -W error'
```

Only loopback endpoints represented by the existing `local-` model-profile
allowlist are accepted; the suite does not disable the production SSRF guard. A
generation receives at most the task policy's configured attempt ceiling. Syntax
repair feeds back a bounded excerpt of the prior response, keeping context growth
predictable on small models.

#### Why beta4 keeps these safeguards

Reviewed 2026-08-20. These upstream practitioner reports document recurring failure modes;
they do not imply that Gludd inherits every historical upstream bug:

- **Ephemeral ports and verified teardown.** An open llama-cpp-python report from
  2024-04-18 describes a configured port being ignored and a subsequent collision with an
  existing service ([issue #1359](https://github.com/abetlen/llama-cpp-python/issues/1359)).
  The hermetic suite therefore asks the OS for a free port and proves that its named server
  thread stops, so parallel projects do not share a fixed endpoint or leave one behind.
- **Immutable revisions and a reusable offline cache.** Hugging Face users have tracked
  expensive unchanged-file downloads since 2023
  ([issue #1738](https://github.com/huggingface/huggingface_hub/issues/1738)), while a
  2025-04-15 report showed that an interrupted snapshot resume could fall back to unsafe
  cached state ([issue #3007](https://github.com/huggingface/huggingface_hub/issues/3007)).
  Beta4 pins the model revision, materializes it atomically from the shared cache, and then
  exercises the local artifact instead of treating a mutable remote branch as test evidence.
- **Deterministic assertions and explicit bounds.** A 2023-07-04 llama.cpp discussion shows
  that the same seed can still diverge across operating-system builds
  ([discussion #2100](https://github.com/ggml-org/llama.cpp/discussions/2100)); an open
  2023-12-07 llama-cpp-python report shows an unset `max_tokens` turning context overflow into
  HTTP 500 ([issue #983](https://github.com/abetlen/llama-cpp-python/issues/983)). The suite
  checks stable API and code properties rather than byte-identical prose, and every repair or
  generation path has an explicit attempt, context, or output ceiling.
- **Loopback-only serving and hostile-input coverage.** A llama.cpp report opened
  2024-05-07 demonstrated that malformed chat JSON could crash the server
  ([issue #7133](https://github.com/ggml-org/llama.cpp/issues/7133)), and practitioners advised
  against public exposure in a 2024-08-18 deployment thread
  ([discussion #9079](https://github.com/ggml-org/llama.cpp/discussions/9079)). The beta4 tests
  bind only to loopback and retain production SSRF checks while covering authentication,
  malformed input, invalid contracts, and upstream service failures.

### What happens during a local model test

For each model in `TestLocalModelMatrixDownloadServe`, the test:

1. **Download** — `ModelDownloader` fetches the GGUF from HuggingFace into `huggingface_hub` cache
   (respects `HF_HOME` / `HF_HUB_CACHE` env vars). Skips download if already cached.
2. **Serve** — `LocalInferenceManager` spawns a llama.cpp server on a random port via
   `LocalServer`. The server exposes an OpenAI-compatible `/v1/chat/completions` endpoint.
   Waits up to 60s for `/health` 200 + warm-up inference.
3. **Generate** — calls the model through `ModelGateway` with a snake-game coding prompt.
   Records latency, token counts, error category.
4. **Verify** — `_verify_snake_code()`: AST parse → class detection → method checks
   (`__init__`, `tick`, `score`, start, is_game_over, restart) → module import →
   instantiation → runtime checks (score type, game_over type, tick loop, restart resets score).
5. **Report** — appends a `MatrixRow` to `/tmp/gludd-model-matrix-report.json`.
6. **Teardown** — kills the server, removes the temp directory.

Error categories: `timeout`, `download_failure`, `serve_failure`, `generation_failure`,
`verification_failure`, `oom`, `none`.

## 3. The Model Matrix Report

### Report artifact

```bash
# View report after a run
cat /tmp/gludd-model-matrix-report.json | python -m json.tool
```

Each row is a `MatrixRow` serialized via `to_dict()`:

```json
{
  "model": "Qwen2.5-Coder-0.5B",
  "tier": "local",
  "category": "coding",
  "role": "coder",
  "passed": true,
  "error_category": null,
  "error_detail": null,
  "latency_ms": 4523,
  "tokens_in": 128,
  "tokens_out": 512,
  "code_generated": true,
  "ast_valid": true,
  "method_checks": {"init": true, "tick": true, "score": true, "start": true, "is_game_over": true, "restart": true},
  "runnable": true,
  "code_quality_score": 1.0
}
```

### Interpreting the summary

`TestModelMatrixReportSummary.test_report_summary()` prints:

- **Pass rate** — `passed` = all of: `code_generated` AND `ast_valid` AND `runnable` AND
  `method_checks` all true. A model that generates syntactically valid code that ALSO passes
  runtime checks.
- **AST valid** — code parsed as valid Python. Does NOT guarantee runnability.
- **Runnable** — module imports and instantiates without error. A model may generate valid
  AST but fail at import (missing imports, undefined symbols).
- **Code generated** — the model returned non-empty content that `_extract_python_module()`
  could extract (strips markdown fences, finds the Python code block).
- **By tier** — local models vs cloud models. Cloud models have higher pass rates but higher
  latency. Local models are free but vary widely in quality.
- **By category** — coding models (trained specifically for code) vs general models
  (repurposed for code). Coding models pass at higher rates.
- **By error category** — the failure distribution: `oom` (model too large for RAM),
  `download_failure` (network/HF outage), `serve_failure` (llama.cpp crash), etc.
- **Per-model detail** — latency breakdown, token throughput, code quality score.

A healthy matrix has:
- Coding models passing at >50% (Qwen2.5-Coder series, DeepSeek-Coder)
- General models generating valid code at >30% (surprisingly capable when prompted well)
- CI-safe models completing within 30s per test
- Zero `oom` errors (filters or env knobs should exclude too-large models)

### Quality scoring

`code_quality_score` is 0.0–1.0:
- 1.0 = all 6 method checks pass (`__init__`, `tick`, `score`, start, `is_game_over`, restart)
  AND module loaded AND runtime checks clean
- 0.8 = AST valid, all methods present, but one runtime check fails
- 0.5 = AST valid, some methods missing
- 0.2 = code generated but AST invalid
- 0.0 = no code generated

## 4. Adding a New Model to the Registry

1. Add an `E2EModelEntry` to `_MODELS` in `tests/e2e/_local_model_configs.py`:
   ```python
   E2EModelEntry(
       name="NewModel-3B",
       repo="org/NewModel-3B-Instruct-GGUF",
       filename="newmodel-3b-instruct-Q4_K_M.gguf",
       size_mb=1800,
       category="coding",
       context_size=32768,
       ci_safe=False,
       aliases=("newmodel",),
   ),
   ```
2. Choose `ci_safe=True` only if size_mb <= 500.
3. Provide at least one short alias.
4. If the model should appear in production paths, add a `LocalModelConfig` to
   `src/general_ludd/local_model/_local_model_configs.py`.
5. Run structural tests:
   ```bash
   make test-e2e TESTFILE=tests/e2e/test_model_matrix_pipeline.py -k "TestModelMatrixStructural"
   ```
   This verifies: total count, CI-safe size constraint, role map integrity, required fields,
   serialization round-trip.
6. Run a single-model smoke test:
   ```bash
   E2E_LOCAL_MODEL=NewModel-3B make test-e2e TESTFILE=tests/e2e/test_model_matrix_pipeline.py -k "TestLocalModelMatrixDownloadServe"
   ```

## 5. Pre-Flight Checklist for E2E Validation

Run before claiming a new model tier or pipeline change is working:

### Hardware & dependencies

- [ ] `make sync-llama-cpp` — llama-cpp-python importable
- [ ] `make build-llamacpp-tools` — llama-quantize built (optional)
- [ ] `python -c "import huggingface_hub"` — GGUF download support
- [ ] Free RAM >= 1 GB (for CI-safe models; 8+ GB for full matrix)

### Cloud API keys

- [ ] `.deepseek.key` present (or `DEEPSEEK_API_KEY` env var) — DeepSeek tier
- [ ] `.openrouter.key` present (or `OPENROUTER_API_KEY` env var) — OpenRouter tier
- [ ] `.anthropic.key` present (or `ANTHROPIC_API_KEY` env var) — Anthropic tier

### Structural gate

```bash
make test-e2e TESTFILE=tests/e2e/test_model_matrix_pipeline.py -k "TestModelMatrixStructural"
```

Expected: 11 tests pass (model counts, CI-safe constraints, role map, serialization, filters,
hardware probes).

### Single-model smoke (fastest)

```bash
E2E_LOCAL_MODEL=Qwen2.5-Coder-0.5B make test-e2e TESTFILE=tests/e2e/test_model_matrix_pipeline.py -k "TestLocalModelMatrixDownloadServe"
```

Expected: 1 test passes. Model downloads (~312 MB), serves, generates snake code, passes
all 6 method checks + runtime verification. ~30–60s.

### CI-safe matrix (6 models)

```bash
CI_SAFE_ONLY=1 make test-e2e TESTFILE=tests/e2e/test_model_matrix_pipeline.py -k "TestLocalModelMatrixDownloadServe"
```

Expected: 6 tests pass (SmolLM2-135M, SmolLM2-360M, Qwen2.5-Coder-0.5B, Qwen2.5-0.5B, Phi-2,
TinyLlama-1.1B). ~3–5 min depending on network.

### Cloud multi-model pipeline

```bash
make test-e2e-multi-model
```

Expected: `TestCIMultiModelStructural` passes (6 structural tests). If keys present:
`TestCIModelPipeline.test_comparison_report` runs the full pipeline and writes
`/tmp/gludd-multi-model-results.json`.

### Report summary

```bash
make test-e2e TESTFILE=tests/e2e/test_model_matrix_pipeline.py -k "TestModelMatrixReportSummary"
```

Expected: prints the aggregated matrix report if prior runs populated
`/tmp/gludd-model-matrix-report.json`.

## 6. Make Targets Reference

| Target | Purpose |
|--------|---------|
| `make sync-llama-cpp` | Install llama-cpp-python[server] |
| `make e2e-download-small-model` | Materialize the pinned Qwen2.5 0.5B GGUF artifact |
| `make build-llamacpp-tools` | Build llama-quantize from source |
| `make test-e2e-multi-model` | Cloud multi-model pipeline (DeepSeek + OpenRouter) |
| `make test-e2e-games-local-model` | Local model game E2E via SmallModelTaskPolicy |
| `make test-e2e TESTFILE=tests/e2e/test_model_matrix_pipeline.py` | Full model matrix (env-controlled) |
| `make local-model-ollama` | Start Ollama server, pull OLLAMA_MODEL |
| `make local-model-stop` | Stop Ollama server |
| `make local-model-status` | Check if Ollama is running |
| `make verify-local-model-quality` | Quality benchmark script |
| `make benchmark-local-model` | Full local model benchmark |
| `make test-local-model-inference` | Direct llama.cpp inference smoke test |

### Environment variable reference

| Variable | Effect | Default |
|----------|--------|---------|
| `E2E_LOCAL_MODEL` | Run only the named model (or alias) | all 24 |
| `CI_SAFE_ONLY` | Limit to CI-safe models (<500 MB) | false |
| `LOCAL_MODEL_FILTER` | Comma-separated filter tokens (coding, general, ci-safe, <model-name>) | none |
| `CI_GAME` | Run only the named game (snake, pong, tetris, breakout) | all 4 |
| `MATRIX_FAIL_FAST` | Stop on first model failure | false |
| `DEEPSEEK_API_KEY` | DeepSeek API key (env or `.deepseek.key`) | — |
| `OPENROUTER_API_KEY` | OpenRouter API key (env or `.openrouter.key`) | — |
| `ANTHROPIC_API_KEY` | Anthropic API key (env or `.anthropic.key`) | — |
| `LOCAL_MODEL_BASE_URL` | Self-hosted OpenAI-compat endpoint | `http://localhost:11434/v1` |
| `LOCAL_MODEL_NAME` | Ollama model name | `qwen2.5:0.5b` |
| `LOCAL_MODEL_KEY` | Optional auth key for self-hosted endpoint | empty |
| `HF_HOME` / `HF_HUB_CACHE` | HuggingFace cache directory | HF defaults |
