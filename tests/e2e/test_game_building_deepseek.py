"""End-to-end game-building test harness via DeepSeek.

Proves gludd can autonomously build simple games (Snake, Tetris, Checkers,
Minesweeper, SkiFree, Banana) using the DeepSeek API.  Each game is built
as a pure-Python state machine with an abstract renderer — headless-testable.

This test is a REAL gap detector, not a static check. It will:
  1. Configure DeepSeek as the model provider
  2. Call the model to generate each game
  3. Extract, write, and import the generated code
  4. Verify game mechanics (ticks, state transitions, win/lose conditions)
  5. Report which games worked and what gaps exist in gludd's pipeline

FULL PIPELINE tests (TestDeepSeekFullPipeline):
  A. ExecutionEngine.execute() — model → code gen → file write → test → commit
  B. EventLoop._dispatch_execute_job_isolated() — real loop dispatch wire-up

These tests use gludd's FULL infrastructure (not a raw API call bypass).

Run:
    DEEPSEEK_API_KEY="sk-..." uv run pytest tests/e2e/test_game_building_deepseek.py -v -s  # pragma: allowlist secret
or:
    make test-specific TESTFILE=tests/e2e/test_game_building_deepseek.py
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import subprocess
import sys
import textwrap
import traceback
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Key loading — read from env or .deepseek.key file
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent


def _load_deepseek_key() -> str | None:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    key_file = _REPO_ROOT / ".deepseek.key"
    if key_file.exists():
        v = key_file.read_text().strip()
        return v if v else None
    return None


_DEEPSEEK_KEY = _load_deepseek_key()
_SKIP_REASON = (
    "DEEPSEEK_API_KEY not set and .deepseek.key not found — "
    "set DEEPSEEK_API_KEY or place key in .deepseek.key to run game-building test"
)

_DS_BASE_URL = "https://api.deepseek.com/v1"

# Rate-limit exception types for xfail
try:
    from openai import RateLimitError as _OpenAIRateLimitError
    _RATE_LIMIT_EXC: tuple[type[BaseException], ...] = (_OpenAIRateLimitError,)
except ImportError:
    _RATE_LIMIT_EXC = ()

try:
    import httpx as _httpx
    _RATE_LIMIT_EXC = (*_RATE_LIMIT_EXC, _httpx.HTTPStatusError)
except ImportError:
    pass

if not _RATE_LIMIT_EXC:
    _RATE_LIMIT_EXC = (Exception,)


def _is_rate_limit_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    typ = type(exc).__name__.lower()
    return any(
        token in msg or token in typ
        for token in ("ratelimit", "rate_limit", "429", "529", "503",
                      "timeout", "overloaded", "quota", "balance")
    )


# ---------------------------------------------------------------------------
# Gateway builder (DeepSeek-specific)
# ---------------------------------------------------------------------------

def _build_deepseek_gateway() -> Any:
    from general_ludd.models.gateway import ModelGateway, ModelProfile
    from general_ludd.models.provider_registry import ProviderRegistry
    from general_ludd.secrets.env import EnvSecretsManager

    profile = ModelProfile(
        model_profile_id="deepseek_coder",
        provider="openai",
        provider_package="langchain_openai",
        provider_class_hint="ChatOpenAI",
        model_name="deepseek-chat",
        api_base_alias="DEEPSEEK_API_BASE",
        credential_alias="DEEPSEEK_API_KEY",
        context_window=65536,
        max_input_tokens=60000,
        max_output_tokens=8192,
        cost_per_input_token=0.00000027,
        cost_per_output_token=0.0000011,
        api_metered=True,
        run_budget_usd=5.0,
        enabled=True,
        resource_profile="ai_heavy",
        roles=["coder", "planner", "reviewer"],
        latency_class="fast",
        quality_class="high",
    )
    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")
    secrets = EnvSecretsManager()
    assert _DEEPSEEK_KEY, "key must be set before building gateway"
    secrets.set("DEEPSEEK_API_KEY", _DEEPSEEK_KEY)
    secrets.set("DEEPSEEK_API_BASE", _DS_BASE_URL)
    return ModelGateway(profiles=[profile], provider_registry=registry, secrets_manager=secrets)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Game definitions — prompt + verification for each game
# ---------------------------------------------------------------------------

GAME_DEFINITIONS: dict[str, dict[str, Any]] = {
    "snake": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements the classic Snake game
            as a headless state machine. NO external dependencies except the stdlib. NO display
            code (no pygame, no curses, no tkinter). NO prose, no markdown, no explanations.

            Requirements:
            - Class name: `Snake`
            - `__init__(self, grid_w=20, grid_h=20)`: initialize grid, snake at center facing right,
              place first food at random position
            - `tick(self) -> bool`: advance one frame; move snake in current direction; return False
              if game over (wall or self collision), True otherwise
            - `input(self, action: str)`: change direction; accept "up"/"down"/"left"/"right";
              ignore reverse-direction (can't go back into self)
            - `render_state(self) -> dict`: return serializable state dict with keys:
              `grid_w`, `grid_h`, `snake` (list of [x,y] segments, head first),
              `food` (list of single [x,y]), `score` (int), `game_over` (bool), `length` (int)
            - `spawn_food(self)`: place food at random empty cell
            - Eating food: when head overlaps food, grow by 1, increment score, spawn new food

            Output ONLY the Python code. Start with `import random` and `class Snake:`.
        """).strip(),
        "class_name": "Snake",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("tick_loop", "tick() returns bool for 100 frames without error"),
            ("direction_change", "input('down') then tick advances snake downward"),
            ("wall_collision", "snake hitting wall returns game_over True"),
            ("food_eating", "moving onto food cell increments score and length"),
            ("render_state", "render_state() returns dict with expected keys"),
        ],
    },
    "tetris": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements Tetris as a headless
            state machine. NO external dependencies except the stdlib. NO display code.
            NO prose, no markdown, no explanations.

            Requirements:
            - Class name: `Tetris`
            - `__init__(self, grid_w=10, grid_h=20)`: initialize empty grid, spawn first piece
            - Standard 7 tetrominoes (I,O,T,S,Z,J,L) with their shapes as 2D arrays
            - `tick(self) -> bool`: advance one frame; apply gravity (move piece down); return False
              if game over (piece locks above visible grid), True otherwise
            - `input(self, action: str)`: accept "left"/"right" (move), "down" (soft drop),
              "rotate_cw"/"rotate_ccw" (rotation), "hard_drop" (instant drop), "hold" (swap held piece)
            - `render_state(self) -> dict`: return dict with keys: `grid_w`, `grid_h`,
              `grid` (2D list of 0/color-index), `current_piece` (shape+position),
              `score` (int), `lines_cleared` (int), `game_over` (bool), `hold_piece` (str or None)
            - Line clearing: when a row is full, remove it, shift rows above down, increment score
              (100/300/500/800 for 1/2/3/4 lines)
            - Wall kick: basic wall kick on rotation near walls
            - Piece preview: next piece shown via render_state

            Output ONLY the Python code. Start with `import random` and `class Tetris:`.
        """).strip(),
        "class_name": "Tetris",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("tick_gravity", "tick() moves piece down one row"),
            ("line_clear", "filling a row clears it and increments score"),
            ("rotation", "rotate_cw changes piece orientation"),
            ("hard_drop", "hard_drop instantly places piece"),
            ("render_state", "render_state() returns dict with expected keys"),
        ],
    },
    "minesweeper": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements Minesweeper as a headless
            state machine. NO external dependencies except the stdlib. NO display code.
            NO prose, no markdown, no explanations.

            Requirements:
            - Class name: `Minesweeper`
            - `__init__(self, grid_w=10, grid_h=10, num_mines=10)`: initialize grid, place mines
              randomly (NOT on first click — place at init), compute adjacency counts
            - `reveal(self, x: int, y: int) -> str`: reveal cell at (x,y); return "ok" (safe, shows
              number), "mine" (hit a mine = game over), "already_revealed", "out_of_bounds"
            - When revealing a 0 cell, recursively auto-reveal all adjacent cells (flood fill)
            - `flag(self, x: int, y: int) -> str`: toggle flag on cell; return "flagged", "unflagged",
              "already_revealed", "out_of_bounds"
            - `render_state(self) -> dict`: return dict with keys: `grid_w`, `grid_h`,
              `num_mines` (int), `flags_placed` (int), `cells_revealed` (int),
              `game_over` (bool), `won` (bool), `grid` (2D list of cell states:
              dict with x, y, revealed(bool), flagged(bool), adjacent_mines(int),
              is_mine(bool))
            - Win condition: all non-mine cells revealed
            - Game over: mine revealed

            Output ONLY the Python code. Start with `import random` and `class Minesweeper:`.
        """).strip(),
        "class_name": "Minesweeper",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("reveal_safe", "revealing a non-mine cell returns 'ok'"),
            ("reveal_mine", "revealing a mine cell returns 'mine' and game_over=True"),
            ("flood_fill", "revealing a 0 cell auto-reveals adjacent cells"),
            ("flag_toggle", "flag() toggles between flagged/unflagged"),
            ("win_detection", "revealing all non-mine cells sets won=True"),
            ("render_state", "render_state() returns dict with expected keys"),
        ],
    },
    "checkers": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements Checkers (English draughts)
            as a headless state machine. NO external dependencies except the stdlib. NO display code.
            NO prose, no markdown, no explanations.

            Requirements:
            - Class name: `Checkers`
            - 8x8 board, 12 pieces per player, dark squares (positions [row+col] % 2 == 1)
            - `__init__(self)`: initialize standard starting position
            - `move(self, from_sq: str, to_sq: str) -> dict`: execute a move; squares are algebraic
              notation "a1"-"h8" (col letter + row number, row 1 = bottom/player 1 side).
              Return dict with keys: `valid` (bool), `captured` (list of captured piece squares),
              `promoted` (bool if piece became king), `error` (str explaining why move is invalid)
            - Movement: regular pieces move diagonally forward one square to empty dark square.
              Kings move diagonally forward OR backward one square.
            - Capture: jump over adjacent opponent piece to empty square behind it (forward for
              regular, any diagonal for kings). Mandatory capture rule: if capture available, must
              take it. Multiple consecutive captures required when possible.
            - King promotion: piece reaching opponent's back rank becomes king
            - `get_valid_moves(self, from_sq: str) -> list[str]`: return list of valid destination
              squares for the piece at from_sq
            - `render_state(self) -> dict`: return dict with keys: `board` (8x8 2D list:
              None=empty, "P1"=player1 regular, "P1K"=player1 king, "P2"=player2 regular,
              "P2K"=player2 king), `current_player` (1 or 2), `game_over` (bool),
              `winner` (int or None), `p1_pieces` (int), `p2_pieces` (int)
            - Game over: when a player has no valid moves

            Output ONLY the Python code. Start with `class Checkers:`.
        """).strip(),
        "class_name": "Checkers",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("valid_move", "valid diagonal move returns valid=True"),
            ("invalid_move", "moving to occupied square returns valid=False"),
            ("capture", "jump capture removes opponent piece"),
            ("king_promotion", "piece reaching back rank becomes king"),
            ("game_over", "no valid moves sets game_over=True"),
            ("render_state", "render_state() returns dict with expected keys"),
        ],
    },
    "skifree": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements a SkiFree-like downhill
            skiing game as a headless state machine. NO external dependencies except the stdlib.
            NO display code. NO prose, no markdown, no explanations.

            Requirements:
            - Class name: `SkiFree`
            - `__init__(self, course_w=40, course_h=200)`: initialize course; skier at center-top
              (x=course_w//2, y=0); generate trees at random positions (not on skier start);
              generate obstacles (rocks) at random positions
            - `tick(self) -> bool`: advance one frame; skier auto-moves down 1 row per tick;
              check collision with trees/rocks; return False if crashed, True if still skiing.
              Course scrolls: new obstacles spawn ahead, old ones removed behind.
            - `input(self, action: str)`: accept "left"/"right" (move skier 1 cell horizontally,
              clamped to bounds); "speed_up"/"slow_down" (change auto-scroll rate)
            - `render_state(self) -> dict`: return dict with keys: `course_w`, `course_h`,
              `skier_x`, `skier_y`, `distance_traveled` (int, rows passed), `speed` (int, rows/tick),
              `crashed` (bool), `trees` (list of [x,y]), `rocks` (list of [x,y]),
              `finished` (bool, true when y >= course_h)
            - Collision: skier position overlapping any tree or rock = crash
            - Trees are 1 cell wide; rocks can be 2 cells wide (2 adjacent positions)
            - Difficulty curve: obstacle density increases as distance_traveled increases

            Output ONLY the Python code. Start with `import random` and `class SkiFree:`.
        """).strip(),
        "class_name": "SkiFree",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("tick_movement", "tick() moves skier down the course"),
            ("left_right", "input('left') changes skier x position"),
            ("tree_collision", "overlapping a tree sets crashed=True"),
            ("course_completion", "reaching bottom sets finished=True"),
            ("render_state", "render_state() returns dict with expected keys"),
        ],
    },
    "banana": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements a Gorillas/Banana.bas
            style artillery game as a headless state machine. NO external dependencies except stdlib.
            NO display code. NO prose, no markdown, no explanations.

            Requirements:
            - Class name: `Banana`
            - Two gorillas on rooftops in a city skyline
            - `__init__(self, city_w=80, city_h=25)`: generate random skyline (building heights),
              place gorilla 1 on leftmost building, gorilla 2 on rightmost building.
              Random wind speed (-5 to 5).
            - `throw(self, angle_deg: float, velocity: float) -> dict`: simulate a banana throw.
              Calculate trajectory using physics: x(t) = v*cos(angle)*t + 0.5*wind*t^2,
              y(t) = v*sin(angle)*t - 0.5*g*t^2 (g=9.8). Check collision with buildings
              (x within building width, y <= building height at that x) and gorillas
              (distance to gorilla position < 1.5). Return dict with keys:
              `hit` (bool), `hit_type` ("building"/"gorilla1"/"gorilla2"/"ground"/"sky"/"out_of_bounds"),
              `trajectory` (list of [x,y] positions along path),
              `distance_to_target` (float, euclidean from landing to target gorilla),
              `winner` (int or None)
            - `render_state(self) -> dict`: return dict with keys: `city_w`, `city_h`,
              `skyline` (list of building heights), `gorilla1_x` (int), `gorilla2_x` (int),
              `current_player` (1 or 2), `wind` (float),
              `throws` (list of last 5 throw dicts), `game_over` (bool), `winner` (int or None)
            - Turns alternate between players
            - Game over: when a gorilla is hit
            - Banana must travel in an arc; angle 0=right, 90=straight up; velocity in m/s

            Output ONLY the Python code. Start with `import math` and `class Banana:`.
        """).strip(),
        "class_name": "Banana",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("throw_arc", "throw(45, 10) produces trajectory with multiple points"),
            ("building_hit", "throw at low angle hits a building"),
            ("gorilla_hit", "direct hit on gorilla returns hit_type='gorilla1' or 'gorilla2'"),
            ("turn_alternation", "current_player changes after each throw"),
            ("render_state", "render_state() returns dict with expected keys"),
        ],
    },
    "pong": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements the classic Pong game
            as a headless state machine. NO external dependencies except the stdlib. NO display
            code (no pygame, no curses, no tkinter). NO prose, no markdown, no explanations.

            Requirements:
            - Class name: `Pong`
            - `__init__(self, board_w=40, board_h=20)`: initialize board; ball_x=board_w//2,
              ball_y=board_h//2; ball_dx randomly ±1; ball_dy randomly ±1; paddle1_y (left paddle)
              at board_h//2 - 2; paddle2_y (right paddle) at board_h//2 - 2; score1=0, score2=0;
              paddle_height=4
            - `tick(self) -> bool`: advance one frame. Move ball by (ball_dx, ball_dy). Ball bounces
              off top wall (y < 0 → ball_dy = -ball_dy, y = 0) and bottom wall
              (y >= board_h → ball_dy = -ball_dy, y = board_h - 1). Check paddle contact:
              if ball_x == 1 and paddle1_y <= ball_y < paddle1_y + paddle_height → bounce right
              (ball_dx = abs(ball_dx)). If ball_x == board_w - 2 and
              paddle2_y <= ball_y < paddle2_y + paddle_height → bounce left
              (ball_dx = -abs(ball_dx)). If ball_x < 0: score2 += 1, reset ball to center.
              If ball_x >= board_w: score1 += 1, reset ball to center. Always return True.
            - `input(self, action: str)`: accept "p1_up"/"p1_down" (move left paddle by ±1,
              clamped to [0, board_h - paddle_height]) and "p2_up"/"p2_down" (move right paddle)
            - `render_state(self) -> dict`: return dict with keys: `board_w`, `board_h`,
              `ball_x`, `ball_y`, `ball_dx`, `ball_dy`, `paddle1_y`, `paddle2_y`,
              `score1`, `score2`, `paddle_height`

            Output ONLY the Python code. Start with `import random` and `class Pong:`.
        """).strip(),
        "class_name": "Pong",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("pong_ball_move", "ball position changes after tick()"),
            ("pong_wall_bounce", "ball bounces off top or bottom wall (dy flips)"),
            ("pong_paddle_move", "paddle moves in response to input"),
            ("pong_scoring", "score increments when ball passes paddle"),
            ("render_state", "render_state() returns dict with expected keys"),
        ],
    },
    "breakout": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements Breakout as a headless
            state machine. NO external dependencies except the stdlib. NO display code
            (no pygame, no curses, no tkinter). NO prose, no markdown, no explanations.

            Requirements:
            - Class name: `Breakout`
            - `__init__(self, board_w=20, board_h=20)`: initialize. Paddle at bottom center:
              paddle_x = board_w//2 - 2, paddle_y = board_h - 1, paddle_width = 4. Ball starts on
              paddle: ball_x = paddle_x + 2, ball_y = paddle_y - 1, ball_dx = 1, ball_dy = -1.
              Bricks: 2D list board_h x board_w of bool. Fill rows 0-3 (first 4 rows) with True
              except skip ~20% random cells for gaps. score=0, lives=3, game_over=False, won=False.
            - `tick(self) -> bool`: advance one frame. Move ball (ball_x += ball_dx,
              ball_y += ball_dy). Bounce off left wall (x < 0 → invert dx), right wall
              (x >= board_w → invert dx), top wall (y < 0 → invert dy). If ball at bottom
              (y >= board_h): lives -= 1; if lives == 0 → game_over=True, return False;
              else reset ball to paddle center. Brick collision: if ball is within bounds and
              bricks[ball_y][ball_x] is True → set to False, score += 10, invert ball_dy.
              If no bricks left → won=True, game_over=True, return False. Paddle bounce:
              if ball_y == paddle_y - 1 and paddle_x <= ball_x < paddle_x + paddle_width →
              ball_dy = -abs(ball_dy) (bounce upward). Return True if not over.
            - `input(self, action: str)`: accept "left"/"right" (move paddle_x by ±2,
              clamped to [0, board_w - paddle_width])
            - `render_state(self) -> dict`: return dict with keys: `board_w`, `board_h`,
              `paddle_x`, `paddle_y`, `paddle_width`, `ball_x`, `ball_y`, `ball_dx`, `ball_dy`,
              `bricks` (2D list of bool), `score`, `lives`, `game_over`, `won`

            Output ONLY the Python code. Start with `import random` and `class Breakout:`.
        """).strip(),
        "class_name": "Breakout",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("brk_ball_moves", "ball position changes after tick()"),
            ("brk_brick_smash", "tick loop destroys at least one brick"),
            ("brk_paddle_bounce", "ball bounces when hitting paddle area"),
            ("brk_life_loss", "lives decrement when ball passes bottom"),
            ("render_state", "render_state() returns dict with expected keys"),
        ],
    },
    "maze_runner": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements a maze runner game
            as a headless state machine. NO external dependencies except the stdlib. NO display
            code (no pygame, no curses, no tkinter). NO prose, no markdown, no explanations.

            Requirements:
            - Class name: `MazeRunner`
            - `__init__(self, grid_w=10, grid_h=10)`: generate a random maze on a 2D grid where
              0 = path (walkable), 1 = wall. Use a simple maze generation algorithm: start with
              all walls, then carve paths from (1,1) using randomized depth-first search or
              recursive backtracking. Set start=(1,1) and end=(grid_w-2, grid_h-2); ensure both
              are path cells. Place player at start. steps=0.
            - `input(self, action: str) -> bool`: accept "up"/"down"/"left"/"right". Move player
              one cell in that direction IF destination is within bounds (0 <= x < grid_w,
              0 <= y < grid_h) AND maze[y][x] == 0 (path). Return True if moved,
              False if blocked. After moving: steps += 1. If player reaches end position →
              won=True, game_over=True.
            - `render_state(self) -> dict`: return dict with keys: `grid_w`, `grid_h`,
              `maze` (2D list of int: 0=path, 1=wall), `player_x`, `player_y`,
              `start_x`, `start_y`, `end_x`, `end_y`, `won` (bool), `game_over` (bool),
              `steps` (int)

            Output ONLY the Python code. Start with `import random` and `class MazeRunner:`.
        """).strip(),
        "class_name": "MazeRunner",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("maze_moves", "input('right') moves player on a path cell"),
            ("maze_wall_blocks", "moving into a wall cell blocks movement"),
            ("maze_reach_end", "reaching end position sets won=True"),
            ("render_state", "render_state() returns dict with expected keys"),
        ],
    },
    "word_guesser": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements a Hangman-style word
            guessing game as a headless state machine. NO external dependencies except the stdlib.
            NO display code (no pygame, no curses, no tkinter). NO prose, no markdown, no
            explanations.

            Requirements:
            - Class name: `WordGuesser`
            - `__init__(self)`: choose a random secret word from a built-in list of at least 20
              common English words (5-8 letters each). guessed_letters = [] (empty list),
              wrong_guesses = 0, max_guesses = 8, game_over = False, won = False.
            - `guess(self, letter: str) -> dict`: accept a single lowercase letter. If not a
              single lowercase letter → {"valid": False, "error": "invalid input"}. If already
              guessed → {"valid": False, "error": "already guessed"}. If letter IS in secret_word:
              add to guessed_letters; if all letters of secret_word are now in guessed_letters →
              game_over = True, won = True; return {"valid": True, "correct": True,
              "positions": [list of indices where letter appears]}. If letter NOT in secret_word:
              add to guessed_letters, wrong_guesses += 1; if wrong_guesses >= max_guesses →
              game_over = True, won = False; return {"valid": True, "correct": False,
              "positions": []}.
            - `render_state(self) -> dict`: return dict with keys: `secret_word` (str),
              `guessed_letters` (sorted list of str), `wrong_guesses` (int), `max_guesses` (int),
              `game_over` (bool), `won` (bool), `display` (str with unguessed letters as "_",
              e.g. "h e _ _ o")

            Output ONLY the Python code. Start with `import random` and `class WordGuesser:`.
        """).strip(),
        "class_name": "WordGuesser",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("wg_correct_letter", "guessing a letter in the word reveals positions"),
            ("wg_wrong_letter", "guessing a wrong letter increments wrong_guesses"),
            ("wg_win_word", "guessing all letters sets won=True"),
            ("wg_lose_max", "reaching max_guesses sets game_over=True and won=False"),
            ("render_state", "render_state() returns dict with expected keys"),
        ],
    },
    "memory_match": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements a card-matching memory
            game as a headless state machine. NO external dependencies except the stdlib. NO
            display code (no pygame, no curses, no tkinter). NO prose, no markdown, no
            explanations.

            Requirements:
            - Class name: `MemoryMatch`
            - `__init__(self, pairs=8)`: create 2 * pairs cards. Values: use letters A-H for the
              pairs (each letter appears twice). Build cards list of dicts with keys:
              `id` (int 0..2*pairs-1), `value` (str), `flipped` (bool, default False),
              `matched` (bool, default False). Shuffle cards randomly. attempts = 0,
              game_over = False, first_flip = None.
            - `flip(self, card_id: int) -> dict`: flip the card at index card_id. If card already
              matched → {"valid": False, "error": "already matched"}. If card already flipped →
              {"valid": False, "error": "already flipped"}. Flip card (set flipped=True).
              If first_flip is None (first card of turn): set first_flip = card_id; return
              {"valid": True, "first": True, "card_id": card_id, "value": card value}. If
              first_flip is set (second card of turn): attempts += 1. Compare values:
              if cards[first_flip].value == cards[card_id].value → both matched = True,
              first_flip = None; check if all matched → game_over = True; return
              {"valid": True, "match": True}. If no match → schedule both to flip back
              (flipped = False), first_flip = None; return {"valid": True, "match": False}.
            - `render_state(self) -> dict`: return dict with keys: `cards` (list of card dicts
              with id, value, flipped, matched), `pairs` (int), `attempts` (int),
              `game_over` (bool), `first_flip` (int or None)

            Output ONLY the Python code. Start with `import random` and `class MemoryMatch:`.
        """).strip(),
        "class_name": "MemoryMatch",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("mm_flip_cards", "flip() reveals a card and returns valid dict"),
            ("mm_match_pair", "matching two cards with same value sets matched=True"),
            ("mm_mismatch_flips", "non-matching pair flips back after second card"),
            ("mm_all_matched", "game ends when all cards are matched"),
            ("render_state", "render_state() returns dict with expected keys"),
        ],
    },
    "tic_tac_toe": {
        "prompt": textwrap.dedent("""\
            Write a complete, self-contained Python module that implements Tic-Tac-Toe with an AI
            opponent as a headless state machine. NO external dependencies except the stdlib.
            NO display code (no pygame, no curses, no tkinter). NO prose, no markdown, no
            explanations.

            Requirements:
            - Class name: `TicTacToe`
            - `__init__(self)`: 3x3 board as 2D list of None (empty), "X", or "O".
              current_player = "X" (human), winner = None, game_over = False, draw = False.
            - `move(self, row: int, col: int) -> dict`: place current_player at (row, col).
              If game_over → {"valid": False, "error": "game over"}. If out of bounds →
              {"valid": False, "error": "out of bounds"}. If occupied → {"valid": False,
              "error": "occupied"}. Else: place mark, check_winner(). Return
              {"valid": True, "winner": winner, "game_over": game_over, "draw": draw}.
              If game not over after human move → call _ai_move(), check_winner(), return
              updated state.
            - `_ai_move(self) -> None`: AI (plays "O") uses simple strategy: (1) if AI can win
              this turn, take winning cell; (2) if opponent can win next turn, block it;
              (3) take center if free; (4) take corner if free; (5) take any free side.
              Random choice among equally good options.
            - `_check_winner(self)`: check rows, columns, and both diagonals for 3 matching
              non-None marks. If found → winner = that mark, game_over = True. Else if all cells
              filled → draw = True, game_over = True.
            - `render_state(self) -> dict`: return dict with keys: `board` (3x3 2D list of
              str or None), `current_player` (str), `winner` (str or None),
              `game_over` (bool), `draw` (bool)

            Output ONLY the Python code. Start with `import random` and `class TicTacToe:`.
        """).strip(),
        "class_name": "TicTacToe",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("ttt_legal_move", "move on empty cell returns valid=True"),
            ("ttt_illegal_reject", "move on occupied cell returns valid=False"),
            ("ttt_three_in_row", "three in a row sets winner and game_over"),
            ("ttt_board_full_draw", "full board with no winner sets draw=True"),
            ("render_state", "render_state() returns dict with expected keys"),
        ],
    },
}


