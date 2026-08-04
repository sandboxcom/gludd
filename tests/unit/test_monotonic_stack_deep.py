"""Deep monotonic stack / monotonic queue tests.

Covers: next greater element, next smaller element, sliding window maximum,
largest rectangle in histogram, stock span, daily temperatures, trapping rain water,
132 pattern, remove K digits, maximum of minimums for every window size,
sum of subarray minimums, max width ramp, online stock span, sum of subarray ranges,
number of visible people in a queue, and final prices with a special discount.
"""



# ---------------------------------------------------------------------------
# Implementations for all functions under test
# ---------------------------------------------------------------------------


def next_greater_element(nums: list[int]) -> list[int]:
    """Return list where result[i] = next greater element to the right of nums[i],
    or -1 if none. Monotonic decreasing stack of indices."""
    n = len(nums)
    result = [-1] * n
    stack: list[int] = []
    for i in range(n):
        while stack and nums[stack[-1]] < nums[i]:
            result[stack.pop()] = nums[i]
        stack.append(i)
    return result


def next_greater_element_circular(nums: list[int]) -> list[int]:
    """Same as NGE but nums is circular; search wraps around once."""
    n = len(nums)
    result = [-1] * n
    stack: list[int] = []
    for i in range(2 * n):
        idx = i % n
        while stack and nums[stack[-1]] < nums[idx]:
            result[stack.pop()] = nums[idx]
        if i < n:
            stack.append(idx)
    return result


def next_smaller_element(nums: list[int]) -> list[int]:
    """Return next smaller element to the right, or -1 if none.
    Monotonic increasing stack."""
    n = len(nums)
    result = [-1] * n
    stack: list[int] = []
    for i in range(n):
        while stack and nums[stack[-1]] > nums[i]:
            result[stack.pop()] = nums[i]
        stack.append(i)
    return result


def sliding_window_maximum(nums: list[int], k: int) -> list[int]:
    """Monotonic decreasing deque storing indices. O(n)."""
    from collections import deque

    dq: deque[int] = deque()
    result = []
    for i, val in enumerate(nums):
        while dq and dq[0] <= i - k:
            dq.popleft()
        while dq and nums[dq[-1]] < val:
            dq.pop()
        dq.append(i)
        if i >= k - 1:
            result.append(nums[dq[0]])
    return result


def largest_rectangle_area(heights: list[int]) -> int:
    """Largest rectangle in histogram: monotonic increasing stack of indices."""
    max_area = 0
    stack: list[int] = []
    for i, h in enumerate(heights):
        while stack and heights[stack[-1]] > h:
            height = heights[stack.pop()]
            width = i if not stack else i - stack[-1] - 1
            max_area = max(max_area, height * width)
        stack.append(i)
    n = len(heights)
    while stack:
        height = heights[stack.pop()]
        width = n if not stack else n - stack[-1] - 1
        max_area = max(max_area, height * width)
    return max_area


def stock_span(prices: list[int]) -> list[int]:
    """Return span[i] = consecutive days (including today) price <= prices[i].
    Monotonic decreasing stack of (index, price)."""
    spans: list[int] = []
    stack: list[tuple[int, int]] = []
    for i, price in enumerate(prices):
        while stack and stack[-1][1] <= price:
            stack.pop()
        span = i + 1 if not stack else i - stack[-1][0]
        spans.append(span)
        stack.append((i, price))
    return spans


def daily_temperatures(temps: list[int]) -> list[int]:
    """Return days to wait for a warmer temperature, or 0 if none."""
    n = len(temps)
    result = [0] * n
    stack: list[int] = []
    for i, t in enumerate(temps):
        while stack and temps[stack[-1]] < t:
            prev = stack.pop()
            result[prev] = i - prev
        stack.append(i)
    return result


def trapping_rain_water(heights: list[int]) -> int:
    """Total trapped water using two-pointer O(n)."""
    if not heights:
        return 0
    left, right = 0, len(heights) - 1
    left_max = right_max = total = 0
    while left < right:
        if heights[left] < heights[right]:
            left_max = max(left_max, heights[left])
            total += left_max - heights[left]
            left += 1
        else:
            right_max = max(right_max, heights[right])
            total += right_max - heights[right]
            right -= 1
    return total


