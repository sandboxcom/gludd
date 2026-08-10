"""Deep edge-case tests for EvidenceChecker — URL/path validation, token
normalization, sentence splitting, claim detection, and integration boundaries."""

from __future__ import annotations

from general_ludd.review.evidence_checker import (
    EvidenceChecker,
    EvidenceResult,
    _deduplicate,
    _extract_sources,
    _is_exempt,
    _is_factual_claim,
    _is_valid_source,
    _matching_tool_sources,
    _meaningful_tokens,
    _normalize_token,
    _source_tokens,
    _split_sentences,
    _valid_sources,
)

# ── _is_valid_source ─────────────────────────────────────────────────────────


class TestIsValidSourceUrl:
    def test_https_basic(self):
        assert _is_valid_source("https://example.com/docs.html")

    def test_http_no_tls(self):
        assert _is_valid_source("http://opencode.ai/plugin") is True

    def test_url_with_username_rejected(self):
        assert _is_valid_source("https://user@example.com/file.py") is False

    def test_url_with_password_rejected(self):
        assert _is_valid_source("https://:pass@example.com/file.py") is False

    def test_url_with_both_creds_rejected(self):
        assert _is_valid_source("https://user:pass@example.com/file") is False

    def test_url_with_path_traversal_dot_rejected(self):
        assert _is_valid_source("https://example.com/./etc/passwd") is False

    def test_url_with_path_traversal_dotdot_rejected(self):
        assert _is_valid_source("https://example.com/a/../secret") is False

    def test_url_path_traversal_hidden_in_middle(self):
        assert _is_valid_source("https://example.com/docs/../src/file.py") is False

    def test_url_no_hostname_rejected(self):
        assert _is_valid_source("https:///path/to/file.py") is False

    def test_url_empty_hostname_rejected(self):
        assert _is_valid_source("https://") is False

    def test_url_with_port_number(self):
        assert _is_valid_source("https://example.com:8080/page.html") is True

    def test_url_with_query_string(self):
        assert _is_valid_source("https://example.com/search?q=test") is True

    def test_url_with_fragment(self):
        assert _is_valid_source("https://example.com/page#section-2") is True

    def test_url_with_query_and_fragment(self):
        assert _is_valid_source("https://example.com/doc?lang=en#intro") is True

    def test_ftp_url_rejected(self):
        assert _is_valid_source("ftp://files.example.com/archive.tar") is False

    def test_file_url_rejected(self):
        assert _is_valid_source("file:///etc/hosts") is False

    def test_url_trailing_punctuation_stripped(self):
        assert _is_valid_source("https://example.com/doc.html.") is True

    def test_url_no_path_components(self):
        assert _is_valid_source("https://example.com") is True

    def test_url_malformed_no_scheme(self):
        assert _is_valid_source("example.com/file.py") is True

    def test_url_path_single_slash(self):
        assert _is_valid_source("https://example.com/") is True


class TestIsValidSourceLocalPath:
    def test_relative_path_with_extension(self):
        assert _is_valid_source("src/main.py") is True

    def test_relative_path_with_line_number(self):
        assert _is_valid_source("src/main.py:42") is True

    def test_absolute_path_rejected(self):
        assert _is_valid_source("/etc/hosts") is False

    def test_tilde_path_rejected(self):
        assert _is_valid_source("~/config.yaml") is False

    def test_backslash_path_rejected(self):
        assert _is_valid_source("src\\main.py") is False

    def test_no_extension_rejected(self):
        assert _is_valid_source("README") is False

    def test_empty_segment_rejected(self):
        assert _is_valid_source("src//main.py") is False

    def test_dot_segment_rejected(self):
        assert _is_valid_source("./main.py") is False

    def test_dotdot_segment_rejected(self):
        assert _is_valid_source("../main.py") is False

    def test_multiple_dot_parts_rejected(self):
        assert _is_valid_source("src/./sub/../main.py") is False

    def test_path_with_spaces(self):
        assert _is_valid_source("my docs/readme.txt") is False

    def test_path_with_underscores(self):
        assert _is_valid_source("my_module/__init__.py") is True

    def test_path_with_hyphens(self):
        assert _is_valid_source("src/my-module/config.yml") is True

    def test_path_with_numbers(self):
        assert _is_valid_source("data/v2/schema.json") is True

    def test_colon_in_path_mid_segment(self):
        result = _is_valid_source("src/foo:bar.py:42")
        assert result is False

    def test_mixed_slashes_and_backslashes_rejected(self):
        assert _is_valid_source("src/main.py\\data") is False

    def test_hidden_file_with_dot(self):
        assert _is_valid_source(".github/workflows/ci.yml") is True

    def test_only_dotfile_no_path(self):
        assert _is_valid_source(".gitignore") is False

    def test_empty_string_rejected(self):
        assert _is_valid_source("") is False

    def test_whitespace_only_rejected(self):
        assert _is_valid_source("   ") is False