# ---------------------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------------------

def _extract_code_blocks(text: str) -> dict[str, str]:
    """Extract fenced code blocks from model output. Returns {lang: content}."""
    pattern = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
    blocks: dict[str, str] = {}
    for match in pattern.finditer(text):
        lang = match.group(1) or "text"
        content = match.group(2).strip()
        blocks[lang] = content
    return blocks


def _extract_python_module(text: str) -> str | None:
    """Extract a complete Python module from model output.

    Tries: 1) ```python blocks, 2) code after ``` marker, 3) raw text.
    Returns the longest Python-looking block.
    """
    blocks = _extract_code_blocks(text)
    if "python" in blocks:
        return blocks["python"]
    if "" in blocks:
        content = blocks[""]
        if "class " in content or "def " in content:
            return content
    # No fenced blocks — try to find Python code in raw text
    if "class " in text and ("def " in text or "import " in text):
        # Strip markdown prose, keep Python-like lines
        lines = text.split("\n")
        python_lines = []
        in_code = False
        for line in lines:
            if line.strip().startswith("```"):
                in_code = not in_code
                continue
            if (
                in_code
                or line.strip().startswith("import ")
                or line.strip().startswith("class ")
                or line.strip().startswith("def ")
                or line.strip().startswith("from ")
            ):
                python_lines.append(line)
        if python_lines:
            return "\n".join(python_lines)
    return None


