"""Unit tests for ansible/unsafe.py — wrap_unsafe, wrap_extravars, has_wrap_var."""

from __future__ import annotations

import pytest

from general_ludd.ansible.unsafe import has_wrap_var, wrap_extravars, wrap_unsafe

pytestmark = pytest.mark.xdist_group("ansible_unsafe")


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


# ---------------------------------------------------------------------------
# ExtraVarsLimits validation
# ---------------------------------------------------------------------------


class TestExtraVarsLimits:
    """ExtraVarsLimits enforces positive-integer bounds."""

    def test_default_limits_are_valid(self):
        from general_ludd.ansible.unsafe import DEFAULT_EXTRAVARS_LIMITS

        limits = DEFAULT_EXTRAVARS_LIMITS
        assert limits.max_depth == 32
        assert limits.max_items == 10_000
        assert limits.max_string_bytes == 1_048_576
        assert limits.max_bytes_value == 1_048_576
        assert limits.max_total_bytes == 4_194_304

    def test_custom_limits_constructor(self):
        from general_ludd.ansible.unsafe import ExtraVarsLimits

        limits = ExtraVarsLimits(
            max_depth=10, max_items=100, max_string_bytes=64, max_bytes_value=64, max_total_bytes=256
        )
        assert limits.max_depth == 10
        assert limits.max_items == 100
        assert limits.max_string_bytes == 64
        assert limits.max_bytes_value == 64
        assert limits.max_total_bytes == 256

    def test_zero_max_depth_raises(self):
        from general_ludd.ansible.unsafe import ExtraVarsLimits

        with pytest.raises(ValueError, match="must be a positive integer"):
            ExtraVarsLimits(max_depth=0)

    def test_negative_max_items_raises(self):
        from general_ludd.ansible.unsafe import ExtraVarsLimits

        with pytest.raises(ValueError, match="must be a positive integer"):
            ExtraVarsLimits(max_items=-1)

    def test_non_int_limit_raises(self):
        from general_ludd.ansible.unsafe import ExtraVarsLimits

        with pytest.raises(ValueError, match="must be a positive integer"):
            ExtraVarsLimits(max_depth=1.5)


# ---------------------------------------------------------------------------
# YAML operator scanning
# ---------------------------------------------------------------------------


class TestScanYamlOperators:
    """_scan_yaml_operators rejects dangerous YAML constructs before loading."""

    def test_rejects_yaml_tags(self):
        from general_ludd.ansible.unsafe import ExtraVarsLimits, _scan_yaml_operators

        with pytest.raises(
            pytest.importorskip("general_ludd.ansible.unsafe").ExtraVarsValidationError, match="YAML tags"
        ):
            _scan_yaml_operators("!!python/object:os.system ['id']", ExtraVarsLimits(max_total_bytes=65536))

    def test_rejects_yaml_anchors_and_aliases(self):
        from general_ludd.ansible.unsafe import ExtraVarsLimits, _scan_yaml_operators

        with pytest.raises(
            pytest.importorskip("general_ludd.ansible.unsafe").ExtraVarsValidationError, match="anchors and aliases"
        ):
            _scan_yaml_operators("key: &anchor value\nother: *anchor", ExtraVarsLimits(max_total_bytes=65536))

    def test_rejects_yaml_directives(self):
        from general_ludd.ansible.unsafe import ExtraVarsLimits, _scan_yaml_operators

        with pytest.raises(
            pytest.importorskip("general_ludd.ansible.unsafe").ExtraVarsValidationError, match="directives"
        ):
            _scan_yaml_operators("%YAML 1.1\n---\nkey: value", ExtraVarsLimits(max_total_bytes=65536))

    def test_rejects_merge_operator(self):
        from general_ludd.ansible.unsafe import ExtraVarsLimits, _scan_yaml_operators

        with pytest.raises(
            pytest.importorskip("general_ludd.ansible.unsafe").ExtraVarsValidationError, match="merge operator"
        ):
            _scan_yaml_operators("<<: *base", ExtraVarsLimits(max_total_bytes=65536))

    def test_rejects_excessive_depth(self):
        from general_ludd.ansible.unsafe import ExtraVarsLimits, _scan_yaml_operators

        deep = "a" + ": {b" * 50 + ": x" + "}" * 50
        with pytest.raises(
            pytest.importorskip("general_ludd.ansible.unsafe").ExtraVarsValidationError, match="depth exceeds"
        ):
            _scan_yaml_operators(deep, ExtraVarsLimits(max_depth=5, max_total_bytes=65536))

    def test_accepts_safe_yaml(self):
        from general_ludd.ansible.unsafe import ExtraVarsLimits, _scan_yaml_operators

        _scan_yaml_operators("key: value\nnested:\n  sub: ok", ExtraVarsLimits(max_total_bytes=65536))
        assert True

    def test_rejects_tab_yaml_syntax(self):
        from general_ludd.ansible.unsafe import ExtraVarsLimits, ExtraVarsValidationError, _scan_yaml_operators

        with pytest.raises(ExtraVarsValidationError, match="invalid extra-vars YAML"):
            _scan_yaml_operators("\tbad: \tyaml", ExtraVarsLimits(max_total_bytes=65536))


