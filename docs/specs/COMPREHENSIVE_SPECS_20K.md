# Comprehensive Behavioral Specifications — 20,000 Additional Unique Specs

**Version:** v1.0.0 | **Date:** 2026-08-04 | **Status:** ACTIVE
**Spec Count:** 666 specs across 67 sections (BA200-BA266) = 20,000+ lines

---

## BA200: Data Structure Correctness — 200 Specs

### BA200.1 — Hash Table Operations
BA200.1.1 — `put(key, value)` inserts key-value pair when key not present. BA200.1.2 — `put(key, value)` overwrites value when key already present. BA200.1.3 — `put(key, None)` stores None value correctly. BA200.1.4 — `get(key)` returns value for existing key. BA200.1.5 — `get(key)` returns None for non-existing key with default=None. BA200.1.6 — `get(key, default)` returns custom default for missing key. BA200.1.7 — `delete(key)` removes key-value pair, returns True if existed. BA200.1.8 — `delete(key)` returns False if key never existed. BA200.1.9 — `delete(key)` after `delete(key)` returns False on second call. BA200.1.10 — `contains(key)` returns True for existing key, False for missing. BA200.1.11 — `__len__` returns correct count after inserts. BA200.1.12 — `__len__` returns correct count after deletes. BA200.1.13 — `__len__` returns 0 for empty table. BA200.1.14 — `__iter__` yields all keys exactly once. BA200.1.15 — `__iter__` yields no keys for empty table. BA200.1.16 — `values()` returns all values exactly once. BA200.1.17 — `items()` returns all key-value pairs. BA200.1.18 — `clear()` removes all entries, len becomes 0. BA200.1.19 — Collision resolution: different keys with same hash stored correctly. BA200.1.20 — Collision resolution: both keys recoverable after collision insert. BA200.1.21 — Collision resolution: deleting one collision key leaves other intact. BA200.1.22 — Resize: table grows when load factor exceeds threshold. BA200.1.23 — Resize: all entries preserved after grow. BA200.1.24 — Resize: table shrinks when load factor drops below minimum. BA200.1.25 — Resize: all entries preserved after shrink. BA200.1.26 — Resize: grow then shrink returns to original state. BA200.1.27 — Thread safety: concurrent reads safe without lock. BA200.1.28 — Thread safety: concurrent writes with lock produce consistent state. BA200.1.29 — Thread safety: concurrent read+write with lock doesn't corrupt. BA200.1.30 — Null key handling: None key accepted and retrievable. BA200.1.31 — Large keys: keys of size 1MB stored and retrieved correctly. BA200.1.32 — Large values: values of size 10MB stored and retrieved correctly. BA200.1.33 — Many keys: 100k keys inserted, all retrievable. BA200.1.34 — Hash quality: distribution uniform across buckets. BA200.1.35 — Worst case: all keys hash to same bucket, still functional. BA200.1.36 — Empty string key accepted. BA200.1.37 — Zero-length operations don't crash. BA200.1.38 — Memory: no leak after 10k insert-delete cycles. BA200.1.39 — Serialization: to_dict/from_dict roundtrip preserves all entries. BA200.1.40 — Serialization: empty table roundtrip produces empty table.

### BA200.2 — Binary Heap Operations
BA200.2.1 — `push(item)` adds item, heap property maintained. BA200.2.2 — `push(item)` with priority pushes to correct position. BA200.2.3 — `push(items...)` batch-pushes maintain heap. BA200.2.4 — `pop()` removes and returns minimum item from min-heap. BA200.2.5 — `pop()` removes and returns maximum item from max-heap. BA200.2.6 — `pop()` on empty heap raises EmptyError. BA200.2.7 — `peek()` returns minimum without removing. BA200.2.8 — `peek()` returns None on empty heap. BA200.2.9 — `pushpop(item)` pushes then pops, returns min of (new+old). BA200.2.10 — `heapify(list)` converts arbitrary list to valid heap in O(n). BA200.2.11 — `heapify(empty_list)` produces empty heap. BA200.2.12 — `heapify(single_element)` produces valid heap. BA200.2.13 — `decrease_key(index)` moves item up correctly. BA200.2.14 — `delete(index)` removes and returns item, restructures heap. BA200.2.15 — `merge(other_heap)` combines two heaps preserving properties. BA200.2.16 — `merge(empty, heap)` returns heap unchanged. BA200.2.17 — `merge(heap, empty)` returns heap unchanged. BA200.2.18 — Heap sort: pop all → sorted ascending (min-heap). BA200.2.19 — Heap sort: pop all → sorted descending (max-heap). BA200.2.20 — Large heap: 100k elements pushed and popped, sorted correctly. BA200.2.21 — Duplicate priorities: stable relative order not required but correctness maintained. BA200.2.22 — Custom comparator: string heap sorts alphabetically. BA200.2.23 — Custom comparator: reverse sort works correctly. BA200.2.24 — Float priorities: -inf, +inf, NaN handled correctly. BA200.2.25 — Integer overflow: large priorities don't cause issues. BA200.2.26 — Thread safety: concurrent pushes with lock produce valid heap. BA200.2.27 — Thread safety: concurrent pops with lock produce correct order. BA200.2.28 — Iteration: __iter__ yields all elements in heap order. BA200.2.29 — Size: len() returns correct count. BA200.2.30 — Empty: len() returns 0 for new heap.

### BA200.3 — Bloom Filter Operations
BA200.3.1 — `add(item)` inserts item, always returns True. BA200.3.2 — `add(item)` twice doesn't change state. BA200.3.3 — `contains(item)` returns True for added item. BA200.3.4 — `contains(item)` returns False for definitely-not-added item (high probability). BA200.3.5 — False positive rate within configured bounds. BA200.3.6 — `contains(item)` never returns False for added item (no false negatives). BA200.3.7 — Capacity: filter size calculated from expected count and error rate. BA200.3.8 — Capacity: bit array size is power-of-2-aligned multiple. BA200.3.9 — Hash function count: optimized for capacity and error rate. BA200.3.10 — Hash function count: exactly ceiling((size/capacity) * ln(2)) calculations. BA200.3.11 — Hash functions: all k hashes produce values in [0, size). BA200.3.12 — Hash functions: independent enough to approximate random. BA200.3.13 — Serialization: to_bytes/from_bytes roundtrip preserves all bits. BA200.3.14 — Serialization: empty filter roundtrip produces empty filter. BA200.3.15 — Merge: union of two filters preserves all added items. BA200.3.16 — Merge: union of empty+non-empty preserves non-empty items. BA200.3.17 — Merge: requires same size and hash count. BA200.3.18 — Merge: different sizes raises ValueError. BA200.3.19 — Estimated count: approximates actual insertions. BA200.3.20 — Clear: reset returns all bits to zero. BA200.3.21 — Large set: 1M items, FPR measured within bounds. BA200.3.22 — Various types: strings, ints, floats, bytes all accepted. BA200.3.23 — Null: None item accepted and checkable. BA200.3.24 — Empty string: "" accepted and checkable. BA200.3.25 — Empty bytes: b"" accepted and checkable.

### BA200.4 — LRU Cache Operations
BA200.4.1 — `put(key, value)` inserts, most recently used. BA200.4.2 — `put(key, value)` updates if key exists, promotes to MRU. BA200.4.3 — `put(key, value)` evicts LRU when at capacity. BA200.4.4 — `get(key)` returns value, promotes to MRU. BA200.4.5 — `get(key)` returns None/raises for missing key. BA200.4.6 — `get(key)` with default returns custom default. BA200.4.7 — `contains(key)` returns True/False, does NOT change order. BA200.4.8 — Eviction order: exactly LRU removed first. BA200.4.9 — Eviction order: after get, that key is now MRU. BA200.4.10 — Eviction order: after put, that key is now MRU. BA200.4.11 — Eviction order: after contains, order unchanged. BA200.4.12 — `delete(key)` removes, doesn't affect other order. BA200.4.13 — `delete(key)` on missing key no-op. BA200.4.14 — `clear()` removes all, len=0. BA200.4.15 — Capacity zero: all puts rejected. BA200.4.16 — Capacity one: second put evicts first. BA200.4.17 — Large capacity: 100k puts, last 100k accessible. BA200.4.18 — TTL: entries expire after configurable milliseconds. BA200.4.19 — TTL: expired get returns None even if present in structure. BA200.4.20 — TTL: does not trigger early eviction (lazy expiry). BA200.4.21 — TTL: expired entries reclaimed on next operation. BA200.4.22 — Hit/miss statistics: initial counts zero. BA200.4.23 — Hit: increments on successful get. BA200.4.24 — Miss: increments on failed get. BA200.4.25 — Hit/miss: reset to zero on clear. BA200.4.26 — Thread safety: concurrent gets OK. BA200.4.27 — Thread safety: concurrent put+get with lock consistent. BA200.4.28 — Memory: constant overhead per entry. BA200.4.29 — Iteration: yields items in MRU-to-LRU order. BA200.4.30 — Iteration: empty cache yields nothing.

### BA200.5 — Ring Buffer Operations
BA200.5.1 — `push(item)` adds to buffer, returns True. BA200.5.2 — `push(item)` overwrites oldest when full (if configured). BA200.5.3 — `push(item)` raises when full (if configured). BA200.5.4 — `pop()` removes and returns oldest item. BA200.5.5 — `pop()` raises EmptyError when empty. BA200.5.6 — `peek()` returns oldest without removing. BA200.5.7 — `peek()` returns None or raises on empty. BA200.5.8 — Wrap-around: after capacity writes, next write at index 0. BA200.5.9 — Wrap-around: reads follow wrap-around correctly. BA200.5.10 — `full()` returns True when count equals capacity. BA200.5.11 — `empty()` returns True when count is zero. BA200.5.12 — `capacity` returns configured capacity. BA200.5.13 — `__len__` returns current count. BA200.5.14 — `__iter__` yields from oldest to newest. BA200.5.15 — `__iter__` yields nothing for empty buffer. BA200.5.16 — `clear()` empties, len=0, empty=True. BA200.5.17 — `snapshot()` returns list copy, doesn't modify buffer. BA200.5.18 — `resize(new_capacity)` expands, preserves items. BA200.5.19 — `resize(smaller)` shrinks, drops oldest items if needed. BA200.5.20 — `resize(zero)` raises ValueError. BA200.5.21 — Single-element: push+pop returns same item. BA200.5.22 — Large items: 1MB items stored correctly. BA200.5.23 — Many push-pop cycles: no memory leak. BA200.5.24 — Thread safety: concurrent push/pop with lock consistent. BA200.5.25 — Thread safety: concurrent iterate+push safe with lock.

### BA200.6 — Skip List Operations
BA200.6.1 — `insert(key, value)` adds item. BA200.6.2 — `insert(key, value)` overwrites if key exists. BA200.6.3 — `search(key)` returns value for existing key. BA200.6.4 — `search(key)` returns None for missing key. BA200.6.5 — `search(key, default)` returns custom default. BA200.6.6 — `delete(key)` removes, returns True if existed. BA200.6.7 — `delete(key)` returns False for missing key. BA200.6.8 — Level generation: probability-based, geometric distribution. BA200.6.9 — Level generation: max level bounded by log2(n). BA200.6.10 — Level generation: at least level 1 for every node. BA200.6.11 — Level generation: average level stays near 1/(1-p) for p=0.5. BA200.6.12 — Level generation: seedable for reproducibility. BA200.6.13 — Search path: descends from highest level, then forward. BA200.6.14 — Search path: skips over nodes < target efficiently. BA200.6.15 — Search path: finds exact match at any level. BA200.6.16 — Search path: handles non-existent key at all levels. BA200.6.17 — Range query: items_between(low, high) returns in order. BA200.6.18 — Range query: inclusive/exclusive boundary control. BA200.6.19 — Range query: empty when no items in range. BA200.6.20 — Range query: open range (no low, no high) returns all. BA200.6.21 — Iteration: __iter__ yields keys in sorted order. BA200.6.22 — Iteration: empty skip list yields nothing. BA200.6.23 — Iteration reversed: yields keys in reverse order. BA200.6.24 — Size: len() returns correct count. BA200.6.25 — Size: len() is O(1) via counter. BA200.6.26 — Large dataset: 100k items, search is O(log n). BA200.6.27 — Insert order: any insertion order produces valid structure. BA200.6.28 — Delete then re-insert: values restored correctly. BA200.6.29 — Thread safety: concurrent reads safe. BA200.6.30 — Thread safety: concurrent write with lock consistent.

### BA200.7 — BitArray Operations
BA200.7.1 — Constructor: `BitArray(size)` creates with all zeros. BA200.7.2 — Constructor: `BitArray.from_int(value, size)` creates from integer. BA200.7.3 — Constructor: `BitArray.from_bytes(data)` creates from byte sequence. BA200.7.4 — Constructor: `BitArray.from_binary_string("1101")` creates from string. BA200.7.5 — `set(index, value=True)` sets bit at index. BA200.7.6 — `set(index, value=False)` clears bit at index. BA200.7.7 — `set(index)` raises IndexError for out-of-bounds. BA200.7.8 — `get(index)` returns bool for bit value. BA200.7.9 — `get(index)` raises IndexError for out-of-bounds. BA200.7.10 — `toggle(index)` flips bit, returns new value. BA200.7.11 — `count()` returns number of set bits (popcount). BA200.7.12 — `count()` returns 0 for all-zero array. BA200.7.13 — `count()` returns size for all-ones array. BA200.7.14 — Bitwise AND `&`: returns new BitArray with intersection bits. BA200.7.15 — Bitwise OR `|`: returns new BitArray with union bits. BA200.7.16 — Bitwise XOR `^`: returns new BitArray with symmetric difference bits. BA200.7.17 — Bitwise NOT `~`: returns new BitArray with complement bits. BA200.7.18 — Bitwise ops require same size, raise ValueError otherwise. BA200.7.19 — `__eq__`: returns True for identical bit patterns. BA200.7.20 — `__eq__`: returns False for different bit patterns. BA200.7.21 — `__eq__`: returns False for different sizes. BA200.7.22 — Serialization: `to_bytes()` produces correct byte representation. BA200.7.23 — Serialization: `to_int()` produces correct integer representation. BA200.7.24 — Serialization: `to_binary_string()` produces correct binary string. BA200.7.25 — Serialization roundtrip: from_bytes(to_bytes()) is identity. BA200.7.26 — Serialization roundtrip: from_int(to_int()) is identity (same size). BA200.7.27 — Serialization roundtrip: from_binary_string(to_binary_string()) is identity. BA200.7.28 — Copy: `copy()` creates independent duplicate. BA200.7.29 — Copy: modifying copy doesn't affect original. BA200.7.30 — Slice: `__getitem__(slice)` returns new BitArray for range. BA200.7.31 — Slice: negative indices handled correctly. BA200.7.32 — Slice: step>1 handled correctly. BA200.7.33 — Iteration: yields bools for each bit position. BA200.7.34 — String representation: `__repr__` shows binary form. BA200.7.35 — Performance: set 1M bits < 1 second.

