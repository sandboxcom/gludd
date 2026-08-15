# Profile Configuration Guide

This guide covers how to configure and version model profiles in General Ludd Agent:
routing multiple models by role/pattern/quality, running multiple projects with
per-project settings, adding and editing profiles, and versioning them consistently
with the project's own version scheme.

All examples are derived directly from the live config schema. Referenced files:

| Topic | Primary source |
|-------|---------------|
| Model routing fields | `config/model_routing.yml`, `src/general_ludd/config/model_routing.py` |
| Model profile fields | `src/general_ludd/models/gateway.py` (`ModelProfile`) |
| Config layering | `src/general_ludd/config/user_config.py` (`UserConfig`, `AgentConfig`, `ConfigLayer`) |
| Agent definitions | `config/agents/default_agents.yml` |
| Process isolation | `config/ansible/isolation.yml` |
| Hot-reload | `src/general_ludd/reload/hot_reloader.py` (`HotReloader`) |
| Python version | `pyproject.toml` (`version = "0.1.0-beta.1"`) |

---

## 1. Configuring Multiple Models

Model profiles and model routing are two distinct concepts that work together.

### 1a. Model profiles — `config/model_profiles/*.yml`

Each file in `config/model_profiles/` defines one model connection. The daemon
loads all `*.yml` files in this directory at startup.

**Pydantic schema** (from `ModelProfile` in `src/general_ludd/models/gateway.py`):

```text
model_profile_id   str          Required. Unique ID used everywhere else.
role_names         list[str]    Roles this profile can be used for (informational).
provider           str          Provider type. Default: "openai".
provider_package   str          LangChain package. Default: "langchain-openai".
provider_class_hint str         LangChain class. Default: "ChatOpenAI".
model_name         str          Model identifier sent to the API.
api_base_alias     str | None   Secret alias for the base URL (resolved from vault/env).
credential_alias   str | None   Secret alias for the API key (resolved from vault/env).
context_window     int          Token context window. Default: 128000.
max_input_tokens   int          Default: 120000.
max_output_tokens  int          Default: 8000.
cost_per_input_token  float     USD per token.
cost_per_output_token float     USD per token.
api_metered        bool         Whether API calls are metered. Default: true.
run_budget_usd     float        Per-run spending cap in USD. Default: 200.0.
enabled            bool         Must be true for the gateway to call this profile.
resource_profile   str          Scheduler hint. Default: "ai_heavy".
latency_class      str | None   For latency_routing resolution.
quality_class      str | None   For quality_routing resolution.
fallback_profiles  list[str]    Profile IDs to try when this one fails.
probe_enabled      bool         Enable liveness probes. Default: false.
```

**API keys are never stored in YAML.** `credential_alias` and `api_base_alias`
are aliases resolved at runtime from OpenBao/Vault (`secret/general-ludd/<alias>`)
or from the environment variable whose name equals the alias.

#### Example: three profiles for three purposes

```yaml
# config/model_profiles/strong_coder.yml
model_profile_id: strong_coder
role_names: [coder, planner]
provider: openai
provider_package: langchain-openai
provider_class_hint: ChatOpenAI
model_name: glm-5.1
credential_alias: ZAI_API_KEY        # reads ZAI_API_KEY from vault or env
api_base_alias: ZAI_BASE_URL         # reads ZAI_BASE_URL from vault or env
context_window: 128000
max_input_tokens: 120000
max_output_tokens: 8000
cost_per_input_token: 0.000001       # adjust to your provider's pricing
cost_per_output_token: 0.000002
run_budget_usd: 50.0
enabled: true
fallback_profiles:
  - cheap_coder                      # try this if strong_coder fails
```

```yaml
# config/model_profiles/cheap_coder.yml
model_profile_id: cheap_coder
role_names: [fast, weak]
provider: openai
provider_package: langchain-openai
provider_class_hint: ChatOpenAI
model_name: glm-4-flash
credential_alias: ZAI_API_KEY
api_base_alias: ZAI_BASE_URL
context_window: 32000
max_input_tokens: 30000
max_output_tokens: 4000
cost_per_input_token: 0.0000001
cost_per_output_token: 0.0000002
run_budget_usd: 5.0
enabled: true
```