# ---------------------------------------------------------------------------
# validate_extravars — deep structural validation
# ---------------------------------------------------------------------------


class TestValidateExtravars:
    """validate_extravars enforces strict JSON-like structure with resource bounds."""

    def test_simple_dict_passes(self):
        from general_ludd.ansible.unsafe import validate_extravars

        result = validate_extravars({"key": "value", "num": 42})
        assert result == {"key": "value", "num": 42}

    def test_nested_dict_passes(self):
        from general_ludd.ansible.unsafe import validate_extravars

        result = validate_extravars({"a": {"b": {"c": "d"}}})
        assert result == {"a": {"b": {"c": "d"}}}

    def test_list_passes(self):
        from general_ludd.ansible.unsafe import validate_extravars

        result = validate_extravars({"items": [1, 2, 3]})
        assert result == {"items": [1, 2, 3]}

    def test_none_values_preserved(self):
        from general_ludd.ansible.unsafe import validate_extravars

        result = validate_extravars({"a": None, "b": "ok"})
        assert result == {"a": None, "b": "ok"}

    def test_bool_values_preserved(self):
        from general_ludd.ansible.unsafe import validate_extravars

        result = validate_extravars({"t": True, "f": False})
        assert result == {"t": True, "f": False}

    def test_float_values_preserved(self):
        from general_ludd.ansible.unsafe import validate_extravars

        result = validate_extravars({"pi": 3.14, "neg": -1.0})
        assert result == {"pi": 3.14, "neg": -1.0}

    def test_rejects_nan(self):
        from general_ludd.ansible.unsafe import ExtraVarsValidationError, validate_extravars

        with pytest.raises(ExtraVarsValidationError, match="numbers must be finite"):
            validate_extravars({"x": float("nan")})

    def test_rejects_infinity(self):
        from general_ludd.ansible.unsafe import ExtraVarsValidationError, validate_extravars

        with pytest.raises(ExtraVarsValidationError, match="numbers must be finite"):
            validate_extravars({"x": float("inf")})

    def test_rejects_non_dict_root(self):
        from general_ludd.ansible.unsafe import ExtraVarsValidationError, validate_extravars

        with pytest.raises(ExtraVarsValidationError, match="root must be a mapping"):
            validate_extravars(["list", "root"])

    def test_rejects_non_str_key(self):
        from general_ludd.ansible.unsafe import ExtraVarsValidationError, validate_extravars

        with pytest.raises(ExtraVarsValidationError, match="key must be a string"):
            validate_extravars({42: "value"})

    def test_rejects_yaml_operator_key(self):
        from general_ludd.ansible.unsafe import ExtraVarsValidationError, validate_extravars

        with pytest.raises(ExtraVarsValidationError, match="YAML operator key"):
            validate_extravars({"<<": "merged"})

    def test_rejects_bang_prefix_key(self):
        from general_ludd.ansible.unsafe import ExtraVarsValidationError, validate_extravars

        with pytest.raises(ExtraVarsValidationError, match="YAML operator key"):
            validate_extravars({"!tag": "value"})

    def test_rejects_ampersand_prefix_key(self):
        from general_ludd.ansible.unsafe import ExtraVarsValidationError, validate_extravars

        with pytest.raises(ExtraVarsValidationError, match="YAML operator key"):
            validate_extravars({"&anchor": "value"})

    def test_rejects_star_prefix_key(self):
        from general_ludd.ansible.unsafe import ExtraVarsValidationError, validate_extravars

        with pytest.raises(ExtraVarsValidationError, match="YAML operator key"):
            validate_extravars({"*alias": "value"})

    def test_rejects_percent_prefix_key(self):
        from general_ludd.ansible.unsafe import ExtraVarsValidationError, validate_extravars

        with pytest.raises(ExtraVarsValidationError, match="YAML operator key"):
            validate_extravars({"%directive": "value"})

    def test_rejects_excessive_depth(self):
        from general_ludd.ansible.unsafe import ExtraVarsLimits, ExtraVarsValidationError, validate_extravars

        deep: dict = {}
        current = deep
        for _i in range(50):
            current["nested"] = {}
            current = current["nested"]
        current["leaf"] = 1
        with pytest.raises(ExtraVarsValidationError, match="depth exceeds"):
            validate_extravars(deep, limits=ExtraVarsLimits(max_depth=5, max_total_bytes=65536))

    def test_rejects_excessive_items(self):
        from general_ludd.ansible.unsafe import ExtraVarsLimits, ExtraVarsValidationError, validate_extravars

        many = {str(i): i for i in range(100)}
        with pytest.raises(ExtraVarsValidationError, match="items exceed"):
            validate_extravars(many, limits=ExtraVarsLimits(max_items=10, max_total_bytes=65536))

    def test_rejects_oversized_string(self):
        from general_ludd.ansible.unsafe import ExtraVarsLimits, ExtraVarsValidationError, validate_extravars

        big = "x" * 2000
        with pytest.raises(ExtraVarsValidationError, match="string exceeds byte limit"):
            validate_extravars({"key": big}, limits=ExtraVarsLimits(max_string_bytes=10, max_total_bytes=65536))

    def test_rejects_oversized_bytes(self):
        from general_ludd.ansible.unsafe import ExtraVarsLimits, ExtraVarsValidationError, validate_extravars

        with pytest.raises(ExtraVarsValidationError, match="byte string exceeds"):
            validate_extravars({"key": b"x" * 2000}, limits=ExtraVarsLimits(max_bytes_value=10, max_total_bytes=65536))

    def test_rejects_total_bytes_exceeded(self):
        from general_ludd.ansible.unsafe import ExtraVarsLimits, ExtraVarsValidationError, validate_extravars

        data = {f"key{i}": f"value{i}" for i in range(50)}
        with pytest.raises(ExtraVarsValidationError, match="total bytes exceed"):
            validate_extravars(data, limits=ExtraVarsLimits(max_total_bytes=50))

    def test_rejects_unsupported_type(self):
        from general_ludd.ansible.unsafe import ExtraVarsValidationError, validate_extravars

        with pytest.raises(ExtraVarsValidationError, match="unsupported extra-vars structure"):
            validate_extravars({"key": set()})

    def test_rejects_dict_subclass(self):
        from general_ludd.ansible.unsafe import ExtraVarsValidationError, validate_extravars

        class MyDict(dict):
            pass

        with pytest.raises(ExtraVarsValidationError, match="unsupported extra-vars structure"):
            validate_extravars(MyDict({"key": "val"}))

    def test_rejects_list_subclass(self):
        from general_ludd.ansible.unsafe import ExtraVarsValidationError, validate_extravars

        class MyList(list):
            pass

        with pytest.raises(ExtraVarsValidationError, match="unsupported extra-vars structure"):
            validate_extravars({"key": MyList([1, 2, 3])})

    def test_rejects_cycle(self):
        from general_ludd.ansible.unsafe import ExtraVarsValidationError, validate_extravars

        a: dict = {}
        b: dict = {}
        a["b"] = b
        b["a"] = a
        with pytest.raises(ExtraVarsValidationError, match="alias or cycle"):
            validate_extravars(a)

    def test_rejects_duplicate_container_reuse(self):
        from general_ludd.ansible.unsafe import ExtraVarsValidationError, validate_extravars

        shared: list = [1, 2]
        with pytest.raises(ExtraVarsValidationError, match="alias or cycle"):
            validate_extravars({"x": shared, "y": shared})

    def test_num_in_boolean_context_preserved(self):
        from general_ludd.ansible.unsafe import validate_extravars

        result = validate_extravars({"zero": 0, "one": 1, "empty": ""})
        assert result == {"zero": 0, "one": 1, "empty": ""}

    def test_bytes_value_preserved(self):
        from general_ludd.ansible.unsafe import validate_extravars

        result = validate_extravars({"raw": b"hello"})
        assert result == {"raw": b"hello"}

    def test_rejects_oversized_string_key(self):
        from general_ludd.ansible.unsafe import ExtraVarsLimits, ExtraVarsValidationError, validate_extravars

        big_key = "x" * 2000
        with pytest.raises(ExtraVarsValidationError, match="string key exceeds byte limit"):
            validate_extravars({big_key: "val"}, limits=ExtraVarsLimits(max_string_bytes=10, max_total_bytes=65536))


