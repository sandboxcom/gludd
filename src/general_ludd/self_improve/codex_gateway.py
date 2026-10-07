"""Local-model decoding and proposal gateway implementation."""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast

from general_ludd.self_improve.codex_protocol import (
    _COMPACT_COMMIT_MESSAGE,
    _COMPACT_EDIT_FIELDS,
    _COMPACT_EDITABLE_RANGES_MARKER,
    _COMPACT_FOCUS_PATH_MARKER,
    _COMPACT_MAX_CONTENT_BYTES,
    _COMPACT_MAX_EDITS,
    _COMPACT_MAX_SCOPE_MARKER_BYTES,
    _COMPACT_OPERATION_BY_EMPTY_TEXT,
    _COMPACT_PROPOSAL_JSON_SCHEMA,
    _COMPACT_ROOT_FIELDS,
    _COMPACT_SPAN_MAX_EDITS,
    _COMPACT_SYSTEM_PROMPT,
    _DETERMINISTIC_DECODE_SEED,
    _DETERMINISTIC_DECODE_TEMPERATURE,
    _LEGACY_COMPACT_EDIT_FIELDS,
    _LEGACY_COMPACT_PROPOSAL_JSON_SCHEMA,
    _LEGACY_COMPACT_PROPOSAL_PROTOCOL_VERSION,
    _LEGACY_COMPACT_SYSTEM_PROMPT,
    _PROPOSAL_JSON_SCHEMA,
    _SAFE_FINISH_REASONS,
    _STRUCTURED_CANARY_EXPECTED,
    _STRUCTURED_CANARY_PROMPT,
    _STRUCTURED_CANARY_SCHEMA,
    _STRUCTURED_CANARY_TOKENS,
    _STRUCTURED_OUTPUT_REQUIRE_STOP,
    CompactLineSpan,
    CompactSpanProposal,
    ProposalContract,
    ProposalManifest,
    _ChatLocalModel,
    _compact_proposal_schema_for_ranges,
    _GrammarFactory,
    _LocalModel,
    _ModelFactory,
    _proposal_sampling_arguments,
    _safe_relative_path,
    _validated_compact_editable_ranges,
    decode_prompt_batch,
)


class _CompatibilityDefaults(Protocol):
    """Late-bound facade defaults retained as public monkeypatch seams."""

    _default_model_factory: _ModelFactory
    _default_json_schema_grammar: _GrammarFactory


def _compatibility_defaults() -> _CompatibilityDefaults:
    """Resolve defaults after the compatibility facade has finished importing."""
    module = importlib.import_module("general_ludd.self_improve.codex_comparison")
    return cast(_CompatibilityDefaults, module)


def _safe_finish_reason(choice: Mapping[object, object]) -> str:
    """Return one allowlisted finish classification without model-controlled text."""
    raw = choice.get("finish_reason")
    return raw if isinstance(raw, str) and raw in _SAFE_FINISH_REASONS else "unknown"


def _safe_token_count(output: Mapping[object, object], field: str) -> str:
    """Return a non-negative token count or an explicit unknown marker."""
    usage = output.get("usage")
    value = usage.get(field) if isinstance(usage, Mapping) else None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return str(value)
    return "unknown"


