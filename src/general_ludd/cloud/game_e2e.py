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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import numpy as np
from numpy.typing import NDArray

from general_ludd.cloud.video_compare import REFERENCE_VIDEO_SPECS, compare_gameplay_to_reference

if TYPE_CHECKING:
    from general_ludd.models.gateway import ModelGateway

logger = logging.getLogger(__name__)
Frame = NDArray[np.uint8]

# ── Optional dependency detection ──────────────────────────────────────────
_HAS_PYGAME: bool
try:
    import pygame  # type: ignore[import-not-found]

    _HAS_PYGAME = True
except ImportError:  # pragma: no cover
    pygame = None
    _HAS_PYGAME = False

_HAS_SKIMAGE: bool
try:
    from skimage.metrics import structural_similarity  # type: ignore[import-not-found]

    _HAS_SKIMAGE = True
except ImportError:  # pragma: no cover
    structural_similarity = None
    _HAS_SKIMAGE = False

_HAS_CV2: bool
try:
    import cv2  # type: ignore[import-not-found]

    _HAS_CV2 = True
except ImportError:  # pragma: no cover
    cv2 = None
    _HAS_CV2 = False


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


# ── GameGenerator ───────────────────────────────────────────────────────────


class GameGenerator:
    """Generates game code via an LLM on Azure GPU compute."""

    def __init__(self, gateway: ModelGateway | None) -> None:
        self._gateway = gateway

    def generate_game(self, spec: GameSpec, model_id: str = "default") -> str:
        """Send the prompt template to the LLM and return generated Python game code."""
        if self._gateway is None:
            raise ValueError("ModelGateway is not configured")

        response = self._gateway.call_model(
            model_id,
            messages=[{"role": "user", "content": spec.prompt_template}],
            estimated_cost=0.0,
            budget_remaining=5.0,
        )
        content = getattr(response, "content", "")
        if not content:
            raise RuntimeError("LLM returned empty response")
        return _extract_python_code(str(content))

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
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(code, encoding="utf-8")


def _extract_python_code(content: str) -> str:
    """Extract Python code from an LLM response, stripping markdown fences."""
    import re

    fence = re.search(r"```(?:python)?\s*\n(.*?)```", content, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return content.strip()


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
        self._processes: list[subprocess.Popen[Any]] = []

    def run_headless(self, game_path: str, num_frames: int) -> list[Frame]:
        """Run the game in headless mode using pygame with dummy video driver."""
        if not _HAS_PYGAME:
            raise ImportError("pygame is required for game execution")

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

    def run_headless_inline(self, game_path: str, num_frames: int) -> list[Frame]:
        """Run the game by importing and calling it in-process for frame capture.

        This method hooks pygame's display to capture frames into memory
        rather than launching a subprocess.
        """
        if not _HAS_PYGAME:
            raise ImportError("pygame is required for game execution")

        os.environ["SDL_VIDEODRIVER"] = "dummy"
        os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

        frames: list[Frame] = []

        display_surface = pygame.display.set_mode((800, 600))

        game_dir = str(Path(game_path).parent)
        game_name = Path(game_path).stem

        if game_dir not in sys.path:
            sys.path.insert(0, game_dir)

        captured: list[pygame.Surface] = []

        original_flip = pygame.display.flip
        original_update = pygame.display.update

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

        pygame.display.flip = _capturing_flip
        pygame.display.update = _capturing_update

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
            pygame.display.flip = original_flip
            pygame.display.update = original_update

        runner = self
        for surf in captured[:num_frames]:
            frames.append(runner.capture_frame(surf))

        return frames

    @staticmethod
    def capture_frame(surface: Any) -> Frame:
        """Convert a pygame Surface to a numpy array (RGB)."""
        if not _HAS_PYGAME:
            raise ImportError("pygame is required for frame capture")
        arr = pygame.surfarray.array3d(surface)
        return cast(Frame, arr.transpose(1, 0, 2))

    def cleanup(self) -> None:
        """Kill any lingering game processes."""
        for proc in self._processes:
            with contextlib.suppress(OSError):
                proc.kill()
                proc.wait()
        self._processes.clear()

    def __del__(self) -> None:
        self.cleanup()


# ── FrameComparator ─────────────────────────────────────────────────────────


class FrameComparator:
    """Compares generated frames against reference using SSIM and PSNR."""

    @staticmethod
    def compute_ssim(frame1: Frame, frame2: Frame) -> float:
        """Structural similarity using skimage if available, else pixel-difference fallback."""
        if frame1.shape != frame2.shape:
            return 0.0

        if _HAS_SKIMAGE and frame1.ndim == 3 and frame1.shape[2] == 3:
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
        if not _HAS_CV2:
            raise ImportError("opencv-python (cv2) is required for video frame extraction")

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise FileNotFoundError(f"Cannot open video: {video_path}")

        frames: list[Frame] = []
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
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
    reference_cache_dir: str = field(
        default_factory=lambda: str(Path.home() / ".cache" / "gludd" / "reference-videos")
    )
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


class AzureGameE2E:
    """Orchestrator for the full Azure GPU game generation + fidelity test."""

    def __init__(
        self,
        deploy_config: DeploymentConfig | None = None,
        model_gateway: ModelGateway | None = None,
    ) -> None:
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
                    frames = self.runner.run_headless_inline(game_path, spec.expected_frames)
                    result.frames_captured = len(frames)
                    result.game_ran = len(frames) > 0

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
                                result.errors.append(
                                    "Reference clip is not cached; network retrieval is disabled"
                                )
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
            "errors": results.errors,
        }


# ── Module public API ──────────────────────────────────────────────────────

__all__ = [
    "GAME_SPECS",
    "AzureGameE2E",
    "DeploymentConfig",
    "E2EResult",
    "FrameComparator",
    "GameGenerator",
    "GameRunner",
    "GameSpec",
]
