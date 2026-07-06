# scan_conflict_markers

Scans git-tracked text files for unresolved git conflict markers.

## Description

Wraps `scripts/scan_conflicts.py` to detect leftover conflict markers from
botched merges/rebases. Flags:
- `<<<<<<<` — start of ours (always flagged)
- `|||||||` — diff3 merge-base (always flagged)
- `=======` — separator (only flagged when file ALSO contains `<<<<<<<` or `>>>>>>>` — suppresses markdown horizontal rule false-positives)
- `>>>>>>>` — end of theirs (always flagged)

**Stdlib-only** — no project imports. Binary files are skipped. Fixture dirs
(`conflict_fixtures`) are excluded. With no `scan_paths`, scans every
git-tracked file via `git ls-files`.

## Variables

| Variable | Default | Description |
|---|---|---|
| `repo_path` | `.` | Path to the gludd repo root |
| `scan_paths` | `[]` | Files to scan (empty = all git-tracked) |
| `artifact_dir` | `/tmp/gludd-conflict-scan` | Artifact output directory |

## Example

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.scan_conflict_markers
```
