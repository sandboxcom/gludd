# `general_ludd.business.entity_research` — Business Entity Intelligence

Comprehensive Ansible role for researching organizations, companies, and entities — their associations, assets, risks, and demographics.

## Quick start

```yaml
- name: Research an entity
  hosts: localhost
  vars:
    entity_name: "Acme Corp"
    entity_research_discover: true
    entity_research_associations: true
    entity_research_assets: true
    entity_research_risks: true
    entity_research_generate_output: true
  roles:
    - general_ludd.business.entity_research
```

## Categories

Each category is enabled by variables and tagged for selective execution:

| Category | Enable var | Tag |
|---|---|---|
| Entity discovery | `entity_research_discover: true` | `discover` |
| Association mapping | `entity_research_associations: true` | `associations` |
| Asset discovery | `entity_research_assets: true` | `assets` |
| Public exposure | `entity_research_exposure: true` | `exposure` |
| Risk analysis | `entity_research_risks: true` | `risks` |
| Demographics | `entity_research_demographics: true` | `demographics` |
| SearX monitoring | `entity_research_searx_monitor: true` | `searx_monitor` |
| Output generation | `entity_research_generate_output: true` | `output` |

## Discovery

```yaml
- name: Discover entity details
  hosts: localhost
  vars:
    entity_name: "Stripe"
    entity_research_discover: true
    entity_research_use_opencorporates: true
    entity_research_use_wikipedia: true
    entity_research_use_searx: true
  roles:
    - general_ludd.business.entity_research
```

## Association mapping

```yaml
- name: Map entity relationships
  hosts: localhost
  vars:
    entity_name: "Google"
    entity_research_associations: true
    entity_research_association_max_depth: 3
  roles:
    - general_ludd.business.entity_research
```

## Risk analysis with monitoring

```yaml
- name: Full risk analysis with continuous monitoring
  hosts: localhost
  vars:
    entity_name: "Target Corp"
    entity_research_discover: true
    entity_research_risks: true
    entity_research_searx_monitor: true
    entity_research_searx_check_interval_hours: 12
    entity_research_generate_output: true
  roles:
    - general_ludd.business.entity_research
```

## Data sources

The role queries 15+ data sources via API and SearX meta-search:

| Source | Category | Purpose |
|---|---|---|
| OpenCorporates | Legal | Company registry |
| SEC EDGAR | Financial | US public company filings |
| Companies House | Legal | UK company registry |
| Crunchbase | Business | Funding, investments |
| Wikipedia | General | Organizational background |
| crt.sh | Digital | SSL certificate discovery |
| PeeringDB | Network | ASN/network info |
| RIPEStat | Network | Routing data |
| USPTO | IP | Patents and trademarks |
| Espacenet | IP | European patents |
| WIPO | IP | Global trademarks |
| Shodan | Security | Internet-connected devices |
| Censys | Security | Internet asset discovery |
| HaveIBeenPwned | Security | Data breach history |
| SearX | Search | Multi-engine meta-search |

See `vars/data_sources.yml` for endpoint details.

## Risk categories

Five weighted risk categories scored from public signals:

| Category | Weight | Dimensions |
|---|---|---|
| Financial | 25% | Debt, profitability, cash flow, runway |
| Security | 20% | Breaches, security program, email config, TLS |
| Personnel | 15% | Leadership turnover, layoffs, satisfaction |
| Market | 20% | Market share, competition, regulation, disruption |
| Legal | 20% | Litigation, regulatory actions, IP risk |

See `vars/risk_templates.yml` for scoring details.

## Output artifacts

Set `entity_research_output_formats` to control output:

```yaml
entity_research_output_formats:
  - json       # entity_report.json
  - markdown   # entity_report.md
  - dot        # entity_graph.dot (Graphviz)
  - csv        # entity_risk_matrix.csv
```

Output directory: `entity_research_output_dir` (default: `/tmp/gludd-entity-research`).

Intermediate artifacts per category are written to `entity_research_artifact_dir` (default: `/tmp/gludd-entity-research`).

## Python integration

The `src/general_ludd/business/entity_graph.py` module provides:

```python
from general_ludd.business import EntityGraph, EntityNode, Association, build_graph

graph = EntityGraph()
graph.add_node(EntityNode(name="Acme", entity_type="corporation"))
graph.add_association(Association("Acme", "SubCo", "subsidiary", strength=1.0))

related = graph.find_related("Acme", max_depth=2)
graph.to_dot("entity_graph.dot")
```

## Key defaults

| Variable | Default | Description |
|---|---|---|
| `entity_research_association_max_depth` | `3` | BFS depth for relationship graph |
| `entity_research_api_delay_seconds` | `1` | Rate limit delay between API calls |
| `entity_research_api_max_retries` | `3` | Max retries per API call |
| `entity_research_searx_alert_topics` | 7 topics | Monitored risk signal topics |
| `entity_research_output_dir` | `/tmp/gludd-entity-research` | Final report output directory |