```yaml
# config/model_profiles/openrouter_reviewer.yml
model_profile_id: openrouter_reviewer
role_names: [reviewer]
provider: openai                      # OpenRouter is OpenAI-compatible
provider_package: langchain-openai
provider_class_hint: ChatOpenAI
model_name: anthropic/claude-3-haiku  # OpenRouter model slug
credential_alias: OPENROUTER_API_KEY
api_base_alias: OPENROUTER_BASE_URL
context_window: 200000
max_input_tokens: 180000
max_output_tokens: 4096
cost_per_input_token: 0.00000025
cost_per_output_token: 0.00000125
run_budget_usd: 10.0
enabled: true
```

Set the aliases in `/etc/general-ludd/env` (or in OpenBao):

```bash
ZAI_API_KEY=your-zai-key
ZAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
```

### 1b. Model routing — `config/model_routing.yml`

Routing maps task types, roles, quality classes, latency classes, and named
patterns to profile IDs. These profile IDs must match `model_profile_id` values
from your `config/model_profiles/*.yml` files.

**Pydantic schema** (from `ModelRoutingConfig` in
`src/general_ludd/config/model_routing.py`):

```text
default_profile      str | None     Fallback when no other routing rule matches.
weak_model_profile   str | None     Profile selected when role == "weak".
role_routing         dict[str, str] Map of role name → profile ID.
quality_routing      dict[str, str] Map of quality class → profile ID.
latency_routing      dict[str, str] Map of latency class → profile ID.
pattern_routing      dict[str, str] Map of pattern name → role name (NOT profile ID).
fallback_chain       list[str]      Ordered profile IDs for gateway-level fallback.
```

Note: `pattern_routing` values are **role names**, not profile IDs. The router
resolves the role name through `role_routing` (or falls back to `default_profile`).

#### Example: routing three profiles

```yaml
# config/model_routing.yml
default_profile: strong_coder         # used when no rule matches
weak_model_profile: cheap_coder       # used for role == "weak"

fallback_chain:
  - strong_coder
  - cheap_coder                       # tried in order if the previous fails

role_routing:
  coder: strong_coder                 # code-generation tasks
  planner: strong_coder               # planning tasks
  reviewer: openrouter_reviewer       # code review
  fast: cheap_coder                   # latency-sensitive, low-stakes

quality_routing:
  high: strong_coder
  medium: cheap_coder

latency_routing:
  fast: cheap_coder

pattern_routing:
  return_review: reviewer             # → looks up "reviewer" in role_routing
  commit_message: weak                # → looks up "weak" → weak_model_profile
  gap_analysis: fast                  # → looks up "fast" in role_routing
  code_generation: coder
  planning: planner
```

**Resolution order** (implemented in `ModelRouter.resolve_role`):

1. If `role_name == "weak"` and `weak_model_profile` is set, return it.
2. Look up `role_name` in `role_routing`.
3. Fall back to `default_profile`.

**Pattern resolution** (implemented in `ModelRouter.resolve_pattern`):

1. Look up `pattern_name` in `pattern_routing` to get a role name.
2. Resolve that role name through the role resolution steps above.

---

## 2. Configuring Multiple Projects

General Ludd Agent supports per-project configuration through the config
**layering** system. Three layers stack in priority order (highest wins):

| Layer | File | Scope |
|-------|------|-------|
| Per-project agent config | `.general-ludd/agent_config.yml` (in the project repo) | Project-level overrides |
| User config | `~/.config/general-ludd/user.yml` | User-wide overrides |
| System config | `/etc/general-ludd/general-ludd.yml` | Shared defaults |

**Pydantic schema for per-project config** (`AgentConfig` in
`src/general_ludd/config/user_config.py`):

```text
model_routing        ModelRoutingConfig | None   Per-project routing override.
active_model_profile str | None                  Pin a specific profile for this project.
preferred_agents     dict[str, Any]              Map of task type → agent preference.
task_preferences     dict[str, Any]              Task-level settings.
session_notes        str                         Free-form notes (used by agents).
```

`ConfigLayer.resolve_model_routing()` (in `src/general_ludd/config/user_config.py`)
checks `user.model_routing` first, then `agent.model_routing`. This means a
`.general-ludd/agent_config.yml` in a project directory overrides the user's
routing config for that project.

### Example: two projects with different model routing

**Project A (cost-sensitive)** — `.general-ludd/agent_config.yml` in project A:

```yaml
# project-a/.general-ludd/agent_config.yml
# Routes everything through the cheap model for this project.
model_routing:
  default_profile: cheap_coder
  weak_model_profile: cheap_coder
  role_routing:
    coder: cheap_coder
    planner: cheap_coder
    reviewer: cheap_coder
    fast: cheap_coder
  pattern_routing:
    code_generation: coder
    planning: planner
    commit_message: weak

active_model_profile: cheap_coder
```

