# GitHub Issue Number Write-Back Contract

Status: S83.129

## Problem

The GitHub issue adapter receives `external_id` from imported or persisted issue
records. Interpolating that value directly into `/repos/{owner}/{repo}/issues/...`
allowed separators, dot segments, signs, whitespace, Unicode digits, and nested
resource suffixes to change the intended request path.

GitHub's maintained REST contract defines `issue_number` as a required integer for
both issue updates and label operations. The adapter therefore treats its string
representation as a domain identifier, not as a general URL segment.

## Decision

- Accept only a canonical positive ASCII decimal: `[1-9][0-9]{0,19}`.
- Do not trim, coerce, normalize, or percent-encode invalid identifiers.
- Validate before headers, credentials, or the injectable transport are touched.
- Reuse the validated value for every write-back path.
- Keep reads unchanged: GitHub's numeric `number` field remains the canonical
  imported external identifier.

The 20-digit ceiling bounds path work while covering the complete unsigned 64-bit
decimal range. This is a small domain validator; adding a URL parser would obscure
the stronger integer contract and duplicate maintained HTTP machinery.

## Practitioner evidence

- [GitHub REST issues documentation](https://docs.github.com/en/rest/issues/issues)
  specifies `issue_number` as an integer path parameter for issue updates.
- [urllib3 issue #1790](https://github.com/urllib3/urllib3/issues/1790) is a
  long-lived user report showing that dot-segment normalization can change a
  request path in surprising ways. Rejecting invalid domain identifiers before
  URL construction avoids depending on client-specific normalization.
- [Echo issue #1974](https://github.com/labstack/echo/issues/1974) documents a
  multi-year path-segment escaping ambiguity where encoded slashes can become
  separators. The adapter avoids this class by admitting digits only.

## Security, resources, and observability

Validation is local, deterministic, linear in at most 20 characters, and performs
no network or credential work on failure. Callers receive a stable `ValueError`
instead of an ambiguous remote 404 or a request to a different subresource. Tests
assert that rejected identifiers leave the transport call log empty.

## Zero-downtime delivery and rollback

The change needs no schema, configuration, token, or wire-format migration.
Existing canonical identifiers continue to work during a rolling deployment;
malformed persisted records fail closed until corrected. Promote after the
focused write-back suite, branch-coverage gate, static checks, and project gate are
green. Rollback is the single feature commit; no data repair or service downtime is
required.
