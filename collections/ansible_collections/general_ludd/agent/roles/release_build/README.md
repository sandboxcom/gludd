# release_build

Codifies the W11 release workflow: PEP 440 version stamping + artifact build.

## What it does

1. Gathers live daemon facts (`gludd_facts`) for context.
2. Generates or validates a PEP 440 version string (e.g. `0.1.0-alpha.202406141200`).
3. Asserts version is PEP 440 compliant: no leading `v`, dot before timestamp.
4. **If `enable_build: true` and not check_mode:**
   - Stamps version into `pyproject.toml` and `version_init_path` (if set)
   - Runs `make build_make_target` (default: `build-executable`)
   - Verifies `expected_artifact_path` was produced
5. Writes `release_build.json` + `release_build.md` artifacts.
6. Sends `gludd_message` to `handoff_recipient` on completion (optional).

## Safety model

- `enable_build: false` (default) — no file stamps, no builds
- `ansible_check_mode: true` — all write/build ops skipped even if enabled
- `enable_git_push: false` (default) — no pushes

## Key variables

| Variable | Default | Description |
|---|---|---|
| `enable_build` | `false` | Set true to stamp version and run build |
| `version_string` | `""` | Explicit version (empty = generate timestamped) |
| `version_base` | `"0.1.0"` | Base for generated version |
| `build_make_target` | `"build-executable"` | Make target for build |
| `expected_artifact_path` | `""` | Path to verify after build |
| `version_init_path` | `""` | Path to `__init__.py` to stamp |

## Artifact

`release_build.json`:
```json
{
  "role": "release_build",
  "status": "dry_run",
  "version": "0.1.0-alpha.202406141200",
  "build_ran": false,
  "artifact_produced": "skipped"
}
```
