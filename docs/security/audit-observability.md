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
