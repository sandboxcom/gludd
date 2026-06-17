"""Tests for shared schema validator callables."""

import pytest

from general_ludd.schemas._validators import (
    non_negative_float,
    non_negative_int,
    percent_range,
    strip_nonempty,
)


class TestStripNonempty:
    def test_strips_leading_trailing_whitespace(self):
        assert strip_nonempty("  hello  ") == "hello"

    def test_returns_plain_string_unchanged(self):
        assert strip_nonempty("hello") == "hello"

    def test_raises_on_empty_string(self):
        with pytest.raises(ValueError, match="must not be empty"):
            strip_nonempty("")

    def test_raises_on_whitespace_only(self):
        with pytest.raises(ValueError, match="must not be empty"):
            strip_nonempty("   ")

    def test_non_str_truthy_passthrough(self):
        # non-str truthy values pass the isinstance guard and the `not v` check
        result = strip_nonempty(42)  # type: ignore[arg-type]
        assert result == 42


class TestNonNegativeFloat:
    def test_zero_allowed(self):
        assert non_negative_float(0.0) == 0.0

    def test_positive_allowed(self):
        assert non_negative_float(1.5) == 1.5

    def test_large_value_allowed(self):
        assert non_negative_float(1e9) == 1e9

    def test_raises_on_negative(self):
        with pytest.raises(ValueError, match="must be non-negative"):
            non_negative_float(-0.1)

    def test_raises_on_negative_large(self):
        with pytest.raises(ValueError, match="must be non-negative"):
            non_negative_float(-100.0)


class TestNonNegativeInt:
    def test_zero_allowed(self):
        assert non_negative_int(0) == 0

    def test_positive_allowed(self):
        assert non_negative_int(5) == 5

    def test_raises_on_negative(self):
        with pytest.raises(ValueError, match="must be non-negative"):
            non_negative_int(-1)

    def test_raises_on_large_negative(self):
        with pytest.raises(ValueError, match="must be non-negative"):
            non_negative_int(-1000)


class TestPercentRange:
    def test_zero_allowed(self):
        assert percent_range(0.0) == 0.0

    def test_hundred_allowed(self):
        assert percent_range(100.0) == 100.0

    def test_midpoint_allowed(self):
        assert percent_range(75.5) == 75.5

    def test_raises_below_zero(self):
        with pytest.raises(ValueError, match=r"between 0\.0 and 100\.0"):
            percent_range(-1.0)

    def test_raises_above_hundred(self):
        with pytest.raises(ValueError, match=r"between 0\.0 and 100\.0"):
            percent_range(100.1)

    def test_raises_far_above_hundred(self):
        with pytest.raises(ValueError, match=r"between 0\.0 and 100\.0"):
            percent_range(200.0)
