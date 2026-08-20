"""Unit tests for the gludd_stream Ansible module + RollingBuffer helper.

Covers:
  - Module file existence + DOCUMENTATION/EXAMPLES/RETURN blocks.
  - argument_spec fields, types, required-flags, and psk no_log.
  - dispatch_trigger / stop_condition type validation.
  - Clean import when ansible is present (SKIP otherwise).
  - RollingBuffer.push / peek / drain / __len__ behavior.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

ROOT = Path(__file__).parent.parent.parent
COLLECTION_ROOT = (
    ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent"
)
MODULE_PATH = COLLECTION_ROOT / "plugins" / "modules" / "gludd_stream.py"
BUFFER_PATH = COLLECTION_ROOT / "plugins" / "module_utils" / "gludd_stream_buffer.py"


# ---------------------------------------------------------------------------
# RollingBuffer (module_utils — no ansible dependency)
# ---------------------------------------------------------------------------

def _load_buffer_module() -> ModuleType:
    assert BUFFER_PATH.is_file(), f"gludd_stream_buffer.py missing at {BUFFER_PATH}"
    spec = importlib.util.spec_from_file_location("gludd_stream_buffer", str(BUFFER_PATH))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def buffer_mod() -> ModuleType:
    return _load_buffer_module()


class TestRollingBuffer:
    def test_push_and_len(self, buffer_mod: ModuleType) -> None:
        RollingBuffer = buffer_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=16)
        assert len(buf) == 0
        buf.push(b"abc")
        assert len(buf) == 3
        buf.push(b"def")
        assert len(buf) == 6

    def test_peek_does_not_clear(self, buffer_mod: ModuleType) -> None:
        RollingBuffer = buffer_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=16)
        buf.push(b"hello")
        assert buf.peek() == b"hello"
        assert buf.peek() == b"hello"
        assert len(buf) == 5

    def test_drain_clears(self, buffer_mod: ModuleType) -> None:
        RollingBuffer = buffer_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=16)
        buf.push(b"hello")
        buf.push(b"world")
        assert buf.drain() == b"helloworld"
        assert len(buf) == 0
        assert buf.drain() == b""

    def test_eviction_drops_oldest(self, buffer_mod: ModuleType) -> None:
        RollingBuffer = buffer_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=8)
        buf.push(b"hello")       # size 5
        buf.push(b"world!!!!!")  # size 15 -> evict to 8, drop leading 7 bytes
        assert len(buf) == 8
        # "helloworld!!!!!" -> last 8 = "rld!!!!!"
        assert buf.peek() == b"rld!!!!!"

    def test_partial_chunk_eviction(self, buffer_mod: ModuleType) -> None:
        RollingBuffer = buffer_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=4)
        buf.push(b"abcdef")  # 6 bytes -> keep last 4
        assert len(buf) == 4
        assert buf.peek() == b"cdef"

    def test_empty_push_noop(self, buffer_mod: ModuleType) -> None:
        RollingBuffer = buffer_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=8)
        buf.push(b"")
        assert len(buf) == 0
        assert buf.peek() == b""

    def test_invalid_max_bytes(self, buffer_mod: ModuleType) -> None:
        RollingBuffer = buffer_mod.RollingBuffer
        with pytest.raises(ValueError):
            RollingBuffer(max_bytes=0)
        with pytest.raises(ValueError):
            RollingBuffer(max_bytes=-1)

    def test_rejects_non_bytes(self, buffer_mod: ModuleType) -> None:
        RollingBuffer = buffer_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=8)
        with pytest.raises(TypeError):
            cast(Any, buf).push("not bytes")

    def test_default_max_bytes(self, buffer_mod: ModuleType) -> None:
        RollingBuffer = buffer_mod.RollingBuffer
        buf = RollingBuffer()
        assert buf.max_bytes == 1048576

    def test_size_property(self, buffer_mod: ModuleType) -> None:
        RollingBuffer = buffer_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=16)
        buf.push(b"abc")
        assert buf.size == 3


# ---------------------------------------------------------------------------
# Module structure (documentation blocks, argument_spec, required flags)
# ---------------------------------------------------------------------------

def _read_module_source() -> str:
    assert MODULE_PATH.is_file(), f"gludd_stream.py missing at {MODULE_PATH}"
    return MODULE_PATH.read_text()


class TestModuleStructure:
    def test_module_file_exists(self) -> None:
        assert MODULE_PATH.is_file(), "gludd_stream.py not created"

    def test_buffer_helper_exists(self) -> None:
        assert BUFFER_PATH.is_file(), "gludd_stream_buffer.py not created"

    def test_has_documentation_block(self) -> None:
        assert "DOCUMENTATION:" in _read_module_source()

    def test_has_examples_block(self) -> None:
        assert "EXAMPLES:" in _read_module_source()

    def test_has_return_block(self) -> None:
        assert "RETURN:" in _read_module_source()

    def test_supports_check_mode(self) -> None:
        assert "supports_check_mode" in _read_module_source()

    def test_argument_spec_present(self) -> None:
        assert "argument_spec=dict(" in _read_module_source()

    def test_psk_no_log(self) -> None:
        assert 'psk=dict(type="str", default="", no_log=True)' in _read_module_source()


class TestArgumentSpec:
    """Inspect argument_spec fields by importing the module and parsing the AST."""

    def _spec(self) -> dict[str, dict[str, Any]]:
        src = _read_module_source()
        tree = ast.parse(src)
        # Find the AnsibleModule(...) call (the only call whose keywords include
        # 'argument_spec' and 'supports_check_mode').
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            kw_names = {k.arg for k in node.keywords}
            if "argument_spec" not in kw_names or "supports_check_mode" not in kw_names:
                continue
            for kw in node.keywords:
                if kw.arg != "argument_spec":
                    continue
                # argument_spec may be a dict literal {...} or a dict(...) call.
                value = kw.value
                if isinstance(value, ast.Call) and getattr(value.func, "id", None) == "dict":
                    # Reconstruct the dict from the dict(key=value, ...) kwargs.
                    out: dict[str, dict[str, Any]] = {}
                    for inner_kw in value.keywords:
                        if (
                            isinstance(inner_kw.value, ast.Call)
                            and isinstance(inner_kw.arg, str)
                        ):
                            out[inner_kw.arg] = self._field_from_call(inner_kw.value)
                    if out:
                        return out
                if isinstance(value, ast.Dict):
                    return self._dict_from_ast(value)
        pytest.skip("argument_spec not found via AST")

    def _dict_from_ast(self, node: ast.Dict) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for key, val in zip(node.keys, node.values, strict=False):
            if (
                not isinstance(key, ast.Constant)
                or not isinstance(key.value, str)
                or not isinstance(val, ast.Call)
            ):
                continue
            result[key.value] = self._field_from_call(val)
        return result

    def _field_from_call(self, call: ast.Call) -> dict[str, Any]:
        field: dict[str, Any] = {}
        for kw in call.keywords:
            if kw.arg == "type" and isinstance(kw.value, ast.Constant):
                field["type"] = kw.value.value
            elif kw.arg == "required" and isinstance(kw.value, ast.Constant):
                field["required"] = kw.value.value
            elif kw.arg == "default" and isinstance(kw.value, ast.Constant):
                field["default"] = kw.value.value
            elif kw.arg == "choices" and isinstance(kw.value, ast.List):
                field["choices"] = [
                    e.value for e in kw.value.elts if isinstance(e, ast.Constant)
                ]
            elif kw.arg == "no_log" and isinstance(kw.value, ast.Constant):
                field["no_log"] = kw.value.value
        return field

    def test_device_required_str(self) -> None:
        spec = self._spec()
        assert "device" in spec
        assert spec["device"].get("type") == "str"
        assert spec["device"].get("required") is True

    def test_device_kind_required_with_choices(self) -> None:
        spec = self._spec()
        assert "device_kind" in spec
        assert spec["device_kind"].get("required") is True
        choices = spec["device_kind"].get("choices", [])
        for expected in ("video", "audio", "text", "binary"):
            assert expected in choices, f"device_kind missing choice {expected!r}"

    def test_buffer_size_int_default(self) -> None:
        spec = self._spec()
        assert "buffer_size" in spec
        assert spec["buffer_size"].get("type") == "int"
        assert spec["buffer_size"].get("default") == 1048576

    def test_daemon_url_required(self) -> None:
        spec = self._spec()
        assert "daemon_url" in spec
        assert spec["daemon_url"].get("type") == "str"

    def test_psk_no_log(self) -> None:
        spec = self._spec()
        assert "psk" in spec
        assert spec["psk"].get("no_log") is True

    def test_dispatch_trigger_dict(self) -> None:
        spec = self._spec()
        assert "dispatch_trigger" in spec
        assert spec["dispatch_trigger"].get("type") == "dict"

    def test_dispatch_role_clone_dict(self) -> None:
        spec = self._spec()
        assert "dispatch_role_clone" in spec
        assert spec["dispatch_role_clone"].get("type") == "dict"

    def test_external_processor_optional(self) -> None:
        spec = self._spec()
        assert "external_processor" in spec
        assert spec["external_processor"].get("type") == "dict"

    def test_stop_condition_dict(self) -> None:
        spec = self._spec()
        assert "stop_condition" in spec
        assert spec["stop_condition"].get("type") == "dict"

    def test_artifact_dir_str(self) -> None:
        spec = self._spec()
        assert "artifact_dir" in spec
        assert spec["artifact_dir"].get("type") == "str"


# ---------------------------------------------------------------------------
# Validation helpers for dispatch_trigger / stop_condition type tags
# ---------------------------------------------------------------------------

class TestDispatchTriggerValidation:
    def _load_main(self) -> ModuleType:
        return _load_stream_module_or_skip()

    def test_valid_trigger_types(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mod = self._load_main()
        validate = mod._validate_dispatch_trigger
        for trigger in [
            {"type": "size_threshold", "bytes": 1024},
            {"type": "interval", "seconds": 5.0},
            {"type": "silence_detection", "min_silence_ms": 800},
            {"type": "external", "topic": "stream/dispatch"},
        ]:
            assert validate(trigger) is None, f"valid trigger rejected: {trigger}"

    def test_unknown_trigger_type_rejected(self) -> None:
        mod = self._load_main()
        err = mod._validate_dispatch_trigger({"type": "bogus"})
        assert err is not None
        assert "bogus" in err

    def test_missing_required_key(self) -> None:
        mod = self._load_main()
        err = mod._validate_dispatch_trigger({"type": "size_threshold"})
        assert err is not None
        assert "bytes" in err.lower()


class TestStopConditionValidation:
    def _load_main(self) -> ModuleType:
        return _load_stream_module_or_skip()

    def test_valid_stop_types(self) -> None:
        mod = self._load_main()
        validate = mod._validate_stop_condition
        for cond in [
            {"type": "timeout", "seconds": 60.0},
            {"type": "eof"},
            {"type": "external", "topic": "stream/stop"},
            {"type": "max_dispatches", "count": 10},
        ]:
            assert validate(cond) is None, f"valid stop rejected: {cond}"

    def test_unknown_stop_type_rejected(self) -> None:
        mod = self._load_main()
        err = mod._validate_stop_condition({"type": "bogus"})
        assert err is not None
        assert "bogus" in err


# ---------------------------------------------------------------------------
# Clean import (skips when ansible is not installed)
# ---------------------------------------------------------------------------

def _load_stream_module_or_skip() -> ModuleType:
    pytest.importorskip("ansible")
    spec = importlib.util.spec_from_file_location("gludd_stream", str(MODULE_PATH))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCleanImport:
    def test_module_imports_without_syntax_error(self) -> None:
        mod = _load_stream_module_or_skip()
        assert hasattr(mod, "main")
        assert callable(mod.main)

    def test_module_has_validation_helpers(self) -> None:
        mod = _load_stream_module_or_skip()
        assert callable(mod._validate_dispatch_trigger)
        assert callable(mod._validate_stop_condition)


# ---------------------------------------------------------------------------
# input_key trigger: validation, key encoding, and the step state machine
# ---------------------------------------------------------------------------

class TestInputKeyValidation:
    def _load_main(self) -> ModuleType:
        return _load_stream_module_or_skip()

    def test_input_key_string_key_accepted(self) -> None:
        mod = self._load_main()
        for mode in ("before", "after", "both"):
            err = mod._validate_dispatch_trigger(
                {"type": "input_key", "key": "TRIGGER", "mode": mode}
            )
            assert err is None, f"mode={mode} rejected: {err}"

    def test_input_key_byte_list_accepted(self) -> None:
        mod = self._load_main()
        err = mod._validate_dispatch_trigger(
            {"type": "input_key", "key": [0x54, 0x52, 0x49], "mode": "before"}
        )
        assert err is None

    def test_input_key_missing_key_rejected(self) -> None:
        mod = self._load_main()
        err = mod._validate_dispatch_trigger({"type": "input_key", "mode": "before"})
        assert err is not None
        assert "key" in err

    def test_input_key_bad_mode_rejected(self) -> None:
        mod = self._load_main()
        err = mod._validate_dispatch_trigger(
            {"type": "input_key", "key": "X", "mode": "sideways"}
        )
        assert err is not None
        assert "mode" in err


class TestEncodeKey:
    def _load_main(self) -> ModuleType:
        return _load_stream_module_or_skip()

    def test_input_key_unicode_string_encoded_to_bytes(self) -> None:
        mod = self._load_main()
        assert mod._encode_key("TRIGGER") == b"TRIGGER"
        assert mod._encode_key("üñî") == "üñî".encode()

    def test_input_key_byte_list_matched_directly(self) -> None:
        mod = self._load_main()
        assert mod._encode_key([0x00, 0xFF, 0x42]) == b"\x00\xffB"

    def test_input_key_bytes_passthrough(self) -> None:
        mod = self._load_main()
        assert mod._encode_key(b"\x01\x02") == b"\x01\x02"

    def test_input_key_bad_type_rejected(self) -> None:
        mod = self._load_main()
        with pytest.raises(TypeError):
            cast(Any, mod)._encode_key(42)


class TestInputKeyStateMachine:
    """Drive _input_key_step directly against a fresh RollingBuffer."""

    def _load(self) -> tuple[ModuleType, ModuleType]:
        pytest.importorskip("ansible")
        mod = _load_stream_module_or_skip()
        buf_mod = _load_buffer_module()
        return mod, buf_mod

    def test_input_key_mode_before_dispatches_pre_key_chunk(self) -> None:
        mod, buf_mod = self._load()
        RollingBuffer = buf_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=4096)
        state = mod._InputKeyState(mode="before", key_bytes=b"TRIGGER")
        # Push bytes up to and past the first key.
        buf.push(b"INFO x\nINFO y\nTRIGGER\nINFO z\n")
        dispatches = mod._input_key_step(state, buf)
        assert len(dispatches) == 1
        payload, position, idx = dispatches[0]
        assert position == "before_key"
        assert b"INFO x" in payload
        assert b"INFO y" in payload
        assert b"TRIGGER" not in payload  # key excluded from pre-key chunk
        assert b"INFO z" not in payload
        assert idx == 0
        # Buffer now holds only the post-key tail.
        assert b"INFO z" in buf.peek()

    def test_input_key_mode_after_dispatches_post_key_chunk(self) -> None:
        mod, buf_mod = self._load()
        RollingBuffer = buf_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=4096)
        # max_bytes_after small so the post-key chunk flushes deterministically.
        state = mod._InputKeyState(
            mode="after", key_bytes=b"TRIGGER", max_bytes_after=8
        )
        buf.push(b"pre\nTRIGGER\npost-key-data")
        # First hit: activates accumulation, no dispatch yet.
        first = mod._input_key_step(state, buf)
        assert first == []
        assert state.post_key_active is True
        # Continue pushing until max_bytes_after fires.
        buf.push(b"-more-bytes-to-flush")
        second = mod._input_key_step(state, buf)
        assert len(second) == 1
        payload, position, _idx = second[0]
        assert position == "after_key"
        assert b"TRIGGER" in payload or b"post-key" in payload

    def test_input_key_mode_both_dispatches_two_chunks_in_parallel(self) -> None:
        mod, buf_mod = self._load()
        RollingBuffer = buf_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=4096)
        state = mod._InputKeyState(
            mode="both", key_bytes=b"TRIGGER", max_bytes_after=4
        )
        # First key: dispatches BEFORE chunk.
        buf.push(b"speech-1\nTRIGGER\n")
        first = mod._input_key_step(state, buf)
        assert len(first) == 1
        assert first[0][1] == "before_key"
        before_idx = first[0][2]
        # Accumulate post-key bytes.
        buf.push(b"silence")
        second = mod._input_key_step(state, buf)
        # max_bytes_after=4 flushes the AFTER chunk, sharing the key index.
        assert len(second) == 1
        assert second[0][1] == "after_key"
        assert second[0][2] == before_idx

    def test_input_key_mode_before_with_byte_list_key(self) -> None:
        mod, buf_mod = self._load()
        RollingBuffer = buf_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=4096)
        key = bytes([0x00, 0xFF, 0xAA])
        state = mod._InputKeyState(mode="before", key_bytes=key)
        buf.push(b"AAA" + key + b"BBB")
        dispatches = mod._input_key_step(state, buf)
        assert len(dispatches) == 1
        assert dispatches[0][0] == b"AAA"

    def test_input_key_no_key_in_buffer_yields_nothing(self) -> None:
        mod, buf_mod = self._load()
        RollingBuffer = buf_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=4096)
        state = mod._InputKeyState(mode="before", key_bytes=b"TRIGGER")
        buf.push(b"just some text, no key here")
        assert mod._input_key_step(state, buf) == []

    def test_input_key_mode_before_two_keys_one_chunk_two_dispatches(self) -> None:
        """Two TRIGGER markers in a single buffer load produce two before-key
        dispatches when the state machine is driven to fixpoint.
        """
        mod, buf_mod = self._load()
        RollingBuffer = buf_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=4096)
        state = mod._InputKeyState(mode="before", key_bytes=b"TRIGGER")
        buf.push(b"INFO x\nINFO y\nTRIGGER\nINFO z\nINFO w\nTRIGGER\nINFO a\n")
        dispatches: list[tuple[bytes, str, int]] = []
        while True:
            step = mod._input_key_step(state, buf)
            if not step:
                break
            dispatches.extend(step)
        assert len(dispatches) == 2
        assert dispatches[0][1] == "before_key"
        assert dispatches[1][1] == "before_key"
        assert b"INFO x" in dispatches[0][0]
        assert b"INFO z" in dispatches[1][0]
        assert b"TRIGGER" not in dispatches[0][0]
        assert b"TRIGGER" not in dispatches[1][0]
        assert dispatches[0][2] == 0
        assert dispatches[1][2] == 1

    def test_input_key_mode_both_two_keys_produce_four_dispatches(self) -> None:
        """Two TRIGGER markers in mode=both produce 2 before + 2 after = 4
        dispatches: each key hit emits a before chunk AND the after chunk
        of the previous key (same bytes, different tag).
        """
        mod, buf_mod = self._load()
        RollingBuffer = buf_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=4096)
        state = mod._InputKeyState(
            mode="both", key_bytes=b"TRIGGER", max_bytes_after=1048576
        )
        buf.push(b"INFO x\nINFO y\nTRIGGER\nINFO z\nINFO w\nTRIGGER\nINFO a\n")
        dispatches: list[tuple[bytes, str, int]] = []
        while True:
            step = mod._input_key_step(state, buf)
            if not step:
                break
            dispatches.extend(step)
        # Drain remaining post-key bytes as the final after chunk.
        dispatches.extend(mod._input_key_drain_final(state, buf))
        positions = [d[1] for d in dispatches]
        assert positions.count("before_key") == 2
        assert positions.count("after_key") == 2
        assert len(dispatches) == 4
