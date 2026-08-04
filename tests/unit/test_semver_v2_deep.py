"""Deep tests for semver 2.0 parsing, comparison, prerelease, build metadata, ranges."""

from __future__ import annotations

import pytest


class TestParseBasic:
    def test_parse_simple(self) -> None:
        from general_ludd.util.semver_v2 import parse

        v = parse("1.2.3")
        assert (v.major, v.minor, v.patch) == (1, 2, 3)
        assert v.prerelease == ()
        assert v.build == ()

    def test_parse_with_v_prefix(self) -> None:
        from general_ludd.util.semver_v2 import parse

        v = parse("v2.0.0")
        assert (v.major, v.minor, v.patch) == (2, 0, 0)

    def test_parse_with_prerelease(self) -> None:
        from general_ludd.util.semver_v2 import parse

        v = parse("1.0.0-alpha.1")
        assert v.prerelease == ("alpha", 1)

    def test_parse_with_prerelease_numeric(self) -> None:
        from general_ludd.util.semver_v2 import parse

        v = parse("1.0.0-0.3.7")
        assert v.prerelease == (0, 3, 7)

    def test_parse_with_build_metadata(self) -> None:
        from general_ludd.util.semver_v2 import parse

        v = parse("1.0.0+build.123")
        assert v.build == ("build", "123")

    def test_parse_with_prerelease_and_build(self) -> None:
        from general_ludd.util.semver_v2 import parse

        v = parse("1.0.0-rc.1+build.abc")
        assert v.prerelease == ("rc", 1)
        assert v.build == ("build", "abc")

    def test_parse_invalid_raises(self) -> None:
        from general_ludd.util.semver_v2 import parse

        with pytest.raises(ValueError):
            parse("not-a-version")
        with pytest.raises(ValueError):
            parse("01.1.1")
        with pytest.raises(ValueError):
            parse("1.02.1")
        with pytest.raises(ValueError):
            parse("a.b.c")


class TestParseZeroVersions:
    def test_parse_major_zero(self) -> None:
        from general_ludd.util.semver_v2 import parse

        v = parse("0.1.0")
        assert str(v) == "0.1.0"

    def test_parse_all_zeros(self) -> None:
        from general_ludd.util.semver_v2 import parse

        v = parse("0.0.0")
        assert (v.major, v.minor, v.patch) == (0, 0, 0)

    def test_parse_zero_patch_with_prerelease(self) -> None:
        from general_ludd.util.semver_v2 import parse

        v = parse("0.0.1-beta")
        assert v.prerelease == ("beta",)


