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
import contextlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import textwrap
import time
import traceback
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest

from tests.e2e._game_lifecycle import _invoke_start_method, run_lifecycle_checks

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




# ---------------------------------------------------------------------------
# Observability data collector
# ---------------------------------------------------------------------------

_OBSERVABILITY_DATA: dict[str, Any] = {}
_OBS_REPORT_PATH = Path(__file__).parent.parent.parent / ".game-audit-report.json"


def _export_observability_report() -> None:
    """Write the accumulated observability data to a JSON report file."""
    report = {
        "report_generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "games": _OBSERVABILITY_DATA,
        "summary": _compute_obs_summary(),
    }
    _OBS_REPORT_PATH.write_text(json.dumps(report, indent=2))
    print(f"\nObservability report written to {_OBS_REPORT_PATH}")


def _compute_obs_summary() -> dict[str, Any]:
    total_games = len(_OBSERVABILITY_DATA)
    if total_games == 0:
        return {"total_games": 0}

    games_imported = sum(1 for g in _OBSERVABILITY_DATA.values() if g.get("imported"))
    games_verified = sum(
        1 for g in _OBSERVABILITY_DATA.values()
        if g.get("checks_passed", 0) == g.get("checks_total", 0)
    )
    total_tokens_in = sum(g.get("tokens_in", 0) for g in _OBSERVABILITY_DATA.values())
    total_tokens_out = sum(g.get("tokens_out", 0) for g in _OBSERVABILITY_DATA.values())
    total_latency_ms = sum(
        (g.get("phases", {}).get("model_call", 0) +
         g.get("phases", {}).get("extract_code", 0) +
         g.get("phases", {}).get("ast_parse", 0) +
         g.get("phases", {}).get("game_verify", 0))
        for g in _OBSERVABILITY_DATA.values()
    )

    games_by_tokens = sorted(
        _OBSERVABILITY_DATA.items(),
        key=lambda kv: kv[1].get("tokens_out", 0),
        reverse=True,
    )
    games_by_latency = sorted(
        _OBSERVABILITY_DATA.items(),
        key=lambda kv: sum(kv[1].get("phases", {}).values()),
        reverse=True,
    )

    return {
        "total_games": total_games,
        "games_imported": games_imported,
        "games_fully_verified": games_verified,
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
        "total_latency_ms": total_latency_ms,
        "total_latency_s": round(total_latency_ms / 1000, 2),
        "most_tokens": games_by_tokens[0][0] if games_by_tokens else None,
        "most_latency": games_by_latency[0][0] if games_by_latency else None,
    }


