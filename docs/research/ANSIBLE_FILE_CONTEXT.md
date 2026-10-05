# Ansible File Change Tracking

> **Feature**: Agent context injection via ansible-runner file-change tracking.
> **Source**: `src/general_ludd/ansible/file_tracker.py`

## Overview

The `FileChangeTracker` class captures file-level changes produced by Ansible
file-management modules during a playbook run, then surfaces a structured
context dict that an LLM agent can consume. It operates at two points in time:

1. **Pre-run** — snapshots the current git `HEAD` SHA.
2. **Post-run** — queries `git diff` since the captured SHA and exposes the
   delta (both a name-status summary and a full unified diff) alongside
   per-file event metadata.

The tracker ingests events one at a time via an `event_handler()` callback
that is compatible with **ansible-runner's event callback interface**, and the
post-run methods (`get_git_diff()`, `get_changed_files()`,
`build_agent_context()`) are idempotent queries — they do not mutate the
tracker's internal state.

---

## Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│                    ansible-runner process                     │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  plays → tasks → modules (copy, template, file, …)   │   │
│  └──────────┬───────────────────────────────────────────┘   │
│             │  event callbacks                                │
│             ▼                                                 │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  FileChangeTracker.event_handler(event_data)          │   │
│  │    • filters runner_on_ok events only                 │   │
│  │    • checks module ∈ FILE_MODULES                     │   │
│  │    • extracts dest, src, checksum, changed, diff      │   │
│  └──────────┬───────────────────────────────────────────┘   │
│             │  (in-memory list, no I/O)                       │
└─────────────┴───────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                    Post-playbook (~ < 1 s)                    │
│                                                               │
│  FileChangeTracker.get_git_diff()                             │
│    → git diff <sha_before> HEAD                               │
│                                                               │
│  FileChangeTracker.get_changed_files()                        │
│    → git diff --name-status <sha_before> HEAD                │
│                                                               │
│  FileChangeTracker.build_agent_context()                      │
│    → { playbook_summary, git_state, file_details, git_diff } │
└─────────────────────────────────────────────────────────────┘
```

### Design decisions

- **`HEAD` not `--staged`.**  The diff is taken against the committed state
  before the run, not the staged state.  This captures ALL changes made by
  the playbook regardless of whether the agent commits them separately.

- **`runner_on_ok` only.**  `runner_on_failed`, `runner_on_skipped`, and
  other event types are ignored.  If a task fails, it produced no effective
  file change.  If the agent needs to surface a failed module's impact, it
  reads the play's `msg`/`stderr` from the failed event separately.

- **No persistent state.**  The event list lives in memory.  An agent that
  wants persistence must serialize `build_agent_context()` itself.

- **Fallback for empty repos.**  When `git rev-parse HEAD` returns nothing
  (an empty repo before the first commit), `get_git_diff()` and
  `get_changed_files()` fall back to a working-tree diff
  (`git diff` / `git diff --name-status`), so the feature works from the
  first playbook run.

---

## FILE_MODULES

The tracker recognises **8 Ansible modules** that modify files on disk:

| Module | Bare name | FQCN | Typical effect |
|---|---|---|---|
| `copy` | `copy` | `ansible.builtin.copy` | Copies a local file to remote/controlled path |
| `template` | `template` | `ansible.builtin.template` | Renders a Jinja2 template to a destination path |
| `file` | `file` | `ansible.builtin.file` | Sets ownership, mode, state (`touch`, `absent`, …) |
| `blockinfile` | `blockinfile` | `ansible.builtin.blockinfile` | Inserts/updates a marked block in a file |
| `lineinfile` | `lineinfile` | `ansible.builtin.lineinfile` | Ensures a particular line exists in a file |
| `replace` | `replace` | `ansible.builtin.replace` | Replaces all occurrences of a regex pattern in a file |
| `assemble` | `assemble` | `ansible.builtin.assemble` | Concatenates snippets into a single file |
| `ini_file` | `ini_file` | `ansible.builtin.ini_file` | Manages `key=value` entries in INI-style files |

The `FILE_MODULES` constant is the union of the bare names and their
FQCN (`ansible.builtin.*`) variants — 16 entries total. It is a `frozenset`
so it cannot be accidentally mutated.

### How the event handler resolves module names

ansible-runner exposes the task name in the event as `event_data.task`, with
the format `"<module_name> <action_name>"` (e.g. `"ansible.builtin.copy
copy"`, `"template Deploy config template"`).  The helper
`_task_uses_file_module()` extracts the first space-delimited token and
checks it against `FILE_MODULES`.

