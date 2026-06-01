# General Ludd Agent

The black swan agentic coding system — autonomous, Ansible-driven, multi-model AI agents.

## Quick Start

### Native uv mode (preferred)

```bash
uv sync --locked
uv run pytest -v
uv run gludd version
```

### Native pip mode

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest -v
```

### Daemon (server + event loop)

```bash
uv run gludd daemon --port 8000 --log-level info
```

### Client commands

```bash
uv run gludd add "Fix the login bug" --queue core
uv run gludd list --status queued
uv run gludd status
uv run gludd log-level debug
uv run gludd health
uv run gludd version
```

## Runtime Modes

- **native_uv**: Preferred native mode using uv
- **native_pip**: Fallback native mode using pip/venv
- **container**: Container mode with explicit data-source mounts

## Project Structure

```
src/general_ludd/       - Main package (internal namespace)
  schemas/               - Pydantic models
  worker/                - FastAPI worker app
  event_loop/            - Event loop
  controllers/           - PID/budget controllers
  rules/                 - Rule engine
  models/                - Model gateway
  quality/               - Quality gates
  db/                    - Database layer
  secrets/               - OpenBao/hvac secrets
  git_automation/        - Git management
  ansible/               - Ansible runner adapter
  prompts/               - Prompt registry
  runtime/               - Runtime profiles
  agents/                - Multitasking agent system
  review/                - Return review + evidence checker
  dependency/            - Dependency update pipeline
tests/                   - Test suite
playbooks/               - Ansible playbooks
molecule/                - Molecule scenarios
templates/prompts/       - Prompt templates
config/                  - Configuration examples
```