def find_132_pattern(nums: list[int]) -> bool:
    """True if i<j<k and nums[i]<nums[k]<nums[j] exists. Monotonic stack."""
    if len(nums) < 3:
        return False
    third = float("-inf")
    stack: list[int] = []
    for val in reversed(nums):
        if val < third:
            return True
        while stack and stack[-1] < val:
            third = stack.pop()
        stack.append(val)
    return False


def remove_k_digits(num: str, k: int) -> str:
    """Remove k digits from num (as string) to get smallest possible string.
    Monotonic increasing stack."""
    stack: list[str] = []
    remaining = k
    for ch in num:
        while remaining and stack and stack[-1] > ch:
            stack.pop()
            remaining -= 1
        stack.append(ch)
    final = stack[: len(stack) - remaining]
    result = "".join(final).lstrip("0")
    return result if result else "0"


def max_of_minimums_for_every_window(arr: list[int]) -> list[int]:
    """For each window size 1..n, return the max of minimums across all windows of
    that size. Uses previous/next smaller element + range-len mapping."""
    n = len(arr)
    left = [-1] * n
    right = [n] * n
    stack: list[int] = []
    for i in range(n):
        while stack and arr[stack[-1]] >= arr[i]:
            stack.pop()
        left[i] = -1 if not stack else stack[-1]
        stack.append(i)
    stack.clear()
    for i in range(n - 1, -1, -1):
        while stack and arr[stack[-1]] >= arr[i]:
            stack.pop()
        right[i] = n if not stack else stack[-1]
        stack.append(i)
    ans = [0] * (n + 1)
    for i in range(n):
        length = right[i] - left[i] - 1
        ans[length] = max(ans[length], arr[i])
    for i in range(n - 1, 0, -1):
        ans[i] = max(ans[i], ans[i + 1])
    return ans[1:]


def sum_of_subarray_minimums(arr: list[int]) -> int:
    """Sum of minimums of all contiguous subarrays modulo 10**9+7."""
    MOD = 10**9 + 7
    n = len(arr)
    left = [0] * n
    right = [0] * n
    stack: list[int] = []
    for i in range(n):
        count = 1
        while stack and arr[stack[-1]] >= arr[i]:
            count += left[stack.pop()]
        left[i] = count
        stack.append(i)
    stack.clear()
    for i in range(n - 1, -1, -1):
        count = 1
        while stack and arr[stack[-1]] > arr[i]:
            count += right[stack.pop()]
        right[i] = count
        stack.append(i)
    total = 0
    for i in range(n):
        total = (total + arr[i] * left[i] * right[i]) % MOD
    return total


def max_width_ramp(nums: list[int]) -> int:
    """Maximum width of a ramp: largest j-i such that i<j and nums[i]<=nums[j].
    Use monotonic decreasing stack of indices then scan right-to-left."""
    n = len(nums)
    stack: list[int] = []
    for i in range(n):
        if not stack or nums[stack[-1]] > nums[i]:
            stack.append(i)
    max_w = 0
    for j in range(n - 1, -1, -1):
        while stack and nums[stack[-1]] <= nums[j]:
            max_w = max(max_w, j - stack.pop())
    return max_w


def online_stock_span(ops: list[int]) -> list[int]:
    """Simulate online stock span: each call pushes a price, returns span.
    Same as stock_span; included to test repeated pushes."""
    spans: list[int] = []
    stack: list[tuple[int, int]] = []
    total = 0
    for price in ops:
        span = 1
        while stack and stack[-1][1] <= price:
            span += stack.pop()[0]
        stack.append((span, price))
        total += span
        spans.append(span)
    return spans


