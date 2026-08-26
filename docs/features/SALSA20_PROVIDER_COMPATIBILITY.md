# Salsa20 Provider Counter Compatibility

## Purpose

Gludd delegates Salsa20 encryption to PyCryptodome while retaining a public
block-counter offset for deterministic stream partitioning. PyCryptodome's
Salsa20 object is stateful but does not expose the `seek` method available on
its ChaCha20 object. Calling that nonexistent method breaks every block helper
and every nonzero-counter stream operation.

## Behavioral contract

- Encryption and decryption remain backed by PyCryptodome.
- Counter zero begins at the first 64-byte Salsa20 block.
- Counter `n` advances exactly `n * 64` bytes before processing output.
- Negative counters fail closed with `Salsa20Error`.
- Advancement discards provider output in fixed 64 KiB chunks, bounding
  temporary memory independently of the requested offset.
- Tests assert length, provider vectors, and decrypt round trips. They do not
  require every ciphertext byte to differ from its plaintext byte: Salsa20
  combines plaintext and keystream with XOR, so a zero keystream byte legally
  leaves the corresponding plaintext byte unchanged.
- Key and nonce validation, HSalsa20, and XSalsa20 behavior remain unchanged.

The provider's Salsa20 implementation exposes `encrypt` and `decrypt`, but
no `seek`; its ChaCha20 implementation separately defines a seek API:
https://github.com/Legrandin/pycryptodome/blob/master/lib/Crypto/Cipher/Salsa20.py
https://github.com/Legrandin/pycryptodome/blob/master/lib/Crypto/Cipher/ChaCha20.py

## Practitioner evidence

PyCryptodome issue #399 records a long-lived interoperability failure caused by
counter and nonce assumptions. Users reported that locally reversible streams
still disagreed across implementations until counter construction and
endianness were made explicit:
https://github.com/Legrandin/pycryptodome/issues/399

That report supports keeping Gludd's counter semantics explicit and tested
instead of assuming all stream-cipher provider objects share one seek API.

On 2026-08-26, hosted Python 3.11 run `32934741442` exposed a second
long-lived test assumption: a random one-byte encryption produced `b"A"` from
plaintext `b"A"`, which is valid when that keystream byte is zero. PyCryptodome
documents Salsa20 as a byte-oriented stream cipher, and practitioner discussions
likewise describe Salsa20 encryption as plaintext XOR keystream:

- https://www.pycryptodome.org/src/cipher/salsa20
- https://www.reddit.com/r/crypto/comments/jqb8yp/question_about_noncecounter_in_salsa20/
- https://crypto.stackexchange.com/questions/71615/append-data-to-authenticated-ciphertext-encrypted-using-a-stream-cipher

The regression now injects an explicit zero-keystream provider result and proves
both the legal byte equality and decrypt round trip without probabilistic input.

## Zero-downtime delivery

The public function signatures and ciphertext semantics do not change.
Existing counter-zero callers retain the provider's direct fast path.
Nonzero-counter calls now advance the same provider state in bounded chunks.
There is no persisted state or migration, so mixed-version workers can roll
forward and back without downtime; callers should not split one ciphertext
between versions during a single operation.

## Verification

`tests/unit/test_salsa20_deep.py` covers block generation, counter separation,
zero-keystream byte equality, round trips, HSalsa20, XSalsa20, and validation.
The production implementation is unchanged by the hosted-test correction; the
existing branch-aware coverage floor remains enforced for
`src/general_ludd/algorithms/salsa20.py`.
