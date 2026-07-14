# command_injection

Command injection awareness role for the `general_ludd.agent` collection.

## Description

Scans source code for command injection patterns: `os.system()`, `subprocess`
with `shell=True`, `os.popen()`, `eval()`/`exec()`, and string-concatenation
command construction. Generates remediation suggestions with safe alternatives
(`subprocess.run` args list, `shlex.quote()`, allowlist validation). Tool-aware
(Bandit, Semgrep, ShellCheck, strace). **REPORT-ONLY.**

## Variables

| Variable | Default | Description |
|---|---|---|
| `artifact_dir` | `/tmp/gludd-command-injection` | Artifact output path |
| `target_path` | `"."` | Codebase path to audit |
| `enable_scan` | `false` | Run grep-based scans (false = skip) |
| `enable_git_push` | `false` | Always false — report-only |
| `audit_patterns` | (see defaults) | List of pattern/severity/description dicts |
| `remediation_tools` | (see defaults) | Tool references for SAST/runtime analysis |
| `attack_vectors` | (see vars) | Command injection vector taxonomy |
| `defense_strategies` | (see vars) | Defense best-practice checklist |

## Artifacts

- `<artifact_dir>/command_injection.json` — findings, severity breakdown, remediation suggestions
- `<artifact_dir>/command_injection.md` — human-readable report with findings table and remediation guidance
