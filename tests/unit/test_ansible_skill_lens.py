"""Deep tests for skill_lens — surgical section extraction from expert skills."""

from __future__ import annotations

import general_ludd.ansible.skill_lens as skill_lens

# ── helpers ────────────────────────────────────────────────────────────────


def _write_skill(dir_, name, content):
    skill_dir = dir_ / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content)


# ── 1. InvalidSkillError ───────────────────────────────────────────────────


def test_invalid_skill_error_is_value_error():
    assert issubclass(skill_lens.InvalidSkillError, ValueError)


def test_invalid_skill_error_message():
    exc = skill_lens.InvalidSkillError("bad skill: foo")
    assert "foo" in str(exc)


# ── 2. SKILL_NAME_RE ───────────────────────────────────────────────────────


def test_valid_skill_names():
    for name in ("python-expert", "go_expert", "azure", "culinary99"):
        assert skill_lens._SKILL_NAME_RE.match(name)


def test_rejects_uppercase_skill_name():
    assert not skill_lens._SKILL_NAME_RE.match("Python-Expert")


def test_rejects_skill_name_with_slash():
    assert not skill_lens._SKILL_NAME_RE.match("a/b")


def test_rejects_skill_name_with_spaces():
    assert not skill_lens._SKILL_NAME_RE.match("my skill")


def test_rejects_empty_skill_name():
    assert not skill_lens._SKILL_NAME_RE.match("")


# ── 3. _strip_frontmatter ──────────────────────────────────────────────────


def test_strip_frontmatter_yaml_header():
    text = "---\ntitle: Demo\n---\n\n# Title\ncontent"
    result = skill_lens._strip_frontmatter(text)
    assert "# Title" in result
    assert "title: Demo" not in result
    assert "---" not in result


def test_strip_frontmatter_no_header():
    text = "# Just a title\n\ncontent"
    result = skill_lens._strip_frontmatter(text)
    assert result == text


def test_strip_frontmatter_malformed_only_opening():
    text = "---\nno closing\n# Title"
    result = skill_lens._strip_frontmatter(text)
    assert result == text  # malformed = no strip


# ── 4. _parse_sections ─────────────────────────────────────────────────────


def test_parse_sections_splits_on_h2():
    text = "# Title\n\n## Section A\nbody a\n\n## Section B\nbody b"
    sections = skill_lens._parse_sections(text)
    assert len(sections) == 2
    assert sections[0][0] == "## Section A"
    assert "body a" in sections[0][1]
    assert sections[1][0] == "## Section B"
    assert "body b" in sections[1][1]


def test_parse_sections_skips_process_frontmatter():
    text = "---\nkey: val\n---\n\n# Main\n\n## Target\npayload"
    sections = skill_lens._parse_sections(text)
    assert len(sections) == 1
    assert sections[0][0] == "## Target"
    assert "---" not in sections[0][1]


def test_parse_sections_ignores_h3_as_header():
    text = "# Title\n\n## Section\n### Subsection\nmore text"
    sections = skill_lens._parse_sections(text)
    assert len(sections) == 1
    assert "### Subsection" in sections[0][1]


def test_parse_sections_no_sections():
    text = "# Only a title"
    sections = skill_lens._parse_sections(text)
    assert len(sections) == 0


def test_parse_sections_empty_content():
    sections = skill_lens._parse_sections("")
    assert sections == []


# ── 5. _tokenize ───────────────────────────────────────────────────────────


def test_tokenize_lowercases():
    tokens = skill_lens._tokenize("ASYNCIO PAtterns")
    assert "asyncio" in tokens
    assert "patterns" in tokens


def test_tokenize_subword_decomposition():
    tokens = skill_lens._tokenize("deadlock")
    assert "dead" in tokens
    assert "lock" in tokens
    assert "deadlock" in tokens


def test_tokenize_underscore_split():
    tokens = skill_lens._tokenize("event_loop_queue")
    assert "event" in tokens
    assert "loop" in tokens
    assert "queue" in tokens
    assert "eventloop" in tokens or "event_loop" in tokens


def test_tokenize_empty_string():
    assert skill_lens._tokenize("") == set()


def test_tokenize_non_lowercase_only_chars():
    tokens = skill_lens._tokenize("!!!")
    assert tokens == set()


