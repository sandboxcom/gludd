# cost_optimizer

Check current model/compute rates via daemon pricing API, defer tasks
to off-peak windows, and report estimated savings.

## FQCN

`general_ludd.agent.cost_optimizer`

## Example

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.cost_optimizer
      savings_threshold_usd: 0.10
      max_deferral_hours: 24
```

## Inputs

See `defaults/main.yml` for the full variable list with defaults.
