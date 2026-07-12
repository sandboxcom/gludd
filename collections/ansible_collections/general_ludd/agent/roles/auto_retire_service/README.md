# auto_retire_service role

Deprecate services that have vanished from upstream discovery. Loads the
project ServiceCatalog, marks the named service as `status: inactive` with a
`retired_at` timestamp, strips it from active pricing sources, and appends
a timestamped retirement event to `.gludd/retired_services.log`. Connector
files are preserved for audit — never deleted.

## Key variables

| Variable | Default | Notes |
|---|---|---|
| `service_name` | *required* | Name of the service to retire |
| `service_catalog_path` | `.gludd/service_catalog.yml` | Path to the project ServiceCatalog YAML |
| `retirement_log_path` | `.gludd/retired_services.log` | Audit log for retirement events |
| `reason` | `vanished from SearX discovery` | Human-readable reason for retirement |

## Example

```yaml
- name: Retire a vanished SearX proxy
  ansible.builtin.include_role:
    name: general_ludd.agent.auto_retire_service
  vars:
    service_name: "searx-ng-proxy"
    reason: "upstream searx instance shut down — unreachable after 72h"
```
