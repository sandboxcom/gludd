# type_safety_audit

Type-safety audit role for the `general_ludd.agent` collection.

Ports the `.opencode/skills/type-safety/SKILL.md` capability into a gludd
ansible role. Scans Python source for `Any` usage in type annotations and
gates on NEW violations.

## Description

Wraps `scripts/check_type_strictness.py` — the AST-based scanner that powers
`make check-types`. The scanner uses `ast` (no code execution) and detects
`Any` in:

- function return annotations (`def f() -> Any`)
- parameter annotations (`def f(x: Any)`)
- annotated assignments (`x: dict[str, Any] = ...`)
- nested container types (`dict[str, Any]`, `list[Any]`, `tuple[Any, ...]`)
- Optional / Union forms (`Optional[Any]`, `Union[int, Any]`)
- attribute form (`typing.Any`)
- stringified forward references (`x: "dict[str, Any]"`)

**Enforces on new code:** pass a baseline file and the gate fails only when a
violation is NOT already listed. **Fails the play** when new violations exceed
the threshold.

## Variables

| Variable | Default | Description |
|---|---|---|
| `checker_script` | `scripts/check_type_strictness.py` | Path to the AST scanner |
| `source_path` | `src` | Source tree to scan |
| `baseline_file` | `config/type_any_baseline.txt` | Tolerated `file:line` violations (empty/missing = scan all) |
| `max_new_violations` | `0` | New violations tolerated before failure (0 = zero-tolerance) |
| `artifact_dir` | `/tmp/gludd-type-safety-audit` | Artifact output path |
| `daemon_url` | `http://localhost:8000` | Daemon URL |
| `psk` | `""` | Pre-shared key (no_log) |
| `enable_model_call` | `false` | Call model for type-narrowing suggestions |
| `model_profile` | `""` | Model profile hint (empty = daemon default) |
| `handoff_recipient` | `""` | gludd_message recipient (empty = no message) |

## Artifacts

- `<artifact_dir>/type_safety_audit.json` — violations[], counts, verdict
- `<artifact_dir>/type_safety_audit.md` — human-readable report with table

## Example

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.type_safety_audit
      vars:
        source_path: "src"
        baseline_file: "config/type_any_baseline.txt"
        max_new_violations: 0
        enable_model_call: true
```

## Policy

`Any` is acceptable only for untyped C-extension interop (with a documented
`# type: ignore`) or a genuinely dynamic dispatcher. Every other `Any` is a
bug. See `.opencode/skills/type-safety/SKILL.md` for the full policy and the
type-tracing workflow.
