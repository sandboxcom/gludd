# Finger Tree Structural Invariants

## Contract

A finger tree digit contains one to four values. Its recursive middle tree stores
`Node2` or `Node3` values, not the leaf values of the outer tree. Removing a
digit from either end therefore views one middle element atomically and expands
that node by exactly one level into the replacement outer digit.

Concatenation bridges the left suffix and right prefix without loss or
duplication. A bridge contains two through eight values and is partitioned
entirely into two- and three-element nodes; a one-element remainder is never
discarded. Push, pop, concatenation, split, iteration, indexed access, and cached
size must all describe the same leaf order.

## Mature-library assessment

The maintained
[`pyrsistent` package](https://pypi.org/project/pyrsistent/) provides production
persistent vectors and deques and should be preferred when callers only need
those standard abstractions. It does not expose Gludd's existing
`Empty | Single | Deep` and `Node2 | Node3` compatibility API, so replacing
this module with it in beta4 would be a breaking interface migration rather than
a bounded repair. The compatibility implementation remains isolated, with the
structural invariants directly tested; new generic collection work should use
the mature package or the Python standard library instead.

## Observability and ZDD

The structure is immutable and has no serialized or external state. A
zero-downtime rollout can shadow any production operation sequence against a
plain list oracle and compare leaf order, size, both end views, and split/concat
round trips before shifting traffic. Any mismatch fails the release gate.
Rollback is immediate because the repair changes no persisted representation or
public signature and old objects remain readable by the same code.

## Practitioner evidence

Long-lived practitioner discussions capture the subtle recursive boundary that
failed here:

- [How do `inits` and `tails` work in
  `Data.Sequence`?](https://stackoverflow.com/questions/28906742/how-do-inits-and-tails-work-in-data-sequence)
  explains that the inner finger tree is traversed as a tree of nodes and those
  nodes are not pulled apart until they become an outer digit.
- [Type error when implementing finger
  trees](https://stackoverflow.com/questions/39854211/type-error-when-implementing-finger-trees)
  shows the same essential distinction between `FingerTree<a>` and
  `FingerTree<Node<a>>` in an independent implementation.
- The maintained
  [GHC `Data.Sequence` documentation](https://ghc.gitlab.haskell.org/ghc/doc/libraries/containers-0.8-inplace/Data-Sequence.html)
  records the reference complexity and ordering contracts for the mature 2-3
  finger-tree implementation.

Gludd consequently keeps node grouping and middle-tree views explicit instead
of recursively flattening an inner node during an end operation.