# ---------------------------------------------------------------------------
# parse_extravars — YAML/JSON parsing and combined validation
# ---------------------------------------------------------------------------


class TestParseExtravars:
    """parse_extravars parses YAML/JSON strings and runs full validation."""

    def test_parses_yaml_string(self):
        from general_ludd.ansible.unsafe import parse_extravars

        result = parse_extravars("key: value\nnum: 42")
        assert result == {"key": "value", "num": 42}

    def test_parses_json_string(self):
        from general_ludd.ansible.unsafe import parse_extravars

        result = parse_extravars('{"key": "value", "arr": [1, 2]}')
        assert result == {"key": "value", "arr": [1, 2]}

    def test_passes_dict_through(self):
        from general_ludd.ansible.unsafe import parse_extravars

        result = parse_extravars({"key": "value"})
        assert result == {"key": "value"}

    def test_parses_bytes(self):
        from general_ludd.ansible.unsafe import parse_extravars

        result = parse_extravars(b"key: value")
        assert result == {"key": "value"}

    def test_rejects_non_utf8_bytes(self):
        from general_ludd.ansible.unsafe import ExtraVarsValidationError, parse_extravars

        with pytest.raises(ExtraVarsValidationError, match="valid UTF-8"):
            parse_extravars(b"\xff\xfe\x00")

    def test_rejects_unsupported_input_type(self):
        from general_ludd.ansible.unsafe import ExtraVarsValidationError, parse_extravars

        with pytest.raises(ExtraVarsValidationError, match="unsupported extra-vars structure"):
            parse_extravars(42)

    def test_rejects_oversized_raw_bytes(self):
        from general_ludd.ansible.unsafe import ExtraVarsLimits, ExtraVarsValidationError, parse_extravars

        with pytest.raises(ExtraVarsValidationError, match="total bytes limit"):
            parse_extravars(b"x" * 2000, limits=ExtraVarsLimits(max_total_bytes=10))

    def test_rejects_oversized_raw_string(self):
        from general_ludd.ansible.unsafe import ExtraVarsLimits, ExtraVarsValidationError, parse_extravars

        with pytest.raises(ExtraVarsValidationError, match="total bytes limit"):
            parse_extravars("x" * 2000, limits=ExtraVarsLimits(max_total_bytes=10))

    def test_rejects_yaml_tags_in_string(self):
        from general_ludd.ansible.unsafe import ExtraVarsValidationError, parse_extravars

        with pytest.raises(ExtraVarsValidationError, match="YAML tags"):
            parse_extravars("!!python/object:os.system ['id']")

    def test_rejects_yaml_anchors_in_string(self):
        from general_ludd.ansible.unsafe import ExtraVarsValidationError, parse_extravars

        with pytest.raises(ExtraVarsValidationError, match="anchors and aliases"):
            parse_extravars("key: &a value\nother: *a")

    def test_rejects_invalid_yaml_syntax(self):
        from general_ludd.ansible.unsafe import ExtraVarsValidationError, parse_extravars

        with pytest.raises(ExtraVarsValidationError, match="invalid extra-vars YAML"):
            parse_extravars("\tbad: \tyaml")

    def test_rejects_non_mapping_root(self):
        from general_ludd.ansible.unsafe import ExtraVarsValidationError, parse_extravars

        with pytest.raises(ExtraVarsValidationError, match="root must be a mapping"):
            parse_extravars("[1, 2, 3]")

    def test_parse_with_limits(self):
        from general_ludd.ansible.unsafe import ExtraVarsLimits, parse_extravars

        result = parse_extravars("a: b", limits=ExtraVarsLimits(max_depth=2, max_items=5, max_total_bytes=256))
        assert result == {"a": "b"}

    def test_rejects_parse_depth_violation(self):
        from general_ludd.ansible.unsafe import ExtraVarsLimits, ExtraVarsValidationError, parse_extravars

        deep_nested = "a: {b: {c: {d: {e: {f: {g: {h: x}}}}}}}"
        with pytest.raises(ExtraVarsValidationError, match="depth exceeds"):
            parse_extravars(deep_nested, limits=ExtraVarsLimits(max_depth=3, max_total_bytes=65536))
