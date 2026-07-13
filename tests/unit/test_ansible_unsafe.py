"""Unit tests for ansible/unsafe.py — wrap_unsafe, wrap_extravars, has_wrap_var."""

from __future__ import annotations

from general_ludd.ansible.unsafe import has_wrap_var, wrap_extravars, wrap_unsafe


class TestWrapUnsafe:
    def test_string_is_wrapped(self):
        result = wrap_unsafe("hello {{ dangerous }}")
        assert isinstance(result, str)

    def test_bytes_is_wrapped(self):
        result = wrap_unsafe(b"hello {{ dangerous }}")
        assert isinstance(result, bytes)

    def test_int_returned_unchanged(self):
        result = wrap_unsafe(42)
        assert result == 42
        assert type(result) is int

    def test_float_returned_unchanged(self):
        result = wrap_unsafe(3.14)
        assert result == 3.14
        assert type(result) is float

    def test_bool_returned_unchanged(self):
        result = wrap_unsafe(True)
        assert result is True

    def test_none_returned_unchanged(self):
        result = wrap_unsafe(None)
        assert result is None

    def test_dict_values_wrapped(self):
        result = wrap_unsafe({"a": "str {{ x }}", "b": 42})
        assert isinstance(result, dict)
        assert "a" in result
        assert "b" in result

    def test_list_values_wrapped(self):
        result = wrap_unsafe(["str {{ x }}", "str2 {{ y }}"])
        assert isinstance(result, list)
        assert len(result) == 2

    def test_tuple_values_wrapped(self):
        result = wrap_unsafe(("str {{ x }}", 42))
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_nested_dict_wrapped(self):
        result = wrap_unsafe({"outer": {"inner": "{{ nested }}"}})
        assert isinstance(result, dict)
        assert "outer" in result

    def test_empty_dict(self):
        result = wrap_unsafe({})
        assert result == {}

    def test_empty_list(self):
        result = wrap_unsafe([])
        assert result == []


class TestWrapExtravars:
    def test_none_returns_none(self):
        assert wrap_extravars(None) is None

    def test_dict_values_wrapped(self):
        result = wrap_extravars({"key": "value {{ x }}"})
        assert isinstance(result, dict)
        assert "key" in result

    def test_empty_dict(self):
        assert wrap_extravars({}) == {}

    def test_multiple_keys(self):
        result = wrap_extravars({"a": "{{ x }}", "b": 42, "c": None})
        assert isinstance(result, dict)
        assert set(result.keys()) == {"a", "b", "c"}


class TestHasWrapVar:
    def test_returns_bool(self):
        assert isinstance(has_wrap_var(), bool)

    def test_consistent_result(self):
        first = has_wrap_var()
        second = has_wrap_var()
        assert first == second
