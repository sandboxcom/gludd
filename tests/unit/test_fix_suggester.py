"""Unit tests for the config-fix suggester (infra/fix_suggester.py).

Covers:
  * _build_fix_prompt — prompt construction with findings
  * make_fix_suggestion_fn — fail-soft wiring, gateway errors, JSON parse failures
  * FixSuggester._deterministic_patch — guaranteed fallback merge
  * FixSuggester.suggest — SLM path + deterministic fallback
  * Module-level exports (FixSuggestFn type alias, constants)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from general_ludd.infra.fix_suggester import (
    _GUIDED_JSON_SCHEMA,
    _SYSTEM_PROMPT,
    FixSuggester,
    FixSuggestFn,
    _build_fix_prompt,
    make_fix_suggestion_fn,
)
from general_ludd.infra.model_deploy_check import Finding, MisconfigDetector


def _make_finding(
    rule_id: str = "MISSING_FIELD",
    severity: str = "error",
    engine: str = "test_engine",
    message: str = "field is missing",
    remediation: str = "add the field",
) -> Finding:
    """Build a minimal Finding for tests."""
    return Finding(
        rule_id=rule_id,
        severity=severity,
        engine=engine,
        message=message,
        remediation=remediation,
    )


# ── Test _build_fix_prompt ───────────────────────────────────────────────


class TestBuildFixPrompt:
    def test_empty_findings_produces_no_bullets(self) -> None:
        deployment = {"key": "val"}
        prompt = _build_fix_prompt(deployment, [])
        assert "key" in prompt
        assert "val" in prompt
        assert "- [" not in prompt

    def test_single_finding_includes_rule_id_and_message(self) -> None:
        deployment = {"port": 8080}
        findings = [_make_finding(rule_id="BAD_PORT", message="port too low")]
        prompt = _build_fix_prompt(deployment, findings)
        assert "BAD_PORT" in prompt
        assert "port too low" in prompt

    def test_multiple_findings_each_have_bullet(self) -> None:
        deployment = {}
        findings = [
            _make_finding(rule_id="R1", message="m1"),
            _make_finding(rule_id="R2", message="m2"),
        ]
        prompt = _build_fix_prompt(deployment, findings)
        assert prompt.count("- [") == 2

    def test_deployment_with_none_values_serialised(self) -> None:
        deployment: dict = {"a": None}
        prompt = _build_fix_prompt(deployment, [])
        assert "null" in prompt

    def test_deployment_is_json_safe(self) -> None:
        deployment = {"nested": {"deep": True}}
        prompt = _build_fix_prompt(deployment, [])
        # The JSON deployment serialization appears in the prompt body
        expected = json.dumps(deployment, indent=2, default=str, sort_keys=True)
        assert expected in prompt

    def test_includes_remediation_hint_per_finding(self) -> None:
        deployment = {}
        findings = [_make_finding(rule_id="R1")]
        prompt = _build_fix_prompt(deployment, findings)
        assert "(hint:" in prompt


# ── Test make_fix_suggestion_fn ──────────────────────────────────────────


class TestMakeFixSuggestionFn:
    def _make_gateway(self, return_content: str | None = None, raises: bool = False) -> MagicMock:
        gw = MagicMock()
        if raises:
            gw.call_model.side_effect = RuntimeError("gateway down")
        else:
            resp = MagicMock()
            resp.content = return_content
            gw.call_model.return_value = resp
        return gw

    def test_valid_json_dict_returned(self) -> None:
        gw = self._make_gateway(return_content='{"api_key": "new_value"}')
        fn = make_fix_suggestion_fn(gw)
        result = fn({}, [_make_finding()])
        assert result == {"api_key": "new_value"}

    def test_non_dict_json_returns_empty(self) -> None:
        gw = self._make_gateway(return_content='[1, 2, 3]')
        fn = make_fix_suggestion_fn(gw)
        result = fn({}, [_make_finding()])
        assert result == {}

    def test_invalid_json_returns_empty(self) -> None:
        gw = self._make_gateway(return_content="not json at all")
        fn = make_fix_suggestion_fn(gw)
        result = fn({}, [_make_finding()])
        assert result == {}

    def test_gateway_exception_returns_empty(self) -> None:
        gw = self._make_gateway(raises=True)
        fn = make_fix_suggestion_fn(gw)
        result = fn({}, [_make_finding()])
        assert result == {}

    def test_gateway_returns_empty_string_returns_empty(self) -> None:
        gw = self._make_gateway(return_content="")
        fn = make_fix_suggestion_fn(gw)
        result = fn({}, [_make_finding()])
        assert result == {}

    def test_gateway_returns_none_content_handled(self) -> None:
        gw = MagicMock()
        resp = MagicMock()
        resp.content = None
        gw.call_model.return_value = resp
        fn = make_fix_suggestion_fn(gw)
        result = fn({}, [_make_finding()])
        assert result == {}

    def test_custom_profile_and_max_tokens_passed(self) -> None:
        gw = self._make_gateway(return_content='{"x": 1}')
        fn = make_fix_suggestion_fn(gw, profile_id="custom", max_output_tokens=256)
        fn({}, [_make_finding()])
        gw.call_model.assert_called_once()
        args = gw.call_model.call_args.args
        kwargs = gw.call_model.call_args.kwargs
        # profile_id is the first positional arg to call_model
        assert args[0] == "custom"
        assert kwargs["requested_max_output_tokens"] == 256

    def test_messages_include_system_prompt(self) -> None:
        gw = self._make_gateway(return_content='{"x": 1}')
        fn = make_fix_suggestion_fn(gw)
        fn({}, [_make_finding()])
        messages = gw.call_model.call_args.kwargs["messages"]
        assert messages[0] == {"role": "system", "content": _SYSTEM_PROMPT}


# ── Test FixSuggester._deterministic_patch ───────────────────────────────


class TestDeterministicPatch:
    def test_empty_findings_returns_empty_dict(self) -> None:
        detector = MisconfigDetector()
        suggester = FixSuggester(detector)
        result = suggester._deterministic_patch({}, [])
        assert result == {}

    def test_merges_config_patches_from_all_findings(self) -> None:
        detector = MisconfigDetector()
        f1 = _make_finding(rule_id="R1")
        f2 = _make_finding(rule_id="R2")

        with patch.object(
            detector, "remediate", side_effect=[
                {"config_patch": {"a": 1}},
                {"config_patch": {"b": 2}},
            ]
        ):
            suggester = FixSuggester(detector)
            result = suggester._deterministic_patch({}, [f1, f2])
            assert result == {"a": 1, "b": 2}

    def test_last_write_wins_on_conflicting_keys(self) -> None:
        detector = MisconfigDetector()
        f1 = _make_finding()
        f2 = _make_finding()
        with patch.object(
            detector, "remediate", side_effect=[
                {"config_patch": {"key": "first"}},
                {"config_patch": {"key": "second"}},
            ]
        ):
            suggester = FixSuggester(detector)
            result = suggester._deterministic_patch({}, [f1, f2])
            assert result == {"key": "second"}

    def test_skips_non_dict_patch(self) -> None:
        detector = MisconfigDetector()
        with patch.object(
            detector, "remediate", side_effect=[
                {"config_patch": "not_a_dict"},
                {"config_patch": {"valid": True}},
            ]
        ):
            suggester = FixSuggester(detector)
            result = suggester._deterministic_patch(
                {}, [_make_finding(), _make_finding()]
            )
            assert result == {"valid": True}

    def test_skips_missing_config_patch_key(self) -> None:
        detector = MisconfigDetector()
        with patch.object(
            detector, "remediate", return_value={}
        ):
            suggester = FixSuggester(detector)
            result = suggester._deterministic_patch({}, [_make_finding()])
            assert result == {}

    def test_remediate_exception_skipped(self) -> None:
        detector = MisconfigDetector()
        with patch.object(
            detector, "remediate", side_effect=[
                RuntimeError("boom"),
                {"config_patch": {"safe": True}},
            ]
        ):
            suggester = FixSuggester(detector)
            result = suggester._deterministic_patch(
                {}, [_make_finding(), _make_finding()]
            )
            assert result == {"safe": True}

    def test_deployment_arg_not_mutated(self) -> None:
        detector = MisconfigDetector()
        deployment = {"original": True}
        with patch.object(
            detector, "remediate", return_value={"config_patch": {"new": "val"}}
        ):
            suggester = FixSuggester(detector)
            result = suggester._deterministic_patch(deployment, [_make_finding()])
            assert deployment == {"original": True}
            assert result == {"new": "val"}


# ── Test FixSuggester.suggest ────────────────────────────────────────────


class TestFixSuggesterSuggest:
    def test_slm_path_returns_proposed_patch(self) -> None:
        detector = MisconfigDetector()
        def suggest_fn(d, f):
            return {"slm_key": "slm_val"}
        suggester = FixSuggester(detector, suggest_fn)  # type: ignore[arg-type]
        result = suggester.suggest({}, [_make_finding()])
        assert result == {"slm_key": "slm_val"}

    def test_falls_back_when_slm_returns_empty(self) -> None:
        detector = MisconfigDetector()
        def suggest_fn(d, f):
            return {}
        with patch.object(
            detector, "remediate", return_value={"config_patch": {"fallback": True}}
        ):
            suggester = FixSuggester(detector, suggest_fn)  # type: ignore[arg-type]
            result = suggester.suggest({}, [_make_finding()])
            assert result == {"fallback": True}

    def test_falls_back_when_slm_returns_none(self) -> None:
        detector = MisconfigDetector()
        def suggest_fn(d, f):
            return {}  # type: ignore[return-value]
        with patch.object(
            detector, "remediate", return_value={"config_patch": {"fallback": True}}
        ):
            suggester = FixSuggester(detector, suggest_fn)
            result = suggester.suggest({}, [_make_finding()])
            assert result == {"fallback": True}

    def test_falls_back_when_slm_raises(self) -> None:
        detector = MisconfigDetector()

        def raise_fn(d: dict, f: list) -> dict:
            raise RuntimeError("boom")

        with patch.object(
            detector, "remediate", return_value={"config_patch": {"fallback": True}}
        ):
            suggester = FixSuggester(detector, raise_fn)
            result = suggester.suggest({}, [_make_finding()])
            assert result == {"fallback": True}

    def test_no_slm_fn_uses_deterministic_only(self) -> None:
        detector = MisconfigDetector()
        with patch.object(
            detector, "remediate", return_value={"config_patch": {"det": True}}
        ):
            suggester = FixSuggester(detector)
            result = suggester.suggest({}, [_make_finding()])
            assert result == {"det": True}

    def test_empty_findings_with_slm_returns_slm_result(self) -> None:
        detector = MisconfigDetector()
        def suggest_fn(d, f):
            return {"slm": "still_works"}
        suggester = FixSuggester(detector, suggest_fn)  # type: ignore[arg-type]
        result = suggester.suggest({}, [])
        assert result == {"slm": "still_works"}

    def test_suggest_never_raises(self) -> None:
        detector = MisconfigDetector()

        def raise_fn(d: dict, f: list) -> dict:
            raise RuntimeError("catastrophic")

        with patch.object(detector, "remediate", return_value={}):
            suggester = FixSuggester(detector, raise_fn)
            result = suggester.suggest({}, [_make_finding()])
            assert result == {}


# ── Test module exports ──────────────────────────────────────────────────


class TestModuleExports:
    def test_fix_suggest_fn_is_callable_type(self) -> None:
        assert FixSuggestFn is not None

    def test_system_prompt_is_non_empty_str(self) -> None:
        assert isinstance(_SYSTEM_PROMPT, str)
        assert len(_SYSTEM_PROMPT) > 0
        assert "JSON" in _SYSTEM_PROMPT

    def test_guided_json_schema_is_object_with_additional_properties(self) -> None:
        assert _GUIDED_JSON_SCHEMA["type"] == "object"
        assert _GUIDED_JSON_SCHEMA["additionalProperties"] is True