**Project B (quality-sensitive)** — `.general-ludd/agent_config.yml` in project B:

```yaml
# project-b/.general-ludd/agent_config.yml
# Routes coding and review to strong models, fast tasks to cheap.
model_routing:
  default_profile: strong_coder
  weak_model_profile: cheap_coder
  role_routing:
    coder: strong_coder
    planner: strong_coder
    reviewer: openrouter_reviewer
    fast: cheap_coder
  fallback_chain:
    - strong_coder
    - cheap_coder
  pattern_routing:
    code_generation: coder
    return_review: reviewer
    commit_message: weak
    planning: planner
```

The daemon resolves routing when it processes a task for a project by reading
that project's `.general-ludd/agent_config.yml` (if present) through
`load_agent_config()` in `src/general_ludd/config/loader.py`.

### What is NOT yet per-project (partial / intended)

The following settings are defined globally in `general-ludd.yml` and in
`config/ansible/isolation.yml` and do **not** currently have a per-project
override mechanism in `AgentConfig`:

- `process_isolation` (enabled/disabled, container runtime, path allow/deny lists)
- `budget` limits
- `agents.max_concurrent`

If you need different isolation or budget settings per project, the supported
path today is to run separate daemon instances pointed at separate
`--config-dir` directories, each with its own `general-ludd.yml` and
`config/ansible/isolation.yml`.

---

## 3. Adding a New Profile

### Step 1 — Create the profile file

Add a new file to `config/model_profiles/`. Name it after the profile ID for
clarity (e.g., `config/model_profiles/openai_gpt4o.yml`):

```yaml
# config/model_profiles/openai_gpt4o.yml
model_profile_id: openai_gpt4o
role_names: [coder, planner, reviewer]
provider: openai
provider_package: langchain-openai
provider_class_hint: ChatOpenAI
model_name: gpt-4o
credential_alias: OPENAI_API_KEY
context_window: 128000
max_input_tokens: 120000
max_output_tokens: 4096
cost_per_input_token: 0.0000025
cost_per_output_token: 0.00001
run_budget_usd: 25.0
enabled: true
fallback_profiles:
  - cheap_coder
```

### Step 2 — Add the credential alias

In `/etc/general-ludd/env`:

```bash
OPENAI_API_KEY=sk-...
```

Or in OpenBao:

```bash
bao kv put secret/general-ludd/OPENAI_API_KEY value=sk-...
```

### Step 3 — Reference it in model routing

Edit `config/model_routing.yml` to add the new profile ID to the relevant
routing keys. For example, to send high-quality work to the new profile:

```yaml
default_profile: openai_gpt4o
weak_model_profile: cheap_coder

role_routing:
  coder: openai_gpt4o
  planner: openai_gpt4o
  reviewer: openrouter_reviewer
  fast: cheap_coder

quality_routing:
  high: openai_gpt4o
  medium: cheap_coder
```

### Step 4 — Reload

The hot-reloader (`HotReloader` in `src/general_ludd/reload/hot_reloader.py`)
supports live reload of routing config. See section 4 for details.

---

## 4. Editing an Existing Profile

### What to change

Edit the relevant file in `config/model_profiles/` or `config/model_routing.yml`
with the Read/Edit tools. Common edits:

- **Swap model**: change `model_name`.
- **Adjust budget**: change `run_budget_usd`.
- **Enable/disable**: toggle `enabled`.
- **Add a fallback**: append a profile ID to `fallback_profiles`.
- **Change routing**: edit `config/model_routing.yml` — no profile file change needed.

### Hot-reload support

`HotReloader.reload(ReloadScope.MODELS)` (in
`src/general_ludd/reload/hot_reloader.py`) re-reads `config/model_routing.yml`
and pushes the new config into the model gateway at runtime — no daemon restart
needed.

**What is live-reloaded**: routing config (`config/model_routing.yml`), templates,
playbooks, and skills.

**What requires a restart**: changes to individual profile YAML files in
`config/model_profiles/` are not automatically watched by the hot-reloader. The
`_reload_models` method reads `model_routing.yml` and walks the `profiles` key
within that file (if present). Profile YAML files in `config/model_profiles/`
are loaded at startup; to pick up changes to those files without a restart, add
them as inline `profiles` blocks inside `model_routing.yml` (this is an
undocumented but supported path — `_reload_models` looks for a `profiles` dict
key in the parsed routing file and calls `gateway.add_profile()` for each entry).

**To trigger a reload via the CLI** (intended path — confirm current CLI commands
with `gludd --help`):

