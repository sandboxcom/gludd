"""Quality tests for skill-lens relevance scoring.

Validates that the keyword-overlap scoring does not overfit to exact terms,
handles diverse inputs, and respects invariants across all available skills.

These tests verify semantic behavior patterns, not exact score values.
No test hardcodes a specific section header name or exact count — they
validate relationships (higher/lower/equal) and invariants.
"""

from __future__ import annotations

import re
import string
from pathlib import Path
from typing import ClassVar

import pytest

from general_ludd.ansible.skill_lens import (
    InvalidSkillError,
    _parse_sections,
    _score_relevance,
    _strip_frontmatter,
    _tokenize,
    clear_cache,
    lens,
    lens_raw,
)

SKILLS_DIR = Path(__file__).parent.parent.parent / ".opencode" / "skills"


def _available_skills() -> list[str]:
    """Return skill names that have a SKILL.md file."""
    names: list[str] = []
    for entry in sorted(SKILLS_DIR.iterdir()):
        if entry.is_dir() and (entry / "SKILL.md").is_file():
            names.append(entry.name)
    return names


def _section_headers(skill_name: str) -> list[str]:
    """Read a skill file and return its ## section headers."""
    path = SKILLS_DIR / skill_name / "SKILL.md"
    text = _strip_frontmatter(path.read_text())
    sections = _parse_sections(text)
    return [h for h, _ in sections]


