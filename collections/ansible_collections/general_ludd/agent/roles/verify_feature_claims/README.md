# verify_feature_claims

Audits `docs/features.yml` against actual code/test evidence on disk. For
every feature claimed at 100%, verifies that each evidence reference (test
files, source files, role directories, module files, molecule scenarios)
actually exists and that any commit hashes in the notes field are reachable
in git log.

## FQCN

`general_ludd.agent.verify_feature_claims`

## Verdicts

| Verdict      | Meaning | Playbook |
|--------------|---------|----------|
| VERIFIED     | All evidence refs exist on disk + all commit hashes reachable | PASS |
| PARTIAL      | Some evidence refs exist, some missing (claimed 100%) | WARN |
| UNVERIFIED   | Feature claims 100% but has NO evidence refs at all | WARN |
| FALSE_CLAIM  | Feature claims 100% but ALL evidence refs point to nonexistent paths | **FAIL** |

## What is checked

Each `evidence_refs` entry in `docs/features.yml` is resolved to a filesystem
path and checked with `ansible.builtin.stat`:

| Ref type    | Example                            | Checked path |
|-------------|------------------------------------|--------------|
| `test`      | `test:tests/unit/test_foo.py`      | `<project_root>/tests/unit/test_foo.py` |
| `file`      | `file:src/foo/bar.py::symbol`      | `<project_root>/src/foo/bar.py` |
| `role`      | `role:agent_task`                  | `<project_root>/collections/.../roles/agent_task/` |
| `module`    | `module:gludd_ping`                | `<project_root>/collections/.../plugins/modules/gludd_ping.py` |
| `molecule`  | `molecule:role_implement_change`   | `<project_root>/molecule/playbooks/<name>/` + `molecule/<name>/` |

Additionally, if `check_commits: true` (default), commit hashes in the
`notes` field matching the pattern `[hexsha]` (7–40 hex chars) are verified
with `git log --oneline -1 <sha>`.

When `check_test_content: true` (default), test files are also checked for
non-zero content (an empty test file is treated as missing evidence).

## Usage

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.verify_feature_claims
      vars:
        project_root: "/workspace/gludd"
```

Or with defaults (uses playbook directory):

```yaml
- hosts: localhost
  roles:
    - general_ludd.agent.verify_feature_claims
```

## Inputs

| Variable              | Default | Description |
|-----------------------|---------|-------------|
| `project_root`        | `{{ playbook_dir }}` | Project root containing `docs/features.yml` |
| `features_path`       | `docs/features.yml` | Path to features manifest relative to project_root |
| `min_pct_to_verify`   | `100` | Only verify features with pct >= this value |
| `check_commits`       | `true` | Verify commit hashes in notes with git log |
| `check_test_content`  | `true` | Verify test files have non-zero content |
| `artifact_dir`        | `/tmp/gludd-verify-feature-claims` | Output directory for reports |

## Outputs

- `verify_feature_claims_report.json` — full per-feature verdicts
- `verify_feature_claims_report.md` — human-readable summary

## Playbook exit codes

| Exit | Meaning |
|------|---------|
| 0 | No FALSE_CLAIMs (all verified features have evidence) |
| 1 | Task-level failure — `ansible.builtin.fail` on FALSE_CLAIM |
| 2 | Ansible error (manifest unreadable, bad YAML, etc.) |

## Edge cases handled

- Features with `pct: 0` are skipped (not claimed as complete)
- Features with `pct: 100` and empty `evidence_refs` → UNVERIFIED (WARN)
- Features with `pct: 100` and `evidence_refs` where ALL paths are missing → FALSE_CLAIM (FAIL)
- Features with `pct: 100` and SOME evidence missing → PARTIAL (WARN)
- Molecule scenarios checked in multiple search paths
- Empty test files (0 bytes) treated as missing evidence when `check_test_content: true`
- Git log failures (missing commits, non-git directory) are non-fatal per-commit
- Multiple commit hashes with the same value are deduplicated

## Assertions

The role makes these assertions during verification:

1. **Feature existence**: each feature ID in features.yml is tracked and can be
   cross-referenced against the verdict list.
2. **Evidence ref existence**: every `test:`, `file:`, `role:`, `module:`, and
   `molecule:` reference is stat'd on disk.
3. **Test file content**: when `check_test_content: true`, test files must have
   `size > 0` to count as verified.
4. **Commit hash reachability**: when `check_commits: true`, every `[hexsha]`
   in notes is checked against `git log` (exit code 0 = found).
5. **Verdict classification**: every feature with pct >= `min_pct_to_verify`
   receives a verdict (VERIFIED, PARTIAL, UNVERIFIED, or FALSE_CLAIM).
6. **Aggregate counts**: summary counts match the per-feature verdicts.
7. **Gate assertion**: `ansible.builtin.fail` fires if ANY feature is a
   FALSE_CLAIM, failing the playbook.

For each feature verified, the role makes:
- 1 stat call per evidence ref
- 1 additional stat call per test evidence ref (content check)
- 1 git log call per unique commit hash in notes

Total assertions per feature = `len(evidence_refs) + len(test_evidence_refs) + len(unique_commits)`.

## See also

- `docs/features.yml` — the manifest this role audits
- `scripts/check_readme_status_current.py` — related gate that checks README vs version
- `collections/ansible_collections/general_ludd/agent/roles/enforcement_verify/` — enforcement role that also checks gate status
