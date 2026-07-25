import general_ludd.agents.skill_context as skill_context


def test_provider_lenses_identified_skill(monkeypatch):
    monkeypatch.setattr(skill_context, "_validate_skill_exists", lambda name: name == "python-expert")
    monkeypatch.setattr(skill_context, "lens_raw", lambda *args: {"sections": [{"header": "## Async", "body": "x"}]})
    monkeypatch.setattr(skill_context, "lens", lambda *args: "## Async\\nUse await.")
    monkeypatch.setattr(skill_context, "_read_full_skill_text", lambda name: "full skill text " * 20)
    result = skill_context.SkillContextProvider(skill_names=["python-expert"]).provide("Fix asyncio pytest test")
    assert result.skills_used == ["python-expert"]
    assert "Skill Context: python-expert" in result.context_text
    assert result.token_savings > 0
