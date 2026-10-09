# service_discovery

Query controller-native SearXNG with multiple search terms to discover API
services. Parses results into `DiscoveredService` records and saves the catalog
to YAML without a listener or subprocess.

## FQCN

`general_ludd.infrastructure.service_discovery`

## Usage

```yaml
- hosts: localhost
  roles:
    - general_ludd.infrastructure.service_discovery
```

With custom vars:

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.infrastructure.service_discovery
      vars:
        searx_transport: remote
        searx_url: "https://searx.example.com"
        search_terms:
          - "AI inference API"
          - "vector database API"
        discovery_timeout: 15
        results_path: ".gludd/catalog.yml"
```

## Inputs

| Variable            | Default                      | Description |
|---------------------|------------------------------|-------------|
| `searx_transport`   | `native`                     | Native primary path or explicit `remote` rollback |
| `searx_url`         | `http://localhost:8888`      | Compatibility URL, used only for remote transport |
| `searx_namespace`   | `gludd-service-discovery`    | Native runtime namespace |
| `searx_settings_path` | `""`                       | Optional controller-local settings file |
| `search_terms`      | 7 built-in terms (see below) | List of search queries |
| `discovery_timeout` | `30`                         | HTTP timeout per query (seconds) |
| `results_path`      | `.gludd/discovered_services.yml` | Output YAML path |

## Outputs

- `{{ results_path }}` — YAML file with `services` list, `total_discovered` count, `errors` list

## Default search terms

- AI inference API provider
- GPU cloud provider
- serverless compute API
- cloud computing API service
- model deployment API
- vector database API service
- LLM hosting provider

## Edge cases

- Zero results from all terms: WARN, no failure
- A failed or malformed search fails closed; no partial batch is published
- Duplicate URLs across terms: deduplicated, first occurrence kept
- Missing `url` in result: entry skipped
- Explicit remote endpoint unreachable: the task fails closed
