# BitArray Index and Serialization Contract

## Purpose

Gludd's `BitArray` indexes bit zero first: list element zero, integer bit zero,
and byte bit zero all map to array index zero. Its binary-string representation
is therefore an index-order string, not an MSB-first numeral. Construction and
serialization must use that same order.

## Behavioral contract

- `BitArray([a, b, ...])[0]` returns `a`.
- `from_int()`, `to_int()`, `from_bytes()`, and `to_bytes()` retain
  little-endian bit indexing.
- `from_binary_string(s).to_binary_string()` returns `s` exactly.
- Bitwise AND, OR, XOR, and inversion follow Boolean truth tables.
- Non-mutating bitwise operators allocate independent results; later operand
  mutation cannot change the result.

The corrected independence assertion retains `True` for
`True OR False`. Its former `False` expectation contradicted the adjacent OR
truth-table test and standard Boolean semantics; changing it is contract
conformance, not a weakened test.

## Practitioner evidence

A long-lived Stack Overflow discussion shows how converting a little-endian
bit array through an MSB-first string changes the interpreted integer, and
points users to explicit conversion APIs:
https://stackoverflow.com/questions/42464514/how-to-convert-bitarray-to-an-integer-in-python

Bitstring issue #156 documents the wider practitioner confusion between
LSB-first indexing and MSB-first properties. Maintainers note that changing
index order also changes integer and related semantics:
https://github.com/scott-griffiths/bitstring/issues/156

These reports support one explicit index-order contract across constructors,
operators, and serializers rather than implicit reversal at one boundary.

## Zero-downtime delivery

No Gludd production path persists or transmits `from_binary_string()` output;
the method is currently confined to the library and its tests. The repair can
therefore roll across workers without a data migration. External callers that
intend an MSB-first numeric literal should parse it as an integer and use
`from_int()`; index-order strings now round-trip consistently on every
version after rollout.

## Verification

`tests/unit/test_bitarray_deep.py` covers construction, indexing, mutation,
bitwise truth tables, independence, integer/byte/string round trips, ranges,
resize, and edge cases. The focused suite passes 58 tests with 90.43%
branch-aware coverage for `src/general_ludd/bitarray.py`.