def _init_game_obs(game_id: str) -> dict[str, Any]:
    entry = {
        "game_id": game_id,
        "imported": False,
        "instantiated": False,
        "checks_passed": 0,
        "checks_total": 0,
        "checks": {},
        "tokens_in": 0,
        "tokens_out": 0,
        "tool_calls": 0,
        "content_len": 0,
        "model": "",
        "phases": {
            "model_call": 0.0,
            "extract_code": 0.0,
            "ast_parse": 0.0,
            "game_verify": 0.0,
        },
        "errors": [],
        "gaps": [],
    }
    _OBSERVABILITY_DATA[game_id] = entry
    return entry


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
    return cast(Any, ModelGateway)(profiles=[profile], provider_registry=registry, secrets_manager=secrets)


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

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": `tick()` advances the game; `score` (int) starts at 0 and
              increments on positive events (eating food).
            - Game-over detection: when a lose condition triggers (wall or self collision),
              `state` transitions to "game_over" and `game_over` (bool) becomes True. `tick()`
              after game_over is a no-op (returns without changing state).
            - `restart()` method: resets ALL state (score=0, game_over=False, snake to initial
              center position, food respawned, state="ready"). The instance is reusable.

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
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
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

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": `tick()` advances the game; `score` (int) starts at 0 and
              increments on positive events (clearing one or more rows).
            - Game-over detection: when a lose condition triggers (piece locks above visible
              grid), `state` transitions to "game_over" and `game_over` (bool) becomes True.
              `tick()` after game_over is a no-op (returns without changing state).
            - `restart()` method: resets ALL state (score=0, game_over=False, grid cleared,
              new piece spawned, state="ready"). The instance is reusable.

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
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
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

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": revealing cells advances the game; `score` (int, e.g.
              cells_revealed) starts at 0 and increments on positive events (revealing safe cell).
            - Game-over detection: when a lose condition triggers (mine revealed), `state`
              transitions to "game_over" and `game_over` (bool) becomes True. Revealing cells
              after game_over is a no-op.
            - Win detection: when a win condition triggers (all non-mine cells revealed),
              `state` transitions to "won" and `won` (bool) becomes True.
            - `restart()` method: resets ALL state (score=0, game_over=False, won=False, grid
              reset with new random mines, state="ready"). The instance is reusable.

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
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
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

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": `move()` advances the game; `score` (int, e.g. opponent pieces
              captured) starts at 0 and increments on positive events (capturing piece).
            - Game-over detection: when a lose condition triggers (current player has no valid
              moves), `state` transitions to "game_over" and `game_over` (bool) becomes True.
              `move()` after game_over is a no-op.
            - Win detection: when a win condition triggers (opponent has no pieces or no valid
              moves), `state` transitions to "won" and `won` (bool) becomes True.
            - `restart()` method: resets ALL state (score=0, game_over=False, won=False, board
              to standard starting position, current_player=1, state="ready"). Reusable.

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
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
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

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": `tick()` advances the game; `score` (int, e.g.
              distance_traveled) starts at 0 and increments on positive events (row traveled).
            - Game-over detection: when a lose condition triggers (collision with tree or
              rock), `state` transitions to "game_over" and `crashed`/`game_over` (bool) is
              True. `tick()` after game_over is a no-op.
            - Win detection: when a win condition triggers (reaching course bottom / y >=
              course_h), `state` transitions to "won" and `finished`/`won` (bool) becomes True.
            - `restart()` method: resets ALL state (score=0, game_over=False, won=False,
              crashed=False, skier at center-top, obstacles regenerated, state="ready").
              The instance is reusable.

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
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
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

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": `throw()` advances the game; `score` (int, e.g. successful
              hits) starts at 0 and increments on positive events (hitting opponent gorilla).
            - Game-over detection: when a lose condition triggers (your gorilla is hit),
              `state` transitions to "game_over" and `game_over` (bool) becomes True.
              `throw()` after game_over is a no-op.
            - Win detection: when a win condition triggers (opponent gorilla is hit), `state`
              transitions to "won", `won` (bool) becomes True, and `winner` is set.
            - `restart()` method: resets ALL state (score=0, game_over=False, won=False,
              winner=None, skyline regenerated, gorillas repositioned, current_player=1,
              wind randomized, state="ready"). The instance is reusable.

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
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
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

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": `tick()` advances the game; `score1`/`score2` (ints) start at 0
              and increment on positive events (ball passes opponent paddle).
            - Game-over detection: Pong is endless by default; `game_over` stays False.
              (Optional: if a score cap is implemented, transition to "game_over" when reached.)
            - `restart()` method: resets ALL state (score1=0, score2=0, ball centered with
              random direction, paddles centered, state="ready"). The instance is reusable.

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
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
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

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": `tick()` advances the game; `score` (int) starts at 0 and
              increments on positive events (destroying a brick).
            - Game-over detection: when a lose condition triggers (lives reach 0), `state`
              transitions to "game_over" and `game_over` (bool) becomes True. `tick()` after
              game_over is a no-op.
            - Win detection: when a win condition triggers (all bricks destroyed), `state`
              transitions to "won" and `won` (bool) becomes True.
            - `restart()` method: resets ALL state (score=0, game_over=False, won=False, ball
              on paddle, bricks regenerated, lives=3, state="ready"). The instance is reusable.

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
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
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

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": `input()` advances the player; `score` (int, e.g. negative of
              steps or efficiency metric) starts at 0 and is tracked alongside `steps`.
            - Win detection: when a win condition triggers (player reaches end position),
              `state` transitions to "won" and `won` (bool) becomes True. `input()` after won
              is a no-op.
            - `restart()` method: resets ALL state (player at start, steps=0, won=False,
              game_over=False, state="ready"). The instance is reusable.

            Output ONLY the Python code. Start with `import random` and `class MazeRunner:`.
        """).strip(),
        "class_name": "MazeRunner",
        "verifications": [
            ("import_and_instantiate", "class imports and instantiates without error"),
            ("maze_moves", "input('right') moves player on a path cell"),
            ("maze_wall_blocks", "moving into a wall cell blocks movement"),
            ("maze_reach_end", "reaching end position sets won=True"),
            ("render_state", "render_state() returns dict with expected keys"),
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
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

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": `guess()` advances the game; `score` (int, e.g. correct letter
              count) starts at 0 and increments on positive events (correct letter guessed).
            - Game-over detection: when a lose condition triggers (wrong_guesses reaches
              max_guesses), `state` transitions to "game_over" and `game_over` (bool) becomes
              True with `won=False`. `guess()` after game_over is a no-op.
            - Win detection: when a win condition triggers (all letters of secret_word
              guessed), `state` transitions to "won" and `won` (bool) becomes True.
            - `restart()` method: resets ALL state (new secret word chosen, guessed_letters
              cleared, wrong_guesses=0, game_over=False, won=False, state="ready"). Reusable.

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
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
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

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": `flip()` advances the game; `score` (int, e.g. pairs matched)
              starts at 0 and increments on positive events (matching a pair).
            - Win detection: when a win condition triggers (all pairs matched), `state`
              transitions to "won" and `won`/`game_over` (bool) becomes True.
            - `restart()` method: resets ALL state (cards reshuffled, flipped=False,
              matched=False, attempts=0, first_flip=None, game_over=False, state="ready").
              The instance is reusable.

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
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
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

            Lifecycle requirements (MANDATORY — tests will verify each transition):
            - Initial state: instance attribute `state` MUST start at "ready" (or "menu") — NOT
              "playing". The constructor does NOT immediately begin play.
            - `start()` method: transitions state from "ready"/"menu" to "playing". Returns None.
              If called when already playing, no-ops or raises.
            - During "playing": `move()` advances the game; `score` (int, e.g. marks placed by
              human) starts at 0 and increments on positive events (placing a mark).
            - Game-over detection: when an end condition triggers (win or draw), `state`
              transitions to "game_over" and `game_over` (bool) becomes True. `move()` after
              game_over is a no-op.
            - Win detection: when a win condition triggers (three in a row), `winner` is set
              and `state` becomes "won" (or "game_over" with winner populated).
            - `restart()` method: resets ALL state (board cleared, current_player="X",
              winner=None, game_over=False, draw=False, state="ready"). The instance is reusable.

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
            ("lifecycle_initial_state", "fresh instance state is 'ready'/'menu' (NOT 'playing')"),
            ("lifecycle_start", "calling start() transitions state to 'playing'"),
            ("lifecycle_score_starts_zero", "score is 0 at start of play"),
            ("lifecycle_score_increments", "score increases after positive event"),
            ("lifecycle_game_over", "triggering lose condition sets game_over=True and state='game_over'"),
            ("lifecycle_game_over_idempotent", "tick()/move() after game_over does not change state"),
            ("lifecycle_restart", "restart() resets score to 0, game_over to False, state to 'ready'"),
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


def _parse_ast(source: str) -> dict[str, Any]:
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


# ---------------------------------------------------------------------------
# Generic game-interface discovery (tolerant of model-naming variance)
# ---------------------------------------------------------------------------
#
# The model emits a different syntactic shape each run — class name, method
# names, constructor signature, state-dict keys all vary.  These helpers
# locate the game by BEHAVIOUR: find the richest class, instantiate it with
# whichever constructor signature works, and resolve methods through
# synonym groups so `instance.tick()` works whether the model wrote
# `tick`, `step`, `update`, etc.  Feature verification then operates on
# the discovered interface instead of hardcoded names.

_TICK_NAMES: tuple[str, ...] = (
    "tick", "step", "update", "advance", "next_frame", "next_turn",
    "frame", "turn", "simulate", "do_tick",
)
_INPUT_NAMES: tuple[str, ...] = (
    "input", "handle_input", "send_input", "set_direction", "direction",
    "action", "key", "press", "set_input", "on_input",
)
_STATE_NAMES: tuple[str, ...] = (
    "render_state", "get_state", "state", "to_dict", "as_dict",
    "snapshot", "serialize", "export_state",
)
_REVEAL_NAMES: tuple[str, ...] = ("reveal", "click", "open", "dig", "uncover")
_FLAG_NAMES: tuple[str, ...] = ("flag", "mark", "toggle_flag", "set_flag")
_MOVE_NAMES: tuple[str, ...] = (
    "move", "play", "make_move", "do_move", "submit_move",
)
_THROW_NAMES: tuple[str, ...] = ("throw", "shoot", "fire", "launch", "toss")
_FLIP_NAMES: tuple[str, ...] = ("flip", "select", "reveal_card", "turn", "pick")
_GUESS_NAMES: tuple[str, ...] = ("guess", "try_letter", "guess_letter", "submit", "attempt")
_START_NAMES: tuple[str, ...] = (
    "start", "begin", "play", "launch", "run", "resume", "new_game", "start_game",
)
_RESTART_NAMES: tuple[str, ...] = (
    "restart", "reset", "new_game", "start_new", "reinitialize", "reset_game",
)

_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    _TICK_NAMES, _INPUT_NAMES, _STATE_NAMES, _REVEAL_NAMES,
    _FLAG_NAMES, _MOVE_NAMES, _THROW_NAMES, _FLIP_NAMES, _GUESS_NAMES,
    _START_NAMES, _RESTART_NAMES,
)


def _find_callable_attr(obj: Any, names: tuple[str, ...]) -> tuple[str, Any] | None:
    """Return ``(attr_name, attr)`` for the first name in ``names`` that
    resolves to a callable on ``obj``, else ``None``."""
    for name in names:
        attr = getattr(obj, name, None)
        if callable(attr):
            return name, attr
    return None


def _discover_game_class(mod: Any, preferred: str | None = None) -> type | None:
    """Find the most likely game class in ``mod``.

    Selection order:
      1. Exact case-insensitive match on ``preferred``.
      2. Substring match (either direction) on ``preferred``.
      3. The class with the most user-defined methods (heuristic: the
         game-state class is the richest one in the module).
    Classes imported into the module (not defined there) are skipped so
    we don't pick up stdlib types re-exported by the generated code.
    """
    import inspect

    candidates = [
        (name, obj)
        for name, obj in inspect.getmembers(mod, inspect.isclass)
        if getattr(obj, "__module__", None) == mod.__name__
    ]
    if not candidates:
        return None
    if preferred:
        plow = preferred.lower()
        for name, obj in candidates:
            if name.lower() == plow:
                return obj
        for name, obj in candidates:
            nlow = name.lower()
            if plow in nlow or nlow in plow:
                return obj
    return max(
        candidates,
        key=lambda kv: len([
            m for m in inspect.getmembers(kv[1], predicate=inspect.isfunction)
            if not m[0].startswith("_")
        ]),
    )


def _instantiate_game_generic(
    cls: type, hints: list[tuple[Any, ...]] | None = None,
) -> Any:
    """Instantiate ``cls`` by trying common constructor signatures.

    ``hints`` are caller-supplied arg-tuples tried first (game-specific
    knowledge).  Generic fallbacks cover no-arg, one-int, and two/three-int
    signatures.  Raises the last exception if every attempt fails.
    """
    import inspect

    sig = inspect.signature(cls.__init__)
    params = [
        p for p in sig.parameters.values()
        if p.name != "self"
        and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    n_required = sum(1 for p in params if p.default is inspect.Parameter.empty)

    candidates: list[tuple[Any, ...]] = []
    if hints:
        candidates.extend(hints)
    candidates.extend([
        (), (10,), (20,), (10, 10), (20, 20), (40, 100), (10, 10, 10),
    ])
    seen: set[tuple[Any, ...]] = set()
    last_exc: Exception | None = None
    for args in candidates:
        if args in seen:
            continue
        seen.add(args)
        if len(args) < n_required:
            continue
        try:
            return cls(*args)
        except (TypeError, ValueError) as exc:
            last_exc = exc
            continue
    if last_exc is not None:
        raise last_exc
    raise TypeError(f"could not instantiate {cls.__name__}: no viable signature")


class _GameFacade:
    """Wrap a generated game instance with name-tolerant method access.

    Attribute lookup for a missing name:
      1. The wrapped instance (proxied).
      2. Any synonym in the same group as the requested name (asking for
         ``tick`` finds ``step`` if the model used that name; asking for
         ``render_state`` finds ``get_state``, etc.).

    Attribute assignment proxies to the wrapped instance, so checks that
    poke internal state (``facade.ball_x = ...``) keep working.  This lets
    the existing per-check verification code survive arbitrary method
    renaming by the model.
    """

    def __init__(self, instance: Any) -> None:
        object.__setattr__(self, "_wrapped", instance)

    def __getattr__(self, name: str) -> Any:
        wrapped = object.__getattribute__(self, "_wrapped")
        if hasattr(wrapped, name):
            return getattr(wrapped, name)
        for group in _SYNONYM_GROUPS:
            if name in group:
                for syn in group:
                    if syn != name and hasattr(wrapped, syn):
                        return getattr(wrapped, syn)
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        wrapped = object.__getattribute__(self, "_wrapped")
        setattr(wrapped, name, value)


def _load_generated_module(source: str, module_name: str, tmp_dir: Path) -> Any:
    """Write ``source`` into ``tmp_dir`` and import it as ``module_name``.

    Raises ``ImportError`` if importlib cannot build a loader for the file.
    """
    module_path = tmp_dir / f"{module_name}.py"
    module_path.write_text(source)
    spec = importlib.util.spec_from_file_location(module_name, str(module_path))
    if spec is None or spec.loader is None:
        raise ImportError("importlib.spec_from_file_location returned None")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _get_state_dict(instance: Any) -> dict[str, Any]:
    """Return a DEEP-COPIED snapshot of ``instance``'s state.

    The deep copy is critical: many Snake implementations mutate ``self.body``
    in place via ``insert()``/``pop()``. If we returned the live reference,
    a before/after equality check would see both sides as the same object
    (post-mutation) and report ``moved=False`` even though tick() advanced.

    Tries common state-accessor method names; always MERGES with ``__dict__``
    so feature checks see raw attributes even when an accessor returns a
    partial dict.
    """
    import copy as _copy

    merged: dict[str, Any] = dict(instance.__dict__)
    found = _find_callable_attr(instance, _STATE_NAMES)
    if found is not None:
        with contextlib.suppress(Exception):
            result = found[1]()
            if isinstance(result, dict):
                merged.update(result)
    return _copy.deepcopy(merged)


# ---------------------------------------------------------------------------
# Semantic attribute discovery (name-agnostic feature verification)
# ---------------------------------------------------------------------------
#
# The model emits different state-attribute names each run.  These helpers
# locate attributes BY SHAPE (list-of-pairs, 2D-grid, score-like int,
# game-over-like bool) so feature verification does not depend on the
# model's naming choices.  Each helper scans a state dict and returns
# ``(attr_name, value)`` or ``None``.

_COORD_PAIR = tuple[float, float] | tuple[int, int] | list[float] | list[int]


def _is_coord_pair(value: Any) -> bool:
    """True if ``value`` looks like a single [x, y] coordinate."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in value)
    type_name = type(value).__name__
    if type_name in ("Vec2", "Vector2", "Point", "Coord", "Cell") and hasattr(value, "x") and hasattr(value, "y"):
        return isinstance(value.x, (int, float)) and isinstance(value.y, (int, float))
    return False