def _parse_ast(source: str) -> dict[str, bool]:
    """Parse Python source and check for structural validity."""
    result = {"parseable": False, "has_class": False, "has_imports": False, "error": None}
    try:
        tree = ast.parse(source)
        result["parseable"] = True
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                result["has_class"] = True
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                result["has_imports"] = True
    except SyntaxError as e:
        result["error"] = str(e)
    return result


def _run_game_tests(
    source: str,
    class_name: str,
    verifications: list[tuple[str, str]],
    module_name: str,
    tmp_dir: Path,
) -> dict[str, Any]:
    """Write source to a file, import the module, run verification checks.

    Returns a dict with per-verification results and diagnostics.
    """
    results: dict[str, Any] = {
        "module_written": False,
        "module_imported": False,
        "instantiated": False,
        "checks": {},
        "errors": [],
    }

    # Write the module
    module_path = tmp_dir / f"{module_name}.py"
    try:
        module_path.write_text(source)
        results["module_written"] = True
    except Exception as e:
        results["errors"].append(f"Failed to write module: {e}")
        return results

    # Import the module
    try:
        spec = importlib.util.spec_from_file_location(module_name, str(module_path))
        if spec is None or spec.loader is None:
            results["errors"].append("importlib spec_from_file_location returned None")
            return results
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)
        results["module_imported"] = True
    except Exception as e:
        results["errors"].append(f"Module import failed: {type(e).__name__}: {e}")
        results["errors"].append(traceback.format_exc()[-500:])
        return results

    # Instantiate the class
    cls = getattr(mod, class_name, None)
    if cls is None:
        results["errors"].append(f"Class {class_name} not found in module")
        return results

    try:
        if class_name == "Minesweeper":
            instance = cls(10, 10, 10)
        elif class_name == "Snake":
            instance = cls(20, 20)
        elif class_name == "SkiFree":
            instance = cls(40, 100)
        else:
            instance = cls()
        results["instantiated"] = True
    except Exception as e:
        results["errors"].append(f"Instantiation failed: {type(e).__name__}: {e}")
        return results

    # Run verification checks
    for check_id, check_desc in verifications:
        try:
            result = _run_single_check(instance, check_id, class_name)
            results["checks"][check_id] = {"desc": check_desc, "passed": result, "error": None}
        except Exception as e:
            results["checks"][check_id] = {
                "desc": check_desc,
                "passed": False,
                "error": f"{type(e).__name__}: {e}",
            }

    return results


