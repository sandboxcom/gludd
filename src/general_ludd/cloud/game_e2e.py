"""Azure game-generation E2E test system.

Orchestrates:
1. Azure GPU compute provisioning (via DeploymentManager or pre-provisioned endpoint)
2. LLM game-code generation on Azure GPU
3. Headless game execution with frame capture
4. SSIM/PSNR comparison against reference gameplay
"""

from __future__ import annotations

import ast
import contextlib
import logging
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Protocol, cast

import numpy as np
from numpy.typing import NDArray

from general_ludd.cloud.game_generation import ensure_lifecycle_start_method
from general_ludd.cloud.software_generator import (
    GenerationCache,
    ProjectSpec,
    SoftwareGenerator,
)
from general_ludd.cloud.video_compare import REFERENCE_VIDEO_SPECS, compare_gameplay_to_reference

if TYPE_CHECKING:
    from general_ludd.models.gateway import ModelGateway

logger = logging.getLogger(__name__)
Frame = NDArray[np.uint8]

# ── Optional dependency detection ──────────────────────────────────────────
pygame: ModuleType | None
try:
    import pygame as _pygame
except ImportError:  # pragma: no cover
    pygame = None
else:
    pygame = _pygame
_HAS_PYGAME = pygame is not None


class _StructuralSimilarity(Protocol):
    def __call__(
        self,
        frame_a: object,
        frame_b: object,
        *,
        data_range: float,
        channel_axis: int,
        win_size: int,
    ) -> float: ...


structural_similarity: _StructuralSimilarity | None
try:
    from skimage.metrics import structural_similarity as _skimage_structural_similarity
except ImportError:  # pragma: no cover
    structural_similarity = None
else:
    structural_similarity = cast(_StructuralSimilarity, _skimage_structural_similarity)
_HAS_SKIMAGE = structural_similarity is not None

cv2: ModuleType | None
try:
    import cv2 as _cv2
except ImportError:  # pragma: no cover
    cv2 = None
else:
    cv2 = _cv2
_HAS_CV2 = cv2 is not None


def _require_pygame() -> ModuleType:
    if pygame is None:
        raise ImportError("pygame is required for game execution")
    return pygame


def _require_cv2() -> ModuleType:
    if cv2 is None:
        raise ImportError("opencv-python (cv2) is required for video frame extraction")
    return cv2


# ── GameSpec ────────────────────────────────────────────────────────────────


@dataclass
class GameSpec:
    """Definition of a game to generate and test."""

    name: str
    genre: str
    description: str
    expected_frames: int
    similarity_threshold: float
    prompt_template: str
    reference_video_url: str | None = None
    required_controls: tuple[str, ...] = ()
    requires_menu: bool = False


@dataclass(frozen=True)
class GameInputEvent:
    """One deterministic input delivered through pygame's event queue."""

    frame: int
    control: str


# ── Predefined game specs ──────────────────────────────────────────────────