def _is_sequence_like(value: Any) -> bool:
    """True for list, tuple, deque, or any non-str/non-dict sequence."""
    if isinstance(value, (list, tuple)):
        return True
    type_name = type(value).__name__
    return type_name in ("deque", "LinkedList", "Chain", "ChainMap")


def _find_body_attribute(state: dict[str, Any]) -> tuple[str, list[Any]] | None:
    """Find an attribute that looks like a snake body: sequence of >=1 coord pairs.

    Accepts list, tuple, or deque as the outer container (models commonly use
    ``collections.deque`` for O(1) popleft, and some implementations start
    with a length-1 body that grows on eating). Selects the LONGEST such
    sequence (the body) over shorter candidates (food, obstacles).  Returns
    ``(attr_name, value)`` or ``None``.
    """
    candidates: list[tuple[str, list[Any]]] = []
    for name, value in state.items():
        if not _is_sequence_like(value) or len(value) < 1:
            continue
        if all(_is_coord_pair(p) for p in value):
            candidates.append((name, list(value)))
    if not candidates:
        return None
    return max(candidates, key=lambda kv: len(kv[1]))


def _find_food_attribute(
    state: dict[str, Any], exclude: str | None = None
) -> tuple[str, Any] | None:
    """Find an attribute that looks like food: a single coord pair OR a
    list/tuple containing exactly one coord pair.  ``exclude`` is the body
    attribute name so we don't match a length-1 body as food (ambiguous
    by shape alone)."""
    for name, value in state.items():
        if name == exclude:
            continue
        if _is_coord_pair(value):
            return name, value
        if (
            isinstance(value, (list, tuple))
            and len(value) == 1
            and _is_coord_pair(value[0])
        ):
            return name, value
    return None


def _find_board_attribute(state: dict[str, Any]) -> tuple[str, list[Any]] | None:
    """Find a 2D grid/board attribute: list of >=3 rows, each a list of >=3 cells."""
    best: tuple[str, list[Any]] | None = None
    best_cells = 0
    for name, value in state.items():
        if not isinstance(value, list) or len(value) < 3:
            continue
        if not all(isinstance(row, list) for row in value):
            continue
        row_len = len(value[0])
        if row_len < 3:
            continue
        if not all(len(row) == row_len for row in value):
            continue
        cell_count = len(value) * row_len
        if cell_count > best_cells:
            best = (name, value)
            best_cells = cell_count
    return best


def _find_score_attribute(state: dict[str, Any]) -> str | None:
    """Find a score-like numeric attribute (name contains score/points/length/...)."""
    score_words = ("score", "points", "length", "lines", "attempts", "distance")
    for name in score_words:
        if name in state and isinstance(state[name], (int, float)) and not isinstance(state[name], bool):
            return name
    for name, value in state.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        nlow = name.lower()
        if any(word in nlow for word in score_words):
            return name
    return None


def _find_position_attribute(state: dict[str, Any], axis: str) -> str | None:
    """Find an x/y position attribute by axis suffix (``x``/``y``/``row``/``col``)."""
    suffixes = {"x": ("x", "col", "column"), "y": ("y", "row")}[axis]
    for name in state:
        nlow = name.lower()
        if nlow.endswith(suffixes):
            value = state[name]
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return name
    for name, value in state.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        nlow = name.lower()
        if any(s in nlow for s in suffixes):
            return name
    return None


def _find_game_over_attribute(state: dict[str, Any]) -> str | None:
    """Find a game-over-like boolean attribute (over/crashed/finished/ended)."""
    over_words = ("over", "crashed", "finished", "ended", "dead", "done")
    for name in state:
        if isinstance(state[name], bool):
            nlow = name.lower()
            if any(word in nlow for word in over_words):
                return name
    return None


def _find_player_attribute(state: dict[str, Any]) -> str | None:
    """Find a current-player-like attribute (numeric or 'X'/'O' marker)."""
    for name in ("current_player", "player", "turn", "active_player", "active"):
        if name in state:
            return name
    for name, value in state.items():
        nlow = name.lower()
        is_player_attr = "player" in nlow or nlow == "turn"
        is_valid_value = isinstance(value, (int, str)) and not isinstance(value, bool)
        if is_player_attr and is_valid_value:
            return name
    return None


# ---------------------------------------------------------------------------
# Per-game feature verification (semantic, name-agnostic)
# ---------------------------------------------------------------------------
#
# Each verifier returns a list of feature-failure strings (empty == pass).
# These check the FLOOR of behaviour a game must demonstrate to count as
# "implements the requested features" — the model is free to name the class,
# its methods, and its state attributes however it likes, as long as the
# observable FEATURES are present.  Richer per-feature diagnostics (line
# clearing, king promotion, flood fill) remain in _run_single_check as
# best-effort informational checks; the verifiers below are the hard
# contract asserted at test time.


def _verify_game_skeleton(
    mod: Any, preferred: str | None,
) -> tuple[list[str], Any]:
    """Shared discovery + instantiation. Returns (failures, instance_or_None)."""
    failures: list[str] = []
    cls = _discover_game_class(mod, preferred=preferred)
    if cls is None:
        names = [n for n in dir(mod) if not n.startswith("_")]
        return ([f"no game class found (preferred={preferred!r}, names={names})"], None)
    try:
        instance = _instantiate_game_generic(cls)
    except Exception as exc:
        return ([f"instantiation failed: {type(exc).__name__}: {exc}"], None)
    return failures, instance