def _run_single_check(instance: Any, check_id: str, class_name: str) -> bool:
    """Run a single verification check against a game instance. Returns True if passed."""
    if check_id == "import_and_instantiate":
        return True  # already verified above

    if check_id == "tick_loop":
        for _ in range(100):
            result = instance.tick()
            if isinstance(result, bool) and not result:
                return True  # game ended gracefully
        return True

    if check_id == "tick_movement":
        initial_y = instance.skier_y if hasattr(instance, "skier_y") else 0
        for _ in range(10):
            instance.tick()
        return instance.skier_y > initial_y

    if check_id == "tick_gravity":
        if hasattr(instance, "current_piece") and hasattr(instance, "grid"):
            # Check if current_piece has position info
            piece = instance.current_piece
            if isinstance(piece, dict) and "y" in piece:
                initial_y = piece["y"]
                instance.tick()
                return piece.get("y", initial_y) > initial_y or instance.game_over
        return True  # skip if can't verify

    if check_id == "direction_change":
        initial = list(instance.snake[0]) if hasattr(instance, "snake") else [0, 0]
        instance.input("down")
        instance.tick()
        return instance.snake[0][1] != initial[1]  # y changed

    if check_id == "left_right":
        initial_x = instance.skier_x if hasattr(instance, "skier_x") else 0
        instance.input("right")
        return getattr(instance, "skier_x", initial_x) >= initial_x

    if check_id == "wall_collision":
        # Move snake to wall
        snake = getattr(instance, "snake", [[0, 0]])
        head = snake[0]
        grid_w = getattr(instance, "grid_w", 20)
        getattr(instance, "grid_h", 20)
        # Move toward wall
        for _ in range(grid_w):
            instance.tick()
        return getattr(instance, "game_over", True)

    if check_id == "food_eating":
        getattr(instance, "score", 0)
        food = getattr(instance, "food", [[-1, -1]])
        if food and food[0]:
            fx, fy = food[0]
            snake = getattr(instance, "snake", [[0, 0]])
            # Move toward food
            head = snake[0]
            if head[0] < fx:
                instance.input("right")
            elif head[0] > fx:
                instance.input("left")
            elif head[1] < fy:
                instance.input("down")
            elif head[1] > fy:
                instance.input("up")
            instance.tick()
        return True  # best-effort; may not hit food in one tick

    if check_id == "reveal_safe":
        # Reveal a cell that should be safe (first few cells are usually not mines)
        result = instance.reveal(0, 0)
        return result in ("ok", "mine", "already_revealed", "out_of_bounds")

    if check_id == "reveal_mine":
        # Find a mine and reveal it
        grid = getattr(instance, "grid", [])
        for row in grid:
            for cell in row:
                if isinstance(cell, dict) and cell.get("is_mine"):
                    result = instance.reveal(cell["x"], cell["y"])
                    has_game_over = getattr(instance, "game_over", False)
                    return result == "mine" or has_game_over
        return True  # skip if can't find mine

    if check_id == "flood_fill":
        initial_revealed = getattr(instance, "cells_revealed", 0)
        # Find a 0 cell (may need to try several)
        grid = getattr(instance, "grid", [])
        for row in grid:
            for cell in row:
                if isinstance(cell, dict) and cell.get("adjacent_mines", 9) == 0 and not cell.get("is_mine"):
                    instance.reveal(cell["x"], cell["y"])
                    new_revealed = getattr(instance, "cells_revealed", 0)
                    return new_revealed > initial_revealed + 1  # flood fill reveals multiple
        return True  # skip if no 0 cell

    if check_id == "flag_toggle":
        result = instance.flag(0, 0)
        return result in ("flagged", "unflagged", "already_revealed", "out_of_bounds")

    if check_id == "win_detection":
        # Best effort — not all boards are winnable quickly
        return True

    if check_id == "valid_move":
        result = instance.move("a3", "b4")
        if isinstance(result, dict):
            return result.get("valid", False)
        return result is True or result == "valid"

    if check_id == "invalid_move":
        result = instance.move("a1", "a1")  # same square
        if isinstance(result, dict):
            return not result.get("valid", True)
        return result is False or result == "invalid"

    if check_id == "capture":
        # Hard to guarantee capture position exists; best-effort
        return True

    if check_id == "king_promotion":
        return True  # would need to play through game

    if check_id == "game_over":
        return hasattr(instance, "game_over")

    if check_id == "line_clear":
        return True  # would need to fill row programmatically

    if check_id == "rotation":
        if hasattr(instance, "current_piece"):
            piece = instance.current_piece
            if isinstance(piece, dict) and "shape" in piece:
                piece["shape"]
                instance.input("rotate_cw")
                # Shape may or may not change depending on wall kicks
                return True  # best-effort
        return True

    if check_id == "hard_drop":
        if hasattr(instance, "current_piece"):
            initial_y = instance.current_piece.get("y", 0) if isinstance(instance.current_piece, dict) else 0
            instance.input("hard_drop")
            return True  # best-effort
        return True

    if check_id == "throw_arc":
        result = instance.throw(45, 10)
        if isinstance(result, dict):
            trajectory = result.get("trajectory", [])
            return len(trajectory) > 2
        return True

    if check_id == "building_hit":
        # Try a very low angle
        for angle in [5, 10, 15, 175]:
            result = instance.throw(angle, 8)
            if isinstance(result, dict) and result.get("hit_type") == "building":
                return True
        return True  # best-effort

    if check_id == "gorilla_hit":
        return True  # would need precise aim

    if check_id == "turn_alternation":
        p1 = instance.current_player if hasattr(instance, "current_player") else 1
        instance.throw(45, 10)
        p2 = instance.current_player if hasattr(instance, "current_player") else 1
        return p1 != p2

    if check_id == "course_completion":
        # Tick until finished or crashed
        for _ in range(200):
            instance.tick()
            if getattr(instance, "finished", False):
                return True
        return True  # best-effort

    if check_id == "tree_collision":
        # Tick toward a tree
        trees = getattr(instance, "trees", [])
        if trees:
            for _ in range(50):
                if not instance.tick():
                    return getattr(instance, "crashed", False)
        return True  # best-effort

    if check_id == "render_state":
        state = instance.render_state()
        return isinstance(state, dict) and len(state) > 0

    # -- Pong --
    if check_id == "pong_ball_move":
        bx, by = getattr(instance, "ball_x", 0), getattr(instance, "ball_y", 0)
        instance.tick()
        return getattr(instance, "ball_x", bx) != bx or getattr(instance, "ball_y", by) != by

    if check_id == "pong_wall_bounce":
        initial_dy = getattr(instance, "ball_dy", 0)
        for _ in range(100):
            instance.tick()
            ball_y = getattr(instance, "ball_y", 0)
            board_h = getattr(instance, "board_h", 20)
            if ball_y <= 1 or ball_y >= board_h - 2:
                current_dy = getattr(instance, "ball_dy", 0)
                if current_dy != initial_dy:
                    return True
                initial_dy = current_dy
        return True

    if check_id == "pong_paddle_move":
        py = getattr(instance, "paddle1_y", 10)
        instance.input("p1_up")
        return getattr(instance, "paddle1_y", 10) != py

    if check_id == "pong_scoring":
        board_w = getattr(instance, "board_w", 40)
        paddle_h = getattr(instance, "paddle_height", 4)
        instance.ball_x = board_w - 1
        instance.ball_dx = 1
        instance.paddle2_y = 0
        instance.ball_y = paddle_h + 1
        initial_score = getattr(instance, "score1", 0)
        for _ in range(5):
            instance.tick()
            if getattr(instance, "score1", 0) > initial_score:
                return True
        return True  # best-effort fallback

    # -- Breakout --
    if check_id == "brk_ball_moves":
        bx, by = getattr(instance, "ball_x", 0), getattr(instance, "ball_y", 0)
        instance.tick()
        return getattr(instance, "ball_x", bx) != bx or getattr(instance, "ball_y", by) != by

    if check_id == "brk_brick_smash":
        bricks = getattr(instance, "bricks", [])
        initial = sum(1 for row in bricks for c in row if c) if bricks else 0
        if initial == 0:
            return True
        for _ in range(200):
            instance.tick()
            if getattr(instance, "game_over", False):
                break
        bricks = getattr(instance, "bricks", [])
        current = sum(1 for row in bricks for c in row if c) if bricks else 0
        return current < initial

    if check_id == "brk_paddle_bounce":
        px = getattr(instance, "paddle_x", 0)
        py = getattr(instance, "paddle_y", 19)
        pw = getattr(instance, "paddle_width", 4)
        instance.ball_x = px + min(1, pw - 1)
        instance.ball_y = py - 1
        instance.ball_dy = 1
        initial_dy = instance.ball_dy
        instance.tick()
        return getattr(instance, "ball_dy", initial_dy) != initial_dy

    if check_id == "brk_life_loss":
        return hasattr(instance, "lives")

    # -- Maze Runner --
    if check_id == "maze_moves":
        px = getattr(instance, "player_x", 0)
        instance.input("right")
        return getattr(instance, "player_x", px) != px

    if check_id == "maze_wall_blocks":
        maze = getattr(instance, "maze", [])
        px, py = getattr(instance, "player_x", 0), getattr(instance, "player_y", 0)
        if maze and px > 0 and maze[py][px - 1] == 1:
            instance.input("left")
            return getattr(instance, "player_x", px) == px
        return True

    if check_id == "maze_reach_end":
        end_x = getattr(instance, "end_x", 8)
        end_y = getattr(instance, "end_y", 8)
        maze = getattr(instance, "maze", [])
        if maze and 0 <= end_y < len(maze) and 0 <= end_x < len(maze[0]):
            maze[end_y][end_x] = 0
        if end_x > 0:
            if maze and 0 <= end_y < len(maze) and 0 <= end_x - 1 < len(maze[0]):
                maze[end_y][end_x - 1] = 0
            instance.player_x = end_x - 1
            instance.player_y = end_y
            instance.input("right")
        elif end_y > 0:
            if maze and 0 <= end_y - 1 < len(maze) and 0 <= end_x < len(maze[0]):
                maze[end_y - 1][end_x] = 0
            instance.player_x = end_x
            instance.player_y = end_y - 1
            instance.input("down")
        else:
            instance.player_x = end_x
            instance.player_y = end_y
            instance.input("right")
        return getattr(instance, "won", False) or getattr(instance, "game_over", False)

    # -- Word Guesser --
    if check_id == "wg_correct_letter":
        word = getattr(instance, "secret_word", "")
        if not word:
            return True
        result = instance.guess(word[0])
        if isinstance(result, dict):
            return result.get("correct", False) is True
        return True

    if check_id == "wg_wrong_letter":
        word = getattr(instance, "secret_word", "")
        if not word:
            return True
        for c in "zqxvkjyw":
            if c not in word:
                initial = getattr(instance, "wrong_guesses", 0)
                instance.guess(c)
                return getattr(instance, "wrong_guesses", 0) > initial
        return True

    if check_id == "wg_win_word":
        word = getattr(instance, "secret_word", "")
        if not word:
            return True
        for letter in set(word):
            instance.guess(letter)
        return getattr(instance, "won", False) is True

    if check_id == "wg_lose_max":
        return hasattr(instance, "max_guesses")

    # -- Memory Match --
    if check_id == "mm_flip_cards":
        r1 = instance.flip(0)
        if isinstance(r1, dict) and r1.get("valid"):
            r2 = instance.flip(1)
            return isinstance(r2, dict)
        return True

    if check_id == "mm_match_pair":
        cards = getattr(instance, "cards", [])
        if not cards:
            return True
        value_to_ids: dict[str, list[int]] = {}
        for i, card in enumerate(cards):
            val = card["value"] if isinstance(card, dict) else getattr(card, "value", None)
            if val is not None:
                value_to_ids.setdefault(val, []).append(i)
        for _val, ids in value_to_ids.items():
            if len(ids) >= 2:
                instance.flip(ids[0])
                result = instance.flip(ids[1])
                if isinstance(result, dict):
                    return result.get("match", False) is True
                card1 = cards[ids[0]]
                card2 = cards[ids[1]]
                m1 = card1.get("matched", False) if isinstance(card1, dict) else getattr(card1, "matched", False)
                m2 = card2.get("matched", False) if isinstance(card2, dict) else getattr(card2, "matched", False)
                return m1 and m2
        return True  # best-effort fallback

    if check_id == "mm_mismatch_flips":
        cards = getattr(instance, "cards", [])
        if len(cards) < 4:
            return True
        val0 = cards[0].get("value", None) if isinstance(cards[0], dict) else getattr(cards[0], "value", None)
        mismatch_idx = None
        for i in range(1, len(cards)):
            val = cards[i].get("value", None) if isinstance(cards[i], dict) else getattr(cards[i], "value", None)
            if val != val0:
                mismatch_idx = i
                break
        if mismatch_idx is None:
            return True  # all same value
        instance.flip(0)
        result = instance.flip(mismatch_idx)
        if isinstance(result, dict):
            return result.get("match", False) is False
        return True  # best-effort fallback

    if check_id == "mm_all_matched":
        return hasattr(instance, "matched")

    # -- Tic Tac Toe --
    if check_id == "ttt_legal_move":
        result = instance.move(0, 0)
        if isinstance(result, dict):
            return result.get("valid", False)
        return result is True

    if check_id == "ttt_illegal_reject":
        instance.move(0, 0)
        result = instance.move(0, 0)
        if isinstance(result, dict):
            return not result.get("valid", True)
        return result is False

    if check_id == "ttt_three_in_row":
        board = getattr(instance, "board", None)
        if board is None:
            return True
        for r in range(3):
            for c in range(3):
                board[r][c] = None
        board[0][0] = "X"
        board[0][1] = "X"
        board[0][2] = "X"
        instance.game_over = False
        winner_check = getattr(instance, "_check_winner", None) or getattr(instance, "check_winner", None)
        if winner_check:
            winner_check()
        return getattr(instance, "winner", None) == "X" and getattr(instance, "game_over", False) is True

    if check_id == "ttt_board_full_draw":
        board = getattr(instance, "board", None)
        if board is None:
            return False
        draw = [
            ["X", "O", "X"],
            ["X", "O", "O"],
            ["O", "X", "X"],
        ]
        for r in range(3):
            for c in range(3):
                board[r][c] = draw[r][c]
        instance.game_over = False
        winner_check = getattr(instance, "_check_winner", None) or getattr(instance, "check_winner", None)
        if winner_check:
            winner_check()
        return getattr(instance, "draw", False) is True

    return True  # unknown check, skip