# ── _normalize_token ────────────────────────────────────────────────────────


class TestNormalizeToken:
    def test_ies_ending_becomes_y(self):
        assert _normalize_token("parties") == "party"

    def test_ies_short_word_unchanged(self):
        assert _normalize_token("pies") == "pie"

    def test_ies_exact_boundary_five_chars(self):
        assert _normalize_token("tries") == "try"

    def test_sses_ending_removes_es(self):
        assert _normalize_token("classes") == "class"

    def test_ches_ending_removes_es(self):
        assert _normalize_token("watches") == "watch"

    def test_shes_ending_removes_es(self):
        assert _normalize_token("crashes") == "crash"

    def test_xes_ending_removes_es(self):
        assert _normalize_token("boxes") == "box"

    def test_zes_ending_removes_es(self):
        assert _normalize_token("buzzes") == "buzz"

    def test_plain_s_removed(self):
        assert _normalize_token("tests") == "test"

    def test_ss_ending_unchanged(self):
        assert _normalize_token("class") == "class"

    def test_short_word_s_removed(self):
        assert _normalize_token("was") == "was"

    def test_no_suffix_unchanged(self):
        assert _normalize_token("python") == "python"

    def test_empty_string(self):
        assert _normalize_token("") == ""


# ── _meaningful_tokens ──────────────────────────────────────────────────────


class TestMeaningfulTokens:
    def test_stopwords_filtered_out(self):
        tokens = _meaningful_tokens("the module has five classes")
        assert "the" not in tokens
        assert "has" not in tokens
        assert "module" in tokens
        assert "classes" not in tokens
        assert "class" in tokens

    def test_case_insensitive_stopwords(self):
        tokens = _meaningful_tokens("THE Module IS HERE")
        assert "module" in tokens
        assert "here" in tokens

    def test_normalization_applied(self):
        tokens = _meaningful_tokens("classes are defined")
        assert "class" in tokens
        assert "defined" in tokens

    def test_empty_string_yields_empty_set(self):
        assert _meaningful_tokens("") == set()

    def test_only_stopwords_yields_empty(self):
        tokens = _meaningful_tokens("the and or by with for")
        assert tokens == set()

    def test_punctuation_excluded(self):
        tokens = _meaningful_tokens("hello, world!")
        assert "hello" in tokens
        assert "world" in tokens

    def test_numbers_preserved(self):
        tokens = _meaningful_tokens("version 3 has 42 entries")
        assert "3" in tokens or "42" in tokens

    def test_underscore_word(self):
        tokens = _meaningful_tokens("my_module_test")
        assert "my_module_test" not in tokens
        assert "my" in tokens
        assert "module" in tokens
        assert "test" in tokens


# ── _source_tokens ──────────────────────────────────────────────────────────


class TestSourceTokens:
    def test_url_hostname_and_path(self):
        tokens = _source_tokens("https://docs.python.org/3/library/re.html")
        assert "doc" in tokens
        assert "python" in tokens
        assert "library" in tokens
        assert "re" in tokens

    def test_local_path_strips_line_number(self):
        tokens = _source_tokens("src/main.py:42")
        assert "main" in tokens
        assert "py" in tokens
        assert "42" not in tokens

    def test_url_no_path(self):
        tokens = _source_tokens("https://example.com")
        assert "example" in tokens
        assert "com" in tokens

    def test_url_trailing_slash(self):
        tokens = _source_tokens("https://example.com/")
        assert "example" in tokens

    def test_trailing_punctuation_stripped(self):
        tokens = _source_tokens("src/main.py.")
        assert "main" in tokens
        assert "py" in tokens


# ── _extract_sources ────────────────────────────────────────────────────────