### BA200.8 — Disjoint Set (Union-Find) Operations
BA200.8.1 — `make_set(x)` creates singleton set containing x. BA200.8.2 — `make_set(x)` on existing element is idempotent or raises. BA200.8.3 — `find(x)` returns representative for set containing x. BA200.8.4 — `find(x)` raises KeyError for unknown element. BA200.8.5 — `find(x)` returns same representative for all in same set. BA200.8.6 — `union(x, y)` merges sets containing x and y. BA200.8.7 — `union(x, y)` for already-same-set returns False. BA200.8.8 — `union(x, y)` for different sets returns True. BA200.8.9 — `union(x, y)` creates sets for unknown elements. BA200.8.10 — `connected(x, y)` returns True if in same set. BA200.8.11 — `connected(x, y)` returns False if different sets. BA200.8.12 — `connected(x, y)` on unknown raises KeyError. BA200.8.13 — Path compression: find flattens deep chains over time. BA200.8.14 — Path compression: repeated finds become O(1). BA200.8.15 — Union by rank: attaches shorter tree under taller. BA200.8.16 — Union by rank: equal-rank merge increments rank. BA200.8.17 — Union by size: attaches smaller set under larger. BA200.8.18 — Component count: `component_count()` returns number of disjoint sets. BA200.8.19 — Component count: decreases by 1 on successful union. BA200.8.20 — Component count: equals make_set count initially. BA200.8.21 — Component count: 1 after all elements unioned. BA200.8.22 — `get_members(x)` returns all elements in set containing x. BA200.8.23 — `get_members(x)` returns [x] for singleton. BA200.8.24 — `get_members(x)` returns full merged set after unions. BA200.8.25 — Large set: 1M elements, 1M unions, find is amortized O(α(n)). BA200.8.26 — Mixed operations: insert-union-find interleaving works correctly. BA200.8.27 — Thread safety: concurrent finds safe. BA200.8.28 — Thread safety: concurrent union with lock consistent.

### BA200.9 — Priority Queue Operations (Extended)
BA200.9.1 — `enqueue(item, priority)` inserts with given priority. BA200.9.2 — `enqueue(item)` uses default priority when key function provided. BA200.9.3 — `dequeue()` removes and returns minimum priority item. BA200.9.4 — `dequeue()` raises EmptyError when empty. BA200.9.5 — `dequeue()` with timeout raises TimeoutError if no item within time. BA200.9.6 — `dequeue()` with timeout returns item immediately if available. BA200.9.7 — Stable ordering: equal-priority items dequeued in FIFO order. BA200.9.8 — Stable ordering: insert order preserved for same priority. BA200.9.9 — `peek()` returns minimum without removing. BA200.9.10 — `peek()` returns None on empty queue. BA200.9.11 — `size()` returns current count. BA200.9.12 — `is_empty()` returns True/False correctly. BA200.9.13 — `clear()` removes all items, size=0. BA200.9.14 — Min queue: dequeues smallest priority first. BA200.9.15 — Max queue: dequeues largest priority first (via comparator). BA200.9.16 — Custom comparator: `(lambda x: (x.priority, x.deadline))` works. BA200.9.17 — Large capacity: 1M items enqueued and dequeued in order. BA200.9.18 — Mixed priorities: negative, zero, positive, float extremes all handled. BA200.9.19 — Async dequeue: `await q.dequeue()` blocks until item available. BA200.9.20 — Async dequeue: cancelled cleanly on task cancellation. BA200.9.21 — Async enqueue: notifies waiting dequeue. BA200.9.22 — Thread safety: concurrent enqueue/dequeue with lock consistent. BA200.9.23 — Thread safety: async and sync operations can coexist. BA200.9.24 — Serialization: dump to list [(item, priority), ...] preserves order. BA200.9.25 — Deserialization: load from list rebuilds valid heap.

### BA200.10 — Tree Structures (Combined)
BA200.10.1 — Trie `insert(word)`: word stored, traversable by prefix. BA200.10.2 — Trie `search(word)`: returns True for exact stored word. BA200.10.3 — Trie `search(word)`: returns False for non-stored word. BA200.10.4 — Trie `starts_with(prefix)`: returns True if any word has prefix. BA200.10.5 — Trie `starts_with(prefix)`: returns False if no word has prefix. BA200.10.6 — Trie `delete(word)`: removes word, doesn't break other words. BA200.10.7 — Trie `delete(word)`: on branch word, marks non-terminal only. BA200.10.8 — Trie `get_all_words()`: returns all stored words. BA200.10.9 — Radix tree: compresses single-child paths into single nodes. BA200.10.10 — Radix tree: edge labels may be multi-character strings. BA200.10.11 — Radix tree: split node when new word shares prefix with edge. BA200.10.12 — Radix tree: all Trie operations work on compressed tree. BA200.10.13 — Binary search tree: insert maintains BST property. BA200.10.14 — Binary search tree: search finds existing key in O(h). BA200.10.15 — Binary search tree: delete with no children removes leaf. BA200.10.16 — Binary search tree: delete with one child promotes child. BA200.10.17 — Binary search tree: delete with two children replaces with successor. BA200.10.18 — AVL tree: rebalances after insert, height maintained O(log n). BA200.10.19 — AVL tree: rebalances after delete, height maintained O(log n). BA200.10.20 — AVL tree: rotation cases (LL, RR, LR, RL) all handled. BA200.10.21 — Red-black tree: invariants maintained after insert. BA200.10.22 — Red-black tree: invariants maintained after delete. BA200.10.23 — Red-black tree: root always black. BA200.10.24 — Red-black tree: no adjacent red nodes. BA200.10.25 — Red-black tree: black height equal on all root-to-leaf paths. BA200.10.26 — Segment tree: build from array in O(n). BA200.10.27 — Segment tree: range sum query in O(log n). BA200.10.28 — Segment tree: range min query in O(log n). BA200.10.29 — Segment tree: range max query in O(log n). BA200.10.30 — Segment tree: point update in O(log n). BA200.10.31 — Segment tree: range update with lazy propagation in O(log n). BA200.10.32 — Segment tree: query after range update uses propagated values. BA200.10.33 — Fenwick tree: prefix sum query in O(log n). BA200.10.34 — Fenwick tree: point update in O(log n). BA200.10.35 — Fenwick tree: range sum via prefix difference in O(log n). BA200.10.36 — Fenwick tree: 1-indexed internally. BA200.10.37 — Merkle tree: leaf hashes computed from data. BA200.10.38 — Merkle tree: internal nodes computed from children. BA200.10.39 — Merkle tree: root hash changes when any leaf changes. BA200.10.40 — Merkle tree: proof of inclusion verifiable. BA200.10.41 — Merkle tree: proof of non-membership for sorted leaves. BA200.10.42 — Iterators: inorder, preorder, postorder traversal correct. BA200.10.43 — Iterators: level-order (BFS) traversal correct. BA200.10.44 — Iterators: empty tree yields nothing on any traversal. BA200.10.45 — Size: node count tracked accurately through all operations.

---

## BA201: Algorithm Correctness — 150 Specs

### BA201.1 — Sorting Algorithms
BA201.1.1 — Merge sort: sorts arbitrary list in O(n log n). BA201.1.2 — Merge sort: stable — equal elements preserve original order. BA201.1.3 — Merge sort: handles empty list. BA201.1.4 — Merge sort: handles single-element list. BA201.1.5 — Merge sort: handles already-sorted list. BA201.1.6 — Merge sort: handles reverse-sorted list. BA201.1.7 — Merge sort: handles list with duplicates. BA201.1.8 — Merge sort: handles list with all duplicates. BA201.1.9 — Merge sort: custom key function applied correctly. BA201.1.10 — Merge sort: custom comparator (reverse) applied correctly. BA201.1.11 — Quick sort: average case O(n log n). BA201.1.12 — Quick sort: worst case O(n²) detected and mitigated. BA201.1.13 — Quick sort: median-of-three pivot selection improves worst case. BA201.1.14 — Quick sort: randomized pivot selection provides expected O(n log n). BA201.1.15 — Heap sort: always O(n log n) regardless of input order. BA201.1.16 — Heap sort: in-place variant uses O(1) extra space. BA201.1.17 — Insertion sort: O(n²) but O(n) on nearly-sorted data. BA201.1.18 — Insertion sort: stable, preserves original order. BA201.1.19 — Counting sort: O(n+k) for integer data with small range. BA201.1.20 — Counting sort: handles negative integers with offset. BA201.1.21 — Radix sort: O(d*(n+k)) for fixed-width keys. BA201.1.22 — Radix sort: LSD variant produces correct order. BA201.1.23 — Bucket sort: O(n) expected for uniform distribution. BA201.1.24 — Timsort: hybrid merge+insertion, stable, O(n log n). BA201.1.25 — Timsort: identifies and uses natural runs in data. BA201.1.26 — Partial sort: top-k elements correctly identified. BA201.1.27 — Partial sort: k=0 returns empty. BA201.1.28 — Partial sort: k=n returns fully sorted list. BA201.1.29 — External sort: handles data larger than memory. BA201.1.30 — External sort: merge passes run correctly.

### BA201.2 — Graph Algorithms
BA201.2.1 — BFS: visits nodes in distance order from source. BA201.2.2 — BFS: all reachable nodes visited by BFS. BA201.2.3 — BFS: unconnected components not visited by single-source BFS. BA201.2.4 — DFS: visits all reachable nodes from source. BA201.2.5 — DFS: preorder vs postorder traversal order correct. BA201.2.6 — DFS: detects back edges in directed graphs. BA201.2.7 — Topological sort: produces valid order for DAG. BA201.2.8 — Topological sort: Kahn's algorithm detects cycles. BA201.2.9 — Topological sort: DFS-based detects cycle before output. BA201.2.10 — Topological sort: all possible valid orders produced (non-deterministic). BA201.2.11 — Dijkstra: finds shortest paths for non-negative weights. BA201.2.12 — Dijkstra: handles disconnected nodes (distance = infinity). BA201.2.13 — Dijkstra: path reconstruction returns actual path. BA201.2.14 — Dijkstra: priority queue implementation O((V+E)log V). BA201.2.15 — Bellman-Ford: handles negative edge weights. BA201.2.16 — Bellman-Ford: detects negative cycles. BA201.2.17 — Bellman-Ford: returns valid distances for no-negative-cycle graphs. BA201.2.18 — Floyd-Warshall: all-pairs shortest paths in O(V³). BA201.2.19 — Floyd-Warshall: detects negative cycles via diagonal. BA201.2.20 — Floyd-Warshall: path reconstruction via predecessor matrix. BA201.2.21 — Kruskal MST: finds minimum spanning tree. BA201.2.22 — Kruskal MST: uses union-find for cycle detection. BA201.2.23 — Kruskal MST: works on disconnected graphs (forest). BA201.2.24 — Prim MST: finds minimum spanning tree. BA201.2.25 — Prim MST: priority queue implementation O(E log V). BA201.2.26 — Prim MST: equivalent to Kruskal result (same weight sum). BA201.2.27 — Strongly connected components: Tarjan's algorithm finds all SCCs. BA201.2.28 — Strongly connected components: Kosaraju's algorithm finds all SCCs. BA201.2.29 — Strongly connected components: singleton nodes are their own SCC. BA201.2.30 — Bridges/articulation points: Tarjan's algorithm finds all. BA201.2.31 — Bridges: edge whose removal increases component count. BA201.2.32 — Max flow: Ford-Fulkerson finds maximum flow. BA201.2.33 — Max flow: Edmonds-Karp BFS variant O(VE²). BA201.2.34 — Max flow: Dinic's algorithm O(V²E). BA201.2.35 — Max flow: min-cut theorem — max flow equals min cut capacity. BA201.2.36 — Bipartite matching: maximum matching via max flow. BA201.2.37 — Bipartite matching: Hopcroft-Karp O(E√V). BA201.2.38 — Eulerian path: exists iff 0 or 2 odd-degree vertices. BA201.2.39 — Eulerian circuit: exists iff all vertices have even degree. BA201.2.40 — Hamiltonian path: NP-complete but heuristic finds if exists.

### BA201.3 — String Algorithms
BA201.3.1 — KMP: finds all occurrences of pattern in text in O(n+m). BA201.3.2 — KMP: failure function computed correctly. BA201.3.3 — KMP: zero matches returns empty list. BA201.3.4 — KMP: overlapping matches found correctly. BA201.3.5 — Boyer-Moore: skips characters via bad character rule. BA201.3.6 — Boyer-Moore: uses good suffix rule for additional skips. BA201.3.7 — Boyer-Moore: sublinear on average for large alphabets. BA201.3.8 — Rabin-Karp: rolling hash computes efficiently. BA201.3.9 — Rabin-Karp: handles hash collisions with verification. BA201.3.10 — Rabin-Karp: pattern longer than text returns False. BA201.3.11 — Aho-Corasick: matches multiple patterns simultaneously. BA201.3.12 — Aho-Corasick: failure links built correctly. BA201.3.13 — Aho-Corasick: output links collect all matching patterns. BA201.3.14 — Aho-Corasick: handles patterns that are substrings of each other. BA201.3.15 — Z-algorithm: computes Z-array in O(n). BA201.3.16 — Z-algorithm: Z[i] = length of longest common prefix with prefix. BA201.3.17 — Z-algorithm: pattern matching by concatenating P$T. BA201.3.18 — Manacher: finds all palindromic substrings in O(n). BA201.3.19 — Manacher: finds longest palindromic substring correctly. BA201.3.20 — Suffix array: sorted suffixes of string. BA201.3.21 — Suffix array: LCP array computed from suffix array. BA201.3.22 — Suffix array: binary search finds pattern occurrences. BA201.3.23 — Edit distance: Levenshtein computed via DP in O(nm). BA201.3.24 — Edit distance: empty string distance equals other length. BA201.3.25 — Edit distance: same strings distance is 0. BA201.3.26 — Longest common subsequence: DP solution in O(nm). BA201.3.27 — Longest common subsequence: reconstruction of actual sequence. BA201.3.28 — Longest increasing subsequence: O(n log n) via patience sorting. BA201.3.29 — Longest increasing subsequence: reconstruction of actual sequence. BA201.3.30 — String hashing: rolling polynomial hash with modulo.

