"""Unit tests for the RollingBuffer input-key helpers (find_key / split_at / peek).

Loaded directly from the module_utils path so no ansible dependency is required.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parent.parent.parent
BUFFER_PATH = (
    ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
    / "plugins"
    / "module_utils"
    / "gludd_stream_buffer.py"
)


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


class TestFindKey:
    def test_find_key_returns_offset(self, buffer_mod: ModuleType) -> None:
        RollingBuffer = buffer_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=64)
        buf.push(b"INFO x\nINFO y\nTRIGGER\nINFO z\n")
        assert buf.find_key(b"TRIGGER") == 14

    def test_find_key_returns_none_when_absent(self, buffer_mod: ModuleType) -> None:
        RollingBuffer = buffer_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=64)
        buf.push(b"nothing here\n")
        assert buf.find_key(b"TRIGGER") is None

    def test_find_key_at_start_returns_zero(self, buffer_mod: ModuleType) -> None:
        RollingBuffer = buffer_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=64)
        buf.push(b"KEY then more")
        assert buf.find_key(b"KEY") == 0

    def test_find_key_empty_returns_none(self, buffer_mod: ModuleType) -> None:
        RollingBuffer = buffer_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=64)
        buf.push(b"abc")
        assert buf.find_key(b"") is None


class TestSplitAt:
    def test_split_at_resets_buffer_to_tail(self, buffer_mod: ModuleType) -> None:
        RollingBuffer = buffer_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=64)
        buf.push(b"headTAIL")
        head, tail = buf.split_at(4)
        assert head == b"head"
        assert tail == b"TAIL"
        assert buf.peek() == b"TAIL"
        assert len(buf) == 4

    def test_split_at_zero(self, buffer_mod: ModuleType) -> None:
        RollingBuffer = buffer_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=64)
        buf.push(b"abcde")
        head, tail = buf.split_at(0)
        assert head == b""
        assert tail == b"abcde"
        assert buf.peek() == b"abcde"

    def test_split_at_past_end(self, buffer_mod: ModuleType) -> None:
        RollingBuffer = buffer_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=64)
        buf.push(b"abc")
        head, tail = buf.split_at(99)
        assert head == b"abc"
        assert tail == b""
        assert len(buf) == 0


class TestPeek:
    def test_peek_head(self, buffer_mod: ModuleType) -> None:
        RollingBuffer = buffer_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=64)
        buf.push(b"hello world")
        assert buf.peek_head(5) == b"hello"
        assert buf.peek() == b"hello world"  # non-destructive

    def test_peek_tail(self, buffer_mod: ModuleType) -> None:
        RollingBuffer = buffer_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=64)
        buf.push(b"hello world")
        assert buf.peek_tail(5) == b"world"
        assert buf.peek() == b"hello world"

    def test_peek_head_more_than_buffer(self, buffer_mod: ModuleType) -> None:
        RollingBuffer = buffer_mod.RollingBuffer
        buf = RollingBuffer(max_bytes=64)
        buf.push(b"abc")
        assert buf.peek_head(10) == b"abc"
