# Multi-Model Code Generation Pipeline

## Overview

The multi-model pipeline produces complete, validated, runnable software
projects by chaining three specialized LLM roles through iterative
improvement cycles. Each role uses a distinct model profile, and the
system can route to any of **24 cloud providers** or **3 locally-served
GGUF models** at each stage.

```text
PLANNER —→ CODER —→ REVIEWER
   │          │          │
   │ (fix loop ──────────┘
   │  up to N rounds)
   ▼
 polished code + design spec
```

## The 12 Project Types

Each type is a frozen `ProjectType` dataclass in
`src/general_ludd/cloud/project_types.py:42`. It bundles a prompt
template, validation rules, file structure, and acceptance criteria.

| # | type_id | Display Name | Entry Point | Tech | Roles |
|---|---------|-------------|-------------|------|-------|
| 1 | `game` | Pygame Game | `game.py` | pygame, python | planner, coder, reviewer |
| 2 | `website` | Single-Page Website | `index.html` | html, css, js | planner, coder |
| 3 | `scraper` | Web Scraper | `scraper.py` | requests, bs4 | planner, coder |
| 4 | `database_schema` | Database Schema | `schema.sql` | SQL | planner, coder |
| 5 | `cli_tool` | CLI Tool | `cli.py` | click, python | coder, reviewer |
| 6 | `api_server` | FastAPI Microservice | `main.py` | fastapi, uvicorn | planner, coder, reviewer |
| 7 | `word_processor` | Word Processor | `processor.py` | python | planner, coder |
| 8 | `kernel_module` | Linux Kernel Module | `module.c` | C | planner, coder, reviewer |
| 9 | `data_pipeline` | ETL Data Pipeline | `pipeline.py` | pandas | planner, coder |
| 10 | `chatbot` | Chat Interface | `chatbot.py` | python | planner, coder |
| 11 | `desktop_app` | Desktop Application | `app.py` | tkinter | planner, coder |
| 12 | `test_suite` | Pytest Test Suite | `test_main.py` | pytest | planner, coder |

### Role assignment

Each type specifies which `TaskRole` values apply:

| Role | Enum value | What it does |
|------|-----------|--------------|
| `PLANNER` | `"planner"` | Design spec from natural-language description |
| `CODER` | `"coder"` | Writes code from the design spec |
| `REVIEWER` | `"reviewer"` | Audits code, scores quality, returns fix list |
| `EDITOR` | `"editor"` | Text transformation (schema extraction, formatting) |
| `COMPACTOR` | `"compactor"` | Context summarization / compression |
| `ENUMERATOR` | `"enumerator"` | Exhaustive listing / cataloging |

### Tech stack defaults

Derived from `_TECH_STACK_DEFAULTS` in `project_types.py:30`. A
`ProjectSpec` inherits its default tech stack from the project type
unless explicitly overridden.

## Models: 24 Providers + 3 Local

### Cloud providers (24)

Each provider has a flagship model registered in
`PROVIDER_FLAGSHIP_MODELS` at
`src/general_ludd/models/provider_presets.py:289`:

| Provider | Flagship Model |
|----------|---------------|
| openrouter | `anthropic/claude-3.5-sonnet` |
| openai | `gpt-4o` |
| anthropic | `claude-3-5-sonnet-20241022` |
| zai | `glm-4.5` |
| groq | `llama-3.3-70b-versatile` |
| deepseek | `deepseek-chat` |
| baseten | `meta-llama/Llama-3.1-70B-Instruct` |
| lambdalabs | `meta-llama/Llama-3.1-70B-Instruct` |
| together | `meta-llama/Llama-3.1-70B-Instruct` |
| fireworks | `meta-llama/Llama-3.1-70B-Instruct` |
| replicate | `meta-llama/Meta-Llama-3.1-70B-Instruct` |
| runpod | `meta-llama/Llama-3.1-70B-Instruct` |
| modal | `meta-llama/Llama-3.1-70B-Instruct` |
| coreweave | `meta-llama/Llama-3.1-70B-Instruct` |
| mistral | `mistral-large-latest` |
| cohere | `command-r-plus` |
| nvidia | `meta-llama/Llama-3.1-70B-Instruct` |
| perplexity | `llama-3.1-sonar-large-128k-online` |
| huggingface | `meta-llama/Llama-3.1-70B-Instruct` |
| ai21 | `jamba-1.5-large` |
| google | `gemini-2.5-pro` |
| cloudflare | `@cf/meta/llama-3.1-70b-instruct` |
| databricks | `databricks-dbrx-instruct` |
| azure-ai-foundry | `Phi-4` |

A provider is auto-configured (via `AutoConfigurator.auto_configure_from_env`
in `src/general_ludd/models/auto_configurator.py:37`) whenever its
credential env var (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.) is set.