# ── 6. _score_relevance ────────────────────────────────────────────────────


def test_score_perfect_match():
    score = skill_lens._score_relevance("python asyncio", "how to use python asyncio for event loops")
    assert score > 0.0


def test_score_no_match():
    score = skill_lens._score_relevance("python asyncio", "java garbage")
    assert score == 0.0


def test_score_near_zero_on_disjoint():
    score = skill_lens._score_relevance("python asyncio", "java garbage collection")
    assert score < 0.01


def test_score_empty_description():
    assert skill_lens._score_relevance("", "some text") == 0.0
    assert skill_lens._score_relevance("task", "") == 0.0


def test_score_partial_match():
    score_a = skill_lens._score_relevance("python asyncio", "python asyncio event loop")
    score_b = skill_lens._score_relevance("python asyncio", "java threads")
    assert score_a > score_b


# ── 7. _read_header_name ───────────────────────────────────────────────────


def test_read_header_name_h1():
    name = skill_lens._read_header_name("# Python Expert\n\ncontent")
    assert name == "Python Expert"


def test_read_header_name_strips_frontmatter_first():
    name = skill_lens._read_header_name("---\nfoo: bar\n---\n\n# Real Header\n\nbody")
    assert name == "Real Header"


def test_read_header_name_no_h1():
    assert skill_lens._read_header_name("## only h2") == ""


# ── 8. lens_raw ────────────────────────────────────────────────────────────


def test_lens_raw_valid_skill(tmp_path, monkeypatch):
    skill_lens.clear_cache()
    _write_skill(tmp_path, "demo", "# Demo\n\n## Python\ndata\n\n## Java\njvm data")
    monkeypatch.setattr(skill_lens, "_skills_dir", lambda: tmp_path)

    raw = skill_lens.lens_raw("demo", "python code", max_sections=1)
    assert raw["skill_name"] == "demo"
    assert raw["header"] == "Demo"
    assert len(raw["sections"]) == 1
    assert raw["sections"][0]["header"] == "## Python"


def test_lens_raw_no_sections(tmp_path, monkeypatch):
    skill_lens.clear_cache()
    _write_skill(tmp_path, "empty", "# Empty\n\nno sections here")
    monkeypatch.setattr(skill_lens, "_skills_dir", lambda: tmp_path)

    raw = skill_lens.lens_raw("empty", "anything")
    assert raw["sections"] == []


def test_lens_raw_sorts_by_relevance(tmp_path, monkeypatch):
    skill_lens.clear_cache()
    _write_skill(
        tmp_path,
        "poly",
        "# Skills\n\n## Python\nasyncio async await\n\n## Java\njvm threads\n\n## Go\ngoroutines channels",
    )
    monkeypatch.setattr(skill_lens, "_skills_dir", lambda: tmp_path)

    raw = skill_lens.lens_raw("poly", "python asyncio", max_sections=2)
    assert raw["sections"][0]["header"] == "## Python"


def test_lens_raw_empty_task_description_preserves_order(tmp_path, monkeypatch):
    skill_lens.clear_cache()
    _write_skill(tmp_path, "demo", "# Demo\n\n## Z Last\nz content\n\n## A First\na content")
    monkeypatch.setattr(skill_lens, "_skills_dir", lambda: tmp_path)

    raw = skill_lens.lens_raw("demo", "")
    assert raw["sections"][0]["header"] == "## Z Last"


# ── 9. lens (rendered function) ────────────────────────────────────────────


def test_lens_renders_markdown(tmp_path, monkeypatch):
    skill_lens.clear_cache()
    _write_skill(tmp_path, "demo", "# Demo\n\n## Python\nasyncio patterns\n\n## Java\nJVM patterns")
    monkeypatch.setattr(skill_lens, "_skills_dir", lambda: tmp_path)

    rendered = skill_lens.lens("demo", "asyncio python", max_sections=1)
    assert "# Demo (lens: demo)" in rendered
    assert "Python" in rendered
    assert "Java" not in rendered
    assert "(relevance:" in rendered


def test_lens_uses_cache(tmp_path, monkeypatch):
    skill_lens.clear_cache()
    _write_skill(tmp_path, "demo", "# Demo\n\n## Python\ndata")
    monkeypatch.setattr(skill_lens, "_skills_dir", lambda: tmp_path)

    first = skill_lens.lens("demo", "python", max_sections=1)
    second = skill_lens.lens("demo", "python", max_sections=1)
    assert first == second