class TestExtractSources:
    def test_single_url(self):
        sources = _extract_sources("See https://example.com/doc.html for details")
        assert "https://example.com/doc.html" in sources

    def test_single_file_path(self):
        sources = _extract_sources("Defined in src/main.py:42")
        assert "src/main.py:42" in sources

    def test_multiple_sources_in_line(self):
        sources = _extract_sources("Fixed in src/foo.py:1 and docs/bar.md:2. Also see https://example.com")
        assert "src/foo.py:1" in sources
        assert "docs/bar.md:2" in sources
        assert "https://example.com" in sources

    def test_no_sources_in_text(self):
        assert _extract_sources("No sources here at all.") == []

    def test_url_in_parentheses_captured(self):
        sources = _extract_sources("Node docs (https://nodejs.org/api/fs.html)")
        assert "https://nodejs.org/api/fs.html" in sources

    def test_url_in_angle_brackets_captured(self):
        sources = _extract_sources("Reference: <https://example.com>")
        assert "https://example.com" in sources

    def test_duplicate_sources_deduplicated(self):
        sources = _extract_sources("See https://example.com and also https://example.com")
        assert sources.count("https://example.com") <= 1
        assert len(sources) == 1

    def test_empty_string_yields_empty(self):
        assert _extract_sources("") == []


# ── _deduplicate ────────────────────────────────────────────────────────────


class TestDeduplicate:
    def test_order_preserved(self):
        assert _deduplicate(["b", "a", "c"]) == ["b", "a", "c"]

    def test_duplicates_removed_first_kept(self):
        assert _deduplicate(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]

    def test_empty_list(self):
        assert _deduplicate([]) == []

    def test_single_element(self):
        assert _deduplicate(["only"]) == ["only"]

    def test_all_duplicates(self):
        assert _deduplicate(["x", "x", "x"]) == ["x"]


# ── _split_sentences ────────────────────────────────────────────────────────


class TestSplitSentences:
    def test_period_split(self):
        parts = _split_sentences("First. Second.")
        assert parts == ["First.", "Second."]

    def test_question_mark_split(self):
        parts = _split_sentences("What? Really?")
        assert parts == ["What?", "Really?"]

    def test_exclamation_split(self):
        parts = _split_sentences("Stop! Go!")
        assert parts == ["Stop!", "Go!"]

    def test_mixed_punctuation(self):
        parts = _split_sentences("Hello! How are you? I am fine.")
        assert len(parts) == 3

    def test_no_trailing_whitespace_split(self):
        parts = _split_sentences("A.B.C.")
        assert len(parts) == 1

    def test_numbers_with_decimal_not_split(self):
        parts = _split_sentences("Version 3.14 has a bug.")
        assert parts == ["Version 3.14 has a bug."]

    def test_file_colon_not_split(self):
        parts = _split_sentences("In src/main.py:42 we define it.")
        assert len(parts) == 1

    def test_empty_string_yields_empty(self):
        assert _split_sentences("") == []

    def test_whitespace_only_yields_empty(self):
        assert _split_sentences("   ") == []

    def test_ellipsis_not_extra_split(self):
        parts = _split_sentences("Wait... and then it failed.")
        assert len(parts) == 2

    def text_multiple_spaces_between_sentences(self):
        parts = _split_sentences("First.    Second.")
        assert parts == ["First.", "Second."]


# ── _is_exempt ──────────────────────────────────────────────────────────────


class TestIsExempt:
    def test_question_exempt(self):
        assert _is_exempt("What is the value?") is True

    def test_hedged_exempt(self):
        assert _is_exempt("I think it might work") is True

    def test_maybe_exempt(self):
        assert _is_exempt("maybe we should check") is True

    def test_perhaps_exempt(self):
        assert _is_exempt("perhaps it is broken") is True

    def test_imo_exempt(self):
        assert _is_exempt("in my opinion this is fine") is True

    def test_ok_exempt(self):
        assert _is_exempt("OK.") is True

    def test_yes_exempt(self):
        assert _is_exempt("yes, that works") is True

    def test_no_exempt(self):
        assert _is_exempt("no, not yet") is True

    def test_factual_claim_not_exempt(self):
        assert _is_exempt("The function returns 42") is False

    def test_path_not_exempt_just_because_question_in_it(self):
        assert _is_exempt("I fixed the bug in src/main.py") is False


# ── _is_factual_claim ───────────────────────────────────────────────────────


