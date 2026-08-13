# Comprehensive feature-spec inventory

`make feature-spec-inventory FORMAT=human` is the single reporting command for
the project's documented specifications. It answers two different questions
without conflating them:

1. Which Gludd enhancement and feature specifications are documented, claimed
   complete, and supported by implementation/test evidence?
2. How many OpenCode behavioral specifications are documented, claim an
   enforcement mechanism, name a specific rather than template mechanism, can
   be statically resolved to an enforcement artifact, or have recurrence
   evidence showing that they are ineffective?

Use `make feature-spec-inventory FORMAT=json` for the complete machine-readable
record set, including canonical IDs, aliases, every source reference, evidence
paths, file-level scan coverage, and the exact rules used by the scanner.

## Inclusion and deduplication

The scanner recursively examines every `.md`, `.yml`, `.yaml`, and `.json` file
under `docs/`; `docs/features.yml` is an input, not an allow-list. It recognizes:

- explicit feature/work-item IDs and numeric ranges such as `NF.1`, `D1`, and
  `F.1-F.4`;
- implementation-plan phase rows such as `P1` through `P6`;
- embedded specification sections in broader plans and roadmaps;
- YAML/JSON `features` and `specs` collections;
- the JSON-schema and YAML contracts for MCP tool capabilities; and
- document-level feature, design, architecture, roadmap, system, and
  specification files with normative implementation markers.

Global IDs are normalized to a single key (`NF.1` and `nf1` become `nf1`).
Local phase IDs are namespaced to their parent specification (`nf1:p1`) so a
`P1` in two independent feature documents cannot collide. Documents without an
explicit ID are matched to a manifest entry only when title-token overlap is
unambiguous; otherwise their repository-relative document slug is the key.
Ranges are expanded into atomic IDs. All aliases retain their source path and
line in JSON output.

OpenCode-, enforcement-, and guardrail-only documents are excluded from the
Gludd feature total. They are not silently discarded: the canonical
`docs/specs/BEHAVIORAL_SPECS.md` source is reported in a separate section with
core (multi-letter IDs) and generated (single-letter IDs) totals.

## Status semantics

The report keeps four layers distinct:

- **Documented** means a source matched the specification grammar.
- **Claimed** is an explicit status field or manifest status/percentage. A
  percentage remains only a claim.
- **Enforced** applies only to behavioral specs with a non-empty, specific
  enforcement field; long/generic fields are reported as templates.
- **Verified** requires resolvable repository evidence. Gludd features need
  implementation and test references for `implemented`; code-only evidence is
  `partial`. Behavioral enforcement is statically verified only when the named
  mechanism and any named test resolve.

Conflicting alias statuses fail closed to `unknown`; the report never chooses
the most optimistic prose. Absence of evidence is also `unknown`, not proof
that a feature is unimplemented.

Core behavioral verification is restricted to multi-letter IDs. A resolvable
single-letter generated mechanism must never inflate the core verified count or
drive the core missing count below zero. This preserves the source separation
described above while still reporting generated enforcement quality separately.

## Source coverage

Human output reports how many documentation files were included, explicitly
excluded, unrecognized, or failed to parse. The four disposition counts always
sum to the scanned-file total. JSON mode identifies every file and reason, so a
new documentation format cannot silently disappear from the inventory.

## Long-lived issue evidence from user forums

The scanner's design responds to recurring problems described by developers:

- In a [2024 r/programming discussion about architecture
  documentation](https://www.reddit.com/r/programming/comments/1dkffmw/documenting_software_architectures/),
  users called out stale/incorrect documents and multiple asynchronous sources
  of truth; keeping parseable Markdown beside code was suggested as a practical
  mitigation. This inventory therefore scans the repository instead of a
  hand-picked list.
- In a [2023 r/programming discussion about documentation
  maintenance](https://www.reddit.com/r/programming/comments/146vt0t/proper_documentation/),
  users emphasized that documentation itself must be maintained and suggested
  release-time review plus change-aware links to code. This inventory retains
  source/evidence links and separates claims from what can be resolved.
- A [Product Management forum thread about product
  logic](https://www.reddit.com/r/ProductManagement/comments/11rrc9k/do_you_have_an_up_to_date_source_of_truth_of_your/)
  describes feature requirements spread across several tools becoming
  unsearchable and stale. The file-level coverage ledger and canonical alias
  keys are intended to make that failure visible rather than hiding it behind a
  curated total.
- A long-running [mypy practitioner report about optional `TypedDict`
  fields](https://github.com/python/mypy/issues/7993) records the common
  confusion between a key whose value may be `None` and a key that may be
  absent. The inventory's supporting audits therefore declare required record
  keys explicitly instead of erasing those distinctions behind a bare `dict`.
- In [mypy issue #10471](https://github.com/python/mypy/issues/10471),
  maintainers explain that an explicit mapping type supplies the inference
  context that changes how a dictionary literal is checked. Named record and
  statistics schemas now provide that context at each parser/audit boundary.

## Typed audit records and zero-downtime delivery

The effectiveness, enforcement-coverage, and generator helpers exchange named
`TypedDict` records. Every parser-produced key remains required, while grouped
statistics retain exact integer counters and a floating-point percentage. The
inventory caller consumes the generator's named result directly, so strict
checking follows the complete in-process audit path without `Any`-shaped gaps.

This is a static contract refactor: command output, file formats, exit codes,
and generated enforcement text are unchanged. Characterization tests pin all
supported artifact resolution, threshold, dry-run, write, and generator-loop
paths before and after the annotations. Deployment needs no migration, process
restart, or compatibility window; rollback is a source revert, and concurrent
read-only inventory calls continue to share no mutable persisted state.
