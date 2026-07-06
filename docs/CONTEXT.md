# CONTEXT.md — General Ludd Agent Domain Glossary

> Updated: 2026-06-09

## Core Concepts

- **Daemon**: FastAPI application (`daemon.py`) that hosts the event loop, REST API, and all subsystems. Started via `gludd daemon` or `make daemon`.
- **EventLoop**: Central orchestrator (`event_loop/loop.py`) that runs a tick cycle: claim todos, dispatch jobs, reconcile returns, evaluate controllers.
- **Worker**: External process (Gunicorn + uvicorn) that receives dispatched jobs and executes them via Ansible runners or model calls.
- **JobSpec**: Dataclass describing a dispatched job — includes playbook, variables, model profile, prompt text, project context.

## Data Flow

- **Todo**: A unit of work with status (`queued`, `dispatched`, `completed`, `failed`, `blocked`). Created via CLI `gludd add` or API `POST /api/todos`.
- **TaskReturn**: Worker's response to a dispatched job — includes output, artifacts, metrics.
- **TaskDecision**: Review outcome (`complete`, `needs_more_work`, `failed`, `blocked`, `manual_hold`, `ignore_duplicate`) produced by ReturnReviewer.
- **AuditEvent**: Trace record for any state transition on a todo/task return.

## Prompt System

- **PromptRegistry**: Jinja2-based template manager. Loads `.j2` files from `templates/prompts/`, renders with context variables (todo, config, artifacts).
- **PromptTemplate**: A Jinja2 file in `templates/prompts/`. All templates include `base_harness_aware.md.j2` for shared persona and `TaskDecision` output schema.
- **WorkType**: String key (`code`, `test`, `review`, `docs`, `infra`, `prompt`, `analysis`, `audit`, `self_improvement`, etc.) mapped to a specific prompt template via `_WORK_TYPE_TEMPLATE_MAP`.
- **PromptProfile**: DB-stored prompt variant used by the benchmark/adaptive routing system. Separate from file-based templates.
- **AdaptiveRouter**: Selects best prompt+model combo based on historical benchmark scores. Falls back to configured defaults.

## Skills System

- **Skill**: A markdown file (`.md`) with YAML frontmatter (`name`, `description`, `tags`, `trigger_patterns`) and a body containing instructions.
- **SkillRegistry**: In-memory registry of loaded skills. Supports per-project skill scoping. Matched against todo titles via `trigger_patterns`.
- **SkillCatalog**: Curated collection of 22 skills (10 original + 12 mattpocock). Searchable by query, tags, category. Skills installed to `{config_dir}/skills/`.
- **RemoteSkillFetcher**: Fetches skills from GitHub repos or raw URLs via HTTP. Used by daemon endpoints `POST /admin/skills/fetch` and `POST /admin/skills/fetch-github`.

## Project Isolation

- **Project**: Scoped workspace with `project_id`. Todos, variables, secrets, skills, and workspaces are all project-scoped.
- **ProjectManager**: Manages active projects with weighted routing for dispatch priority.
- **ProjectSecretsManager**: Wraps base SecretsManager with scoped paths (`projects/{project_id}/{path}`).
- **ProjectWorkspace**: Per-project directory structure (artifacts, logs, config, repo, runner).

## Infrastructure

- **SecretsManager**: Interface to OpenBao/Vault or env vars. Resolves `credential_alias` from model profiles.
- **ModelGateway**: Routes model calls to configured providers (OpenAI, Anthropic, ZAI, local inference). Records metrics.
- **AnsibleRunnerAdapter**: Executes playbooks via `ansible-runner` library. The tool-call boundary for the agent.
- **HotReloader**: Reloads config, templates, playbooks, and skills at runtime without restart.

## Testing Conventions

- **TDD**: Write failing test first, then implement. Enforced by AGENTS.md and `enforce-make.ts` plugin.
- **Three test levels**: Unit (`tests/unit/`), Integration (`tests/integration/`), E2E (`tests/e2e/`).
- **Guardrails**: All agent restrictions enforced at 3 layers: config permissions, runtime hooks (`enforce-make.ts`), agent prompts (`AGENTS.md`).
- **Bash policy**: Only `make <target>` commands allowed in agent sessions. No direct shell commands.

## TUI

- **TUI**: Terminal UI built with Rich (tables, panels, layouts) and raw `getch()` for input.
- **Layout**: Two-panel layout (left/right) with 26 table builders. All tables use `expand=True`, `ratio=`, `min_width=`, and `Panel(padding=0)` to eliminate whitespace gaps.
- **Views**: Switched via single-key toggles (`p`=projects, `m`=models, `t`=todos, `h`=hooks, etc.). Breadcrumb trail tracks view history.

## Key Directories

- `src/general_ludd/` — Main package
- `templates/prompts/` — Jinja2 prompt templates (11 files)
- `config/` — User-facing config (YAML), model profiles, prompt profiles
- `alembic/versions/` — DB migration scripts
- `.opencode/` — Agent configuration (skills, plugins, agents)
- `scripts/` — Bootstrap and utility scripts
