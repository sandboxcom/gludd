# AI Parallel Dispatch Barrier Contract

Status: beta4 release contract

## Contract

The `ai_parallel_dispatch` role launches one bounded batch with
`async`/`poll: 0`, selects an explicit wait-set from the configured join
policy, and polls only job IDs in that set. A barrier item completes only when
its current job ID belongs to the selected wait-set and `async_status.finished`
coerces to true.

Harvest republishes a job only when its result exists, contains response text,
is explicitly finished, and is not failed. Unfinished or failed optional jobs
remain absent; the role's existing join-policy assertion remains the single
fail-closed decision point.

## ZDD and security

No partial or failed response enters `_apd_results`. Required jobs therefore
cannot be mistaken for successful work during a rolling execution, and
best-effort optional jobs can time out without corrupting completed results.
The role continues to redact job output when a PSK is configured.

## Observability and compatibility

Barrier and harvest tasks label every job ID or call name, retain bounded
retry/delay controls, and preserve the full async ledger. Explicit `| bool`
coercion accepts both legacy integer and current Boolean `finished` values.

## Verification

`tests/unit/test_ai_parallel_dispatch_role.py` pins the selected wait-set,
bounded asynchronous polling, finished-only/non-failed harvest, concurrency
capping, and the handler-barrier variant.

## Practitioner evidence

- [Ansible #85048](https://github.com/ansible/ansible/issues/85048) documents
  the long-lived `async_status.finished` integer-to-Boolean compatibility
  problem and the need for explicit Boolean coercion in conditions.