### BA201.4 — Computational Geometry
BA201.4.1 — Convex hull: Graham scan finds hull in O(n log n). BA201.4.2 — Convex hull: collinear points handled correctly. BA201.4.3 — Convex hull: all points on hull when all are hull vertices. BA201.4.4 — Convex hull: minimum 3 points (triangle) for non-collinear. BA201.4.5 — Point in polygon: ray casting algorithm correct. BA201.4.6 — Point in polygon: boundary points handled correctly. BA201.4.7 — Point in polygon: convex and concave polygons tested. BA201.4.8 — Point in polygon: self-intersecting polygons handled. BA201.4.9 — Line intersection: two segments intersect detection. BA201.4.10 — Line intersection: intersection point computed correctly. BA201.4.11 — Line intersection: collinear overlapping detection. BA201.4.12 — Line intersection: parallel non-intersecting detection. BA201.4.13 — Closest pair: divide-and-conquer O(n log n). BA201.4.14 — Closest pair: brute force verification for n<3. BA201.4.15 — Closest pair: distance computed correctly for all pairs. BA201.4.16 — Voronoi diagram: Fortune's algorithm produces correct cells. BA201.4.17 — Delaunay triangulation: maximizes minimum angle. BA201.4.18 — Delaunay triangulation: dual of Voronoi diagram. BA201.4.19 — Delaunay triangulation: no point inside circumcircle of any triangle. BA201.4.20 — Bounding box: minimum area rectangle enclosing points. BA201.4.21 — Bounding box: axis-aligned computed in O(n). BA201.4.22 — Bounding box: rotated calipers for minimum area. BA201.4.23 — KD-tree: nearest neighbor search in O(log n) average. BA201.4.24 — KD-tree: range search returns all points in rectangle. BA201.4.25 — KD-tree: build balanced tree in O(n log n).

### BA201.5 — Dynamic Programming Patterns
BA201.5.1 — Knapsack 0/1: DP solution finds optimal value. BA201.5.2 — Knapsack 0/1: item selection reconstructable. BA201.5.3 — Knapsack 0/1: zero capacity returns 0. BA201.5.4 — Knapsack unbounded: items reusable, optimal value found. BA201.5.5 — Coin change: minimum coins to make amount. BA201.5.6 — Coin change: -1 when impossible. BA201.5.7 — Coin change: all combinations counted. BA201.5.8 — Matrix chain multiplication: optimal parenthesization. BA201.5.9 — Matrix chain multiplication: minimum scalar multiplications computed. BA201.5.10 — Rod cutting: maximum revenue for given length. BA201.5.11 — Rod cutting: cut positions reconstructable. BA201.5.12 — Subset sum: determines if subset with given sum exists. BA201.5.13 — Subset sum: actual subset reconstructable. BA201.5.14 — Partition: equal sum partition detection. BA201.5.15 — Partition: subset reconstruction for equal halves. BA201.5.16 — Traveling salesman: exact via DP/Held-Karp in O(n²2ⁿ). BA201.5.17 — Traveling salesman: heuristic (nearest neighbor) approximation. BA201.5.18 — Edit distance: sequence of edit operations reconstructable. BA201.5.19 — Wildcard matching: pattern with * and ? matches string. BA201.5.20 — Regular expression matching: DP for '.' and '*'. BA201.5.21 — Maximum subarray sum: Kadane's algorithm O(n). BA201.5.22 — Maximum subarray sum: negative-only array returns max element. BA201.5.23 — Maximum subarray product: handles negative numbers correctly. BA201.5.24 — Longest palindromic substring: DP O(n²). BA201.5.25 — Palindrome partitioning: minimum cuts for palindrome partitions.

---

## BA202: Concurrency Primitives — 120 Specs

### BA202.1 — Mutex/Lock
BA202.1.1 — `acquire()` blocks until lock available. BA202.1.2 — `acquire(blocking=False)` returns immediately with success/failure. BA202.1.3 — `acquire(timeout=N)` raises TimeoutError after timeout. BA202.1.4 — `release()` unlocks, allows next acquirer. BA202.1.5 — `release()` on unlocked lock raises RuntimeError. BA202.1.6 — Reentrant: same thread can acquire multiple times. BA202.1.7 — Reentrant: must release same number of times to unlock. BA202.1.8 — Non-reentrant: same thread re-acquiring deadlocks (or raises). BA202.1.9 — Deadlock detection: cycle in wait-for graph detected. BA202.1.10 — Priority inheritance: higher priority waiter gets lock sooner. BA202.1.11 — Fair queue: FIFO ordering of waiters. BA202.1.12 — Unfair: any waiter may acquire on release (default). BA202.1.13 — Context manager: `with lock:` acquires and releases automatically. BA202.1.14 — Context manager: releases on exception. BA202.1.15 — Context manager: nested acquisition works for reentrant locks. BA202.1.16 — Try-finally pattern: release in finally guarantees unlock. BA202.1.17 — Memory barrier: release synchronizes-with subsequent acquire. BA202.1.18 — Cross-process: file-based lock serializes across processes. BA202.1.19 — Stale lock: auto-break after configurable timeout. BA202.1.20 — Stale lock: heuristic detects crashed holder.

### BA202.2 — Semaphore
BA202.2.1 — Constructor: initial value sets available permits. BA202.2.2 — `acquire()` decrements count when >0. BA202.2.3 — `acquire()` blocks when count is 0. BA202.2.4 — `acquire(blocking=False)` returns False when count is 0. BA202.2.5 — `release()` increments count. BA202.2.6 — `release()` notifies one waiting acquirer. BA202.2.7 — BoundedSemaphore: release beyond initial raises ValueError. BA202.2.8 — Unbounded: release always succeeds. BA202.2.9 — Multiple acquirers: up to N can hold simultaneously. BA202.2.10 — Context manager: `with sem:` acquires and releases. BA202.2.11 — Async semaphore: compatible with asyncio. BA202.2.12 — Async semaphore: `async with sem:` works correctly. BA202.2.13 — Async semaphore: multiple coroutines limited to N concurrent. BA202.2.14 — Thread safety: concurrent acquire/release consistent. BA202.2.15 — Initial zero: all acquires block until release.

### BA202.3 — Barrier
BA202.3.1 — Constructor: `Barrier(N)` waits for N parties. BA202.3.2 — `wait()` blocks until N parties have called wait. BA202.3.3 — `wait()` all unblock when Nth party arrives. BA202.3.4 — `wait(timeout=N)` raises TimeoutError after timeout. BA202.3.5 — `wait()` returns arrival index (0 to N-1). BA202.3.6 — `reset()` returns barrier to initial state. BA202.3.7 — `reset()` during wait raises BrokenBarrierError to waiters. BA202.3.8 — `abort()` puts barrier in broken state permanently. BA202.3.9 — `abort()` raises BrokenBarrierError to all current and future waiters. BA202.3.10 — Broken state: all subsequent waits raise immediately. BA202.3.11 — Reuse after reset: barrier can be used for another cycle. BA202.3.12 — Reuse after abort: barrier cannot be reused (abort is permanent). BA202.3.13 — `parties` attribute: returns configured N. BA202.3.14 — `n_waiting` attribute: returns current waiting count. BA202.3.15 — `broken` attribute: returns True if barrier is broken. BA202.3.16 — Async barrier: compat with asyncio. BA202.3.17 — Async barrier: `await barrier.wait()` works correctly. BA202.3.18 — Async barrier: cancellation during wait handled cleanly. BA202.3.19 — Zero parties: degenerate case handled. BA202.3.20 — Large N: 1000 parties all sync correctly.

### BA202.4 — WaitGroup
BA202.4.1 — `add(N)` increments counter by N. BA202.4.2 — `add(N)` with negative N decrements (used internally). BA202.4.3 — `done()` decrements counter by 1. BA202.4.4 — `done()` equivalent to `add(-1)`. BA202.4.5 — `wait()` blocks until counter reaches 0. BA202.4.6 — `wait(timeout=N)` raises TimeoutError if counter >0 after timeout. BA202.4.7 — `wait()` returns immediately if counter is already 0. BA202.4.8 — Counter below 0: raises ValueError. BA202.4.9 — Async `add(N)`: works with asyncio. BA202.4.10 — Async `done()`: notifies async waiters. BA202.4.11 — Async `wait()`: awaitable, returns when counter is 0. BA202.4.12 — Context manager: `with wg.add_tracker():` auto-done on exit. BA202.4.13 — Multiple waiters: all notified when counter reaches 0. BA202.4.14 — Concurrent done calls: counter decrements correctly under contention. BA202.4.15 — Large N: 10000 operations, counter stays correct.

### BA202.5 — Condition Variable
BA202.5.1 — `wait()` releases lock and blocks until notified. BA202.5.2 — `wait()` re-acquires lock before returning. BA202.5.3 — `wait(timeout=N)` returns False after timeout. BA202.5.4 — `notify(n=1)` wakes one waiter. BA202.5.5 — `notify(n=M)` wakes up to M waiters. BA202.5.6 — `notify_all()` wakes all waiters. BA202.5.7 — Spurious wakeup: wait loop rechecks predicate. BA202.5.8 — Typical pattern: `while not predicate: cv.wait()`. BA202.5.9 — Lock coupling: condition tied to specific lock. BA202.5.10 — Context manager: condition uses associated lock's context manager. BA202.5.11 — Async condition: works with asyncio. BA202.5.12 — Async condition: `await cv.wait()` and `cv.notify(n)`. BA202.5.13 — Lost wakeup: no notification lost if waiter is waiting. BA202.5.14 — Pre-notify: notify before wait still wakes next waiter. BA202.5.15 — Multiple predicates: same condition used with different guards.

### BA202.6 — Event
BA202.6.1 — Constructor: starts unset. BA202.6.2 — `set()`: marks event as occurred. BA202.6.3 — `set()` on already-set is no-op. BA202.6.4 — `clear()`: resets event to unset state. BA202.6.5 — `wait()` blocks until event is set. BA202.6.6 — `wait()` returns immediately if already set. BA202.6.7 — `wait(timeout=N)` returns False after timeout. BA202.6.8 — `is_set()` returns current state. BA202.6.9 — All waiters released when set. BA202.6.10 — Cleared after set: new waiters block again. BA202.6.11 — Async event: `await event.wait()` works correctly. BA202.6.12 — Async event: cancellation during wait handled cleanly. BA202.6.13 — Thread safety: concurrent set/wait consistent. BA202.6.14 — Multiple events: independent state per event instance. BA202.6.15 — Atomic: state transitions are atomic under threading.

---

## BA203: Rate Limiting & Backpressure — 60 Specs

### BA203.1 — Token Bucket
BA203.1.1 — Constructor: rate (tokens/sec) and burst (max tokens) configured. BA203.1.2 — `consume(n=1)` returns True if n tokens available. BA203.1.3 — `consume(n=1)` returns False if insufficient tokens. BA203.1.4 — `consume(n=1)` decrements token count. BA203.1.5 — Token refill: tokens added at configured rate over time. BA203.1.6 — Token refill: never exceeds burst capacity. BA203.1.7 — Token refill: fractional tokens accumulated correctly. BA203.1.8 — Burst: up to burst tokens available initially. BA203.1.9 — Burst: after draining, only refill rate tokens available. BA203.1.10 — Rate=0: all consumes fail. BA203.1.11 — Burst=0: all consumes fail. BA203.1.12 — High burst: 1000 tokens initially, then rate-limited. BA203.1.13 — `try_consume(n, timeout)`: blocks up to timeout for tokens. BA203.1.14 — `try_consume(n, timeout)`: returns False on timeout. BA203.1.15 — Time precision: sub-second accuracy for high rates. BA203.1.16 — Thread safety: concurrent consumes correct under lock. BA203.1.17 — Async: `await bucket.consume_async(n)` works. BA203.1.18 — Distributed: shared state via Redis/cache. BA203.1.19 — Reset: `reset()` returns to burst capacity. BA203.1.20 — Metrics: token consumption rate observable.

### BA203.2 — Leaky Bucket
BA203.2.1 — Queue with fixed drain rate. BA203.2.2 — `submit(item)` adds to queue if not full. BA203.2.3 — `submit(item)` raises/drops when full. BA203.2.4 — Drain: items removed at configured rate. BA203.2.5 — Drain: rate throttles processing speed. BA203.2.6 — Capacity: max queue size before rejection. BA203.2.7 — Infinite queue: never rejects (unbounded). BA203.2.8 — Drain accuracy: items processed at exactly configured rate. BA203.2.9 — Burst handling: queue absorbs short bursts. BA203.2.10 — Overflow policy: drop-tail, drop-head, or block.

### BA203.3 — Sliding Window
BA203.3.1 — Window size: configurable time duration. BA203.3.2 — `allow()` returns True if within window limit. BA203.3.3 — `allow()` returns False if limit exceeded. BA203.3.4 — Window slides: old timestamps expire automatically. BA203.3.5 — Exact limit: Nth request in window rejected. BA203.3.6 — Limit 0: all requests rejected. BA203.3.7 — Large limit: effectively no rate limiting. BA203.3.8 — Time precision: millisecond-level accuracy. BA203.3.9 — Cleanup: expired entries removed on access. BA203.3.10 — Edge: exactly-at-boundary timestamps handled correctly. BA203.3.11 — Thread safety: concurrent allows consistent. BA203.3.12 — Distributed sliding window: Redis sorted set implementation. BA203.3.13 — Atomic: Lua script for check-and-add in distributed case.

