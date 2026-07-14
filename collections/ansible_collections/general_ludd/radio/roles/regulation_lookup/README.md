# `general_ludd.radio.regulation_lookup` — Frequency Regulation Lookup

Lookup frequency allocations, license classes, and band plans by country.

## Quick start

```yaml
- name: Lookup US 2m band
  hosts: localhost
  vars:
    regulation_lookup_enabled: true
    regulation_lookup_country: US
    regulation_lookup_freq_hz: 146520000
  roles:
    - general_ludd.radio.regulation_lookup
```

## Variables

| Variable | Default | Description |
|---|---|---|
| `regulation_lookup_enabled` | `false` | Enable lookup |
| `regulation_lookup_country` | `US` | 2-letter ISO country code |
| `regulation_lookup_freq_hz` | `null` | Frequency to look up |
| `regulation_lookup_service` | `null` | Filter by service type |
