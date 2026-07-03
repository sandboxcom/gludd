"""SLM-backed config-fix suggester for detected deployment misconfigs (#76).

SLICE C core. Given a ``deployment`` config and the :class:`Finding` list
:class:`MisconfigDetector` produced, propose a **config-patch dict** to overlay
onto the deployment to fix the reported misconfigurations.

Design mirrors ``compaction/slm.py``:

* The model call is a dependency-injected callable
  ``suggest_fn(deployment, findings) -> dict`` — so :class:`FixSuggester` has no
  hard model dependency and tests run offline.
* :func:`make_fix_suggestion_fn` wires it to gludd's ``ModelGateway`` (a small
  local ``compactor`` profile). It NEVER raises: on any gateway/parse error it
  returns ``{}`` so the caller's deterministic fallback engages.
* :class:`FixSuggester` is fail-soft by construction: when the SLM returns
  ``{}``/``None`` it falls back to merging every finding's
  ``detector.remediate(f)["config_patch"]`` — the SLM is strictly **additive**
  over the guaranteed deterministic remediation.

The router / retry wiring (dispatching this behind the model router with
retries) is deferred to a later slice.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from general_ludd.infra.model_deploy_check import Finding, MisconfigDetector

logger = logging.getLogger(__name__)

#: A fix suggester takes the deployment config and its findings and returns a
#: config-patch dict (keys to overlay onto the deployment).
FixSuggestFn = Callable[[dict[str, Any], list[Finding]], dict[str, Any]]

_SYSTEM_PROMPT = (
    "You are a deployment-config fixer; return ONLY a JSON object of config keys "
    "to overlay to fix the reported misconfigurations."
)

# A permissive object schema for guided decoding: any JSON object of config keys.
_GUIDED_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
}


def _build_fix_prompt(deployment: dict[str, Any], findings: list[Finding]) -> str:
    """Embed the deployment plus each finding's message + remediate() hint."""
    detector = MisconfigDetector()
    lines = [
        "DEPLOYMENT (current config):",
        json.dumps(deployment, indent=2, default=str, sort_keys=True),
        "",
        "MISCONFIGURATIONS to fix (each with a suggested config-patch hint):",
    ]
    for f in findings:
        hint = detector.remediate(f).get("config_patch", {})
        lines.append(
            f"- [{f.severity}] {f.rule_id}: {f.message} "
            f"(hint: {json.dumps(hint, default=str, sort_keys=True)})"
        )
    lines.append("")
    lines.append(
        "Return ONLY a JSON object of config keys to overlay onto the deployment "
        "to fix these. No prose."
    )
    return "\n".join(lines)


def make_fix_suggestion_fn(
    model_gateway: Any,
    profile_id: str = "compactor",
    *,
    max_output_tokens: int = 512,
) -> FixSuggestFn:
    """Wire a ``suggest_fn`` to gludd's ``ModelGateway`` (a small local model).

    The returned callable never raises — on any gateway error, non-JSON content,
    or non-dict result it returns ``{}`` so :class:`FixSuggester`'s deterministic
    fallback engages.
    """

    def _fn(deployment: dict[str, Any], findings: list[Finding]) -> dict[str, Any]:
        try:
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_fix_prompt(deployment, findings)},
            ]
            resp = model_gateway.call_model(
                profile_id,
                messages=messages,
                work_type="misconfig_fix",
                requested_max_output_tokens=max_output_tokens,
                guided_json=_GUIDED_JSON_SCHEMA,
            )
            content = str(getattr(resp, "content", "") or "")
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return parsed
            return {}
        except Exception:
            logger.warning("misconfig fix gateway call failed", exc_info=True)
            return {}

    return _fn


class FixSuggester:
    """Propose a config-patch to fix a deployment's misconfigurations.

    The SLM ``suggest_fn`` is strictly additive: whenever it returns nothing
    usable (``{}``/``None``/raises via the fail-soft wrapper), :meth:`suggest`
    falls back to the DETERMINISTIC merge of every finding's
    ``detector.remediate(f)["config_patch"]`` — so a fix is always produced.
    """

    def __init__(
        self, detector: MisconfigDetector, suggest_fn: FixSuggestFn | None = None
    ) -> None:
        self._detector = detector
        self._suggest_fn = suggest_fn

    def _deterministic_patch(
        self, deployment: dict[str, Any], findings: list[Finding]
    ) -> dict[str, Any]:
        """Merge every finding's remediate() config_patch (guaranteed, no model)."""
        merged: dict[str, Any] = {}
        for f in findings:
            try:
                patch = self._detector.remediate(f).get("config_patch", {})
            except Exception:
                logger.warning("remediate failed for %s", f.rule_id, exc_info=True)
                patch = {}
            if isinstance(patch, dict):
                merged.update(patch)
        return merged

    def suggest(
        self, deployment: dict[str, Any], findings: list[Finding]
    ) -> dict[str, Any]:
        """Return a config-patch dict to fix ``findings`` on ``deployment``.

        Tries the SLM ``suggest_fn`` first (additive); if it yields ``{}``/``None``
        FALLS BACK to the deterministic merge of every finding's
        ``remediate()`` patch. Never raises.
        """
        if self._suggest_fn is not None:
            try:
                proposed = self._suggest_fn(deployment, findings)
            except Exception:
                logger.warning("fix suggest_fn raised; using deterministic fallback", exc_info=True)
                proposed = None
            if isinstance(proposed, dict) and proposed:
                return proposed
        return self._deterministic_patch(deployment, findings)
