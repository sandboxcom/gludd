# ornith_self_improve

The bidirectional half of the gludd × Ornith symbiotic loop. Pulls the
artifacts that NEED improvement (training pairs whose outcome was
`rejected_by_gate`, `rejected_by_review`, or `reverted`), invokes Ornith to
propose a fix, opens a PR, and files a human-todo so the proposal is reviewed
before it lands.

## FQCN

`general_ludd.agent.ornith_self_improve`

## The loop

```
rejected training pairs ─►  ornith_self_improve role
                                   │
                                   ▼
                         gludd_ornith (state=pairs)
                                   │
                                   ▼
                    pick artifact with ≥ require_minimum_rejection_count
                                   │
                                   ▼
                    gludd_ornith (state=improve)  ─►  Ornith rollout
                                   │
                                   ▼
                         write proposed-<artifact>.patch
                                   │
                                   ▼
                    gludd_git (branch + commit + push)  ─►  GitHub PR
                                   │
                                   ▼
                    gludd_human_todo (category=decision)  ─►  human gate
```

The PR is NEVER auto-merged. The human-todo is the gate: a human reviews
the diff, runs `make gate` on the PR branch, and either merges or dismisses
the human-todo. Dismissing cancels the proposal.

## Safety defaults

- `ornith_enabled: false` — the role is a no-op until an operator opts in.
- `max_artifacts_per_run: 1` — one improvement per invocation.
- `require_minimum_rejection_count: 3` — only artifacts with ≥3 rejections
  in the lookback window are candidates.
- `lookback_days: 14` — older rejections are ignored.
- One PR per artifact per run — never batched.

## Enabling

Either set `ornith_enabled: true` in the gludd user config, or pass it as a
var when invoking the role:

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.ornith_self_improve
      vars:
        ornith_enabled: true
        repo_path: "/workspace/gludd"
        parent_agent_todo_id: "TODO-abc123"
```

The scheduled playbook `playbooks/ornith_self_improve.yml` runs this role
weekly (Mondays 04:00 UTC) when Ornith is enabled. The seed script
`scripts/seed_ornith_self_improve_schedule.py` registers the schedule entry
idempotently at install time (it no-ops when Ornith is not configured).

## Opting out

```bash
# Permanently disable the scheduled entry:
gludd perm deny agent:ornith improve

# Or remove the schedule:
gludd scheduled list
gludd scheduled remove --name ornith_self_improve
```

Setting `ornith_enabled: false` in the config also fully disables the role
at the task level (it writes a `skip.json` artifact and exits).

## Outputs

Per artifact improved, the role writes to `artifact_dir`:

- `proposed-<artifact_name>.patch` — the raw improvement diff.
- `proposed-<artifact_name>.json` — summary with PR URL, human-todo id,
  failure citations.

When no artifact meets the rejection threshold, it writes `skip.json`.

## Molecule daemon isolation

The end-to-end scenario owns mock port `8897`; it does not share the OpenBao
backup scenario's port. Both scenarios run the shared cleanup before and after
their work. Cleanup verifies that the pidfile points to this checkout's mock
daemon and port, sends `SIGTERM` only to that owned process, waits for the port
to close, and only then removes the pidfile. This prevents a health check from
accepting an old daemon while the replacement process fails to bind.

This follows two long-lived user reports: the
[macOS `SimpleHTTPServer` address-in-use discussion](https://stackoverflow.com/questions/19071512/socket-error-errno-48-address-already-in-use)
identifies a still-running prior server as the common cause and recommends
either stopping that process or assigning another port; the
[Python TCPServer restart discussion](https://stackoverflow.com/questions/15260558/python-tcpserver-address-already-in-use-but-i-close-the-server-and-i-use-allow)
shows that port reuse can still be timing-sensitive after clients have
connected. Gludd therefore uses both distinct scenario namespaces and
observable, ownership-checked shutdown instead of retrying the next request.

## See also

- `docs/design/SYMBIOTIC_AGENT_INTEGRATION.md` §5.7 (self-improvement loop)
- `collections/.../roles/self_improve_propose/` (the codebase-introspection half)
- `collections/.../roles/self_improve_promote/` (the validated-promotion half)
