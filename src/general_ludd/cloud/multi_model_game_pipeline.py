"""Multi-model game generation pipeline: PLANNER → CODER → REVIEWER.

Each stage uses a different model role:
- PLANNER: receives a high-level description, produces a structured DesignSpec
- CODER: receives the design spec, produces Python game code
- REVIEWER: reviews the code against the spec, produces polished code

The pipeline supports multiple review rounds with iterative feedback.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict

from general_ludd.cloud.game_generation import normalize_generated_python

if TYPE_CHECKING:
    from general_ludd.models.gateway import ModelGateway
    from general_ludd.routing_roles.small_model_policy import SmallModelTaskPolicy


@dataclass
class DesignSpec:
    """Structured game design produced by the PLANNER model."""

    name: str
    genre: str
    description: str
    architecture_plan: str = ""
    component_list: tuple[str, ...] = ()
    tech_stack: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()

    def to_prompt(self) -> str:
        """Render this design spec as a planner-model prompt string."""
        lines = [
            f"# Game Design Specification: {self.name}",
            "",
            f"**Genre:** {self.genre}",
            f"**Description:** {self.description}",
        ]
        if self.architecture_plan:
            lines.append(f"\n## Architecture\n{self.architecture_plan}")
        if self.component_list:
            lines.append(f"\n## Components\n{', '.join(self.component_list)}")
        if self.tech_stack:
            lines.append(f"\n## Tech Stack\n{', '.join(self.tech_stack)}")
        if self.acceptance_criteria:
            lines.append("\n## Acceptance Criteria\n" + "\n".join(f"- {c}" for c in self.acceptance_criteria))
        return "\n".join(lines)


@dataclass
class ReviewResult:
    """Structured review output from the REVIEWER model."""

    code: str
    issues_found: tuple[str, ...] = ()
    fixes_applied: tuple[str, ...] = ()
    quality_score: float = 0.0
    passed: bool = False

    def to_feedback_prompt(self) -> str:
        """Render the review issues as a fix-request prompt, empty when clean."""
        if not self.issues_found:
            return ""
        lines = [
            "## Code Review Feedback",
            "",
            "The previous code had the following issues:",
            *(f"- {issue}" for issue in self.issues_found),
            "",
            "Please fix the code to address all of these issues.",
        ]
        return "\n".join(lines)


_PLANNER_SYSTEM_PROMPT = """\
You are a GAME DESIGN PLANNER. Given a brief game description, produce a structured
design specification. Output each field on its own line in `field:value` format:

name:<game-name>
genre:<genre>
architecture:<architecture description>
components:<comma-separated list>
tech:<comma-separated tech stack>
acceptance:<comma-separated acceptance criteria>

Be specific and concrete. Only output these fields, nothing else."""

_CODER_SYSTEM_PROMPT = """\
You are a GAME CODER. Write complete, runnable Python game code from the design spec.
The code must:

- Be self-contained in one file
- Honor every explicit constraint in the original description
- Implement every explicitly named class, method, attribute, signature, and lifecycle step
- Include ALL components listed in the spec
- Use ONLY the tech stack specified
- Pass every acceptance criterion

Output ONLY the Python code, no explanation, no markdown fences."""

_REVIEWER_SYSTEM_PROMPT = """\
You are a GAME CODE REVIEWER. Review the provided game code against the design spec.
Output a structured review in `field:value` format:

issues:<comma-separated issues found, or empty>
fixes:<comma-separated fixes recommended, or empty>
score:<0.0-1.0 quality score>
passed:<true or false>