class TestIsFactualClaim:
    def test_is_pattern(self):
        assert _is_factual_claim("The value is 42") is True

    def test_are_pattern(self):
        assert _is_factual_claim("The tests are green") is True

    def test_has_pattern(self):
        assert _is_factual_claim("The module has 5 classes") is True

    def test_uses_pattern(self):
        assert _is_factual_claim("The project uses FastAPI") is True

    def test_contains_pattern(self):
        assert _is_factual_claim("The file contains secrets") is True

    def test_returns_pattern(self):
        assert _is_factual_claim("The function returns None") is True

    def test_percentage_pattern(self):
        assert _is_factual_claim("Coverage is at 85%") is True

    def test_total_count_pattern(self):
        assert _is_factual_claim("The total is 42") is True

    def test_count_is_pattern(self):
        assert _is_factual_claim("The count is 10") is True

    def test_number_of_pattern(self):
        assert _is_factual_claim("The number of tests is 200") is True

    def test_number_equals_pattern(self):
        assert _is_factual_claim("The count equals 5") is True

    def test_fixed_in_pattern(self):
        assert _is_factual_claim("Fixed in src/main.py:42") is True

    def test_implemented_at_pattern(self):
        assert _is_factual_claim("Implemented at config/settings.py:10") is True

    def test_changed_in_pattern(self):
        assert _is_factual_claim("Changed in the latest commit") is True

    def test_documented_at_pattern(self):
        assert _is_factual_claim("Documented at docs/guide.md") is True

    def test_simple_greeting_not_claim(self):
        assert _is_factual_claim("Hello world") is False

    def test_empty_not_claim(self):
        assert _is_factual_claim("") is False

    def test_numeric_only_not_claim(self):
        assert _is_factual_claim("42") is False


# ── _valid_sources ──────────────────────────────────────────────────────────


class TestValidSources:
    def test_all_valid(self):
        sources = _valid_sources(["src/a.py:1", "docs/b.md", "https://x.com/c"])
        assert len(sources) == 3

    def test_mixed_valid_invalid(self):
        sources = _valid_sources(["src/a.py:1", "bad", "/abs/path"])
        assert sources == ["src/a.py:1"]

    def test_all_invalid(self):
        assert _valid_sources(["bad", "../up", ""]) == []

    def test_empty_list(self):
        assert _valid_sources([]) == []

    def test_deduplication(self):
        sources = _valid_sources(["src/a.py:1", "src/a.py:1"])
        assert len(sources) == 1


# ── _matching_tool_sources ──────────────────────────────────────────────────


class TestMatchingToolSources:
    def test_matches_token_overlap(self):
        sources = _matching_tool_sources(
            "deploy is green",
            ["File deploy/config.yaml:7 modified"],
        )
        assert "deploy/config.yaml:7" in sources

    def test_no_match_on_unrelated(self):
        sources = _matching_tool_sources(
            "database is up",
            ["File frontend/style.css:3 modified"],
        )
        assert sources == []

    def test_empty_tool_outputs(self):
        assert _matching_tool_sources("tests pass", []) == []

    def test_tool_output_without_sources(self):
        sources = _matching_tool_sources(
            "tests pass",
            ["All tests passed successfully"],
        )
        assert sources == []

    def test_empty_claim(self):
        sources = _matching_tool_sources("", ["src/a.py:1"])
        assert sources == []

    def test_claim_only_stopwords(self):
        sources = _matching_tool_sources("the and or by", ["src/a.py:1"])
        assert sources == []

    def test_multiple_tool_outputs_match(self):
        sources = _matching_tool_sources(
            "config has 3 entries",
            [
                "config/settings.yaml updated",
                "config/defaults.yaml modified",
            ],
        )
        assert "config/settings.yaml" in sources
        assert "config/defaults.yaml" in sources

    def test_same_source_in_multiple_outputs_deduped(self):
        sources = _matching_tool_sources(
            "config changed",
            [
                "config/settings.yaml:1 modified",
                "config/settings.yaml:1 also here",
            ],
        )
        assert sources.count("config/settings.yaml:1") <= 1

    def test_multiline_tool_output(self):
        sources = _matching_tool_sources(
            "auth module updated",
            ["src/auth.py:1\nsrc/auth.py:2"],
        )
        assert "src/auth.py:1" in sources
        assert "src/auth.py:2" in sources

    def test_url_source_in_tool_output(self):
        sources = _matching_tool_sources(
            "opencode docs reference",
            ["See https://opencode.ai/docs for details"],
        )
        assert "https://opencode.ai/docs" in sources

    def test_no_overlap_with_normalized_forms(self):
        sources = _matching_tool_sources(
            "classes defined",
            ["src/class.py:1"],
        )
        assert "src/class.py:1" in sources


# ── EvidenceChecker.check_claim ─────────────────────────────────────────────


