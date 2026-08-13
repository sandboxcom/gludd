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

## HyperLogLog hash-domain contract

HyperLogLog accuracy depends on uniformly distributed hash bits. New legacy and
HLL++ sketches use a standard-library BLAKE2b 64-bit digest with a
class-specific personalization string. The retained FNV-1a helper continues to
match its published vectors, but its former two-pass composition is restricted
to hash domain 1 for compatibility with already serialized sketches.

Every new payload records a magic value and hash-domain version. The reader
accepts the original unversioned payload as domain 1, preserves that domain when
the sketch is updated or reserialized, and rejects unknown versions, invalid
geometry, and malformed sparse entries. Merge fails closed when two sketches
use different precision or hash domains; a plausible estimate from mixed hash
domains is not meaningful.

## HyperLogLog ZDD and practitioner evidence

During a zero-downtime rollout, both payload domains remain readable, but their
aggregates remain separate. New instances write domain 2, shadow-count a fixed
sample, expose `hash_domain_version`, and shift traffic only after accuracy and
serialization checks pass. Domain 1 can be drained or retained until its window
expires. Rollback is safe because domain 1 reading and updating remain intact;
no register migration or silent cross-domain merge occurs.

This contract responds directly to long-lived practitioner evidence:

- [ascv/HyperLogLog issue #9](https://github.com/ascv/HyperLogLog/issues/9),
  opened in 2014, reports roughly 45 percent error after adding 1,024 ordinary
  string values, closely matching Gludd's isolated low-precision failure.
- The project's later
  [2.0 migration notes](https://github.com/ascv/HyperLogLog) record a move to a
  uniformly distributed 64-bit hash and explicitly warn that old and new
  serialized sketches are incompatible.
- [java-hll's practitioner guidance](https://github.com/aggregateknowledge/java-hll)
  explains that near-uniform hash bits are essential to the estimator's error
  guarantee and that merged sketches must share a constant hash domain.

Gludd makes that incompatibility explicit and observable instead of corrupting
an estimate during a rolling deployment.
