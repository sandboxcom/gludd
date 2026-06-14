# security_review

Code security review role for the `general_ludd.agent` collection.

## Description

Reviews a code change/diff for insecure patterns using **real grep-based pattern
matching** (never canned output). Detects: `eval(`, `exec(`, `shell=True`,
`pickle.loads`, hardcoded secrets, `os.system(`, `subprocess.call`, and
`yaml.load(` without Loader. Emits a `gludd_message` when findings are present.
**REPORT-ONLY — never auto-patches code.**

## Variables

| Variable | Default | Description |
|---|---|---|
| `daemon_url` | `http://localhost:8000` | Daemon URL |
| `psk` | `""` | Pre-shared key (no_log) |
| `artifact_dir` | `/tmp/gludd-security-review` | Artifact output path |
| `diff_path` | `""` | Path to diff file (takes precedence over repo_path) |
| `repo_path` | `"."` | Path to repository root |
| `enable_model_call` | `false` | Call model for narrative review |
| `handoff_recipient` | `""` | gludd_message recipient (empty = no message) |
| `enable_git_push` | `false` | Always false — report-only |

## Artifacts

- `<artifact_dir>/security_review.json` — findings[], verdict (pass/warn/fail)
- `<artifact_dir>/security_review.md` — human-readable report
