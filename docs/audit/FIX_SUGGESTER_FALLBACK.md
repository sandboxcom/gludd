# Fix-Suggester Fallback Contract

The deployment fix suggester treats the model as an optional additive hint. A
gateway response with empty content, a `None` response, invalid JSON, a
non-object JSON value, or an exception is normalized to `{}`. `FixSuggester`
then merges every finding's deterministic `remediate()` patch, so a model
outage cannot remove the guaranteed safety fix.

The E2E regression workflow is deterministic and offline:

```text
make test-specific TESTFILE=tests/e2e/test_fix_suggester_fallback_workflows.py
```

It covers gateway empty/`None`/raising responses and an injected suggestion
callable that raises. Each case asserts the exact remediation patch and verifies
that the unsafe deployment value is reduced.