---

## Agent Context

The tracker's primary deliverable is `build_agent_context()`, which returns a
dict designed to be injected directly into an LLM agent's context window:

```python
{
    "playbook_summary": {
        "file_events_count": 3,
        "events": [
            {
                "task": "template Deploy nginx config",
                "host": "webserver-01",
                "dest": "/etc/nginx/nginx.conf",
                "src": "templates/nginx.conf.j2",
                "checksum": "abc123...",
                "changed": True,
                "diff": {"prepared": "...", "after": "..."},
            },
            ...
        ],
    },
    "git_state": {
        "sha_before": "a1b2c3d...",
        "sha_after": "e4f5g6h...",
    },
    "file_details": [
        # (same event list — duplicated for direct consumption)
    ],
    "git_diff": "diff --git a/etc/nginx/nginx.conf b/etc/nginx/nginx.conf\n...",
}
```

| Key | Type | Purpose |
|---|---|---|
| `playbook_summary` | `dict` | Count of file-modifying events + per-event metadata |
| `git_state` | `dict` | Git SHAs before and after the playbook run |
| `file_details` | `list[dict]` | Per-event `{dest, src, checksum, changed, diff}` |
| `git_diff` | `str` | Full unified diff for the entire repo |

The `file_details` key duplicates the event list so that agent tools can
consume the per-file metadata directly without navigating
`playbook_summary.events`.

---

## Enabling Diffs

By default, Ansible modules do NOT include `diff` in their result unless
diff mode is enabled. Without it, the `diff` field in each captured event
will be absent, and the agent only receives `{dest, src, checksum, changed}`.

### Enable globally (environment variable)

```bash
export ANSIBLE_DIFF_ALWAYS=1
```

### Enable per-invocation (command line)

```bash
ansible-playbook --diff playbook.yml
```

### Enable in ansible.cfg

```ini
[defaults]
diff_always = True
```

The tracker itself never forces diff mode — it captures whatever the Ansible
runtime provides.  **Recommendation**: set `ANSIBLE_DIFF_ALWAYS=1` in the
daemon environment so every agent playbook run produces rich diffs
automatically.

---

## Integration Points

The tracker is designed to integrate at two points in the ansible-runner
execution lifecycle:

1. **`event_handler` as a callback.**  Pass `tracker.event_handler` to
   ansible-runner's `event_handler` keyword argument:

   ```python
   tracker = FileChangeTracker(repo_root=project_root)
   runner = ansible_runner.run(
       private_data_dir=private_dir,
       playbook="playbook.yml",
       event_handler=tracker.event_handler,
   )
   ```

2. **Agent context injection.**  After the run completes, call
   `tracker.build_agent_context()` and merge the result into the agent's
   system prompt or tool context:

   ```python
   context = tracker.build_agent_context()
   agent_prompt = f"""
   You just ran an Ansible playbook. Here is the file-change context:

   Files changed: {context['playbook_summary']['file_events_count']}
   Git diff:
   {context['git_diff']}
   """
   ```

---

## Error Handling

- **git not installed / not in PATH** — `subprocess.run` calls will raise
  `FileNotFoundError`.  Wrap construction in a try/except or guard with
  `shutil.which("git")`.
- **not a git repo** — `git rev-parse HEAD` returns exit code 128 (`fatal:
  not a git repository`).  `_git_rev_parse()` returns `None`, which causes
  all diff methods to fall back to working-tree diffs (which will also fail
  with `fatal: not a git repository`).  The result is an empty string for
  all diff queries.
- **permission denied / no read access** — git subprocess errors are not
  raised by default (`check=False`).  `proc.returncode` != 0 produces empty
  stdout, so diff output will be empty strings.

---

## Related Files

| File | Role |
|---|---|
| `src/general_ludd/ansible/file_tracker.py` | Implementation (196 lines) |
| `src/general_ludd/ansible/core_runner.py` | ansible-runner execution wrapper |
| `src/general_ludd/ansible/isolation.py` | Process isolation config (`_WRITE_MODULES`) |
| `tests/unit/test_file_tracker.py` | Unit tests |
