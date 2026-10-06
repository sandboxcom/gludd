"""Generated-game loading and feature verification for the DeepSeek live harness."""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

from tests.e2e._game_lifecycle import _invoke_start_method, run_lifecycle_checks


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
    "tick",
    "step",
    "update",
    "advance",
    "next_frame",
    "next_turn",
    "frame",
    "turn",
    "simulate",
    "do_tick",
)
_INPUT_NAMES: tuple[str, ...] = (
    "input",
    "handle_input",
    "send_input",
    "set_direction",
    "direction",
    "action",
    "key",
    "press",
    "set_input",
    "on_input",
)
_STATE_NAMES: tuple[str, ...] = (
    "render_state",
    "get_state",
    "state",
    "to_dict",
    "as_dict",
    "snapshot",
    "serialize",
    "export_state",
)
_REVEAL_NAMES: tuple[str, ...] = ("reveal", "click", "open", "dig", "uncover")
_FLAG_NAMES: tuple[str, ...] = ("flag", "mark", "toggle_flag", "set_flag")
_MOVE_NAMES: tuple[str, ...] = (
    "move",
    "play",
    "make_move",
    "do_move",
    "submit_move",
)
_THROW_NAMES: tuple[str, ...] = ("throw", "shoot", "fire", "launch", "toss")
_FLIP_NAMES: tuple[str, ...] = ("flip", "select", "reveal_card", "turn", "pick")
_GUESS_NAMES: tuple[str, ...] = ("guess", "try_letter", "guess_letter", "submit", "attempt")
_START_NAMES: tuple[str, ...] = (
    "start",
    "begin",
    "play",
    "launch",
    "run",
    "resume",
    "new_game",
    "start_game",
)
_RESTART_NAMES: tuple[str, ...] = (
    "restart",
    "reset",
    "new_game",
    "start_new",
    "reinitialize",
    "reset_game",
)

_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    _TICK_NAMES,
    _INPUT_NAMES,
    _STATE_NAMES,
    _REVEAL_NAMES,
    _FLAG_NAMES,
    _MOVE_NAMES,
    _THROW_NAMES,
    _FLIP_NAMES,
    _GUESS_NAMES,
    _START_NAMES,
    _RESTART_NAMES,
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
        key=lambda kv: len(
            [m for m in inspect.getmembers(kv[1], predicate=inspect.isfunction) if not m[0].startswith("_")]
        ),
    )


