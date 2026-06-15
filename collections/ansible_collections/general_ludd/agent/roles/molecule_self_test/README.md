# molecule_self_test

Enable gludd to run molecule tests on its own roles/collections from within its agentic framework.

## What it does

1. Gathers live daemon facts (`gludd_facts`) for system context.
2. **Enumerates** available molecule scenarios (`molecule/playbooks/` dirs) and the roles/modules they cover.
3. **Optionally** runs molecule — ONLY when `enable_run: true` AND NOT in check mode. Default is `false` (safe by default).
4. Parses pass/fail output into structured lists.
5. Computes coverage: which roles/modules have scenarios vs which are missing.
6. Determines verdict: `PASS` / `PARTIAL` / `FAIL` / `NO_DATA`.
7. Writes `molecule_self_test.json` + `molecule_self_test.md` artifacts.
8. Sends a `gludd_message` handoff on failures (when `handoff_recipient` is set).

## Key variables

| Variable | Default | Description |
|---|---|---|
| `enable_run` | `false` | Set `true` to actually run molecule (heavy op) |
| `molecule_output_override` | `PASS noop\nPASS role_report_status\n...` | Canned output used when `enable_run=false` or check_mode |
| `scenarios` | `[]` | Specific scenarios to run (empty = all) when `run_all=false` |
| `run_all` | `false` | When `true` and `enable_run=true`: run `make molecule-test-all` |
| `repo_path` | `"."` | Path where `Makefile` and `molecule/` live |
| `artifact_dir` | `/tmp/gludd-molecule-self-test` | Where to write artifacts |
| `daemon_url` | `http://localhost:8000` | Daemon for gludd_facts / gludd_message |
| `handoff_recipient` | `""` | Agent/role to notify on failures (empty = no send) |

## Safety model

- `enable_run: false` (default) → molecule never runs; uses `molecule_output_override` for all parsing/reporting logic
- `ansible_check_mode: true` → molecule never runs even if `enable_run: true`
- No file mutations outside `artifact_dir`
- `make molecule-test SCENARIO={{ item }}` / `make molecule-test-all` — fixed make targets only (no templated shell input)

## How to trigger a real run

```yaml
- name: Run molecule self-test (real)
  ansible.builtin.include_role:
    name: general_ludd.agent.molecule_self_test
  vars:
    enable_run: true
    run_all: true
    repo_path: "/path/to/gludd"
    artifact_dir: "/tmp/gludd-mst-real"
    handoff_recipient: "gate_triage"
```

## Artifact

`molecule_self_test.json`:
```json
{
  "role": "molecule_self_test",
  "status": "completed",
  "enable_run": false,
  "run_used_override": true,
  "scenarios": ["noop", "role_report_status", "test_gludd_ping"],
  "passed": ["noop", "role_report_status"],
  "failed": [],
  "coverage": {
    "roles_total": 34,
    "roles_with_scenario": 34,
    "roles_missing": [],
    "modules_total": 12,
    "modules_with_scenario": 12,
    "modules_missing": []
  },
  "verdict": "PASS"
}
```

## Molecule scenario

See `molecule/playbooks/role_molecule_self_test/` — port 8855.
Prepare seeds a `molecule_output_override` with mixed PASS/FAIL lines.
Verify asserts the role parsed them into correct `passed`/`failed` lists, computed coverage, and determined the correct verdict.
