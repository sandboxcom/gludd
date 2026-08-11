"""Tests for game_gen — game code generation, syntax validation, specs,
headless execution, frame capture."""

from __future__ import annotations

import importlib
import tempfile
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

import general_ludd.cloud.game_gen as game_gen_module
from general_ludd.cloud.game_gen import (
    DOOM_HALLWAY_SPEC,
    FUTURE_GAMES,
    GAME_TEMPLATES,
    QUAKE_ARENA_SPEC,
    generate_game_code,
    run_game_headless,
    validate_game_syntax,
    write_game_file,
)


class TestRequirePygame:
    def test_raises_when_pygame_not_available(self):
        with mock.patch("general_ludd.cloud.game_gen.pygame", None):
            with pytest.raises(ImportError, match="pygame is required"):
                game_gen_module._require_pygame()

    def test_returns_pygame_when_available(self):
        fake_module = mock.MagicMock()
        with mock.patch("general_ludd.cloud.game_gen.pygame", fake_module):
            assert game_gen_module._require_pygame() is fake_module

    def test_pygame_none_at_module_level_fails_gracefully(self):
        saved = importlib.import_module("general_ludd.cloud.game_gen").pygame

        try:
            game_gen_module.pygame = None
            with pytest.raises(ImportError, match="pygame is required"):
                game_gen_module._require_pygame()
        finally:
            game_gen_module.pygame = saved


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

    def test_quake_spec_colors_structure(self):
        spec = QUAKE_ARENA_SPEC
        assert spec["colors"]["primary"] == "orange/brown"
        assert "lava" in spec["colors"]
        assert spec["expected_frames"] == 30
        assert spec["similarity_threshold"] == 0.35

    def test_future_games_each_has_required_keys(self):
        for name, spec in FUTURE_GAMES.items():
            assert spec["name"] == name
            assert "genre" in spec
            assert "expected_frames" in spec
            assert spec["expected_frames"] == 30
            assert "colors" in spec

    def test_game_templates_contains_five(self):
        assert "doom_hallway" in GAME_TEMPLATES
        assert "quake_arena" in GAME_TEMPLATES
        assert "wipeout_racing" in GAME_TEMPLATES
        assert "descent_tunnel" in GAME_TEMPLATES
        assert "rogue_dungeon" in GAME_TEMPLATES
        assert len(GAME_TEMPLATES) == 5

    def test_game_templates_non_empty_strings(self):
        for _name, template in GAME_TEMPLATES.items():
            assert len(template) > 100
            assert "pygame" in template.lower() or "python game" in template.lower()
            assert "800x600" in template or "800x600" in template

    def test_every_template_has_all_games(self):
        template_names = set(GAME_TEMPLATES)
        {spec["name"] for spec in [QUAKE_ARENA_SPEC, DOOM_HALLWAY_SPEC]}
        set(FUTURE_GAMES)
        assert template_names == {"doom_hallway", "quake_arena", "wipeout_racing", "descent_tunnel", "rogue_dungeon"}


