"""Tests for game_gen — game code generation, syntax validation, specs."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import mock

import pytest

from general_ludd.cloud.game_gen import (
    DOOM_HALLWAY_SPEC,
    FUTURE_GAMES,
    GAME_TEMPLATES,
    QUAKE_ARENA_SPEC,
    generate_game_code,
    validate_game_syntax,
    write_game_file,
)


class TestSpecsAndTemplates:
    def test_quake_arena_spec_keys(self):
        spec = QUAKE_ARENA_SPEC
        assert spec["name"] == "quake_dm6_arena"
        assert spec["genre"] == "fps"
        assert "expected_frames" in spec
        assert "similarity_threshold" in spec
        assert "colors" in spec

    def test_doom_hallway_spec_keys(self):
        spec = DOOM_HALLWAY_SPEC
        assert spec["name"] == "doom_e1m1_hallway"
        assert spec["genre"] == "fps"
        assert "expected_frames" in spec
        assert "similarity_threshold" in spec

    def test_future_games_contains_four_entries(self):
        assert "wipeout_racing" in FUTURE_GAMES
        assert "descent_tunnel" in FUTURE_GAMES
        assert "rogue_dungeon" in FUTURE_GAMES
        assert len(FUTURE_GAMES) == 3

    def test_game_templates_contains_five(self):
        assert "doom_hallway" in GAME_TEMPLATES
        assert "quake_arena" in GAME_TEMPLATES
        assert "wipeout_racing" in GAME_TEMPLATES
        assert "descent_tunnel" in GAME_TEMPLATES
        assert "rogue_dungeon" in GAME_TEMPLATES
        assert len(GAME_TEMPLATES) == 5


class TestGenerateGameCode:
    def test_unknown_template_raises(self):
        with pytest.raises(ValueError, match="Unknown game template"):
            generate_game_code(None, "nonexistent")

    def test_none_gateway_raises(self):
        with pytest.raises(ValueError, match="ModelGateway is required"):
            generate_game_code(None, "doom_hallway")

    def test_calls_gateway_and_normalizes(self):
        mock_gw = mock.MagicMock()
        mock_gw.call_model.return_value = "```python\nprint('generated')\n```"

        result = generate_game_code(mock_gw, "doom_hallway")
        assert result == "print('generated')"
        mock_gw.call_model.assert_called_once()

    def test_passes_template_as_content(self):
        mock_gw = mock.MagicMock()
        mock_gw.call_model.return_value = "result"

        generate_game_code(mock_gw, "quake_arena")
        call_kwargs = mock_gw.call_model.call_args
        messages = call_kwargs[1]["messages"]
        assert "Dark metal-textured platforms" in messages[0]["content"]


class TestValidateGameSyntax:
    def test_valid_game_code(self):
        code = (
            "import pygame\n"
            "pygame.display.set_mode((800,600))\n"
            "running = True\n"
            "while running:\n"
            "    for event in pygame.event.get():\n"
            "        if event.type == pygame.QUIT:\n"
            "            running = False\n"
        )
        assert validate_game_syntax(code) is True

    def test_invalid_syntax(self):
        code = "def foo(:\n    pass"
        assert validate_game_syntax(code) is False

    def test_missing_pygame_import(self):
        code = (
            "running = True\n"
            "while running:\n"
            "    for event in [1,2,3]:\n"
            "        if event == pygame.QUIT:\n"
            "            running = False\n"
        )
        assert validate_game_syntax(code) is False

    def test_missing_game_loop(self):
        code = "import pygame\npygame.display.set_mode((800,600))\n"
        assert validate_game_syntax(code) is False

    def test_missing_input_handling(self):
        code = "import pygame\nrunning = True\nwhile running:\n    pass\n"
        assert validate_game_syntax(code) is False

    def test_import_from_pygame(self):
        code = (
            "from pygame.locals import QUIT\n"
            "import pygame\n"
            "running = True\n"
            "while running:\n"
            "    for evt in pygame.event.get():\n"
            "        if evt.type == QUIT:\n"
            "            running = False\n"
        )
        assert validate_game_syntax(code) is True

    def test_for_loop_game_loop(self):
        code = (
            "import pygame\n"
            "for frame in range(30):\n"
            "    for evt in pygame.event.get():\n"
            "        if evt.type == pygame.QUIT:\n"
            "            break\n"
        )
        assert validate_game_syntax(code) is True

    def test_uses_flip_under_pygame(self):
        code = (
            "import pygame\n"
            "pygame.display.set_mode((800,600))\n"
            "running = True\n"
            "while running:\n"
            "    for e in pygame.event.get():\n"
            "        if e.type == pygame.QUIT:\n"
            "            running = False\n"
            "    pygame.display.flip()\n"
        )
        assert validate_game_syntax(code) is True

    def test_unparseable_syntax(self):
        code = "this is not valid python (((("
        assert validate_game_syntax(code) is False

    def test_empty_string(self):
        assert validate_game_syntax("") is False


class TestWriteGameFile:
    def test_writes_code_to_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_game_file("print('hello')", Path(tmpdir) / "game.py")
            assert path.exists()
            assert path.read_text(encoding="utf-8") == "print('hello')"

    def test_creates_parent_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            deep = Path(tmpdir) / "a" / "b" / "game.py"
            path = write_game_file("x = 1", deep)
            assert path.exists()
            assert path == deep

    def test_returns_path_instance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = write_game_file("x = 1", Path(tmpdir) / "out.py")
            assert isinstance(result, Path)

    def test_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "overwrite.py"
            p.write_text("old")
            write_game_file("new", p)
            assert p.read_text(encoding="utf-8") == "new"
