"""Deep tests for galaxy.py input validation — the injection-prevention regex layer.

Existing tests mock subprocess.run and never exercise the validators.
These tests exercise _validate_galaxy_type, _validate_name_spec, and
_validate_search_query directly against the full threat model.
"""

from __future__ import annotations

import pytest

from general_ludd.ansible.galaxy import (
    _validate_galaxy_type,
    _validate_name_spec,
    _validate_search_query,
)


class TestValidateGalaxyType:
    def test_accepts_role(self):
        assert _validate_galaxy_type("role") == "role"

    def test_accepts_collection(self):
        assert _validate_galaxy_type("collection") == "collection"

    def test_rejects_unknown_type(self):
        with pytest.raises(ValueError, match="invalid galaxy_type"):
            _validate_galaxy_type("module")

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError, match="invalid galaxy_type"):
            _validate_galaxy_type("")

    def test_rejects_upper_case_role(self):
        with pytest.raises(ValueError, match="invalid galaxy_type"):
            _validate_galaxy_type("Role")

    def test_rejects_numeric_string(self):
        with pytest.raises(ValueError, match="invalid galaxy_type"):
            _validate_galaxy_type("123")


class TestValidateNameSpec:
    def test_accepts_namespace_dot_name(self):
        assert _validate_name_spec("geerlingguy.nginx") == "geerlingguy.nginx"

    def test_accepts_three_segment_name(self):
        assert _validate_name_spec("community.general.foo") == "community.general.foo"

    def test_accepts_with_version(self):
        assert _validate_name_spec("geerlingguy.nginx:1.0.0") == "geerlingguy.nginx:1.0.0"

    def test_accepts_version_with_prerelease(self):
        assert _validate_name_spec("community.general:2.0.0-beta.1") == "community.general:2.0.0-beta.1"

    def test_accepts_version_with_build_metadata(self):
        assert _validate_name_spec("ns.name:1.0.0+build123") == "ns.name:1.0.0+build123"

    def test_accepts_two_segment_minimum(self):
        assert _validate_name_spec("a.b") == "a.b"

    def test_rejects_single_segment(self):
        with pytest.raises(ValueError, match="invalid galaxy name"):
            _validate_name_spec("nginx")

    def test_rejects_leading_dash_option_injection(self):
        with pytest.raises(ValueError, match="may not begin with '-'"):
            _validate_name_spec("-r")

    def test_rejects_leading_dash_with_valid_name_form(self):
        with pytest.raises(ValueError, match="may not begin with '-'"):
            _validate_name_spec("-r /etc/passwd")

    def test_rejects_whitespace_in_name(self):
        with pytest.raises(ValueError, match="invalid galaxy name"):
            _validate_name_spec("a.b c")

    def test_rejects_shell_metacharacter_semicolon(self):
        with pytest.raises(ValueError):
            _validate_name_spec("a.b;cat /etc/passwd")

    def test_rejects_shell_metacharacter_pipe(self):
        with pytest.raises(ValueError):
            _validate_name_spec("a.b|id")

    def test_rejects_shell_metacharacter_backtick(self):
        with pytest.raises(ValueError):
            _validate_name_spec("a.b`id`")

    def test_rejects_newline_in_name(self):
        with pytest.raises(ValueError):
            _validate_name_spec("a.b\nc.d")

    def test_rejects_null_byte_in_name(self):
        with pytest.raises(ValueError):
            _validate_name_spec("a.b\x00c")

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError, match="must be a non-empty string"):
            _validate_name_spec("")

    def test_rejects_non_string_input(self):
        with pytest.raises(ValueError, match="must be a non-empty string"):
            _validate_name_spec(None)

    def test_rejects_segment_starting_with_digit(self):
        with pytest.raises(ValueError, match="invalid galaxy name"):
            _validate_name_spec("0ns.name")

    def test_rejects_segment_with_uppercase(self):
        with pytest.raises(ValueError, match="invalid galaxy name"):
            _validate_name_spec("MyNs.Name")

    def test_rejects_version_not_starting_with_digit(self):
        with pytest.raises(ValueError, match="invalid galaxy version"):
            _validate_name_spec("ns.name:abc")

    def test_rejects_version_with_leading_dash(self):
        with pytest.raises(ValueError, match="invalid galaxy version"):
            _validate_name_spec("ns.name:-1.0")

    def test_rejects_version_with_whitespace(self):
        with pytest.raises(ValueError, match="invalid galaxy version"):
            _validate_name_spec("ns.name:1.0 x")

    def test_accepts_version_with_uppercase_elements(self):
        assert _validate_name_spec("ns.name:2.0.0-RC1") == "ns.name:2.0.0-RC1"

    def test_rejects_segment_with_hyphen(self):
        with pytest.raises(ValueError, match="invalid galaxy name"):
            _validate_name_spec("my-ns.name")

    def test_accepts_segment_with_trailing_numbers(self):
        assert _validate_name_spec("community.general2") == "community.general2"

    def test_rejects_path_traversal_in_name(self):
        with pytest.raises(ValueError):
            _validate_name_spec("../etc/passwd")

    def test_rejects_path_traversal_with_dots_preceding(self):
        with pytest.raises(ValueError):
            _validate_name_spec("..")

    def test_rejects_dollar_sign_in_name(self):
        with pytest.raises(ValueError):
            _validate_name_spec("a.$(whoami)")