```bash
gludd reload models
```

Or via the API if you have a running daemon.

### Environment variable overrides

Any `UserConfig` field can be overridden at runtime without touching files by
setting `GLUDD_<FIELD>` environment variables (JSON-encoded for nested values):

```bash
# Force a different default profile for this shell session
GLUDD_MODEL_ROUTING='{"default_profile": "cheap_coder"}' gludd run
```

---

## 5. Versioning Profiles

### Project version scheme

This project uses two co-existing version schemes:

| Artifact | File | Format | Example |
|----------|------|--------|---------|
| Python package | `pyproject.toml` `version` | PEP 440, no leading `v` | `0.1.0-beta.1` |
| Ansible collection | `galaxy.yml` `version:` (if/when added) | Semver | `0.1.0` |

From `pyproject.toml`:

```toml
version = "0.1.0-beta.1"
```

No leading `v`.

### Recommended convention for profile versioning

Add a `version:` field to each profile YAML following PEP 440 with the same
`0.1.0-alpha.<timestamp>` pattern the project already uses. Use the timestamp of
the last meaningful change to that profile.

#### Adding a version to a new profile

```yaml
# config/model_profiles/strong_coder.yml
model_profile_id: strong_coder
version: "0.1.0-alpha.202606140000"   # PEP 440; no leading v
model_name: glm-5.1
# ... rest of fields ...
enabled: true
```

The `version` field is not part of the current `ModelProfile` Pydantic schema
(it has no `version` field) — it will be silently ignored by the loader today.
Adding it is still valuable as human-readable provenance in the YAML file.
When a `version` field is added to `ModelProfile` in the future, existing files
will be picked up automatically.

#### Bumping a profile version

When you make a meaningful change to a profile (new model, adjusted budget,
changed fallback chain), bump the `version` timestamp:

```yaml
# Before
version: "0.1.0-alpha.202606140000"
model_name: glm-5.1
run_budget_usd: 50.0

# After (model swap + budget increase)
version: "0.1.0-alpha.202606200930"
model_name: glm-5.2
run_budget_usd: 75.0
```

#### Versioned filenames as an alternative

If you want to keep old profiles available without overwriting them, use versioned
filenames. The daemon loads all `*.yml` files in `config/model_profiles/`, so
both files are active simultaneously — give each a distinct `model_profile_id`:

```text
config/model_profiles/
  strong_coder_v1.yml    # model_profile_id: strong_coder_v1
  strong_coder_v2.yml    # model_profile_id: strong_coder_v2
```

Then in `config/model_routing.yml`, switch the routing reference:

```yaml
# Promote v2 to default:
default_profile: strong_coder_v2
role_routing:
  coder: strong_coder_v2
  planner: strong_coder_v2
  # keep v1 in fallback chain during transition:
  fallback_chain:
    - strong_coder_v2
    - strong_coder_v1
```

Remove the old profile file once you're confident in the new one.

#### For Ansible collection contributions

If this project ships an Ansible collection (currently no `galaxy.yml` is present
in the repo root), profile-related roles and tasks follow semver in `galaxy.yml`:

```yaml
# galaxy.yml (if added)
namespace: general_ludd
name: agent
version: 0.1.0   # semver, no leading v; bump minor for new profiles, patch for fixes
```

Use `0.x.y` for pre-1.0 work. Align collection bumps with the Python package
alpha release that introduces the profile change.

---

## Quick Reference

### Resolution lookup table

| What you have | How routing finds a profile |
|---------------|----------------------------|
| Agent role (e.g. `coder`) | `role_routing[role]` → profile ID |
| Pattern name (e.g. `commit_message`) | `pattern_routing[pattern]` → role → `role_routing[role]` |
| Quality class (e.g. `high`) | `quality_routing[quality_class]` → profile ID |
| Latency class (e.g. `fast`) | `latency_routing[latency_class]` → profile ID |
| Role == `"weak"` | `weak_model_profile` → profile ID |
| Nothing matches | `default_profile` |
| Primary profile fails | `fallback_chain[0]`, then `fallback_chain[1]`, ... |

### File locations

```text
config/
  model_routing.yml              Top-level routing rules (hot-reloadable)
  model_profiles/
    strong_coder.yml             One file per profile (loaded at startup)
    cheap_coder.yml
    openrouter_reviewer.yml
  agents/
    default_agents.yml           Agent definitions (model_profile: <id>)
  ansible/
    isolation.yml                Process isolation settings (global)

<project-repo>/
  .general-ludd/
    agent_config.yml             Per-project routing + profile override
```