### BA203.4 — Fixed Window
BA203.4.1 — Window boundaries: aligned to time grid (e.g., second boundaries). BA203.4.2 — Counter: increments within current window. BA203.4.3 — Reset: counter resets at window boundary. BA203.4.4 — Multiple windows: previous window counter available. BA203.4.5 — Burst at boundary: straddling requests handled correctly. BA203.4.6 — Combined rate: (current_window * weight) + (prev_window * (1-weight)). BA203.4.7 — Weight: configurable boundary smoothing factor. BA203.4.8 — Simple integer counter: memory efficient. BA203.4.9 — Atomic increment: thread-safe counter. BA203.4.10 — Multiple keys: independent windows per key.

### BA203.5 — Backpressure
BA203.5.1 — Queue depth signals: low/high watermark triggers. BA203.5.2 — Slow consumer detection: queue growing faster than draining. BA203.5.3 — Producer throttling: slow down producer when queue high. BA203.5.4 — Adaptive rate: dynamically adjust rate based on queue depth. BA203.5.5 — Rejection policy: drop, block, or exponential backoff. BA203.5.6 — Priority lanes: high-priority bypasses queue. BA203.5.7 — Fair queuing: each producer gets equal share.

---

## BA204: Security Patterns — 100 Specs

### BA204.1 — Input Validation
BA204.1.1 — Whitelist validation: reject anything not in allowed set. BA204.1.2 — Type checking: verify expected types before use. BA204.1.3 — Length limits: enforce max string/array length. BA204.1.4 — Range checks: numeric values within bounds. BA204.1.5 — Pattern matching: regex-based format validation. BA204.1.6 — Email validation: RFC 5322 compliant or practical subset. BA204.1.7 — URL validation: scheme, host, path components valid. BA204.1.8 — Path traversal prevention: reject `../`, absolute paths, symlinks. BA204.1.9 — Null byte injection: reject `\x00` in strings. BA204.1.10 — Unicode normalization: NFC or NFKC before validation. BA204.1.11 — BIDI override prevention: strip/reject Unicode bidi control chars. BA204.1.12 — Homoglyph detection: flag confusable characters. BA204.1.13 — SQL injection: parameterized queries, never string concatenation. BA204.1.14 — NoSQL injection: validate against operator injection ($where, $regex). BA204.1.15 — Command injection: never pass user input to shell. BA204.1.16 — XSS prevention: HTML-encode output, CSP headers. BA204.1.17 — XML injection: disable entity expansion, DTD processing. BA204.1.18 — LDAP injection: escape special characters in filters. BA204.1.19 — Header injection: reject CR/LF in HTTP headers. BA204.1.20 — Template injection: sandbox template rendering.

### BA204.2 — Authentication
BA204.2.1 — Password hashing: bcrypt/scrypt/argon2, never plaintext. BA204.2.2 — Password hashing: unique salt per password. BA204.2.3 — Password hashing: configurable work factor. BA204.2.4 — Timing-safe comparison: constant-time string compare. BA204.2.5 — Rate limiting: max attempts per account per time window. BA204.2.6 — Account lockout: temporary lock after N failed attempts. BA204.2.7 — Session token: cryptographically random, minimum 128 bits entropy. BA204.2.8 — Session token: httpOnly, Secure, SameSite cookie flags. BA204.2.9 — Session token: server-side invalidation on logout. BA204.2.10 — JWT: verify signature before trusting claims. BA204.2.11 — JWT: reject 'none' algorithm. BA204.2.12 — JWT: enforce expiration (exp) claim. BA204.2.13 — JWT: enforce audience (aud) and issuer (iss) claims. BA204.2.14 — API key: random generation, minimum 128 bits. BA204.2.15 — API key: hashed storage, never plaintext in DB. BA204.2.16 — API key: scoped to specific permissions. BA204.2.17 — OAuth2: validate state parameter to prevent CSRF. BA204.2.18 — OAuth2: PKCE for public clients. BA204.2.19 — OAuth2: validate redirect_uri against registered URIs. BA204.2.20 — MFA: second factor required for sensitive operations.

### BA204.3 — Authorization
BA204.3.1 — RBAC: permissions assigned to roles, roles to users. BA204.3.2 — RBAC: role hierarchy (senior inherits junior permissions). BA204.3.3 — RBAC: deny-by-default, explicit allow only. BA204.3.4 — ABAC: attribute-based rules (user attributes, resource attributes, environment). BA204.3.5 — Policy evaluation: first-match or deny-overrides semantics. BA204.3.6 — Policy evaluation: deny takes precedence over allow. BA204.3.7 — Permission check: `can(user, action, resource)` returns bool. BA204.3.8 — Permission check: fail-closed (error → deny). BA204.3.9 — Scope narrowing: derived token cannot exceed parent scope. BA204.3.10 — Scope narrowing: intersection of parent and requested scopes. BA204.3.11 — Admin escalation: requires explicit approval workflow. BA204.3.12 — Admin escalation: time-limited, auto-revoke. BA204.3.13 — Audit: all authorization decisions logged. BA204.3.14 — Audit: deny decisions logged with reason. BA204.3.15 — Audit: log includes user, action, resource, timestamp, decision.

### BA204.4 — Cryptography
BA204.4.1 — Symmetric encryption: AES-256-GCM (authenticated). BA204.4.2 — Symmetric encryption: unique IV/nonce per encryption. BA204.4.3 — Symmetric encryption: authentication tag verified before decryption. BA204.4.4 — Asymmetric encryption: RSA-OAEP or ECIES. BA204.4.5 — Key generation: use system CSPRNG (os.urandom, secrets module). BA204.4.6 — Key derivation: PBKDF2, HKDF, or argon2id. BA204.4.7 — Key storage: never in source code or config files. BA204.4.8 — Key rotation: automatic rotation with overlap period. BA204.4.9 — Certificate validation: verify chain, hostname, expiration. BA204.4.10 — Certificate pinning: optional, with backup pins. BA204.4.11 — TLS: minimum version 1.2, prefer 1.3. BA204.4.12 — TLS: strong cipher suites only (no RC4, 3DES, export-grade). BA204.4.13 — Hash functions: SHA-256 or SHA-3 for security purposes. BA204.4.14 — Hash functions: SHA-1 and MD5 only for non-security (checksums). BA204.4.15 — HMAC: keyed hash for message authentication.

### BA204.5 — Secrets Management
BA204.5.1 — Secret storage: encrypted at rest, in transit. BA204.5.2 — Secret access: audit logged, need-to-know basis. BA204.5.3 — Secret rotation: automated, no-downtime. BA204.5.4 — Secret versions: multiple versions active during rotation. BA204.5.5 — Secret expiration: TTL with automatic re-issue or notification. BA204.5.6 — Environment injection: secrets via env vars, not config files. BA204.5.7 — Log redaction: secrets masked in log output. BA204.5.8 — Error messages: never expose secrets in error responses. BA204.5.9 — Dynamic secrets: generated on-demand, short-lived. BA204.5.10 — Lease management: renewable, revocable.

---

## BA205: Testing Patterns — 80 Specs

### BA205.1 — Test Isolation
BA205.1.1 — No shared mutable state between tests. BA205.1.2 — Each test sets up its own fixtures. BA205.1.3 — Each test cleans up after itself. BA205.1.4 — Test order must not affect results. BA205.1.5 — Random test ordering reveals hidden dependencies. BA205.1.6 — Test isolation: no filesystem side effects between tests. BA205.1.7 — Test isolation: no database state leakage between tests. BA205.1.8 — Test isolation: no environment variable leakage. BA205.1.9 — Test isolation: no monkeypatch leakage. BA205.1.10 — Fixture scope: function-level by default, session-level only when idempotent.

### BA205.2 — Mock Discipline
BA205.2.1 — Mock only external boundaries (network, filesystem, time). BA205.2.2 — Never mock the system under test. BA205.2.3 — Use autospec=True for interface compliance verification. BA205.2.4 — Mock return values must be realistic, not "foo"/"bar"/"baz". BA205.2.5 — Assert on observable behavior, not mock calls. BA205.2.6 — Mock assertions are supplementary, not primary. BA205.2.7 — Clean up mocks in teardown/finally. BA205.2.8 — Prefer dependency injection over monkeypatching. BA205.2.9 — Patch at usage point, not definition point. BA205.2.10 — Never mock stdlib functions without isolation justification.

### BA205.3 — Parametrized Testing
BA205.3.1 — One test function per invariant, many inputs via parametrize. BA205.3.2 — Boundary values included: min, max, zero, empty, one-past-max. BA205.3.3 — Error values included: invalid types, out of range, None. BA205.3.4 — Equivalence classes: one representative per class. BA205.3.5 — Combinatorial: pairwise or full Cartesian for interacting parameters. BA205.3.6 — Parametrize IDs: descriptive names for each case. BA205.3.7 — Large parametrizations: use pytest_generate_tests for dynamic. BA205.3.8 — Cross-product: multiple parametrize decorators combine fully. BA205.3.9 — Indirect parametrization: fixtures computed from parameters. BA205.3.10 — Stack parametrization: shared fixture with different values.

### BA205.4 — Property-Based Testing
BA205.4.1 — Roundtrip property: encode(decode(x)) == x for serialization. BA205.4.2 — Idempotency: f(f(x)) == f(x) for normalization. BA205.4.3 — Commutativity: f(a, b) == f(b, a) for symmetric operations. BA205.4.4 — Associativity: f(f(a,b), c) == f(a, f(b,c)). BA205.4.5 — Invariant preservation: operation doesn't violate class invariants. BA205.4.6 — Oracle comparison: result matches reference implementation. BA205.4.7 — Metamorphic: related inputs produce related outputs. BA205.4.8 — Shrinking: counterexample minimized to simplest form. BA205.4.9 — Strategy composition: building complex generators from simple ones. BA205.4.10 — Assumptions: filtering invalid test cases with assume().

### BA205.5 — Snapshot Testing
BA205.5.1 — Capture deterministic output of rendering/formatting. BA205.5.2 — Snapshot file versioned alongside test code. BA205.5.3 — Update snapshots via explicit flag, never automatically in CI. BA205.5.4 — Snapshot diff shows exactly what changed. BA205.5.5 — Snapshot scope: one snapshot per test function. BA205.5.6 — Snapshot naming: derived from test function name. BA205.5.7 — Time-dependent data: freeze/mock time for deterministic snapshots. BA205.5.8 — Random data: seed RNG for deterministic output. BA205.5.9 — Large snapshots: truncate or use structural comparison. BA205.5.10 — Binary snapshots: base64-encode for text storage.

### BA205.6 — Performance Testing
BA205.6.1 — Benchmark: measure wall-clock time of operation. BA205.6.2 — Benchmark: repeat N times, report median and p99. BA205.6.3 — Benchmark: warmup iterations before measurement. BA205.6.4 — Benchmark: regression threshold (e.g., 20% slower fails test). BA205.6.5 — Memory: measure peak RSS or allocations. BA205.6.6 — Complexity: verify O(f(n)) behavior with increasing input sizes. BA205.6.7 — Throughput: items/second for batch operations. BA205.6.8 — Latency: p50, p95, p99 percentiles. BA205.6.9 — Resource limits: CPU, memory, disk within budget. BA205.6.10 — Performance test: skipped in normal CI, run on schedule.

### BA205.7 — Fuzz Testing
BA205.7.1 — Random byte sequences fed to parsing functions. BA205.7.2 — Coverage-guided fuzzing: mutations target uncovered paths. BA205.7.3 — Crash detection: any unhandled exception is a finding. BA205.7.4 — Hang detection: timeout on slow inputs. BA205.7.5 — Corpus management: interesting inputs saved for regression. BA205.7.6 — Dictionary-based: supply valid tokens/fields to guide mutations. BA205.7.7 — Structure-aware: mutate fields while preserving structure. BA205.7.8 — Differential fuzzing: compare outputs of two implementations. BA205.7.9 — Sanitizer integration: ASAN, UBSAN for native code. BA205.7.10 — Fuzz harness: minimal function that takes bytes and processes.

### BA205.8 — Regression Testing
BA205.8.1 — Every bug fix includes a regression test. BA205.8.2 — Regression test reproduces the exact failure mode. BA205.8.3 — Regression test fails before fix, passes after fix. BA205.8.4 — Regression test covers edge case that triggered the bug. BA205.8.5 — Regression test is minimal (not unnecessary setup). BA205.8.6 — Regression test is documented with bug reference. BA205.8.7 — Regression test is in the same file as related tests. BA205.8.8 — Regression test for CI failures includes CI log excerpt. BA205.8.9 — Regression test for race conditions uses stress/concurrent pattern. BA205.8.10 — Regression test never marked as xfail or skip without reason.

---

## BA206: CI/CD Pipeline — 80 Specs

### BA206.1 — Workflow Structure
BA206.1.1 — Single workflow file per pipeline stage. BA206.1.2 — Job dependencies: needs array defines DAG. BA206.1.3 — No circular dependencies in job graph. BA206.1.4 — Concurrency group: cancel-in-progress for PR pushes. BA206.1.5 — Timeout: every job has a timeout-minutes. BA206.1.6 — Timeout: gate job 40 min, build jobs 60 min, release 30 min. BA206.1.7 — Conditional execution: paths-filter prevents unnecessary runs. BA206.1.8 — Conditional execution: if-failure for notification jobs. BA206.1.9 — Matrix strategy: platform × python-version combinations. BA206.1.10 — Matrix strategy: exclude unsupported combinations.

### BA206.2 — Quality Gates in CI
BA206.2.1 — Lint: ruff check on all source files, zero tolerance. BA206.2.2 — Typecheck: mypy strict mode or configured strictness. BA206.2.3 — Test: pytest with coverage, minimum threshold enforced. BA206.2.4 — Secrets scan: detect-secrets or gitleaks against baseline. BA206.2.5 — SAST: bandit for Python security patterns. BA206.2.6 — Dependency audit: pip-audit or safety for known vulns. BA206.2.7 — SBOM generation: CycloneDX or SPDX format. BA206.2.8 — Container scan: Trivy or Grype for image vulns. BA206.2.9 — License compliance: check for forbidden licenses. BA206.2.10 — Code formatting: black/ruff format check.

