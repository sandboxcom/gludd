"""Unit tests for prompt registry."""

import pytest
from jinja2 import TemplateNotFound

from general_ludd.prompts.registry import PromptRegistry


class TestPromptRegistry:
    def test_register_and_render(self):
        reg = PromptRegistry()
        reg.register("greeting", "Hello {{ name }}!")
        result = reg.render("greeting", name="World")
        assert result == "Hello World!"

    def test_list_templates(self):
        reg = PromptRegistry()
        reg.register("a", "template a")
        reg.register("b", "template b")
        assert sorted(reg.list_templates()) == ["a", "b"]

    def test_render_missing_template_raises(self):
        reg = PromptRegistry()
        with pytest.raises(TemplateNotFound):
            reg.render("nonexistent")

    def test_version_default(self):
        reg = PromptRegistry()
        assert reg.version == "0.1.0"

    def test_version_custom(self):
        reg = PromptRegistry(version="2.3.1")
        assert reg.version == "2.3.1"

    def test_template_hash_computed_on_register(self):
        reg = PromptRegistry()
        reg.register("t1", "Hello {{ name }}!")
        info = reg.get_template_version_info("t1")
        assert "hash" in info
        assert info["hash"] is not None
        assert len(info["hash"]) == 64  # SHA-256 hex digest

    def test_template_hash_history_tracks_changes(self):
        reg = PromptRegistry()
        reg.register("t1", "original content")
        h1 = reg.get_template_version_info("t1")["hash"]
        reg.register("t1", "modified content")
        h2 = reg.get_template_version_info("t1")["hash"]
        assert h1 != h2
        history = reg.get_template_version_info("t1").get("history", [])
        assert len(history) >= 1
        assert h1 in history

    def test_get_template_version_info_unknown_returns_empty(self):
        reg = PromptRegistry()
        info = reg.get_template_version_info("nonexistent")
        assert info == {"hash": None, "history": []}

    def test_hash_based_on_content_only(self):
        reg1 = PromptRegistry()
        reg2 = PromptRegistry()
        reg1.register("t", "same content")
        reg2.register("t", "same content")
        h1 = reg1.get_template_version_info("t")["hash"]
        h2 = reg2.get_template_version_info("t")["hash"]
        assert h1 == h2  # same content = same hash

    def test_history_limit_is_bounded(self):
        reg = PromptRegistry()
        for i in range(10):
            reg.register("t", f"content {i}")
        info = reg.get_template_version_info("t")
        assert len(info["history"]) <= 5  # bounded history