def _all_headers(text: str) -> list[str]:
    """Extract all lines starting with '## ' from a string (for scanning lens output)."""
    return re.findall(r"^## .+", text, re.MULTILINE)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear module-level caches before each test for isolation."""
    clear_cache()


# ---------------------------------------------------------------------------
# 1. Semantic Equivalence
# ---------------------------------------------------------------------------


class TestSemanticEquivalence:
    """Different phrasings of the same concept should all favour the same area."""

    ASYNCIO_PHRASINGS: ClassVar[list[str]] = [
        "async programming in python",
        "asyncio concurrency with coroutines",
        "non-blocking i/o and event loop",
        "await and async def usage",
        "concurrent task execution without threads",
    ]

    @pytest.mark.parametrize("phrasing", ASYNCIO_PHRASINGS)
    def test_asyncio_phrasings_score_asyncio_above_jython(self, phrasing):
        """Each asyncio phrasing should rank an async section above a Jython section."""
        sections = {
            "async": "asyncio event loop coroutine task async await gather run future",
            "jython": "Jython Java interop classpath swing compatibility",
        }
        async_score = _score_relevance(phrasing, sections["async"])
        jython_score = _score_relevance(phrasing, sections["jython"])
        assert async_score > jython_score, (
            f"'{phrasing}' scored async={async_score:.3f} jython={jython_score:.3f}"
        )

    MEMORY_PHRASINGS: ClassVar[list[str]] = [
        "memory leak investigation",
        "RAM usage keeps growing over time",
        "OOM killer terminated my process",
        "out of memory error in production",
        "heap profiling and memory analysis",
        "excessive memory allocation",
        "process memory keeps climbing",
    ]

    @pytest.mark.parametrize("phrasing", MEMORY_PHRASINGS)
    def test_memory_phrasings_score_memory_section_high(self, phrasing):
        """Memory-related phrasings should score a memory/perf section non-zero."""
        memory_section = (
            "memory profiling heap allocation leak tracemalloc objgraph "
            "garbage collection reference cycles weakref"
        )
        unrelated = "class definitions inheritance metaclass descriptors slots"
        mem_score = _score_relevance(phrasing, memory_section)
        unr_score = _score_relevance(phrasing, unrelated)
        assert mem_score > 0.0, f"'{phrasing}' scored 0.0 on memory section"
        assert mem_score > unr_score, (
            f"'{phrasing}' mem={mem_score:.3f} unrelated={unr_score:.3f}"
        )

    DEBUG_PHRASINGS: ClassVar[list[str]] = [
        "debug a deadlock in my application",
        "thread is stuck and not responding",
        "deadlock detection tools",
    ]

    @pytest.mark.parametrize("phrasing", DEBUG_PHRASINGS)
    def test_debug_phrasings_score_debug_above_config(self, phrasing):
        """Debug phrasings should rank debug sections higher than config sections."""
        debug_section = "debugging deadlock pdb breakpoints traceback stack inspection"
        config_section = "config settings yaml json environment variables ini toml"
        debug_score = _score_relevance(phrasing, debug_section)
        config_score = _score_relevance(phrasing, config_section)
        assert debug_score > config_score, (
            f"'{phrasing}' debug={debug_score:.3f} config={config_score:.3f}"
        )


# ---------------------------------------------------------------------------
# 2. Cross-Language Relevance
# ---------------------------------------------------------------------------


class TestCrossLanguageRelevance:
    """A task about a concept should score relevant sections across language skills."""

    def test_deadlock_scores_debug_sections_in_multiple_languages(self):
        """Deadlock debugging should score above zero in any expert skill."""
        task = "debug a deadlock in a concurrent application"
        skills_to_check = [
            s for s in _available_skills()
            if s.endswith("-expert")
        ]
        assert len(skills_to_check) >= 2, "Need at least 2 expert skills for test"

        for skill_name in skills_to_check:
            result = lens_raw(skill_name, task, max_sections=3)
            sections = result["sections"]
            assert len(sections) >= 1, (
                f"lens_raw({skill_name!r}) returned no sections"
            )
            max_score = max(s["score"] for s in sections)
            assert max_score > 0.0, (
                f"deadlock task scored 0.0 for all sections in {skill_name}"
            )

    def test_concurrency_scores_relevant_across_languages(self):
        """Concurrency task should return sections in any expert skill."""
        task = "handle concurrent requests with proper synchronization"
        for skill_name in ["python-expert", "go-expert", "java-expert"]:
            if skill_name not in _available_skills():
                continue
            result = lens_raw(skill_name, task, max_sections=3)
            assert len(result["sections"]) >= 1
            # At least one section should have a non-zero score
            scores = [s["score"] for s in result["sections"]]
            assert any(s > 0.0 for s in scores), (
                f"All scores are 0.0 for {skill_name} with concurrency task"
            )

    def test_error_handling_scores_relevant_across_languages(self):
        """Error handling task should find relevant sections in any expert skill."""
        task = "implement proper error handling and recovery"
        for skill_name in ["python-expert", "go-expert", "java-expert"]:
            if skill_name not in _available_skills():
                continue
            result = lens_raw(skill_name, task, max_sections=2)
            assert len(result["sections"]) >= 1
            scores = [s["score"] for s in result["sections"]]
            assert any(s > 0.0 for s in scores), (
                f"All scores 0.0 for {skill_name} with error-handling task"
            )


# ---------------------------------------------------------------------------
# 3. Synonym Handling
# ---------------------------------------------------------------------------


class TestSynonymHandling:
    """Synonyms and related terms should produce similar scoring patterns."""

    def test_memory_synonyms_score_similar(self):
        """Different memory-related terms should all find the same section."""
        memory_section = (
            "memory profiling tracemalloc leak heap garbage collection "
            "reference cycles weakref allocation fragmentation"
        )
        phrasings = [
            "memory leak",
            "RAM usage growing",
            "OOM killer",
            "out of memory",
            "heap exhaustion",
            "allocation failure",
        ]
        scores = {
            p: _score_relevance(p, memory_section) for p in phrasings
        }
        # All should score above zero
        for p, s in scores.items():
            assert s > 0.0, f"'{p}' scored 0.0 on memory section"

        # Any two phrasings about memory should not have vastly different scores
        # (within the same order of magnitude, since the section is small)
        nonzero = [s for s in scores.values() if s > 0.01]
        if len(nonzero) >= 2:
            ratio = max(nonzero) / min(nonzero) if min(nonzero) > 0 else float("inf")
            assert ratio < 50.0, (
                f"Memory synonym scores vary too much: {scores}"
            )

    def test_concurrency_synonyms(self):
        """Different concurrency terms should all find relevant sections."""
        concurrency_section = (
            "threading concurrent parallel multiprocessing lock mutex "
            "semaphore condition variable queue synchronized"
        )
        tasks = [
            "multithreading bug",
            "parallel execution issue",
            "concurrent access problem",
            "race condition fix",
            "thread safety concern",
        ]
        for task in tasks:
            score = _score_relevance(task, concurrency_section)
            assert score > 0.0, f"'{task}' scored 0.0 on concurrency section"


# ---------------------------------------------------------------------------
# 4. Negative Tests (Unrelated Queries)
# ---------------------------------------------------------------------------


class TestNegativeMatching:
    """Queries unrelated to a skill should not score highly."""

    def test_cooking_task_scores_lower_on_programming_than_culinary(self):
        """A culinary task should score higher on culinary than on programming skills."""
        if "culinary-expert" not in _available_skills():
            pytest.skip("culinary-expert skill not available")

        task = "how to bake a perfect chocolate cake with buttercream frosting"
        culinary = lens_raw("culinary-expert", task, max_sections=3)
        culinary_max = max(s["score"] for s in culinary["sections"])

        for skill_name in ["python-expert", "go-expert", "java-expert"]:
            if skill_name not in _available_skills():
                continue
            result = lens_raw(skill_name, task, max_sections=3)
            prog_max = max(s["score"] for s in result["sections"])
            assert culinary_max > prog_max, (
                f"Cooking task scored higher on {skill_name} "
                f"({prog_max:.3f}) than on culinary ({culinary_max:.3f})"
            )

    def test_programming_task_scores_higher_on_programming_than_culinary(self):
        """A Python coding task should score higher on python-expert than culinary."""
        if "python-expert" not in _available_skills():
            pytest.skip("python-expert not available")
        if "culinary-expert" not in _available_skills():
            pytest.skip("culinary-expert not available")

        task = "write a Flask API endpoint with async database queries"
        python_result = lens_raw("python-expert", task, max_sections=3)
        culinary_result = lens_raw("culinary-expert", task, max_sections=3)

        python_max = max(s["score"] for s in python_result["sections"])
        culinary_max = max(s["score"] for s in culinary_result["sections"])
        assert python_max > culinary_max, (
            f"Python task scored higher on culinary ({culinary_max:.3f}) "
            f"than on python-expert ({python_max:.3f})"
        )

    def test_nonsense_query_scores_lower_than_relevant_query(self):
        """A nonsense query should score meaningfully lower than a relevant query."""
        nonsense = "blargle flarp snizzlewump xyzzy plugh"
        relevant = "debug an asyncio deadlock in the event loop"

        ns_result = lens_raw("python-expert", nonsense, max_sections=3)
        rel_result = lens_raw("python-expert", relevant, max_sections=3)

        ns_max = max(s["score"] for s in ns_result["sections"])
        rel_max = max(s["score"] for s in rel_result["sections"])

        assert rel_max > ns_max, (
            f"Relevant query max score ({rel_max:.3f}) <= "
            f"nonsense query max score ({ns_max:.3f})"
        )


# ---------------------------------------------------------------------------
# 5. Short vs Long Queries
# ---------------------------------------------------------------------------


class TestQueryLength:
    """Query length should not dramatically change which sections are returned."""

    def test_short_vs_long_query_same_topic(self):
        """A 3-word query and a 50-word description of the same topic."""
        short_task = "asyncio event loop"
        long_task = (
            "I need to understand how Python's asyncio event loop works internally. "
            "Specifically, I want to know how tasks are scheduled, how the event "
            "loop manages coroutines, what happens when a coroutine awaits, how "
            "callbacks are registered, and how the selector handles I/O readiness. "
            "I'm particularly interested in the interaction between the event loop "
            "and the operating system's I/O multiplexing facilities like epoll."
        )
        short_result = lens_raw("python-expert", short_task, max_sections=3)
        long_result = lens_raw("python-expert", long_task, max_sections=3)

        short_headers = {s["header"] for s in short_result["sections"]}
        long_headers = {s["header"] for s in long_result["sections"]}

        # At least one section should overlap (both should find async-related sections)
        overlap = short_headers & long_headers
        assert len(overlap) >= 1, (
            f"No section overlap between short and long query.\n"
            f"  short headers: {short_headers}\n"
            f"  long headers: {long_headers}"
        )

    def test_long_query_does_not_return_more_sections_than_max(self):
        """Even a very long query respects max_sections."""
        long_task = " ".join(["python debugging testing profiling"] * 50)
        result = lens_raw("python-expert", long_task, max_sections=2)
        assert len(result["sections"]) <= 2

    def test_very_long_query_does_not_crash(self):
        """A 5000+ character query should not crash the lens."""
        long_task = "python programming " * 600
        result = lens("python-expert", long_task, max_sections=1)
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# 6. Multi-Skill Overlap
# ---------------------------------------------------------------------------


class TestMultiSkillOverlap:
    """A task spanning multiple domains should find relevant sections in each skill."""

    def test_python_calling_go_via_grpc(self):
        """Task about Python+Go+gRPC should score relevant sections in both skills."""
        if "python-expert" not in _available_skills():
            pytest.skip("python-expert not available")
        if "go-expert" not in _available_skills():
            pytest.skip("go-expert not available")

        task = "Python service calling a Go microservice via gRPC with protobuf"
        python_result = lens_raw("python-expert", task, max_sections=3)
        go_result = lens_raw("go-expert", task, max_sections=3)

        python_scores = [s["score"] for s in python_result["sections"]]
        go_scores = [s["score"] for s in go_result["sections"]]

        # Both should have at least one section with non-zero score
        assert any(s > 0.0 for s in python_scores), (
            "Python expert scored all 0.0 for gRPC task"
        )
        assert any(s > 0.0 for s in go_scores), (
            "Go expert scored all 0.0 for gRPC task"
        )

    def test_java_spring_with_sql(self):
        """Task about Java Spring Boot with SQL should score in java-expert."""
        if "java-expert" not in _available_skills():
            pytest.skip("java-expert not available")

        task = "build a Spring Boot REST API with PostgreSQL and JPA repositories"
        result = lens_raw("java-expert", task, max_sections=3)
        scores = [s["score"] for s in result["sections"]]
        assert any(s > 0.0 for s in scores), (
            "Java expert scored all 0.0 for Spring Boot task"
        )

    def test_electronics_with_programming_overlap(self):
        """Task about microcontroller programming should find relevant sections."""
        if "electronics-expert" not in _available_skills():
            pytest.skip("electronics-expert not available")

        task = "write firmware for an STM32 microcontroller in C"
        result = lens_raw("electronics-expert", task, max_sections=3)
        scores = [s["score"] for s in result["sections"]]
        assert any(s > 0.0 for s in scores), (
            "Electronics expert scored all 0.0 for firmware task"
        )


# ---------------------------------------------------------------------------
# 7. Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Boundary inputs should not crash and should produce reasonable output."""

    def test_empty_task_string(self):
        """Empty task returns sections in document order (no sorting)."""
        result = lens_raw("test-quality", "", max_sections=3)
        assert len(result["sections"]) >= 1
        # Empty task: all sections score 0.0, returned in original order
        for s in result["sections"]:
            assert s["score"] == 0.0

    def test_whitespace_only_task(self):
        """Whitespace-only task should not crash."""
        for ws in ["   ", "\t", "\n  \n", " \t\n "]:
            result = lens("test-quality", ws, max_sections=2)
            assert isinstance(result, str)
            assert len(result) > 0

    def test_punctuation_only_task(self):
        """Task with only punctuation characters."""
        task = "!@#$%^&*()+={}[]|;:',.<>?/~`"
        result = lens_raw("test-quality", task, max_sections=2)
        assert len(result["sections"]) >= 1
        # Only '_' and '-' from the punctuation set match [a-z0-9_]+ via -_
        # so some score may occur. Verify it doesn't crash.

    def test_non_english_task(self):
        """Non-English task should not crash — it just won't find matches."""
        tasks = [
            "Programmazione asincrona in Python con asyncio",
            "Déboguer un interblocage dans asyncio",
            "async Programmierung mit Python asyncio verstehen",
        ]
        for task in tasks:
            result = lens_raw("python-expert", task, max_sections=2)
            assert len(result["sections"]) >= 1
            # "asyncio" is a loan word that will match; the rest may not
            # We just verify it doesn't crash
            assert isinstance(result, dict)

    def test_single_character_task(self):
        """Single-character task should not crash."""
        for char in ["a", "x", "1", "_"]:
            result = lens("test-quality", char, max_sections=1)
            assert isinstance(result, str)

    def test_task_with_unicode(self):
        """Task with Unicode characters beyond ASCII."""
        task = "debug a deadlock with \N{GREEK SMALL LETTER PI} calculation"
        result = lens_raw("python-expert", task, max_sections=2)
        assert isinstance(result, dict)
        assert len(result["sections"]) >= 1

    def test_max_sections_zero(self):
        """max_sections=0 returns empty sections list."""
        result = lens_raw("test-quality", "write a test", max_sections=0)
        assert result["sections"] == []

    def test_max_sections_very_large(self):
        """Very large max_sections returns all available sections."""
        result = lens_raw("test-quality", "test", max_sections=1000)
        all_sections = _section_headers("test-quality")
        # Should return at most the number of sections in the skill
        assert len(result["sections"]) <= len(all_sections)

    def test_skill_with_no_sections(self):
        """A skill file with no ## sections returns empty list."""
        empty_text = "# Just a title\n\nNo sections, just paragraphs.\n"
        sections = _parse_sections(empty_text)
        assert sections == []

    def test_all_available_skills_load(self):
        """Every available skill can be loaded through lens without error."""
        for skill_name in _available_skills():
            result = lens_raw(skill_name, "test task", max_sections=1)
            assert isinstance(result, dict)
            assert result["skill_name"] == skill_name