GAME_SPECS: list[GameSpec] = [
    GameSpec(
        name="doom_e1m1_hallway",
        genre="fps",
        description=(
            "First-person shooter with grey stone walls, a long hallway with "
            "pillars, green armor pickup at the end, red floor, ceiling lights. "
            "Player can move with WASD and look with mouse."
        ),
        expected_frames=30,
        similarity_threshold=REFERENCE_VIDEO_SPECS["doom_e1m1_hallway"].comparison_threshold,
        prompt_template=(
            "Write a complete Python game using pygame that renders a first-person "
            "shooter scene.\n"
            "Requirements:\n"
            "- Grey stone-textured walls forming a long hallway\n"
            "- Square pillars along the hallway sides\n"
            "- Red/brown floor\n"
            "- Ceiling with periodic light sources\n"
            "- A green glowing pickup item at the far end of the hallway\n"
            "- Start in a visible menu that lists controls; RETURN starts play and ESCAPE returns to the menu\n"
            "- Player uses W/A/S/D to move and strafes while looking around with the mouse\n"
            "- Rendering should use raycasting or simple 3D projection\n"
            "- Window size 800x600\n"
            "- Run for at least 30 frames then exit\n"
            "The game must be self-contained in one file and runnable with: python game.py\n"
        ),
        reference_video_url=REFERENCE_VIDEO_SPECS["doom_e1m1_hallway"].source_url,
        required_controls=("w", "a", "s", "d", "mouse", "escape", "return"),
        requires_menu=True,
    ),
    GameSpec(
        name="quake_dm6_arena",
        genre="fps",
        description=(
            "Dark industrial arena with metal platforms at different heights, "
            "lava pools below, a central pillar structure, orange/brown color "
            "palette, flickering lights. Player can jump between platforms."
        ),
        expected_frames=30,
        similarity_threshold=REFERENCE_VIDEO_SPECS["quake_dm6_arena"].comparison_threshold,
        prompt_template=(
            "Write a complete Python game using pygame that renders a dark industrial "
            "arena.\n"
            "Requirements:\n"
            "- Dark metal-textured platforms at 3 different heights\n"
            "- Orange lava pool at the bottom\n"
            "- A central pillar/column structure\n"
            "- Orange/brown color palette\n"
            "- Flickering point lights on platforms\n"
            "- Start in a visible menu that lists controls; RETURN starts play and ESCAPE returns to the menu\n"
            "- Player uses W/A/S/D to move, looks with the mouse, and jumps with SPACE\n"
            "- Simple gravity and platform collision\n"
            "- Window 800x600\n"
            "- Run for 30 frames then exit\n"
            "Self-contained, runnable with: python game.py\n"
        ),
        reference_video_url=REFERENCE_VIDEO_SPECS["quake_dm6_arena"].source_url,
        required_controls=("w", "a", "s", "d", "mouse", "space", "escape", "return"),
        requires_menu=True,
    ),
]


def build_game_input_script(spec: GameSpec) -> tuple[GameInputEvent, ...]:
    """Build a menu→play→menu event script covering every declared control."""
    controls = [control.lower() for control in spec.required_controls]
    ordered: list[str] = []
    if "return" in controls:
        ordered.append("return")
    ordered.extend(control for control in controls if control not in {"return", "escape"})
    if "escape" in controls:
        ordered.append("escape")
    return tuple(GameInputEvent(frame=index, control=control) for index, control in enumerate(ordered))


# ── GameGenerator ───────────────────────────────────────────────────────────


