# threat_model

STRIDE threat enumeration role for the `general_ludd.agent` collection.

## Description

Reads a design document (`design_path`) and gathers live attack surface data
from `gludd_facts` (queues, MCP tools, execution traces, model profiles), then
emits a structured STRIDE threat enumeration across Spoofing, Tampering,
Repudiation, Information Disclosure, Denial of Service, and Elevation of
Privilege categories. Optionally calls a model to draft an executive narrative
(gated behind `enable_model_call: false`). **REPORT-ONLY — never mutates the
repo.**

## Variables

| Variable | Default | Description |
|---|---|---|
| `daemon_url` | `http://localhost:8000` | Daemon URL for gludd_facts |
| `psk` | `""` | Pre-shared key (no_log) |
| `artifact_dir` | `/tmp/gludd-threat-model` | Where to write artifacts |
| `system_name` | `general-ludd-agent` | Name of the system under threat modeling |
| `design_path` | `docs/design.md` | Path to the design document |
| `model_profile` | `""` | Model profile for narrative (daemon default) |
| `enable_model_call` | `false` | Call model for threat narrative drafting |
| `enable_git_push` | `false` | Always false — this role is report-only |

## Artifacts

- `<artifact_dir>/threat_model.json` — per-STRIDE threats, asset list, surface summary
- `<artifact_dir>/threat_model.md` — human-readable markdown report

## gludd_* modules used

- `gludd_facts` — live queues, todos, models, traces, messages
- `gludd_model_call` — optional narrative drafting (gated)