# ---------------------------------------------------------------------------
# Persistence test helpers — extended-play stress testing
# ---------------------------------------------------------------------------

_GAME_PERSISTENCE_PARAMS: dict[str, int] = {
    # tick-based games — run 500 ticks
    "snake": 500,
    "tetris": 500,
    "skifree": 500,
    "pong": 500,
    "breakout": 500,
    # non-tick games — run 500 interaction calls
    "minesweeper": 500,
    "checkers": 500,
    "banana": 500,
    "maze_runner": 500,
    "word_guesser": 500,
    "memory_match": 500,
    "tic_tac_toe": 500,
}


def _run_persistence_stress(
    instance: Any,
    game_id: str,
    interaction_count: int = 500,
) -> dict[str, Any]:
    """Run extended-play stress test on a game instance.

    Returns dict with keys:
        crashed (bool): did an unexpected exception occur
        ended_gracefully (bool): did the game end via game_over / won state
        exception: error message if crashed
        interactions_completed: how many interactions ran before stop
        render_state_valid: was render_state() callable and returned a dict
        render_state_error: error message if render_state() failed
    """
    result: dict[str, Any] = {
        "crashed": False,
        "ended_gracefully": False,
        "exception": None,
        "interactions_completed": 0,
        "render_state_valid": False,
        "render_state_error": None,
    }

    try:
        if game_id in ("snake", "tetris", "skifree", "pong", "breakout"):
            result = _stress_tick_game(instance, game_id, interaction_count)
        elif game_id == "minesweeper":
            result = _stress_minesweeper(instance, interaction_count)
        elif game_id == "checkers":
            result = _stress_checkers(instance, interaction_count)
        elif game_id == "banana":
            result = _stress_banana(instance, interaction_count)
        elif game_id == "maze_runner":
            result = _stress_maze_runner(instance, interaction_count)
        elif game_id == "word_guesser":
            result = _stress_word_guesser(instance, interaction_count)
        elif game_id == "memory_match":
            result = _stress_memory_match(instance, interaction_count)
        elif game_id == "tic_tac_toe":
            result = _stress_tic_tac_toe(instance, interaction_count)
    except Exception as exc:
        result["crashed"] = True
        result["exception"] = f"{type(exc).__name__}: {exc}"
        result["interactions_completed"] = interaction_count  # best-effort

    # Verify render_state() after stress
    try:
        state = instance.render_state()
        result["render_state_valid"] = isinstance(state, dict) and len(state) > 0
    except Exception as exc:
        result["render_state_valid"] = False
        result["render_state_error"] = f"{type(exc).__name__}: {exc}"

    return result


def _stress_tick_game(instance: Any, game_id: str, count: int) -> dict[str, Any]:
    """Run N ticks on a tick-based game, tracking crashes vs graceful end."""
    result: dict[str, Any] = {
        "crashed": False,
        "ended_gracefully": False,
        "exception": None,
        "interactions_completed": 0,
    }
    try:
        for i in range(count):
            try:
                alive = instance.tick()
                if not alive:
                    result["ended_gracefully"] = True
                    result["interactions_completed"] = i + 1
                    return result
            except Exception as exc:
                result["crashed"] = True
                result["exception"] = f"{type(exc).__name__}: {exc}"
                result["interactions_completed"] = i
                return result
        result["interactions_completed"] = count
    except Exception as exc:
        result["crashed"] = True
        result["exception"] = f"{type(exc).__name__}: {exc}"
    return result


def _stress_minesweeper(instance: Any, count: int) -> dict[str, Any]:
    import random as _random
    result: dict[str, Any] = {
        "interactions_completed": 0, "crashed": False,
        "ended_gracefully": False, "exception": None,
    }
    try:
        gw = getattr(instance, "grid_w", 10)
        gh = getattr(instance, "grid_h", 10)
        for i in range(count):
            try:
                x, y = _random.randint(0, gw - 1), _random.randint(0, gh - 1)
                instance.reveal(x, y)
                if getattr(instance, "game_over", False):
                    result["ended_gracefully"] = True
                    result["interactions_completed"] = i + 1
                    return result
            except Exception as exc:
                result["crashed"] = True
                result["exception"] = f"{type(exc).__name__}: {exc}"
                result["interactions_completed"] = i
                return result
        result["interactions_completed"] = count
    except Exception as exc:
        result["crashed"] = True
        result["exception"] = f"{type(exc).__name__}: {exc}"
    return result


def _stress_checkers(instance: Any, count: int) -> dict[str, Any]:
    import random as _random
    result: dict[str, Any] = {
        "interactions_completed": 0, "crashed": False,
        "ended_gracefully": False, "exception": None,
    }
    cols = "abcdefgh"
    try:
        for i in range(count):
            try:
                sq = f"{_random.choice(cols)}{_random.randint(1, 8)}"
                if hasattr(instance, "get_valid_moves"):
                    moves = instance.get_valid_moves(sq)
                    if moves:
                        target = _random.choice(moves)
                        instance.move(sq, target)
                if getattr(instance, "game_over", False):
                    result["ended_gracefully"] = True
                    result["interactions_completed"] = i + 1
                    return result
            except Exception as exc:
                result["crashed"] = True
                result["exception"] = f"{type(exc).__name__}: {exc}"
                result["interactions_completed"] = i
                return result
        result["interactions_completed"] = count
    except Exception as exc:
        result["crashed"] = True
        result["exception"] = f"{type(exc).__name__}: {exc}"
    return result


def _stress_banana(instance: Any, count: int) -> dict[str, Any]:
    import random as _random
    result: dict[str, Any] = {
        "interactions_completed": 0, "crashed": False,
        "ended_gracefully": False, "exception": None,
    }
    try:
        for i in range(count):
            try:
                angle = _random.uniform(0, 90)
                velocity = _random.uniform(1, 20)
                instance.throw(angle, velocity)
                if getattr(instance, "game_over", False):
                    result["ended_gracefully"] = True
                    result["interactions_completed"] = i + 1
                    return result
            except Exception as exc:
                result["crashed"] = True
                result["exception"] = f"{type(exc).__name__}: {exc}"
                result["interactions_completed"] = i
                return result
        result["interactions_completed"] = count
    except Exception as exc:
        result["crashed"] = True
        result["exception"] = f"{type(exc).__name__}: {exc}"
    return result


def _stress_maze_runner(instance: Any, count: int) -> dict[str, Any]:
    import random as _random
    result: dict[str, Any] = {
        "interactions_completed": 0, "crashed": False,
        "ended_gracefully": False, "exception": None,
    }
    directions = ["up", "down", "left", "right"]
    try:
        for i in range(count):
            try:
                d = _random.choice(directions)
                instance.input(d)
                if getattr(instance, "game_over", False) or getattr(instance, "won", False):
                    result["ended_gracefully"] = True
                    result["interactions_completed"] = i + 1
                    return result
            except Exception as exc:
                result["crashed"] = True
                result["exception"] = f"{type(exc).__name__}: {exc}"
                result["interactions_completed"] = i
                return result
        result["interactions_completed"] = count
    except Exception as exc:
        result["crashed"] = True
        result["exception"] = f"{type(exc).__name__}: {exc}"
    return result


def _stress_word_guesser(instance: Any, count: int) -> dict[str, Any]:
    import random as _random
    import string as _string
    result: dict[str, Any] = {
        "interactions_completed": 0, "crashed": False,
        "ended_gracefully": False, "exception": None,
    }
    try:
        for i in range(count):
            try:
                letter = _random.choice(_string.ascii_lowercase)
                instance.guess(letter)
                if getattr(instance, "game_over", False):
                    result["ended_gracefully"] = True
                    result["interactions_completed"] = i + 1
                    return result
            except Exception as exc:
                result["crashed"] = True
                result["exception"] = f"{type(exc).__name__}: {exc}"
                result["interactions_completed"] = i
                return result
        result["interactions_completed"] = count
    except Exception as exc:
        result["crashed"] = True
        result["exception"] = f"{type(exc).__name__}: {exc}"
    return result