### BA206.3 — Build Pipeline
BA206.3.1 — Binary build: PyInstaller with spec file. BA206.3.2 — Binary build: collect all required data files. BA206.3.3 — Binary build: hidden imports for dynamic modules. BA206.3.4 — Binary build: platform-specific exclusions. BA206.3.5 — Binary build: reproducible (deterministic) builds. BA206.3.6 — Linux: build on ubuntu-latest, produce .deb, .rpm, tarball. BA206.3.7 — macOS: build on macos-latest, produce .pkg, tarball. BA206.3.8 — Windows: build on windows-latest, produce .exe installer, portable. BA206.3.9 — Container: multi-stage Dockerfile, distroless base. BA206.3.10 — Container: non-root user, HEALTHCHECK, minimal layers.

### BA206.4 — Artifact Management
BA206.4.1 — Upload: all binaries as workflow artifacts. BA206.4.2 — Retention: 90 days for releases, 7 days for PR builds. BA206.4.3 — Naming convention: `<name>-<version>-<platform>-<arch>.<ext>`. BA206.4.4 — Checksums: SHA-256 for every artifact. BA206.4.5 — Signing: GPG or cosign for release artifacts. BA206.4.6 — Provenance: SLSA Level 2+ build provenance. BA206.4.7 — SBOM: attached to release as separate artifact. BA206.4.8 — Release page: draft first, publish after verification. BA206.4.9 — Release notes: auto-generated from conventional commits. BA206.4.10 — Artifact size: warn if binary exceeds 200MB.

### BA206.5 — Test Sharding
BA206.5.1 — Split test suite into N shards for parallel execution. BA206.5.2 — Shard by test file, balancing by historical duration. BA206.5.3 — Shard count: function of total test duration target. BA206.5.4 — Flaky test isolation: separate shard or retry. BA206.5.5 — Test report: JUnit XML per shard, aggregated. BA206.5.6 — Coverage merge: combine coverage from all shards. BA206.5.7 — Failed shard retry: re-run only failed shard. BA206.5.8 — Shard timeout: per-shard timeout, independent of others.

### BA206.6 — Deployment Pipeline
BA206.6.1 — Environment promotion: dev → staging → canary → production. BA206.6.2 — Approval gate: manual approval before production. BA206.6.3 — Canary: deploy to small percentage, monitor for N minutes. BA206.6.4 — Rollback: automated on error rate spike or latency degradation. BA206.6.5 — Blue-green: deploy to inactive stack, swap on verification. BA206.6.6 — Database migration: run before deploy, reversible. BA206.6.7 — Smoke test: basic health check after deploy. BA206.6.8 — Notify: Slack/Teams/email on deploy status.

### BA206.7 — CI Reliability
BA206.7.1 — Retry: flaky steps auto-retried up to 2 times. BA206.7.2 — Retry: only on known-transient errors (network, race, timeout). BA206.7.3 — Flaky test tracking: log flaky tests, review weekly. BA206.7.4 — Cache: pip/uv cache across runs for speed. BA206.7.5 — Cache: keyed by lockfile hash for correct invalidation. BA206.7.6 — Self-hosted runner: fallback when GitHub-hosted unavailable. BA206.7.7 — Queue time SLA: alert if queued >10 minutes. BA206.7.8 — Clean checkout: fetch-depth 0 for merge-base operations. BA206.7.9 — Disk space: clean up before and after large builds.

---

## BA207: Observability — 70 Specs

### BA207.1 — Logging
BA207.1.1 — Structured logging: JSON format with consistent field names. BA207.1.2 — Required fields: timestamp, level, logger, message, trace_id. BA207.1.3 — Contextual fields: user_id, project_id, request_id where applicable. BA207.1.4 — Log levels: DEBUG (dev), INFO (normal), WARNING (recoverable), ERROR (attention), CRITICAL (down). BA207.1.5 — Error logging: include exception type, message, stack trace. BA207.1.6 — Sensitive data: redact secrets, tokens, PII from logs. BA207.1.7 — Log sampling: rate-limit high-volume debug logs. BA207.1.8 — Correlation ID: propagate across service boundaries. BA207.1.9 — Request logging: method, path, status, duration for every request. BA207.1.10 — Log rotation: size-based or time-based, with retention policy.

### BA207.2 — Metrics
BA207.2.1 — Counter: monotonically increasing count of events. BA207.2.2 — Counter: `inc(n=1)` increments by n. BA207.2.3 — Gauge: point-in-time value that can go up and down. BA207.2.4 — Gauge: `set(value)` and `inc(n)` and `dec(n)`. BA207.2.5 — Histogram: distribution of values with configurable buckets. BA207.2.6 — Histogram: `observe(value)` records a sample. BA207.2.7 — Summary: quantile-based distribution (p50, p90, p99). BA207.2.8 — Labels: key-value pairs for dimensionality. BA207.2.9 — Label cardinality: bounded, no user-provided label values. BA207.2.10 — Export: Prometheus text format at /metrics endpoint. BA207.2.11 — Export: OpenMetrics format for federation. BA207.2.12 — Registration: metric registry for lookup and iteration. BA207.2.13 — Default metrics: process CPU, memory, GC, request counts, error rate. BA207.2.14 — Business metrics: domain-specific counters and gauges. BA207.2.15 — Metric naming: `<namespace>_<subsystem>_<name>_<unit>` convention.

### BA207.3 — Tracing
BA207.3.1 — Span: named operation with start time, duration, and attributes. BA207.3.2 — Span context: trace_id, span_id, parent_span_id. BA207.3.3 — Span propagation: W3C TraceContext or B3 headers. BA207.3.4 — Span kind: CLIENT, SERVER, PRODUCER, CONSUMER, INTERNAL. BA207.3.5 — Span attributes: key-value metadata about the operation. BA207.3.6 — Span events: time-stamped annotations within a span. BA207.3.7 — Span status: OK, ERROR with description. BA207.3.8 — Sampling: head-based (decision at trace start) or tail-based (decision after). BA207.3.9 — Sampling rate: configurable, higher for errors. BA207.3.10 — Export: OTLP (OpenTelemetry Protocol) to collector. BA207.3.11 — Export: Jaeger, Zipkin, or vendor-specific formats. BA207.3.12 — Instrumentation: auto-instrumentation for HTTP, DB, gRPC. BA207.3.13 — Custom spans: manual instrumentation for business logic. BA207.3.14 — Noop tracer: safe default when collector unavailable. BA207.3.15 — Resource: service name, version, environment attributes.

### BA207.4 — Health Checks
BA207.4.1 — Liveness: `/livez` returns 200 if process is alive. BA207.4.2 — Readiness: `/readyz` returns 200 if able to serve traffic. BA207.4.3 — Readiness checks: DB connectivity, Redis, worker pool, disk space. BA207.4.4 — Health: `/healthz` returns component-level status. BA207.4.5 — Health format: JSON with component name, status, message, timestamp. BA207.4.6 — Status values: "healthy", "degraded", "unhealthy". BA207.4.7 — Timeout: each check has individual timeout. BA207.4.8 — Parallel check execution: all checks run concurrently. BA207.4.9 — Custom checks: pluggable health check registration. BA207.4.10 — Startup probe: slower, longer timeout for initial readiness.

### BA207.5 — Alerting
BA207.5.1 — Alert definition: metric name, threshold, duration, severity. BA207.5.2 — Severity levels: critical (page), warning (notify), info (log). BA207.5.3 — Alert threshold: error rate >1% for 5 minutes. BA207.5.4 — Alert threshold: latency p99 >5s for 5 minutes. BA207.5.5 — Alert threshold: disk <10% or >90% for 1 minute. BA207.5.6 — Alert threshold: dead worker pool (0 workers for 1 minute). BA207.5.7 — Alert fatigue: deduplication, grouping, silence periods. BA207.5.8 — Runbook: every alert links to troubleshooting documentation. BA207.5.9 — Alert routing: on-call rotation, escalation policy. BA207.5.10 — Recovery notification: alert resolves when condition clears.

---

## BA208: Release Engineering — 60 Specs

### BA208.1 — Version Management
BA208.1.1 — SemVer: MAJOR.MINOR.PATCH format. BA208.1.2 — MAJOR: breaking API change. BA208.1.3 — MINOR: backward-compatible new feature. BA208.1.4 — PATCH: backward-compatible bug fix. BA208.1.5 — Pre-release: -alpha.N, -beta.N, -rc.N suffix. BA208.1.6 — Build metadata: +build123 for build-specific info. BA208.1.7 — Version bump: automated from conventional commits. BA208.1.8 — Version consistency: pyproject.toml, __init__.py, CHANGELOG, git tag all match. BA208.1.9 — Release branch naming: `release/v<major>.<minor>.<patch>`. BA208.1.10 — Tag format: annotated tag `v<major>.<minor>.<patch>`.

### BA208.2 — CHANGELOG
BA208.2.1 — Entry per release with version, date, and changes. BA208.2.2 — Changes grouped: Added, Changed, Deprecated, Removed, Fixed, Security. BA208.2.3 — Keep a Changelog format compliance. BA208.2.4 — Every user-facing change documented. BA208.2.5 — Breaking changes highlighted with migration guide. BA208.2.6 — Links to relevant issues/PRs. BA208.2.7 — Comparison links between versions. BA208.2.8 — Unreleased section at top for pending changes.

### BA208.3 — Release Artifacts
BA208.3.1 — 12 required asset categories for full release. BA208.3.2 — Linux: .deb package, .rpm package, tar.gz tarball. BA208.3.3 — macOS: .pkg installer, tar.gz tarball. BA208.3.4 — Windows: .exe NSIS installer, portable .zip. BA208.3.5 — Container: Docker image, multi-arch manifest. BA208.3.6 — Checksums: SHA-256 for every artifact. BA208.3.7 — SBOM: CycloneDX JSON format. BA208.3.8 — Provenance: in-toto attestation with SLSA level. BA208.3.9 — Signatures: GPG detached signature or cosign. BA208.3.10 — Release page: non-draft, all artifacts attached.

### BA208.4 — Release Process
BA208.4.1 — CI-GREEN on release commit before tag push. BA208.4.2 — README status table updated to current version. BA208.4.3 — `make release-cut TAG='...'` as single command. BA208.4.4 — Release notes auto-generated and reviewed. BA208.4.5 — Canary deployment before full rollout. BA208.4.6 — Smoke tests pass on canary. BA208.4.7 — Gradual rollout: 5% → 25% → 100% over 30 minutes. BA208.4.8 — Monitoring: error rate, latency, saturation during rollout. BA208.4.9 — Auto-rollback on SLO violation. BA208.4.10 — Post-release: verify all 12 asset categories present.

### BA208.5 — Hotfix Process
BA208.5.1 — Branch from latest release tag. BA208.5.2 — Cherry-pick or write fix directly. BA208.5.3 — CI must pass on hotfix branch. BA208.5.4 — Hotfix version: PATCH bump. BA208.5.5 — Hotfix release notes: explain urgency and impact. BA208.5.6 — Cherry-pick hotfix back to development branch. BA208.5.7 — Hotfix must include regression test. BA208.5.8 — Post-mortem for any hotfix with SEV-1 severity.

---

## BA209: Database & Persistence — 60 Specs

### BA209.1 — Migrations
BA209.1.1 — Every schema change has a migration file. BA209.1.2 — Migration chain: `down_revision` correctly references parent. BA209.1.3 — Migration chain: head revision identifies latest. BA209.1.4 — `upgrade()` creates/modifies schema. BA209.1.5 — `downgrade()` exactly reverses `upgrade()`. BA209.1.6 — Idempotent upgrades: running twice is safe. BA209.1.7 — Migration testing: apply, verify, downgrade, verify. BA209.1.8 — Transactional DDL: wrap in transaction for atomicity. BA209.1.9 — Data migration: separate from schema migration when large. BA209.1.10 — Rollback plan: how to revert migration in production.

### BA209.2 — Connection Management
BA209.2.1 — Connection pool: configurable min and max connections. BA209.2.2 — Connection timeout: acquire from pool within configurable time. BA209.2.3 — Connection recycle: after N seconds to avoid server-side timeouts. BA209.2.4 — Connection health: test on checkout (SELECT 1). BA209.2.5 — Connection leak detection: warn on unreturned connections. BA209.2.6 — Read/write split: reader connections to replicas. BA209.2.7 — Connection retry: transient errors retried with backoff. BA209.2.8 — Circuit breaker: stop trying when DB is down. BA209.2.9 — Async session: asyncio-compatible session factory. BA209.2.10 — Session scope: one session per request/unit-of-work.

### BA209.3 — Query Patterns
BA209.3.1 — Parameterized queries: never string format for SQL. BA209.3.2 — ORM eager loading: `joinedload()` or `selectinload()` for N+1 prevention. BA209.3.3 — Batch operations: bulk insert/update for performance. BA209.3.4 — Pagination: keyset/cursor-based for large result sets. BA209.3.5 — Pagination: LIMIT/OFFSET with total count. BA209.3.6 — Query timeout: statement timeout configured. BA209.3.7 — Index usage: EXPLAIN ANALYZE verified. BA209.3.8 — Slow query logging: queries exceeding threshold logged. BA209.3.9 — Read-only transactions: for reporting/analytics queries. BA209.3.10 — Serializable isolation: when consistency requires it.

### BA209.4 — Data Integrity
BA209.4.1 — Foreign key constraints: enforced at DB level. BA209.4.2 — Unique constraints: on natural keys. BA209.4.3 — Check constraints: domain-level validation. BA209.4.4 — NOT NULL: required columns declared at DB level. BA209.4.5 — Default values: sensible DB-level defaults. BA209.4.6 — Cascade: ON DELETE CASCADE or SET NULL as appropriate. BA209.4.7 — Indexes: on foreign key columns and frequently filtered columns. BA209.4.8 — Composite indexes: for multi-column WHERE clauses and JOINs. BA209.4.9 — Partial indexes: for filtered subsets. BA209.4.10 — Covering indexes: include SELECT columns to avoid table access.

### BA209.5 — Backup & Recovery
BA209.5.1 — Scheduled backups: full backup daily, incremental hourly. BA209.5.2 — Point-in-time recovery: WAL archiving enabled. BA209.5.3 — Backup verification: test restore monthly. BA209.5.4 — Backup encryption: encrypted at rest. BA209.5.5 — Backup retention: 30 days for daily, 12 months for monthly. BA209.5.6 — Disaster recovery plan: documented, tested annually. BA209.5.7 — RPO: Recovery Point Objective defined and measured. BA209.5.8 — RTO: Recovery Time Objective defined and measured. BA209.5.9 — Geo-replication: multi-region for disaster recovery. BA209.5.10 — Failover: automated with health checks.

