# delegate_discipline_check

Read-only audit of delegation discipline for the `general_ludd.agent` collection.

This role is the **gludd equivalent of the opencode `enforce-delegate.ts`
plugin**. Where the plugin *enforces* (denies tool calls inline), this role
*reports* the same dimensions so a playbook, CI job, or operator can observe
delegation health without running an agent session.

## Why this role exists

AGENTS.md codifies three delegation-discipline contracts that keep the agent
multitasking cost-efficiently:

1. **Sonnet-dominant dispatch ratio** — `sonnet` is the cost-efficient default;
   the time-bound 2:1 target (`sonnet_target_share: 0.67`) keeps opus tokens
   reserved for coordination, not grunt work.
2. **Worktree disk discipline** — each isolated worktree agent creates a
   ~320 MB venv; exceeding `worktree_cap` or the free-disk floor causes
   ENOSPC deadlocks.
3. **Main-thread streak** — 4+ consecutive mutating tool calls with no
   intervening dispatch is the grind-inline anti-pattern the plugin
   hard-blocks at threshold 4.

The plugin enforces all three inline. This role reads the same state files
and emits a `{healthy, degraded, critical}` verdict with breached dimensions
and recommended remediation. **It never enforces** — enforcement lives in
the plugin layer.

## Dimensions audited

| Dimension | Source | Breached when |
|---|---|---|
| `model` | `{{ model_util_path }}` — `{history: [...]}` | `sonnet_target_share > 0` AND `sonnet_count / total_count < sonnet_target_share` |
| `streak` | `{{ mainthread_streak_path }}` — `{count: N}` | `streak >= 3` (warning) / `>= 4` (critical) |
| `disk` | `df -m {{ worktree_root }}` | `Avail (MB) < disk_free_min_mb` |
| `worktree` | `find {{ worktree_root }} -type d` | `count > worktree_cap` |

All reads are **fail-open**: a missing/unreadable state file is treated as
healthy for that dimension (no enforcement wedges the play).

## Verdict logic

| Status | When |
|---|---|
| `healthy` | zero dimensions breached |
| `degraded` | exactly one dimension breached |
| `critical` | 2+ dimensions breached, OR `sonnet_share < 0.3`, OR `streak >= 4` |

The `critical` overrides model the plugin's behaviour: a sub-30% sonnet share
or a streak at the plugin's hard-block threshold is critical regardless of
how many other dimensions happen to be green.

## Variables

| Variable | Default | Description |
|---|---|---|
| `model_util_path` | `/tmp/gludd-model-util.json` | Plugin state file with the rolling 20-sample model history |
| `mainthread_streak_path` | `/tmp/gludd-mainthread-streak.json` | Plugin state file with the consecutive main-thread streak count |
| `worktree_cap` | `6` | Max worktree-isolated agents (AGENTS.md disk-discipline section) |
| `worktree_root` | `{{ lookup('env','HOME') }}/.gludd-worktrees` | Directory to scan for worktree count + free disk |
| `sonnet_target_share` | `0.67` | 2:1 sonnet target; set to `0.0` to DISABLE the model dimension |
| `disk_free_min_mb` | `2048` | Minimum free MB on the worktree filesystem |
| `fail_on_critical` | `false` | Fail the play when status == critical |
| `artifact_dir` | `/tmp/gludd-delegate-check-artifacts` | Where the JSON artifact is written |
| `daemon_url` | `http://localhost:8000` | Daemon URL for `gludd_facts` |
| `psk` | `""` | Pre-shared key (no_log) |

## Artifacts

- `<artifact_dir>/delegate_discipline_check.json` — full verdict with
  per-dimension metrics, breach flags, and remediation hints.

## Example

Audit-only (does not fail the play even on critical):

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.delegate_discipline_check
```

Strict mode — fail CI when delegation discipline is critical:

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.delegate_discipline_check
      vars:
        fail_on_critical: true
        sonnet_target_share: 0.67
        worktree_cap: 6
        disk_free_min_mb: 2048
```

Disable the model dimension (use on a non-expensive main model where the
plugin's sonnet-ratio gate is inert by design):

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.delegate_discipline_check
      vars:
        sonnet_target_share: 0.0
```

## Enforcement boundary

This role is **report-only**. The actual block-the-tool-call enforcement
happens in `.opencode/plugin/enforce-delegate.ts`, which:

- `tool.execute.before` — for `task`/`agent`/`workflow` dispatches, evaluates
  the model ratio + disk discipline and `throw`s to deny.
- `tool.execute.before` — for every tool, evaluates the force-delegate +
  mainthread streak rules and `throw`s to deny.
- `tool.execute.after` — updates the streak counter.

The role and the plugin share the same state files, so a playbook using
this role sees exactly what the plugin would see on the next tool call.
