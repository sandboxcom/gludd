# Pipeline triage role

`general_ludd.git_release.pipeline_triage` reduces one controller-local JUnit
XML report to content-free terminal outcome evidence. The paired action plugin
keeps report bytes on the controller and the companion module fails closed if
the action boundary is bypassed.

## Required variables

| Variable | Meaning |
|---|---|
| `git_release_pipeline_triage_enabled` | Must be explicitly `true`. |
| `git_release_pipeline_triage_root` | Absolute, normalized root without symbolic-link components. |
| `git_release_pipeline_triage_report_path` | Relative path to one regular JUnit XML report. |
| `git_release_pipeline_triage_result_fact` | Optional fact name for the content-free result. |

The report may contain at most 16 MiB, 100,000 uniquely identified testcases,
and 64 failures or errors. DTDs, entities, symbolic links, hard links,
concurrent mutation, conflicting summary counts, and ambiguous testcase
outcomes fail closed. Test names, failure text, properties, stdout, and stderr
are never returned. Normal and check mode perform the identical read-only
operation and report `changed: false`.

## Zero-downtime delivery and rollback

Stage the collection in a digest-addressed execution-environment candidate,
run the dedicated Molecule scenario against canary JUnit evidence, and admit
new triage jobs only after the candidate digest is healthy. Drain in-flight
controller jobs before moving the remaining inventory. Rollback selects the
previous immutable environment digest; it never rewrites a report or changes a
managed host.
