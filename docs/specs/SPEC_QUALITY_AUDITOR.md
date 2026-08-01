# Behavioral spec quality auditor

Gludd's behavioral-spec gate validates the structure and acceptance contract of
each `AA` and `AB` entry without treating unrelated prose as part of the final
entry. A spec passes only when it has a non-empty body, no known filler or vague
language, a named implemented enforcement mechanism, and either a quantitative
limit or a deterministic observable verdict.

## Parsing and enforcement contract

- All typed spec headings delimit entries, even when the auditor is currently
  selecting only the `AA` and `AB` families. This prevents the last selected
  entry from absorbing later spec families and producing distant false results.
- `**Enforcement:**` is parsed as a field. A concrete file, backticked target,
  Makefile guard, plugin, hook, workflow, or prerequisite counts; `planned`,
  `future`, `TBD`, and similar placeholders do not.
- `**Behavior:**` is parsed separately. Numeric limits and exact mechanical
  outcomes such as block, deny, reject, record, classify, restore, or verify are
  measurable. Advisory phrases remain violations.
- The global quality-ratio check and per-entry audit use the same heading and
  enforcement semantics. A repository-level regression test evaluates every
  tracked `AA`/`AB` entry, alongside negative tests for vague prose.
- No inline ignore, baseline, or suppression is used. Violations must be fixed in
  the spec or in a demonstrably incorrect auditor rule.

## Upstream evidence and operator reports

Markdownlint parses Markdown before applying rules and offers parser support to
custom rules. That design supports Gludd's decision to recognize structured
fields and entry boundaries instead of scanning an unbounded text tail:
[markdownlint custom-rule documentation](https://github.com/DavidAnson/markdownlint).

A markdownlint user report opened in 2016 and discussed through 2020 shows how a
rule can report valid ordered-list content when its inferred scope or configured
style does not match the document. Gludd pins the equivalent boundary case in a
repository-level regression test rather than adding an exception:
[markdownlint issue #45](https://github.com/DavidAnson/markdownlint/issues/45).

Vale operators similarly reported that a global vocabulary exception could fix
one spelling check while creating capitalization errors elsewhere. The six-year
thread reinforces keeping field semantics local and testable instead of growing
global exception lists:
[Vale issue #213](https://github.com/errata-ai/vale/issues/213).

## Acceptance

The following must all pass:

```text
make lint-specs
make audit-spec-entry
make check-spec-quality-ratio
make test-files TESTFILES='tests/unit/test_spec_quality_auditors.py'
```

Expected repository results are zero lint violations, every parsed `AA`/`AB`
entry passing the quality gate, and at least 90% concrete enforcement coverage.
