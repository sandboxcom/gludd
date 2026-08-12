"""Generic software generation pipeline — project-type-agnostic code generation.

Replaces the game-specific :class:`GameGenerator` with a generic
:class:`SoftwareGenerator` that accepts a ``project_type`` parameter
(game, website, scraper, cli_tool, api_server, etc.) and delegates
to the same :class:`MultiModelGamePipeline` and :class:`ModelPipeline`
for multi-step LLM generation.
"""

from __future__ import annotations

import ast
import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from general_ludd.cloud.game_generation import normalize_generated_python
from general_ludd.cloud.multi_model_game_pipeline import MultiModelGamePipeline
from general_ludd.cloud.project_types import (
    get_project_type,
    validate_project_against_rules,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from general_ludd.models.gateway import ModelGateway

logger = logging.getLogger(__name__)


# ── ProjectSpec ──────────────────────────────────────────────────────────────


@dataclass
class ProjectSpec:
    """Definition of a software project to generate."""

    name: str
    project_type: str
    description: str
    prompt_template: str
    expected_output_files: int = 1
    acceptance_criteria: tuple[str, ...] = ()
    extra_context: str = ""


# ── SoftwareGenerator ────────────────────────────────────────────────────────


class _TaskPolicy(Protocol):
    def authorize(
        self,
        task: object,
        identity: object,
        evidence: object,
    ) -> object: ...


class SoftwareGenerator:
    """Generates software code via an LLM backed by Azure GPU compute.

    Accepts a ``project_type`` parameter that selects the project template
    and validation rules from :mod:`general_ludd.cloud.project_types`.

    When *task_policy* is provided, ``generate()`` gates the LLM call
    through ``SmallModelTaskPolicy.authorize()``.
    """

    _gateway: ModelGateway | None
    _task_policy: object | None

    def __init__(
        self,
        gateway: ModelGateway | None,
        task_policy: object | None = None,
    ) -> None:
        self._gateway = gateway
        self._task_policy = task_policy

    def generate(
        self,
        spec: ProjectSpec,
        model_id: str = "default",
        model_identity: object | None = None,
        evidence: tuple[object, ...] = (),
    ) -> str:
        if self._gateway is None:
            raise ValueError("ModelGateway is not configured")

        if self._task_policy is not None and model_identity is not None:
            self._authorize_dispatch(spec, model_identity, evidence)

        response = self._gateway.call_model(
            model_id,
            messages=[{"role": "user", "content": spec.prompt_template}],
            estimated_cost=0.0,
            budget_remaining=5.0,
        )
        return normalize_generated_python(response)

    def generate_multi(
        self,
        spec: ProjectSpec,
        model_profiles: dict[Any, str],
        model_identity: object | None = None,
        evidence: tuple[object, ...] = (),
    ) -> str:
        from general_ludd.schemas.benchmark import TaskRole

        if self._gateway is None:
            raise ValueError("ModelGateway is not configured")

        if self._task_policy is not None and model_identity is not None:
            self._authorize_dispatch(spec, model_identity, evidence)

        pipeline = MultiModelGamePipeline(self._gateway)
        return normalize_generated_python(
            pipeline.generate(
                spec.description,
                planner_model=model_profiles.get(TaskRole.PLANNER, "default"),
                coder_model=model_profiles.get(TaskRole.CODER, "default"),
                reviewer_model=model_profiles.get(TaskRole.REVIEWER, "default"),
            )
        )

    def _authorize_dispatch(
        self,
        spec: ProjectSpec,
        model_identity: object,
        evidence: tuple[object, ...],
    ) -> None:
        from general_ludd.routing_roles.small_model_policy import (
            CapabilityEvidence,
            DispatchAction,
            ModelIdentity,
            SmallModelTaskPolicy,
            SmallModelTaskSpec,
            TaskImpact,
        )
        from general_ludd.schemas.benchmark import TaskRole

        assert self._task_policy is not None
        task_policy = cast(SmallModelTaskPolicy, self._task_policy)
        task = SmallModelTaskSpec(
            task_id=f"fpx.1.{spec.project_type}.{spec.name}",
            task_kind="coding",
            role=TaskRole.CODER,
            collection="gludd.fpx",
            input_digest=hashlib.sha256(spec.prompt_template.encode()).hexdigest(),
            impacts=frozenset({TaskImpact.READ_SOURCE, TaskImpact.WRITE_ARTIFACT}),
            acceptance_checks=("syntax_valid", "import_ok", "run_without_crash"),
        )
        decision = task_policy.authorize(
            task, cast(ModelIdentity, model_identity), cast(Sequence["CapabilityEvidence"], evidence)
        )
        action = decision.action
        # Older policy adapters represented LOCAL as integer 1.  Accept only
        # that exact legacy value (never arbitrary truthy values); every other
        # unknown action remains fail-closed.
        legacy_local = isinstance(action, int) and not isinstance(action, bool) and action == 1
        if action is not DispatchAction.LOCAL and not legacy_local:
            raise PermissionError(
                f"SmallModelTaskPolicy denied {spec.project_type} dispatch for {spec.name}: {decision.reason}"
            )

    def validate_code(self, code: str, project_type: str | None = None) -> bool:
        """Validate generated code using the project type's validation rules.

        Dispatches through :func:`validate_project_against_rules` when
        *project_type* is given.  Falls back to ``ast.parse`` for
        untyped code.  Backward-compat: if the type definition carries a
        ``validate`` callable it is used directly.
        """
        if project_type is not None:
            type_def = get_project_type(project_type)
            custom = getattr(type_def, "validate", None)
            if callable(custom):
                return bool(custom(code))
            return validate_project_against_rules(code, type_def)
        try:
            ast.parse(code)
        except SyntaxError:
            return False
        return True

    @staticmethod
    def save_output(code: str, path: str) -> None:
        """Write generated code to file."""
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(code, encoding="utf-8")


# ── GenerationCache ──────────────────────────────────────────────────────────


class GenerationCache:
    """Session cache for generated software, keyed by project spec + model."""

    def __init__(self) -> None:
        self._generated: dict[tuple[str, str, str, tuple[tuple[str, str], ...]], str] = {}
        self.miss_count = 0

    def generate(
        self,
        generator: SoftwareGenerator,
        spec: ProjectSpec,
        *,
        model_id: str = "default",
        model_settings: Mapping[str, str] | None = None,
    ) -> str:
        settings = tuple(sorted((model_settings or {}).items()))
        key = (spec.name, spec.prompt_template, model_id, settings)
        cached = self._generated.get(key)
        if cached is not None:
            return cached
        generated = generator.generate(spec, model_id=model_id)
        self._generated[key] = generated
        self.miss_count += 1
        return generated


# ── Module public API ────────────────────────────────────────────────────────

__all__ = [
    "GenerationCache",
    "ProjectSpec",
    "SoftwareGenerator",
]
