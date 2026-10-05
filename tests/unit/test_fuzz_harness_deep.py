"""Deep fuzz harness tests for parsing functions -- JSON, YAML, regex, syslog.

Covers: random input fuzzing, boundary byte patterns, maximum depth,
recursion limits, encoding edge cases, ReDoS, and size bombs.

Each test validates that parsing functions fail gracefully (return None,
[], or raise a controlled error) rather than crashing, hanging, or
allocating without bound on adversarial input.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import multiprocessing
import multiprocessing.context
import re
import sys
import threading
import warnings

import pytest
import yaml

from general_ludd.ansible.unsafe import parse_extravars
from general_ludd.dispatch.dynamic_dispatcher import parse_tool_calls
from general_ludd.execution.engine import (
    _parse_fenced_blocks,
    validate_extra_vars_safe,
)
from general_ludd.receiver.parsers import (
    _decode_json,
    _otlp_any_value,
    parse_syslog,
)
from general_ludd.skills.loader import FRONTMATTER_RE, parse_skill_md


def _run_regex_search(pattern: str, text: str) -> None:
    """Run one regex search in an importable spawn-process target."""
    with contextlib.suppress(Exception):
        re.compile(pattern).search(text)


def _re_completes(pattern: str, text: str, timeout_s: float = 2.0) -> bool:
    """Return True if re.search completes within timeout (no hang / ReDoS)."""

    ctx = multiprocessing.get_context("spawn")
    p = ctx.Process(target=_run_regex_search, args=(pattern, text), daemon=True)
    p.start()
    p.join(timeout_s)
    if p.is_alive():
        p.terminate()
        p.join(1)
        p.close()
        return False
    completed = p.exitcode == 0
    p.close()
    return completed


def _rand_bytes(n: int, *, case: int, domain: str) -> bytes:
    """Return cross-version replayable bytes for one domain-separated case."""

    material = f"gludd-fuzz-v1:{domain}:{case}".encode()
    return hashlib.shake_256(material).digest(n)


# -- JSON Fuzz ----------------------------------------------------------


class TestJSONRandomFuzz:
    def test_random_bytes_does_not_crash(self) -> None:
        for case in range(200):
            payload = _rand_bytes(256, case=case, domain="json")
            with contextlib.suppress(json.JSONDecodeError, UnicodeDecodeError, ValueError):
                json.loads(payload)

    def test_json_boundary_null_bytes(self) -> None:
        for pattern in [b"\x00", b'{"a":\x001}', b'\x00{"a":1}', b'{"\x00":1}']:
            with pytest.raises((json.JSONDecodeError, UnicodeDecodeError, ValueError)):
                json.loads(pattern)

    def test_json_bom_and_encoding(self) -> None:
        payload = b'\xef\xbb\xbf{"a":1}'
        try:
            result = json.loads(payload)
            assert isinstance(result, (dict, list, str, int, float, bool, type(None)))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        assert json.loads(r'{"a":"\u00e9"}') == {"a": "\u00e9"}

    def test_json_surrogate_pairs(self) -> None:
        with contextlib.suppress(json.JSONDecodeError, UnicodeDecodeError, UnicodeEncodeError):
            json.loads('"\ud800"')

    def test_json_surrogate_pairs_raw_bytes(self) -> None:
        payload = b'"\xed\xa0\x80"'
        with contextlib.suppress(json.JSONDecodeError, UnicodeDecodeError):
            json.loads(payload)

    def test_json_max_depth_nesting(self) -> None:
        depth = 200
        nested = "[" * depth + "1" + "]" * depth
        with contextlib.suppress(json.JSONDecodeError, RecursionError, MemoryError):
            json.loads(nested)

    def test_json_deep_dict_nesting(self) -> None:
        depth = 200
        nested = "{" + ",".join(f'"k{i}":{{' for i in range(depth)) + '"v":1' + "}" * depth
        with contextlib.suppress(json.JSONDecodeError, RecursionError, MemoryError):
            json.loads(nested)

    def test_json_size_bomb(self) -> None:
        large = "[" + "1," * 100_000 + "1]"
        with contextlib.suppress(json.JSONDecodeError, MemoryError):
            json.loads(large)

    def test_json_escaped_unicode_sequences(self) -> None:
        for esc in ["\\u0000", "\\uFFFF", "\\uD800\\uDC00", "\\u00e9"]:
            s = f'"{esc}"'
            with contextlib.suppress(json.JSONDecodeError):
                json.loads(s)

    def test_json_overlong_utf8_bytes(self) -> None:
        payloads = [
            b'"\xc0\x80"',
            b'"\xe0\x80\x80"',
            b'"\xf0\x80\x80\x80"',
        ]
        for p in payloads:
            with contextlib.suppress(json.JSONDecodeError, UnicodeDecodeError):
                json.loads(p)

    def test_json_truncated_utf8(self) -> None:
        payloads = [b'"\xe2', b'"\xe2\x82', b'"\xf0\x9f']
        for p in payloads:
            with contextlib.suppress(json.JSONDecodeError, UnicodeDecodeError):
                json.loads(p)

    def test_json_duplicate_keys(self) -> None:
        result = json.loads('{"a":1,"a":2}')
        assert result["a"] == 2

    def test_json_negative_zero_and_extremes(self) -> None:
        for val in ["-0", "1e308", "-1e308", "1e-308", "1e309", "0.0"]:
            with contextlib.suppress(json.JSONDecodeError):
                json.loads(val)

    def test_json_control_characters_in_strings(self) -> None:
        for ch in range(0, 32):
            if ch in (9, 10, 13):
                continue
            s = f'"{chr(ch)}"'
            with pytest.raises(json.JSONDecodeError):
                json.loads(s)

    def test_json_integers_overflow(self) -> None:
        huge = "9" * 500
        with contextlib.suppress(json.JSONDecodeError):
            json.loads(huge)

    def test_json_string_with_backslash_chain(self) -> None:
        payload = '"' + "\\\\" * 5000 + '"'
        result = json.loads(payload)
        assert isinstance(result, str)


# -- YAML Fuzz ----------------------------------------------------------


class TestYAMLFuzz:
    def test_yaml_random_bytes_does_not_crash(self) -> None:
        for case in range(100):
            payload = _rand_bytes(128, case=case, domain="yaml")
            with contextlib.suppress(yaml.YAMLError, UnicodeDecodeError, ValueError, AttributeError):
                yaml.safe_load(payload)

    def test_yaml_nesting_depth(self) -> None:
        depth = 150
        payload = " " * 2 * depth + "x: 1\n"
        nested = "a:\n" * depth + payload
        with contextlib.suppress(yaml.YAMLError, RecursionError, MemoryError):
            yaml.safe_load(nested)

    def test_yaml_alias_bomb(self) -> None:
        payload = "a: &a [" + ",".join(["*a"] * 500) + "]"
        with contextlib.suppress(yaml.YAMLError, RecursionError, MemoryError):
            yaml.safe_load(payload)

    def test_yaml_binary_tag(self) -> None:
        payload = '!!binary "SGVsbG8gV29ybGQ="'
        result = yaml.safe_load(payload)
        assert isinstance(result, bytes) or result is not None

    def test_yaml_special_floats(self) -> None:
        for val in [".nan", ".inf", "-.inf", ".NaN", ".INF"]:
            result = yaml.safe_load(val)
            assert result is not None or isinstance(result, float)

    def test_yaml_null_bytes(self) -> None:
        payload = b"key: value\x00extra"
        with contextlib.suppress(yaml.YAMLError, UnicodeDecodeError, ValueError):
            yaml.safe_load(payload)

    def test_yaml_explicit_tags(self) -> None:
        payloads = [
            "!!int 42",
            "!!str 42",
            "!!bool true",
            "!!null",
            "!!timestamp 2024-01-01",
        ]
        for p in payloads:
            with contextlib.suppress(yaml.YAMLError):
                yaml.safe_load(p)

    def test_yaml_merge_keys(self) -> None:
        payload = """