class TestComparison:
    def test_equal_versions(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.0.0") == parse("v1.0.0")

    def test_patch_ordering(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.0.0") < parse("1.0.1")
        assert parse("1.0.2") > parse("1.0.1")

    def test_minor_ordering(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.1.0") > parse("1.0.9")
        assert parse("1.0.9") < parse("1.1.0")

    def test_major_ordering(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("2.0.0") > parse("1.9.9")
        assert parse("1.9.9") < parse("2.0.0")

    def test_sort_list(self) -> None:
        from general_ludd.util.semver_v2 import parse

        versions = ["1.0.0", "1.0.1", "0.9.9", "2.0.0", "1.1.0"]
        parsed = sorted([parse(v) for v in versions])
        result = [str(v) for v in parsed]
        assert result == ["0.9.9", "1.0.0", "1.0.1", "1.1.0", "2.0.0"]


class TestPrereleaseComparison:
    def test_prerelease_less_than_release(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.0.0-alpha") < parse("1.0.0")
        assert parse("1.0.0") > parse("1.0.0-alpha")

    def test_prerelease_with_same_core(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.0.0-alpha") < parse("1.0.0-alpha.1")
        assert parse("1.0.0-alpha.1") < parse("1.0.0-alpha.beta")
        assert parse("1.0.0-alpha.beta") < parse("1.0.0-beta")
        assert parse("1.0.0-beta") < parse("1.0.0-beta.2")
        assert parse("1.0.0-beta.2") < parse("1.0.0-beta.11")
        assert parse("1.0.0-beta.11") < parse("1.0.0-rc.1")

    def test_numeric_identifiers_sort_lower_than_alpha(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.0.0-1") < parse("1.0.0-alpha")
        assert parse("1.0.0-alpha") > parse("1.0.0-1")

    def test_fewer_prerelease_fields_wins(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.0.0-alpha") < parse("1.0.0-alpha.1")
        assert parse("1.0.0-alpha.1") > parse("1.0.0-alpha")

    def test_prerelease_sorting_mixed(self) -> None:
        from general_ludd.util.semver_v2 import sort_versions

        versions = [
            "1.0.0",
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-alpha.beta",
            "1.0.0-beta",
            "1.0.0-beta.2",
            "1.0.0-beta.11",
            "1.0.0-rc.1",
            "1.0.0-1",
        ]
        sorted_v = sort_versions(versions)
        assert sorted_v == [
            "1.0.0-1",
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-alpha.beta",
            "1.0.0-beta",
            "1.0.0-beta.2",
            "1.0.0-beta.11",
            "1.0.0-rc.1",
            "1.0.0",
        ]


class TestBuildMetadata:
    def test_build_metadata_ignored_in_comparison(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.0.0+build.1") == parse("1.0.0+build.2")

    def test_build_metadata_with_prerelease(self) -> None:
        from general_ludd.util.semver_v2 import parse

        a = parse("1.0.0-alpha+build.1")
        b = parse("1.0.0-alpha+build.2")
        assert a == b

    def test_build_preserved_in_string(self) -> None:
        from general_ludd.util.semver_v2 import parse

        v = parse("1.0.0+build.123")
        assert "+build.123" in str(v)
        assert "build" in v.build


class TestStrAndRepr:
    def test_str_roundtrip(self) -> None:
        from general_ludd.util.semver_v2 import parse

        original = "1.2.3-alpha.1+build.42"
        assert str(parse(original)) == original

    def test_str_no_build(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert str(parse("2.0.0")) == "2.0.0"


class TestBump:
    def test_bump_major(self) -> None:
        from general_ludd.util.semver_v2 import parse

        v = parse("1.2.3")
        bumped = v.bump_major()
        assert str(bumped) == "2.0.0"

    def test_bump_minor(self) -> None:
        from general_ludd.util.semver_v2 import parse

        v = parse("1.2.3")
        bumped = v.bump_minor()
        assert str(bumped) == "1.3.0"

    def test_bump_patch(self) -> None:
        from general_ludd.util.semver_v2 import parse

        v = parse("1.2.3")
        bumped = v.bump_patch()
        assert str(bumped) == "1.2.4"

    def test_bump_preserves_prerelease_build(self) -> None:
        from general_ludd.util.semver_v2 import parse

        v = parse("1.2.3-alpha+build")
        bumped = v.bump_patch()
        assert bumped.prerelease == ()
        assert bumped.build == ()


class TestCoerce:
    def test_coerce_valid_semver(self) -> None:
        from general_ludd.util.semver_v2 import coerce

        v = coerce("1.2.3")
        assert str(v) == "1.2.3"

    def test_coerce_partial(self) -> None:
        from general_ludd.util.semver_v2 import coerce

        assert str(coerce("1.2")) == "1.2.0"
        assert str(coerce("1")) == "1.0.0"

    def test_coerce_from_tag(self) -> None:
        from general_ludd.util.semver_v2 import coerce

        v = coerce("v1.2.3-alpha+build")
        assert str(v) == "1.2.3-alpha+build"

    def test_coerce_garbage_raises(self) -> None:
        from general_ludd.util.semver_v2 import coerce

        with pytest.raises(ValueError):
            coerce("not-a-version")


class TestSatisfiesTilde:
    def test_tilde_patch_range(self) -> None:
        from general_ludd.util.semver_v2 import parse

        v = parse("1.2.3")
        assert v.satisfies("~1.2.3")
        assert v.satisfies("~1.2")
        assert not parse("1.3.0").satisfies("~1.2.3")

    def test_tilde_minor_range(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.2.0").satisfies("~1.2.0")
        assert parse("1.2.9").satisfies("~1.2")
        assert parse("1.3.0").satisfies("~1.3")
        assert not parse("1.4.0").satisfies("~1.2")
        assert not parse("1.3.0").satisfies("~1.2.3")


class TestSatisfiesCaret:
    def test_caret_nonzero_major(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.2.3").satisfies("^1.2.3")
        assert parse("1.9.9").satisfies("^1.2.3")
        assert not parse("2.0.0").satisfies("^1.2.3")

    def test_caret_zero_major_nonzero_minor(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("0.2.3").satisfies("^0.2.3")
        assert parse("0.2.9").satisfies("^0.2.3")
        assert not parse("0.3.0").satisfies("^0.2.3")

    def test_caret_zero_major_zero_minor(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("0.0.3").satisfies("^0.0.3")
        assert not parse("0.0.4").satisfies("^0.0.3")
        assert not parse("0.0.2").satisfies("^0.0.3")
        assert not parse("0.1.0").satisfies("^0.0.3")

    def test_caret_prerelease(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.2.3").satisfies("^1.2.2")
        assert not parse("1.2.2").satisfies("^1.2.3")


class TestSatisfiesComparisonOps:
    def test_greater_than(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("2.0.0").satisfies(">1.0.0")
        assert not parse("1.0.0").satisfies(">1.0.0")

    def test_less_than(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.0.0").satisfies("<2.0.0")
        assert not parse("2.0.0").satisfies("<1.0.0")

    def test_greater_equal(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.0.0").satisfies(">=1.0.0")
        assert parse("1.0.1").satisfies(">=1.0.0")
        assert not parse("0.9.9").satisfies(">=1.0.0")

    def test_less_equal(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.0.0").satisfies("<=1.0.0")
        assert parse("0.9.9").satisfies("<=1.0.0")
        assert not parse("1.0.1").satisfies("<=1.0.0")

    def test_not_equal(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.0.1").satisfies("!=1.0.0")
        assert not parse("1.0.0").satisfies("!=1.0.0")


class TestSatisfiesHyphenRange:
    def test_hyphen_range(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.2.3").satisfies("1.2.3 - 1.3.0")
        assert parse("1.3.0").satisfies("1.2.3 - 1.3.0")
        assert parse("1.2.5").satisfies("1.2.3 - 1.3.0")
        assert not parse("1.3.1").satisfies("1.2.3 - 1.3.0")
        assert not parse("1.2.2").satisfies("1.2.3 - 1.3.0")


class TestSatisfiesExact:
    def test_exact_version_match(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.2.3").satisfies("1.2.3")
        assert not parse("1.2.4").satisfies("1.2.3")

    def test_equals_prefix(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.2.3").satisfies("=1.2.3")
        assert not parse("1.2.4").satisfies("=1.2.3")


class TestSatisfiesWildcard:
    def test_star_matches_all(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("9.9.9").satisfies("*")
        assert parse("0.0.0").satisfies("*")
        assert parse("1.0.0-alpha").satisfies("*")


class TestMaxSatisfying:
    def test_returns_highest(self) -> None:
        from general_ludd.util.semver_v2 import max_satisfying

        result = max_satisfying(
            ["1.0.0", "1.1.0", "1.2.0", "2.0.0"],
            "^1.0.0",
        )
        assert result == "1.2.0"

    def test_returns_none_when_none_match(self) -> None:
        from general_ludd.util.semver_v2 import max_satisfying

        result = max_satisfying(
            ["1.0.0", "1.1.0"],
            "^2.0.0",
        )
        assert result is None

    def test_skips_invalid(self) -> None:
        from general_ludd.util.semver_v2 import max_satisfying

        result = max_satisfying(
            ["invalid", "1.0.0", "1.1.0"],
            "^1.0.0",
        )
        assert result == "1.1.0"


class TestSortVersions:
    def test_sort_ascending(self) -> None:
        from general_ludd.util.semver_v2 import sort_versions

        result = sort_versions(["2.0.0", "1.0.0", "1.1.0", "0.9.9"])
        assert result == ["0.9.9", "1.0.0", "1.1.0", "2.0.0"]


class TestHashAndSet:
    def test_same_version_same_hash(self) -> None:
        from general_ludd.util.semver_v2 import parse

        a = parse("1.0.0")
        b = parse("1.0.0")
        assert hash(a) == hash(b)
        assert a == b

    def test_different_build_same_hash(self) -> None:
        from general_ludd.util.semver_v2 import parse

        a = parse("1.0.0+build.1")
        b = parse("1.0.0+build.2")
        assert hash(a) == hash(b)

    def test_set_dedup(self) -> None:
        from general_ludd.util.semver_v2 import parse

        versions = [parse("1.0.0"), parse("1.0.0"), parse("v1.0.0")]
        unique = set(versions)
        assert len(unique) == 1


class TestTotalOrdering:
    def test_le_ge(self) -> None:
        from general_ludd.util.semver_v2 import parse

        v = parse("1.0.0")
        assert v <= parse("1.0.0")
        assert v <= parse("1.0.1")
        assert v >= parse("1.0.0")
        assert v >= parse("0.9.9")
        assert not (v <= parse("0.9.9"))
        assert not (v >= parse("1.0.1"))

    def test_ne(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.0.0") != parse("1.0.1")
        assert parse("1.0.0") == parse("v1.0.0")


class TestIsPrereleaseStable:
    def test_is_prerelease(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.0.0-alpha").is_prerelease
        assert not parse("1.0.0").is_prerelease

    def test_is_stable(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.0.0").is_stable
        assert not parse("0.1.0").is_stable
        assert not parse("1.0.0-alpha").is_stable


class TestWithMethods:
    def test_with_prerelease(self) -> None:
        from general_ludd.util.semver_v2 import parse

        v = parse("1.2.3")
        pre = v.with_prerelease("alpha.1")
        assert pre.prerelease == ("alpha", 1)
        assert str(pre) == "1.2.3-alpha.1"

    def test_with_build(self) -> None:
        from general_ludd.util.semver_v2 import parse

        v = parse("1.2.3")
        b = v.with_build("exp.sha.5114f85")
        assert b.build == ("exp", "sha", "5114f85")
        assert str(b) == "1.2.3+exp.sha.5114f85"


class TestCornerCases:
    def test_large_version_numbers(self) -> None:
        from general_ludd.util.semver_v2 import parse

        v = parse("999999.999999.999999")
        assert v.major == 999999
        assert v.minor == 999999
        assert v.patch == 999999

    def test_prerelease_with_hyphens(self) -> None:
        from general_ludd.util.semver_v2 import parse

        v = parse("1.0.0-x-y-z.1")
        assert v.prerelease == ("x-y-z", 1)

    def test_long_prerelease_chain(self) -> None:
        from general_ludd.util.semver_v2 import parse

        v = parse("1.0.0-alpha.1.beta.2.gamma")
        assert len(v.prerelease) == 5
        assert v.prerelease == ("alpha", 1, "beta", 2, "gamma")

    def test_empty_spec_in_satisfies(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.0.0").satisfies("")
        assert parse("9.9.9").satisfies("   ")

    def test_or_operator(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.0.0").satisfies("1.0.0 || 2.0.0")
        assert parse("2.0.0").satisfies("1.0.0 || 2.0.0")
        assert not parse("3.0.0").satisfies("1.0.0 || 2.0.0")

    def test_and_operator(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.5.0").satisfies(">=1.0.0 <2.0.0")
        assert not parse("2.0.0").satisfies(">=1.0.0 <2.0.0")
        assert not parse("0.9.0").satisfies(">=1.0.0 <2.0.0")

    def test_not_equal_to_type(self) -> None:
        from general_ludd.util.semver_v2 import parse

        assert parse("1.0.0").__eq__("not-a-semver") is NotImplemented
