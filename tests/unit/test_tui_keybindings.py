"""Tests for TUI keybindings — importability, validation, and key handler."""

from __future__ import annotations

import pytest


class TestKeybindingsImports:
    def test_module_importable(self) -> None:
        from general_ludd.tui import keybindings

        assert keybindings is not None

    def test_tui_key_handler_class_exists(self) -> None:
        from general_ludd.tui.keybindings import TUIKeyHandler

        assert TUIKeyHandler is not None

    def test_valid_log_levels_frozenset(self) -> None:
        from general_ludd.tui.keybindings import _VALID_LOG_LEVELS

        assert "debug" in _VALID_LOG_LEVELS
        assert "info" in _VALID_LOG_LEVELS
        assert "error" in _VALID_LOG_LEVELS

    def test_dispatch_modes_defined(self) -> None:
        from general_ludd.tui.keybindings import DISPATCH_MODES

        assert "active" in DISPATCH_MODES


class TestValidateInt:
    def test_valid_int_in_range(self) -> None:
        from general_ludd.tui.keybindings import _validate_int

        result = _validate_int(42, "port", lo=1, hi=65535)
        assert result == 42

    def test_int_below_range_raises(self) -> None:
        from general_ludd.tui.keybindings import _validate_int

        with pytest.raises(ValueError, match="out of range"):
            _validate_int(0, "port", lo=1, hi=65535)

    def test_int_above_range_raises(self) -> None:
        from general_ludd.tui.keybindings import _validate_int

        with pytest.raises(ValueError, match="out of range"):
            _validate_int(99999, "port", lo=1, hi=65535)

    def test_bool_rejected_as_int(self) -> None:
        from general_ludd.tui.keybindings import _validate_int

        with pytest.raises(ValueError, match="must be an int"):
            _validate_int(True, "workers", lo=1, hi=16)

    def test_non_int_rejected(self) -> None:
        from general_ludd.tui.keybindings import _validate_int

        with pytest.raises(ValueError, match="must be an int"):
            _validate_int("42", "port", lo=1, hi=65535)


class TestValidateHost:
    def test_ipv4_literal_accepted(self) -> None:
        from general_ludd.tui.keybindings import _validate_host

        result = _validate_host("192.168.1.1")
        assert result == "192.168.1.1"

    def test_ipv6_literal_accepted(self) -> None:
        from general_ludd.tui.keybindings import _validate_host

        result = _validate_host("::1")
        assert result == "::1"

    def test_localhost_accepted(self) -> None:
        from general_ludd.tui.keybindings import _validate_host

        result = _validate_host("localhost")
        assert result == "localhost"

    def test_empty_string_raises(self) -> None:
        from general_ludd.tui.keybindings import _validate_host

        with pytest.raises(ValueError, match="non-empty"):
            _validate_host("")

    def test_non_string_raises(self) -> None:
        from general_ludd.tui.keybindings import _validate_host

        with pytest.raises(ValueError, match="non-empty"):
            _validate_host(None)

    def test_invalid_hostname_raises(self) -> None:
        from general_ludd.tui.keybindings import _validate_host

        with pytest.raises(ValueError, match="not a valid hostname"):
            _validate_host("host name with spaces")


class TestValidateLogLevel:
    def test_debug_accepted(self) -> None:
        from general_ludd.tui.keybindings import _validate_log_level

        result = _validate_log_level("debug")
        assert result == "debug"

    def test_warn_alias_maps_to_warning(self) -> None:
        from general_ludd.tui.keybindings import _validate_log_level

        result = _validate_log_level("warn")
        assert result == "warn"

    def test_unknown_level_raises(self) -> None:
        from general_ludd.tui.keybindings import _validate_log_level

        with pytest.raises(ValueError, match="not a recognized"):
            _validate_log_level("verbose")


class TestValidatePaths:
    def test_single_path_accepted(self) -> None:
        import tempfile

        from general_ludd.tui.keybindings import _validate_paths

        with tempfile.TemporaryDirectory() as td:
            result = _validate_paths([td], confine_root=None)
            assert len(result) == 1

    def test_empty_list_raises(self) -> None:
        from general_ludd.tui.keybindings import _validate_paths

        with pytest.raises(ValueError, match="must be a list"):
            _validate_paths("not-a-list", confine_root=None)

    def test_null_byte_rejected(self) -> None:
        from general_ludd.tui.keybindings import _validate_paths

        with pytest.raises(ValueError, match="null byte"):
            _validate_paths(["/tmp/foo\x00bar"], confine_root=None)
