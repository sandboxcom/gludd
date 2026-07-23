# `general_ludd.forensics.trace_evidence_examiner` — Trace Evidence Analysis

Examine and compare trace evidence samples (fiber, hair, glass, paint, soil,
GSR, toolmark, footwear, tire impressions) against reference data. Produces a
JSON verdict with evidence type, findings, match result, and confidence level.

## Quick start

```yaml
- name: Examine fiber evidence
  hosts: localhost
  vars:
    trace_evidence_enabled: true
    trace_evidence_type: "fiber"
    trace_evidence_sample_data:
      color: "blue"
      diameter_um: 22
      cross_section: "round"
    trace_evidence_reference_data:
      color: "blue"
      diameter_um: 21.5
      cross_section: "round"
  roles:
    - general_ludd.forensics.trace_evidence_examiner
```

## Variables

| Variable | Default | Description |
|---|---|---|
| `trace_evidence_enabled` | `true` | Enable the role (safety gate) |
| `trace_evidence_type` | `"fiber"` | Evidence type: fiber, hair, glass, paint, soil, gsr, toolmark, footwear, tire |
| `trace_evidence_sample_data` | `{}` | Dict of sample measurements |
| `trace_evidence_reference_data` | `{}` | Dict of reference measurements |
| `trace_evidence_output_dir` | `{{ playbook_dir }}/output/forensics/trace` | Output directory for JSON verdict |