class TestValidateSearchQuery:
    def test_accepts_simple_query(self):
        assert _validate_search_query("nginx") == "nginx"

    def test_accepts_query_with_dots(self):
        assert _validate_search_query("community.general") == "community.general"

    def test_accepts_query_with_hyphens(self):
        assert _validate_search_query("nginx-role") == "nginx-role"

    def test_accepts_query_with_underscores(self):
        assert _validate_search_query("my_role") == "my_role"

    def test_accepts_numeric_start(self):
        assert _validate_search_query("123abc") == "123abc"

    def test_rejects_leading_dash_option_injection(self):
        with pytest.raises(ValueError, match="may not begin with '-'"):
            _validate_search_query("-r")

    def test_rejects_whitespace(self):
        with pytest.raises(ValueError, match="invalid search query"):
            _validate_search_query("nginx role")

    def test_rejects_tab_character(self):
        with pytest.raises(ValueError, match="invalid search query"):
            _validate_search_query("nginx\trole")

    def test_rejects_shell_pipe(self):
        with pytest.raises(ValueError, match="invalid search query"):
            _validate_search_query("x|id")

    def test_rejects_shell_semicolon(self):
        with pytest.raises(ValueError, match="invalid search query"):
            _validate_search_query("x;id")

    def test_rejects_backtick(self):
        with pytest.raises(ValueError, match="invalid search query"):
            _validate_search_query("x`id`")

    def test_rejects_dollar_sign(self):
        with pytest.raises(ValueError, match="invalid search query"):
            _validate_search_query("$(whoami)")

    def test_rejects_newline(self):
        with pytest.raises(ValueError, match="invalid search query"):
            _validate_search_query("a\nb")

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError, match="must be a non-empty string"):
            _validate_search_query("")

    def test_rejects_leading_whitespace(self):
        with pytest.raises(ValueError, match="invalid search query"):
            _validate_search_query(" nginx")

    def test_rejects_trailing_whitespace(self):
        with pytest.raises(ValueError, match="invalid search query"):
            _validate_search_query("nginx ")

    def test_rejects_angle_brackets(self):
        with pytest.raises(ValueError, match="invalid search query"):
            _validate_search_query("x<y")

    def test_rejects_parentheses(self):
        with pytest.raises(ValueError, match="invalid search query"):
            _validate_search_query("a(b)")

    def test_rejects_asterisk_glob(self):
        with pytest.raises(ValueError, match="invalid search query"):
            _validate_search_query("nginx*")

    def test_rejects_slash(self):
        with pytest.raises(ValueError, match="invalid search query"):
            _validate_search_query("a/b")

    def test_rejects_non_string(self):
        with pytest.raises(ValueError, match="must be a non-empty string"):
            _validate_search_query(None)


class TestParseGalaxySearchOutputEdgeCases:
    def test_header_line_different_casing(self):
        from general_ludd.ansible.galaxy import parse_galaxy_search_output

        output = "Name              Description\n----              -----------\nns.name    desc"
        results = parse_galaxy_search_output(output)
        assert results == [{"name": "ns.name", "description": "desc"}]

    def test_line_without_whitespace_between_name_and_desc_is_skipped(self):
        from general_ludd.ansible.galaxy import parse_galaxy_search_output

        output = "singleword"
        results = parse_galaxy_search_output(output)
        assert results == []

    def test_only_found_header_skipped(self):
        from general_ludd.ansible.galaxy import parse_galaxy_search_output

        output = "Found 0 roles matching your search:\n\n"
        results = parse_galaxy_search_output(output)
        assert results == []

    def test_empty_line_skipped(self):
        from general_ludd.ansible.galaxy import parse_galaxy_search_output

        output = "\n\n   \nns.name    desc\n\n"
        results = parse_galaxy_search_output(output)
        assert len(results) == 1