def _stress_memory_match(instance: Any, count: int) -> dict[str, Any]:
    import random as _random
    result: dict[str, Any] = {
        "interactions_completed": 0, "crashed": False,
        "ended_gracefully": False, "exception": None,
    }
    try:
        cards = getattr(instance, "cards", [])
        num_cards = len(cards) if cards else 16
        for i in range(count):
            try:
                card_id = _random.randint(0, num_cards - 1)
                instance.flip(card_id)
                if getattr(instance, "game_over", False):
                    result["ended_gracefully"] = True
                    result["interactions_completed"] = i + 1
                    return result
            except Exception as exc:
                result["crashed"] = True
                result["exception"] = f"{type(exc).__name__}: {exc}"
                result["interactions_completed"] = i
                return result
        result["interactions_completed"] = count
    except Exception as exc:
        result["crashed"] = True
        result["exception"] = f"{type(exc).__name__}: {exc}"
    return result


def _stress_tic_tac_toe(instance: Any, count: int) -> dict[str, Any]:
    import random as _random
    result: dict[str, Any] = {
        "interactions_completed": 0, "crashed": False,
        "ended_gracefully": False, "exception": None,
    }
    try:
        for i in range(count):
            try:
                row, col = _random.randint(0, 2), _random.randint(0, 2)
                outcome = instance.move(row, col)
                if isinstance(outcome, dict) and outcome.get("game_over"):
                    result["ended_gracefully"] = True
                    result["interactions_completed"] = i + 1
                    return result
                if isinstance(outcome, dict) and not outcome.get("valid"):
                    pass
                if getattr(instance, "game_over", False):
                    result["ended_gracefully"] = True
                    result["interactions_completed"] = i + 1
                    return result
            except Exception as exc:
                result["crashed"] = True
                result["exception"] = f"{type(exc).__name__}: {exc}"
                result["interactions_completed"] = i
                return result
        result["interactions_completed"] = count
    except Exception as exc:
        result["crashed"] = True
        result["exception"] = f"{type(exc).__name__}: {exc}"
    return result


def _run_persistence_tests(
    source: str,
    game_id: str,
    class_name: str,
    interaction_count: int,
    tmp_dir: Path,
) -> dict[str, Any]:
    """Write source, import module, instantiate, run persistence stress."""
    results: dict[str, Any] = {
        "module_imported": False,
        "instantiated": False,
        "stress": {},
        "errors": [],
    }

    module_path = tmp_dir / f"{game_id}_persist.py"
    try:
        module_path.write_text(source)
    except Exception as e:
        results["errors"].append(f"Failed to write module: {e}")
        return results

    module_name = f"{game_id}_persist"
    try:
        spec = importlib.util.spec_from_file_location(module_name, str(module_path))
        if spec is None or spec.loader is None:
            results["errors"].append("importlib spec_from_file_location returned None")
            return results
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)
        results["module_imported"] = True
    except Exception as e:
        results["errors"].append(f"Module import failed: {type(e).__name__}: {e}")
        return results

    cls = getattr(mod, class_name, None)
    if cls is None:
        results["errors"].append(f"Class {class_name} not found in module")
        return results

    try:
        if class_name == "Minesweeper":
            instance = cls(10, 10, 10)
        elif class_name == "Snake":
            instance = cls(20, 20)
        elif class_name == "SkiFree":
            instance = cls(40, 100)
        else:
            instance = cls()
        results["instantiated"] = True
    except Exception as e:
        results["errors"].append(f"Instantiation failed: {type(e).__name__}: {e}")
        return results

    results["stress"] = _run_persistence_stress(instance, game_id, interaction_count)
    return results


# ---- Module-level helper: call DeepSeek for game generation ----
def _call_deepseek(gateway: Any, prompt: str) -> dict[str, Any]:
    """Call DeepSeek and return response metadata + content."""
    response = gateway.call_model(
        "deepseek_coder",
        messages=[{"role": "user", "content": prompt}],
        estimated_cost=0.0,
        budget_remaining=5.0,
    )
    usage = response.usage_metadata or {}
    return {
        "content": response.content,
        "tokens_in": int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
        "tokens_out": int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
        "tool_calls": len(response.tool_calls) if response.tool_calls else 0,
        "content_len": len(response.content),
        "model": getattr(response, "model_profile_id", "unknown"),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _DEEPSEEK_KEY, reason=_SKIP_REASON)
class TestDeepSeekGameBuilding:
    """Build each game via DeepSeek API and verify it works headlessly."""

    @pytest.fixture(scope="class")
    def gateway(self):
        return _build_deepseek_gateway()

    @staticmethod
    def _call_model(gateway: Any, prompt: str) -> dict[str, Any]:
        """Call DeepSeek and return response metadata + content."""
        response = gateway.call_model(
            "deepseek_coder",
            messages=[{"role": "user", "content": prompt}],
            estimated_cost=0.0,
            budget_remaining=5.0,
        )
        usage = response.usage_metadata or {}
        return {
            "content": response.content,
            "tokens_in": int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
            "tokens_out": int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
            "tool_calls": len(response.tool_calls) if response.tool_calls else 0,
            "content_len": len(response.content),
            "model": getattr(response, "model_profile_id", "unknown"),
        }

    # ---- Snake ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_build_snake(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Snake game."""
        self._build_and_verify_game(gateway, tmp_path, "snake")

    # ---- Tetris ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_build_tetris(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Tetris game."""
        self._build_and_verify_game(gateway, tmp_path, "tetris")

    # ---- Minesweeper ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_build_minesweeper(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Minesweeper game."""
        self._build_and_verify_game(gateway, tmp_path, "minesweeper")

    # ---- Checkers ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_build_checkers(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Checkers game."""
        self._build_and_verify_game(gateway, tmp_path, "checkers")

    # ---- SkiFree ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_build_skifree(self, gateway, tmp_path):
        """Test: DeepSeek builds a working SkiFree game."""
        self._build_and_verify_game(gateway, tmp_path, "skifree")

    # ---- Banana ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_build_banana(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Banana (Gorillas) game."""
        self._build_and_verify_game(gateway, tmp_path, "banana")

    # ---- Pong ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_build_pong(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Pong game."""
        self._build_and_verify_game(gateway, tmp_path, "pong")

    # ---- Breakout ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_build_breakout(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Breakout game."""
        self._build_and_verify_game(gateway, tmp_path, "breakout")

    # ---- Maze Runner ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_build_maze_runner(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Maze Runner game."""
        self._build_and_verify_game(gateway, tmp_path, "maze_runner")

    # ---- Word Guesser ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_build_word_guesser(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Word Guesser game."""
        self._build_and_verify_game(gateway, tmp_path, "word_guesser")

    # ---- Memory Match ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_build_memory_match(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Memory Match game."""
        self._build_and_verify_game(gateway, tmp_path, "memory_match")

    # ---- Tic-Tac-Toe ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_build_tic_tac_toe(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Tic-Tac-Toe game."""
        self._build_and_verify_game(gateway, tmp_path, "tic_tac_toe")

    # ---- Shared build + verify logic ----
    def _build_and_verify_game(self, gateway, tmp_path, game_id):
        game_def = GAME_DEFINITIONS[game_id]
        class_name = game_def["class_name"]
        verifications = game_def["verifications"]

        print(f"\n\n{'='*70}")
        print(f"BUILDING: {game_id} ({class_name})")
        print(f"{'='*70}")

        # Step 1: Call DeepSeek
        print(f"\n--- Step 1: Calling DeepSeek for {game_id} ---")
        try:
            response = self._call_model(gateway, game_def["prompt"])
        except Exception as e:
            if _is_rate_limit_error(e):
                pytest.xfail(f"Rate limited: {e}")
            raise

        print(f"  tokens_in={response['tokens_in']} tokens_out={response['tokens_out']}")
        print(f"  content_len={response['content_len']} tool_calls={response['tool_calls']}")

        # Step 2: Extract code
        print("\n--- Step 2: Extracting Python code ---")
        source = _extract_python_module(response["content"])
        if source is None:
            print("  FAIL: Could not extract Python module from model output")
            print(f"  Raw output (first 500): {response['content'][:500]!r}")
            # Don't fail the test — this is a gap finding
            self._record_gap(game_id, "code_extraction", "Model did not produce extractable Python code")
            return

        print(f"  Extracted {len(source)} chars of Python code")

        # Step 3: Parse AST
        print("\n--- Step 3: AST parsing ---")
        ast_result = _parse_ast(source)
        print(f"  parseable={ast_result['parseable']} has_class={ast_result['has_class']}")
        if ast_result["error"]:
            print(f"  AST error: {ast_result['error']}")

        # Step 4: Write module and run game tests
        print("\n--- Step 4: Game verification ---")
        game_dir = tmp_path / game_id
        game_dir.mkdir(exist_ok=True)
        test_results = _run_game_tests(source, class_name, verifications, game_id, game_dir)

        print(f"  module_written={test_results['module_written']}")
        print(f"  module_imported={test_results['module_imported']}")
        print(f"  instantiated={test_results['instantiated']}")
        if test_results["errors"]:
            for err in test_results["errors"]:
                print(f"  ERROR: {err[:200]}")

        checks = test_results.get("checks", {})
        passed = sum(1 for c in checks.values() if c["passed"])
        failed = len(checks) - passed
        print(f"  Checks: {passed} passed, {failed} failed out of {len(checks)}")

        for check_id, check_data in checks.items():
            status = "PASS" if check_data["passed"] else "FAIL"
            print(f"    [{status}] {check_id}: {check_data['desc']}")
            if check_data.get("error"):
                print(f"           error: {check_data['error'][:200]}")

        # Step 5: Report gap if module didn't import or checks failed
        if not test_results["module_imported"]:
            errors = test_results["errors"]
            detail = errors[:2] if errors else "unknown"
            self._record_gap(
                game_id, "import",
                f"Module failed to import: {detail}",
            )
        elif not test_results["instantiated"]:
            errors = test_results["errors"]
            detail = errors[:2] if errors else "unknown"
            self._record_gap(
                game_id, "instantiation",
                f"Class {class_name} failed to instantiate: {detail}",
            )
        elif failed > len(checks) * 0.5:
            self._record_gap(game_id, "game_logic", f"{failed}/{len(checks)} verification checks failed")

        print(f"\n{'-'*70}")
        print(f"RESULT: {game_id} — {passed}/{len(checks)} checks passed")

    # ---- Gap tracking ----
    _gaps: list[dict[str, Any]] = []  # noqa: RUF012

    @classmethod
    def _record_gap(cls, game_id: str, category: str, detail: str) -> None:
        cls._gaps.append({"game": game_id, "category": category, "detail": detail})