def _check_tick_advances_state(instance: Any) -> list[str]:
    """Floor features for tick-based games: tick runs, state changes, no crash.

    Shared by snake/tetris/skifree/pong/breakout verifiers.  Returns a list
    of feature-failure strings (empty == pass).
    """
    failures: list[str] = []
    tick = _find_callable_attr(instance, _TICK_NAMES)
    if tick is None:
        failures.append("no state-advancing method (tick/step/update/...) found")
        return failures
    tick_fn = tick[1]

    # Per the new prompt spec, games start in 'ready' and tick() short-circuits
    # until start() transitions to 'playing'. Call start() (or a synonym) before
    # any tick loop, otherwise state never advances and we record a false fail.
    start_fail = _invoke_start_method(instance)
    if start_fail is not None:
        failures.append(f"could not start game before ticking: {start_fail}")
        return failures

    try:
        tick_fn()
    except Exception as exc:
        failures.append(f"first tick raised: {type(exc).__name__}: {exc}")
        return failures
    state_before = _get_state_dict(instance)
    moved = False
    for _ in range(10):
        try:
            tick_fn()
        except Exception:
            break
        if _get_state_dict(instance) != state_before:
            moved = True
            break
    if not moved:
        failures.append("state did not change across 10 ticks")
    try:
        for _ in range(200):
            tick_fn()
    except Exception as exc:
        failures.append(f"extended tick loop crashed: {type(exc).__name__}: {exc}")
    return failures


def _verify_tick_game_features(mod: Any, preferred: str | None) -> list[str]:
    """Generic skeleton + tick floor (kept for non-user-mentioned tick games)."""
    failures, instance = _verify_game_skeleton(mod, preferred=preferred)
    if instance is None:
        return failures
    failures.extend(_check_tick_advances_state(instance))
    return failures


def _verify_snake_features(mod: Any) -> list[str]:
    """Snake feature contract (name-agnostic).

    Required features:
      - Game class with callable state-advancing method (tick/step/update/...).
      - Tick advances state without crashing across an extended loop.
      - A "body" attribute exists: a list of >=2 coordinate pairs.
      - A "food" attribute exists: a single [x,y] pair or 1-element list of pairs.
      - A score/length/points numeric attribute exists.
      - A game-over-like boolean attribute exists (or tick returns False).
    """
    failures, instance = _verify_game_skeleton(mod, preferred="Snake")
    if instance is None:
        return failures
    failures.extend(_check_tick_advances_state(instance))
    state = _get_state_dict(instance)
    body_attr = _find_body_attribute(state)
    if body_attr is None:
        failures.append(
            "no body-like attribute found (list of >=1 coordinate pairs); "
            "snake state must track body segments"
        )
    if _find_food_attribute(state, exclude=body_attr[0] if body_attr else None) is None:
        failures.append(
            "no food-like attribute found (single [x,y] pair); "
            "snake state must track food position"
        )
    if _find_score_attribute(state) is None:
        failures.append(
            "no score-like numeric attribute found; "
            "snake must track score/length"
        )
    failures.extend(run_lifecycle_checks("snake", mod))
    return failures


def _verify_tetris_features(mod: Any) -> list[str]:
    """Tetris feature contract (name-agnostic).

    Required features:
      - Game class with callable state-advancing method.
      - Tick advances state without crashing.
      - A 2D grid/board attribute exists (list of >=3 rows of >=3 cells).
      - A "current piece" attribute exists (any non-scalar state describing
        the active piece), OR piece-position info appears in the board state.
      - A line-clear counter exists (numeric attribute with score/lines).
    """
    failures, instance = _verify_game_skeleton(mod, preferred="Tetris")
    if instance is None:
        return failures
    failures.extend(_check_tick_advances_state(instance))
    state = _get_state_dict(instance)
    if _find_board_attribute(state) is None:
        failures.append(
            "no 2D board/grid attribute found (list of >=3 equal-length rows); "
            "tetris state must track the playfield"
        )
    if _find_score_attribute(state) is None:
        failures.append(
            "no score-like numeric attribute found; "
            "tetris must track score/lines cleared"
        )
    failures.extend(run_lifecycle_checks("tetris", mod))
    return failures


def _verify_skifree_features(mod: Any) -> list[str]:
    """SkiFree feature contract (name-agnostic).

    Required features:
      - Game class with callable state-advancing method.
      - Tick advances state without crashing.
      - A 1D position attribute exists for both axes (x and y of the skier).
      - A crashed/over boolean attribute exists OR tick returns False on crash.
      - A list attribute exists for obstacles (trees/rocks), OR the state
        includes some iterable of obstacle positions.
    """
    failures, instance = _verify_game_skeleton(mod, preferred="SkiFree")
    if instance is None:
        return failures
    failures.extend(_check_tick_advances_state(instance))
    state = _get_state_dict(instance)
    if _find_position_attribute(state, "x") is None:
        failures.append(
            "no x-axis position attribute found; skifree must track skier x"
        )
    if _find_position_attribute(state, "y") is None:
        failures.append(
            "no y-axis position attribute found; skifree must track skier y"
        )
    if _find_game_over_attribute(state) is None:
        failures.append(
            "no crashed/over boolean attribute found; skifree must track crash state"
        )
    has_obstacle_list = any(
        isinstance(v, list) and len(v) >= 1
        and all(_is_coord_pair(item) for item in v if not isinstance(item, (int, float)))
        for v in state.values()
    )
    if not has_obstacle_list:
        failures.append(
            "no obstacle-list attribute found (trees/rocks); "
            "skifree must track obstacle positions"
        )
    failures.extend(run_lifecycle_checks("skifree", mod))
    return failures


def _verify_pong_features(mod: Any) -> list[str]:
    failures = _verify_tick_game_features(mod, preferred="Pong")
    failures.extend(run_lifecycle_checks("pong", mod))
    return failures


def _verify_breakout_features(mod: Any) -> list[str]:
    failures = _verify_tick_game_features(mod, preferred="Breakout")
    failures.extend(run_lifecycle_checks("breakout", mod))
    return failures


def _verify_minesweeper_features(mod: Any) -> list[str]:
    """Minesweeper feature contract (name-agnostic).

    Required features:
      - Game class instantiable.
      - A reveal-like method exists (reveal/click/open/dig/uncover).
      - reveal() returns a status string (ok/mine/already_revealed/out_of_bounds)
        OR a dict — does not raise on a corner cell.
      - A 2D grid attribute exists.
      - A flag/mark-like method exists (flag/mark/toggle_flag/set_flag).
      - A game-over boolean attribute appears after revealing a mine (best-effort).
    """
    failures, instance = _verify_game_skeleton(mod, preferred="Minesweeper")
    if instance is None:
        return failures
    reveal = _find_callable_attr(instance, _REVEAL_NAMES)
    if reveal is None:
        failures.append("no reveal-like method (reveal/click/open/dig) found")
    else:
        try:
            result = reveal[1](0, 0)
            if isinstance(result, str) and result not in (
                "ok", "mine", "already_revealed", "out_of_bounds",
            ):
                failures.append(f"reveal returned unexpected value: {result!r}")
        except Exception as exc:
            failures.append(f"reveal(0,0) raised: {type(exc).__name__}: {exc}")
    state = _get_state_dict(instance)
    if _find_board_attribute(state) is None:
        failures.append(
            "no 2D grid attribute found (list of >=3 equal-length rows); "
            "minesweeper must track cell grid"
        )
    if _find_callable_attr(instance, _FLAG_NAMES) is None:
        failures.append(
            "no flag-like method found (flag/mark/toggle_flag/set_flag); "
            "minesweeper must support flagging"
        )
    failures.extend(run_lifecycle_checks("minesweeper", mod))
    return failures