def _instantiate_game_generic(
    cls: type,
    hints: list[tuple[Any, ...]] | None = None,
) -> Any:
    """Instantiate ``cls`` by trying common constructor signatures.

    ``hints`` are caller-supplied arg-tuples tried first (game-specific
    knowledge).  Generic fallbacks cover no-arg, one-int, and two/three-int
    signatures.  Raises the last exception if every attempt fails.
    """
    import inspect

    sig = inspect.signature(cls.__init__)
    params = [
        p
        for p in sig.parameters.values()
        if p.name != "self" and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    n_required = sum(1 for p in params if p.default is inspect.Parameter.empty)

    candidates: list[tuple[Any, ...]] = []
    if hints:
        candidates.extend(hints)
    candidates.extend(
        [
            (),
            (10,),
            (20,),
            (10, 10),
            (20, 20),
            (40, 100),
            (10, 10, 10),
        ]
    )
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


def _find_food_attribute(state: dict[str, Any], exclude: str | None = None) -> tuple[str, Any] | None:
    """Find an attribute that looks like food: a single coord pair OR a
    list/tuple containing exactly one coord pair.  ``exclude`` is the body
    attribute name so we don't match a length-1 body as food (ambiguous
    by shape alone)."""
    for name, value in state.items():
        if name == exclude:
            continue
        if _is_coord_pair(value):
            return name, value
        if isinstance(value, (list, tuple)) and len(value) == 1 and _is_coord_pair(value[0]):
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


def _find_piece_y(state: dict[str, Any]) -> int | float | None:
    """Extract the active Tetris piece row from common generated shapes."""
    for name, value in state.items():
        if not any(token in name.lower() for token in ("piece", "falling", "active")):
            continue
        if isinstance(value, dict):
            for key in ("y", "row", "top"):
                candidate = value.get(key)
                if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                    return candidate
        if isinstance(value, (list, tuple)) and len(value) >= 3:
            candidate = value[2]
            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                return candidate
    for name in ("piece_y", "current_y", "active_y", "falling_y"):
        candidate = state.get(name)
        if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
            return candidate
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
    mod: Any,
    preferred: str | None,
    hints: list[tuple[Any, ...]] | None = None,
) -> tuple[list[str], Any]:
    """Shared discovery + instantiation. Returns (failures, instance_or_None)."""
    failures: list[str] = []
    cls = _discover_game_class(mod, preferred=preferred)
    if cls is None:
        names = [n for n in dir(mod) if not n.startswith("_")]
        return ([f"no game class found (preferred={preferred!r}, names={names})"], None)
    try:
        instance = _instantiate_game_generic(cls, hints=hints)
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
            "no body-like attribute found (list of >=1 coordinate pairs); snake state must track body segments"
        )
    if _find_food_attribute(state, exclude=body_attr[0] if body_attr else None) is None:
        failures.append("no food-like attribute found (single [x,y] pair); snake state must track food position")
    if _find_score_attribute(state) is None:
        failures.append("no score-like numeric attribute found; snake must track score/length")
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
    tick = _find_callable_attr(instance, _TICK_NAMES)
    before_y = _find_piece_y(_get_state_dict(instance))
    if tick is not None and before_y is not None:
        try:
            tick[1]()
            after_y = _find_piece_y(_get_state_dict(instance))
            if after_y is not None and after_y != before_y + 1:
                failures.append(f"gravity moved active piece by {after_y - before_y!r} rows; expected exactly one")
        except Exception as exc:
            failures.append(f"gravity tick raised: {type(exc).__name__}: {exc}")
    elif tick is not None:
        # Z.3 fallback: when piece-Y can't be extracted by name, use a
        # board-diff check to verify gravity moves the active piece.
        from tests.e2e._game_lifecycle import _check_tetris_gravity

        gravity_fail = _check_tetris_gravity(instance)
        if gravity_fail is not None:
            failures.append(gravity_fail)
    state = _get_state_dict(instance)
    if _find_board_attribute(state) is None:
        failures.append(
            "no 2D board/grid attribute found (list of >=3 equal-length rows); tetris state must track the playfield"
        )
    if _find_score_attribute(state) is None:
        failures.append("no score-like numeric attribute found; tetris must track score/lines cleared")
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
    failures, instance = _verify_game_skeleton(
        mod,
        preferred="SkiFree",
        hints=[(40, 100), (40, 200), ()],
    )
    if instance is None:
        return failures
    failures.extend(_check_tick_advances_state(instance))
    state = _get_state_dict(instance)
    if _find_position_attribute(state, "x") is None:
        failures.append("no x-axis position attribute found; skifree must track skier x")
    if _find_position_attribute(state, "y") is None:
        failures.append("no y-axis position attribute found; skifree must track skier y")
    if _find_game_over_attribute(state) is None:
        failures.append("no crashed/over boolean attribute found; skifree must track crash state")
    has_obstacle_list = any(
        isinstance(v, list)
        and len(v) >= 1
        and all(_is_coord_pair(item) for item in v if not isinstance(item, (int, float)))
        for v in state.values()
    )
    if not has_obstacle_list:
        failures.append("no obstacle-list attribute found (trees/rocks); skifree must track obstacle positions")
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
        start_fail = _invoke_start_method(instance)
        if start_fail is not None:
            failures.append(f"could not start game before reveal: {start_fail}")
            return failures
        try:
            result = reveal[1](0, 0)
            if isinstance(result, str) and result not in (
                "ok",
                "mine",
                "already_revealed",
                "out_of_bounds",
            ):
                failures.append(f"reveal returned unexpected value: {result!r}")
        except Exception as exc:
            failures.append(f"reveal(0,0) raised: {type(exc).__name__}: {exc}")
    state = _get_state_dict(instance)
    if _find_board_attribute(state) is None:
        failures.append("no 2D grid attribute found (list of >=3 equal-length rows); minesweeper must track cell grid")
    if _find_callable_attr(instance, _FLAG_NAMES) is None:
        failures.append("no flag-like method found (flag/mark/toggle_flag/set_flag); minesweeper must support flagging")
    # Prove the documented terminal path by revealing an actual mine rather
    # than an arbitrary corner that may be safe in a random board.
    if reveal is not None:
        board_name = _find_board_attribute(state)
        board = state.get(board_name) if board_name else None
        mine_coord: tuple[int, int] | None = None
        if isinstance(board, list):
            for y, row in enumerate(board):
                if not isinstance(row, list):
                    continue
                for x, cell in enumerate(row):
                    if isinstance(cell, dict) and cell.get("is_mine") is True:
                        mine_coord = (x, y)
                        break
                if mine_coord is not None:
                    break
        if mine_coord is not None:
            try:
                result = reveal[1](*mine_coord)
                game_over = _find_game_over_attribute(_get_state_dict(instance))
                if result != "mine" and not (game_over and getattr(instance, game_over, False)):
                    failures.append("revealing a known mine did not enter game-over")
            except Exception as exc:
                failures.append(f"revealing known mine raised: {type(exc).__name__}: {exc}")
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
            "no 2D board attribute found (list of >=3 equal-length rows); checkers must track the 8x8 board"
        )
    elif len(board[1]) < 8 or any(len(row) < 8 for row in board[1]):
        failures.append(
            f"board attribute {board[0]!r} is smaller than 8x8 "
            f"(got {len(board[1])}x{len(board[1][0])}); checkers requires 8x8"
        )
    if _find_player_attribute(state) is None:
        failures.append("no current-player-like attribute found; checkers must track whose turn")
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
                failures.append(f"throw dict lacks all of {throw_keys}; got keys {sorted(result.keys())}")
            traj = result.get("trajectory")
            if traj is not None and not isinstance(traj, list):
                failures.append(f"throw trajectory must be a list, got {type(traj).__name__!r}")
    state = _get_state_dict(instance)
    if _find_player_attribute(state) is None:
        failures.append("no current-player-like attribute found; banana must track turn")
    has_skyline = any(
        isinstance(v, list) and len(v) >= 3 and all(isinstance(h, (int, float)) and not isinstance(h, bool) for h in v)
        for v in state.values()
    )
    if not has_skyline:
        failures.append(
            "no skyline-like attribute found (list of >=3 building heights); banana must track the city skyline"
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