class TestCheckClaimDeep:
    def test_empty_sources_returns_unsupported(self):
        checker = EvidenceChecker()
        result = checker.check_claim("assertion", [])
        assert result.supported is False
        assert result.missing_sources == ["no source provided"]

    def test_all_invalid_sources_unsupported(self):
        checker = EvidenceChecker()
        result = checker.check_claim("assertion", ["bad", "../up", ""])
        assert result.supported is False
        assert result.missing_sources == ["no valid source provided"]

    def test_mixed_valid_invalid_sources_supported(self):
        checker = EvidenceChecker()
        result = checker.check_claim("assertion", ["bad", "src/a.py:1"])
        assert result.supported is True
        assert result.sources == ["src/a.py:1"]

    def test_multiple_valid_sources_deduped(self):
        checker = EvidenceChecker()
        result = checker.check_claim("assertion", ["src/a.py:1", "src/a.py:1"])
        assert result.supported is True
        assert len(result.sources) == 1

    def test_url_source_counts(self):
        checker = EvidenceChecker()
        result = checker.check_claim("assertion", ["https://example.com/doc"])
        assert result.supported is True

    def test_evidence_result_dataclass_default(self):
        result = EvidenceResult(supported=False, claim="x", sources=[])
        assert result.missing_sources == []


# ── EvidenceChecker.audit_response ──────────────────────────────────────────


class TestAuditResponseDeep:
    def test_empty_response(self):
        checker = EvidenceChecker()
        results = checker.audit_response("", [])
        assert results == []

    def test_whitespace_only_response(self):
        checker = EvidenceChecker()
        results = checker.audit_response("   \n  ", [])
        assert results == []

    def test_only_exempt_sentences(self):
        checker = EvidenceChecker()
        results = checker.audit_response("OK. What? I think so.", [])
        assert results == []

    def test_only_non_factual_sentences(self):
        checker = EvidenceChecker()
        results = checker.audit_response("Hello. Goodbye.", [])
        assert results == []

    def test_mixed_exempt_and_claims(self):
        checker = EvidenceChecker()
        results = checker.audit_response(
            "OK. The value is 42. I think it works.",
            tool_outputs=[],
        )
        assert len(results) == 1
        assert not results[0].supported

    def test_tool_output_supports_claim_via_token_overlap(self):
        checker = EvidenceChecker()
        results = checker.audit_response(
            "The deploy is green.",
            tool_outputs=["deploy/config.yaml:7 modified"],
        )
        assert len(results) == 1
        assert results[0].supported is True
        assert "deploy/config.yaml:7" in results[0].sources

    def test_tool_output_irrelevant_to_claim(self):
        checker = EvidenceChecker()
        results = checker.audit_response(
            "The database is online.",
            tool_outputs=["frontend/style.css:3 modified"],
        )
        assert len(results) == 1
        assert results[0].supported is False

    def test_inline_source_combined_with_tool_source(self):
        checker = EvidenceChecker()
        results = checker.audit_response(
            "Fixed in src/foo.py:42. The tests are passing.",
            tool_outputs=["File src/foo.py:42 modified"],
        )
        assert len(results) == 2
        assert results[0].supported is True
        tests_claim = [r for r in results if "tests" in r.claim.lower()]
        assert len(tests_claim) == 1

    def test_long_response_with_many_sentences(self):
        checker = EvidenceChecker()
        sentences = ". ".join(f"Module {i} has {i} classes" for i in range(20)) + "."
        results = checker.audit_response(sentences, tool_outputs=[])
        assert len(results) == 20
        assert all(not r.supported for r in results)

    def test_sentence_with_multiple_percentages(self):
        checker = EvidenceChecker()
        results = checker.audit_response(
            "Coverage is 85% and 92% on the modules.",
            tool_outputs=[],
        )
        assert len(results) == 1
        assert not results[0].supported

    def test_url_as_only_source_makes_claim_supported(self):
        checker = EvidenceChecker()
        results = checker.audit_response(
            "The API supports plugins per https://opencode.ai/docs.",
            tool_outputs=[],
        )
        assert len(results) == 1
        assert results[0].supported is True
        assert any("opencode.ai/docs" in s for s in results[0].sources)

    def test_path_tokens_normalized_for_matching(self):
        checker = EvidenceChecker()
        results = checker.audit_response(
            "The classes are defined.",
            tool_outputs=["src/class.py:1"],
        )
        assert len(results) == 1
        assert results[0].supported is True

    def test_tool_output_pathline_colon_format(self):
        checker = EvidenceChecker()
        results = checker.audit_response(
            "The test suite is passing.",
            tool_outputs=["test_output.txt:42: test suite completed"],
        )
        assert len(results) == 1
        assert results[0].supported is True

    def test_tool_source_matching_stopword_normalized(self):
        checker = EvidenceChecker()
        results = checker.audit_response(
            "the config has yaml settings.",
            tool_outputs=["config/settings.yaml:1"],
        )
        assert len(results) == 1
        assert results[0].supported is True
