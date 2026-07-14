# `general_ludd.business` — Business Intelligence Collection

Ansible collection providing roles and modules for researching organizations,
companies, and entities — their associations, assets, risks, and demographics.

## Roles

| Role | Description |
|---|---|
| `entity_research` | Full entity intelligence: discovery, associations, assets, exposure, risks, demographics |

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
  roles:
    - general_ludd.business.entity_research
```
