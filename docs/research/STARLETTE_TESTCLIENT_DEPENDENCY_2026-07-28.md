# Starlette TestClient Dependency Reproducibility

Date: 2026-07-28

## Finding

After restoring Gludd's real dependency floors, a clean dependency sync removed
an undeclared `httpx2` installation. Importing `starlette.testclient` then
emitted:

```text
StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is
deprecated; install `httpx2` instead.
```

The warning had been hidden by residue in the long-lived local virtual
environment. It was a clean-install and CI reproducibility defect, not a warning
to suppress.

## Upstream and user evidence

- Starlette's current
  [TestClient documentation](https://www.starlette.io/testclient/) says the
  client is built on `httpx2`; plain `httpx` remains temporarily supported but
  is deprecated.
- Starlette
  [1.2.0 and 1.3.0 release notes](https://www.starlette.io/release-notes/)
  record the `httpx2` TestClient support and inclusion in the `full` extra.
- The
  [verified PyPI project](https://pypi.org/project/httpx2/) identifies Pydantic
  as the owner and links the upstream source and provenance attestations.
- A long-running
  [Starlette user discussion](https://github.com/encode/starlette/discussions/2594)
  documents TestClient event-loop/lifespan surprises and the need to use the
  supported client lifecycle correctly.
- A practitioner
  [supply-chain analysis](https://scalefactory.com/one-tool-call-away-from-a-supply-chain-breach/)
  uses this exact warning to show why agents must verify the official package
  owner instead of installing a similarly named package from untrusted advice.

## Repair and guardrails

1. Declare `httpx2>=2.7.0` in both the PEP 621 `dev` extra and uv's default
   `dev` dependency group.
2. Regenerate `uv.lock`; the resolver currently selects `httpx2` 2.9.1 with
   registry hashes and source-distribution provenance recorded in the lock.
3. Test the committed manifest for both accidental Gludd-version dependency
   floors and the required Starlette TestClient backend.
4. Treat a clean `make relock` followed by a warning-free focused test as the
   reproducibility check; installed-but-undeclared packages are not evidence.