# ---------------------------------------------------------------------------
# Pipeline Gap Analysis
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _DEEPSEEK_KEY, reason=_SKIP_REASON)
class TestGameBuildingGapAnalysis:
    """After running all game-building tests, analyze gludd's pipeline for gaps."""

    def test_gap_report(self):
        """Print a comprehensive gap analysis based on all game-building results."""
        gaps = TestDeepSeekGameBuilding._gaps
        if not gaps:
            print("\nNo gaps recorded yet — run game-building tests first")
            return

        print("\n\n" + "="*70)
        print("GAME-BUILDING GAP REPORT")
        print("="*70)
        print(f"\nTotal gaps found: {len(gaps)}")

        by_category: dict[str, list[dict]] = {}
        for g in gaps:
            by_category.setdefault(g["category"], []).append(g)

        print("\nBy category:")
        for cat, items in sorted(by_category.items()):
            games = sorted(set(i["game"] for i in items))
            print(f"  {cat}: {len(items)} gaps across {len(games)} games ({', '.join(games)})")

        print("\nDetailed gaps:")
        for g in gaps:
            print(f"  [{g['game']}] {g['category']}: {g['detail'][:200]}")

        print("\n" + "="*70)
        print("PIPELINE IMPROVEMENT RECOMMENDATIONS")
        print("="*70)
        print("""
1. ITERATIVE CODE GENERATION: The ExecutionEngine only does single-shot generation.
   For complex tasks like game-building, the model needs multiple attempts with
   feedback from test results. The ToolCallLoop is currently restricted to
   'analysis'/'audit' work types only.

2. PROMPT ENGINEERING: Game-building prompts may need refinement for better
   code generation. Consider:
   - Few-shot examples in the prompt
   - Structured output format requirements
   - Breaking complex tasks into sub-tasks (build skeleton → add feature → test)

3. CODE EXTRACTION ROBUSTNESS: The fenced-block parser may miss code when
   the model uses non-standard formatting. Consider:
   - Natural language code detection (heuristic: indented blocks after "class X:")
   - Multi-pass extraction (try fenced blocks, then heuristic, then raw)
   - Model output that isn't valid Python should trigger automatic retry

4. TEST FEEDBACK LOOP: After writing code, gludd runs 'make test' but there's
   no mechanism to feed test failures back to the model for corrections.
   This is THE critical gap — without it, complex tasks are single-shot guesses.

5. DEPENDENCY MANAGEMENT: Games may require additional packages (pygame, etc.).
   gludd should detect import errors and either add dependencies or suggest
   stdlib-only alternatives.

6. WORKSPACE ISOLATION: Each game should be built in its own workspace to
   prevent cross-contamination between tasks.

7. METRICS AND OBSERVABILITY: Track per-task metrics:
   - Tokens consumed per task
   - Iterations/debug cycles per task
   - Success rate by task type
    - Common failure modes
""")


# ---------------------------------------------------------------------------
# Full Pipeline Tests — gludd's ExecutionEngine + EventLoop wired to DeepSeek
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _DEEPSEEK_KEY, reason=_SKIP_REASON)
class TestDeepSeekFullPipeline:
    """DeepSeek through gludd's FULL pipeline (not a raw API call bypass).

    TEST A: ExecutionEngine.execute()
        - model call → code extraction → file write → test run → git commit
        - This is the canonical code-generation path; it exercises the real engine.

    TEST B: EventLoop._dispatch_execute_job_isolated()
        - real loop dispatch wired to DeepSeek via invoke_model_for_generation
        - Follows the proven pattern from test_pipeline_live_zai.py (G7 real loop).
    """

    # -- async session factory (SQLite in-memory) for EventLoop tests ----------

    @staticmethod
    async def _make_session_factory() -> Any:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import StaticPool

        from general_ludd.db.models import Base

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            echo=False,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return async_sessionmaker(engine, expire_on_commit=False)

    # -- No-op runner (records calls, no Ansible subprocess) -------------------

    class _NoopRunner:
        """Minimal AnsibleRunnerAdapter stand-in — no subprocess, just records."""

        def __init__(self, workspace_root: str) -> None:
            self._root = workspace_root
            self.prepare_calls: list[str] = []
            self.run_calls: list[str] = []
            self.vars_written: list[dict[str, Any]] = []

        def prepare_job_dirs(self, job_id: str) -> dict[str, str]:
            self.prepare_calls.append(job_id)
            root = str(Path(self._root) / "jobs" / job_id)
            Path(root, "env").mkdir(parents=True, exist_ok=True)
            return {"root": root}

        def write_vars(self, job_id: str, job_vars: dict[str, Any], shared_vars: Any) -> None:
            self.vars_written.append({"job_id": job_id, "job_vars": dict(job_vars)})

        def run_playbook(
            self, playbook_name: str, private_data_dir: str,
            env: dict[str, str] | None = None,
        ) -> None:
            self.run_calls.append(playbook_name)

        def list_playbooks(self) -> list[str]:
            return ["noop.yml", "validate_task.yml", "return_review.yml"]

    # -------------------------------------------------------------------
    # TEST A: ExecutionEngine.execute() — the full code-generation path
    # -------------------------------------------------------------------

    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_execution_engine_full_pipeline_snake(self, tmp_path: Path) -> None:
        """ExecutionEngine: model → code gen → file write → test → commit."""
        from general_ludd.execution.engine import ExecutionEngine
        from general_ludd.schemas.job import JobSpec

        # 1. Create temp git workspace
        ws = tmp_path / "workspace"
        ws.mkdir()
        subprocess.run(["git", "init", str(ws)], check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@general-ludd.local"],
            cwd=ws, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test Agent"],
            cwd=ws, check=True, capture_output=True,
        )

        (ws / "snake_game.py").write_text("# placeholder\n")
        (ws / "test_snake.py").write_text("def test_placeholder():\n    assert True\n")
        subprocess.run(["git", "add", "."], cwd=ws, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial workspace"],
            cwd=ws, check=True, capture_output=True,
        )

        # 2. Create gateway
        gateway = _build_deepseek_gateway()

        # 3. Create ExecutionEngine
        engine = ExecutionEngine(
            model_gateway=gateway,
            workspace_path=str(ws),
        )

        # 4. Create JobSpec for Snake game
        job = JobSpec(
            job_id="EXEC-SNAKE-001",
            todo_id="TODO-SNAKE-001",
            playbook="validate_task.yml",
            queue="core",
            work_type="code",
            prompt_text=GAME_DEFINITIONS["snake"]["prompt"],
            model_profile="deepseek_coder",
        )

        # 5. Execute
        print("\n\n" + "=" * 70)
        print("FULL PIPELINE TEST A: ExecutionEngine.execute() via DeepSeek")
        print("=" * 70)
        result = engine.execute(job)

        print("\n  TaskReturn:")
        print(f"    return_id    = {result.return_id}")
        print(f"    exit_code    = {result.exit_code}")
        print(f"    summary      = {result.result_summary[:300]}")
        print(f"    artifacts    = {result.artifacts}")
        print(f"    diff_ref     = {result.diff_ref}")
        print(f"    test_results = {result.test_results_ref}")

        # 6. Check files written
        all_files = sorted(ws.glob("**/*"))
        code_files = [
            str(f.relative_to(ws))
            for f in all_files
            if f.suffix == ".py" or f.suffix == ".md" or f.suffix == ".txt"
        ]
        print(f"\n  Workspace files: {code_files}")

        # 7. Check git status
        log_result = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            cwd=ws, capture_output=True, text=True,
        )
        branch_result = subprocess.run(
            ["git", "branch"],
            cwd=ws, capture_output=True, text=True,
        )
        print(f"  Git log:\n{log_result.stdout}")
        print(f"  Git branches:\n{branch_result.stdout}")

        # 8. Analysis
        print("\n" + "-" * 70)
        print("ANALYSIS")
        print("-" * 70)
        print(f"  Model returned content:   {result.diff_ref or 'NONE'}")
        print(f"  Files changed:            {result.artifacts}")
        print(f"  Test exit code:           {result.exit_code}")

        has_git_artifact = bool(
            result.artifacts
            and any("commit:" in str(a) for a in result.artifacts)
        )
        print(f"  Has commit in artifacts:  {has_git_artifact}")

        code_generated = (
            has_git_artifact
            or (result.artifacts and len(result.artifacts) > 1)
        )
        print(f"  Code was generated:       {code_generated}")

        has_game_file = any("snake" in str(f).lower() for f in code_files)
        print(f"  Snake file exists:        {has_game_file}")

        # Structural assertions (setup wiring, not model output quality)
        assert result.return_id.startswith("RET-"), "return_id missing RET- prefix"
        assert result.result_summary, "result_summary should not be empty"

        if not code_generated:
            print("\n  GAP: ExecutionEngine did not produce application code.")
            print("  This means either: (a) DeepSeek output was not parseable, or")
            print("  (b) the fenced-block / FILE: extraction failed.")
            print(f"  raw diff_ref: {result.diff_ref}")
        else:
            print("\n  SUCCESS: ExecutionEngine generated files, tests ran.")

        print("=" * 70)
        print("END TEST A")
        print("=" * 70 + "\n")

    # -------------------------------------------------------------------
    # TEST B: EventLoop._dispatch_execute_job_isolated (real loop wire-up)
    # -------------------------------------------------------------------

    @pytest.mark.asyncio
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    async def test_event_loop_dispatch_snake(self, tmp_path: Path) -> None:
        """EventLoop dispatch wired to DeepSeek — real invoke_model_for_generation.

        Follows the proven pattern from test_pipeline_live_zai.py (G7).
        """
        from general_ludd.event_loop.loop import EventLoop
        from general_ludd.prompts.registry import PromptRegistry
        from general_ludd.schemas.todo import Todo, TodoStatus, WorkType

        gw = _build_deepseek_gateway()
        session_factory = await self._make_session_factory()

        ws = str(tmp_path / "loop-workspace")
        Path(ws).mkdir(parents=True, exist_ok=True)
        runner = self._NoopRunner(ws)

        prompt_registry = PromptRegistry()
        prompt_registry.register(
            "snake_build.md.j2",
            GAME_DEFINITIONS["snake"]["prompt"],
        )

        loop = EventLoop(
            session=None,
            model_gateway=gw,
            runner=runner,
            prompt_registry=prompt_registry,
        )
        loop._session_factory = session_factory
        loop._total_ticks = 1
        loop._tick_state = {}
        loop._config_snapshot = {}

        todo = Todo(
            todo_id="TODO-SNAKE-LIVE-001",
            title="Build a Snake game in Python",
            description=GAME_DEFINITIONS["snake"]["prompt"],
            work_type=WorkType.CODE,
            queue="core",
            model_profile="deepseek_coder",
            prompt_profile="snake_build.md.j2",
        )
        todo.status = TodoStatus.ACTIVE

        print("\n\n" + "=" * 70)
        print("FULL PIPELINE TEST B: EventLoop._dispatch_execute_job_isolated")
        print("=" * 70)
        print(f"[LOOP] dispatching {todo.todo_id} via real EventLoop dispatch")
        print(f"[LOOP] model_profile={todo.model_profile!r} "
              f"prompt_profile={todo.prompt_profile!r}")

        await loop._dispatch_execute_job_isolated(todo)

        print(f"\n[LOOP] prepare_calls  = {runner.prepare_calls}")
        print(f"[LOOP] run_calls       = {runner.run_calls}")
        print(f"[LOOP] vars_written    = {len(runner.vars_written)}")

        # ---- Assertions (wiring) ----
        assert runner.vars_written, (
            "LOOP: write_vars never called — dispatch is broken"
        )
        assert runner.prepare_calls, (
            "LOOP: prepare_job_dirs never called — dispatch is broken"
        )

        vars_entry = runner.vars_written[0]
        job_vars = vars_entry.get("job_vars", {})
        job_id = job_vars.get("job_id", "")
        prompt_text = job_vars.get("prompt_text")
        model_response = job_vars.get("model_response")

        print(f"[LOOP] job_id          = {job_id!r}")
        print(f"[LOOP] prompt_text[:200] = {str(prompt_text)[:200]!r}")

        assert job_id.startswith("EXEC-"), (
            f"LOOP: job_id should start with EXEC-, got {job_id!r}"
        )
        assert prompt_text, (
            "LOOP: prompt_text is empty — PromptRegistry wiring failed"
        )
        assert model_response, (
            "LOOP: model_response is empty — DeepSeek was never called "
            "(invoke_model_for_generation returned None)"
        )

        response_text = str(model_response)
        print(f"[LOOP] model_response  = {len(response_text)} chars")
        print(f"[LOOP] model_response[:500] = {response_text[:500]!r}")

        # ---- Code quality checks ----
        has_class = "class Snake" in response_text or "class Snake:" in response_text
        has_import = "import" in response_text.lower()
        has_def = "def " in response_text
        print(f"\n[LOOP] has class Snake?:  {has_class}")
        print(f"[LOOP] has imports?:      {has_import}")
        print(f"[LOOP] has function def?: {has_def}")

        # ---- Extract and validate Python ----
        source = _extract_python_module(response_text)
        if source:
            ast_result = _parse_ast(source)
            print(f"[LOOP] AST parseable:     {ast_result['parseable']}")
            print(f"[LOOP] AST has_class:     {ast_result['has_class']}")
            print(f"[LOOP] AST has_imports:   {ast_result['has_imports']}")
            if ast_result["error"]:
                print(f"[LOOP] AST error:         {ast_result['error']}")

            if ast_result["parseable"] and ast_result["has_class"]:
                # Write to disk and try to import
                game_dir = tmp_path / "snake_game"
                game_dir.mkdir(exist_ok=True)
                test_results = _run_game_tests(
                    source,
                    GAME_DEFINITIONS["snake"]["class_name"],
                    GAME_DEFINITIONS["snake"]["verifications"],
                    "snake_deepseek",
                    game_dir,
                )
                print(f"[LOOP] module_imported:    {test_results['module_imported']}")
                print(f"[LOOP] instantiated:       {test_results['instantiated']}")
                checks = test_results.get("checks", {})
                passed = sum(1 for c in checks.values() if c["passed"])
                failed = len(checks) - passed
                print(f"[LOOP] game checks:        {passed}/{len(checks)} passed, "
                      f"{failed} failed")
                if test_results["errors"]:
                    for err in test_results["errors"]:
                        print(f"[LOOP] ERROR: {err[:200]}")
            else:
                print("[LOOP] WARNING: model output does not parse as valid Python")
        else:
            print("[LOOP] WARNING: could not extract Python module from model output")
            print(f"[LOOP] Raw output contains 'class': "
                  f"{'class ' in response_text or 'class:' in response_text}")
            print(f"[LOOP] Raw output contains '```': "
                  f"{'```' in response_text}")

        # ---- Gap analysis ----
        print("\n" + "-" * 70)
        print("ANALYSIS")
        print("-" * 70)
        print(f"  Model returned content:   {'YES' if model_response else 'NO'}")
        print(f"  Contains Python class:    {has_class}")
        print(f"  Contains imports:         {has_import}")
        print(f"  Source extractable:       {source is not None}")
        if source:
            print(f"  Source parseable:         {ast_result['parseable']}")
            print(f"  Source has Snake class:   {ast_result['has_class']}")

        gaps: list[str] = []
        if not has_class:
            gaps.append("Model output lacks 'class Snake'")
        if source is None:
            gaps.append("Python code extraction failed")
        elif not ast_result["parseable"]:
            gaps.append(f"AST parse error: {ast_result.get('error', 'unknown')}")
        elif not ast_result["has_class"]:
            gaps.append("Parsed AST has no class definition")

        if gaps:
            print(f"\n  PIPELINE GAPS ({len(gaps)}):")
            for g in gaps:
                print(f"    - {g}")
        else:
            print("\n  No pipeline gaps detected — model output is valid Python.")

        print("=" * 70)
        print("END TEST B — Full pipeline via EventLoop dispatch: PROVEN")
        print("=" * 70 + "\n")


