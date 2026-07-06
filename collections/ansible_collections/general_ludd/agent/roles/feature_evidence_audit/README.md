# feature_evidence_audit

Audits `docs/features.yml` for false 100% claims by classifying evidence quality.

## Description

Wraps `scripts/audit_features.py` to verify that every feature marked at 100%
completion has real, reachable evidence references. Classifies evidence into:
- `TEST_PLUS_CODE` — both test and code/file evidence
- `TEST_ONLY` — test evidence only
- `CODE_ONLY` — code/file evidence only
- `NO_EVIDENCE` — 100% claimed but no evidence_refs at all

**Never mutates the repo** — audit-only, writes JSON + MD artifacts.

## Variables

| Variable | Default | Description |
|---|---|---|
| `repo_path` | `.` | Path to the gludd repo root |
| `artifact_dir` | `/tmp/gludd-feature-evidence-audit` | Artifact output directory |

## Artifacts

- `<artifact_dir>/feature_evidence_audit.json` — structured audit result
- `<artifact_dir>/feature_evidence_audit.md` — human-readable audit report

## Example

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.feature_evidence_audit
```