class GameGenerator:
    """Generates game code via an LLM on Azure GPU compute.

    Delegates to :class:`SoftwareGenerator` with ``project_type="game"``.
    Maintained for backward compatibility.

    When *task_policy* is provided, ``generate_game()`` gates the LLM call
    through ``SmallModelTaskPolicy.authorize()`` so local / constrained
    models are only dispatched for tasks they have proven capability for.
    """

    def __init__(
        self,
        gateway: ModelGateway | None,
        task_policy: object | None = None,
    ) -> None:
        """Initialize game generation with an optional gateway and task policy."""
        self._gateway = gateway
        self._task_policy = task_policy
        self._generator = SoftwareGenerator(gateway, task_policy)

    def generate_game(
        self,
        spec: GameSpec,
        model_id: str = "default",
        model_identity: object | None = None,
        evidence: tuple[object, ...] = (),
    ) -> str:
        """Send the prompt template to the LLM and return generated Python game code."""
        project_spec = self._to_project_spec(spec)
        return ensure_lifecycle_start_method(
            self._generator.generate(
                project_spec,
                model_id=model_id,
                model_identity=model_identity,
                evidence=evidence,
            )
        )

    def generate_game_multi(
        self,
        spec: GameSpec,
        model_profiles: dict[Any, str],
        model_identity: object | None = None,
        evidence: tuple[object, ...] = (),
    ) -> str:
        """Generate game code using role-specific models via :class:`MultiModelGamePipeline`."""
        project_spec = self._to_project_spec(spec)
        return ensure_lifecycle_start_method(
            self._generator.generate_multi(
                project_spec,
                model_profiles=model_profiles,
                model_identity=model_identity,
                evidence=evidence,
            )
        )

    @staticmethod
    def _to_project_spec(spec: GameSpec) -> ProjectSpec:
        return ProjectSpec(
            name=spec.name,
            project_type="game",
            description=spec.description,
            prompt_template=spec.prompt_template,
        )

    @staticmethod
    def validate_game_code(code: str, spec: GameSpec | None = None) -> bool:
        """Check syntax and, when supplied, the playable game contract."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return False

        has_pygame_import = False
        has_game_loop = False
        has_pygame_init = False

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "pygame":
                        has_pygame_import = True
            elif isinstance(node, ast.ImportFrom):
                if node.module and "pygame" in node.module:
                    has_pygame_import = True
            elif isinstance(node, ast.Call):
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "pygame"
                    and func.attr == "init"
                ):
                    has_pygame_init = True
            elif isinstance(node, ast.While):
                has_game_loop = True

        if not (has_pygame_import and has_game_loop and has_pygame_init):
            return False
        if spec is None:
            return True
        if spec.requires_menu and not _has_rendered_menu_state(tree):
            return False
        observed_controls = _collect_pygame_controls(tree)
        return all(control.lower() in observed_controls for control in spec.required_controls)

    @staticmethod
    def save_game(code: str, path: str) -> None:
        """Write generated game code to file."""
        SoftwareGenerator.save_output(code, path)


class GameGenerationCache:
    """Session cache keyed by fixture prompt, model id, and model settings.

    Delegates to :class:`GenerationCache` for the generic storage layer
    while preserving the game-specific key shape.
    """

    def __init__(self) -> None:
        """Initialize an empty game-generation cache and miss counter."""
        self._cache = GenerationCache()
        self._generated: dict[tuple[str, str, str, tuple[tuple[str, str], ...]], str] = {}

    @property
    def miss_count(self) -> int:
        """Return the number of generation requests that missed the cache."""
        return self._cache.miss_count

    @miss_count.setter
    def miss_count(self, value: int) -> None:
        self._cache.miss_count = value

    def generate(
        self,
        generator: GameGenerator,
        spec: GameSpec,
        *,
        model_id: str = "default",
        model_settings: Mapping[str, str] | None = None,
    ) -> str:
        """Return cached game code or generate and cache a new result."""
        settings = tuple(sorted((model_settings or {}).items()))
        key = (spec.name, spec.prompt_template, model_id, settings)
        cached = self._generated.get(key)
        if cached is not None:
            return cached
        generated = generator.generate_game(spec, model_id=model_id)
        self._generated[key] = generated
        self._cache.miss_count += 1
        return generated


_PYGAME_KEY_CONTROLS: dict[str, str] = {
    "k_w": "w",
    "k_a": "a",
    "k_s": "s",
    "k_d": "d",
    "k_space": "space",
    "k_escape": "escape",
    "k_return": "return",
    "k_enter": "return",
    "k_kp_enter": "return",
}
_MENU_STATES = frozenset({"menu", "main_menu", "pause", "paused", "start"})


def _collect_pygame_controls(tree: ast.AST) -> set[str]:
    """Return canonical controls referenced by executable pygame code."""
    controls: set[str] = set()
    for node in ast.walk(tree):
        name = ""
        if isinstance(node, ast.Attribute):
            name = node.attr.lower()
        elif isinstance(node, ast.Name):
            name = node.id.lower()
        if name in _PYGAME_KEY_CONTROLS:
            controls.add(_PYGAME_KEY_CONTROLS[name])
        if name in {"mouse", "mousemotion", "get_rel", "get_pos"}:
            controls.add("mouse")
    return controls


def _has_rendered_menu_state(tree: ast.AST) -> bool:
    """Require both an initial menu value and a control-flow branch for it."""
    has_initial_state = False
    has_menu_branch = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                has_initial_state = value.value.lower() in _MENU_STATES or has_initial_state
        elif isinstance(node, (ast.If, ast.While)):
            states = {
                child.value.lower()
                for child in ast.walk(node.test)
                if isinstance(child, ast.Constant) and isinstance(child.value, str)
            }
            has_menu_branch = bool(states & _MENU_STATES) or has_menu_branch
    return has_initial_state and has_menu_branch


# ── GameRunner ──────────────────────────────────────────────────────────────


class GameRunner:
    """Runs generated games in headless mode and captures frames."""

    def __init__(self) -> None:
        """Initialize process ownership and captured-control state."""
        self._processes: list[subprocess.Popen[Any]] = []
        self.last_injected_controls: set[str] = set()

    def run_headless(self, game_path: str, num_frames: int) -> list[Frame]:
        """Run the game in headless mode using pygame with dummy video driver."""
        _require_pygame()

        env = os.environ.copy()
        env["SDL_VIDEODRIVER"] = "dummy"
        env["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

        frames: list[Frame] = []

        proc = subprocess.Popen(
            [sys.executable, str(game_path)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(Path(game_path).parent),
        )

        self._processes.append(proc)

        try:
            proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        return frames

    def run_headless_inline(
        self,
        game_path: str,
        num_frames: int,
        *,
        input_script: tuple[GameInputEvent, ...] = (),
    ) -> list[Frame]:
        """Run the game by importing and calling it in-process for frame capture.

        This method hooks pygame's display to capture frames into memory
        rather than launching a subprocess.
        """
        pygame_module = _require_pygame()

        os.environ["SDL_VIDEODRIVER"] = "dummy"
        os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

        frames: list[Frame] = []

        display_surface = pygame_module.display.set_mode((800, 600))

        game_dir = str(Path(game_path).parent)
        game_name = Path(game_path).stem

        if game_dir not in sys.path:
            sys.path.insert(0, game_dir)

        captured: list[object] = []

        original_flip = pygame_module.display.flip
        original_update = pygame_module.display.update
        original_event_get = pygame_module.event.get
        events_by_frame: dict[int, list[GameInputEvent]] = {}
        for scripted_event in input_script:
            events_by_frame.setdefault(scripted_event.frame, []).append(scripted_event)
        event_frame = 0
        self.last_injected_controls.clear()

        key_constants = {
            "w": pygame_module.K_w,
            "a": pygame_module.K_a,
            "s": pygame_module.K_s,
            "d": pygame_module.K_d,
            "space": pygame_module.K_SPACE,
            "escape": pygame_module.K_ESCAPE,
            "return": pygame_module.K_RETURN,
        }

        def _scripted_event_get(*args: Any, **kwargs: Any) -> list[Any]:
            nonlocal event_frame
            events = list(original_event_get(*args, **kwargs))
            for scripted_event in events_by_frame.get(event_frame, []):
                control = scripted_event.control
                if control == "mouse":
                    event = pygame_module.event.Event(
                        pygame_module.MOUSEMOTION,
                        pos=(400, 300),
                        rel=(8, 0),
                        buttons=(False, False, False),
                    )
                else:
                    key = key_constants.get(control)
                    if key is None:
                        raise ValueError(f"Unsupported scripted game control: {control}")
                    event = pygame_module.event.Event(pygame_module.KEYDOWN, key=key)
                events.append(event)
                self.last_injected_controls.add(control)
            event_frame += 1
            return events

        def _capturing_flip() -> None:
            surf = display_surface.copy()
            captured.append(surf)
            if len(captured) >= num_frames:
                raise SystemExit(0)
            original_flip()

        def _capturing_update(*args: Any, **kwargs: Any) -> None:
            surf = display_surface.copy()
            captured.append(surf)
            if len(captured) >= num_frames:
                raise SystemExit(0)
            original_update(*args, **kwargs)

        pygame_module.display.flip = _capturing_flip
        pygame_module.display.update = _capturing_update
        pygame_module.event.get = _scripted_event_get

        try:
            import importlib.util

            spec = importlib.util.spec_from_file_location(game_name, game_path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot load module from {game_path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[game_name] = module

            with contextlib.suppress(SystemExit):
                spec.loader.exec_module(module)
        finally:
            pygame_module.display.flip = original_flip
            pygame_module.display.update = original_update
            pygame_module.event.get = original_event_get

        runner = self
        for surf in captured[:num_frames]:
            frames.append(runner.capture_frame(surf))

        return frames

    @staticmethod
    def capture_frame(surface: Any) -> Frame:
        """Convert a pygame Surface to an ``(height, width, 3)`` RGB frame."""
        pygame_module = _require_pygame()
        arr = pygame_module.surfarray.array3d(surface)
        return cast(Frame, arr.transpose(1, 0, 2))

    def cleanup(self) -> None:
        """Kill any lingering game processes."""
        for proc in self._processes:
            with contextlib.suppress(OSError):
                proc.kill()
                proc.wait()
        self._processes.clear()

    def __del__(self) -> None:
        """Best-effort reap any subprocesses still owned during finalization."""
        self.cleanup()


# ── FrameComparator ─────────────────────────────────────────────────────────


class FrameComparator:
    """Compares generated frames against reference using SSIM and PSNR."""

    @staticmethod
    def compute_ssim(frame1: Frame, frame2: Frame) -> float:
        """Structural similarity using skimage if available, else pixel-difference fallback."""
        if frame1.shape != frame2.shape:
            return 0.0

        if structural_similarity is not None and frame1.ndim == 3 and frame1.shape[2] == 3:
            try:
                val: float = structural_similarity(
                    frame1,
                    frame2,
                    data_range=frame2.max() - frame2.min() or 255.0,
                    channel_axis=2,
                    win_size=min(7, min(frame1.shape[0], frame1.shape[1]) or 7),
                )
                return val
            except (ValueError, RuntimeError):
                pass

        diff = frame1.astype(np.float64) - frame2.astype(np.float64)
        mse = np.mean(diff**2)
        if mse == 0:
            return 1.0
        max_val = max(frame1.max(), frame2.max(), 1.0)
        return float(1.0 / (1.0 + mse / max_val))

    @staticmethod
    def compute_psnr(frame1: Frame, frame2: Frame) -> float:
        """Peak signal-to-noise ratio between two frames."""
        if frame1.shape != frame2.shape:
            return 0.0

        diff = frame1.astype(np.float64) - frame2.astype(np.float64)
        mse = np.mean(diff**2)
        if mse == 0:
            return float("inf")

        max_val = max(frame1.max(), frame2.max(), 255.0)
        import math

        return float(20.0 * math.log10(max_val) - 10.0 * math.log10(mse))

    @staticmethod
    def compare_frames(
        generated: list[Frame],
        reference: list[Frame],
        threshold: float = 0.4,
    ) -> dict[str, Any]:
        """Compare two sequences of frames, returning mean SSIM, mean PSNR, and pass/fail."""
        if not generated or not reference:
            return {
                "mean_ssim": 0.0,
                "mean_psnr": 0.0,
                "pass": False,
                "frame_count": 0,
                "threshold": threshold,
            }

        fc = FrameComparator()
        min_len = min(len(generated), len(reference))
        ssims: list[float] = []
        psnrs: list[float] = []

        for i in range(min_len):
            ssims.append(fc.compute_ssim(generated[i], reference[i]))
            psnrs.append(fc.compute_psnr(generated[i], reference[i]))

        mean_ssim = float(np.mean(ssims)) if ssims else 0.0
        mean_psnr = float(np.mean(psnrs)) if psnrs else 0.0

        return {
            "mean_ssim": round(mean_ssim, 4),
            "mean_psnr": round(mean_psnr, 2),
            "pass": mean_ssim >= threshold,
            "frame_count": min_len,
            "threshold": threshold,
            "ssim_values": ssims,
            "psnr_values": psnrs,
        }

    @staticmethod
    def load_reference_frames(video_path: str) -> list[Frame]:
        """Extract frames from a reference video using cv2."""
        cv2_module = _require_cv2()
        cap = cv2_module.VideoCapture(video_path)
        if not cap.isOpened():
            raise FileNotFoundError(f"Cannot open video: {video_path}")

        frames: list[Frame] = []
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            frames.append(cv2_module.cvtColor(frame, cv2_module.COLOR_BGR2RGB))
        cap.release()
        return frames


# ── AzureGameE2E Orchestrator ───────────────────────────────────────────────


@dataclass
class DeploymentConfig:
    """Configuration for Azure GPU deployment."""

    subscription_id: str = ""
    resource_group: str = "gludd-game-e2e"
    vm_size: str = "Standard_NC24ads_A100_v4"
    location: str = "eastus"
    use_preprovisioned: bool = True
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    provision_timeout_seconds: int = 600
    reference_cache_dir: str = field(default_factory=lambda: str(Path.home() / ".cache" / "gludd" / "reference-videos"))
    allow_reference_network: bool = False


@dataclass
class E2EResult:
    """Structured result from an E2E game fidelity test."""

    spec_name: str
    code_generated: bool = False
    code_valid: bool = False
    game_ran: bool = False
    frames_captured: int = 0
    mean_ssim: float = 0.0
    mean_psnr: float = 0.0
    comparison_pass: bool = False
    errors: list[str] = field(default_factory=list)
    generated_code_path: str = ""
    generated_code: str = ""
    reference_source_url: str = ""
    reference_video_id: str = ""
    reference_cache_status: str = ""
    reference_frames_sampled: int = 0
    reference_network_used: bool = False
    comparison_threshold: float = 0.0
    motion_correlation: float = 0.0
    input_controls_injected: tuple[str, ...] = ()


class AzureGameE2E:
    """Orchestrator for the full Azure GPU game generation + fidelity test."""

    def __init__(
        self,
        deploy_config: DeploymentConfig | None = None,
        model_gateway: ModelGateway | None = None,
    ) -> None:
        """Initialize the E2E pipeline and its owned generator and runner."""
        self.deploy_config = deploy_config or DeploymentConfig()
        self.gateway = model_gateway
        self.generator = GameGenerator(model_gateway)
        self.runner = GameRunner()
        self.comparator = FrameComparator()

    def run_full_test(self, spec: GameSpec) -> E2EResult:
        """Run the full pipeline: generate → run → compare → report."""
        result = E2EResult(spec_name=spec.name)

        try:
            code = self.generator.generate_game(spec)
            result.generated_code = code
            result.code_generated = True

            result.code_valid = GameGenerator.validate_game_code(code, spec)
            if not result.code_valid:
                result.errors.append("Generated game failed its required controls/menu contract")
                return result

            with tempfile.TemporaryDirectory() as tmpdir:
                game_path = os.path.join(tmpdir, "game.py")
                GameGenerator.save_game(code, game_path)
                result.generated_code_path = game_path

                if _HAS_PYGAME:
                    input_script = build_game_input_script(spec)
                    frames = self.runner.run_headless_inline(
                        game_path,
                        spec.expected_frames,
                        input_script=input_script,
                    )
                    result.frames_captured = len(frames)
                    result.game_ran = len(frames) > 0
                    result.input_controls_injected = tuple(
                        control for control in spec.required_controls if control in self.runner.last_injected_controls
                    )

                    if spec.reference_video_url:
                        try:
                            comparison = compare_gameplay_to_reference(
                                spec.name,
                                frames,
                                self.deploy_config.reference_cache_dir,
                                allow_network=self.deploy_config.allow_reference_network,
                            )
                            result.reference_source_url = comparison.source_url
                            result.reference_video_id = comparison.source_video_id
                            result.reference_cache_status = comparison.cache_status
                            result.reference_frames_sampled = comparison.reference_frame_count
                            result.reference_network_used = comparison.network_used
                            result.comparison_threshold = comparison.threshold
                            result.mean_ssim = comparison.mean_ssim
                            result.motion_correlation = comparison.motion_correlation
                            result.comparison_pass = comparison.passed
                            if comparison.cache_status == "missing":
                                result.errors.append("Reference clip is not cached; network retrieval is disabled")
                        except Exception as exc:
                            result.errors.append(f"Reference comparison failed: {exc}")
                    elif frames:
                        self_comparison = self.comparator.compare_frames(
                            frames,
                            frames,
                            spec.similarity_threshold,
                        )
                        result.mean_ssim = self_comparison["mean_ssim"]
                        result.mean_psnr = self_comparison["mean_psnr"]
                        result.comparison_pass = self_comparison["pass"]
                else:
                    result.errors.append("pygame not installed — cannot run game")

        except Exception as exc:
            result.errors.append(str(exc))
        finally:
            self.runner.cleanup()

        return result

    def report_results(self, results: E2EResult) -> dict[str, Any]:
        """Return a structured results dict suitable for logging/CI."""
        return {
            "spec_name": results.spec_name,
            "code_generated": results.code_generated,
            "code_valid": results.code_valid,
            "game_ran": results.game_ran,
            "frames_captured": results.frames_captured,
            "mean_ssim": results.mean_ssim,
            "mean_psnr": results.mean_psnr,
            "comparison_pass": results.comparison_pass,
            "comparison_threshold": results.comparison_threshold,
            "motion_correlation": results.motion_correlation,
            "reference_source_url": results.reference_source_url,
            "reference_video_id": results.reference_video_id,
            "reference_cache_status": results.reference_cache_status,
            "reference_frames_sampled": results.reference_frames_sampled,
            "reference_network_used": results.reference_network_used,
            "input_controls_injected": results.input_controls_injected,
            "errors": results.errors,
        }


# ── Module public API ──────────────────────────────────────────────────────

__all__ = [
    "GAME_SPECS",
    "AzureGameE2E",
    "DeploymentConfig",
    "E2EResult",
    "FrameComparator",
    "GameGenerationCache",
    "GameGenerator",
    "GameInputEvent",
    "GameRunner",
    "GameSpec",
    "build_game_input_script",
]