### Local GGUF models (3)

Defined in `src/general_ludd/local_model/_local_model_configs.py:15`,
served via vLLM or llama.cpp:

| Name | HuggingFace Repo | Quant |
|------|-----------------|-------|
| `qwen-0.5b` | `bartowski/Qwen2.5-0.5B-Instruct-GGUF` | Q4_K_M |
| `tinyllama-1.1b` | `bartowski/TinyLlama-1.1B-Chat-v1.0-GGUF` | Q4_K_M |
| `smollm2-135m` | `bartowski/SmolLM2-135M-Instruct-GGUF` | Q4_K_M |

## Pipeline Architecture

### Stage 1: Planner

**File:** `multi_model_game_pipeline.py:174`

The planner receives a high-level description ("make a space shooter
game") and returns a structured `DesignSpec` with name, genre,
architecture plan, component list, tech stack, and acceptance
criteria. The model responds in `field:value` key-value format parsed
by `_PLANNER_RESPONSE_RE` (`:119`).

System prompt: `_PLANNER_SYSTEM_PROMPT` (`:76`)

### Stage 2: Coder

**File:** `multi_model_game_pipeline.py:195`

The coder consumes the `DesignSpec.to_prompt()` output and writes
complete, self-contained code. On the first pass it uses
`_CODER_SYSTEM_PROMPT` (`:89`). On subsequent review-fix passes it
uses `_CODER_FIX_SYSTEM_PROMPT` (`:114`) and receives the previous
code + feedback in the conversation history.

### Stage 3: Reviewer

**File:** `multi_model_game_pipeline.py:224`

The reviewer audits the code against the design spec. It returns a
`ReviewResult` with issues found, fixes recommended, a 0.0–1.0 quality
score, and a boolean `passed` flag. The reviewer uses
`_REVIEWER_SYSTEM_PROMPT` (`:102`) and its output is parsed by
`_REVIEWER_RESPONSE_RE` (`:120`).

### Review loop

**File:** `multi_model_game_pipeline.py:146`, method `generate()`

```text
spec = plan(description)
code = code(spec)
for round in 1..max_review_rounds:
    result = review(code, spec)
    if result.passed: return result.code
    code = code(spec, previous_code=code, feedback=result.to_feedback_prompt())
raise RuntimeError if all rounds exhausted without passing
```

Default: 3 review rounds (overridable per invocation).

### Model resolution

Each pipeline stage receives a `model_id` parameter. When `"default"`,
the `ModelGateway` selects the best available profile for the task
kind. The task-kind→model mapping is in
`src/general_ludd/models/gateway.py:1171`:

| Task Kind | Model Preference Chain |
|-----------|----------------------|
| `"code"` | deepseek-coder → glm-4 → deepseek-v3 → qwen2.5-coder-7b |
| `"ansible"` | qwen2.5-coder → qwen2.5-coder-7b → qwen2.5 |
| `"general"` | default → deepseek-v3 → qwen2.5 |
| `"game"` | claude → qwen2.5 → qwen2.5-coder → qwen2.5-coder-7b |

For any other task kind, the fallback is `_DEFAULT_MODEL_PREFERENCE`:
deepseek-v3 → qwen2.5 → qwen2.5-coder-7b.

Cost-aware routing (`route_for_task_with_cost`, `:1207`) delegates to
`CostAwareRouter.route_by_cost` when configured, selecting the cheapest
model that meets the capability threshold.

## API Endpoints

### POST /api/game/generate-multi

**File:** `src/general_ludd/routers/game.py:39`

Game-specific endpoint. Requires at least one of `planner_model`,
`coder_model`, `reviewer_model` to be explicitly set (not `"default"`).

```json
{
  "description": "a side-scrolling platformer",
  "planner_model": "claude-3.5-sonnet",
  "coder_model": "default",
  "reviewer_model": "default",
  "max_review_rounds": 3
}
```

### POST /api/generate/create

**File:** `src/general_ludd/routers/generate.py:65`

Generic endpoint accepting any registered project type.

```json
{
  "project_type": "api_server",
  "description": "REST API for a task manager with CRUD operations",
  "planner_model": "default",
  "coder_model": "deepseek-v3",
  "reviewer_model": "default"
}
```

### POST /api/generate/list-types

Returns all 12 registered project types with metadata.

### POST /api/generate/validate

Validates a generated project directory against its type's expected
output structure.

## Adding a New Project Type

### 1. Register at runtime

Call `register_project_type()` with a `ProjectType` instance:

```python
from general_ludd.cloud.project_types import register_project_type, ProjectType

register_project_type(ProjectType(
    type_id="markdown_site",
    display_name="Markdown Static Site",
    default_entry_point="index.html",
    description="Static site from markdown with a simple build script.",
    output_structure={
        "build.py": "Markdown-to-HTML build script.",
        "content/index.md": "Sample content page.",
    },
    required_imports=["markdown"],
    validation_rules=["ast_valid", "importable"],
    prompt_template_planner=_PLANNER_PREAMBLE + "...",
    prompt_template_coder=_CODER_PREAMBLE + "...",
    acceptance_criteria=["python build.py runs", "output/index.html exists"],
    suggested_model_roles={"planner": "reasoning", "coder": "coding"},
    token_budget_estimate=2500,
))
```

### 2. Add to _BASE_DEFINITIONS (for persistence)

Add the type definition to `_BASE_DEFINITIONS` in
`src/general_ludd/cloud/project_types.py:128`. This makes it available
on every daemon restart without re-registration.

### 3. Update VALID_PROJECT_TYPES (for legacy validation)

If the type should pass `validate_project_type()`, add it to
`VALID_PROJECT_TYPES` at `project_types.py:829`.

### 4. Add model profile (optional)

If the type needs specific model preferences, add an entry to
`_MODEL_PROFILES` at `project_types.py:860`.

## Model Dispatch Routing Logic

### Flow: user request → model selection → pipeline execution

1. **Request arrives** at `POST /api/generate/create` or
   `POST /api/game/generate-multi`.

2. **Project type is validated** against `PROJECT_TYPE_REGISTRY`.

3. **`SoftwareGenerator.generate_multi()`** creates a
   `MultiModelGamePipeline` with the `ModelGateway`.

4. **Pipeline stages call `gateway.call_model(model_id, ...)`**
   with per-stage model IDs. Each `model_id` is either:
   - `"default"` → the gateway resolves it via `route_for_task()`
   - An explicit profile ID → used directly

5. **`ModelGateway.route_for_task(task_kind)`** at `gateway.py:1180`:
   - Looks up `_TASK_MODEL_PREFERENCES[kind]`
   - Iterates the preference list, calling `_best_profile_for(name)`
   - `_best_profile_for` returns the first enabled profile whose
     `model_name` or `profile_id` matches the preference entry
   - Falls back to any enabled profile
   - Raises `ValueError` if no profile is enabled

6. **Cost-aware path** (`route_for_task_with_cost`): when a
   `CostAwareRouter` is wired, it scores all enabled profiles by
   price and selects the cheapest that meets the capability threshold
   (configured via evidence from the `CapabilityEvidenceStore`).

7. **SmallModelTaskPolicy gate** (`small_model_policy.py`): when a
   task policy is configured, `SoftwareGenerator._authorize_dispatch()`
   gates the LLM call through `SmallModelTaskPolicy.authorize()`,
   which checks the task's role, impact, and acceptance contract
   against the model's proven capabilities (from local benchmark
   evaluation). Forbidden impacts (`MUTATE_REPOSITORY`,
   `EXECUTE_COMMAND`, `CREDENTIAL_ACCESS`, etc.) are always denied
   for small/local models.