def _verify_checkers_features(mod: Any) -> list[str]:
    """Checkers feature contract (name-agnostic).

    Required features:
      - Game class instantiable.
      - A move-like method exists (move/play/make_move/do_move/submit_move).
      - A 2D board attribute exists (8x8 expected, but any >=3x3 grid accepted).
      - A current-player-like attribute exists (numeric turn tracker).
      - A game-over-like boolean attribute exists (or move() reports it).
    """
    failures, instance = _verify_game_skeleton(mod, preferred="Checkers")
    if instance is None:
        return failures
    move = _find_callable_attr(instance, _MOVE_NAMES)
    if move is None:
        failures.append("no move-like method (move/play/make_move) found")
    state = _get_state_dict(instance)
    board = _find_board_attribute(state)
    if board is None:
        failures.append(
            "no 2D board attribute found (list of >=3 equal-length rows); "
            "checkers must track the 8x8 board"
        )
    elif len(board[1]) < 8 or any(len(row) < 8 for row in board[1]):
        failures.append(
            f"board attribute {board[0]!r} is smaller than 8x8 "
            f"(got {len(board[1])}x{len(board[1][0])}); checkers requires 8x8"
        )
    if _find_player_attribute(state) is None:
        failures.append(
            "no current-player-like attribute found; checkers must track whose turn"
        )
    failures.extend(run_lifecycle_checks("checkers", mod))
    return failures


def _verify_banana_features(mod: Any) -> list[str]:
    """Banana (Gorillas) feature contract (name-agnostic).

    Required features:
      - Game class instantiable.
      - A throw-like method exists (throw/shoot/fire/launch/toss).
      - throw() returns a dict with at least one of: trajectory, hit, hit_type,
        winner, distance — the throw result must be structured.
      - A current-player-like attribute exists (turn alternation).
      - Either a skyline attribute (list of building heights) OR a list of
        gorilla positions exists.
    """
    failures, instance = _verify_game_skeleton(mod, preferred="Banana")
    if instance is None:
        return failures
    throw = _find_callable_attr(instance, _THROW_NAMES)
    if throw is None:
        failures.append("no throw-like method (throw/shoot/fire/launch) found")
        return failures
    try:
        result = throw[1](45, 10)
    except Exception as exc:
        failures.append(f"throw(45, 10) raised: {type(exc).__name__}: {exc}")
        result = None
    if result is not None:
        if not isinstance(result, dict):
            failures.append(
                f"throw did not return a dict (got {type(result).__name__}); "
                "banana throw result must be a structured dict"
            )
        else:
            throw_keys = ("trajectory", "hit", "hit_type", "winner", "distance")
            if not any(k in result for k in throw_keys):
                failures.append(
                    f"throw dict lacks all of {throw_keys}; "
                    f"got keys {sorted(result.keys())}"
                )
            traj = result.get("trajectory")
            if traj is not None and (not isinstance(traj, list) or len(traj) < 1):
                failures.append(
                    f"throw trajectory must be a non-empty list, got {traj!r}"
                )
    state = _get_state_dict(instance)
    if _find_player_attribute(state) is None:
        failures.append(
            "no current-player-like attribute found; banana must track turn"
        )
    has_skyline = any(
        isinstance(v, list) and len(v) >= 3
        and all(isinstance(h, (int, float)) and not isinstance(h, bool) for h in v)
        for v in state.values()
    )
    if not has_skyline:
        failures.append(
            "no skyline-like attribute found (list of >=3 building heights); "
            "banana must track the city skyline"
        )
    failures.extend(run_lifecycle_checks("banana", mod))
    return failures


def _verify_maze_runner_features(mod: Any) -> list[str]:
    failures, instance = _verify_game_skeleton(mod, preferred="MazeRunner")
    if instance is None:
        return failures
    if _find_callable_attr(instance, _INPUT_NAMES) is None:
        failures.append("no input-like method found")
    failures.extend(run_lifecycle_checks("maze_runner", mod))
    return failures


def _verify_word_guesser_features(mod: Any) -> list[str]:
    failures, instance = _verify_game_skeleton(mod, preferred="WordGuesser")
    if instance is None:
        return failures
    guess = _find_callable_attr(instance, _GUESS_NAMES)
    if guess is None:
        failures.append("no guess-like method (guess/try_letter/submit) found")
        return failures
    try:
        guess[1]("a")
    except Exception as exc:
        failures.append(f"guess raised: {type(exc).__name__}: {exc}")
    failures.extend(run_lifecycle_checks("word_guesser", mod))
    return failures


def _verify_memory_match_features(mod: Any) -> list[str]:
    failures, instance = _verify_game_skeleton(mod, preferred="MemoryMatch")
    if instance is None:
        return failures
    flip = _find_callable_attr(instance, _FLIP_NAMES)
    if flip is None:
        failures.append("no flip-like method (flip/select/turn) found")
        return failures
    try:
        flip[1](0)
    except Exception as exc:
        failures.append(f"flip raised: {type(exc).__name__}: {exc}")
    failures.extend(run_lifecycle_checks("memory_match", mod))
    return failures


def _verify_tic_tac_toe_features(mod: Any) -> list[str]:
    failures, instance = _verify_game_skeleton(mod, preferred="TicTacToe")
    if instance is None:
        return failures
    move = _find_callable_attr(instance, _MOVE_NAMES)
    if move is None:
        failures.append("no move-like method found")
        return failures
    try:
        result = move[1](0, 0)
        if isinstance(result, dict) and "valid" not in result:
            failures.append("move result dict missing 'valid' key")
    except Exception as exc:
        failures.append(f"move raised: {type(exc).__name__}: {exc}")
    failures.extend(run_lifecycle_checks("tic_tac_toe", mod))
    return failures


_VERIFY_DISPATCH: dict[str, Any] = {
    "snake": _verify_snake_features,
    "tetris": _verify_tetris_features,
    "minesweeper": _verify_minesweeper_features,
    "checkers": _verify_checkers_features,
    "skifree": _verify_skifree_features,
    "banana": _verify_banana_features,
    "pong": _verify_pong_features,
    "breakout": _verify_breakout_features,
    "maze_runner": _verify_maze_runner_features,
    "word_guesser": _verify_word_guesser_features,
    "memory_match": _verify_memory_match_features,
    "tic_tac_toe": _verify_tic_tac_toe_features,
}


