# auto_register_service role

Register a newly discovered cloud/AI service: generate a minimal connector
module, extend the provider registry, and verify import health.

## Key variables

| Variable | Default | Notes |
|---|---|---|
| `service` | `{}` | Dict with `name` (required), `url`, `api_docs_url`, `pricing_url`, `description`, `kind` |
| `register_in_registry` | `true` | Append entry to `_BUILTIN_PROVIDERS` |
| `run_healthcheck` | `true` | Run `make healthcheck` after generation |

## Example

```yaml
- name: Register a new AI inference service
  ansible.builtin.include_role:
    name: general_ludd.agent.auto_register_service
  vars:
    service:
      name: "Groq"
      url: "https://api.groq.com"
      api_docs_url: "https://console.groq.com/docs"
      pricing_url: "https://groq.com/pricing"
      description: "Groq LPU inference API"
      kind: "metrics"
```

## Design

- The Jinja2 template `templates/connector.py.j2` generates a stub connector
  satisfying the `Source` protocol (`KIND`, `health()`, `query()`).
- Provider entries are appended to `_BUILTIN_PROVIDERS` via `blockinfile` with
  `AUTO-REGISTERED` markers for idempotency.
- Pricing URLs are appended to a `_pricing_sources.yml` sidecar file in the
  connectors directory.