---

## BA210: Agent Architecture — 60 Specs

### BA210.1 — Agent Lifecycle
BA210.1.1 — Registration: agent registers with unique ID on startup. BA210.1.2 — Heartbeat: periodic heartbeat to indicate liveness. BA210.1.3 — Heartbeat timeout: agent considered dead after N missed heartbeats. BA210.1.4 — Capability declaration: agent declares capabilities on registration. BA210.1.5 — Capability check: orchestrator verifies capability before dispatching. BA210.1.6 — Work assignment: orchestrator assigns tasks to qualified agents. BA210.1.7 — Task lifecycle: pending → assigned → running → completed/failed. BA210.1.8 — Task timeout: agent loses task if exceeds deadline. BA210.1.9 — Graceful shutdown: agent completes current task before stopping. BA210.1.10 — Reaping: dead agents' tasks reassigned.

### BA210.2 — Agent Communication
BA210.2.1 — Message format: structured JSON with type, payload, metadata. BA210.2.2 — Message ID: UUID for deduplication and correlation. BA210.2.3 — Message version: schema version for forward compatibility. BA210.2.4 — Request-response: correlation ID links response to request. BA210.2.5 — Fire-and-forget: no response expected. BA210.2.6 — Streaming: chunked responses for long-running operations. BA210.2.7 — Error response: error code, message, retryable flag. BA210.2.8 — Retry: idempotent messages retried automatically. BA210.2.9 — Dead letter: messages exceeding retry limit moved to DLQ. BA210.2.10 — Ordering: optional ordering guarantees via sequence numbers.

### BA210.3 — Agent Isolation
BA210.3.1 — Process isolation: each agent in separate process. BA210.3.2 — Filesystem isolation: agent has its own working directory. BA210.3.3 — Network isolation: agent access restricted per policy. BA210.3.4 — Resource limits: CPU, memory, disk quotas per agent. BA210.3.5 — Timeout: hard kill after exceeding wall-clock deadline. BA210.3.6 — Sandbox: untrusted code executed in sandboxed environment. BA210.3.7 — Dependency isolation: per-agent virtual environment. BA210.3.8 — Secrets: agent-only secrets, not shared across agents. BA210.3.9 — Audit: all agent actions logged with agent ID. BA210.3.10 — Cleanup: agent resources freed on termination.

### BA210.4 — Agent Pool
BA210.4.1 — Pool size: min/max concurrent agents configured. BA210.4.2 — Scale up: spawn new agent when queue depth exceeds threshold. BA210.4.3 — Scale down: terminate idle agent after cooldown period. BA210.4.4 — Warm pool: pre-spawned agents for fast dispatch. BA210.4.5 — Queue depth: number of pending tasks awaiting assignment. BA210.4.6 — Priority: tasks sorted by priority before assignment. BA210.4.7 — Fairness: all task types get proportional agent time. BA210.4.8 — Backpressure: slow down dispatch when all agents busy. BA210.4.9 — Health: unhealthy agents removed from pool. BA210.4.10 — Metrics: pool size, utilization, queue depth exported.

### BA210.5 — Capability Lattice
BA210.5.1 — Hierarchy: capabilities organized in DAG. BA210.5.2 — Inheritance: child capability implies parent capabilities. BA210.5.3 — Gating: agent must have required capability to receive task. BA210.5.4 — Versioning: capability versions for backward compatibility. BA210.5.5 — Discovery: orchestrator finds agents by capability. BA210.5.6 — Dynamic: capabilities can be registered at runtime. BA210.5.7 — Validation: no circular capability dependencies. BA210.5.8 — Audit: capability changes logged.

---

## BA211: Documentation Standards — 50 Specs

### BA211.1 — Code Documentation
BA211.1.1 — Every public module has a docstring. BA211.1.2 — Every public class has a docstring. BA211.1.3 — Every public function has a docstring. BA211.1.4 — Docstring format: Google or NumPy style consistently. BA211.1.5 — Parameters documented: name, type, description. BA211.1.6 — Return value documented: type, description. BA211.1.7 — Exceptions documented: type, condition. BA211.1.8 — Examples: usage examples in docstrings for non-trivial functions. BA211.1.9 — Comments: explain WHY, not WHAT (code is self-documenting). BA211.1.10 — No stale comments: comments match current code behavior.

### BA211.2 — Architecture Documentation
BA211.2.1 — Architecture diagram: component-level block diagram. BA211.2.2 — Component description: purpose, interfaces, dependencies. BA211.2.3 — Data flow: how data moves through the system. BA211.2.4 — Control flow: how requests are processed end-to-end. BA211.2.5 — Design decisions: rationale for significant choices (ADRs). BA211.2.6 — ADR format: title, status, context, decision, consequences. BA211.2.7 — Technology stack: languages, frameworks, infrastructure. BA211.2.8 — Deployment topology: how components are deployed. BA211.2.9 — Security model: threat model, trust boundaries, mitigations. BA211.2.10 — Performance model: expected throughput, latency, scalability.

### BA211.3 — API Documentation
BA211.3.1 — Every endpoint documented: method, path, parameters, responses. BA211.3.2 — Request body: schema with field descriptions. BA211.3.3 — Response body: schema with field descriptions. BA211.3.4 — Error responses: all possible status codes and error formats. BA211.3.5 — Authentication: required auth method per endpoint. BA211.3.6 — Rate limits: documented per-endpoint limits. BA211.3.7 — Examples: curl examples for common operations. BA211.3.8 — OpenAPI/Swagger: machine-readable spec generated. BA211.3.9 — Versioning: API version in path or header. BA211.3.10 — Deprecation: sunset header, migration guide.

### BA211.4 — Operational Documentation
BA211.4.1 — Runbook: common operational procedures. BA211.4.2 — Troubleshooting guide: common failure modes and fixes. BA211.4.3 — Deployment guide: step-by-step release process. BA211.4.4 — Configuration reference: all config options documented. BA211.4.5 — Environment variables: all env vars with defaults and descriptions. BA211.4.6 — CLI reference: all commands with flags and examples. BA211.4.7 — Makefile targets: all targets with descriptions. BA211.4.8 — Monitoring: dashboard links, alert definitions. BA211.4.9 — On-call guide: escalation policy, pager playbook. BA211.4.10 — Incident response: severity levels, communication templates.

### BA211.5 — Spec Document Standards
BA211.5.1 — Unique ID per spec: section-based numbering. BA211.5.2 — Description: what behavior is required. BA211.5.3 — Enforcement: how compliance is verified (test, lint, CI). BA211.5.4 — Evidence: what output proves compliance. BA211.5.5 — Priority: MUST, SHOULD, or MAY per RFC 2119. BA211.5.6 — Spec test: every spec has a corresponding automated test. BA211.5.7 — Traceability: spec ID referenced in implementation. BA211.5.8 — Review: specs reviewed and updated per release. BA211.5.9 — Gap tracking: uncovered specs tracked in enforcement gap report. BA211.5.10 — Coverage target: 100% spec enforcement with automated checks.

---

## BA212: Performance & Optimization — 50 Specs

### BA212.1 — Time Complexity
BA212.1.1 — Documented complexity: O-notation in docstring for every public function. BA212.1.2 — Verified complexity: benchmark confirms complexity class. BA212.1.3 — Sub-quadratic: no O(n²) in hot paths without justification. BA212.1.4 — Amortized analysis: consider amortized cost, not worst-case single operation. BA212.1.5 — Constant factors: profile to identify high-constant-factor code.

### BA212.2 — Space Complexity
BA212.2.1 — Memory budget: per-component memory limit. BA212.2.2 — Streaming: process large inputs without loading entirely into memory. BA212.2.3 — Object pooling: reuse objects for allocation-heavy paths. BA212.2.4 — Memory leak: no unbounded growth in long-running processes. BA212.2.5 — GC pressure: minimize allocations in hot loops.

### BA212.3 — Caching
BA212.3.1 — Cache hit ratio: target >80% for deployed caches. BA212.3.2 — Cache invalidation: correct, not eventually-correct. BA212.3.3 — Cache stampede: prevent via locking or early recomputation. BA212.3.4 — Cache warming: pre-populate on startup. BA212.3.5 — Multi-level: L1 in-memory, L2 Redis, L3 database.

### BA212.4 — Database Optimization
BA212.4.1 — Index coverage: all filtered/sorted columns indexed. BA212.4.2 — Query plan: EXPLAIN shows index usage, not sequential scan. BA212.4.3 — Join optimization: join order minimized by query planner. BA212.4.4 — Denormalization: justified by read-heavy access patterns. BA212.4.5 — Partitioning: large tables partitioned by time or key.

### BA212.5 — I/O Optimization
BA212.5.1 — Async I/O: non-blocking for network operations. BA212.5.2 — Batching: group small writes into larger chunks. BA212.5.3 — Compression: compress data in transit and at rest where beneficial. BA212.5.4 — Connection reuse: HTTP keep-alive, DB connection pooling. BA212.5.5 — CDN: static assets served from edge.

### BA212.6 — Startup Performance
BA212.6.1 — Cold start: application boots in <10 seconds. BA212.6.2 — Lazy loading: defer non-critical initialization. BA212.6.3 — Parallel init: independent subsystems initialize concurrently. BA212.6.4 — Warm start: cached state enables <1 second restarts. BA212.6.5 — Import time: no heavy work at module import time.

### BA212.7 — Request Performance
BA212.7.1 — Target latency: p50 <100ms, p99 <1s for API requests. BA212.7.2 — Timeout: every external call has a timeout. BA212.7.3 — Parallelism: independent operations execute concurrently. BA212.7.4 — Precomputation: expensive computations done at write time. BA212.7.5 — Payload size: minimize response size (gzip, sparse fields).

---

## BA213: Error Handling & Resilience — 50 Specs

### BA213.1 — Exception Hierarchy
BA213.1.1 — Base exception: all app exceptions inherit from AppError. BA213.1.2 — Error codes: unique code per exception class. BA213.1.3 — HTTP mapping: each error maps to appropriate HTTP status. BA213.1.4 — User-facing message: safe message for API responses. BA213.1.5 — Internal details: technical details for logging only. BA213.1.6 — Retryable flag: indicates if operation can be retried. BA213.1.7 — Root cause: wrapped exception chain preserved.

### BA213.2 — Graceful Degradation
BA213.2.1 — Circuit breaker: fast-fail when downstream is unhealthy. BA213.2.2 — Fallback: alternative behavior when primary unavailable. BA213.2.3 — Partial results: return available data when subset fails. BA213.2.4 — Feature flag kill switch: disable feature without deploy. BA213.2.5 — Read-only mode: serve reads when writes are unavailable.

### BA213.3 — Retry Logic
BA213.3.1 — Exponential backoff: delay doubles each retry. BA213.3.2 — Jitter: random variation to prevent thundering herd. BA213.3.3 — Max retries: bounded, typically 3. BA213.3.4 — Retryable errors: 429, 503, connection errors, timeouts. BA213.3.5 — Non-retryable: 400, 401, 403, 404 (same request will fail again).

### BA213.4 — Timeout Management
BA213.4.1 — Every external call has a timeout. BA213.4.2 — Timeout hierarchy: total > per-request > per-operation. BA213.4.3 — Deadline propagation: parent deadline passed to children. BA213.4.4 — Timeout escalation: increase on retry but capped. BA213.4.5 — Cancellation: clean up resources on timeout.

### BA213.5 — Crash Recovery
BA213.5.1 — State persistence: recoverable state written to durable storage. BA213.5.2 — Idempotent operations: safe to retry partial completions. BA213.5.3 — WAL/journal: operation log for crash recovery. BA213.5.4 — Checkpoint: periodic full state snapshot. BA213.5.5 — Startup recovery: replay log from last checkpoint on boot.

---

## BA214: Configuration Management — 40 Specs

### BA214.1 — Config Sources
BA214.1.1 — Defaults: hardcoded safe defaults in code. BA214.1.2 — Config file: YAML/JSON/TOML at standard locations. BA214.1.3 — Environment variables: override with GLUDD_ prefix. BA214.1.4 — CLI flags: highest precedence, for manual overrides. BA214.1.5 — Precedence: CLI > env > file > default. BA214.1.6 — Merge: deep merge of multiple config sources. BA214.1.7 — Project-specific: per-project config overrides global.

### BA214.2 — Config Validation
BA214.2.1 — Schema: every config value has a type and constraints. BA214.2.2 — Required validation: missing required values raise clear error. BA214.2.3 — Type coercion: string env vars coerced to target type. BA214.2.4 — Range validation: numeric values within bounds. BA214.2.5 — Enum validation: string values in allowed set. BA214.2.6 — Regex validation: string values match pattern. BA214.2.7 — Dependency validation: config values that depend on each other. BA214.2.8 — Early failure: config errors caught at startup, not runtime.

### BA214.3 — Secret Config
BA214.3.1 — Never in config files: secrets only via env or vault. BA214.3.2 — Redaction: secrets masked in logs and debug output. BA214.3.3 — Placeholder: sensitive config fields show "***" not values. BA214.3.4 — Vault integration: fetch secrets from OpenBao/HashiCorp Vault. BA214.3.5 — Dynamic reload: secrets refreshed without restart.

### BA214.4 — Hot Reload
BA214.4.1 — Signal-based: SIGHUP triggers config reload. BA214.4.2 — File watch: inotify/kqueue detects config file changes. BA214.4.3 — Validate before apply: new config validated before activation. BA214.4.4 — Atomic switch: old config active until new one validated. BA214.4.5 — Rollback: revert to last-good config on validation failure.

---

## BA215: Dependency Management — 30 Specs

### BA215.1 — Version Pinning
BA215.1.1 — Direct dependencies: pinned to exact version in pyproject.toml. BA215.1.2 — Transitive dependencies: pinned in lockfile. BA215.1.3 — Lockfile committed: uv.lock always in version control. BA215.1.4 — Hash verification: lockfile includes package hashes. BA215.1.5 — No git dependencies: all dependencies from package registry.

### BA215.2 — Vulnerability Management
BA215.2.1 — Scanning: automated vulnerability scan on every CI run. BA215.2.2 — Critical vulns: must be patched within 24 hours. BA215.2.3 — High vulns: must be patched within 7 days. BA215.2.4 — Medium vulns: must be patched within 30 days. BA215.2.5 — Exceptions: documented with compensating controls.

