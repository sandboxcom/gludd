# Indexed Skip-List Span Invariant

## Status

Implemented for the beta.4 indexed skip-list API.

## Problem

`IndexedSkipList.insert()` rewired a predecessor's higher-level forward pointer
and replaced its span with the distance to the new node. It then initialized the
new node's span to zero even when the old edge crossed additional base-level nodes.

That discarded the residual distance. Under particular random level layouts,
`select(rank)` advanced too far and returned a later key while base-level
iteration remained correctly sorted. The existing 300-key randomized test exposed
the failure intermittently because insertion order was seeded but node heights used
the process-global random generator.

## Contract

This implementation defines `span[level]` as the number of base-level nodes
strictly between a node and its forward target at that level.

When an insertion splits an existing edge:

1. Save the predecessor's old forward target and old span before rewiring.
2. Assign the predecessor the prefix distance from it to the new node.
3. When an old target exists, assign the new node the residual
   `old_span - prefix` distance to that target.
4. At levels above the new node, increment crossing spans by one.
5. Deletion of a directly linked node combines the predecessor and deleted-node
   spans; deletion below a crossing edge decrements that edge.
6. For every stored key, `select(rank(key))` must return that same key.
7. Base-level ordering, duplicate updates, range queries, and the other skip-list
   variants remain unchanged.

## Zero-Downtime Development Evidence

The adjacent gate first failed when rank 4 returned key 16. A deterministic
failing-first regression then forced node levels `[2, 2, 0, 2]` while inserting
keys `[0, 10, 5, 2]`; before the repair, rank 2 returned key 10 instead of 5.

After residual-span transfer was implemented:

- the complete indexed class is 12/12 green, including the original 300-key
  randomized test;
- the combined v2 and legacy skip-list behavior set is 87/87 green under strict
  warnings; and
- all 40 v2 tests pass with 85.83 percent branch coverage for
  `skip_list_v2.py`.

The change updates only insertion metadata. It changes no persisted schema, network
contract, process state, or constructor, so old and new workers can overlap during a
rolling deployment.

## Security and Resource Boundaries

Incorrect rank selection can return the wrong policy, queue, or priority record even
when sorted iteration appears sound. The deterministic regression checks both
`select` and `rank` as inverse views of the same structure. The fix adds no
allocation beyond existing local variables, no lock, no background process, and no
external dependency; expected insertion and rank complexity remain O(log n).

## Practitioner Evidence

[The 2012 Stack Overflow discussion of Redis skip-list spans](https://stackoverflow.com/questions/10458572/what-does-the-skiplistnode-variable-span-mean-in-redis-h)
documents the long-lived practitioner contract that spans count the nodes crossed by
a forward edge and are used to calculate rank.

[Redis's current skip-list implementation](https://github.com/redis/redis/blob/unstable/src/t_zset.c)
contains structural verification that checks per-level span values, span sums,
length, and rank after each seeded insertion and deletion. That mature implementation
practice supports preserving the entire old edge distance whenever insertion splits
it, plus deterministic invariant tests rather than relying only on random layouts.
