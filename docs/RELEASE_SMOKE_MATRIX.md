# Release Smoke Matrix

The release smoke matrix is credential-safe and fail-closed:

- Azure and RunPod dry-runs validate configuration without opening a network
  connection, creating resources, or incurring billing.
- Live provider mode rejects missing credentials before any request. Provider
  outages remain failed smoke checks.
- OpenCode's registered plugins are resolved locally, and the Node verifier
  dynamically imports each factory when Node is available.
- The verified-claims helper is executed through Node and must block a
  completion claim that reports only 84% coverage.
- Release preflight uses the canonical version helper and blocks unchecked
  `T-BETA3-*` task IDs while ignoring unrelated backlog items.

Run the focused matrix with:

```text
make test-files TESTFILES='tests/e2e/test_release_smoke_matrix.py'
```

The test suite never requires provider keys, Azure billing identifiers, RunPod
credits, or a live OpenCode model.
