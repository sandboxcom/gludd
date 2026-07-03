"""Unit tests for SecretAlias path/mount injection validation.

Covers: path traversal, shell injection, null byte, and valid paths.
"""

from __future__ import annotations

import pytest

from general_ludd.secrets.manager import SecretAlias

# ── valid paths ──────────────────────────────────────────────────────────

class TestSecretAliasValid:
    def test_simple_path(self) -> None:
        a = SecretAlias("myalias", "projects/my-project/cosign/my-key")
        assert a.alias == "myalias"
        assert a.path == "projects/my-project/cosign/my-key"
        assert a.mount == "secret"

    def test_path_with_dots_and_colons(self) -> None:
        a = SecretAlias("img", "image-pins/ghcr.io/openbao/openbao")
        assert a.path == "image-pins/ghcr.io/openbao/openbao"

    def test_path_with_underscores_and_hyphens(self) -> None:
        a = SecretAlias("x", "a-b_c/d_e-f")
        assert a.path == "a-b_c/d_e-f"

    def test_custom_mount(self) -> None:
        a = SecretAlias("x", "a/b", mount="kv")
        assert a.mount == "kv"

    def test_mount_with_dots_and_slashes(self) -> None:
        a = SecretAlias("x", "a/b", mount="secret/team-a")
        assert a.mount == "secret/team-a"

    def test_single_segment_path(self) -> None:
        a = SecretAlias("x", "justonething")
        assert a.path == "justonething"


# ── empty / missing path ─────────────────────────────────────────────────

class TestSecretAliasEmpty:
    def test_empty_path_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            SecretAlias("a", "")

    def test_default_mount_still_validates_path(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            SecretAlias("a", "")


# ── path traversal attacks ───────────────────────────────────────────────

class TestSecretAliasTraversal:
    def test_dotdot_segment(self) -> None:
        with pytest.raises(ValueError, match="traversal"):
            SecretAlias("a", "../../../etc/passwd")

    def test_dotdot_mid_path(self) -> None:
        with pytest.raises(ValueError, match="traversal"):
            SecretAlias("a", "projects/../victim/cosign/default")

    def test_dotdot_single_segment(self) -> None:
        with pytest.raises(ValueError, match="traversal"):
            SecretAlias("a", "..")

    def test_dotdot_leading(self) -> None:
        with pytest.raises(ValueError, match="traversal"):
            SecretAlias("a", "../x")

    def test_tilde_in_path(self) -> None:
        with pytest.raises(ValueError, match="tilde"):
            SecretAlias("a", "~root/.ssh/id_rsa")

    def test_tilde_with_slash(self) -> None:
        with pytest.raises(ValueError, match="tilde"):
            SecretAlias("a", "~/etc/passwd")


# ── shell / command injection ────────────────────────────────────────────

class TestSecretAliasInjection:
    def test_semicolon_curl(self) -> None:
        with pytest.raises(ValueError, match="invalid characters"):
            SecretAlias("a", "foo; curl evil.com")

    def test_dollar_subshell(self) -> None:
        with pytest.raises(ValueError, match="invalid characters"):
            SecretAlias("a", "foo/$(id)/bar")

    def test_backtick_substitution(self) -> None:
        with pytest.raises(ValueError, match="invalid characters"):
            SecretAlias("a", "foo/`id`/bar")

    def test_pipe_injection(self) -> None:
        with pytest.raises(ValueError, match="invalid characters"):
            SecretAlias("a", "foo|cat /etc/passwd")

    def test_ampersand_injection(self) -> None:
        with pytest.raises(ValueError, match="invalid characters"):
            SecretAlias("a", "foo&rm -rf /")

    def test_brace_expansion(self) -> None:
        with pytest.raises(ValueError, match="invalid characters"):
            SecretAlias("a", "foo{bar,baz}")

    def test_space_in_path(self) -> None:
        with pytest.raises(ValueError, match="invalid characters"):
            SecretAlias("a", "foo bar")

    def test_newline_in_path(self) -> None:
        with pytest.raises(ValueError, match="invalid characters"):
            SecretAlias("a", "foo\nbar")

    def test_percent_encoding(self) -> None:
        with pytest.raises(ValueError, match="invalid characters"):
            SecretAlias("a", "..%2F..%2Fetc/passwd")

    def test_hash_in_path(self) -> None:
        with pytest.raises(ValueError, match="invalid characters"):
            SecretAlias("a", "foo#comment")


# ── null byte injection ──────────────────────────────────────────────────

class TestSecretAliasNullByte:
    def test_null_in_path(self) -> None:
        with pytest.raises(ValueError, match="null byte"):
            SecretAlias("a", "foo\x00bar")

    def test_null_leading(self) -> None:
        with pytest.raises(ValueError, match="null byte"):
            SecretAlias("a", "\x00etc/passwd")

    def test_null_in_mount(self) -> None:
        with pytest.raises(ValueError, match="null byte"):
            SecretAlias("a", "valid/path", mount="sec\x00ret")


# ── mount injection ──────────────────────────────────────────────────────

class TestSecretAliasMountInjection:
    def test_semicolon_in_mount(self) -> None:
        with pytest.raises(ValueError, match="invalid characters"):
            SecretAlias("a", "valid/path", mount="secret; curl evil.com")

    def test_dollar_in_mount(self) -> None:
        with pytest.raises(ValueError, match="invalid characters"):
            SecretAlias("a", "valid/path", mount="$(id)")

    def test_backtick_in_mount(self) -> None:
        with pytest.raises(ValueError, match="invalid characters"):
            SecretAlias("a", "valid/path", mount="`id`")

    def test_pipe_in_mount(self) -> None:
        with pytest.raises(ValueError, match="invalid characters"):
            SecretAlias("a", "valid/path", mount="x|y")


# ── path with dots that are NOT traversal ────────────────────────────────

class TestSecretAliasDotsNonTraversal:
    def test_single_dot_allowed(self) -> None:
        a = SecretAlias("x", "ghcr.io/openbao/openbao")
        assert a.path == "ghcr.io/openbao/openbao"

    def test_dotdot_as_substring_allowed(self) -> None:
        a = SecretAlias("x", "foo..bar/baz")
        assert a.path == "foo..bar/baz"
