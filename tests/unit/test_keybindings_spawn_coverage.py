"""Coverage tests for gunicorn spawn validator functions in tui/keybindings.py.

Targets:
  src/general_ludd/tui/keybindings.py
    _validate_int
    _validate_host
    _validate_log_level
    _validate_paths
    validate_gunicorn_spawn_args
    build_gunicorn_cmd
"""
from __future__ import annotations

import os
import tempfile

import pytest

from general_ludd.tui.keybindings import (
    _validate_host,
    _validate_int,
    _validate_log_level,
    _validate_paths,
    build_gunicorn_cmd,
    validate_gunicorn_spawn_args,
)


# ---------------------------------------------------------------------------
# _validate_int
# ---------------------------------------------------------------------------

class TestValidateInt:
    def test_valid_lo_boundary(self) -> None:
        assert _validate_int(1, "port", lo=1, hi=65535) == 1

    def test_valid_hi_boundary(self) -> None:
        assert _validate_int(65535, "port", lo=1, hi=65535) == 65535

    def test_valid_midrange(self) -> None:
        assert _validate_int(8000, "port", lo=1, hi=65535) == 8000

    def test_below_lo_raises(self) -> None:
        with pytest.raises(ValueError, match="out of range"):
            _validate_int(0, "port", lo=1, hi=65535)

    def test_above_hi_raises(self) -> None:
        with pytest.raises(ValueError, match="out of range"):
            _validate_int(65536, "port", lo=1, hi=65535)

    def test_bool_true_rejected(self) -> None:
        """bool is a subclass of int — True must NOT be accepted as 1."""
        with pytest.raises(ValueError, match="must be an int"):
            _validate_int(True, "port", lo=1, hi=65535)

    def test_bool_false_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be an int"):
            _validate_int(False, "workers", lo=1, hi=4096)

    def test_string_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be an int"):
            _validate_int("8000", "port", lo=1, hi=65535)

    def test_float_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be an int"):
            _validate_int(8000.0, "port", lo=1, hi=65535)

    def test_none_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be an int"):
            _validate_int(None, "port", lo=1, hi=65535)

    def test_workers_lo_boundary(self) -> None:
        assert _validate_int(1, "workers", lo=1, hi=4096) == 1

    def test_workers_hi_boundary(self) -> None:
        assert _validate_int(4096, "workers", lo=1, hi=4096) == 4096

    def test_workers_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="out of range"):
            _validate_int(0, "workers", lo=1, hi=4096)


# ---------------------------------------------------------------------------
# _validate_host
# ---------------------------------------------------------------------------

class TestValidateHost:
    # --- happy paths ---

    def test_ipv4_loopback(self) -> None:
        assert _validate_host("127.0.0.1") == "127.0.0.1"

    def test_ipv4_wildcard(self) -> None:
        assert _validate_host("0.0.0.0") == "0.0.0.0"

    def test_ipv6_loopback(self) -> None:
        assert _validate_host("::1") == "::1"

    def test_simple_hostname(self) -> None:
        assert _validate_host("localhost") == "localhost"

    def test_fqdn(self) -> None:
        assert _validate_host("my-host.example.com") == "my-host.example.com"

    def test_single_char_hostname(self) -> None:
        """Single-char host like 'a' must be accepted by the regex."""
        assert _validate_host("a") == "a"

    # --- rejection ---

    def test_non_str_int_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_host(8080)

    def test_non_str_none_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_host(None)

    def test_empty_string_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_host("")

    def test_colon_port_injection_rejected(self) -> None:
        """'host:port' smuggles a port via colon — must be rejected."""
        with pytest.raises(ValueError, match="not a valid hostname"):
            _validate_host("localhost:8080")

    def test_space_in_host_rejected(self) -> None:
        with pytest.raises(ValueError, match="not a valid hostname"):
            _validate_host("local host")

    def test_shell_metachar_in_host_rejected(self) -> None:
        with pytest.raises(ValueError, match="not a valid hostname"):
            _validate_host("host$(id)")

    def test_leading_hyphen_rejected(self) -> None:
        with pytest.raises(ValueError, match="not a valid hostname"):
            _validate_host("-badhost")

    def test_leading_dot_rejected(self) -> None:
        with pytest.raises(ValueError, match="not a valid hostname"):
            _validate_host(".badhost")

    def test_trailing_hyphen_rejected(self) -> None:
        with pytest.raises(ValueError, match="not a valid hostname"):
            _validate_host("badhost-")


# ---------------------------------------------------------------------------
# _validate_log_level
# ---------------------------------------------------------------------------

