# Pricing source module split

The pricing-source implementation is separated by responsibility while
`general_ludd.pricing_intel.sources` remains the compatibility facade. The
facade explicitly re-exports the original classes, protocol, errors, helpers,
registry functions, and historically imported table constants. No consumer
migration is required.

## Module boundaries

| Module | Responsibility | Physical lines |
| --- | --- | ---: |
| `pricing_intel/sources.py` | Stable compatibility facade | 103 |
| `source_components/base.py` | Protocol, errors, shared response validation | 80 |
| `source_components/model_apis.py` | Model API and hosted-inference sources | 979 |
| `source_components/cloud_compute.py` | RunPod, Lambda Labs, AWS, and GCP sources | 1,286 |
| `source_components/cache.py` | TTL cache and static fallback | 163 |
| `source_components/registry.py` | Ordered registry and staleness labels | 78 |

Every implementation file and the facade are below the split-plan's 2,000-line
maintenance target and therefore have substantial headroom beneath the
repository's hard 2,500-line limit.

## Compatibility invariants

- Facade exports are the implementation objects themselves, not wrappers or
  duplicate class definitions. Object identity and signatures therefore remain
  stable across both import paths.
- The 18-entry registry order is pinned because the catalog uses first-match
  semantics for provider slugs.
- Component modules never import the facade, keeping the dependency graph
  one-way and preventing a facade/component cycle.
- Fresh subprocess tests import the facade before the components and the
  components before the facade, then verify identical exports in both orders.
- Existing tests patch `general_ludd.pricing_intel.sources.httpx.Client`.
  The facade deliberately re-exports the shared `httpx` module object, and a
  functional test proves that this historic patch target still controls an
  extracted provider's network call.
- Invalid OpenRouter JSON, malformed response contracts, malformed model
  entries, non-finite prices, and invalid optional context windows remain
  fail-closed or fail-soft exactly as before the extraction.

## Practitioner reports and design response

Long-lived testing discussions show why a mechanical file move is not enough:

- In pytest's [environment-at-import-time discussion #10027][pytest-10027],
  users report that a value copied with `from module import value` can require
  patching every lookup site and that fixture-time patching may already be too
  late. The split avoids copied HTTP-client attributes: every implementation
  and the facade refer to the same imported `httpx` module object, with a
  regression test at the old lookup path.
- In pytest's [module-initialization discussion #9922][pytest-9922], users
  describe collection-time cost and side effects caused by importing modules;
  maintainers recommend removing non-trivial import-time work. The extracted
  modules perform no network or provider-client creation during import, and
  cold-process tests exercise both import orders.
- Python's [mock documentation][mock-where] explains that a patch must target
  the namespace where the dependency is looked up. This is why the old facade
  path is tested by behavior rather than merely asserted to exist.
- Python's [import FAQ][python-import-faq] warns that `from module import
  object` can retain an older object identity after reload. Explicit aliases
  and identity checks prevent the split from silently defining parallel class
  objects.

These reports have been active since 2021-2022 and describe compatibility
failures that remain inherent to Python's import and patch lookup rules. The
facade, one-way import graph, and cold-import identity matrix directly guard
against those failure modes.

## Verification and rollback

The warning-strict pricing suite covers all static and live providers, cache
fallbacks, catalogs, routers, registry ordering, private compatibility imports,
and the historic HTTP patch seam. Branch-aware coverage is 91% aggregate; each
measured file is at least 75% (base 100%, cache 93%, cloud compute 87%, model
APIs 93%, registry 100%, facade 100%).

Rollback is a single revert of the facade and component extraction. There is
no schema, cache format, provider order, or runtime state migration.

[mock-where]: https://docs.python.org/3/library/unittest.mock.html#where-to-patch
[pytest-10027]: https://github.com/pytest-dev/pytest/discussions/10027
[pytest-9922]: https://github.com/pytest-dev/pytest/discussions/9922
[python-import-faq]: https://docs.python.org/3/faq/programming.html#when-i-edit-an-imported-module-and-reimport-it-the-changes-don-t-show-up-why-does-this-happen
