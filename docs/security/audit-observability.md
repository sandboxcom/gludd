# Security audit observability

`make security-audit` runs the complete local secrets, SAST, Python dependency,
Node dependency, and security-backlog audit. Every phase emits one compact JSON
`started` event, bounded `running` heartbeats, and a terminal event with elapsed
time and the real exit code. The aggregate result is written atomically to
`dist/security-audit-summary.json` by default.

The secrets phase is deliberately different: detect-secrets stdout and stderr
are never forwarded. Only phase status and timing are observable, so credential
values cannot be copied into terminal, agent, or CI logs. This follows the
[detect-secrets baseline workflow](https://github.com/Yelp/detect-secrets),
which keeps hashed findings in a baseline and uses `detect-secrets-hook` for new
findings. The wrapper does not parse or reimplement secret detection.

Normal commit and push gates run `scripts/detect_secrets_readonly.py`. It copies
the canonical baseline to a namespaced temporary file, delegates detection to
the upstream `detect-secrets-hook`, and removes the copy on both success and
failure. Non-update statuses propagate unchanged. For upstream status 3, the
wrapper compares counted filename, detector, and hashed-secret identities in
the canonical and disposable baselines. It returns success only when the update
adds no identity; a new or duplicated finding, malformed scanner output, or
operational error remains fail-closed. Line numbers and generation timestamps
are deliberately excluded because they are presentation metadata, not secret
identity. The upstream hook remains available only at the explicit `manual`
pre-commit stage for baseline maintenance. This keeps the gate zero-downtime:
scanning can reject a candidate, but it cannot mutate the candidate or force a
gate/commit/push retry. Rollback is limited to removing the wrapper and restoring
the prior hook stage; detection remains owned by the upstream scanner.

This boundary follows upstream's documented design: the hook automatically
keeps baselines current, while line numbers are presentation metadata rather
than secret identity. Long-lived practitioner reports
[#149](https://github.com/Yelp/detect-secrets/issues/149) and
[#212](https://github.com/Yelp/detect-secrets/issues/212) document the resulting
baseline rewrites and failed commit loops. Gludd therefore preserves upstream
detection but confines its intentional mutation to a disposable copy during
admission gates.

## Baseline refresh and canonical compaction

`scripts/manage_secrets_baseline.py` keeps `.secrets.baseline` as the one
supported, non-slim detect-secrets JSON document. Refresh delegates discovery
and stale-entry pruning to the pinned `detect-secrets scan --baseline` CLI. The
`--baseline` form is important: it initializes upstream's
`detect_secrets.filters.common.is_baseline_file` filter with its required
filename context. Gludd then verifies that the plugin configuration and every
scan-surviving filename/detector/fingerprint occurrence are unchanged, carries
forward surviving audit labels, sorts deterministically, and serializes compact
JSON. It never parses source text or implements a detector.

The exact exclusion regex is versioned in
`config/detect_secrets_baseline_policy.json`. It excludes the baseline itself;
the generated `uv.lock`, plugin-hash, and resource-ownership inventories; the
retired sandboxcom key paths; root Git, virtual-environment, distribution,
build, worktree, cache, dependency, and vendor directories; plus recursive
Python/cache, `node_modules`, and vendor directories. Near-name source paths
such as `build_plan.py` and `vendor-selection.md` remain scanned. A policy test
pins both sides of that boundary.

Replacement is zero-downtime: the mature scanner updates a disposable copy,
all semantic checks finish before publication, the compact candidate is flushed
and `fsync`ed in the destination directory, and one `os.replace` publishes it.
Any scanner, schema, plugin, filter, or write failure leaves the prior baseline
in place. Rollback is the inverse single-file Git revert together with the
policy/manager commit; normal admission continues to use a disposable copy
during either version.

This is JSON compaction, not upstream `--slim`: audit labels and line metadata
remain available, and `detect-secrets audit --stats` is tested against the
compact artifact. It is not an adjudication of existing findings, a claim that
excluded generated/vendor material is secret-free, or a substitute for live
verification. A dynamically assembled credential regression proves that the
upstream hook still blocks a genuinely new finding. Only counts and digests may
reach logs; neither baseline payloads nor scanner stdout/stderr are emitted.

The separation responds to the long-lived update-versus-hook confusion in
[detect-secrets issue #246](https://github.com/Yelp/detect-secrets/issues/246)
without replacing the maintained tool. Issues
[#149](https://github.com/Yelp/detect-secrets/issues/149) and
[#212](https://github.com/Yelp/detect-secrets/issues/212) remain the evidence
for keeping intentional metadata refresh out of commit/push admission.

The manager's direct, content-free behavioral equivalents are:

```console
uv run python scripts/manage_secrets_baseline.py refresh --baseline .secrets.baseline --policy config/detect_secrets_baseline_policy.json --repo-root . --executable .venv/bin/detect-secrets
uv run python scripts/manage_secrets_baseline.py check --baseline .secrets.baseline --policy config/detect_secrets_baseline_policy.json --repo-root . --executable .venv/bin/detect-secrets
```

Project automation exposes these through Make; operators do not run the direct
commands under the repository's make-only execution policy.

Bandit remains the SAST engine. Its documented
[JSON formatter](https://bandit.readthedocs.io/en/1.7.3/formatters/json.html)
feeds `scripts/summarize_sast.py`; the summary intentionally excludes source
code and issue text. The audit passes `--ignore-nosec`, so legacy suppression
comments cannot hide findings or produce repeated suppression warnings. It
contains counts grouped by severity, rule, and file. It also emits source-free
coordinates for every high- or medium-severity finding so remediation can be
automated without exposing snippets or issue text:

```json
{
  "totals": {"baseline": 10, "current": 12, "delta": 2},
  "by_rule": {"B104": {"baseline": 1, "current": 2, "delta": 1}},
  "actionable_findings": [
    {"filename": "src/server.py", "line": 42, "rule": "B104", "severity": "MEDIUM"}
  ]
}
```

Pass either a prior Bandit JSON report or a prior generated summary through
`SAST_BASELINE`. This makes changes actionable without conflating Bandit's
reporting threshold with the audit's exit policy. That separation addresses the
long-lived operator request in
[Bandit issue #696](https://github.com/PyCQA/bandit/issues/696), opened in 2021,
whose CI use case requires retaining all findings while gating at a separately
chosen threshold. Upstream's 2026
[progress-to-stderr change](https://github.com/PyCQA/bandit/pull/1422) also
confirms that scanner progress belongs on a side channel rather than inside the
machine report.

## Usage

Run the full audit with explicit operational bounds:

```console
make security-audit SECURITY_AUDIT_HEARTBEAT_SECS=15 SECURITY_AUDIT_PHASE_TIMEOUT_SECS=1800 SECURITY_AUDIT_VALIDATE_ONLY=0 SECURITY_AUDIT_SUMMARY=dist/security-audit-summary.json SAST_REPORT=dist/sast-report.json SAST_SUMMARY=dist/sast-summary.json SAST_BASELINE=config/sast-summary-baseline.json
```

Generate only the SAST comparison:

```console
make sast-summary SAST_REPORT=dist/sast-report.json SAST_SUMMARY=dist/sast-summary.json SAST_BASELINE=config/sast-summary-baseline.json
```

Heartbeats are limited to 5–300 seconds and each phase timeout to 60–7200
seconds. A timeout terminates the phase process group and records exit code 124.
All phases run even when one fails, so a single invocation yields the complete
actionable failure set. `SECURITY_AUDIT_VALIDATE_ONLY=1` exercises all telemetry
and summary-writing paths without running scanners or contacting registries.

Do not attach raw detect-secrets process output to bug reports. Share the
aggregate audit summary and the SAST summary; neither contains credential values
or source snippets.