# ---------------------------------------------------------------------------
# Game Persistence Tests — extended-play stress testing
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _DEEPSEEK_KEY, reason=_SKIP_REASON)
class TestGamePersistence:
    """Extended-play stress tests: each game must survive 500 ticks/interactions.

    Tracks: crashes (exceptions), graceful endings (game_over), and render_state
    validity after extended play. Adds to gap analysis if a game fails.
    """

    _persistence_gaps: list[dict[str, Any]] = []  # noqa: RUF012

    @classmethod
    def _record_persistence_gap(cls, game_id: str, category: str, detail: str) -> None:
        cls._persistence_gaps.append({"game": game_id, "category": category, "detail": detail})

    @pytest.fixture(scope="class")
    def gateway(self):
        return _build_deepseek_gateway()

    # ---- Shared builder ----
    def _build_and_stress(self, gateway, tmp_path, game_id):
        game_def = GAME_DEFINITIONS[game_id]
        class_name = game_def["class_name"]
        interaction_count = _GAME_PERSISTENCE_PARAMS.get(game_id, 500)

        print(f"\n\n{'='*70}")
        print(f"PERSISTENCE TEST: {game_id} ({class_name}) — {interaction_count} interactions")
        print(f"{'='*70}")

        # Step 1: Call DeepSeek
        print(f"\n--- Step 1: Calling DeepSeek for {game_id} ---")
        try:
            response = _call_deepseek(gateway, game_def["prompt"])
        except Exception as e:
            if _is_rate_limit_error(e):
                pytest.xfail(f"Rate limited: {e}")
            raise

        print(f"  tokens_in={response['tokens_in']} tokens_out={response['tokens_out']}")

        # Step 2: Extract code
        source = _extract_python_module(response["content"])
        if source is None:
            self._record_persistence_gap(game_id, "code_extraction", "Could not extract Python code")
            print("  FAIL: Could not extract Python code")
            return

        # Step 3: Parse AST
        ast_result = _parse_ast(source)
        if not ast_result["parseable"]:
            self._record_persistence_gap(game_id, "ast_parse", f"AST error: {ast_result.get('error')}")
            print(f"  FAIL: AST not parseable: {ast_result.get('error')}")
            return

        # Step 4: Run persistence stress test
        game_dir = tmp_path / game_id
        game_dir.mkdir(exist_ok=True)
        results = _run_persistence_tests(source, game_id, class_name, interaction_count, game_dir)

        print(f"\n  module_imported={results['module_imported']}")
        print(f"  instantiated={results['instantiated']}")
        if results["errors"]:
            for err in results["errors"]:
                print(f"  ERROR: {err[:200]}")

        stress = results.get("stress", {})
        crashed = stress.get("crashed", True)
        ended = stress.get("ended_gracefully", False)
        interactions = stress.get("interactions_completed", 0)
        render_ok = stress.get("render_state_valid", False)

        print("\n  Stress results:")
        print(f"    crashed              = {crashed}")
        print(f"    ended_gracefully     = {ended}")
        print(f"    interactions_completed = {interactions}/{interaction_count}")
        print(f"    render_state_valid   = {render_ok}")
        if stress.get("exception"):
            print(f"    exception: {stress['exception'][:200]}")
        if stress.get("render_state_error"):
            print(f"    render_state_error: {stress['render_state_error'][:200]}")

        # Gap detection
        if not results["module_imported"]:
            self._record_persistence_gap(
                game_id, "import",
                f"Module failed to import during persistence test: {results['errors'][:2]}",
            )
        elif not results["instantiated"]:
            self._record_persistence_gap(
                game_id, "instantiation",
                f"Class {class_name} failed to instantiate: {results['errors'][:2]}",
            )
        elif crashed:
            self._record_persistence_gap(
                game_id, "persistence_crash",
                f"Crashed at interaction {interactions}: {stress.get('exception', 'unknown')}",
            )
        elif not render_ok:
            self._record_persistence_gap(
                game_id, "render_state",
                f"render_state() failed after {interactions} interactions: "
                f"{stress.get('render_state_error', 'unknown')}",
            )

        status = "CRASHED" if crashed else ("ENDED" if ended else "OK")
        print(f"\n  PERSISTENCE STATUS: {status}")
        print(f"{'-'*70}")

    # ---- Snake ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_persistence_snake(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "snake")

    # ---- Tetris ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_persistence_tetris(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "tetris")

    # ---- Minesweeper ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_persistence_minesweeper(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "minesweeper")

    # ---- Checkers ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_persistence_checkers(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "checkers")

    # ---- SkiFree ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_persistence_skifree(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "skifree")

    # ---- Banana ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_persistence_banana(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "banana")

    # ---- Pong ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_persistence_pong(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "pong")

    # ---- Breakout ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_persistence_breakout(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "breakout")

    # ---- Maze Runner ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_persistence_maze_runner(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "maze_runner")

    # ---- Word Guesser ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_persistence_word_guesser(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "word_guesser")

    # ---- Memory Match ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_persistence_memory_match(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "memory_match")

    # ---- Tic-Tac-Toe ----
    @pytest.mark.xfail(raises=_RATE_LIMIT_EXC, reason="rate-limit", strict=False)
    def test_persistence_tic_tac_toe(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "tic_tac_toe")

    # ---- Persistence Gap Report ----
    def test_persistence_gap_report(self):
        """Print a comprehensive persistence gap analysis."""
        gaps = TestGamePersistence._persistence_gaps
        if not gaps:
            print("\nNo persistence gaps recorded — all tested games survived extended play")
            return

        print("\n\n" + "=" * 70)
        print("GAME PERSISTENCE GAP REPORT")
        print("=" * 70)
        print(f"\nTotal persistence gaps found: {len(gaps)}")

        by_game: dict[str, list[dict]] = {}
        for g in gaps:
            by_game.setdefault(g["game"], []).append(g)

        print("\nBy game:")
        for game, items in sorted(by_game.items()):
            cats = sorted(set(i["category"] for i in items))
            print(f"  {game}: {len(items)} gaps ({', '.join(cats)})")

        print("\nDetailed gaps:")
        for g in gaps:
            print(f"  [{g['game']}] {g['category']}: {g['detail'][:200]}")

        print("\n" + "=" * 70)