def _trusted_compact_editable_ranges(
    prompt: str,
) -> tuple[tuple[int, int], ...] | None:
    """Read only one canonical leading parent scope marker, never source labels."""
    first_line = prompt.partition("\n")[0]
    if not first_line.startswith(_COMPACT_EDITABLE_RANGES_MARKER):
        if _COMPACT_EDITABLE_RANGES_MARKER in prompt:
            raise ValueError("compact editable-range marker must be the first prompt line")
        return None
    if prompt.count(_COMPACT_EDITABLE_RANGES_MARKER) != 1:
        raise ValueError("compact prompt must contain exactly one editable-range marker")
    if len(first_line.encode("utf-8")) > _COMPACT_MAX_SCOPE_MARKER_BYTES:
        raise ValueError(
            "compact editable-range marker exceeds "
            f"{_COMPACT_MAX_SCOPE_MARKER_BYTES} bytes"
        )
    if not first_line.isascii():
        raise ValueError("compact editable-range marker must be ASCII")
    encoded = first_line.removeprefix(_COMPACT_EDITABLE_RANGES_MARKER)
    try:
        value = json.loads(encoded)
    except (RecursionError, ValueError) as exc:
        raise ValueError("compact editable-range marker is not canonical JSON") from exc
    if not isinstance(value, list) or not all(
        isinstance(item, list) and len(item) == 2 for item in value
    ):
        raise ValueError("compact editable-range marker must contain integer pairs")
    ranges = tuple((item[0], item[1]) for item in value)
    validated = _validated_compact_editable_ranges(ranges)
    canonical = json.dumps(
        [list(item) for item in validated],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    if encoded != canonical:
        raise ValueError("compact editable-range marker is not canonical JSON")
    return validated


def bind_compact_focus_path(
    prompt: str,
    focus_path: str,
    *,
    editable_ranges: tuple[tuple[int, int], ...] | None = None,
) -> str:
    """Bind one parent-trusted path and optional exact scope to a compact prompt."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("compact prompt must be non-empty")
    if _COMPACT_FOCUS_PATH_MARKER in prompt:
        raise ValueError("compact prompt already contains a focus-path marker")
    if not isinstance(focus_path, str) or not _safe_relative_path(focus_path):
        raise ValueError("compact focus path is not repository-relative and confined")
    path_bound = f"{_COMPACT_FOCUS_PATH_MARKER}{focus_path}\n{prompt}"
    if editable_ranges is None:
        return path_bound
    if _COMPACT_EDITABLE_RANGES_MARKER in prompt:
        raise ValueError("compact prompt already contains an editable-range marker")
    validated = _validated_compact_editable_ranges(editable_ranges)
    encoded = json.dumps(
        [list(item) for item in validated],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    marker_line = f"{_COMPACT_EDITABLE_RANGES_MARKER}{encoded}"
    if len(marker_line.encode("ascii")) > _COMPACT_MAX_SCOPE_MARKER_BYTES:
        raise ValueError(
            "compact editable-range marker exceeds "
            f"{_COMPACT_MAX_SCOPE_MARKER_BYTES} bytes"
        )
    return f"{marker_line}\n{_COMPACT_FOCUS_PATH_MARKER}{focus_path}\n{prompt}"


def _trusted_compact_focus_path(prompt: str) -> str:
    """Recover exactly one parent-authored focus path, never model output."""
    candidates = tuple(
        line.removeprefix(_COMPACT_FOCUS_PATH_MARKER)
        for line in prompt.splitlines()
        if line.startswith(_COMPACT_FOCUS_PATH_MARKER)
    )
    if len(candidates) != 1 or not _safe_relative_path(candidates[0]):
        raise ValueError("compact prompt must contain exactly one trusted focus path")
    return candidates[0]


def _model_visible_compact_prompt(prompt: str) -> str:
    """Remove validated parent-only bindings before sending a prompt to the model."""
    _trusted_compact_editable_ranges(prompt)
    focus_path = _trusted_compact_focus_path(prompt)
    first_line, separator, remainder = prompt.partition("\n")
    if first_line.startswith(_COMPACT_EDITABLE_RANGES_MARKER):
        if not separator:
            raise ValueError("compact prompt has no model-visible task")
        first_line, separator, remainder = remainder.partition("\n")
    expected_focus = f"{_COMPACT_FOCUS_PATH_MARKER}{focus_path}"
    if first_line != expected_focus or not separator or not remainder.strip():
        raise ValueError("compact parent bindings are not in canonical leading order")
    return remainder


def _completion_text(
    output: object,
    *,
    phase: str,
    budget: int,
    require_stop: bool,
) -> str:
    """Extract completion text with secret-safe finish and token diagnostics."""
    if not isinstance(output, Mapping):
        raise ValueError("local model returned a non-object response")
    choices = output.get("choices")
    if (
        not isinstance(choices, list)
        or not choices
        or not isinstance(choices[0], Mapping)
    ):
        raise ValueError("local model response has no choices")
    choice = choices[0]
    finish = _safe_finish_reason(choice)
    diagnostic = (
        f"phase={phase} finish={finish} "
        f"prompt_tokens={_safe_token_count(output, 'prompt_tokens')} "
        f"completion_tokens={_safe_token_count(output, 'completion_tokens')} "
        f"total_tokens={_safe_token_count(output, 'total_tokens')} "
        f"budget={budget}"
    )
    print(f"SELF_IMPROVE_LOCAL_DECODE {diagnostic}", flush=True)
    if finish == "length":
        raise ValueError(
            "local model exhausted the proposal token budget before completion; "
            + diagnostic
        )
    if require_stop and finish != "stop":
        raise ValueError("local model did not complete structured output; " + diagnostic)
    text = choice.get("text")
    if not isinstance(text, str):
        message = choice.get("message")
        text = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(text, str) or not text.strip():
        raise ValueError("local model response has no proposal text; " + diagnostic)
    encoded_text = text.encode("utf-8")
    print(
        "SELF_IMPROVE_LOCAL_OUTPUT "
        f"phase={phase} output_bytes={len(encoded_text)} "
        f"output_sha256={hashlib.sha256(encoded_text).hexdigest()}",
        flush=True,
    )
    return text


def _decode_compact_span_proposal(
    raw: str,
    *,
    focus_path: str,
) -> CompactSpanProposal:
    """Parse one strict compact-v4 response without trusting model coordinates."""
    if not isinstance(focus_path, str) or not _safe_relative_path(focus_path):
        raise ValueError("compact focus path is not repository-relative and confined")
    stripped = raw.strip()
    try:
        value = json.loads(stripped)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(
            "compact-v4 proposal is not one complete JSON object; "
            f"output_bytes={len(stripped.encode('utf-8'))}"
        ) from exc
    if not isinstance(value, dict) or set(value) != _COMPACT_ROOT_FIELDS:
        raise ValueError("compact proposal must contain exactly e")
    edits_raw = value["e"]
    if (
        not isinstance(edits_raw, list)
        or not 1 <= len(edits_raw) <= _COMPACT_SPAN_MAX_EDITS
    ):
        detail = (
            ""
            if not isinstance(edits_raw, list)
            or len(edits_raw) <= _COMPACT_SPAN_MAX_EDITS
            else (
                f"; received_edits=>{_COMPACT_SPAN_MAX_EDITS} "
                f"max_edits={_COMPACT_SPAN_MAX_EDITS}"
            )
        )
        raise ValueError(
            "compact proposal edits must contain "
            f"1..{_COMPACT_SPAN_MAX_EDITS} entries{detail}"
        )
    edits: list[CompactLineSpan] = []
    for item in edits_raw:
        if not isinstance(item, dict) or set(item) != _COMPACT_EDIT_FIELDS:
            raise ValueError("each compact edit must contain exactly n, s, and z")
        start_line = item["s"]
        old_line_count = item["n"]
        new_text = item["z"]
        if isinstance(start_line, bool) or isinstance(old_line_count, bool):
            raise ValueError("compact span coordinates must be integers, not booleans")
        if not isinstance(start_line, int) or not isinstance(old_line_count, int):
            raise ValueError("compact span coordinates must be integers, not booleans")
        if not isinstance(new_text, str):
            raise ValueError("compact span new text must be a string")
        edits.append(
            CompactLineSpan(
                start_line=start_line,
                old_line_count=old_line_count,
                new_text=new_text,
            )
        )
    canonical_edits = tuple(sorted(edits, key=lambda edit: edit.start_line))
    return CompactSpanProposal(focus_path=focus_path, edits=canonical_edits)


def _decode_compact_proposal(
    raw: str,
    contract: ProposalContract,
    *,
    focus_path: str,
) -> ProposalManifest:
    """Expand parent-owned fields and revalidate one compact single-file object."""
    if not isinstance(focus_path, str) or not _safe_relative_path(focus_path):
        raise ValueError("compact focus path is not repository-relative and confined")
    stripped = raw.strip()
    try:
        value = json.loads(stripped)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(
            "compact proposal is not one complete JSON object; "
            f"output_bytes={len(stripped.encode('utf-8'))}"
        ) from exc
    if not isinstance(value, dict) or set(value) != _COMPACT_ROOT_FIELDS:
        raise ValueError("compact proposal must contain exactly e")
    edits_raw = value["e"]
    if (
        not isinstance(edits_raw, list)
        or not 1 <= len(edits_raw) <= _COMPACT_MAX_EDITS
    ):
        raise ValueError(
            f"compact proposal edits must contain 1..{_COMPACT_MAX_EDITS} entries"
        )
    edits: list[dict[str, object]] = []
    content_bytes = 0
    for item in edits_raw:
        if not isinstance(item, dict) or set(item) != _LEGACY_COMPACT_EDIT_FIELDS:
            raise ValueError("each compact edit must contain exactly a and z")
        old_text = item["a"]
        new_text = item["z"]
        if not isinstance(old_text, str) or not isinstance(new_text, str):
            raise ValueError("compact edit text fields must be strings")
        content_bytes += len(old_text.encode("utf-8"))
        content_bytes += len(new_text.encode("utf-8"))
        if content_bytes > _COMPACT_MAX_CONTENT_BYTES:
            raise ValueError(
                f"compact edit content exceeds {_COMPACT_MAX_CONTENT_BYTES} bytes"
            )
        operation = _COMPACT_OPERATION_BY_EMPTY_TEXT.get(
            (not old_text, not new_text)
        )
        if operation is None:
            raise ValueError("compact edit must change content")
        edits.append(
            {
                "operation": operation,
                "path": focus_path,
                "old_text": old_text,
                "new_text": new_text,
            }
        )
    expanded = json.dumps(
        {
            "schema_version": 1,
            "baseline_sha": contract.baseline_sha,
            "task_id": contract.task_id,
            "edits": edits,
            "tests": list(contract.tests),
            "make_commands": list(contract.make_commands),
            "commit_message": _COMPACT_COMMIT_MESSAGE,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return ProposalManifest.from_json(expanded)


class LocalProposalGateway:
    """Generate strict proposal JSON with one explicit local GGUF model."""

    def __init__(
        self,
        model_path: Path,
        *,
        model_factory: _ModelFactory | None = None,
        grammar_factory: _GrammarFactory | None = None,
        n_gpu_layers: int | None = None,
    ) -> None:
        """Bind one GGUF and an optional hardware-derived GPU offload boundary."""
        if not model_path.is_file():
            raise FileNotFoundError(f"local GGUF is not readable: {model_path}")
        if (
            isinstance(n_gpu_layers, bool)
            or not isinstance(n_gpu_layers, (int, type(None)))
            or (n_gpu_layers is not None and n_gpu_layers < -1)
        ):
            raise ValueError("n_gpu_layers must be None or an integer of at least -1")
        defaults = _compatibility_defaults()
        self._model_path = model_path
        self._model_factory = model_factory or defaults._default_model_factory
        self._grammar_factory = grammar_factory
        if self._grammar_factory is None and model_factory is None:
            self._grammar_factory = defaults._default_json_schema_grammar
        self._n_gpu_layers = n_gpu_layers
        self._model: _LocalModel | None = None
        self._structured_canary_protocols: set[str] = set()

    def _load_model(self) -> _LocalModel:
        """Lazily construct exactly one retained model instance."""
        if self._model is None:
            if self._n_gpu_layers is None:
                self._model = self._model_factory(
                    model_path=str(self._model_path),
                    n_ctx=0,
                    verbose=False,
                )
            else:
                self._model = self._model_factory(
                    model_path=str(self._model_path),
                    n_ctx=0,
                    verbose=False,
                    n_gpu_layers=self._n_gpu_layers,
                )
        return self._model

    def _grammar_for_schema(self, schema: dict[str, object]) -> object | None:
        """Build a per-call grammar when the production or injected helper is bound."""
        if self._grammar_factory is None:
            return None
        grammar = self._grammar_factory(schema)
        if grammar is None:
            raise RuntimeError(
                "llama.cpp JSON-schema grammar construction returned no grammar"
            )
        return grammar

    def _run_structured_canary(
        self,
        model: _ChatLocalModel,
        proposal_protocol: str,
    ) -> None:
        """Prove the retained model can finish a tiny schema before task decoding."""
        if proposal_protocol in self._structured_canary_protocols:
            return
        system_prompt = (
            _LEGACY_COMPACT_SYSTEM_PROMPT
            if proposal_protocol == _LEGACY_COMPACT_PROPOSAL_PROTOCOL_VERSION
            else _COMPACT_SYSTEM_PROMPT
        )
        schema = _STRUCTURED_CANARY_SCHEMA
        output = model.create_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": _STRUCTURED_CANARY_PROMPT},
            ],
            max_tokens=_STRUCTURED_CANARY_TOKENS,
            temperature=_DETERMINISTIC_DECODE_TEMPERATURE,
            seed=_DETERMINISTIC_DECODE_SEED,
            response_format={"type": "json_object", "schema": schema},
            grammar=self._grammar_for_schema(schema),
        )
        try:
            text = _completion_text(
                output,
                phase="canary",
                budget=_STRUCTURED_CANARY_TOKENS,
                require_stop=_STRUCTURED_OUTPUT_REQUIRE_STOP,
            )
            value = json.loads(text)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"local structured-output canary failed: {exc}") from exc
        if value != _STRUCTURED_CANARY_EXPECTED:
            raise ValueError(
                "local structured-output canary failed: response did not match contract"
            )
        self._structured_canary_protocols.add(proposal_protocol)

    def _propose_compact(
        self,
        model: _ChatLocalModel,
        prompt: str,
        contract: ProposalContract,
    ) -> ProposalManifest | CompactSpanProposal:
        """Decode one contract-bound compact proposal through chat completion."""
        self._run_structured_canary(model, contract.proposal_protocol)
        legacy = contract.proposal_protocol == _LEGACY_COMPACT_PROPOSAL_PROTOCOL_VERSION
        if legacy:
            schema = _LEGACY_COMPACT_PROPOSAL_JSON_SCHEMA
        else:
            scope_ranges = _trusted_compact_editable_ranges(prompt)
            schema = (
                _COMPACT_PROPOSAL_JSON_SCHEMA
                if scope_ranges is None
                else _compact_proposal_schema_for_ranges(scope_ranges)
            )
        sampling_arguments = _proposal_sampling_arguments(
            contract.sampling_profile,
            sampling_seed=contract.sampling_seed,
        )
        budget = contract.proposal_output_token_budget
        output = model.create_chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        _LEGACY_COMPACT_SYSTEM_PROMPT
                        if legacy
                        else _COMPACT_SYSTEM_PROMPT
                    ),
                },
                {"role": "user", "content": _model_visible_compact_prompt(prompt)},
            ],
            max_tokens=budget,
            response_format={"type": "json_object", "schema": schema},
            grammar=self._grammar_for_schema(schema),
            **sampling_arguments,
        )
        text = _completion_text(
            output,
            phase="proposal",
            budget=budget,
            require_stop=_STRUCTURED_OUTPUT_REQUIRE_STOP,
        )
        focus_path = _trusted_compact_focus_path(prompt)
        if not legacy:
            return _decode_compact_span_proposal(text, focus_path=focus_path)
        return _decode_compact_proposal(text, contract, focus_path=focus_path)

    def _uncontracted_output(self, model: _LocalModel, prompt: str) -> object:
        """Run the historical unconstrained proposal adapter."""
        if hasattr(model, "create_chat_completion"):
            chat_model = cast(_ChatLocalModel, model)
            schema = _PROPOSAL_JSON_SCHEMA
            return chat_model.create_chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Return exactly one valid JSON proposal object. "
                            "Do not emit markdown or prose."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=4096,
                temperature=0.0,
                seed=0,
                response_format={"type": "json_object", "schema": schema},
                grammar=self._grammar_for_schema(schema),
            )
        return model(prompt, max_tokens=4096, temperature=0.0, echo=False)

    def propose_envelope(
        self,
        request: str,
        *,
        contract: ProposalContract,
        response_instruction: str,
        response_schema_json: str,
    ) -> str:
        """Submit one canonical provider-neutral envelope without reconstruction."""
        if not isinstance(contract, ProposalContract):
            raise ValueError("proposal envelope contract is invalid")
        if (
            type(response_instruction) is not str
            or not response_instruction.strip()
            or "\x00" in response_instruction
        ):
            raise ValueError("proposal envelope response instruction is invalid")
        try:
            schema = json.loads(response_schema_json)
        except (TypeError, json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("proposal envelope response schema is invalid") from None
        canonical_schema = json.dumps(
            schema,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        if not isinstance(schema, dict) or canonical_schema != response_schema_json:
            raise ValueError("proposal envelope response schema is not canonical")
        decode_prompt_batch(request)
        contract.verify_sampling_context(request)
        model = self._load_model()
        if not hasattr(model, "create_chat_completion"):
            raise ValueError("proposal envelope requires chat-completion support")
        chat_model = cast(_ChatLocalModel, model)
        self._run_structured_canary(chat_model, contract.proposal_protocol)
        budget = contract.proposal_output_token_budget
        output = chat_model.create_chat_completion(
            messages=[
                {"role": "system", "content": response_instruction},
                {"role": "user", "content": request},
            ],
            max_tokens=budget,
            response_format={"type": "json_object", "schema": schema},
            grammar=self._grammar_for_schema(schema),
            **_proposal_sampling_arguments(
                contract.sampling_profile,
                sampling_seed=contract.sampling_seed,
            ),
        )
        return _completion_text(
            output,
            phase="proposal",
            budget=budget,
            require_stop=_STRUCTURED_OUTPUT_REQUIRE_STOP,
        )

    def propose(
        self,
        prompt: str,
        *,
        contract: ProposalContract | None = None,
    ) -> ProposalManifest | CompactSpanProposal:
        """Run deterministic decode and parse one bounded proposal."""
        model = self._load_model()
        if contract is not None:
            if not hasattr(model, "create_chat_completion"):
                raise ValueError(
                    "compact structured proposal requires chat-completion support"
                )
            return self._propose_compact(cast(_ChatLocalModel, model), prompt, contract)
        output = self._uncontracted_output(model, prompt)
        text = _completion_text(
            output,
            phase="proposal",
            budget=4096,
            require_stop=False,
        )
        return ProposalManifest.from_json(_extract_json_object(text))


def _extract_json_object(text: str) -> str:
    """Extract one object while keeping model-controlled diagnostics bounded."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            stripped = "\n".join(lines[1:-1])
            if stripped.lstrip().startswith("json"):
                stripped = stripped.lstrip()[4:].lstrip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    diagnostic = (
        f"output_bytes={len(stripped.encode('utf-8'))} "
        f"has_json_start={start >= 0} has_json_end={end >= start}"
    )
    if start < 0:
        raise ValueError(f"local model response has no JSON start: {diagnostic}")
    if end < start:
        raise ValueError(f"local model response contains incomplete JSON: {diagnostic}")
    return stripped[start : end + 1]


__all__ = ["LocalProposalGateway", "bind_compact_focus_path"]