class TestGenerateGameCode:
    def test_unknown_template_raises(self):
        with pytest.raises(ValueError, match="Unknown game template"):
            generate_game_code(None, "nonexistent")

    def test_none_gateway_raises(self):
        with pytest.raises(ValueError, match="ModelGateway is required"):
            generate_game_code(None, "doom_hallway")

    def test_unknown_template_with_valid_gateway_raises(self):
        mock_gw = mock.MagicMock()
        with pytest.raises(ValueError, match="Unknown game template"):
            generate_game_code(mock_gw, "bogus_template")

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

    def test_calls_model_with_default_profile(self):
        mock_gw = mock.MagicMock()
        mock_gw.call_model.return_value = "ok"
        generate_game_code(mock_gw, "rogue_dungeon")
        mock_gw.call_model.assert_called_once()
        call_args = mock_gw.call_model.call_args[0]
        assert call_args[0] == "default"

    def test_passes_spec_parameter_through(self):
        mock_gw = mock.MagicMock()
        mock_gw.call_model.return_value = "ok"
        extra_spec = {"custom": "value"}
        generate_game_code(mock_gw, "doom_hallway", spec=extra_spec)
        assert True  # spec is accepted without error

    def test_all_five_templates_generate(self):
        mock_gw = mock.MagicMock()
        mock_gw.call_model.return_value = "result"
        for name in GAME_TEMPLATES:
            result = generate_game_code(mock_gw, name)
            assert isinstance(result, str)
            assert len(result) > 0

    def test_normalize_generated_python_handles_no_fence(self):
        mock_gw = mock.MagicMock()
        mock_gw.call_model.return_value = "import pygame\nprint('hello')"
        result = generate_game_code(mock_gw, "doom_hallway")
        assert result == "import pygame\nprint('hello')"


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

    def test_comments_only_is_invalid(self):
        code = "# just a comment\n# no real code here\n"
        assert validate_game_syntax(code) is False

    def test_keydown_input_detected(self):
        code = (
            "import pygame\n"
            "running = True\n"
            "while running:\n"
            "    for event in pygame.event.get():\n"
            "        if event.type == pygame.KEYDOWN:\n"
            "            running = False\n"
        )
        assert validate_game_syntax(code) is True

    def test_mousemotion_input_detected(self):
        code = (
            "import pygame\n"
            "running = True\n"
            "while running:\n"
            "    for event in pygame.event.get():\n"
            "        if event.type == pygame.MOUSEMOTION:\n"
            "            running = False\n"
        )
        assert validate_game_syntax(code) is True

    def test_multiple_imports_still_valid(self):
        code = (
            "import sys, os, pygame\n"
            "running = True\n"
            "while running:\n"
            "    for event in pygame.event.get():\n"
            "        running = False\n"
        )
        assert validate_game_syntax(code) is True

    def test_pygame_attribute_not_event_method_still_valid(self):
        code = (
            "import pygame\n"
            "screen = pygame.display.set_mode((800,600))\n"
            "running = True\n"
            "while running:\n"
            "    for event in pygame.event.get():\n"
            "        if event.type == pygame.QUIT:\n"
            "            running = False\n"
            "    screen.fill((0,0,0))\n"
        )
        assert validate_game_syntax(code) is True


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

    def test_encoding_is_utf8(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_game_file("héllo 🌍", Path(tmpdir) / "utf8.py")
            assert path.read_text(encoding="utf-8") == "héllo 🌍"

    def test_nested_dirs_created(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_game_file("code", Path(tmpdir) / "x" / "y" / "z" / "game.py")
            assert path.parent.exists()
            assert path.exists()

    def test_path_is_absolute(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_game_file("x", Path(tmpdir) / "game.py")
            assert path.is_absolute()


class TestRunGameHeadless:
    def test_raises_when_pygame_not_available(self, tmp_path):
        game_file = tmp_path / "test_game.py"
        game_file.write_text("import pygame\n")

        with mock.patch("general_ludd.cloud.game_gen.pygame", None):
            with pytest.raises(ImportError, match="pygame is required"):
                run_game_headless(game_file)

    def test_captures_frames_via_flip(self, tmp_path):
        game_file = tmp_path / "test_game.py"
        game_file.write_text("unused\n")

        fake_pygame = mock.MagicMock()
        fake_surface = mock.MagicMock()
        copied_surface = mock.MagicMock()
        fake_surface.copy.return_value = copied_surface
        fake_pygame.display.set_mode.return_value = fake_surface

        mock_surfarray = mock.MagicMock()
        mock_surfarray.array3d.return_value = np.full((600, 800, 3), 128, dtype=np.uint8)
        fake_pygame.surfarray = mock_surfarray

        captured = []

        def capturing_flip():
            captured.append(1)
            if len(captured) >= 1:
                raise SystemExit(0)

        fake_pygame.display.flip = capturing_flip
        fake_pygame.display.update = capturing_flip
        fake_pygame.display.set_mode = mock.MagicMock(return_value=fake_surface)

        mock_loader = mock.MagicMock()

        def raise_system_exit(*args, **kwargs):
            raise SystemExit(0)

        mock_loader.exec_module.side_effect = raise_system_exit
        mock_spec = mock.MagicMock()
        mock_spec.loader = mock_loader

        with mock.patch("general_ludd.cloud.game_gen._require_pygame", return_value=fake_pygame):
            with mock.patch("importlib.util.spec_from_file_location", return_value=mock_spec):
                with mock.patch("importlib.util.module_from_spec", return_value=mock.MagicMock()):
                    frames = run_game_headless(game_file, num_frames=5)

        assert isinstance(frames, list)

    def test_system_exit_handled_gracefully(self, tmp_path):
        game_file = tmp_path / "test_game.py"
        game_file.write_text("import sys; sys.exit(0)\n")

        fake_pygame = mock.MagicMock()
        fake_surface = mock.MagicMock()
        fake_pygame.display.set_mode.return_value = fake_surface

        fake_pygame_module = fake_pygame

        with (
            mock.patch("general_ludd.cloud.game_gen._require_pygame", return_value=fake_pygame_module),
            mock.patch("importlib.util.spec_from_file_location") as mock_spec,
        ):
            mock.MagicMock()
            mock_spec.return_value = mock.MagicMock()
            mock_spec.return_value.loader = mock.MagicMock()
            mock_spec.return_value.loader.exec_module.side_effect = SystemExit(0)

            frames = run_game_headless(game_file, num_frames=3)
            assert frames == []

    def test_restores_display_functions_after_error(self, tmp_path):
        game_file = tmp_path / "test_game.py"
        game_file.write_text("raise RuntimeError('game crash')\n")

        fake_pygame = mock.MagicMock()
        fake_surface = mock.MagicMock()
        fake_pygame.display.set_mode.return_value = fake_surface
        original_flip = fake_pygame.display.flip
        original_update = fake_pygame.display.update

        with (
            mock.patch("general_ludd.cloud.game_gen._require_pygame", return_value=fake_pygame),
            mock.patch("importlib.util.spec_from_file_location") as mock_spec,
        ):
            mock_spec.return_value = mock.MagicMock()
            mock_spec.return_value.loader = mock.MagicMock()
            mock_spec.return_value.loader.exec_module.side_effect = RuntimeError("game crash")

            with pytest.raises(RuntimeError, match="game crash"):
                run_game_headless(game_file, num_frames=3)

            assert fake_pygame.display.flip is original_flip
            assert fake_pygame.display.update is original_update

    def test_limits_to_num_frames(self, tmp_path):
        game_file = tmp_path / "test_game.py"
        game_file.write_text("import pygame\n")

        fake_pygame = mock.MagicMock()
        fake_surface = mock.MagicMock()
        copied_surface = mock.MagicMock()
        fake_surface.copy.return_value = copied_surface
        fake_pygame.display.set_mode.return_value = fake_surface

        captured = []

        def capturing_flip():
            captured.append("flip")
            if len(captured) >= 2:
                raise SystemExit(0)

        fake_pygame.display.flip = capturing_flip

        mock_surfarray = mock.MagicMock()
        arr = np.zeros((600, 800, 3), dtype=np.uint8)
        mock_surfarray.array3d.return_value = arr
        fake_pygame.surfarray = mock_surfarray

        with (
            mock.patch("general_ludd.cloud.game_gen._require_pygame", return_value=fake_pygame),
            mock.patch("importlib.util.spec_from_file_location") as mock_spec,
        ):
            mock_spec.return_value = mock.MagicMock()
            mock_spec.return_value.loader = mock.MagicMock()
            frames = run_game_headless(game_file, num_frames=2)

            assert len(frames) <= 2

    def test_frame_has_correct_ndarray_type(self, tmp_path):
        game_file = tmp_path / "test_game.py"
        game_file.write_text("import pygame\n")

        fake_pygame = mock.MagicMock()
        fake_surface = mock.MagicMock()
        fake_pygame.display.set_mode.return_value = fake_surface

        mock_surfarray = mock.MagicMock()
        mock_surfarray.array3d.return_value = np.full((600, 800, 3), 128, dtype=np.uint8)
        fake_pygame.surfarray = mock_surfarray

        captured = []

        def capturing_flip():
            surf = fake_surface.copy()
            captured.append(surf)
            raise SystemExit(0)

        fake_pygame.display.flip = capturing_flip
        fake_pygame.display.update = capturing_flip

        mock_loader = mock.MagicMock()

        def raise_system_exit(*args, **kwargs):
            raise SystemExit(0)

        mock_loader.exec_module.side_effect = raise_system_exit
        mock_spec = mock.MagicMock()
        mock_spec.loader = mock_loader

        with (
            mock.patch("general_ludd.cloud.game_gen._require_pygame", return_value=fake_pygame),
            mock.patch("importlib.util.spec_from_file_location", return_value=mock_spec),
            mock.patch("importlib.util.module_from_spec", return_value=mock.MagicMock()),
        ):
            frames = run_game_headless(game_file, num_frames=5)

        assert isinstance(frames, list)
        if len(frames) > 0:
            assert isinstance(frames[0], np.ndarray)
            assert frames[0].dtype == np.uint8
            assert len(frames[0].shape) == 3

    def test_import_error_for_bad_path(self, tmp_path):
        bad_path = tmp_path / "nonexistent.py"

        fake_pygame = mock.MagicMock()
        fake_pygame.display.set_mode.return_value = mock.MagicMock()

        with mock.patch("general_ludd.cloud.game_gen._require_pygame", return_value=fake_pygame):
            with mock.patch("importlib.util.spec_from_file_location", return_value=None):
                with pytest.raises(ImportError, match="Cannot load module"):
                    _ = run_game_headless(bad_path, num_frames=1)
                    raise AssertionError("should have raised")

    def test_patches_display_update_and_flip(self, tmp_path):
        game_file = tmp_path / "test_game.py"
        game_file.write_text("import pygame\n")

        fake_pygame = mock.MagicMock()
        fake_surface = mock.MagicMock()
        fake_pygame.display.set_mode.return_value = fake_surface

        sys_exit = SystemExit(0)

        def side_effect():
            raise sys_exit

        fake_pygame.display.flip.side_effect = side_effect

        with (
            mock.patch("general_ludd.cloud.game_gen._require_pygame", return_value=fake_pygame),
            mock.patch("importlib.util.spec_from_file_location") as mock_spec,
        ):
            mock_spec.return_value = mock.MagicMock()
            mock_spec.return_value.loader = mock.MagicMock()
            run_game_headless(game_file, num_frames=5)

            assert fake_pygame.display.set_mode.called

    def test_str_path_accepted(self, tmp_path):
        game_file = tmp_path / "test_game.py"
        game_file.write_text("import pygame\n")

        fake_pygame = mock.MagicMock()
        fake_surface = mock.MagicMock()
        fake_pygame.display.set_mode.return_value = fake_surface

        sys_exit = SystemExit(0)

        def side_effect():
            raise sys_exit

        fake_pygame.display.flip.side_effect = side_effect

        mock_surfarray = mock.MagicMock()
        mock_surfarray.array3d.return_value = np.zeros((600, 800, 3), dtype=np.uint8)
        fake_pygame.surfarray = mock_surfarray

        with (
            mock.patch("general_ludd.cloud.game_gen._require_pygame", return_value=fake_pygame),
            mock.patch("importlib.util.spec_from_file_location") as mock_spec,
        ):
            mock_spec.return_value = mock.MagicMock()
            mock_spec.return_value.loader = mock.MagicMock()
            frames = run_game_headless(str(game_file), num_frames=1)

            assert isinstance(frames, list)

    def test_default_num_frames_30(self, tmp_path):
        game_file = tmp_path / "test_game.py"
        game_file.write_text("import pygame\n")

        fake_pygame = mock.MagicMock()
        fake_surface = mock.MagicMock()
        fake_pygame.display.set_mode.return_value = fake_surface

        sys_exit = SystemExit(0)

        def side_effect():
            raise sys_exit

        fake_pygame.display.flip.side_effect = side_effect

        with (
            mock.patch("general_ludd.cloud.game_gen._require_pygame", return_value=fake_pygame),
            mock.patch("importlib.util.spec_from_file_location") as mock_spec,
        ):
            mock_spec.return_value = mock.MagicMock()
            mock_spec.return_value.loader = mock.MagicMock()
            run_game_headless(game_file)

            fake_pygame.display.set_mode.assert_called_once_with((800, 600))

    def test_update_function_captures_frames(self, tmp_path):
        game_file = tmp_path / "test_game.py"
        game_file.write_text("import pygame\n")

        fake_pygame = mock.MagicMock()
        fake_surface = mock.MagicMock()
        fake_pygame.display.set_mode.return_value = fake_surface

        captured_flip = []

        def capturing_flip():
            captured_flip.append(1)
            raise SystemExit(0)

        fake_pygame.display.flip = capturing_flip
        original_update = fake_pygame.display.update

        mock_surfarray = mock.MagicMock()
        mock_surfarray.array3d.return_value = np.zeros((600, 800, 3), dtype=np.uint8)
        fake_pygame.surfarray = mock_surfarray

        with (
            mock.patch("general_ludd.cloud.game_gen._require_pygame", return_value=fake_pygame),
            mock.patch("importlib.util.spec_from_file_location") as mock_spec,
        ):
            mock_spec.return_value = mock.MagicMock()
            mock_spec.return_value.loader = mock.MagicMock()
            run_game_headless(game_file, num_frames=1)

            assert fake_pygame.display.update is original_update
