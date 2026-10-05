# Hosted runner capacity

## Purpose

Development pushes start both `Build and Release` and `Molecule Tests`. Their
startup matrices previously requested eight `ubuntu-latest` runners at once.
GitHub run `37363551133` then lost one gate leg and run `37363666474` lost three
Molecule legs before any step ran. A full same-SHA retry acquired the first gate
wave, but three of six Molecule legs again expired without a runner. After its
gates passed, the same Build retry fanned out twenty jobs and seven Linux jobs
expired before setup. Each failed Check Run carried the exact annotation `The
job was not acquired by Runner of type hosted even after multiple attempts`; no
repository test failed.

## Contract

- Every Linux job in both workflows pins `ubuntu-24.04`. This removes the
  announced `ubuntu-latest` migration from release evidence while retaining the
  same current image generation.
- The two-version Build gate uses `max-parallel: 1` and the six-shard standalone
  Molecule job uses `max-parallel: 3`. At workflow startup, Gludd therefore asks
  for at most four Linux runners instead of eight.
- Build's later FreeLLMAPI, test, and Molecule matrices use limits of one, three,
  and two respectively. They request at most six matrix runners instead of
  fourteen while retaining concurrency inside both release-critical suites.
- `fail-fast: false` remains intact. Every Python version and every Molecule
  shard still runs and reports independently; capacity is bounded, not coverage.
- No automatic retry follows attempt 2. The failure-ledger recovery guard owns
  one infrastructure-only retry, after which scheduling or platform capacity
  must change before another candidate is dispatched.

This is zero-downtime delivery: the current candidate continues to completion,
the workflow change is validated locally, and only a newer exact SHA starts the
bounded topology. Rollback is the inverse YAML change; it does not alter release
artifacts, application state, credentials, or production resources.

## Practitioner and official evidence

GitHub documents that matrix jobs maximize concurrency by default and that
`strategy.max-parallel` bounds simultaneous legs
([running job variations](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/run-job-variations#defining-the-maximum-number-of-concurrent-jobs)).
The explicit image pin follows GitHub's runner-image lifecycle rather than
allowing an alias migration to change a candidate underneath the project
([runner images](https://github.com/actions/runner-images)).

Long-lived practitioner reports show the same acquisition annotation on simple,
unchanged workflows, queued jobs expiring around fifteen minutes, and later
retries sometimes succeeding. They attribute the condition to hosted runner
infrastructure rather than workflow steps
([GitHub Community #186208](https://github.com/orgs/community/discussions/186208),
[#165291](https://github.com/orgs/community/discussions/165291), and
[#166283](https://github.com/orgs/community/discussions/166283)). A separate
13-job report describes random cancellations and includes GitHub staff
confirmation of a platform-side incident
([GitHub Community #126539](https://github.com/orgs/community/discussions/126539)).

## Verification

`tests/unit/test_ci_hosted_environment_contract.py` parses both workflows and
pins the image, startup limits, and every Linux fan-out matrix.
`tests/unit/test_molecule_parallel.py` independently proves that the Build
Molecule job remains parallel while staying below its total shard count. The
broader workflow YAML, action-pin, timeout, and pre-commit suites verify valid
syntax, independent results, and the existing release gates.
