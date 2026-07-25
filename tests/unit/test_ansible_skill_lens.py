import general_ludd.ansible.skill_lens as skill_lens


def test_lens_selects_relevant_sections_from_skill_file(tmp_path, monkeypatch):
    skill = tmp_path / "demo" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(chr(10).join(("# Demo", "", "## Python", "asyncio patterns", "", "## Java", "JVM patterns", "")))
    monkeypatch.setattr(skill_lens, "_skills_dir", lambda: tmp_path)
    skill_lens.clear_cache()
    rendered = skill_lens.lens("demo", "asyncio python", max_sections=1)
    assert "# Demo (lens: demo)" in rendered
    assert "Python" in rendered
    assert "Java" not in rendered
