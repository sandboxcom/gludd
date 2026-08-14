# HTML and XSLT Branch Reconciliation

## Scope and compatibility contract

This feature reconciles five historical heads onto development base `8094851`
without changing shared build configuration. The public surface remains additive:

| Utility | Selected source head | Reconciled contract |
|---|---|---|
| `html_css_core` | `5b7548890` | Validate bounded HTML/CSS strings, flag structural and missing-`alt` issues, extract design tokens, and detect real media rules. |
| `html_processor` | `5728fcc5c` | Parse bounded tag soup with `lxml.html`, extract visible text with a documented selector subset, extract real links, and strip tags. |
| `xslt_transformer` | `5cbfc910c` | Compile and apply bounded XSLT 1.0 with parameters, validate stylesheets, and extract template metadata. |

The utilities accept in-memory strings and perform no application-state writes.
`html_processor` intentionally skips anchors without `href`; they are named
anchors, not navigable links. Its selector grammar is limited to tag, class, ID,
compound, descendant, and direct-child forms. Unsupported syntax fails closed.

## Focused ancestry proof

The reconciliation base already contains the shared prerequisite commits:
`make git-is-ancestor A=979dfff6c B=8094851` and
`make git-is-ancestor A=f5ccaa906 B=8094851` both returned `exit=0`.
Each feature tip (`5b7548890`, `5728fcc5c`, `a8bfd8537`, `5cbfc910c`, and
`df807e33d`) returned `exit=1`, so the feature changes themselves are genuinely
missing from the base.

The duplicate patches are not equivalent. Focused
`make git-patch-equivalence ... PATCH_LIMIT=5` checks returned
`patch-equivalent=0` for both `agent-html-processor` versus
`agent-x14-html-proc` and `agent-xslt-transformer` versus `agent-x13-xslt`.
The following tips are therefore recorded as superseded ancestry, not imported
as competing content:

- `a8bfd8537` is superseded by `5728fcc5c`: the selected HTML implementation
  supports class/ID/compound selectors, skips non-link anchors, and includes the
  larger behavioral test set.
- `df807e33d` is superseded by `5cbfc910c`: the selected XSLT implementation
  exposes named template, mode, and all `value-of` selections. Reconciliation
  removes the competing custom partial XSLT engine and delegates transformation
  semantics to libxslt.

All five tip hashes must remain visible as merge parents in the reconciliation
branch so future inventory treats them as reviewed rather than abandoned.

## Mature OSS and long-lived user reports

The XML collection already documents `lxml` as required for HTML tag-soup and
XSLT 1.0, and the `xml-security` project extra already pins `lxml>=5.0.0`.
Accordingly, this feature uses `lxml.html` and `lxml.etree.XSLT`; it does not
maintain a second parser or XSLT interpreter.

The security defaults require explicit tightening. The
[lxml XSLT access-control documentation](https://lxml.de/api/lxml.etree.XSLTAccessControl-class.html)
states that file and network permissions default to allowed, so transforms use
`XSLTAccessControl.DENY_ALL`. The
[lxml resolver documentation](https://lxml.de/5.1/resolvers.html) explains that
parsers and transformations can load referenced resources; parsers here disable
entity resolution, DTD loading, network access, and `huge_tree`. The
[lxml FAQ](https://lxml.de/4.9/FAQ.html) notes that libxml2's hard limits can
still be high for a service, which is why these wrappers also enforce explicit
input and output character limits.

Two durable user-forum threads informed compatibility decisions:

- A [2014 Stack Overflow cssselect report](https://stackoverflow.com/questions/23221073/how-to-fix-issue-with-the-removed-cssselect-package-in-lxml)
  documents the long-running surprise that lxml CSS selection needs a separate
  package. Because `cssselect` is not in this repository's lock, this change
  exposes a deliberately small validated selector subset instead of adding an
  undeclared runtime dependency.
- A [2017 Stack Overflow XSLT file-access discussion](https://stackoverflow.com/questions/42863376/xml-not-formatted-when-in-a-different-path-of-xsl-file)
  records the longstanding local-file disclosure risk around stylesheet
  processing. Local and network reads and writes are denied here, including
  `document()` and extension output.

## Security and resource invariants

- XML and stylesheet parsers use `resolve_entities=False`, `load_dtd=False`,
  `no_network=True`, `huge_tree=False`, and strict recovery settings.
- XSLT compilation uses `XSLTAccessControl.DENY_ALL`; caller parameter values
  are encoded with `XSLT.strparam`, and parameter names are validated.
- HTML parsing uses the mature lxml HTML parser with network access and
  `huge_tree` disabled. Selector syntax is validated before XPath generation.
- HTML, CSS, XML, and XSLT inputs are bounded at one million characters. XSLT
  output is bounded at four million characters. Oversized work fails closed.
- Text and link extraction omit `script` and `style` payloads. CSS comments and
  strings do not create false media-query or design-token findings.
- The utilities launch no processes, open no sockets, and create no runtime
  state. Tests use pytest-owned temporary paths for denied-access probes.

## ZDD, rollout, and rollback

This is a zero-downtime additive change: new module-utils and tests land without
altering a daemon, schema, route, Make target, dependency lock, or existing
callsite. Existing workers can continue using the old code until their normal
deployment replacement; mixed-version workers share no mutable state. Rollout
is development-first, then the normal green-gate promotion to master.

Rollback is a normal revert of the reconciliation commits. Because there are no
migrations, generated artifacts, background processes, or persistent writes,
rollback needs no cleanup and does not depend on deployment order. The two
superseded heads remain ancestry records only and must not be reintroduced as
fallback implementations.

## Verification evidence

The focused verification set is:

```text
tests/unit/test_html_css_core.py
tests/unit/test_html_processor.py
tests/unit/test_xslt_transformer.py
```

TDD and focused verification completed in the isolated reconciliation worktree:

- RED: `make test-files TESTFILES='tests/unit/test_html_css_core.py
  tests/unit/test_html_processor.py tests/unit/test_xslt_transformer.py'` failed
  collection because all three intended source files were absent.
- GREEN: the unchanged focused command passed 80 tests after implementation;
  the package-export coverage test then brought the final focused set to 81.
- Coverage: `make coverage-files` used the same three tests, an ephemeral
  include-only coverage configuration, `COVERAGE_AGGREGATE_MIN=85`, and
  `COVERAGE_PER_FILE_MIN=75`. It passed 81 tests with 93% aggregate line/branch
  coverage. Per-file results were 100% for the package initializer, 94% for
  `html_css_core.py`, 88% for `html_processor.py`, and 96% for
  `xslt_transformer.py`.
- Static checks: focused `make lint-files` reported no findings and
  `make typecheck-scope` reported no issues across all seven Python source/test
  files. Focused `make lint-markdown` reported zero issues for this document.
- Resources: `make check-system-load` reported a 0.32 load/CPU ratio (`OK`),
  and `make disk-check` reported 90% disk use against a 95% stop threshold.

No dependency manifest or lock changed. The isolated worktree restored the
already-locked Markdown toolchain only to execute its focused documentation
check.
