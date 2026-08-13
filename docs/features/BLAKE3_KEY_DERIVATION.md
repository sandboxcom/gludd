# BLAKE3 Key Derivation Contract

## Purpose

Gludd exposes BLAKE3 hashing, keyed hashing, and context-separated key
derivation through one small wrapper. Key derivation must use the upstream
library's dedicated derivation mode; it must never combine a keyed-hash key
with a derivation context.

## Behavioral contract

- `derive_key(context, key_material, out_len)` supplies the context only as
  `derive_key_context` and streams the key material as input.
- A missing context fails closed with `ValueError`.
- Context and material changes produce distinct outputs.
- Requested XOF lengths are preserved and repeated calls are deterministic.
- Hashing and keyed hashing remain separate modes.

The BLAKE3 specification describes derivation as two domain-separated stages:
hash the context, then hash key material under the derived context key. It is
not keyed-hash mode with an additional context:
https://github.com/BLAKE3-team/BLAKE3-specs/blob/master/blake3.tex

## Practitioner evidence

A compatibility report by BLAKE3 maintainer Jack O'Connor ran the Python
package tests against another implementation. Its explicit
`test_key_context_incompatible` case and derivation-vector usage show that
`key` and `derive_key_context` are mutually exclusive API modes:
https://gist.github.com/oconnor663/533048580b1c0f4a01d1d55f57f92792

This is a long-lived integration hazard because wrappers often try to map the
two-stage specification onto constructor parameters and accidentally supply
both mutually exclusive options.

## Zero-downtime delivery

The repair changes only constructor selection inside the derivation branch.
There is no storage migration, process restart, wire-format change, or altered
hash/keyed-hash path. Existing callers continue using the same public
signature, so the change can roll forward and roll back without downtime.

## Verification

`tests/unit/test_blake3_deep.py` exercises hashing, keyed hashing, derivation,
incremental operation, and XOF lengths. The focused suite passes 54 tests and
measures 93.75% branch-aware coverage for
`src/general_ludd/algorithms/blake3.py`, above the project aggregate and
per-file floors.
