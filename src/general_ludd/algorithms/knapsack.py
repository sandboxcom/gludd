"""Knapsack-family algorithms: 0/1, unbounded, fractional,
subset-sum, partition, coin-change (min coins + count ways).

Pure-Python, stdlib only.
"""

from __future__ import annotations


def knapsack_01(values: list[int], weights: list[int], capacity: int) -> int:
    """Maximum value achievable with 0/1 knapsack (each item at most once).

    O(n * capacity) time, O(capacity) space (1D DP).
    Returns the maximum total value; the chosen subset is not reconstructed.
    """
    dp = [0] * (capacity + 1)
    for v, w in zip(values, weights, strict=False):
        for c in range(capacity, w - 1, -1):
            if dp[c - w] + v > dp[c]:
                dp[c] = dp[c - w] + v
    return dp[capacity]


def knapsack_01_items(values: list[int], weights: list[int], capacity: int) -> tuple[int, list[int]]:
    """0/1 knapsack returning (max_value, list_of_selected_indices).

    O(n * capacity) time, O(n * capacity) space (2D DP for traceback).
    """
    n = len(values)
    dp = [[0] * (capacity + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        v, w = values[i - 1], weights[i - 1]
        for c in range(capacity + 1):
            dp[i][c] = dp[i - 1][c]
            if w <= c and dp[i - 1][c - w] + v > dp[i][c]:
                dp[i][c] = dp[i - 1][c - w] + v
    selected: list[int] = []
    c = capacity
    for i in range(n, 0, -1):
        if dp[i][c] != dp[i - 1][c]:
            selected.append(i - 1)
            c -= weights[i - 1]
    selected.reverse()
    return dp[n][capacity], selected


def knapsack_unbounded(values: list[int], weights: list[int], capacity: int) -> int:
    """Unbounded knapsack — each item can be taken any number of times.

    O(n * capacity) time, O(capacity) space.
    """
    dp = [0] * (capacity + 1)
    for c in range(1, capacity + 1):
        best = dp[c - 1]
        for v, w in zip(values, weights, strict=False):
            if w <= c and dp[c - w] + v > best:
                best = dp[c - w] + v
        dp[c] = best
    return dp[capacity]


def knapsack_fractional(values: list[int], weights: list[int], capacity: float) -> float:
    """Fractional knapsack — can take fractions of items.

    Greedy by value/weight ratio. O(n log n) time.
    Returns the maximum total value (float).
    """
    items = sorted(
        [(v / w, v, w) for v, w in zip(values, weights, strict=False)],
        key=lambda x: x[0],
        reverse=True,
    )
    total: float = 0.0
    remaining: float = capacity
    for ratio, _v, w in items:
        if remaining <= 0:
            break
        take = w if w <= remaining else remaining
        total += ratio * take
        remaining -= take
    return total


def subset_sum(nums: list[int], target: int) -> bool:
    """Return True if some subset of *nums* sums to exactly *target*.

    O(n * target) time, O(target) space (bitset/1D DP).
    """
    reachable = [False] * (target + 1)
    reachable[0] = True
    for x in nums:
        for s in range(target, x - 1, -1):
            if reachable[s - x]:
                reachable[s] = True
    return reachable[target]


def subset_sum_items(nums: list[int], target: int) -> tuple[bool, list[int]]:
    """Subset-sum returning (possible, indices_of_chosen_elements).

    O(n * target) time, O(n * target) space for traceback.
    """
    n = len(nums)
    dp = [[False] * (target + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = True
    for i in range(1, n + 1):
        x = nums[i - 1]
        for s in range(target + 1):
            dp[i][s] = dp[i - 1][s]
            if x <= s and dp[i - 1][s - x]:
                dp[i][s] = True
    if not dp[n][target]:
        return False, []
    selected: list[int] = []
    s = target
    for i in range(n, 0, -1):
        if s >= nums[i - 1] and dp[i - 1][s - nums[i - 1]]:
            selected.append(i - 1)
            s -= nums[i - 1]
    selected.reverse()
    return True, selected


def partition(nums: list[int]) -> bool:
    """Return True if *nums* can be partitioned into two subsets of equal sum.

    Equivalent to subset_sum with target = sum(nums) // 2.
    """
    total = sum(nums)
    if total % 2 != 0:
        return False
    return subset_sum(nums, total // 2)


def partition_sets(nums: list[int]) -> tuple[bool, list[int], list[int]]:
    """Partition returning (possible, set1_indices, set2_indices).

    When possible, each index appears in exactly one of the two returned lists.
    """
    total = sum(nums)
    if total % 2 != 0:
        return False, [], []
    possible, chosen = subset_sum_items(nums, total // 2)
    if not possible:
        return False, [], []
    chosen_set = frozenset(chosen)
    other = [i for i in range(len(nums)) if i not in chosen_set]
    return True, chosen, other


def coin_change_min(coins: list[int], amount: int) -> int:
    """Minimum number of coins needed to make *amount* (unlimited each).

    Returns -1 if impossible. Standard LeetCode 322 DP.
    O(n * amount) time, O(amount) space.
    """
    INF = amount + 1
    dp = [INF] * (amount + 1)
    dp[0] = 0
    for c in coins:
        for a in range(c, amount + 1):
            if dp[a - c] + 1 < dp[a]:
                dp[a] = dp[a - c] + 1
    return dp[amount] if dp[amount] != INF else -1


def coin_change_ways(coins: list[int], amount: int) -> int:
    """Number of distinct combinations that sum to *amount* (unlimited each).

    O(n * amount) time, O(amount) space.  Combination-order-agnostic;
    permutations of the same multiset are not double-counted.
    """
    dp = [0] * (amount + 1)
    dp[0] = 1
    for c in coins:
        for a in range(c, amount + 1):
            dp[a] += dp[a - c]
    return dp[amount]
