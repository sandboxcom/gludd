"""Tests that PromptEnhancer is wired into the production prompt-rendering path.

Tests verify:
1. PromptEnhancer can be imported from its module.
2. PromptEnhancer can be constructed with mock store / None store.
3. BehaviorRenderer accepts and uses PromptEnhancer (production wiring).
4. PromptEnhancer is importable in daemon.py (production module).
5. Daemon startup pattern: construct and store on app.state.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from general_ludd.agents.behavior import AgentBehavior
from general_ludd.execution.situation_store import BadCallSituationStore
from general_ludd.prompts.enhancer import PromptEnhancer


class TestPromptEnhancerWiring:
    """Tests confirming PromptEnhancer is wired into the production path."""

    def test_prompt_enhancer_importable(self) -> None:
        """PromptEnhancer can be imported from its module."""
        from general_ludd.prompts.enhancer import PromptEnhancer as PE

        assert PE is not None
        assert callable(PE)

    def test_prompt_enhancer_constructable_with_no_store(self) -> None:
        """PromptEnhancer can be constructed with store=None."""
        enhancer = PromptEnhancer(store=None)
        assert enhancer is not None
        assert enhancer.generate_avoidance_warning() == ""
        assert enhancer.get_recent_blocked_tools() == set()
        assert enhancer.get_blocked_tool_counts() == {}
        assert enhancer.format_tool_advice("some_tool") == ""

    def test_prompt_enhancer_constructable_with_mock_store(self) -> None:
        """PromptEnhancer can be constructed with a mock BadCallSituationStore."""
        mock_store = MagicMock(spec=BadCallSituationStore)
        mock_store.list_recent.return_value = []
        mock_store.list_by_tool.return_value = []

        enhancer = PromptEnhancer(store=mock_store, max_situations=10)
        assert enhancer is not None
        assert enhancer.generate_avoidance_warning() == ""

    def test_prompt_enhancer_constructable_with_max_situations(self) -> None:
        """PromptEnhancer accepts max_situations constructor param."""
        enhancer = PromptEnhancer(store=None, max_situations=50)
        assert enhancer._max_situations == 50

    def test_enhance_prompt_passes_through_when_no_store(self) -> None:
        """enhance_prompt returns original prompt unchanged when store is None."""
        enhancer = PromptEnhancer(store=None)
        original = "You are a coding agent."
        assert enhancer.enhance_prompt(original) == original

    def test_enhance_messages_passes_through_when_no_store(self) -> None:
        """enhance_messages returns original messages unchanged when store is None."""
        enhancer = PromptEnhancer(store=None)
        messages = [
            {"role": "system", "content": "You are a coding agent."},
            {"role": "user", "content": "Write a test."},
        ]
        result = enhancer.enhance_messages(messages)
        assert result == messages

    # --- Production wiring tests ---

    def test_behavior_renderer_accepts_prompt_enhancer(self) -> None:
        """BehaviorRenderer.__init__ accepts an optional prompt_enhancer parameter."""
        from general_ludd.agents.behavior import BehaviorRenderer

        mock_enhancer = MagicMock(spec=PromptEnhancer)
        mock_enhancer.enhance_prompt.return_value = "enhanced"
        mock_enhancer.enhance_messages.return_value = [{"role": "system", "content": "enhanced"}]

        renderer = BehaviorRenderer(prompt_enhancer=mock_enhancer)
        assert renderer is not None

    def test_behavior_renderer_enhances_prompt_when_wired(self) -> None:
        """BehaviorRenderer.render_as_prompt calls enhance_prompt when enhancer is wired."""
        from general_ludd.agents.behavior import BehaviorRenderer

        mock_enhancer = MagicMock(spec=PromptEnhancer)
        mock_enhancer.enhance_prompt.return_value = "enhanced: " + "original"

        renderer = BehaviorRenderer(prompt_enhancer=mock_enhancer)
        behavior = AgentBehavior()
        result = renderer.render_as_prompt(behavior, "test_agent", "fix the bug")

        mock_enhancer.enhance_prompt.assert_called_once()
        assert result.startswith("enhanced: ")

    def test_behavior_renderer_works_without_enhancer(self) -> None:
        """BehaviorRenderer still works without prompt_enhancer (backward compat)."""
        from general_ludd.agents.behavior import BehaviorRenderer

        renderer = BehaviorRenderer()
        behavior = AgentBehavior()
        result = renderer.render_as_prompt(behavior, "test_agent", "fix the bug")

        assert "You are agent **test_agent**" in result
        assert "fix the bug" in result

    # --- Daemon startup pattern tests ---

    def test_daemon_startup_wires_prompt_enhancer_on_app_state(self) -> None:
        """PromptEnhancer can be instantiated and stored on app.state (daemon pattern)."""
        enhancer = PromptEnhancer(store=None)

        class MockAppState:
            _prompt_enhancer: PromptEnhancer | None = None

        state = MockAppState()
        state._prompt_enhancer = enhancer

        assert state._prompt_enhancer is enhancer
        assert isinstance(state._prompt_enhancer, PromptEnhancer)
        assert state._prompt_enhancer.generate_avoidance_warning() == ""
        assert state._prompt_enhancer.get_recent_blocked_tools() == set()

    def test_prompt_enhancer_importable_in_daemon_module(self) -> None:
        """PromptEnhancer is imported in daemon.py (production module)."""
        import ast
        import importlib

        daemon_mod = importlib.import_module("general_ludd.daemon")
        source_file = daemon_mod.__file__
        assert source_file is not None

        with open(source_file) as f:
            tree = ast.parse(f.read())

        enhancer_imported = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "general_ludd.prompts.enhancer":
                for alias in node.names:
                    if alias.name == "PromptEnhancer":
                        enhancer_imported = True
                        break

        assert enhancer_imported, "PromptEnhancer must be imported in daemon.py"

    def test_prompt_enhancer_stored_on_daemon_state(self) -> None:
        """PromptEnhancer is stored on app.state._prompt_enhancer in daemon.py."""
        import ast
        import importlib

        daemon_mod = importlib.import_module("general_ludd.daemon")
        source_file = daemon_mod.__file__
        assert source_file is not None

        with open(source_file) as f:
            tree = ast.parse(f.read())

        enhancer_stored = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    code = ast.unparse(target)
                    if "._prompt_enhancer" in code:
                        enhancer_stored = True
                        break

        assert enhancer_stored, "PromptEnhancer must be stored on app.state._prompt_enhancer in daemon.py"
