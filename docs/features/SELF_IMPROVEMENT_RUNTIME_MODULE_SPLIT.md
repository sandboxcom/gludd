# Self-improvement runtime module split

## Outcome

`general_ludd.self_improve.runtime` remains the installed compatibility facade
and CLI entry point. Local proposal decoding and compact-v4 syntax repair now
live in `runtime_local_proposals.py`; pure Make-input and evidence validators
live in `runtime_validation.py`. The files contain 2,485, 948, and 45 lines,
respectively, so every component stays below the repository's 2,500-line hard
limit.

The dependency direction is one way. The implementation modules do not import
the facade. At each proposal call, the facade constructs immutable hooks from
its current globals and supplies them to the extracted implementation. This
keeps the existing facade patch points live without creating an import cycle or
storing process-global callback state.

## Compatibility contract

The split preserves these observable boundaries:

- `generate_local_proposal()` and `generate_local_proposal_plan()` retain their
  module, names, signatures, return values, and pickle paths.
- `MakeResult` remains defined in the facade, so existing serialized instances
  still resolve through `general_ludd.self_improve.runtime.MakeResult`.
- Imported proposal helpers used by callers remain facade attributes by object
  identity, including `merge_proposal_manifests()` and the compact syntax-span
  provenance helper.
- `generate_local_proposal_plan()` still resolves
  `_generate_local_proposal_plan_result` on the facade at call time. Tests and
  embedders can therefore patch the established seam.
- Worker requests, progress events, syntax diagnostics, prompt tests, and
  selected-line rendering are injected from the facade for each call. A patch
  applied after import is the dependency the implementation actually invokes.
- No persistent proposal, retry, envelope, event, or error format changed.

## Long-lived upstream and user reports

Research on 2026-10-06 found three recurring refactor hazards that shaped the
design:

1. A Stack Overflow question open since 2010 documents unpickling failures when
   a class is no longer importable from the module path recorded in its pickle.
   That is why `MakeResult` stays defined in the facade and why the regression
   test round-trips both the value object and the public functions through
   `pickle`.
   [AttributeError when unpickling an object](https://stackoverflow.com/questions/3614379/attributeerror-when-unpickling-an-object)
2. Pytest issue #2229, opened in 2017, shows that replacing one module attribute
   does not update a name previously copied by `from module import name`. The
   facade therefore builds hooks from its live attributes per invocation rather
   than copying patch-sensitive dependencies into the extracted module.
   [pytest issue #2229](https://github.com/pytest-dev/pytest/issues/2229)
3. Pytest's maintained monkeypatch guidance says to patch the reference used by
   the system under test and recommends explicit dependency injection for code
   under the application's control. `ProposalRuntimeHooks` applies both lessons:
   established facade references remain patchable, while the extracted module
   receives explicit dependencies.
   [pytest monkeypatch guidance](https://docs.pytest.org/en/stable/how-to/monkeypatch.html)

These reports describe stable Python lookup semantics, not a version-specific
workaround. Import, signature, monkeypatch, and pickle compatibility are
therefore automated boundaries of the split.

## Zero-downtime delivery and rollback

This is a source-only packaging refactor. It adds no database migration, file
format, daemon protocol, process, port, cache, lease, or external resource.
Existing workers keep their already imported facade while replacement workers
load the split package, so a rolling deployment does not require a coordinated
restart or interrupt an in-flight proposal. Proposal hooks are immutable and
call-local; concurrent requests cannot replace one another's dependencies.

Rollback selects the preceding application artifact or reverts the split
commits. No data reversal or cleanup is required because persistent and wire
formats are unchanged. The compatibility test supplies rollback evidence by
pinning old import paths, signatures, monkeypatch lookup, and pickle round
trips. The retained-worker suite exercises syntax repair, frozen-shard reuse,
worker failure cleanup, timeout cleanup, and process-group reaping through the
new boundary.

## Verification evidence

- TDD red: the split contract first failed two of four tests because the
  component did not exist and the facade exceeded 2,500 lines.
- Compatibility and focused behavior: the expanded split contract passes
  12/12, including pickle identity, live facade patch lookup, one-way imports,
  provenance drift, absent baselines, repair retargeting, and managed-decoder
  boundaries. The branch-aware runtime replay passes 312/312 after restoring
  every historical facade import seam.
- Scoped Ruff and strict mypy pass for the facade, both extracted modules, and
  the split contract.
- Branch-aware coverage is 93.0% lines and 85.25% branches in aggregate. The
  facade is 91.0% lines and 80.28% branches, local proposal orchestration is
  98.99% lines and 97.17% branches, and validation is 100% for both. Every
  owned source file therefore exceeds the 75% per-file floor.