Check: syntax validity, runnable control flow, all required components implemented,
tech stack compliance, acceptance criteria met, and all
explicit constraints from the original description preserved."""

_CODER_FIX_SYSTEM_PROMPT = """\
You are a GAME CODER fixing review feedback. Given the original code, the design spec,
and review feedback listing specific issues, return the complete module with minimal
changes that address every issue. Preserve working behavior. Every function definition
must have an indented body. Output the entire corrected Python module. Never return a
partial patch, explanation, or markdown fence."""

_MARKDOWN_FIELD_PREFIX = r"^[ \t]*(?:[-+*][ \t]*)?(?:\*\*)?"
_MARKDOWN_FIELD_VALUE = r"[ \t]*:(?:\*\*)?[ \t]*(.*?)[ \t]*$"
_PLANNER_RESPONSE_RE = re.compile(
    _MARKDOWN_FIELD_PREFIX
    + r"(name|genre|architecture|components|tech|acceptance)"
    + _MARKDOWN_FIELD_VALUE,
    re.IGNORECASE | re.MULTILINE,
)
_REVIEWER_RESPONSE_RE = re.compile(
    _MARKDOWN_FIELD_PREFIX + r"(issues|fixes|score|passed)" + _MARKDOWN_FIELD_VALUE,
    re.IGNORECASE | re.MULTILINE,
)

_DEFAULT_PLANNER_MAX_TOKENS = 256
_DEFAULT_CODER_MAX_TOKENS = 1024
_DEFAULT_REVIEWER_MAX_TOKENS = 128
_MAX_CANDIDATE_FEEDBACK_CHARS = 1000


class _OutputOptions(TypedDict):
    requested_max_output_tokens: int
    max_tokens: int


def _parse_key_value(text: str, pattern: re.Pattern[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for match in pattern.finditer(text):
        result[match.group(1).strip().lower()] = match.group(2).strip().strip("*_` ")
    return result


class MultiModelGamePipeline:
    """PLANNER → design spec → CODER → Python code → REVIEWER → polished code.

    Each stage dispatches to a different model role through the ModelGateway.
    The pipeline supports iterative review-fix cycles with a configurable
    maximum number of rounds.
    """

    def __init__(
        self,
        gateway: ModelGateway,
        task_policy: SmallModelTaskPolicy | None = None,
        *,
        planner_max_tokens: int = _DEFAULT_PLANNER_MAX_TOKENS,
        coder_max_tokens: int = _DEFAULT_CODER_MAX_TOKENS,
        reviewer_max_tokens: int = _DEFAULT_REVIEWER_MAX_TOKENS,
    ) -> None:
        """Initialize the pipeline with a model gateway and optional task policy."""
        self._gateway = gateway
        self._task_policy = task_policy
        self._planner_max_tokens = self._validate_max_tokens(
            "planner_max_tokens", planner_max_tokens
        )
        self._coder_max_tokens = self._validate_max_tokens("coder_max_tokens", coder_max_tokens)
        self._reviewer_max_tokens = self._validate_max_tokens(
            "reviewer_max_tokens", reviewer_max_tokens
        )

    @staticmethod
    def _validate_max_tokens(name: str, value: int) -> int:
        """Reject invalid generation limits before any provider call."""
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} must be a positive int")
        return value

    @staticmethod
    def _output_options(max_tokens: int) -> _OutputOptions:
        """Use one cap for provider generation and gateway budget estimation."""
        return {
            "requested_max_output_tokens": max_tokens,
            "max_tokens": max_tokens,
        }

    @staticmethod
    def _validate_candidate(
        validator: Callable[[str], bool | str] | None,
        code: str,
    ) -> tuple[bool, str]:
        """Normalize deterministic acceptance or bounded repair feedback."""
        if validator is None:
            return False, ""
        outcome = validator(code)
        if outcome is True:
            return True, ""
        if outcome is False:
            return False, ""
        if isinstance(outcome, str):
            return False, outcome.strip()[:_MAX_CANDIDATE_FEEDBACK_CHARS]
        raise TypeError("candidate_validator must return bool or str")

    @staticmethod
    def _normalize_candidate(
        normalizer: Callable[[str], str] | None,
        code: str,
    ) -> str:
        """Apply one deterministic repair seam to model-authored source."""
        if normalizer is None:
            return code
        normalized = normalizer(code)
        if not isinstance(normalized, str) or not normalized.strip():
            raise ValueError("candidate_normalizer must return non-empty Python source")
        return normalized

    def generate(
        self,
        description: str,
        *,
        planner_model: str = "default",
        coder_model: str = "default",
        reviewer_model: str = "default",
        max_review_rounds: int = 3,
        candidate_normalizer: Callable[[str], str] | None = None,
        candidate_validator: Callable[[str], bool | str] | None = None,
    ) -> str:
        """Generate through review cycles, honoring stronger deterministic validation."""
        spec = self.plan(description, model_id=planner_model)
        code = self._normalize_candidate(
            candidate_normalizer,
            self.code(spec, model_id=coder_model),
        )

        for _round in range(max_review_rounds):
            result = self.review(code, spec, model_id=reviewer_model)
            candidate_feedback = ""
            if candidate_validator is None:
                if result.passed:
                    return result.code
            else:
                candidate_passed, candidate_feedback = self._validate_candidate(
                    candidate_validator,
                    result.code,
                )
                if candidate_passed:
                    return result.code
            code = self._normalize_candidate(
                candidate_normalizer,
                self.code(
                    spec,
                    model_id=coder_model,
                    previous_code=code,
                    feedback=candidate_feedback or result.to_feedback_prompt(),
                ),
            )

        if candidate_validator is not None:
            # The final revision has already incorporated one reviewer verdict
            # plus deterministic repair feedback. Validate it directly so a
            # redundant subjective call cannot bypass or delay the hard gate.
            candidate_passed, _candidate_feedback = self._validate_candidate(
                candidate_validator,
                code,
            )
            if candidate_passed:
                return code
        else:
            # Without a deterministic validator, retain the traditional final
            # reviewer pass so the last revision is never returned unchecked.
            result = self.review(code, spec, model_id=reviewer_model)
            if result.passed:
                return result.code

        raise RuntimeError(
            f"Game generation failed after {max_review_rounds} review rounds "
            f"for '{spec.name}' — code did not pass review"
        )

    def plan(self, description: str, *, model_id: str = "default") -> DesignSpec:
        """Ask the planner model for a structured game design specification."""
        response = self._gateway.call_model(
            model_id,
            messages=[
                {"role": "system", "content": _PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": description},
            ],
            estimated_cost=0.0,
            budget_remaining=5.0,
            **self._output_options(self._planner_max_tokens),
        )
        parsed = _parse_key_value(response.content, _PLANNER_RESPONSE_RE)
        return DesignSpec(
            name=parsed.get("name", "untitled"),
            genre=parsed.get("genre", ""),
            description=description,
            architecture_plan=parsed.get("architecture", ""),
            component_list=tuple(c.strip() for c in parsed.get("components", "").split(",") if c.strip()),
            tech_stack=tuple(t.strip() for t in parsed.get("tech", "").split(",") if t.strip()),
            acceptance_criteria=tuple(a.strip() for a in parsed.get("acceptance", "").split(",") if a.strip()),
        )

    def code(
        self,
        spec: DesignSpec,
        *,
        model_id: str = "default",
        previous_code: str = "",
        feedback: str = "",
    ) -> str:
        """Ask the coder model to generate (or fix) the game implementation."""
        if previous_code and feedback:
            messages: list[dict[str, str]] = [
                {"role": "system", "content": _CODER_FIX_SYSTEM_PROMPT},
                {"role": "user", "content": spec.to_prompt()},
                {"role": "assistant", "content": previous_code},
                {"role": "user", "content": feedback},
            ]
        else:
            messages = [
                {"role": "system", "content": _CODER_SYSTEM_PROMPT},
                {"role": "user", "content": spec.to_prompt()},
            ]

        response = self._gateway.call_model(
            model_id,
            messages=messages,
            estimated_cost=0.0,
            budget_remaining=5.0,
            **self._output_options(self._coder_max_tokens),
        )
        return normalize_generated_python(response)

    def review(
        self,
        code: str,
        spec: DesignSpec,
        *,
        model_id: str = "default",
    ) -> ReviewResult:
        """Ask the reviewer model to critique the generated code."""
        review_messages: list[dict[str, str]] = [
            {"role": "system", "content": _REVIEWER_SYSTEM_PROMPT},
            {"role": "user", "content": spec.to_prompt()},
            {"role": "assistant", "content": code},
        ]

        response = self._gateway.call_model(
            model_id,
            messages=review_messages,
            estimated_cost=0.0,
            budget_remaining=5.0,
            **self._output_options(self._reviewer_max_tokens),
        )
        parsed = _parse_key_value(response.content, _REVIEWER_RESPONSE_RE)
        score_str = parsed.get("score", "0.0")
        passed_str = parsed.get("passed", "false")

        try:
            score = float(score_str)
        except ValueError:
            score = 0.0

        return ReviewResult(
            code=code,
            issues_found=tuple(i.strip() for i in parsed.get("issues", "").split(",") if i.strip()),
            fixes_applied=tuple(f.strip() for f in parsed.get("fixes", "").split(",") if f.strip()),
            quality_score=max(0.0, min(1.0, score)),
            passed=passed_str.lower() in ("true", "yes", "1"),
        )


__all__ = [
    "DesignSpec",
    "MultiModelGamePipeline",
    "ReviewResult",
]
