"""Deep sliding window median tests.

Covers TwoHeapMedian, SlidingWindowMedian, and MultiStreamMedian
with 20+ test cases.
"""

from __future__ import annotations

import random
import statistics

from general_ludd.algorithms.sliding_median import (
    MultiStreamMedian,
    SlidingWindowMedian,
    TwoHeapMedian,
)


class TestTwoHeapMedian:
    """Two-heap median structure tests."""

    def test_empty_returns_none(self) -> None:
        th = TwoHeapMedian()
        assert th.find_median() is None

    def test_single_element(self) -> None:
        th = TwoHeapMedian()
        th.add(7)
        assert th.find_median() == 7.0

    def test_two_elements_average(self) -> None:
        th = TwoHeapMedian()
        th.add(3)
        th.add(7)
        assert th.find_median() == 5.0

    def test_odd_count_low_heap_top(self) -> None:
        th = TwoHeapMedian()
        for x in [5, 10, 2]:
            th.add(x)
        assert th.find_median() == 5.0

    def test_even_count_average(self) -> None:
        th = TwoHeapMedian()
        for x in [5, 10, 2, 8]:
            th.add(x)
        assert th.find_median() == 6.5

    def test_duplicates(self) -> None:
        th = TwoHeapMedian()
        for x in [3, 3, 3]:
            th.add(x)
        assert th.find_median() == 3.0

    def test_negative_values(self) -> None:
        th = TwoHeapMedian()
        for x in [-5, -10, -3, -8, -1]:
            th.add(x)
        assert th.find_median() == -5.0

    def test_mixed_signs(self) -> None:
        th = TwoHeapMedian()
        for x in [-4, 10, 2, -7, 3]:
            th.add(x)
        assert th.find_median() == 2.0

    def test_sequential_inserts(self) -> None:
        th = TwoHeapMedian()
        expected = []
        for i in range(1, 101):
            th.add(i)
            expected.append(i)
        actual = th.find_median()
        assert actual == statistics.median(expected)

    def test_reverse_sequential(self) -> None:
        th = TwoHeapMedian()
        for i in reversed(range(1, 101)):
            th.add(i)
        assert th.find_median() == statistics.median(range(1, 101))

    def test_size_tracks_correctly(self) -> None:
        th = TwoHeapMedian()
        for x in [4, 1, 7, 3, 9, 2]:
            th.add(x)
        assert th.size == 6

    def test_clear_resets_state(self) -> None:
        th = TwoHeapMedian()
        for x in [4, 1, 7]:
            th.add(x)
        th.clear()
        assert th.size == 0
        assert th.find_median() is None

    def test_large_random_dataset_matches_statistics(self) -> None:
        rng = random.Random(42)
        th = TwoHeapMedian()
        values = [rng.randint(-10000, 10000) for _ in range(1000)]
        for v in values:
            th.add(v)
        assert th.find_median() == statistics.median(values)

    def test_interleaved_small_large(self) -> None:
        th = TwoHeapMedian()
        order = [100, 1, 99, 2, 98, 3, 97, 4]
        values: list[int] = []
        for x in order:
            th.add(x)
            values.append(x)
        assert th.find_median() == statistics.median(values)


