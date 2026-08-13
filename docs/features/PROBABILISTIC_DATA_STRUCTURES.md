# Probabilistic Data Structures

## Cuckoo filter contract

The Cuckoo filter is a bounded approximate-membership multiset. Unique
fingerprints occupy table slots; repeated insertion of the same item increases
its logical multiplicity without consuming another slot. Removing a duplicate
decrements that multiplicity and removes the fingerprint only after the last
logical entry is removed.

Insertion is transactional. If the relocation budget is exhausted, the table is
restored byte-for-byte and the operation returns `False`; a reported failure
must never introduce a false negative for an item that was already present.
Load factor measures occupied physical slots, while `size` measures logical
entries.

Serialized state preserves capacity, bucket geometry, fingerprint width, logical
size, configured error rate, and packed table bytes. Deserialization rejects
truncated or length-inconsistent payloads before reading packed entries.

## Practitioner evidence

Long-lived reports in the reference implementation show why these invariants
need direct regression coverage:

- [Issue #34](https://github.com/efficient/cuckoofilter/issues/34) reports false
  negatives for several packed fingerprint widths.
- [Issue #43](https://github.com/efficient/cuckoofilter/issues/43) reports a
  measured false-positive rate materially above the requested rate.
- [Issue #28](https://github.com/efficient/cuckoofilter/issues/28) asks for a
  durable serialization/deserialization contract.

Gludd therefore sizes fingerprints using bucket width and requested error rate,
tests packed widths through the public API, rolls failed relocation back, and
round-trips configuration as well as table bytes.
