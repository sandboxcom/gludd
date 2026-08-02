# agent_orchestrate

Env-fact-driven orchestration: read advice, branch to LangGraph workflow or single-shot model call.

## FQCN

`general_ludd.agent.agent_orchestrate`

## Example

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.agent_orchestrate
```

## Inputs

See `defaults/main.yml` for the full variable list with defaults.

## Constrained-model boundary

This role can write an artifact and update todo state, so it is not an eligible
constrained-model execution surface. If environment advice recommends the
configured `weak_model_profile`, the role records a policy escalation and uses
the configured `default_profile` before either workflow branch can run.

Smaller/local models may still create an artifact-only upstream draft, but that
dispatch must go through `SmallModelTaskPolicy.authorize()`, use exact offline
capability evidence and one task fingerprint (dedupe), and pass bounded
acceptance in `record_completion()`. Callers must honor `retry` and `escalate`;
this role may consume only an accepted draft and uses the stronger profile for
all side effects. See `docs/design/SMALL_MODEL_TASK_POLICY.md`, including the
linked Ollama, vLLM, and llama.cpp operator reports that motivate fail-closed
role integration.
