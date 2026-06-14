# flaky_quarantine

Codifies the recurring flaky-test reclassification workflow as a reusable Ansible role.

## What it does

1. Gathers live daemon facts (`gludd_facts`) for system context.
2. Classifies the `failure_signature` into one of: `xpass_strict`, `timeout_under_load`, `fsevents_race`, `intermittent`, `unknown_signature`.
3. Produces a concrete ratchet/pytest marker recommendation per signature type.
4. Writes `flaky_quarantine.json` + `flaky_quarantine.md` artifacts with the recommendation and evidence requirements.
5. Sends a `gludd_message` to `handoff_recipient` with the recommendation (optional).

## Signature Classification

| Signature Pattern | Type | Recommendation |
|---|---|---|
| `xpass` + `strict` | `xpass_strict` | `@pytest.mark.xfail(strict=False)` |
| `timeout` / `timed out` | `timeout_under_load` | `@pytest.mark.timeout(N)` |
| `fsevents` / `kqueue` / `inotify` | `fsevents_race` | `@pytest.mark.skipif(CI)` |
| `flaky` / `intermittent` / `random` | `intermittent` | `xfail(strict=False)` + ratchet |
| other | `unknown_signature` | Manual investigation |

## Safety model

- `enable_auto_apply: false` (default) — report only, no test file edits
- `enable_git_push: false` (default)
- All recommendations include evidence requirements before applying

## Key variables

| Variable | Default | Description |
|---|---|---|
| `test_name` | `""` | Name of the failing test (required) |
| `failure_signature` | `""` | Error text that characterizes the flakiness |
| `timeout_seconds` | `30` | Seconds for timeout marker recommendation |
| `enable_auto_apply` | `false` | Set true to write marker into test file |
| `handoff_recipient` | `""` | Agent/role to notify via gludd_message |