class TestValidateLogLevel:
    @pytest.mark.parametrize("level,expected", [
        ("debug", "debug"),
        ("info", "info"),
        ("warning", "warning"),
        ("warn", "warn"),
        ("error", "error"),
        ("critical", "critical"),
    ])
    def test_valid_levels_lowercase(self, level: str, expected: str) -> None:
        assert _validate_log_level(level) == expected

    @pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL"])
    def test_valid_levels_uppercase_lowered(self, level: str) -> None:
        result = _validate_log_level(level)
        assert result == level.lower()

    def test_mixed_case_lowered(self) -> None:
        assert _validate_log_level("Warning") == "warning"

    def test_trace_rejected(self) -> None:
        """'trace' is not in the valid set."""
        with pytest.raises(ValueError, match="not a recognized level"):
            _validate_log_level("trace")

    def test_none_rejected(self) -> None:
        with pytest.raises(ValueError, match="not a recognized level"):
            _validate_log_level(None)

    def test_empty_string_rejected(self) -> None:
        with pytest.raises(ValueError, match="not a recognized level"):
            _validate_log_level("")

    def test_int_rejected(self) -> None:
        with pytest.raises(ValueError, match="not a recognized level"):
            _validate_log_level(1)

    def test_verbose_rejected(self) -> None:
        with pytest.raises(ValueError, match="not a recognized level"):
            _validate_log_level("verbose")


# ---------------------------------------------------------------------------
# _validate_paths
# ---------------------------------------------------------------------------

