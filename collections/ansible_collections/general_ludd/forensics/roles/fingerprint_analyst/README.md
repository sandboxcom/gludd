# `general_ludd.forensics.fingerprint_analyst` -- Fingerprint Analyzer

Classify and match fingerprint patterns from ridge-flow and minutiae data.
Supports latent, patent, and plastic fingerprint types.

## Quick start

```yaml
- name: Classify fingerprint
  hosts: localhost
  vars:
    fingerprint_enabled: true
    fingerprint_type: "latent"
    fingerprint_data:
      ridge_flow_description: "friction ridge flows and recurves toward thumb"
      core_present: true
      delta_count: 1
      ridge_count: 42
      quality_score: 0.8
      minutiae_list:
        - type: "RIDGE_ENDING"
          x: 120
          y: 340
        - type: "BIFURCATION"
          x: 225
          y: 298
  roles:
    - general_ludd.forensics.fingerprint_analyst
```

## Variables

| Variable | Default | Description |
|---|---|---|
| `fingerprint_enabled` | `true` | Enable the role |
| `fingerprint_type` | `"latent"` | Type: latent, patent, plastic |
| `fingerprint_data` | `{}` | Ridge flow, core, delta, minutiae data |
| `fingerprint_output_dir` | `{{ playbook_dir }}/output/forensics/fingerprint` | Output directory |
