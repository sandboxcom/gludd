# Project-Type Registry Compatibility

## Scope

The software generator's canonical registry stores frozen ProjectType values.
Earlier integrations consumed a dictionary-shaped PROJECT_TYPES registry,
called list_project_types(), and registered definitions with a type ID plus a
dictionary. Both generations must coexist while callers migrate.

## Compatibility invariants

- PROJECT_TYPE_REGISTRY is the only mutable source of truth.
- PROJECT_TYPES is a read-only live Mapping view. It serializes current typed
  values on access, so no second registry can drift or require reconciliation.
- Legacy prompt keys and the {description} placeholder are translated at the
  view boundary; the canonical typed templates retain {context}.
- list_project_types() delegates to the sorted canonical ID inventory.
- register_project_type() accepts either a frozen ProjectType or the former
  two-argument form. Legacy input is completely validated and converted before
  the canonical registry is mutated.
- Mismatched IDs, malformed fields, empty role sets, and invalid templates fail
  closed without a partial registration.
- get_project_type() remains typed and raises KeyError for unknown or empty IDs.
  Compatibility does not restore the ambiguous None result.

## Practitioner evidence

The importlib_metadata issue
[#409](https://github.com/python/importlib_metadata/issues/409) records a 2022
break when a registry-like API changed from dict-like to list-like and
downstream projects depended on .items(). The report has multiple user
reactions and explicitly names a real consumer that broke. Pydantic issue
[#5792](https://github.com/pydantic/pydantic/issues/5792) asks for compatibility
shims during a typed API migration so dependencies can move at different
speeds; maintainers describe preserving the old namespace while behavioral
differences migrate deliberately. These are direct precedents for a live
adapter over one canonical registry instead of a flag-day replacement or two
independently mutable stores.

## Zero-downtime rollout

The adapter is additive and requires no registry rebuild. Existing typed
callers continue using the same objects, while old callers see a dictionary
projection of those same entries. Registration constructs and validates the
replacement off to the side, then performs one dictionary assignment; readers
therefore observe either the prior complete definition or the new complete
definition. Rollback removes the adapter surface without rewriting canonical
entries.

## Security and observability

Unknown typed lookups and malformed registrations raise bounded exceptions.
No dynamic import or generated code runs during compatibility conversion.
Operators can compare available_type_ids(), list_project_types(), and the keys
of PROJECT_TYPES; all three must remain identical and sorted where promised.
Tests pin that parity, prompt translation, role validity, overwrite behavior,
and non-mutation on rejected input.

## Verification

The combined current and compatibility suites cover 127 cases and measure the
registry source with branch coverage. Release promotion still requires the
project's 85% aggregate threshold and at least 75% in every source file.