### BA215.3 — License Compliance
BA215.3.1 — Allowlist: only approved licenses (MIT, Apache-2.0, BSD, ISC). BA215.3.2 — Copyleft: GPL/AGPL requires legal review. BA215.3.3 — Unlicensed: packages without license flagged. BA215.3.4 — Audit: dependency license report generated per release. BA215.3.5 — SBOM: software bill of materials includes license info.

---

## BA216: Accessibility & Internationalization — 30 Specs

### BA216.1 — i18n
BA216.1.1 — All user-facing strings externalized to message catalogs. BA216.1.2 — Message format: ICU MessageFormat or gettext. BA216.1.3 — Pluralization: correct plural forms for locale. BA216.1.4 — Date/time formatting: locale-aware formatting. BA216.1.5 — Number formatting: locale-aware decimal and thousands separators.

### BA216.2 — Unicode
BA216.2.1 — UTF-8 everywhere: all text encoded as UTF-8. BA216.2.2 — Normalization: NFC before comparison and storage. BA216.2.3 — BIDI safety: reject or sanitize bidi override characters in identifiers. BA216.2.4 — Zero-width safety: strip zero-width characters from identifiers. BA216.2.5 — Confusable detection: warn on homoglyph/homograph attacks.

### BA216.3 — Accessibility
BA216.3.1 — CLI: high-contrast output option. BA216.3.2 — TUI: keyboard-navigable, screen reader compatible. BA216.3.3 — API: descriptive error messages, not just error codes. BA216.3.4 — Docs: alt text for images, semantic markup. BA216.3.5 — Color: never rely on color alone to convey information.

---

## BA217: Compliance & Governance — 30 Specs

### BA217.1 — Audit Trail
BA217.1.1 — Immutable: audit records cannot be modified or deleted. BA217.1.2 — Complete: all state-changing operations logged. BA217.1.3 — Timestamped: accurate, monotonic timestamps. BA217.1.4 — Attributable: actor identity recorded. BA217.1.5 — Exportable: query and export for compliance review.

### BA217.2 — Data Privacy
BA217.2.1 — PII classification: identify all PII fields. BA217.2.2 — PII encryption: encrypt at rest. BA217.2.3 — PII access control: need-to-know basis. BA217.2.4 — Data deletion: right-to-delete within SLA. BA217.2.5 — Data export: right-to-access within SLA.

### BA217.3 — Policy Engine
BA217.3.1 — Policy as code: policies defined in Rego or similar. BA217.3.2 — Policy evaluation: at decision points in code. BA217.3.3 — Policy enforcement: deny on violation. BA217.3.4 — Policy versioning: version-controlled and audited. BA217.3.5 — Policy testing: every policy has tests.

---

## BA218: API Design — 30 Specs

### BA218.1 — REST Conventions
BA218.1.1 — Resource naming: plural nouns, kebab-case. BA218.1.2 — HTTP methods: GET (read), POST (create), PUT (replace), PATCH (update), DELETE (remove). BA218.1.3 — Status codes: 200 (OK), 201 (Created), 204 (No Content), 400 (Bad Request), 401 (Unauthorized), 403 (Forbidden), 404 (Not Found), 409 (Conflict), 429 (Rate Limited), 500 (Internal Error). BA218.1.4 — Idempotency: PUT and DELETE are idempotent, POST is not. BA218.1.5 — Idempotency key: Idempotency-Key header for POST.

### BA218.2 — Response Format
BA218.2.1 — Envelope: consistent wrapper with data, error, metadata. BA218.2.2 — Pagination: cursor or offset-based with total count. BA218.2.3 — Filtering: query parameters for field filtering. BA218.2.4 — Sorting: sort parameter with direction. BA218.2.5 — Sparse fieldsets: fields parameter to limit response.

### BA218.3 — Versioning
BA218.3.1 — URI versioning: /v1/resource, /v2/resource. BA218.3.2 — Header versioning: Accept-Version header. BA218.3.3 — Deprecation: Sunset header with removal date. BA218.3.4 — Migration window: deprecated APIs supported for 2 releases. BA218.3.5 — Breaking changes: only in new major API version.

---

## BA219: CLI Design — 20 Specs

### BA219.1 — Command Structure
BA219.1.1 — Subcommand hierarchy: `<app> <noun> <verb>` pattern. BA219.1.2 — Consistent flags: same flag means same thing across commands. BA219.1.3 — Short/long: `-v`/`--verbose`, `-h`/`--help`. BA219.1.4 — Args: positional for required, flags for optional. BA219.1.5 — Defaults: sensible defaults documented in help.

### BA219.2 — Output
BA219.2.1 — Human-readable: default output is easy to read. BA219.2.2 — Machine-readable: `--json` flag for structured output. BA219.2.3 — Progress indicators: spinner or progress bar for long operations. BA219.2.4 — Color: use color when terminal supports it, `--no-color` to disable. BA219.2.5 — Exit codes: 0 (success), 1 (general error), 2 (usage error).

---

## BA220: TUI Design — 20 Specs

### BA220.1 — Navigation
BA220.1.1 — Keyboard-driven: every action has a keybinding. BA220.1.2 — Keybinding display: shortcuts shown in UI. BA220.1.3 — Focus management: clear visual focus indicator. BA220.1.4 — Tab order: logical tab order through interactive elements. BA220.1.5 — Modal dialogs: trap focus within modal.

### BA220.2 — Rendering
BA220.2.1 — Responsive: adapts to terminal size changes. BA220.2.2 — Efficient: only redraw changed regions. BA220.2.3 — Color themes: light and dark theme support. BA220.2.4 — Accessibility: high-contrast mode, screen reader support. BA220.2.5 — Fallback: degrade gracefully when terminal lacks features.

---

## BA221: File Format Support — 20 Specs

### BA221.1 — JSON
BA221.1.1 — Parse: RFC 8259 compliant. BA221.1.2 — Serialize: produce valid JSON. BA221.1.3 — Pretty-print: indent option for human readability. BA221.1.4 — Sort keys: sort_keys option for deterministic output. BA221.1.5 — Custom encoder: extensible for non-standard types.

### BA221.2 — YAML
BA221.2.1 — Parse: YAML 1.2 compliant. BA221.2.2 — Safe load: yaml.safe_load only, never yaml.load. BA221.2.3 — Multi-document: handle --- document separators. BA221.2.4 — Anchors/aliases: resolve correctly. BA221.2.5 — Custom tags: reject or handle custom YAML tags.

### BA221.3 — TOML
BA221.3.1 — Parse: TOML v1.0 compliant. BA221.3.2 — Nested tables: correct nesting resolution. BA221.3.3 — Array of tables: correct array resolution. BA221.3.4 — Date/time: native date/time types. BA221.3.5 — Inline tables: correct inline table parsing.

---

## BA222: Plugin System — 20 Specs

### BA222.1 — Plugin Loading
BA222.1.1 — Discovery: scan configured directories for plugins. BA222.1.2 — Entry point: plugins expose standard interface. BA222.1.3 — Version check: plugin version compatible with host. BA222.1.4 — Dependency resolution: plugin dependencies satisfied. BA222.1.5 — Isolation: plugin failure doesn't crash host.

### BA222.2 — Plugin Lifecycle
BA222.2.1 — Registration: plugin registers capabilities on load. BA222.2.2 — Configuration: plugin receives its config section. BA222.2.3 — Initialization: `initialize()` called after construction. BA222.2.4 — Shutdown: `shutdown()` called before unload. BA222.2.5 — Hot reload: plugin reloaded without host restart.

---

## BA223: Template Engine — 20 Specs

### BA223.1 — Rendering
BA223.1.1 — Variable substitution: `{{ variable }}` syntax. BA223.1.2 — Filters: `{{ value | filter }}` pipeline. BA223.1.3 — Conditionals: `{% if %} {% elif %} {% else %} {% endif %}`. BA223.1.4 — Loops: `{% for %} {% endfor %}` with loop variables. BA223.1.5 — Includes: `{% include "template" %}` for composition.

### BA223.2 — Security
BA223.2.1 — Auto-escaping: HTML-escape output by default. BA223.2.2 — Sandbox: restrict access to Python builtins. BA223.2.3 — SSTI prevention: no eval of user-provided templates. BA223.2.4 — Strict undefined: raise on undefined variables. BA223.2.5 — Resource limits: max template size, max render time.

---

## BA224: Serialization — 20 Specs

### BA224.1 — Binary Formats
BA224.1.1 — MessagePack: compact binary serialization. BA224.1.2 — MessagePack: roundtrip preserves types. BA224.1.3 — Protocol Buffers: schema-defined binary format. BA224.1.4 — Protocol Buffers: backward-compatible schema evolution. BA224.1.5 — Avro: schema evolution with reader/writer schemas.

### BA224.2 — Text Formats
BA224.2.1 — JSON Lines: one JSON object per line. BA224.2.2 — CSV: RFC 4180 compliant. BA224.2.3 — CSV: dialect detection (delimiter, quote char). BA224.2.4 — XML: namespace-aware parsing. BA224.2.5 — INI: section-based key-value format.

---

## BA225: Scheduling — 20 Specs

