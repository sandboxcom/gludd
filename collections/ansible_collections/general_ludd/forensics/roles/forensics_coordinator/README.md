# `general_ludd.forensics.forensics_coordinator` -- Forensic Investigation Coordinator

Orchestrate multi-analyst forensic workflows. Dispatches fingerprint analyst,
DNA analyst, trace evidence examiner, and photo forensics analyst roles with
shared case context. Initializes chain of custody and assembles a coordinator
report from all analyst verdicts.

## Quick start

```yaml
- name: Run full forensic analysis
  hosts: localhost
  vars:
    forensics_case_id: "CASE-2026-001"
    forensics_evidence_dir: "/evidence/"
    forensics_analyst_types:
      - fingerprint
      - dna
      - trace_evidence
      - photo_forensics
  roles:
    - general_ludd.forensics.forensics_coordinator
```

## Variables

| Variable | Default | Description |
|---|---|---|
| `forensics_enabled` | `true` | Enable the role |
| `forensics_case_id` | `""` | Unique case identifier |
| `forensics_evidence_dir` | `""` | Directory with evidence files |
| `forensics_analyst_types` | `[fingerprint, dna, trace_evidence, photo_forensics]` | Analysts to dispatch |
| `forensics_output_dir` | `{{ playbook_dir }}/output/forensics` | Output directory |
| `forensics_pipeline_enabled` | `true` | Gate for sub-roles |
