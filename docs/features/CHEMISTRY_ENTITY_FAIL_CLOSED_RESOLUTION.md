# Fail-Closed Chemistry Entity Resolution

## Problem and contract

Chemistry resolution already used an unknown structure with empty `value` and
`submitted` fields as its typed failure result. The structure schema rejected
that result before its validation warning could be returned. At the same time,
the lightweight status adapter treated every unmarked token as a natural-isotope
structure and treated every non-explicit stereo token as unknown. Those defaults
overstated ambiguous inputs such as `CCO` and lost the partial status of an
unspecified multiple-bond structure such as `C(=O)O`.

The repaired contract is deliberately narrow:

- an unknown representation may carry the complete empty failure sentinel;
- an unknown representation with only one empty text field is rejected;
- every concrete representation requires non-blank canonical and submitted text;
- explicit atom or bond stereo markers are `specified`, an unqualified double
  bond is `partial`, and inputs without those markers remain `unknown`;
- a leading numeric isotope in a bracket atom is `specified`; structurally
  marked or homoelemental bare inputs without it are `natural`; and ambiguous
  mixed bare tokens remain `unknown`.

These states describe evidence present in the submitted text. They do not claim
that the text is chemically valid or that every possible stereocentre or isotope
has been perceived.

## Mature primitives and practitioner evidence

[Pydantic model validators](https://docs.pydantic.dev/latest/concepts/validators/#model-validators)
are the maintained primitive for a rule that depends on multiple fields. The
schema therefore validates representation, canonical text, and submitted text as
one state instead of duplicating order-sensitive field validators. Pydantic's
[multi-field validation issue #7507](https://github.com/pydantic/pydantic/issues/7507)
and [empty-string discussion #2687](https://github.com/pydantic/pydantic/discussions/2687)
show that these ordering and empty-value boundaries have remained practical
integration concerns.

RDKit remains the mature choice for full molecular perception. Its
[stereochemistry documentation](https://www.rdkit.org/docs/RDKit_Book.html#stereochemistry)
distinguishes specified stereo from potential centres with missing information.
The long-lived practitioner record includes reports about
[preserving undefined stereochemistry from 2020](https://sourceforge.net/p/rdkit/mailman/rdkit-discuss/thread/0b87bb41de9d4c8589d2fcf9bb3027da%40uni.lu/),
[cleaning up stereochemistry from 2010](https://sourceforge.net/p/rdkit/mailman/rdkit-discuss/thread/AANLkTikEV%2Bp9_dkiBWHVgWeeB5u808nqJMLmHajEnf9Y%40mail.gmail.com/),
and [finding unassigned chiral centres from 2016](https://sourceforge.net/p/rdkit/mailman/message/35368659/).
An [isotope-handling discussion from 2011](https://sourceforge.net/p/rdkit/mailman/rdkit-discuss/thread/9DCB012E-E2FD-4951-A1AA-96B29AD33E68%40dalkescientific.com/)
similarly demonstrates that isotope interpretation belongs in a chemistry
toolkit rather than an expanding local parser.

RDKit is not part of the locked runtime for this feature, and dependency or
shared-configuration changes are outside this repair. The fallback therefore
uses only fixed marker recognition and returns `unknown` whenever that limited
evidence is insufficient. It is an adapter seam, not a custom SMILES parser;
a future RDKit-backed adapter can replace it without changing the entity schema.

## Security and resource boundaries

Unknown and malformed states fail closed. The empty sentinel is accepted only as
a paired state on the unknown representation, so it cannot disguise a concrete
structure with missing provenance. Ambiguous isotope evidence is never promoted
to `natural`, and the adapter performs no network lookup, file access, dynamic
evaluation, or mutation of a shared registry.

The compiled standard-library expressions are fixed, non-recursive scans with
linear work in the submitted text and constant auxiliary state. The repair adds
no retries, workers, background processes, caches, or persistent data. Existing
request-size and deadline enforcement remains the outer resource boundary.

## ZDD rollout, observability, and rollback

No database, wire-version, package, or configuration migration is required.
Deploy with the focused warning-strict chemistry family green, then compare
unknown, partial, natural, and specified status counts during a canary. During a
mixed-version window, keep each resolution request and its entity validation on
the same worker; an old worker cannot consume the empty sentinel that it already
rejected. New workers preserve all non-empty entity payloads unchanged.

Rollback is a source-only deployment rollback with no data reversal. It restores
the former exception for empty failure results and the former coarse statuses,
so operators should drain or pin requests that produced an empty unknown entity
before routing them to an old worker. Validation warnings and status counts are
the operational signals; the repair introduces no hidden asynchronous work.