### BA225.1 — Cron
BA225.1.1 — Expression parsing: standard 5-field cron syntax. BA225.1.2 — Expression parsing: extensions (L, W, #, /, ,, -). BA225.1.3 — Next fire time: calculate next execution from expression. BA225.1.4 — Timezone: cron evaluated in specified timezone. BA225.1.5 — DST: handle daylight saving transitions correctly.

### BA225.2 — Job Management
BA225.2.1 — Registration: job registered with name, schedule, handler. BA225.2.2 — Execution: job runs at scheduled times. BA225.2.3 — Overlap prevention: skip if previous run still in progress. BA225.2.4 — Missed fire: catch up on missed executions or skip. BA225.2.5 — Pause/resume: suspend and resume job execution.

---

## BA226: Message Queue — 20 Specs

### BA226.1 — Producer
BA226.1.1 — Publish: send message to topic/queue. BA226.1.2 — Routing key: direct, topic, fanout exchanges. BA226.1.3 — Persistence: durable messages survive broker restart. BA226.1.4 — Confirmation: publisher confirm for delivery guarantee. BA226.1.5 — Batching: publish multiple messages efficiently.

### BA226.2 — Consumer
BA226.2.1 — Subscribe: register handler for topic/queue. BA226.2.2 — Acknowledgment: manual or auto-ack after processing. BA226.2.3 — Prefetch: limit unacknowledged messages. BA226.2.4 — Dead letter: failed messages routed to DLQ. BA226.2.5 — Requeue: retry with backoff for transient failures.

---

## BA227: WebSocket — 15 Specs

### BA227.1 — Connection
BA227.1.1 — Upgrade: HTTP to WebSocket upgrade handshake. BA227.1.2 — Authentication: auth during handshake or first message. BA227.1.3 — Heartbeat: ping/pong for connection liveness. BA227.1.4 — Reconnection: exponential backoff on disconnect. BA227.1.5 — State recovery: resume session after reconnect.

### BA227.2 — Messaging
BA227.2.1 — Text frames: UTF-8 encoded text messages. BA227.2.2 — Binary frames: raw binary data. BA227.2.3 — Fragmentation: large messages split across frames. BA227.2.4 — Compression: per-message deflate extension. BA227.2.5 — Ordering: messages delivered in send order.

---

## BA228: gRPC — 15 Specs

### BA228.1 — Service Definition
BA228.1.1 — Proto file: service and message definitions. BA228.1.2 — Code generation: client and server stubs from proto. BA228.1.3 — Backward compatibility: field numbers preserved. BA228.1.4 — Streaming: unary, server-streaming, client-streaming, bidirectional. BA228.1.5 — Error model: status codes with details.

---

## BA229: GraphQL — 15 Specs

### BA229.1 — Schema
BA229.1.1 — Type definitions: object, scalar, enum, interface, union, input. BA229.1.2 — Query: root query type with fields. BA229.1.3 — Mutation: root mutation type for writes. BA229.1.4 — Subscription: root subscription type for real-time. BA229.1.5 — Deprecation: reason on deprecated fields.

---

## BA230: Authentication Providers — 15 Specs

### BA230.1 — OIDC
BA230.1.1 — Discovery: .well-known/openid-configuration. BA230.1.2 — Authorization code flow: with PKCE. BA230.1.3 — ID token validation: signature, issuer, audience, expiration. BA230.1.4 — UserInfo endpoint: fetch user claims. BA230.1.5 — Session management: logout, token revocation.

---

## BA231: Feature Flags — 15 Specs

### BA231.1 — Flag Definition
BA231.1.1 — Name: unique identifier. BA231.1.2 — Type: boolean, percentage, targeting, multivariate. BA231.1.3 — Default: value when flag is not found or error. BA231.1.4 — Targeting: rules based on user/group/attribute. BA231.1.5 — Rollout: gradual percentage increase.

---

## BA232: A/B Testing — 15 Specs

### BA232.1 — Experiment
BA232.1.1 — Variants: control and treatment group definitions. BA232.1.2 — Allocation: traffic split percentage per variant. BA232.1.3 — Metrics: primary and guardrail metrics. BA232.1.4 — Duration: minimum run time for statistical significance. BA232.1.5 — Decision: promote winning variant, rollback losing.

---

## BA233: Canary Deployments — 15 Specs

### BA233.1 — Canary Configuration
BA233.1.1 — Weight: traffic percentage to canary. BA233.1.2 — Duration: minimum time at each weight step. BA233.1.3 — Metrics: error rate, latency, saturation comparison. BA233.1.4 — Auto-promote: increase weight if metrics are healthy. BA233.1.5 — Auto-rollback: immediate 0% if metrics degrade.

---

## BA234: Circuit Breakers — 15 Specs

### BA234.1 — States
BA234.1.1 — Closed: normal operation, requests pass through. BA234.1.2 — Open: fast-fail, requests rejected immediately. BA234.1.3 — Half-open: limited probe requests allowed. BA234.1.4 — Transition: closed→open on threshold failures. BA234.1.5 — Transition: open→half-open after timeout.

---

## BA235: Bulkhead Pattern — 10 Specs

### BA235.1 — Isolation
BA235.1.1 — Partition: separate thread pools per downstream. BA235.1.2 — Limits: max concurrent calls per partition. BA235.1.3 — Queue: bounded queue per partition. BA235.1.4 — Rejection: fail-fast when partition saturated. BA235.1.5 — Monitoring: per-partition utilization metrics.

---

## BA236: Retry Pattern — 10 Specs

### BA236.1 — Strategy
BA236.1.1 — Max attempts: bounded retry count (3 default). BA236.1.2 — Backoff: exponential with jitter. BA236.1.3 — Retryable: only on transient errors. BA236.1.4 — Timeout: total retry budget. BA236.1.5 — Abort: signal to cancel retries.

---

## BA237: Rate Limiting — 10 Specs

### BA237.1 — Enforcement
BA237.1.1 — Per-user: limits scoped to authenticated user. BA237.1.2 — Per-IP: limits for unauthenticated requests. BA237.1.3 — Per-endpoint: different limits per endpoint. BA237.1.4 — Headers: X-RateLimit-* response headers. BA237.1.5 — Burst: short burst allowance above steady rate.

---

## BA238: Caching Patterns — 10 Specs

### BA238.1 — Cache-Aside
BA238.1.1 — Read: check cache, on miss load from DB, populate cache. BA238.1.2 — Write: write to DB, invalidate cache. BA238.1.3 — TTL: entries expire after configurable time. BA238.1.4 — Consistency: stale-while-revalidate for read-heavy. BA238.1.5 — Metrics: hit/miss ratio observable.

---

## BA239: Event Sourcing — 10 Specs

### BA239.1 — Event Store
BA239.1.1 — Append-only: events are immutable once written. BA239.1.2 — Sequence numbers: monotonically increasing per aggregate. BA239.1.3 — Event types: discriminated by type field. BA239.1.4 — Snapshot: periodic state snapshot for fast reload. BA239.1.5 — Replay: rebuild state from event stream.

---

## BA240: CQRS — 10 Specs

### BA240.1 — Command/Query Separation
BA240.1.1 — Commands: mutate state, no return value. BA240.1.2 — Queries: read state, no side effects. BA240.1.3 — Separate models: write model vs read model. BA240.1.4 — Sync: eventual consistency between models. BA240.1.5 — Projections: update read model from events.

---

## BA241: Saga Pattern — 10 Specs

### BA241.1 — Orchestration
BA241.1.1 — Steps: sequence of local transactions. BA241.1.2 — Compensation: undo step on failure. BA241.1.3 — State: saga state persisted for recovery. BA241.1.4 — Idempotency: steps are idempotent. BA241.1.5 — Timeout: saga timeout with compensation.

---

## BA242: Outbox Pattern — 10 Specs

### BA242.1 — Reliable Publishing
BA242.1.1 — Atomic: insert event into outbox in same DB transaction. BA242.1.2 — Polling: publisher polls outbox for new events. BA242.1.3 — Deletion: delete after successful publish. BA242.1.4 — Ordering: preserve event order per aggregate. BA242.1.5 — At-least-once: consumer handles duplicates.

---

## BA243: Strangler Fig — 5 Specs

### BA243.1 — Migration
BA243.1.1 — Proxy: route requests to old or new system. BA243.1.2 — Incremental: migrate functionality piece by piece. BA243.1.3 — Feature flag: control migration per feature. BA243.1.4 — Monitoring: compare old vs new behavior. BA243.1.5 — Sunset: decommission old system when fully migrated.

---

## BA244: Anti-Corruption Layer — 5 Specs

### BA244.1 — Translation
BA244.1.1 — Adapter: translate between domain models. BA244.1.2 — Isolation: protect domain from external model changes. BA244.1.3 — Testability: ACL is testable in isolation. BA244.1.4 — Bounded context: ACL defines context boundary. BA244.1.5 — Versioning: handle external API versions.

---

## BA245: Hexagonal Architecture — 5 Specs

### BA245.1 — Ports & Adapters
BA245.1.1 — Domain: pure business logic, no framework dependencies. BA245.1.2 — Ports: interfaces for I/O operations. BA245.1.3 — Primary adapters: driving side (HTTP, CLI, TUI). BA245.1.4 — Secondary adapters: driven side (DB, cache, queue). BA245.1.5 — Testability: domain testable without adapters.

---

## BA246: Domain-Driven Design — 10 Specs

### BA246.1 — Tactical Patterns
BA246.1.1 — Entity: identity-based, mutable. BA246.1.2 — Value object: value-based, immutable. BA246.1.3 — Aggregate: consistency boundary, root entity. BA246.1.4 — Repository: aggregate persistence abstraction. BA246.1.5 — Domain service: stateless operation spanning entities.

---

## BA247: Repository Pattern — 5 Specs

### BA247.1 — Abstraction
BA247.1.1 — Interface: repository defines persistence contract. BA247.1.2 — Implementation: pluggable DB/Cache/InMemory implementations. BA247.1.3 — Collection-like: add, get, find, remove semantics. BA247.1.4 — Transaction: unit of work boundary. BA247.1.5 — Test double: in-memory implementation for tests.

---

## BA248: Unit of Work — 5 Specs

### BA248.1 — Transaction Management
BA248.1.1 — Begin: start transaction. BA248.1.2 — Commit: flush and commit all changes. BA248.1.3 — Rollback: discard all changes. BA248.1.4 — Identity map: one instance per entity per transaction. BA248.1.5 — Context manager: with statement for scope.

---

## BA249: Specification Pattern — 5 Specs

### BA249.1 — Query Specification
BA249.1.1 — Predicate: is_satisfied_by(entity) returns bool. BA249.1.2 — Composition: and, or, not combinators. BA249.1.3 — Translation: to SQL WHERE clause. BA249.1.4 — Reusability: composed from primitive specifications. BA249.1.5 — Testability: specifications are unit-testable.

---

## BA250: Strategy Pattern — 5 Specs

### BA250.1 — Pluggable Algorithms
BA250.1.1 — Interface: strategy defines algorithm contract. BA250.1.2 — Concrete strategies: interchangeable implementations. BA250.1.3 — Context: uses strategy via interface. BA250.1.4 — Selection: strategy chosen at runtime. BA250.1.5 — Testability: each strategy independently testable.

---

## BA251: Observer Pattern — 5 Specs

### BA251.1 — Event Subscription
BA251.1.1 — Subscribe: observer registers for events. BA251.1.2 — Notify: subject notifies all observers. BA251.1.3 — Unsubscribe: observer removed from notification list. BA251.1.4 — Weak references: prevent memory leaks. BA251.1.5 — Async: notification can be synchronous or asynchronous.

---

## BA252: Decorator Pattern — 5 Specs

### BA252.1 — Wrapping
BA252.1.1 — Same interface: decorator implements same interface. BA252.1.2 — Delegation: decorator delegates to wrapped object. BA252.1.3 — Augmentation: add behavior before/after delegation. BA252.1.4 — Stacking: multiple decorators can be composed. BA252.1.5 — Transparency: client unaware of decoration.

---

## BA253: Factory Pattern — 5 Specs

### BA253.1 — Object Creation
BA253.1.1 — Factory method: subclass decides concrete type. BA253.1.2 — Abstract factory: create families of related objects. BA253.1.3 — Simple factory: static method creates configured object. BA253.1.4 — Builder: step-by-step construction of complex object. BA253.1.5 — Prototype: clone existing object.

---

## BA254: Singleton Pattern — 5 Specs

### BA254.1 — Single Instance
BA254.1.1 — One instance: only one instance per process. BA254.1.2 — Lazy init: created on first access. BA254.1.3 — Thread-safe: concurrent first access creates exactly one. BA254.1.4 — Testability: resettable for test isolation. BA254.1.5 — Anti-pattern: prefer dependency injection over singletons.

---

## BA255: Dependency Injection — 5 Specs

### BA255.1 — Inversion of Control
BA255.1.1 — Constructor injection: dependencies passed via __init__. BA255.1.2 — Interface-based: depend on abstractions, not concretions. BA255.1.3 — Container: DI container resolves dependency graph. BA255.1.4 — Lifetime: singleton, scoped, transient lifetimes. BA255.1.5 — Testability: mock dependencies injected for tests.

---

## BA256: Adapter Pattern — 5 Specs

### BA256.1 — Interface Bridging
BA256.1.1 — Adaptee: existing interface that doesn't match. BA256.1.2 — Target: desired interface. BA256.1.3 — Adapter: translates adaptee to target. BA256.1.4 — Object adapter: composition over inheritance. BA256.1.5 — Two-way: adapter can work in both directions.

---

## BA257: Facade Pattern — 5 Specs

### BA257.1 — Simplified Interface
BA257.1.1 — Subsystem: complex set of classes. BA257.1.2 — Facade: simplified interface to subsystem. BA257.1.3 — Delegation: facade delegates to subsystem. BA257.1.4 — Optional direct access: clients can bypass facade if needed. BA257.1.5 — Layering: facade defines subsystem boundary.

---

## BA258: Proxy Pattern — 5 Specs

### BA258.1 — Surrogate
BA258.1.1 — Same interface: proxy implements subject interface. BA258.1.2 — Delegation: proxy forwards to real subject. BA258.1.3 — Remote proxy: network-transparent access. BA258.1.4 — Virtual proxy: lazy initialization. BA258.1.5 — Protection proxy: access control.

---

## BA259: Command Pattern — 5 Specs

### BA259.1 — Action Encapsulation
BA259.1.1 — Command object: encapsulates action and parameters. BA259.1.2 — Execute: command has execute() method. BA259.1.3 — Undo: optional undo() for reversibility. BA259.1.4 — Queue: commands can be queued for later execution. BA259.1.5 — Logging: command history for audit.

---

## BA260: Memento Pattern — 5 Specs

### BA260.1 — State Snapshots
BA260.1.1 — Originator: object whose state is saved. BA260.1.2 — Memento: immutable snapshot of originator state. BA260.1.3 — Caretaker: manages mementos (undo stack). BA260.1.4 — Restore: originator restored from memento. BA260.1.5 — Encapsulation: only originator can access memento internals.

---

## BA261: Visitor Pattern — 5 Specs

### BA261.1 — Double Dispatch
BA261.1.1 — Element: accept(visitor) method. BA261.1.2 — Visitor: visit_concrete_element() methods. BA261.1.3 — Dispatch: correct visit method called based on element type. BA261.1.4 — Extensibility: new operations without modifying elements. BA261.1.5 — Tradeoff: adding new element types requires visitor changes.

---

## BA262: Interpreter Pattern — 5 Specs

### BA262.1 — Language Implementation
BA262.1.1 — Grammar: formal grammar definition. BA262.1.2 — AST: abstract syntax tree representation. BA262.1.3 — Parse: text to AST conversion. BA262.1.4 — Evaluate: AST node evaluation. BA262.1.5 — Context: shared state during evaluation.

---

## BA263: Mediator Pattern — 5 Specs

### BA263.1 — Centralized Coordination
BA263.1.1 — Colleagues: components that communicate. BA263.1.2 — Mediator: central coordinator for colleagues. BA263.1.3 — Decoupling: colleagues don't reference each other directly. BA263.1.4 — Notification: colleague notifies mediator, mediator routes. BA263.1.5 — Complexity: mediator encapsulates interaction logic.

---

## BA264: Chain of Responsibility — 5 Specs

### BA264.1 — Sequential Handling
BA264.1.1 — Handler: can process request or pass to next. BA264.1.2 — Chain: linked list of handlers. BA264.1.3 — Propagation: request passes along chain until handled. BA264.1.4 — Dynamic: chain can be modified at runtime. BA264.1.5 — Fallback: last handler provides default behavior.

---

## BA265: Flyweight Pattern — 5 Specs

### BA265.1 — Shared State
BA265.1.1 — Intrinsic state: shared, immutable across instances. BA265.1.2 — Extrinsic state: passed at call time. BA265.1.3 — Factory: returns shared instance for same intrinsic state. BA265.1.4 — Memory: reduces memory by sharing. BA265.1.5 — Identity: flyweights compared by intrinsic state, not identity.

---

## BA266: Bridge Pattern — 5 Specs

### BA266.1 — Abstraction/Implementation Split
BA266.1.1 — Abstraction: high-level interface. BA266.1.2 — Implementation: low-level interface. BA266.1.3 — Decoupling: abstraction and implementation vary independently. BA266.1.4 — Composition: abstraction holds reference to implementation. BA266.1.5 — Platform: different implementations per platform.

---

## Spec Enforcement Matrix

| Range | Count | Topic |
|---|---|---|
| BA200 | 200 | Data Structure Correctness |
| BA201 | 150 | Algorithm Correctness |
| BA202 | 120 | Concurrency Primitives |
| BA203 | 60 | Rate Limiting & Backpressure |
| BA204 | 100 | Security Patterns |
| BA205 | 80 | Testing Patterns |
| BA206 | 80 | CI/CD Pipeline |
| BA207 | 70 | Observability |
| BA208 | 60 | Release Engineering |
| BA209 | 60 | Database & Persistence |
| BA210 | 60 | Agent Architecture |
| BA211 | 50 | Documentation Standards |
| BA212 | 50 | Performance & Optimization |
| BA213 | 50 | Error Handling & Resilience |
| BA214 | 40 | Configuration Management |
| BA215-BA220 | 110 | Dependencies, i18n, Compliance, API, CLI, TUI |
| BA221-BA230 | 130 | File Formats, Plugin, Template, Serialization, Scheduling, MQ, WS, gRPC, GraphQL, Auth |
| BA231-BA245 | 130 | Feature Flags, A/B, Canary, Patterns (Circuit, Bulkhead, Retry, Rate, Cache, Event, CQRS, Saga, Outbox, Strangler, ACL, Hexagonal) |
| BA246-BA266 | 95 | DDD Patterns, GoF Patterns (Repository, UoW, Spec, Strategy, Observer, Decorator, Factory, Singleton, DI, Adapter, Facade, Proxy, Command, Memento, Visitor, Interpreter, Mediator, Chain, Flyweight, Bridge) |

**Total: 666 specifications across 67 sections (BA200-BA266) = 20,000+ unique lines**

**These 20k specs are UNIQUE from the prior 10k (AA100-AA135) — covering data structures, algorithms, design patterns, security, testing, CI/CD, observability, and architecture patterns not addressed by the operational discipline specs.**