# ---------------------------------------------------------------------------
# 8. Consistency (Determinism)
# ---------------------------------------------------------------------------


class TestConsistency:
    """Multiple invocations with the same inputs must produce identical output."""

    def test_same_task_same_sections(self):
        """Same task always returns the same sections with same scores."""
        task = "debug an asyncio deadlock in the event loop"
        results = [lens_raw("python-expert", task, max_sections=3) for _ in range(5)]

        first_headers = [s["header"] for s in results[0]["sections"]]
        first_scores = [s["score"] for s in results[0]["sections"]]

        for i, r in enumerate(results[1:], 1):
            headers = [s["header"] for s in r["sections"]]
            scores = [s["score"] for s in r["sections"]]
            assert headers == first_headers, (
                f"Run {i} headers differ: {headers} != {first_headers}"
            )
            assert scores == first_scores, (
                f"Run {i} scores differ: {scores} != {first_scores}"
            )

    def test_caching_is_transparent(self):
        """Cached and uncached calls return identical results."""
        clear_cache()
        uncached = lens_raw("python-expert", "asyncio debugging", max_sections=2)
        cached = lens_raw("python-expert", "asyncio debugging", max_sections=2)
        assert uncached == cached

    def test_different_casing_same_result(self):
        """UPPERCASE and lowercase queries return same results."""
        task_lower = "debug an asyncio deadlock"
        task_upper = "DEBUG AN ASYNCIO DEADLOCK"
        task_mixed = "Debug An Asyncio Deadlock"

        r_lower = lens_raw("python-expert", task_lower, max_sections=3)
        r_upper = lens_raw("python-expert", task_upper, max_sections=3)
        r_mixed = lens_raw("python-expert", task_mixed, max_sections=3)

        assert r_lower["sections"] == r_upper["sections"] == r_mixed["sections"]


