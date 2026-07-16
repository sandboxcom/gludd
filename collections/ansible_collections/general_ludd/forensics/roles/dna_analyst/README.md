# `general_ludd.forensics.dna_analyst` — DNA Profile Analyzer

Compare and match DNA profiles using STR, CODIS, mtDNA, or Y-chromosome analysis.
Produces a verdict JSON artifact with match result, probability, and loci count.

## Quick start

```yaml
- name: Run DNA analysis
  hosts: localhost
  vars:
    dna_sample_profile:
      loci:
        D3S1358: [15, 17]
        vWA: [14, 16]
        FGA: [21, 23]
    dna_reference_profile:
      loci:
        D3S1358: [15, 17]
        vWA: [14, 16]
        FGA: [21, 23]
    dna_analysis_type: "str"
  roles:
    - general_ludd.forensics.dna_analyst
```

## Variables

| Variable | Default | Description |
|---|---|---|
| `dna_enabled` | `true` | Enable DNA analysis (safety gate) |
| `dna_analysis_type` | `str` | Analysis type: `str`, `codis`, `mtdna`, `ychromosome` |
| `dna_sample_profile` | `{}` | Dict of sample DNA profile with loci alleles |
| `dna_reference_profile` | `{}` | Dict of reference DNA profile with loci alleles |
| `dna_output_dir` | `{{ playbook_dir }}/output/forensics/dna` | Output directory for verdict JSON |

## Output

The role writes `dna_verdict.json` to `dna_output_dir` containing:

```json
{
  "analysis_type": "str",
  "match_result": "match",
  "probability": 0.9998,
  "loci_count": 13,
  "sample_id": "unknown",
  "reference_id": "unknown"
}
```