### Model recommendation from task description

`src/general_ludd/small_models/recommender.py:54` maps natural-language
task descriptions to `(task_kind, TaskRole)` pairs via keyword regex:

| Keyword Pattern | task_kind | Role |
|----------------|-----------|------|
| compact/compress/summarize | `context_compaction` | COMPACTOR |
| document/draft/doc/readme | `documentation_draft` | EDITOR |
| enumerate/list/enum | `bounded_enumeration` | ENUMERATOR |
| classify/failure/error/triage | `failure_classification` | REVIEWER |
| format/normalize/cleanse | `format_normalization` | EDITOR |
| schema/extract/parse | `schema_extraction` | EDITOR |

## Key Source Files

| File | Purpose |
|------|---------|
| `src/general_ludd/cloud/multi_model_game_pipeline.py` | PLANNER→CODER→REVIEWER pipeline core |
| `src/general_ludd/cloud/project_types.py` | 12 project type definitions and registry |
| `src/general_ludd/cloud/software_generator.py` | Project-type-agnostic generator |
| `src/general_ludd/models/gateway.py` | Model routing, profiles, call_model |
| `src/general_ludd/models/provider_presets.py` | 24 provider presets + flagship models |
| `src/general_ludd/local_model/_local_model_configs.py` | 3 local GGUF model configs |
| `src/general_ludd/routers/game.py` | `POST /api/game/generate-multi` |
| `src/general_ludd/routers/generate.py` | `POST /api/generate/*` |
| `src/general_ludd/routing_roles/small_model_policy.py` | Capability gating for small/local models |
| `src/general_ludd/small_models/recommender.py` | Task→model recommendation engine |
| `src/general_ludd/schemas/benchmark.py` | TaskRole, TaskType enums |