def sum_of_subarray_ranges(nums: list[int]) -> int:
    """Sum of (max-min) over all subarrays.
    Sum of (max) - sum of (min).  Left pass uses non-strict comparison,
    right pass uses strict comparison so equal elements are not double-counted."""
    MOD = 10**9 + 7

    def _extremes_sum(arr: list[int], is_max: bool) -> int:
        """is_max=True → sum-of-maximums; is_max=False → sum-of-minimums.
        Left pass: pop while <= (max) or >= (min)  (non-strict).
        Right pass: pop while < (max) or > (min)  (strict)."""
        n = len(arr)
        left_cnt: list[int] = [1] * n
        right_cnt: list[int] = [1] * n
        stack: list[int] = []

        for i in range(n):
            while stack:
                top = stack[-1]
                if is_max:
                    if arr[top] <= arr[i]:
                        left_cnt[i] += left_cnt[stack.pop()]
                    else:
                        break
                else:
                    if arr[top] >= arr[i]:
                        left_cnt[i] += left_cnt[stack.pop()]
                    else:
                        break
            stack.append(i)

        stack.clear()
        for i in range(n - 1, -1, -1):
            while stack:
                top = stack[-1]
                if is_max:
                    if arr[top] < arr[i]:
                        right_cnt[i] += right_cnt[stack.pop()]
                    else:
                        break
                else:
                    if arr[top] > arr[i]:
                        right_cnt[i] += right_cnt[stack.pop()]
                    else:
                        break
            stack.append(i)

        total = 0
        for i in range(n):
            total = (total + arr[i] * left_cnt[i] * right_cnt[i]) % MOD
        return total

    max_sum = _extremes_sum(nums, is_max=True)
    min_sum = _extremes_sum(nums, is_max=False)
    return (max_sum - min_sum) % MOD


def visible_people_in_queue(heights: list[int]) -> list[int]:
    """Number of people each person can see to their right (until blocked by
    a taller or equal-height person). Monotonic decreasing stack."""
    n = len(heights)
    result = [0] * n
    stack: list[int] = []
    for i in range(n - 1, -1, -1):
        visible = 0
        while stack and heights[stack[-1]] < heights[i]:
            stack.pop()
            visible += 1
        if stack:
            visible += 1
        result[i] = visible
        stack.append(i)
    return result


def final_prices(prices: list[int]) -> list[int]:
    """Special discount: price[i] - next smaller-or-equal price to the right,
    or price[i] if none. Monotonic increasing stack."""
    len(prices)
    result = list(prices)
    stack: list[int] = []
    for i, p in enumerate(prices):
        while stack and prices[stack[-1]] >= p:
            prev = stack.pop()
            result[prev] = prices[prev] - p
        stack.append(i)
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNextGreaterElement:
    def test_basic(self):
        assert next_greater_element([2, 1, 2, 4, 3]) == [4, 2, 4, -1, -1]

    def test_strictly_increasing(self):
        assert next_greater_element([1, 2, 3, 4, 5]) == [2, 3, 4, 5, -1]

    def test_strictly_decreasing(self):
        assert next_greater_element([5, 4, 3, 2, 1]) == [-1, -1, -1, -1, -1]

    def test_empty_and_single(self):
        assert next_greater_element([]) == []
        assert next_greater_element([42]) == [-1]

    def test_duplicates(self):
        assert next_greater_element([1, 3, 3, 1, 2]) == [3, -1, -1, 2, -1]


class TestNextGreaterElementCircular:
    def test_circular(self):
        assert next_greater_element_circular([1, 2, 1]) == [2, -1, 2]

    def test_all_equal(self):
        assert next_greater_element_circular([3, 3, 3]) == [-1, -1, -1]

    def test_peak(self):
        assert next_greater_element_circular([5, 4, 3, 2, 1]) == [-1, 5, 5, 5, 5]


class TestNextSmallerElement:
    def test_basic(self):
        assert next_smaller_element([4, 5, 2, 10, 8]) == [2, 2, -1, 8, -1]

    def test_increasing(self):
        assert next_smaller_element([1, 2, 3, 4]) == [-1, -1, -1, -1]


class TestSlidingWindowMaximum:
    def test_basic(self):
        assert sliding_window_maximum([1, 3, -1, -3, 5, 3, 6, 7], 3) == [3, 3, 5, 5, 6, 7]

    def test_k1(self):
        assert sliding_window_maximum([4, 2, 7, 1], 1) == [4, 2, 7, 1]

    def test_k_equals_n(self):
        assert sliding_window_maximum([2, 8, 1, 5], 4) == [8]


class TestLargestRectangleInHistogram:
    def test_basic(self):
        assert largest_rectangle_area([2, 1, 5, 6, 2, 3]) == 10

    def test_all_one_height(self):
        assert largest_rectangle_area([3, 3, 3, 3]) == 12

    def test_sawtooth(self):
        assert largest_rectangle_area([1, 2, 3, 2, 1]) == 6