class TestSlidingWindowMedian:
    """Fixed-size sliding window median tests."""

    def test_window_size_one(self) -> None:
        sw = SlidingWindowMedian(1)
        result = list(sw.process([5, 3, 9]))
        assert result == [5.0, 3.0, 9.0]

    def test_returns_none_until_window_full(self) -> None:
        sw = SlidingWindowMedian(3)
        result = list(sw.process([1, 2]))
        assert result == [None, None]

    def test_full_window_returns_median(self) -> None:
        sw = SlidingWindowMedian(3)
        result = list(sw.process([1, 3, 2]))
        assert result == [None, None, 2.0]

    def test_sliding_over_odd_window(self) -> None:
        sw = SlidingWindowMedian(3)
        values = [1, 3, -1, 3, 5, 3, 6, 2]
        result = list(sw.process(values))
        expected = [
            None if index < 2 else float(statistics.median(values[index - 2 : index + 1]))
            for index in range(len(values))
        ]
        assert result == expected

    def test_sliding_over_even_window(self) -> None:
        sw = SlidingWindowMedian(4)
        values = [1, 3, -1, 3, 5, 3, 6, 2]
        result = list(sw.process(values))
        expected = [
            None if index < 3 else float(statistics.median(values[index - 3 : index + 1]))
            for index in range(len(values))
        ]
        assert result == expected

    def test_stream_of_duplicates(self) -> None:
        sw = SlidingWindowMedian(3)
        result = list(sw.process([5, 5, 5, 5, 5]))
        assert result == [None, None, 5.0, 5.0, 5.0]

    def test_stream_with_negatives(self) -> None:
        sw = SlidingWindowMedian(3)
        result = list(sw.process([-5, -10, -3, -8, -1]))
        assert result == [None, None, -5.0, -8.0, -3.0]

    def test_large_window_random_matches_statistics(self) -> None:
        rng = random.Random(99)
        window_size = 7
        values = [rng.randint(-500, 500) for _ in range(200)]
        sw = SlidingWindowMedian(window_size)
        result = list(sw.process(values))
        assert len(result) == 200
        for i in range(window_size - 1):
            assert result[i] is None
        for i in range(window_size - 1, 200):
            expected = statistics.median(values[i - window_size + 1 : i + 1])
            assert result[i] == expected, f"idx={i} window={values[i - window_size + 1 : i + 1]}"

    def test_window_larger_than_stream_returns_all_none(self) -> None:
        sw = SlidingWindowMedian(10)
        result = list(sw.process([1, 2, 3]))
        assert result == [None, None, None]

    def test_window_equals_stream_length(self) -> None:
        sw = SlidingWindowMedian(5)
        result = list(sw.process([3, 1, 4, 1, 5]))
        assert result == [None, None, None, None, 3.0]

    def test_empty_stream(self) -> None:
        sw = SlidingWindowMedian(3)
        result = list(sw.process([]))
        assert result == []

    def test_size_property(self) -> None:
        sw = SlidingWindowMedian(5)
        list(sw.process([1, 2, 3, 4, 5, 6, 7]))
        assert sw.size == 5

    def test_iterator_input(self) -> None:
        sw = SlidingWindowMedian(3)
        result = list(sw.process(iter([4, 1, 7, 3])))
        assert result == [None, None, 4.0, 3.0]

    def test_reset_clears_state(self) -> None:
        sw = SlidingWindowMedian(3)
        list(sw.process([1, 2, 3, 4]))
        sw.reset()
        result = list(sw.process([10, 20, 30]))
        assert result == [None, None, 20.0]

    def test_odd_window_across_many_elements(self) -> None:
        rng = random.Random(7)
        values = [rng.randint(-100, 100) for _ in range(500)]
        sw = SlidingWindowMedian(15)
        result = list(sw.process(values))
        for i in range(14):
            assert result[i] is None
        for i in range(14, 500):
            expected = statistics.median(values[i - 14 : i + 1])
            assert result[i] == expected


class TestMultiStreamMedian:
    """Multi-stream median aggregator tests."""

    def test_single_stream_returns_medians(self) -> None:
        ms = MultiStreamMedian(3)
        result = ms.process(stream_a=[1, 3, 2, 5, 7])
        assert result == {"stream_a": [None, None, 2.0, 3.0, 5.0]}

    def test_two_streams_independent_windows(self) -> None:
        ms = MultiStreamMedian(3)
        result = ms.process(stream_a=[1, 5, 3, 7, 2], stream_b=[10, 20, 30, 40, 50])
        assert result["stream_a"] == [None, None, 3.0, 5.0, 3.0]
        assert result["stream_b"] == [None, None, 20.0, 30.0, 40.0]

    def test_different_stream_lengths(self) -> None:
        ms = MultiStreamMedian(3)
        result = ms.process(stream_a=[4, 1, 7, 3], stream_b=[100])
        assert result["stream_a"] == [None, None, 4.0, 3.0]
        assert result["stream_b"] == [None]

    def test_empty_all_streams(self) -> None:
        ms = MultiStreamMedian(3)
        result = ms.process(stream_a=[], stream_b=[])
        assert result == {"stream_a": [], "stream_b": []}

    def test_three_streams(self) -> None:
        ms = MultiStreamMedian(5)
        result = ms.process(
            prices=[10, 12, 9, 11, 10, 13, 14, 8, 10, 11],
            volume=[100, 200, 150, 300, 250, 350, 400, 200, 150, 300],
            spread=[1.0, 1.5, 1.2, 1.1, 1.0, 0.9, 1.3, 1.4, 1.0, 1.2],
        )
        assert len(result) == 3
        assert len(result["prices"]) == 10
        assert len(result["volume"]) == 10
        assert len(result["spread"]) == 10
        assert result["prices"][4] == 10.0
        assert result["prices"][5] == 11.0

    def test_mixed_window_sizes(self) -> None:
        ms = MultiStreamMedian(3)
        result = ms.process(stream_a=[1, 2, 3, 4, 5], stream_b=[10, 20, 30, 40, 50])
        medians_a = [None, None, 2.0, 3.0, 4.0]
        medians_b = [None, None, 20.0, 30.0, 40.0]
        assert result["stream_a"] == medians_a
        assert result["stream_b"] == medians_b

    def test_reset_clears_all_streams(self) -> None:
        ms = MultiStreamMedian(3)
        ms.process(stream_a=[1, 2, 3, 4, 5], stream_b=[10, 20, 30, 40, 50])
        ms.reset()
        result = ms.process(stream_a=[7, 8, 9], stream_b=[1, 1, 1])
        assert result["stream_a"] == [None, None, 8.0]
        assert result["stream_b"] == [None, None, 1.0]