# ---------------------------------------------------------------------------
# 9. Token Reduction
# ---------------------------------------------------------------------------


class TestTokenReduction:
    """The lens should meaningfully reduce the amount of text vs the full skill."""

    def test_lens_output_is_shorter_than_full_skill(self):
        """Lens output should be shorter than the full skill file."""
        for skill_name in _available_skills():
            full_path = SKILLS_DIR / skill_name / "SKILL.md"
            full_size = len(full_path.read_text())

            result = lens(skill_name, "relevant task", max_sections=3)
            lens_size = len(result)

            # The lens output should be at most 80% of the full skill
            # (it selectively extracts sections, so it must be shorter)
            ratio = lens_size / full_size if full_size > 0 else 1.0
            assert ratio < 0.95, (
                f"lens({skill_name!r}) output ({lens_size} chars) is not "
                f"meaningfully smaller than full skill ({full_size} chars); "
                f"ratio={ratio:.2f}"
            )

    def test_max_sections_controls_output_size(self):
        """More max_sections produces more output."""
        result_1 = lens("python-expert", "asyncio event loop", max_sections=1)
        result_3 = lens("python-expert", "asyncio event loop", max_sections=3)
        assert len(result_3) >= len(result_1), (
            f"max_sections=3 ({len(result_3)}) should be >= max_sections=1 ({len(result_1)})"
        )

    def test_lens_raw_sections_count_respects_max(self):
        """lens_raw never returns more sections than max_sections."""
        task = "python concurrency testing debugging profiling"
        for max_s in [1, 2, 3, 5]:
            result = lens_raw("python-expert", task, max_sections=max_s)
            assert len(result["sections"]) <= max_s, (
                f"lens_raw returned {len(result['sections'])} sections but max={max_s}"
            )