def test_lens_cache_partitioned_by_params(tmp_path, monkeypatch):
    skill_lens.clear_cache()
    _write_skill(tmp_path, "demo", "# Demo\n\n## Python\ndata\n\n## Java\ndata")
    monkeypatch.setattr(skill_lens, "_skills_dir", lambda: tmp_path)

    a = skill_lens.lens("demo", "python", max_sections=1)
    b = skill_lens.lens("demo", "java", max_sections=1)
    assert a != b


def test_lens_includes_context_line(tmp_path, monkeypatch):
    skill_lens.clear_cache()
    _write_skill(tmp_path, "demo", "# Demo\n\n## Section\nbody")
    monkeypatch.setattr(skill_lens, "_skills_dir", lambda: tmp_path)

    rendered = skill_lens.lens("demo", "debug something")
    assert "_Context:" in rendered
    assert "debug something" in rendered


# ── 10. _skill_path validation ─────────────────────────────────────────────


def test_skill_path_invalid_name_raises():
    try:
        skill_lens._skill_path("INVALID name!")
    except skill_lens.InvalidSkillError:
        return
    raise AssertionError("should have raised InvalidSkillError")


def test_skill_path_missing_directory(monkeypatch, tmp_path):
    missing = tmp_path / "nonexistent"
    monkeypatch.setattr(skill_lens, "_skills_dir", lambda: missing)
    try:
        skill_lens._skill_path("demo")
    except skill_lens.InvalidSkillError:
        return
    raise AssertionError("should have raised InvalidSkillError")


def test_skill_path_missing_skill_file(monkeypatch, tmp_path):
    empty_dir = tmp_path / "skills"
    empty_dir.mkdir()
    monkeypatch.setattr(skill_lens, "_skills_dir", lambda: empty_dir)
    try:
        skill_lens._skill_path("demo")
    except skill_lens.InvalidSkillError:
        return
    raise AssertionError("should have raised InvalidSkillError")


# ── 11. _read_skill_file ───────────────────────────────────────────────────


def test_read_skill_file(tmp_path):
    sf = tmp_path / "SKILL.md"
    sf.write_text("# My Skill\n\ncontent")
    result = skill_lens._read_skill_file(sf)
    assert result == "# My Skill\n\ncontent"


# ── 12. clear_cache ────────────────────────────────────────────────────────


def test_clear_cache_wipes_both(tmp_path, monkeypatch):
    _write_skill(tmp_path, "demo", "# Demo\n\n## Section\ndata")
    monkeypatch.setattr(skill_lens, "_skills_dir", lambda: tmp_path)

    skill_lens.lens("demo", "python")
    assert skill_lens._SKILLS_CACHE or skill_lens._LENS_CACHE
    skill_lens.clear_cache()
    assert not skill_lens._SKILLS_CACHE
    assert not skill_lens._LENS_CACHE


# ── 13. lens raises on invalid skill ───────────────────────────────────────


def test_lens_raises_on_invalid_name():
    try:
        skill_lens.lens("BAD NAME!", "task")
    except skill_lens.InvalidSkillError:
        return
    raise AssertionError("should have raised")


# ── 14. lens_raw high max_sections ─────────────────────────────────────────


def test_lens_raw_max_sections_larger_than_available(tmp_path, monkeypatch):
    skill_lens.clear_cache()
    _write_skill(tmp_path, "demo", "# Demo\n\n## A\na\n\n## B\nb")
    monkeypatch.setattr(skill_lens, "_skills_dir", lambda: tmp_path)

    raw = skill_lens.lens_raw("demo", "b", max_sections=99)
    assert len(raw["sections"]) == 2


# ── 15. tokenize edge: numbers and mixed content ───────────────────────────


def test_tokenize_includes_numbers():
    tokens = skill_lens._tokenize("python3 v26")
    assert "python3" in tokens or "python" in tokens
    assert "v26" in tokens or "26" in tokens


# ── 16. _strip_frontmatter empty ───────────────────────────────────────────


def test_strip_frontmatter_empty():
    assert skill_lens._strip_frontmatter("") == ""