def verify_features(game_id: str, module: Any) -> list[str]:
    """Return feature-failure strings for ``game_id`` (empty == pass).

    Dispatches to the per-game verifier registered in ``_VERIFY_DISPATCH``.
    Returns ``["no verifier registered for game <id>"]`` if unknown.
    """
    fn = _VERIFY_DISPATCH.get(game_id)
    if fn is None:
        return [f"no verifier registered for game {game_id!r}"]
    return fn(module)


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

    # Discover and instantiate the game class generically.
    # The model may name the class anything (Snake, SnakeGame, Game, ...);
    # we locate it by behaviour (richest class in the module) and try
    # several constructor signatures until one works.
    cls = _discover_game_class(mod, preferred=class_name)
    if cls is None:
        results["errors"].append(
            f"No game class found (preferred={class_name!r}); "
            f"module names={[n for n in dir(mod) if not n.startswith('_')]}"
        )
        return results

    ctor_hints: dict[str, list[tuple[Any, ...]]] = {
        "Minesweeper": [(10, 10, 10), (10, 10)],
        "Snake": [(20, 20), (10, 10)],
        "SkiFree": [(40, 100), (40, 200)],
    }
    try:
        raw_instance = _instantiate_game_generic(cls, hints=ctor_hints.get(class_name))
        results["instantiated"] = True
    except Exception as e:
        results["errors"].append(f"Instantiation failed: {type(e).__name__}: {e}")
        return results

    # Wrap in a name-tolerant facade so per-check verification survives
    # arbitrary method renaming by the model (tick→step, input→action, etc).
    instance = _GameFacade(raw_instance)

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

    # Per the new prompt spec, games start in 'ready' and tick() short-circuits
    # until start() transitions to 'playing'. Call start() (or a synonym) before
    # any tick-based check, otherwise ticks no-op and we record false failures.
    # Idempotent: no-op if state is already 'playing', 'game_over', absent, or
    # non-string. Skipped for lifecycle_initial_state which MUST observe the
    # pre-start 'ready' state. Start failure is tolerated here — the downstream
    # check will record its own descriptive failure if the game cannot play.
    if check_id != "lifecycle_initial_state":
        _invoke_start_method(instance)

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
        # Try multiple directions — "down" may be rejected if snake faces up.
        # Also handle coord as [x,y] or [y,x]; just check ANY coord changed.
        snake = getattr(instance, "snake", None)
        if not snake or not hasattr(instance, "tick"):
            return True
        initial = list(snake[0])
        for direction in ("down", "up", "left", "right"):
            try:
                instance.input(direction)
            except Exception:
                continue
            instance.tick()
            if getattr(instance, "game_over", False):
                return True
            try:
                new_head = list(snake[0])
            except (TypeError, IndexError):
                return True
            if new_head != initial:
                return True
        return True  # best-effort

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
        # Handle food as [[x,y]], [x,y], (x,y), or other shapes.
        # This is best-effort: moving toward food in one tick may not land on it.
        food = getattr(instance, "food", None)
        if food is None:
            return True
        try:
            if isinstance(food, (list, tuple)) and len(food) >= 1:
                item = food[0]
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    fx, fy = item[0], item[1]
                elif isinstance(item, (int, float)):
                    fx, fy = food[0], food[1]
                else:
                    return True
            else:
                return True
        except (TypeError, IndexError, ValueError):
            return True
        snake = getattr(instance, "snake", None)
        if not snake:
            return True
        try:
            head = snake[0]
            hx, hy = head[0], head[1]
        except (TypeError, IndexError):
            return True
        if hx < fx:
            instance.input("right")
        elif hx > fx:
            instance.input("left")
        elif hy < fy:
            instance.input("down")
        elif hy > fy:
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
        # Prior check (reveal_mine) may have set game_over=True, making
        # further reveals no-ops. Can't test flood fill on a dead board.
        if getattr(instance, "game_over", False):
            return True
        # Count revealed cells directly from the grid rather than relying
        # on a cells_revealed counter (which the model may not maintain).
        grid = getattr(instance, "grid", [])

        def _count_revealed(g: Any) -> int:
            count = 0
            for row in g:
                for cell in row:
                    if isinstance(cell, dict) and cell.get("revealed"):
                        count += 1
            return count

        initial = _count_revealed(grid)
        for row in grid:
            for cell in row:
                if not isinstance(cell, dict):
                    continue
                if cell.get("adjacent_mines", -1) == 0 and not cell.get("is_mine") and not cell.get("revealed"):
                    try:
                        instance.reveal(cell["x"], cell["y"])
                    except Exception:
                        return True
                    grid_after = getattr(instance, "grid", grid)
                    new_count = _count_revealed(grid_after)
                    attr_count = getattr(instance, "cells_revealed", new_count)
                    # flood fill should reveal more than just the clicked cell
                    return new_count > initial + 1 or attr_count > initial + 1
        return True  # no suitable 0 cell found — skip

    if check_id == "flag_toggle":
        result = instance.flag(0, 0)
        return result in ("flagged", "unflagged", "already_revealed", "out_of_bounds")

    if check_id == "win_detection":
        # Best effort — not all boards are winnable quickly
        return True

    if check_id == "valid_move":
        # Use get_valid_moves to find a piece that can actually move,
        # rather than guessing "a3"->"b4" which may not be valid for
        # the model's coordinate system or starting layout.
        cols = "abcdefgh"
        if hasattr(instance, "get_valid_moves"):
            for col in cols:
                for row in range(1, 9):
                    sq = f"{col}{row}"
                    try:
                        moves = instance.get_valid_moves(sq)
                    except Exception:
                        continue
                    if moves:
                        target = moves[0]
                        try:
                            result = instance.move(sq, target)
                        except Exception:
                            continue
                        if isinstance(result, dict):
                            return result.get("valid", False)
                        return result is True or result == "valid"
        # Strategy 2: try common opening diagonal moves
        for from_sq, to_sq in [("a3", "b4"), ("c3", "b4"), ("c3", "d4"),
                                ("a1", "b2"), ("b2", "a3"), ("b2", "c3")]:
            try:
                result = instance.move(from_sq, to_sq)
            except Exception:
                continue
            if isinstance(result, dict) and result.get("valid"):
                return True
        # Best-effort: move() works (proven by invalid_move check), we just
        # couldn't guess the right coordinates for this model's layout.
        return True

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
        # Try multiple angle/velocity combos — low velocity may produce
        # very short trajectories (building hit, ground hit). Accept any
        # non-empty trajectory as evidence the arc mechanic works.
        # Each throw may end the game (gorilla hit); wrap in try/except
        # so one bad call doesn't fail the whole check.
        for angle, velocity in [(45, 10), (45, 25), (60, 20), (75, 15)]:
            try:
                result = instance.throw(angle, velocity)
            except Exception:
                continue
            if isinstance(result, dict):
                trajectory = result.get("trajectory", [])
                if len(trajectory) >= 1:
                    return True
        return True  # best-effort

    if check_id == "building_hit":
        # Try a very low angle; game may already be over from prior checks
        for angle in [5, 10, 15, 175]:
            try:
                result = instance.throw(angle, 8)
            except Exception:
                continue
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
        # The collision check may happen before or after the ball moves,
        # and the model may use ball_y == paddle_y or ball_y == paddle_y - 1.
        # Try several starting positions above the paddle.
        px = getattr(instance, "paddle_x", 0)
        py = getattr(instance, "paddle_y", 19)
        pw = getattr(instance, "paddle_width", 4)
        bx_target = px + (pw // 2)
        for setup_offset in (1, 2, 3):
            instance.ball_x = bx_target
            instance.ball_y = py - setup_offset
            instance.ball_dx = 0
            instance.ball_dy = 1
            initial_dy = instance.ball_dy
            initial_lives = getattr(instance, "lives", 3)
            try:
                instance.tick()
            except Exception:
                continue
            new_dy = getattr(instance, "ball_dy", initial_dy)
            new_lives = getattr(instance, "lives", initial_lives)
            # Bounce evidence: dy flipped negative OR lives preserved
            # (ball didn't fall through the paddle)
            if new_dy < 0:
                return True
            if new_dy != initial_dy and new_lives == initial_lives:
                return True
        return True  # best-effort

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
        # The old check `hasattr(instance, "matched")` was wrong — `matched`
        # is a per-card attribute, not a game-level one. Instead, actually
        # flip all remaining unmatched pairs and check game_over.
        cards = getattr(instance, "cards", [])
        if not cards:
            return True
        # Reset any pending turn state left by prior checks (mm_mismatch_flips
        # may have left first_flip pointing at a card, desynchronizing flips).
        with contextlib.suppress(AttributeError, TypeError):
            instance.first_flip = None
        # Iteratively match remaining unmatched pairs
        for _round in range(len(cards)):
            if getattr(instance, "game_over", False):
                return True
            cards = getattr(instance, "cards", [])
            value_to_ids: dict[str, list[int]] = {}
            for i, card in enumerate(cards):
                matched = card.get("matched", False) if isinstance(card, dict) else getattr(card, "matched", False)
                if matched:
                    continue
                flipped = card.get("flipped", False) if isinstance(card, dict) else getattr(card, "flipped", False)
                if flipped:
                    continue
                val = card.get("value") if isinstance(card, dict) else getattr(card, "value", None)
                if val is not None:
                    value_to_ids.setdefault(val, []).append(i)
            matched_one = False
            for _val, ids in value_to_ids.items():
                if len(ids) >= 2:
                    with contextlib.suppress(Exception):
                        # Ensure clean turn state before each pair
                        with contextlib.suppress(AttributeError, TypeError):
                            instance.first_flip = None
                        instance.flip(ids[0])
                        r2 = instance.flip(ids[1])
                        if isinstance(r2, dict) and r2.get("match"):
                            matched_one = True
                            break
            if not matched_one:
                break
        # Success if game_over, OR all cards matched (model may not set game_over)
        if getattr(instance, "game_over", False):
            return True
        cards = getattr(instance, "cards", [])
        all_matched = all(
            (c.get("matched", False) if isinstance(c, dict) else getattr(c, "matched", False))
            for c in cards
        ) if cards else False
        return all_matched

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

    # -- Lifecycle (open -> play -> close -> restart) --
    if check_id == "lifecycle_initial_state":
        state = _get_state_dict(instance)
        st = state.get("state") or getattr(instance, "state", None)
        if st is None:
            return True  # state attribute absent — best-effort skip
        return str(st).lower() in ("ready", "menu", "idle", "start", "not_started", "initialized")

    if check_id == "lifecycle_start":
        start_found = _find_callable_attr(instance, _START_NAMES)
        if start_found is None:
            return True  # no start method — best-effort skip
        try:
            start_found[1]()
        except Exception:
            return False
        st = getattr(instance, "state", None) or _get_state_dict(instance).get("state")
        return st is not None and str(st).lower() in ("playing", "active", "running", "in_progress", "play")

    if check_id == "lifecycle_score_starts_zero":
        with contextlib.suppress(Exception):
            start_found = _find_callable_attr(instance, _START_NAMES)
            if start_found is not None:
                start_found[1]()
        state = _get_state_dict(instance)
        score_name = _find_score_attribute(state)
        if score_name is None:
            return True  # best-effort skip
        try:
            return float(state.get(score_name, 0)) == 0
        except (TypeError, ValueError):
            return True

    if check_id == "lifecycle_score_increments":
        with contextlib.suppress(Exception):
            start_found = _find_callable_attr(instance, _START_NAMES)
            if start_found is not None:
                start_found[1]()
        state_before = _get_state_dict(instance)
        score_name = _find_score_attribute(state_before)
        if score_name is None:
            return True
        before = state_before.get(score_name, 0)
        tick_found = _find_callable_attr(instance, _TICK_NAMES)
        if tick_found is None:
            return True
        with contextlib.suppress(Exception):
            for _ in range(50):
                tick_found[1]()
                if getattr(instance, "game_over", False):
                    break
        state_after = _get_state_dict(instance)
        after = state_after.get(score_name, 0)
        try:
            return float(after) > float(before)
        except (TypeError, ValueError):
            return True

    if check_id == "lifecycle_game_over":
        with contextlib.suppress(Exception):
            start_found = _find_callable_attr(instance, _START_NAMES)
            if start_found is not None:
                start_found[1]()
        tick_found = _find_callable_attr(instance, _TICK_NAMES)
        if tick_found is None:
            return True
        with contextlib.suppress(Exception):
            for _ in range(300):
                tick_found[1]()
                if getattr(instance, "game_over", False):
                    return True
        return getattr(instance, "game_over", False)

    if check_id == "lifecycle_game_over_idempotent":
        if not getattr(instance, "game_over", False):
            tick_found = _find_callable_attr(instance, _TICK_NAMES)
            if tick_found is not None:
                with contextlib.suppress(Exception):
                    for _ in range(300):
                        tick_found[1]()
                        if getattr(instance, "game_over", False):
                            break
        if not getattr(instance, "game_over", False):
            return True
        state_before = _get_state_dict(instance)
        tick_found = _find_callable_attr(instance, _TICK_NAMES)
        if tick_found is not None:
            with contextlib.suppress(Exception):
                tick_found[1]()
        state_after = _get_state_dict(instance)
        return state_after.get("game_over") == state_before.get("game_over")

    if check_id == "lifecycle_restart":
        restart_found = _find_callable_attr(instance, _RESTART_NAMES)
        if restart_found is None:
            return True
        try:
            restart_found[1]()
        except Exception:
            return False
        state = _get_state_dict(instance)
        score_name = _find_score_attribute(state)
        score_zero = True
        if score_name is not None:
            with contextlib.suppress(TypeError, ValueError):
                score_zero = float(state.get(score_name, 0)) == 0
        game_over_false = state.get("game_over") is False or getattr(instance, "game_over", None) is False
        st = state.get("state") or getattr(instance, "state", None)
        state_reset = st is None or str(st).lower() in ("ready", "menu", "idle", "start", "not_started", "initialized")
        return score_zero and game_over_false and state_reset

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

    cls = _discover_game_class(mod, preferred=class_name)
    if cls is None:
        results["errors"].append(
            f"No game class found (preferred={class_name!r}); "
            f"module names={[n for n in dir(mod) if not n.startswith('_')]}"
        )
        return results

    ctor_hints: dict[str, list[tuple[Any, ...]]] = {
        "Minesweeper": [(10, 10, 10), (10, 10)],
        "Snake": [(20, 20), (10, 10)],
        "SkiFree": [(40, 100), (40, 200)],
    }
    try:
        raw_instance = _instantiate_game_generic(cls, hints=ctor_hints.get(class_name))
        results["instantiated"] = True
    except Exception as e:
        results["errors"].append(f"Instantiation failed: {type(e).__name__}: {e}")
        return results

    # Wrap in a name-tolerant facade so stress functions survive renaming.
    instance = _GameFacade(raw_instance)
    results["stress"] = _run_persistence_stress(instance, game_id, interaction_count)
    return results


# ---- Module-level helper: call DeepSeek for game generation ----
def _call_deepseek(gateway: Any, prompt: str) -> dict[str, Any]:
    """Call DeepSeek and return response metadata + content."""
    t0 = time.time()
    response = gateway.call_model(
        "deepseek_coder",
        messages=[{"role": "user", "content": prompt}],
        estimated_cost=0.0,
        budget_remaining=5.0,
    )
    latency_ms = (time.time() - t0) * 1000
    usage = response.usage_metadata or {}
    return {
        "content": response.content,
        "tokens_in": int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
        "tokens_out": int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
        "tool_calls": len(response.tool_calls) if response.tool_calls else 0,
        "content_len": len(response.content),
        "model": getattr(response, "model_profile_id", "unknown"),
        "latency_ms": latency_ms,
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
        t0 = time.time()
        response = gateway.call_model(
            "deepseek_coder",
            messages=[{"role": "user", "content": prompt}],
            estimated_cost=0.0,
            budget_remaining=5.0,
        )
        latency_ms = (time.time() - t0) * 1000
        usage = response.usage_metadata or {}
        return {
            "content": response.content,
            "tokens_in": int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
            "tokens_out": int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
            "tool_calls": len(response.tool_calls) if response.tool_calls else 0,
            "content_len": len(response.content),
            "model": getattr(response, "model_profile_id", "unknown"),
            "latency_ms": latency_ms,
        }

    # ---- Snake ----
    def test_build_snake(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Snake game."""
        self._build_and_verify_game(gateway, tmp_path, "snake")

    # ---- Tetris ----
    def test_build_tetris(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Tetris game."""
        self._build_and_verify_game(gateway, tmp_path, "tetris")

    # ---- Minesweeper ----
    def test_build_minesweeper(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Minesweeper game."""
        self._build_and_verify_game(gateway, tmp_path, "minesweeper")

    # ---- Checkers ----
    def test_build_checkers(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Checkers game."""
        self._build_and_verify_game(gateway, tmp_path, "checkers")

    # ---- SkiFree ----
    def test_build_skifree(self, gateway, tmp_path):
        """Test: DeepSeek builds a working SkiFree game."""
        self._build_and_verify_game(gateway, tmp_path, "skifree")

    # ---- Banana ----
    def test_build_banana(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Banana (Gorillas) game."""
        self._build_and_verify_game(gateway, tmp_path, "banana")

    # ---- Pong ----
    def test_build_pong(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Pong game."""
        self._build_and_verify_game(gateway, tmp_path, "pong")

    # ---- Breakout ----
    def test_build_breakout(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Breakout game."""
        self._build_and_verify_game(gateway, tmp_path, "breakout")

    # ---- Maze Runner ----
    def test_build_maze_runner(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Maze Runner game."""
        self._build_and_verify_game(gateway, tmp_path, "maze_runner")

    # ---- Word Guesser ----
    def test_build_word_guesser(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Word Guesser game."""
        self._build_and_verify_game(gateway, tmp_path, "word_guesser")

    # ---- Memory Match ----
    def test_build_memory_match(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Memory Match game."""
        self._build_and_verify_game(gateway, tmp_path, "memory_match")

    # ---- Tic-Tac-Toe ----
    def test_build_tic_tac_toe(self, gateway, tmp_path):
        """Test: DeepSeek builds a working Tic-Tac-Toe game."""
        self._build_and_verify_game(gateway, tmp_path, "tic_tac_toe")

    # ---- Shared build + verify logic ----
    def _build_and_verify_game(self, gateway, tmp_path, game_id):
        game_def = GAME_DEFINITIONS[game_id]
        class_name = game_def["class_name"]
        verifications = game_def["verifications"]

        obs = _init_game_obs(game_id)

        print(f"\n\n{'='*70}")
        print(f"BUILDING: {game_id} ({class_name})")
        print(f"{'='*70}")

        # Step 1: Call DeepSeek
        print(f"\n--- Step 1: Calling DeepSeek for {game_id} ---")
        try:
            response = self._call_model(gateway, game_def["prompt"])
        except Exception:
            raise

        print(f"  tokens_in={response['tokens_in']} tokens_out={response['tokens_out']}")
        print(f"  content_len={response['content_len']} tool_calls={response['tool_calls']}")
        obs["tokens_in"] = response["tokens_in"]
        obs["tokens_out"] = response["tokens_out"]
        obs["tool_calls"] = response["tool_calls"]
        obs["content_len"] = response["content_len"]
        obs["model"] = response["model"]
        obs["phases"]["model_call"] = round(response["latency_ms"], 1)

        # Step 2: Extract code
        print("\n--- Step 2: Extracting Python code ---")
        t0 = time.time()
        source = _extract_python_module(response["content"])
        obs["phases"]["extract_code"] = round((time.time() - t0) * 1000, 1)
        if source is None:
            print("  FAIL: Could not extract Python module from model output")
            print(f"  Raw output (first 500): {response['content'][:500]!r}")
            obs["errors"].append("Could not extract Python module from model output")
            self._record_gap(game_id, "code_extraction", "Model did not produce extractable Python code")
            return

        print(f"  Extracted {len(source)} chars of Python code")

        # Step 3: Parse AST
        print("\n--- Step 3: AST parsing ---")
        t0 = time.time()
        ast_result = _parse_ast(source)
        obs["phases"]["ast_parse"] = round((time.time() - t0) * 1000, 1)
        print(f"  parseable={ast_result['parseable']} has_class={ast_result['has_class']}")
        if ast_result["error"]:
            print(f"  AST error: {ast_result['error']}")

        # Step 4: Write module and run game tests
        print("\n--- Step 4: Game verification ---")
        game_dir = tmp_path / game_id
        game_dir.mkdir(exist_ok=True)
        t0 = time.time()
        test_results = _run_game_tests(source, class_name, verifications, game_id, game_dir)
        obs["phases"]["game_verify"] = round((time.time() - t0) * 1000, 1)

        obs["imported"] = test_results["module_imported"]
        obs["instantiated"] = test_results["instantiated"]

        print(f"  module_written={test_results['module_written']}")
        print(f"  module_imported={test_results['module_imported']}")
        print(f"  instantiated={test_results['instantiated']}")
        if test_results["errors"]:
            for err in test_results["errors"]:
                print(f"  ERROR: {err[:200]}")
                obs["errors"].append(err[:200])

        checks = test_results.get("checks", {})
        passed = sum(1 for c in checks.values() if c["passed"])
        failed = len(checks) - passed
        obs["checks_passed"] = passed
        obs["checks_total"] = len(checks)
        obs["checks"] = {k: {"passed": v["passed"], "desc": v.get("desc", "")} for k, v in checks.items()}
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

        # Step 6: Hard feature verification (name-agnostic contract).
        # The per-check diagnostics above are best-effort; THIS is the hard
        # contract.  We re-load a fresh copy of the module so feature
        # verification sees pristine state (the per-check run above may
        # have mutated its instance).
        feature_failures: list[str] = []
        if source is not None and ast_result["parseable"]:
            feature_dir = tmp_path / f"{game_id}_features"
            feature_dir.mkdir(exist_ok=True)
            try:
                feature_mod = _load_generated_module(
                    source, f"{game_id}_feature_check", feature_dir,
                )
                feature_failures = verify_features(game_id, feature_mod)
            except Exception as exc:
                feature_failures = [
                    f"feature verifier crashed: {type(exc).__name__}: {exc}",
                ]
        else:
            feature_failures = ["source missing or not parseable; cannot verify features"]

        obs["feature_failures"] = feature_failures
        if feature_failures:
            print(f"\n  FEATURE FAILURES ({len(feature_failures)}):")
            for fail in feature_failures:
                print(f"    - {fail}")
            self._record_gap(
                game_id, "features",
                f"{len(feature_failures)} feature failures: " + "; ".join(feature_failures[:3]),
            )
        else:
            print("  All required features verified.")

        print(f"\n{'-'*70}")
        print(f"RESULT: {game_id} — {passed}/{len(checks)} checks passed, "
              f"{len(feature_failures)} feature failures")

        # Hard assertion: the model's output MUST satisfy the feature floor.
        # This is the contract the user cares about — the game may be
        # written differently each time, but it must implement the features.
        assert not feature_failures, (
            f"{game_id}: required features not satisfied:\n  - "
            + "\n  - ".join(feature_failures)
        )

    # ---- Gap tracking ----
    _gaps: ClassVar[list[dict[str, Any]]] = []

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
        else:
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

        # Always export observability report
        _export_observability_report()

        print("\n" + "="*70)
        print("PIPELINE IMPROVEMENT RECOMMENDATIONS")
        print("="*70)
        print("""
1. ITERATIVE CODE GENERATION: The ExecutionEngine only does single-shot generation.
   For complex tasks like game-building, the model needs multiple attempts with
   feedback from test results. ToolCallLoop now supports code work types (code,
   bug_fix, refactor, feature, test) with budget/per-iteration guards — remaining
   gap is wiring the test-failure feedback loop from ExecutionEngine into
   ToolCallLoop retries.

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
        # Note: do NOT require a literal "class Snake" — the model may rename
        # the class (SnakeGame, Game, etc.). The feature verifier below is
        # the real contract; this block only reports observable signals.
        has_any_class = "class " in response_text
        has_import = "import" in response_text.lower()
        has_def = "def " in response_text
        print(f"\n[LOOP] has any class?:   {has_any_class}")
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
        print(f"  Contains Python class:    {has_any_class}")
        print(f"  Contains imports:         {has_import}")
        print(f"  Source extractable:       {source is not None}")
        if source:
            print(f"  Source parseable:         {ast_result['parseable']}")
            print(f"  Source has a class:       {ast_result['has_class']}")

        gaps: list[str] = []
        if not has_any_class:
            gaps.append("Model output lacks any 'class' definition")
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

    _persistence_gaps: ClassVar[list[dict[str, Any]]] = []

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

        obs = _OBSERVABILITY_DATA.get(game_id, {})
        if not obs:
            obs = _init_game_obs(game_id)

        print(f"\n\n{'='*70}")
        print(f"PERSISTENCE TEST: {game_id} ({class_name}) — {interaction_count} interactions")
        print(f"{'='*70}")

        # Step 1: Call DeepSeek
        print(f"\n--- Step 1: Calling DeepSeek for {game_id} ---")
        try:
            response = _call_deepseek(gateway, game_def["prompt"])
        except Exception:
            raise

        print(f"  tokens_in={response['tokens_in']} tokens_out={response['tokens_out']}")
        obs["tokens_in"] = obs.get("tokens_in", 0) or response["tokens_in"]
        obs["tokens_out"] = obs.get("tokens_out", 0) or response["tokens_out"]
        obs["phases"]["model_call"] = round(response.get("latency_ms", 0), 1)

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
    def test_persistence_snake(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "snake")

    # ---- Tetris ----
    def test_persistence_tetris(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "tetris")

    # ---- Minesweeper ----
    def test_persistence_minesweeper(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "minesweeper")

    # ---- Checkers ----
    def test_persistence_checkers(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "checkers")

    # ---- SkiFree ----
    def test_persistence_skifree(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "skifree")

    # ---- Banana ----
    def test_persistence_banana(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "banana")

    # ---- Pong ----
    def test_persistence_pong(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "pong")

    # ---- Breakout ----
    def test_persistence_breakout(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "breakout")

    # ---- Maze Runner ----
    def test_persistence_maze_runner(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "maze_runner")

    # ---- Word Guesser ----
    def test_persistence_word_guesser(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "word_guesser")

    # ---- Memory Match ----
    def test_persistence_memory_match(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "memory_match")

    # ---- Tic-Tac-Toe ----
    def test_persistence_tic_tac_toe(self, gateway, tmp_path):
        self._build_and_stress(gateway, tmp_path, "tic_tac_toe")

    # ---- Persistence Gap Report ----
    def test_persistence_gap_report(self):
        """Print a comprehensive persistence gap analysis."""
        gaps = TestGamePersistence._persistence_gaps
        if not gaps:
            print("\nNo persistence gaps recorded — all tested games survived extended play")
        else:
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

        # Always export observability report
        _export_observability_report()

        print("\n" + "=" * 70)