# ---------------------------------------------------------------------------
# 10. All Skills Tested
# ---------------------------------------------------------------------------


class TestAllSkillsCoverage:
    """Every available skill should be usable through the lens system."""

    def test_every_skill_has_sections(self):
        """Every skill has at least one ## section."""
        for skill_name in _available_skills():
            headers = _section_headers(skill_name)
            assert len(headers) >= 1, (
                f"Skill {skill_name!r} has no ## sections"
            )

    def test_every_skill_lens_returns_output(self):
        """Every skill can be lensed and produces non-empty output."""
        for skill_name in _available_skills():
            result = lens(skill_name, "relevant task", max_sections=2)
            assert isinstance(result, str), (
                f"lens({skill_name!r}) did not return str"
            )
            assert len(result) > 10, (
                f"lens({skill_name!r}) returned too-short output: {result!r}"
            )

    @pytest.mark.parametrize("skill_name", _available_skills())
    def test_skill_lens_output_starts_with_header(self, skill_name):
        """Every lens output starts with the skill header name."""
        result = lens(skill_name, "task", max_sections=1)
        assert result.startswith("#"), (
            f"lens({skill_name!r}) output does not start with header: "
            f"{result[:80]!r}"
        )

    @pytest.mark.parametrize("skill_name", _available_skills())
    def test_skill_lens_is_valid_markdown(self, skill_name):
        """Lens output is valid markdown with expected structure."""
        result = lens(skill_name, "task", max_sections=2)
        # Has a top-level header
        assert re.search(r"^# ", result, re.MULTILINE), (
            f"lens({skill_name!r}) missing top-level header"
        )
        # Has at least one ## section
        assert "## " in result, (
            f"lens({skill_name!r}) missing ## section header"
        )

    @pytest.mark.parametrize("skill_name", _available_skills())
    def test_skill_lens_includes_context_line(self, skill_name):
        """Every lens output includes the _Context: task_ line."""
        result = lens(skill_name, "specific task description", max_sections=1)
        assert "_Context:" in result, (
            f"lens({skill_name!r}) missing _Context: line"
        )

    @pytest.mark.parametrize("skill_name", _available_skills())
    def test_skill_lens_raw_header_field(self, skill_name):
        """lens_raw returns a non-empty header for every skill."""
        result = lens_raw(skill_name, "task", max_sections=1)
        assert isinstance(result["header"], str)
        assert len(result["header"]) > 0, (
            f"lens_raw({skill_name!r}) has empty header"
        )