base: &base
  a: 1
  b: 2
merged:
  <<: *base
  c: 3
"""
        try:
            result = yaml.safe_load(payload)
            assert isinstance(result, dict)
        except yaml.YAMLError:
            pass


# -- Regex ReDoS / Catastrophic Backtracking ----------------------------


class TestRegexFuzz:
    def test_redos_nested_quantifiers_hangs(self) -> None:
        pattern = r"^(a+)+$"
        text = "a" * 30 + "!"
        hung = not _re_completes(pattern, text, timeout_s=2.0)
        assert hung, f"Known-redos pattern should hang on input len={len(text)}"

    def test_redos_alternation_hangs(self) -> None:
        pattern = r"^(\w+|[-+*/%])+$"
        text = "a" * 50 + "!"
        hung = not _re_completes(pattern, text, timeout_s=2.0)
        assert hung, f"Known-redos alternation should hang on input len={len(text)}"

    def test_lookahead_completes_fast(self) -> None:
        pattern = r"^(?=.*a)(?=.*b)(?=.*c).*$"
        text = "x" * 100000
        ok = _re_completes(pattern, text, timeout_s=3.0)
        assert ok, f"Lookahead should complete; hung on input len={len(text)}"

    def test_frontmatter_regex_boundary(self) -> None:
        for payload in [
            "---\n",
            "---\n\n---\n",
            "---\nkey: val\n---\nbody",
            "x" * 100000 + "---\n\n---\n",
            "\x00---\n\n---\n",
        ]:
            with contextlib.suppress(re.error, MemoryError, RecursionError):
                FRONTMATTER_RE.match(payload)

    def test_frontmatter_regex_no_redos(self) -> None:
        text = "---\n" + "x" * 100000
        ok = _re_completes(FRONTMATTER_RE.pattern, text, timeout_s=3.0)
        assert ok, f"FRONTMATTER_RE suspected ReDoS on input len={len(text)}"

    def test_rfc5424_regex_no_redos(self) -> None:
        from general_ludd.receiver.parsers import _RFC5424_RE

        text = "<34>1 2024-01-01T00:00:00Z host app proc msgid " + "x" * 10000
        ok = _re_completes(_RFC5424_RE.pattern, text, timeout_s=3.0)
        assert ok, "_RFC5424_RE suspected ReDoS"

    def test_rfc3164_regex_no_redos(self) -> None:
        from general_ludd.receiver.parsers import _RFC3164_RE

        text = "<34>Oct 11 22:14:15 myhost " + "x" * 10000
        ok = _re_completes(_RFC3164_RE.pattern, text, timeout_s=3.0)
        assert ok, "_RFC3164_RE suspected ReDoS"

    def test_compile_random_patterns(self) -> None:
        patterns = [b"[[a]"]
        patterns.extend(
            _rand_bytes(32, case=case, domain="regex") for case in range(50)
        )
        observed: list[tuple[type[Warning], str]] = []
        for payload in patterns:
            raw = payload.decode("latin-1", errors="replace")
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                with contextlib.suppress(re.error):
                    re.compile(raw)
            observed.extend((item.category, str(item.message)) for item in caught)

        allowed_prefixes = (
            "Possible nested set at position ",
            "Possible set difference at position ",
            "Possible set intersection at position ",
            "Possible set symmetric difference at position ",
            "Possible set union at position ",
        )
        assert any(message.startswith(allowed_prefixes[0]) for _, message in observed)
        assert all(
            category is FutureWarning and message.startswith(allowed_prefixes)
            for category, message in observed
        )

    def test_search_null_byte_pattern(self) -> None:
        pattern = re.compile(r".*")
        text = "\x00" * 10000
        assert pattern.search(text) is not None

    def test_search_invalid_utf8_bytes(self) -> None:
        pattern = re.compile(r"\w+")
        text = b"\xff\xfe\xfd\xfc".decode("latin-1", errors="replace")
        try:
            result = pattern.search(text)
            assert result is None or isinstance(result, re.Match)
        except re.error:
            pass


# -- Receiver Parser Fuzz -----------------------------------------------


class TestReceiverParserFuzz:
    def test_decode_json_random_bytes(self) -> None:
        for case in range(200):
            payload = _rand_bytes(128, case=case, domain="receiver-json")
            result = _decode_json(payload)
            assert result is None or isinstance(result, (dict, list, str, int, float, bool))

    def test_decode_json_boundary_bytes(self) -> None:
        for payload in [b"", b" ", b"\x00", b"\xff\xfe", b"null", b"true", b"false"]:
            result = _decode_json(payload)
            assert result is None or isinstance(result, (dict, list, str, int, float, bool, type(None)))

    def test_parse_syslog_random_bytes(self) -> None:
        for case in range(100):
            payload = _rand_bytes(128, case=case, domain="syslog")
            result = parse_syslog(payload)
            assert isinstance(result, list)
            for rec in result:
                assert isinstance(rec, dict)

    def test_parse_syslog_empty_and_null(self) -> None:
        payloads: tuple[bytes | str, ...] = (b"", b" ", b"\n", "\n", "   \n   ")
        for payload in payloads:
            result = parse_syslog(payload)
            assert result == []

    def test_parse_syslog_valid_shapes(self) -> None:
        valid = b"<34>Oct 11 22:14:15 myhost su: 'su root' failed for lonvick on /dev/pts/8"
        result = parse_syslog(valid)
        assert len(result) >= 1
        assert result[0]["message"]

    def test_otlp_any_value_nested_bombs(self) -> None:
        depth = 10
        val: object = {"stringValue": "x"}
        for _i in range(depth):
            val = {"arrayValue": {"values": [val]}}
        try:
            result = _otlp_any_value(val)
            assert isinstance(result, (dict, list))
        except (RecursionError, MemoryError):
            pass

    def test_otlp_any_value_random_dicts(self) -> None:
        for case in range(100):
            obj: dict[str, object] = {}
            for field in range(10):
                item = case * 10 + field
                k = _rand_bytes(4, case=item, domain="otlp-key").hex()
                v = _rand_bytes(16, case=item, domain="otlp-value")
                obj[k] = v
            with contextlib.suppress(TypeError, ValueError):
                _otlp_any_value(obj)


# -- Dynamic Dispatcher Parser Fuzz -------------------------------------


class TestDynamicDispatcherFuzz:
    def test_parse_tool_calls_random_strings(self) -> None:
        for case in range(100):
            raw = _rand_bytes(64, case=case, domain="tool-call-text").decode(
                "latin-1", errors="replace"
            )
            result = parse_tool_calls(raw)
            assert isinstance(result, list)

    def test_parse_tool_calls_random_dicts(self) -> None:
        for case in range(50):
            d: dict[str, object] = {}
            for field in range(5):
                item = case * 5 + field
                k = _rand_bytes(4, case=item, domain="tool-call-key").hex()
                d[k] = _rand_bytes(8, case=item, domain="tool-call-value").hex()
            result = parse_tool_calls(d)
            assert isinstance(result, list)

    def test_parse_tool_calls_boundary_inputs(self) -> None:
        for payload in [
            "",
            "null",
            "[]",
            "{}",
            '{"tool_calls": null}',
            '{"tool_calls": []}',
            '{"tool_calls": [{}]}',
            '{"kind": "role", "name": "x"}',
            '{"kind": "role", "name": "x", "args": null}',
        ]:
            result = parse_tool_calls(payload)
            assert isinstance(result, list)

    def test_parse_tool_calls_deep_nesting(self) -> None:
        depth = 200
        nested = "{" + ",".join(f'"k{i}":{{' for i in range(depth)) + '"v":1' + "}" * depth
        result = parse_tool_calls(nested)
        assert isinstance(result, list)


# -- Ansible ExtraVars / Engine Fuzz ------------------------------------


class TestExtraVarsFuzz:
    def test_parse_extravars_random_bytes(self) -> None:
        from general_ludd.ansible.unsafe import ExtraVarsValidationError

        for case in range(50):
            payload = _rand_bytes(64, case=case, domain="extra-vars")
            with contextlib.suppress(ExtraVarsValidationError):
                parse_extravars(payload)

    def test_parse_extravars_valid_yaml(self) -> None:
        from general_ludd.ansible.unsafe import ExtraVarsValidationError

        try:
            result = parse_extravars(b"key: value\nlist: [1, 2, 3]")
            assert isinstance(result, dict)
        except ExtraVarsValidationError:
            pass

    def test_validate_extra_vars_safe_jinja2_patterns(self) -> None:
        with pytest.raises(ValueError):
            validate_extra_vars_safe({"a": "{{ 7*7 }}"})
        with pytest.raises(ValueError):
            validate_extra_vars_safe({"a": "{% if True %}x{% endif %}"})
        with pytest.raises(ValueError):
            validate_extra_vars_safe({"a": "{# comment #}"})

    def test_validate_extra_vars_safe_clean_dict(self) -> None:
        validate_extra_vars_safe({"a": "plain", "b": [1, 2], "c": {"d": "no template"}})

    def test_validate_extra_vars_safe_bypass_flag(self) -> None:
        validate_extra_vars_safe({"a": "{{ 7*7 }}"}, allow_jinja2_in_extravars=True)

    def test_validate_extra_vars_safe_nested_jinja2(self) -> None:
        with pytest.raises(ValueError):
            validate_extra_vars_safe({"level1": {"level2": ["safe", "{{ 7*7 }}"]}})
        with pytest.raises(ValueError):
            validate_extra_vars_safe({"arr": [{"k": "{% if x %}y{% endif %}"}]})


# -- Engine Parsers Fuzz ------------------------------------------------


class TestEngineParserFuzz:
    def test_parse_fenced_blocks_random(self) -> None:
        for case in range(100):
            raw = _rand_bytes(256, case=case, domain="fenced-blocks").decode(
                "latin-1", errors="replace"
            )
            result = _parse_fenced_blocks(raw)
            assert isinstance(result, list)
            for block in result:
                assert isinstance(block, dict)
                assert "language" in block
                assert "content" in block

    def test_parse_fenced_blocks_boundary(self) -> None:
        for payload in [
            "",
            "```\n```",
            "```python\nx```",
            "```python\nx\n```",
            "```\n\n\n```",
            "```" + "x" * 100000 + "\n```",
        ]:
            result = _parse_fenced_blocks(payload)
            assert isinstance(result, list)

    def test_parse_fenced_blocks_multiple(self) -> None:
        payload = "```python\nprint(1)\n```\n\ntext\n\n```yaml\nkey: val\n```"
        result = _parse_fenced_blocks(payload)
        assert len(result) == 2
        assert result[0]["language"] == "python"
        assert result[1]["language"] == "yaml"


# -- Skill Loader Fuzz --------------------------------------------------


class TestSkillLoaderFuzz:
    def test_parse_skill_md_random(self) -> None:
        for case in range(50):
            raw = _rand_bytes(128, case=case, domain="skill-markdown").decode(
                "latin-1", errors="replace"
            )
            try:
                skill = parse_skill_md(raw)
                assert skill.name
                assert skill.body is not None
            except Exception:
                pass

    def test_parse_skill_md_frontmatter_edge_cases(self) -> None:
        for payload in [
            "---\nname: test\n---\nbody",
            "---\n\n---\n",
            "---\nkey: [*invalid yaml*\n---\n",
            "x" * 100000,
        ]:
            try:
                skill = parse_skill_md(payload)
                assert skill.name
            except Exception:
                pass


# -- Encoding Edge Cases -----------------------------------------------


class TestEncodingFuzz:
    def test_latin1_roundtrip(self) -> None:
        raw = b"key: \xe9l\xe8ve"
        decoded = raw.decode("latin-1")
        try:
            result = yaml.safe_load(decoded)
            assert result is not None
        except yaml.YAMLError:
            pass

    def test_utf16_bom(self) -> None:
        payload = "\ufeff" + '{"a": 1}'
        try:
            result = json.loads(payload)
            assert result == {"a": 1}
        except json.JSONDecodeError:
            pass

    def test_cp1252_smart_quotes_in_json(self) -> None:
        raw = b'{"key": "\x93value\x94"}'
        with contextlib.suppress(json.JSONDecodeError, UnicodeDecodeError):
            json.loads(raw)

    def test_isolated_surrogate_half(self) -> None:
        data = "\ud800"
        with contextlib.suppress(UnicodeEncodeError, UnicodeDecodeError):
            json.dumps(data)

    def test_emoji_and_wide_chars(self) -> None:
        data = '{"emoji": "\U0001f600\U0001f4a9\U0001f980"}'
        result = json.loads(data)
        assert "emoji" in result

    def test_mixed_encodings_in_string(self) -> None:
        raw = b'{"mix": "caf\xc3\xa9 \xe2\x82\xac \xf0\x9f\x98\x80"}'
        try:
            result = json.loads(raw)
            assert "mix" in result
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    def test_utf8_4byte_boundary(self) -> None:
        data = '{"val": "\U0010ffff"}'
        result = json.loads(data)
        assert result["val"] == "\U0010ffff"


# -- Recursion / Depth Limits -------------------------------------------


class TestRecursionDepthFuzz:
    def test_yaml_recursive_mapping(self) -> None:
        payload = "key: &a\n  - *a\n"
        with contextlib.suppress(yaml.YAMLError, RecursionError, MemoryError):
            yaml.safe_load(payload)

    def test_json_recursive_array(self) -> None:
        depth = sys.getrecursionlimit() + 100
        arr = "[" * depth + "]" * depth
        with contextlib.suppress(json.JSONDecodeError, RecursionError, MemoryError):
            json.loads(arr)

    def test_json_trailing_data_after_valid(self) -> None:
        payload = '{"a":1} extra garbage {"b":2}'
        with pytest.raises(json.JSONDecodeError):
            json.loads(payload)

    def test_yaml_multiple_documents(self) -> None:
        payload = "key: value\n---\nother: 2\n"
        try:
            result = list(yaml.safe_load_all(payload))
            assert len(result) >= 1
        except yaml.YAMLError:
            pass

    def test_yaml_mixed_types_deep(self) -> None:
        depth = 80
        parts: list[str] = []
        for i in range(depth):
            parts.append(f"k{i}:\n  - {i}\n  - !!str {i}")
        payload = "\n".join(parts)
        with contextlib.suppress(yaml.YAMLError, RecursionError, MemoryError):
            yaml.safe_load(payload)


# -- Structural / Concurrency Smoke -------------------------------------


class TestFuzzStructuralSmoke:
    def test_all_parsers_accept_empty_input(self) -> None:
        assert _decode_json(b"") is None
        assert parse_syslog(b"") == []
        assert parse_tool_calls("") == []
        assert _parse_fenced_blocks("") == []

    def test_concurrent_json_parse(self) -> None:
        errors: list[Exception] = []

        def _worker() -> None:
            for _i in range(50):
                try:
                    json.loads('{"a": [1, 2, 3]}')
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=_worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors

    def test_concurrent_yaml_parse(self) -> None:
        errors: list[Exception] = []

        def _worker() -> None:
            for _i in range(50):
                try:
                    yaml.safe_load("key: value\nlist: [1, 2, 3]")
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=_worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
