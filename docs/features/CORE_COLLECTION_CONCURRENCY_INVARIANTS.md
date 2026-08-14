# Core Collection and Concurrency Invariants

## Status

Implemented for the beta.4 Swiss-map metadata boundary. Radix-sort and
semaphore regressions are conformed to their existing public contracts. The
lock-free skip-list implementation is unchanged because its seeded replay and
complete focused suite do not reproduce a product defect.

## Swiss-map control-byte contract

`SwissHashMap` has three disjoint metadata states:

1. `0x80` represents an empty slot.
2. `0xFE` represents a tombstone.
3. An occupied slot stores only the low seven bits of its key hash, in the
   inclusive range `0x00` through `0x7F`.

The former implementation set the high bit on every fingerprint. Integer keys
whose hashes ended in `0x00` or `0x7E` therefore produced the reserved empty or
tombstone bytes. Insertion incremented the logical size, but lookup, iteration,
and resize subsequently treated those live entries as absent. Hash
fingerprints are now kept in the documented seven-bit domain, making every
occupied byte disjoint from both sentinels. A deterministic regression uses
integer keys `0` and `126` to exercise both former collisions.

The [Go Swiss-table implementation note](https://go.dev/blog/swisstable)
describes the mature design boundary directly: a control byte must distinguish
empty, deleted, and in-use slots, while an in-use byte carries `h2`. Abseil
issue [#720](https://github.com/abseil/abseil-cpp/issues/720) records a
long-lived practitioner request for reproducible hash seeds and the maintainers'
countervailing security and evolution rationale. Gludd therefore tests stable
metadata-state behavior for adversarial fingerprints without promising stable
bucket positions or persisted hash values across processes.

## Stable radix-pass contract

`counting_sort_for_radix()` is a stable sort on one digit. Elements with equal
digits retain their relative order from the input. For tens-place input
`[321, 102, 43, 500]`, digit-zero values must therefore remain `102, 500`; the
contract result is `[102, 500, 321, 43]`. The prior assertion reversed the two
equal-digit values and contradicted both the function documentation and the
reverse-input placement used by the implementation.

The seven-year practitioner discussion
[Why does Radix sort require stable digit sorts?](https://cs.stackexchange.com/questions/100223/why-does-radix-sort-require-stable-digit-sorts)
shows why this is correctness-critical rather than cosmetic: an LSD radix pass
must preserve the ordering established by less-significant passes. An adjacent
characterization now pins original order for an entire equal-digit group.

## Semaphore permit arithmetic

`asyncio.Semaphore(value)` starts with exactly `value` permits. Every
`release()` adds one permit, including releases not paired with an earlier
acquisition, and every successful `acquire()` consumes one. Consequently, a
semaphore initialized to one requires two extra releases before three immediate
acquisitions. The previous test released once and awaited three acquisitions,
so its final acquisition correctly blocked forever.

Python's maintained
[synchronization documentation](https://docs.python.org/3/library/asyncio-sync.html)
distinguishes this unbounded-release behavior from `BoundedSemaphore`. The
long-running practitioner thread
[Cancelable tasks cannot safely use semaphores](https://discuss.python.org/t/cancelable-tasks-cannot-safely-use-semaphores/70949)
also demonstrates why precise ownership and permit accounting matter for
bounded memory and cancellation. The conformed regression states the arithmetic
at the call site and ends with zero immediately available permits.

## Lock-free skip-list disposition

The gate attributed a failure to the seeded random-insertion test after its
worker crashed. Independent replay passes, as does the broader skip-list suite
under strict warnings. No skip-list code or expectation is changed without an
independently reproducible failure. This preserves randomized coverage while
avoiding an unsupported concurrency rewrite.

Practitioners discussing a
[lock-free linked structure](https://www.reddit.com/r/C_Programming/comments/1f0pnfk)
call out compare-and-swap and ABA semantics as essential to genuine lock-free
claims. Gludd's current Python implementation relies on narrower interpreter
atomicity assumptions. This task does not broaden those claims or alter its
synchronization model merely to explain an unrelated worker crash.

## Security and resource boundaries

Reserved-byte aliasing can silently drop a policy or authorization record while
reporting a plausible collection size, so the regression checks size, direct
lookup, and iteration together. The repair adds no allocation, process, lock,
dependency, I/O, or persisted state. Hash complexity and the existing 87.5
percent growth threshold remain unchanged.

The radix and semaphore changes modify tests only. They neither expand
concurrency nor hide waits: warnings-as-errors and a bounded test timeout ensure
an arithmetic mismatch fails visibly. The skip-list replay remains seeded, and
the complete randomized assertions remain enabled.

## Zero-downtime delivery and rollback

The Swiss fix changes only ephemeral control metadata built in each process.
There is no schema, wire format, file format, or shared-memory migration. Old and
new workers may overlap during a rolling deployment because maps are not
transferred between them. Promote after focused lookup/resize coverage and the
full gate are green. Rollback is a source revert or traffic shift to the prior
worker set; no data migration is required. Maps created by a retiring process
are discarded with that process, preventing mixed metadata encodings.

## Verification

- The original four gate nodes and the new sentinel/stability boundaries pass
  under warnings-as-errors and bounded timeouts.
- The affected four-file suite remains green, including randomized skip-list
  and concurrent semaphore coverage.
- Focused branch coverage must remain at least 85 percent aggregate and at
  least 75 percent for every touched production file.
- Ruff, strict mypy, docstring, Markdown, feature-spec, and task-ledger checks
  must remain green without suppressions.
