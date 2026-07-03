"""Plan-time technical-debt evaluator — forward-looking scope-gap analysis.

This module looks FORWARD at a *planned* change (a :class:`PlanArtifact`) and
asks the question a competent reviewer asks in design review: *what is being
engineered for, and what is NOT?* — the missing-but-useful features and the
sharp edges the plan leaves unaddressed.  It is deliberately NOT a code audit
of existing code (that is ``validation/gap_analyzer.py`` and
``self_improve/harness.py``); it reasons about a change that has not been
implemented yet.

Design mirrors :mod:`general_ludd.compaction.slm`:

* The model call is a **dependency-injected callable** ``evaluate_fn(plan, goal,
  repo_context) -> list[dict]`` so the class has no hard model dependency and
  tests run fully offline.  :func:`make_debt_evaluate_fn` wires it to gludd's
  ``ModelGateway``; any callable works.
* It **fails soft**: if ``evaluate_fn`` is missing, raises, returns a non-list,
  returns malformed dicts, or returns nothing, :class:`DebtEvaluator` falls back
  to a small set of DETERMINISTIC structural heuristics.  Evaluation must never
  crash the planner it sits on.

Crucially the **recommendation policy is enforced in code, not trusted from the
LLM**.  Whatever ``recommendation`` the model returns is discarded; a
deterministic classifier (:meth:`DebtEvaluator._classify`) decides ``fold_in``
vs ``defer`` from crisp, inspectable rules (see :meth:`_classify`).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from general_ludd.planning.artifact import PlanArtifact

logger = logging.getLogger(__name__)

DebtKind = Literal["missing_feature", "sharp_edge"]
DebtRecommendation = Literal["fold_in", "defer"]
DebtEffort = Literal["small", "medium", "large"]

# Rationale strings the deterministic classifier stamps onto every finding.
_FOLD_IN_RATIONALE = (
    "in-scope sharp edge, closes a gap inside files already being changed"
)
_DEFER_RATIONALE = (
    "introduces new scope/files/feature beyond this task — deferring avoids "
    "feature creep"
)

# Phrases that signal a NEW capability is being proposed (used by _classify to
# keep new features out of the fold_in bucket).  Deliberately biased toward
# DEFER: a false "this is a new feature" only over-defers, which is the safe
# direction for feature-creep control.
_FEATURE_SIGNALS: tuple[str, ...] = (
    "add support",
    "add a new",
    "additional",
    "implement",
    "introduce",
    "new feature",
    "new endpoint",
    "support for",
    "integrate ",
    "build a",
    "provide a new",
)

# Resilience vocabulary used by the deterministic fallback to flag contracts
# that promise robustness the plan does not obviously build for.
_RESILIENCE_WORDS: tuple[str, ...] = (
    "error",
    "timeout",
    "retry",
    "retries",
    "failure",
    "fail",
    "fallback",
)


class DebtFinding(BaseModel):
    """A single forward-looking scope gap in a planned change."""

    model_config = ConfigDict(strict=True)

    gap: str
    kind: DebtKind
    why_it_matters: str = ""
    # recommendation + feature_creep_rationale are POLICY fields: whatever the
    # model supplies is overwritten by DebtEvaluator._classify.  They carry
    # permissive defaults so a model omitting them does not fail strict
    # coercion (the classifier fills them deterministically afterwards).
    recommendation: DebtRecommendation = "defer"
    feature_creep_rationale: str = ""
    effort: DebtEffort = "medium"
    touched_files: list[str] = []


class DebtFindings(BaseModel):
    """A collection of :class:`DebtFinding`."""

    findings: list[DebtFinding] = []


# (plan, goal, repo_context) -> raw list of finding dicts from the model.
EvaluateFn = Callable[[PlanArtifact, str, str], list[dict[str, Any]]]


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _is_test_file(path: str) -> bool:
    base = _basename(path)
    return base.startswith("test_") and base.endswith(".py")


def _is_impl_py(path: str) -> bool:
    """A source implementation ``.py`` file (not a test)."""
    return path.endswith(".py") and not _is_test_file(path)


def _stem(path: str) -> str:
    base = _basename(path)
    return base[:-3] if base.endswith(".py") else base


def _test_name_of(test_path: str) -> str:
    """``tests/app/test_config.py`` -> ``config`` (the implementation stem)."""
    base = _basename(test_path)  # test_<name>.py
    return base[len("test_") : -3]


class DebtEvaluator:
    """Evaluate a planned change for forward-looking technical debt.

    ``evaluate_fn`` is the injected model call.  When it is ``None`` — or fails
    in any way — evaluation falls back to deterministic structural heuristics.
    Every model-produced finding is re-classified in code, so the ``fold_in`` /
    ``defer`` decision never depends on the LLM's own judgement.
    """

    def __init__(
        self,
        evaluate_fn: EvaluateFn | None = None,
        *,
        max_findings: int = 8,
        profile_id: str = "compactor",
    ) -> None:
        self._evaluate_fn = evaluate_fn
        self._max_findings = max_findings
        self.profile_id = profile_id

    # -- policy -----------------------------------------------------------

    def _classify(
        self, finding: DebtFinding, plan: PlanArtifact, goal: str
    ) -> DebtFinding:
        """Deterministically decide fold_in vs defer, overriding the model.

        POLICY — recommend ``fold_in`` IFF ALL of:

          (a) ``touched_files`` is NON-EMPTY and every entry is already in
              ``plan.target_files``, or is the sibling test of one
              (``tests/.../test_<name>.py`` for a source ``<name>.py`` in
              target_files); AND
          (b) ``effort == "small"``; AND
          (c) the gap introduces NO capability absent from ``goal +
              plan.contracts`` — i.e. it is a ``sharp_edge`` (never a
              ``missing_feature``) and carries no new-feature signal phrase.

        Otherwise ``defer``.  The recommendation and feature_creep_rationale are
        overwritten on the returned copy; all other fields are preserved.

        Note (a): a finding that names ZERO touched files cannot be "a gap
        inside files already being changed", so it can never be ``fold_in``.
        """
        in_scope = self._touched_in_scope(finding.touched_files, plan.target_files)
        small = finding.effort == "small"
        no_new_cap = not self._introduces_new_capability(
            finding.gap, finding.kind, plan, goal
        )

        if in_scope and small and no_new_cap:
            return finding.model_copy(
                update={
                    "recommendation": "fold_in",
                    "feature_creep_rationale": _FOLD_IN_RATIONALE,
                }
            )
        return finding.model_copy(
            update={
                "recommendation": "defer",
                "feature_creep_rationale": _DEFER_RATIONALE,
            }
        )

    @staticmethod
    def _touched_in_scope(touched: list[str], target_files: list[str]) -> bool:
        """True iff ``touched`` is non-empty and every entry is a target file
        or a sibling test of one.

        Empty ``touched`` is NOT in scope: a fold_in must name concrete files
        already being changed (see :meth:`_classify` note).
        """
        if not touched:
            return False
        target_set = set(target_files)
        impl_stems = {_stem(t) for t in target_files if _is_impl_py(t)}
        for t in touched:
            if t in target_set:
                continue
            if _is_test_file(t) and _test_name_of(t) in impl_stems:
                continue
            return False
        return True

    @staticmethod
    def _introduces_new_capability(
        gap: str, kind: DebtKind, plan: PlanArtifact, goal: str
    ) -> bool:
        """True iff the gap names a capability absent from goal + contracts.

        A ``missing_feature`` is a new capability by definition.  For a
        ``sharp_edge`` we look for explicit new-feature signal phrases that are
        NOT already reflected in the goal/contracts reference text.
        """
        if kind == "missing_feature":
            return True
        gl = gap.lower()
        reference = (
            goal + " " + plan.title + " " + plan.description
            + " " + " ".join(plan.contracts)
        ).lower()
        return any(sig in gl and sig not in reference for sig in _FEATURE_SIGNALS)

    # -- evaluation -------------------------------------------------------

    def evaluate(
        self, plan: PlanArtifact, goal: str, repo_context: str = ""
    ) -> DebtFindings:
        """Evaluate ``plan`` for forward-looking debt.  Never raises."""
        if self._evaluate_fn is None:
            return self._fallback(plan)

        try:
            raw = self._evaluate_fn(plan, goal, repo_context)
        except Exception:
            # Fail SOFT: a model hiccup must not break the planner.
            logger.warning(
                "debt evaluate_fn failed; using deterministic fallback",
                exc_info=True,
            )
            return self._fallback(plan)

        if not isinstance(raw, list):
            logger.warning(
                "debt evaluate_fn returned %s, not a list; using deterministic "
                "fallback",
                type(raw).__name__,
            )
            return self._fallback(plan)

        # An empty result means the model produced nothing usable (the wired fn
        # returns [] on ANY gateway error — mirroring slm.py's ""-on-error). Fall
        # back to the deterministic heuristics so a gateway failure never masks
        # itself as "no debt found".
        if not raw:
            return self._fallback(plan)

        try:
            coerced = [DebtFinding(**dict(d)) for d in raw]
        except Exception:
            logger.warning(
                "debt evaluate_fn returned malformed findings; using "
                "deterministic fallback",
                exc_info=True,
            )
            return self._fallback(plan)

        classified = [self._classify(f, plan, goal) for f in coerced]
        return DebtFindings(findings=classified[: self._max_findings])

    # -- deterministic fallback ------------------------------------------

    def _fallback(self, plan: PlanArtifact) -> DebtFindings:
        """Model-free structural heuristics over the plan.

        Correctness over recall: emit only findings we can assert structurally.
        """
        findings: list[DebtFinding] = []

        # Heuristic 1: an implementation file being changed with no sibling test
        # in the same change is an in-scope sharp edge -> fold_in.
        test_names = {
            _test_name_of(t) for t in plan.target_files if _is_test_file(t)
        }
        for f in plan.target_files:
            if not _is_impl_py(f):
                continue
            if _stem(f) in test_names:
                continue
            findings.append(
                DebtFinding(
                    gap=f"no test for {f}",
                    kind="sharp_edge",
                    why_it_matters=(
                        "changing this file without a sibling test leaves the "
                        "new behaviour unverified"
                    ),
                    recommendation="fold_in",
                    feature_creep_rationale=_FOLD_IN_RATIONALE,
                    effort="small",
                    touched_files=[f],
                )
            )

        # Heuristic 2: a contract promising resilience (error/timeout/retry/...)
        # with no target file obviously handling it is a sharp edge worth its own
        # ticket -> defer.
        for contract in plan.contracts:
            cl = contract.lower()
            if any(w in cl for w in _RESILIENCE_WORDS):
                findings.append(
                    DebtFinding(
                        gap=(
                            "contract promises resilience with no target file "
                            f"handling it: {contract}"
                        ),
                        kind="sharp_edge",
                        why_it_matters=(
                            "the failure/timeout path is contracted but no "
                            "planned file implements it"
                        ),
                        recommendation="defer",
                        feature_creep_rationale=_DEFER_RATIONALE,
                        effort="medium",
                        touched_files=[],
                    )
                )

        return DebtFindings(findings=findings[: self._max_findings])


# ---------------------------------------------------------------------------
# Gateway wiring
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You look FORWARD at a PLANNED change and list what is NOT being engineered "
    "for — missing-but-useful features and sharp edges a competent reviewer "
    "would flag. Do NOT audit existing code quality. Return JSON matching the "
    "schema."
)


def _build_user_prompt(plan: PlanArtifact, goal: str, repo_context: str) -> str:
    parts = [f"GOAL: {goal}".strip(), "", "PLANNED CHANGE:", plan.to_markdown()]
    if repo_context.strip():
        parts += ["", "REPO CONTEXT:", repo_context.strip()]
    parts += [
        "",
        "List the forward-looking scope gaps (missing features and sharp "
        "edges) this plan leaves unaddressed.",
    ]
    return "\n".join(parts)


def make_debt_evaluate_fn(
    model_gateway: Any,
    profile_id: str = "compactor",
    *,
    max_output_tokens: int = 768,
) -> EvaluateFn:
    """Wire an ``evaluate_fn`` to gludd's ``ModelGateway``.

    The returned callable never raises: on ANY gateway/parse error it returns
    ``[]`` (mirroring :func:`make_slm_summarize_fn` returning ``""``), which
    engages :meth:`DebtEvaluator._fallback`.
    """
    schema = DebtFindings.model_json_schema()

    def _fn(
        plan: PlanArtifact, goal: str, repo_context: str = ""
    ) -> list[dict[str, Any]]:
        try:
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": _build_user_prompt(plan, goal, repo_context),
                },
            ]
            resp = model_gateway.call_model(
                profile_id,
                messages=messages,
                work_type="debt_eval",
                requested_max_output_tokens=max_output_tokens,
                guided_json=schema,
            )
            content = str(getattr(resp, "content", "") or "")
            parsed = json.loads(content)
            items = parsed.get("findings", []) if isinstance(parsed, dict) else parsed
            if not isinstance(items, list):
                return []
            return [dict(x) for x in items if isinstance(x, dict)]
        except Exception:
            logger.warning("debt_eval gateway call failed", exc_info=True)
            return []

    return _fn
