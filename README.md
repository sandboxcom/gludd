# Agentic Harness

Autonomous coding system with Ansible runners and multi-model AI agents.

## Quick Start

### Native uv mode (preferred)

```bash
uv sync --locked
uv run pytest -v
uv run agentic-harness version
```

### Native pip mode

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest -v
```

### Worker service

```bash
uv run agentic-harness worker
```

### Event loop

```bash
uv run agentic-harness loop
```

## Runtime Modes

- **native_uv**: Preferred native mode using uv
- **native_pip**: Fallback native mode using pip/venv
- **container**: Container mode with explicit data-source mounts

## Project Structure

```
src/agentic_harness/     - Main package
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
tests/                   - Test suite
playbooks/               - Ansible playbooks
molecule/                - Molecule scenarios
templates/prompts/       - Prompt templates
config/                  - Configuration examples
```
