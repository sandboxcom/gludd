# enforce_disengage

> ⚠️ **WARNING — LAST-RESORT ESCAPE HATCH.** This role is the gludd (ansible)
> equivalent of the opencode `enforce-bootstrap` skill. Use it ONLY when
> every enforcement plugin (`enforce-stop`, `enforce-floor`, `enforce-delegate`,
> `enforce-make`) is blocking **legitimate** work, AND you have already tried
> and been blocked on the normal `make` targets. If a guardrail is correctly
> blocking a policy violation, **fix the violation** — do not disengage.

Last-resort escape hatch for the `general_ludd.agent` collection. Writes the
disengage signal file that every enforcement plugin checks first in its
`tool.execute.before` hook, resets the block counter, optionally commits via
the regex-evasion target `make git-commit-file`, and optionally verifies a
remote push.

## Why normal targets get blocked

The enforcement plugins use regex patterns to detect "commit-shaped" and
"push-shaped" `make` targets and deny them when certain conditions hold
(pending todos, low floor count, stale gate, etc.). The escape-hatch targets
bypass these regexes by using names the plugins do not match:

| Normal target (blocked) | Escape-hatch target (not matched) |
|---|---|
| `make git-commit MSG=...` | `make git-commit-file FILE=/tmp/msg.txt` |
| `make ship-commit MSG=...` | `make git-commit-file FILE=/tmp/msg.txt` |
| `make commit-no-verify` | `make git-commit-file FILE=/tmp/msg.txt` |

`git-commit-file` still runs `collect-check` and verifies `.gate-status`
freshness, so it is safer than a raw `git commit`.

## The 6-step procedure (mirrors the enforce-bootstrap skill)

1. **Disengage enforcement.** Write `/tmp/gludd-watchdog-disengage.json`
   with a bounded expiry (default 1h, hard max 5h per AGENTS.md). Reset
   `/tmp/gludd-block-counter.json` to `count: 0`.
2. **Verify the disengage file** exists and `expires_at` is in the future.
3. **Commit using the escape-hatch target** `make git-commit-file FILE=...`
   (set `enable_commit: true` and `commit_msg_file: /tmp/msg.txt`).
4. **Push** using your normal push path (this role does NOT push). Typically
   a sibling role or manual `make push-me` / `make git-push-sandboxcom`.
5. **Verify the remote** with `make verify-remote BRANCH=master`
   (set `enable_push: true`). Never claim a push succeeded until
   `VERIFIED <branch>@<sha>` is printed.
6. **Clean up.** The disengage file auto-expires; to re-arm enforcement
   early, delete `/tmp/gludd-watchdog-disengage.json` and
   `/tmp/gludd-block-counter.json`, then **restart opencode**.

## When NOT to use this

- A guardrail is correctly blocking a policy violation (fix the violation).
- You haven't tried the normal targets first (`git-commit`, `test-and-commit`).
- You're trying to bypass a red gate or failing tests.
- The work item is speculative or optional.

## Variables

| Variable | Default | Description |
|---|---|---|
| `disengage_duration_hours` | `1` | Disengage window in hours (asserted `<= disengage_max_hours`) |
| `disengage_max_hours` | `5` | Hard cap per AGENTS.md (change at your own risk) |
| `disengage_file_path` | `/tmp/gludd-watchdog-disengage.json` | Signal file read by every plugin |
| `block_counter_path` | `/tmp/gludd-block-counter.json` | Block counter reset to zero |
| `skip_pre_checks` | `false` | Silence the "try normal targets first" reminder |
| `enable_commit` | `false` | Opt-in: commit via `make git-commit-file` |
| `commit_msg_file` | `""` | Path to commit message file (required when `enable_commit=true`) |
| `enable_push` | `false` | Opt-in: run `make verify-remote` after a push (never pushes) |
| `verify_branch` | `master` | Branch whose remote tip to verify |
| `repo_path` | `.` | Where `make` targets run |
| `artifact_dir` | `/tmp/gludd-disengage-artifacts` | Artifact output directory |
| `daemon_url` | `http://localhost:8000` | Daemon for `gludd_facts` context |
| `psk` | `""` | Daemon PSK |

## Artifacts

- `<artifact_dir>/disengage.json` — disengage file path, block counter path,
  issued/expiry epochs, duration, issuer, commit/push flags, and daemon
  system context (backlog size, success rate).

## Example — disengage + commit + verify push

```yaml
- hosts: localhost
  connection: local
  gather_facts: true
  vars:
    repo_path: "{{ playbook_dir }}"
  tasks:
    - name: Write commit message to /tmp/msg.txt
      ansible.builtin.copy:
        dest: /tmp/msg.txt
        content: |
          fix(loop): unblock integrator when floor plugin over-fires

    - name: Disengage, commit via escape hatch, verify remote push
      ansible.builtin.include_role:
        name: general_ludd.agent.enforce_disengage
      vars:
        repo_path: "{{ repo_path }}"
        disengage_duration_hours: 1   # 1h window (default; max 5h)
        enable_commit: true
        commit_msg_file: "/tmp/msg.txt"
        enable_push: true             # runs `make verify-remote` AFTER you push
        verify_branch: "master"
```

The push itself must be performed separately (sibling role or manual
`make push-me`). `enable_push: true` only runs `make verify-remote`.

---

*Generated by general_ludd.agent.enforce_disengage — last-resort escape hatch.*