class TestStockSpan:
    def test_basic(self):
        assert stock_span([100, 80, 60, 70, 60, 75, 85]) == [1, 1, 1, 2, 1, 4, 6]

    def test_all_increasing(self):
        assert stock_span([10, 20, 30, 40]) == [1, 2, 3, 4]

    def test_all_decreasing(self):
        assert stock_span([40, 30, 20, 10]) == [1, 1, 1, 1]


class TestDailyTemperatures:
    def test_basic(self):
        assert daily_temperatures([73, 74, 75, 71, 69, 72, 76, 73]) == [1, 1, 4, 2, 1, 1, 0, 0]

    def test_only_one_warmer(self):
        assert daily_temperatures([30, 40, 50, 60]) == [1, 1, 1, 0]


class TestTrappingRainWater:
    def test_basic(self):
        assert trapping_rain_water([0, 1, 0, 2, 1, 0, 1, 3, 2, 1, 2, 1]) == 6

    def test_no_trap(self):
        assert trapping_rain_water([1, 2, 3]) == 0

    def test_valley(self):
        assert trapping_rain_water([3, 0, 2, 0, 4]) == 7


class TestFind132Pattern:
    def test_true(self):
        assert find_132_pattern([1, 2, 3, 4]) is False
        assert find_132_pattern([3, 1, 4, 2]) is True

    def test_false_short(self):
        assert find_132_pattern([1, 2]) is False
        assert find_132_pattern([1]) is False

    def test_negative(self):
        assert find_132_pattern([-1, 3, 2, 0]) is True
        assert find_132_pattern([-1, -2, -3]) is False


class TestRemoveKDigits:
    def test_basic(self):
        assert remove_k_digits("1432219", 3) == "1219"

    def test_all_removed(self):
        assert remove_k_digits("10", 2) == "0"

    def test_leading_zeros(self):
        assert remove_k_digits("10200", 1) == "200"


class TestMaxOfMinimumsForEveryWindow:
    def test_basic(self):
        assert max_of_minimums_for_every_window([10, 20, 30, 50, 10, 70, 30]) == [
            70,
            30,
            20,
            10,
            10,
            10,
            10,
        ]

    def test_all_equal(self):
        assert max_of_minimums_for_every_window([5, 5, 5, 5]) == [5, 5, 5, 5]


class TestSumOfSubarrayMinimums:
    def test_basic(self):
        assert sum_of_subarray_minimums([3, 1, 2, 4]) == 17

    def test_single(self):
        assert sum_of_subarray_minimums([7]) == 7

    def test_all_same(self):
        assert sum_of_subarray_minimums([11, 11, 11]) == 66


class TestMaxWidthRamp:
    def test_basic(self):
        assert max_width_ramp([6, 0, 8, 2, 1, 5]) == 4

    def test_increasing(self):
        assert max_width_ramp([0, 1, 2, 3]) == 3

    def test_decreasing(self):
        assert max_width_ramp([3, 2, 1, 0]) == 0


class TestOnlineStockSpan:
    def test_same_as_static(self):
        prices = [100, 80, 60, 70, 60, 75, 85]
        assert online_stock_span(prices) == stock_span(prices)

    def test_increasing(self):
        assert online_stock_span([10, 20, 30, 40]) == [1, 2, 3, 4]


class TestSumOfSubarrayRanges:
    def test_basic(self):
        assert sum_of_subarray_ranges([1, 2, 3]) == 4

    def test_decreasing(self):
        assert sum_of_subarray_ranges([4, 3, 2, 1]) == 10

    def test_mixed(self):
        # [1,6,1]: subarrays: [1]=0, [6]=0, [1]=0, [1,6]=5, [6,1]=5, [1,6,1]=5 → 15
        assert sum_of_subarray_ranges([1, 6, 1]) == 15


class TestVisiblePeopleInQueue:
    def test_basic(self):
        assert visible_people_in_queue([10, 6, 8, 5, 11, 9]) == [3, 1, 2, 1, 1, 0]

    def test_all_ascending(self):
        assert visible_people_in_queue([1, 2, 3, 4]) == [1, 1, 1, 0]

    def test_all_descending(self):
        assert visible_people_in_queue([4, 3, 2, 1]) == [1, 1, 1, 0]


class TestFinalPrices:
    def test_basic(self):
        assert final_prices([8, 4, 6, 2, 3]) == [4, 2, 4, 2, 3]

    def test_increasing(self):
        assert final_prices([1, 2, 3, 4, 5]) == [1, 2, 3, 4, 5]
