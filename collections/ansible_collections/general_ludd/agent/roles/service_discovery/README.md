# service_discovery

Query a SearXNG instance with multiple search terms to discover API services.
Parses results into `DiscoveredService` records and saves the catalog to YAML.

## FQCN

`general_ludd.agent.service_discovery`

## Usage

```yaml
- hosts: localhost
  roles:
    - general_ludd.agent.service_discovery
```

With custom vars:

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.service_discovery
      vars:
        searx_url: "http://searx.example.com:8080"
        search_terms:
          - "AI inference API"
          - "vector database API"
        discovery_timeout: 15
        results_path: ".gludd/catalog.yml"
```

## Inputs

| Variable            | Default                      | Description |
|---------------------|------------------------------|-------------|
| `searx_url`         | `http://localhost:8888`      | SearXNG instance base URL |
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
- Partial failures (some terms error, some succeed): errors recorded, playbook continues
- Duplicate URLs across terms: deduplicated, first occurrence kept
- Missing `url` in result: entry skipped
- SearXNG unreachable: error recorded per term