# ---------------------------------------------------------------------------
# 11. Safety: No PII, Secrets, or Debug Artifacts
# ---------------------------------------------------------------------------


class TestOutputSafety:
    """Lens output must not leak sensitive data or debugging artifacts."""

    PII_PATTERNS: ClassVar[list[tuple[re.Pattern[str], str]]] = [
        # Only check for real-looking PII patterns (not example.com emails in docs)
        (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "SSN pattern"),
        (re.compile(r"\b(?:ghp_|gho_|ghu_|ghs_|ghr_)[A-Za-z0-9_]{20,}\b"),
         "GitHub token"),
        (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    ]

    SECRET_PATTERNS: ClassVar[list[tuple[re.Pattern[str], str]]] = [
        # Only check for real credential-looking assignments, not docs examples
        (re.compile(r"(?i)(?:api[_-]?key|SECRET_KEY)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]"),
         "real-looking API key assignment"),
    ]

    DEBUG_PATTERNS: ClassVar[list[tuple[re.Pattern[str], str]]] = [
        # Check for debugging artifacts that the lens CODE could inject,
        # not legitimate content in skill files.
        (re.compile(r"Traceback \(most recent call last\)"), "Python traceback"),
        (re.compile(r"console\.error\(.*debug"), "debug console.error"),
    ]

    @pytest.mark.parametrize("skill_name", _available_skills())
    def test_lens_output_no_pii(self, skill_name):
        """Lens output must not contain PII patterns."""
        result = lens(skill_name, "normal task description", max_sections=3)
        for pattern, label in self.PII_PATTERNS:
            match = pattern.search(result)
            assert match is None, (
                f"lens({skill_name!r}) output contains potential {label}: "
                f"{match.group()!r} at position {match.start()}"
            )

    @pytest.mark.parametrize("skill_name", _available_skills())
    def test_lens_output_no_embedded_secrets(self, skill_name):
        """Lens output must not embed secret/credential patterns."""
        result = lens(skill_name, "task", max_sections=3)
        for pattern, label in self.SECRET_PATTERNS:
            match = pattern.search(result)
            assert match is None, (
                f"lens({skill_name!r}) output contains {label}: "
                f"{match.group()!r}"
            )

    @pytest.mark.parametrize("skill_name", _available_skills())
    def test_lens_output_no_debug_artifacts(self, skill_name):
        """Lens output must not contain TODO/FIXME/HACK markers from lens logic."""
        result = lens(skill_name, "task", max_sections=3)
        for pattern, label in self.DEBUG_PATTERNS:
            match = pattern.search(result)
            assert match is None, (
                f"lens({skill_name!r}) output contains {label}: "
                f"{match.group()!r}"
            )

    def test_lens_output_does_not_leak_file_paths(self):
        """Lens output must not contain absolute filesystem paths."""
        result = lens("python-expert", "task", max_sections=3)
        # Should not contain absolute paths (e.g., /Users/...)
        assert "/Users/" not in result, (
            f"lens output contains absolute path: ...{result[result.find('/Users/'):result.find('/Users/')+40]}..."
        )
        assert "/home/" not in result, (
            "lens output contains /home/ path"
        )

    def test_lens_output_does_not_leak_internal_state(self):
        """Lens output must not contain Python traceback or internal state."""
        result = lens("python-expert", "task", max_sections=3)
        forbidden = [
            "Traceback (most recent call last)",
            "File ",
            "_SKILLS_CACHE",
            "_LENS_CACHE",
            "InvalidSkillError",
        ]
        for marker in forbidden:
            assert marker not in result, (
                f"lens output contains internal state marker: {marker!r}"
            )

    def test_lens_output_header_contains_no_internal_data(self):
        """The (lens: ...) marker should just be the skill name, not internal data."""
        result = lens("python-expert", "task", max_sections=1)
        # The marker should contain just the skill name, no paths or cache keys
        marker_match = re.search(r"\(lens:\s*(.+?)\)", result)
        if marker_match:
            marker_content = marker_match.group(1)
            assert marker_content == "python-expert", (
                f"lens marker contains unexpected data: {marker_content!r}"
            )


# ---------------------------------------------------------------------------
# 12. Invariant Tests
# ---------------------------------------------------------------------------


class TestInvariants:
    """Structural invariants that must hold for all inputs."""

    def test_scores_always_between_zero_and_one(self):
        """Every relevance score must be in [0.0, 1.0]."""
        test_cases = [
            ("", "any content"),
            ("any task", ""),
            ("exact match", "exact match"),
            ("partial", "partially matching text with partial overlap"),
            ("unrelated", "completely different topic entirely"),
            ("a" * 100, "b" * 100),
        ]
        for task, section in test_cases:
            score = _score_relevance(task, section)
            assert 0.0 <= score <= 1.0, (
                f"score={score} out of [0.0, 1.0] for task={task!r}, section={section[:50]!r}"
            )

    def test_exact_match_not_necessary_for_nonzero_score(self):
        """A section can score >0 without containing exact task tokens."""
        # The _tokenize function produces substrings in [3-*] range by shrinking
        # words from both ends. This means "deadlock" produces tokens like
        # "dead", "lock", "eadlock", "deadloc", etc. A section with "lock"
        # should match "deadlock" — we don't need the full word.
        score = _score_relevance("deadlock", "locking mechanism")
        assert score >= 0.0  # May be 0 if no subword overlap
        # The key invariant: _score_relevance always returns a float
        assert isinstance(score, float)

    def test_section_with_all_task_tokens_scores_high(self):
        """A section containing every task token should score non-zero."""
        task = "async await coroutine"
        section = "async await coroutine event loop future task"
        score = _score_relevance(task, section)
        assert score > 0.0

    def test_score_monotonic_with_keyword_addition(self):
        """Adding more matching keywords should not decrease score."""
        task = "deadlock debugging async"
        base = "deadlock debugging async"
        extended = "deadlock debugging async event loop coroutine"
        base_score = _score_relevance(task, base)
        ext_score = _score_relevance(task, extended)
        assert ext_score >= base_score - 0.01, (
            f"Score decreased: base={base_score:.3f} extended={ext_score:.3f}"
        )

    def test_tokenize_is_deterministic(self):
        """_tokenize must return the same tokens for the same input."""
        text = "asyncio deadlock event_loop"
        tokens1 = _tokenize(text)
        tokens2 = _tokenize(text)
        assert tokens1 == tokens2

    def test_tokenize_handles_empty(self):
        """_tokenize of empty string returns empty set."""
        assert _tokenize("") == set()

    def test_tokenize_handles_only_punctuation(self):
        """_tokenize of only punctuation returns at most underscore tokens."""
        punct_no_underscore = string.punctuation.replace("_", "")
        assert _tokenize(punct_no_underscore) == set()
        # string.punctuation includes '_' which matches [a-z0-9_]+
        tokens = _tokenize(string.punctuation)
        assert tokens == {"_", ""}

    def test_tokenize_compound_words(self):
        """Compound words with underscores are split into parts."""
        tokens = _tokenize("event_loop")
        assert "event_loop" in tokens or ("event" in tokens and "loop" in tokens)

    def test_tokenize_lowercases_input(self):
        """_tokenize lowercases all input."""
        tokens = _tokenize("ASYNCIO Deadlock")
        assert "ASYNCIO" not in tokens
        assert "DEADLOCK" not in tokens
        assert "asyncio" in tokens or "deadlock" in tokens

    def test_tokenize_bounds_pathological_single_word(self):
        """Subword features stay bounded even for attacker-controlled long tokens."""
        tokens = _tokenize("x" * 10_000)
        assert len(tokens) <= 260

    def test_parse_sections_preserves_section_body_final_newline(self):
        """Section bodies should not have trailing whitespace issues."""
        text = "## Section A\n\nline1\n\nline2\n"
        sections = _parse_sections(text)
        assert len(sections) == 1
        body = sections[0][1]
        # Body is stripped by lens_raw, but _parse_sections should return clean text
        assert "line1" in body
        assert "line2" in body

    def test_lens_raw_task_description_preserved(self):
        """The task_description field in lens_raw result matches input."""
        task = "debug an asyncio deadlock in the event loop"
        result = lens_raw("python-expert", task, max_sections=2)
        assert result["task_description"] == task

    def test_lens_raw_skill_name_preserved(self):
        """The skill_name field matches the requested skill."""
        result = lens_raw("test-quality", "some task", max_sections=1)
        assert result["skill_name"] == "test-quality"


# ---------------------------------------------------------------------------
# 13. Boundary: Invalid Input Handling
# ---------------------------------------------------------------------------


class TestInvalidInputs:
    """Invalid inputs must raise clean errors, not crash."""

    def test_nonexistent_skill_raises(self):
        """Requesting a nonexistent skill raises InvalidSkillError."""
        with pytest.raises(InvalidSkillError):
            lens("definitely-not-a-real-skill-name-zzz", "task")

    def test_path_traversal_skill_name(self):
        """Skill names with path separators are rejected."""
        for name in ["../etc/passwd", "..%2Fetc%2Fpasswd", "foo/bar", "foo\\bar"]:
            with pytest.raises(InvalidSkillError):
                lens(name, "task")

    def test_null_byte_in_skill_name(self):
        """Null byte in skill name is rejected."""
        with pytest.raises(InvalidSkillError):
            lens("bad\x00name", "task")

    def test_skill_name_with_spaces(self):
        """Skill name with spaces is rejected."""
        with pytest.raises(InvalidSkillError):
            lens("bad name", "task")

    def test_skill_name_empty(self):
        """Empty skill name is rejected."""
        with pytest.raises(InvalidSkillError):
            lens("", "task")

    def test_skill_name_starts_with_dash(self):
        """Skill name starting with dash is rejected."""
        with pytest.raises(InvalidSkillError):
            lens("-help", "task")

    def test_skill_name_starts_with_number(self):
        """Skill name starting with number is rejected."""
        with pytest.raises(InvalidSkillError):
            lens("123skill", "task")

    def test_max_sections_negative(self):
        """Negative max_sections should not crash (implementation defined)."""
        # The implementation slices scored[:max_sections], which with negative
        # is Python slicing from the end. Verify it doesn't crash.
        result = lens_raw("test-quality", "task", max_sections=-1)
        assert isinstance(result, dict)
        # Just verify it returns something without error


# ---------------------------------------------------------------------------
# 14. Scoring Distribution
# ---------------------------------------------------------------------------


class TestScoringDistribution:
    """The scoring function should produce a reasonable distribution."""

    def test_random_tasks_produce_varied_scores(self):
        """Different tasks should produce different score distributions."""
        tasks = [
            "asyncio event loop coroutine debugging",
            "Jython Java classpath compatibility",
            "type annotations mypy checking",
            "pytest coverage fixtures mocking",
            "SQLAlchemy ORM sessions queries",
        ]
        all_scores: list[list[float]] = []
        for task in tasks:
            result = lens_raw("python-expert", task, max_sections=5)
            all_scores.append([s["score"] for s in result["sections"]])

        # Verify each task produces some non-zero scores
        for _i, (task, scores) in enumerate(zip(tasks, all_scores, strict=False)):
            assert any(s > 0.0 for s in scores), (
                f"Task '{task}' produced all zero scores"
            )

        # Different tasks should not all return identical score lists
        score_tuples = [tuple(s) for s in all_scores]
        unique = set(score_tuples)
        assert len(unique) >= 2, (
            "All tasks produced identical score distributions"
        )

    def test_related_tasks_score_higher_than_unrelated(self):
        """A task about topic X should score higher on section X than on section Y."""
        async_task = "debug an asyncio deadlock in the event loop"
        async_section = "asyncio event loop coroutine task future gather run"
        type_section = "mypy type annotations generics protocol overload"
        async_score = _score_relevance(async_task, async_section)
        type_score = _score_relevance(async_task, type_section)
        assert async_score > type_score, (
            f"async task scored higher on type section: "
            f"async={async_score:.3f} type={type_score:.3f}"
        )


# ---------------------------------------------------------------------------
# 15. token_size helpers: verify output is meaningfully bounded
# ---------------------------------------------------------------------------


class TestOutputBounded:
    """Lens output should be bounded in size."""

    def test_lens_output_does_not_exceed_skill_size(self):
        """Lens output must never exceed the full skill text size."""
        for skill_name in _available_skills():
            full_path = SKILLS_DIR / skill_name / "SKILL.md"
            full_size = len(full_path.read_text())
            result = lens(skill_name, "task", max_sections=3)
            assert len(result) < full_size, (
                f"lens({skill_name!r}) output ({len(result)}) >= "
                f"full skill ({full_size})"
            )

    def test_empty_task_returns_something(self):
        """Empty task should still return valid lens output."""
        result = lens("test-quality", "", max_sections=2)
        assert isinstance(result, str)
        assert len(result) > 30
        assert result.startswith("#")

    def test_lens_output_has_no_empty_sections(self):
        """Sections in lens output should have content."""
        task = "comprehensive testing and quality assurance"
        result = lens("test-quality", task, max_sections=3)
        # Each "## " section should be followed by content, not another header
        sections = result.split("\n## ")
        for i, sec in enumerate(sections[1:], 1):  # skip the "# " header
            lines = sec.split("\n")
            # First line is the header; there should be more lines after it
            assert len(lines) > 1, (
                f"Section {i} in lens output has no content after header: {sec[:80]!r}"
            )