class TestValidatePaths:
    def test_non_list_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be a list"):
            _validate_paths("/tmp/foo", confine_root=None)

    def test_tuple_accepted(self) -> None:
        """Tuples are accepted (isinstance check covers tuple)."""
        with tempfile.TemporaryDirectory() as td:
            result = _validate_paths((td,), confine_root=None)
            assert isinstance(result, list)

    def test_empty_string_elem_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_paths([""], confine_root=None)

    def test_null_byte_in_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="null byte"):
            _validate_paths(["/tmp/foo\x00bar"], confine_root=None)

    def test_none_elem_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_paths([None], confine_root=None)

    def test_valid_path_no_confinement(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = _validate_paths([td], confine_root=None)
            assert len(result) == 1
            assert os.path.isabs(result[0])

    def test_path_within_root_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            subdir = os.path.join(root, "sub")
            os.makedirs(subdir)
            result = _validate_paths([subdir], confine_root=root)
            assert result[0] == os.path.realpath(subdir)

    def test_path_escaping_root_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            with tempfile.TemporaryDirectory() as other:
                with pytest.raises(ValueError, match="escapes confinement"):
                    _validate_paths([other], confine_root=root)

    def test_path_traversal_rejected(self) -> None:
        """A path like /root/sub/../../other must not escape confinement."""
        with tempfile.TemporaryDirectory() as root:
            with tempfile.TemporaryDirectory() as other:
                # Construct a traversal path that resolves outside root
                traversal = os.path.join(root, "sub", "..", "..", other.lstrip("/"))
                # Only test if the traversal actually resolves outside root
                real = os.path.realpath(traversal)
                real_root = os.path.realpath(root)
                if real != real_root and not real.startswith(real_root + os.sep):
                    with pytest.raises(ValueError, match="escapes confinement"):
                        _validate_paths([traversal], confine_root=root)


# ---------------------------------------------------------------------------
# validate_gunicorn_spawn_args
# ---------------------------------------------------------------------------

class TestValidateGunicornSpawnArgs:
    def test_valid_args_no_exception(self) -> None:
        validate_gunicorn_spawn_args(host="127.0.0.1", port=8000, workers=2)

    def test_bad_host_raises(self) -> None:
        with pytest.raises(ValueError):
            validate_gunicorn_spawn_args(host="host:port", port=8000, workers=2)

    def test_port_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="out of range"):
            validate_gunicorn_spawn_args(host="127.0.0.1", port=0, workers=2)

    def test_port_too_large_raises(self) -> None:
        with pytest.raises(ValueError, match="out of range"):
            validate_gunicorn_spawn_args(host="127.0.0.1", port=99999, workers=2)

    def test_workers_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="out of range"):
            validate_gunicorn_spawn_args(host="127.0.0.1", port=8000, workers=0)

    def test_workers_bool_raises(self) -> None:
        with pytest.raises(ValueError, match="must be an int"):
            validate_gunicorn_spawn_args(host="127.0.0.1", port=8000, workers=True)

    def test_bad_log_level_raises(self) -> None:
        with pytest.raises(ValueError, match="not a recognized level"):
            validate_gunicorn_spawn_args(host="127.0.0.1", port=8000, log_level="trace")

    def test_valid_log_level_no_exception(self) -> None:
        validate_gunicorn_spawn_args(host="127.0.0.1", port=8000, log_level="info")

    def test_log_level_none_skipped(self) -> None:
        """log_level=None (default) must not raise."""
        validate_gunicorn_spawn_args(host="127.0.0.1", port=8000)

    def test_paths_escaping_root_raises(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            with tempfile.TemporaryDirectory() as other:
                with pytest.raises(ValueError, match="escapes confinement"):
                    validate_gunicorn_spawn_args(
                        host="127.0.0.1",
                        port=8000,
                        paths=[other],
                        confine_root=root,
                    )

    def test_paths_none_skipped(self) -> None:
        """paths=None (default) must not raise."""
        validate_gunicorn_spawn_args(host="127.0.0.1", port=8000, paths=None)

    def test_ipv6_host_accepted(self) -> None:
        validate_gunicorn_spawn_args(host="::1", port=8000, workers=1)


# ---------------------------------------------------------------------------
# build_gunicorn_cmd
# ---------------------------------------------------------------------------

class TestBuildGunicornCmd:
    def test_basic_argv_shape(self) -> None:
        cmd = build_gunicorn_cmd(host="127.0.0.1", port=8000, workers=2)
        assert cmd[0] == "gunicorn"
        assert "--bind" in cmd
        bind_idx = cmd.index("--bind")
        assert cmd[bind_idx + 1] == "127.0.0.1:8000"
        assert "--workers" in cmd
        workers_idx = cmd.index("--workers")
        assert cmd[workers_idx + 1] == "2"

    def test_no_log_level_flag_when_none(self) -> None:
        cmd = build_gunicorn_cmd(host="127.0.0.1", port=8000, workers=1)
        assert "--log-level" not in cmd

    def test_log_level_appended_when_set(self) -> None:
        cmd = build_gunicorn_cmd(host="127.0.0.1", port=8000, workers=1, log_level="warning")
        assert "--log-level" in cmd
        ll_idx = cmd.index("--log-level")
        assert cmd[ll_idx + 1] == "warning"

    def test_log_level_lowercased(self) -> None:
        """build_gunicorn_cmd must lower the log level in the argv."""
        cmd = build_gunicorn_cmd(host="127.0.0.1", port=8000, workers=1, log_level="WARNING")
        ll_idx = cmd.index("--log-level")
        assert cmd[ll_idx + 1] == "warning"

    def test_workers_as_string_in_argv(self) -> None:
        """The --workers value in the argv must be a string, not an int."""
        cmd = build_gunicorn_cmd(host="127.0.0.1", port=8000, workers=4)
        workers_idx = cmd.index("--workers")
        assert isinstance(cmd[workers_idx + 1], str)
        assert cmd[workers_idx + 1] == "4"

    def test_bad_host_raises_before_build(self) -> None:
        with pytest.raises(ValueError):
            build_gunicorn_cmd(host="bad host!", port=8000)

    def test_bad_port_raises_before_build(self) -> None:
        with pytest.raises(ValueError, match="out of range"):
            build_gunicorn_cmd(host="127.0.0.1", port=0)

    def test_bad_workers_raises_before_build(self) -> None:
        with pytest.raises(ValueError):
            build_gunicorn_cmd(host="127.0.0.1", port=8000, workers=-1)

    def test_bad_log_level_raises_before_build(self) -> None:
        with pytest.raises(ValueError, match="not a recognized level"):
            build_gunicorn_cmd(host="127.0.0.1", port=8000, log_level="trace")

    def test_ipv4_wildcard_in_bind(self) -> None:
        cmd = build_gunicorn_cmd(host="0.0.0.0", port=9000, workers=1)
        bind_idx = cmd.index("--bind")
        assert cmd[bind_idx + 1] == "0.0.0.0:9000"

    def test_result_is_list_of_strings(self) -> None:
        cmd = build_gunicorn_cmd(host="localhost", port=8080, workers=1)
        assert isinstance(cmd, list)
        assert all(isinstance(s, str) for s in cmd)

    def test_worker_class_present(self) -> None:
        cmd = build_gunicorn_cmd(host="127.0.0.1", port=8000, workers=1)
        assert "--worker-class" in cmd
        wc_idx = cmd.index("--worker-class")
        assert "Uvicorn" in cmd[wc_idx + 1] or "uvicorn" in cmd[wc_idx + 1].lower()
