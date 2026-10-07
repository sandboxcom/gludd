"""Tests split from :mod:`tests.unit.test_self_improve_codex_comparison` by coherent behavior."""

from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from scripts.makefile_layout import compose_makefile

import general_ludd.self_improve.codex_comparison as comparison_module
from general_ludd.self_improve.codex_comparison import (
    LocalProposalGateway,
    ProposalContract,
)
from tests.unit.test_self_improve_codex_comparison import (
    _contract,
    _expand_span_proposals,
    _span_proposal,
)


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ("{", "complete JSON"),
        ("{}", "exactly e"),
        ('{"e":[]}', "1..16"),
        ('{"e":[7]}', "compact edit"),
        ('{"e":[{"a":7,"z":"y"}]}', "text fields"),
        ('{"e":[{"p":"src/example.py","a":"x","z":"y"}]}', "exactly a and z"),
    ],
)
def test_compact_proposal_codec_rejects_ambiguous_or_unsafe_output(
    raw: str,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        comparison_module._decode_compact_proposal(
            raw,
            _contract(),
            focus_path="src/general_ludd/example.py",
        )


def test_compact_codec_rejects_untrusted_or_duplicate_focus_marker() -> None:
    raw = json.dumps({"e": [{"a": "x", "z": "y"}]})
    with pytest.raises(ValueError, match="focus path"):
        comparison_module._decode_compact_proposal(
            raw,
            _contract(),
            focus_path="../escape.py",
        )
    prompt = comparison_module.bind_compact_focus_path(
        "bounded task",
        "src/general_ludd/example.py",
    )
    with pytest.raises(ValueError, match="already contains"):
        comparison_module.bind_compact_focus_path(
            prompt,
            "src/general_ludd/example.py",
        )


def test_compact_v4_gateway_emits_only_bounded_line_span_fields(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    calls: list[dict[str, object]] = []

    class SpanModel:
        def __call__(
            self,
            prompt: str,
            *,
            max_tokens: int,
            temperature: float,
            echo: bool,
        ) -> object:
            del prompt, max_tokens, temperature, echo
            raise AssertionError("compact mode must use chat completion")

        def create_chat_completion(self, **kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            content = (
                '{"ok":true}'
                if len(calls) == 1
                else '{"e":[{"s":2,"n":1,"z":"changed\\n"}]}'
            )
            return {
                "choices": [
                    {"finish_reason": "stop", "message": {"content": content}}
                ]
            }

    contract = replace(
        _contract(),
        proposal_protocol="self-improve-compact-proposal-v4",
    )
    prompt = comparison_module.bind_compact_focus_path(
        "Use the numbered source lines.",
        "src/general_ludd/example.py",
    )

    proposal = LocalProposalGateway(
        model_path,
        model_factory=lambda **_kwargs: SpanModel(),
    ).propose(prompt, contract=contract)

    assert isinstance(proposal, comparison_module.CompactSpanProposal)
    assert proposal.focus_path == "src/general_ludd/example.py"
    assert proposal.edits == (
        comparison_module.CompactLineSpan(start_line=2, old_line_count=1, new_text="changed\n"),
    )
    schema = calls[1]["response_format"]
    assert isinstance(schema, dict)
    item = schema["schema"]["properties"]["e"]["items"]
    assert item["required"] == ["s", "n", "z"]
    assert set(item["properties"]) == {"s", "n", "z"}
    assert calls[1]["max_tokens"] == 4096


def test_compact_v4_repair_uses_reproducible_non_greedy_sampling_only(
    tmp_path: Path,
) -> None:
    """Diversify a repair deterministically without changing canary or first-pass decode."""
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    prompt = comparison_module.bind_compact_focus_path(
        "Use the numbered source lines.",
        "src/general_ludd/example.py",
        editable_ranges=((1, 3),),
    )
    normal_contract = replace(
        _contract(),
        proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
    )
    request = comparison_module.encode_prompt_batch(
        (prompt,),
        protocol_digest="f" * 64,
    )
    repair_contract = ProposalContract.for_request(
        request=request,
        baseline_sha=normal_contract.baseline_sha,
        task_id=normal_contract.task_id,
        tests=normal_contract.tests,
        make_commands=normal_contract.make_commands,
        proposal_protocol=normal_contract.proposal_protocol,
        sampling_profile=(
            comparison_module.COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID
        ),
    )

    def run(contract: ProposalContract) -> list[dict[str, object]]:
        calls: list[dict[str, object]] = []

        class SpanModel:
            def create_chat_completion(self, **kwargs: object) -> dict[str, object]:
                calls.append(dict(kwargs))
                content = (
                    '{"ok":true}'
                    if len(calls) == 1
                    else '{"e":[{"s":1,"n":1,"z":"changed\\n"}]}'
                )
                return {
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": content}}
                    ]
                }

            def __call__(self, prompt: str, **kwargs: object) -> object:
                del prompt, kwargs
                raise AssertionError("compact mode must use chat completion")

        proposal = LocalProposalGateway(
            model_path,
            model_factory=lambda **_kwargs: SpanModel(),
        ).propose(prompt, contract=contract)
        assert isinstance(proposal, comparison_module.CompactSpanProposal)
        return calls

    normal_calls = run(normal_contract)
    first_repair_calls = run(repair_contract)
    second_repair_calls = run(repair_contract)

    assert normal_calls[0]["temperature"] == 0.0
    assert normal_calls[0]["seed"] == 0
    assert normal_calls[1]["temperature"] == 0.0
    assert normal_calls[1]["seed"] == 0
    assert "top_p" not in normal_calls[1]
    assert "top_k" not in normal_calls[1]
    assert first_repair_calls[0]["temperature"] == 0.0
    assert first_repair_calls[0]["seed"] == 0
    assert "top_p" not in first_repair_calls[0]
    assert "top_k" not in first_repair_calls[0]
    assert {
        key: first_repair_calls[1][key]
        for key in ("temperature", "top_p", "top_k", "seed")
    } == {
        "temperature": 0.8,
        "top_p": 0.95,
        "top_k": 40,
        "seed": repair_contract.sampling_seed,
    }
    assert first_repair_calls[1] == second_repair_calls[1]
    assert first_repair_calls[1]["response_format"] == normal_calls[1]["response_format"]
    assert first_repair_calls[1]["max_tokens"] == normal_calls[1]["max_tokens"]


def test_compact_v4_gateway_passes_distinct_explicit_json_schema_grammars(
    tmp_path: Path,
) -> None:
    """Use llama.cpp grammar objects for both the canary and v4 proposal."""
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    calls: list[dict[str, object]] = []
    grammar_schemas: list[dict[str, object]] = []
    grammars: list[object] = []

    def grammar_factory(schema: dict[str, object]) -> object:
        grammar_schemas.append(schema)
        grammar = object()
        grammars.append(grammar)
        return grammar

    class SpanModel:
        def __call__(self, prompt: str, **kwargs: object) -> object:
            del prompt, kwargs
            raise AssertionError("raw completion must not be used")

        def create_chat_completion(self, **kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            content = (
                '{"ok":true}'
                if len(calls) == 1
                else '{"e":[{"s":1,"n":1,"z":"changed\\n"}]}'
            )
            return {
                "choices": [
                    {"finish_reason": "stop", "message": {"content": content}}
                ]
            }

    contract = replace(
        _contract(),
        proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
    )
    prompt = comparison_module.bind_compact_focus_path(
        "Use the numbered source lines.",
        "src/general_ludd/example.py",
    )

    proposal = LocalProposalGateway(
        model_path,
        model_factory=lambda **_kwargs: SpanModel(),
        grammar_factory=grammar_factory,
    ).propose(prompt, contract=contract)

    assert isinstance(proposal, comparison_module.CompactSpanProposal)
    assert grammar_schemas == [
        comparison_module._STRUCTURED_CANARY_SCHEMA,
        comparison_module._COMPACT_PROPOSAL_JSON_SCHEMA,
    ]
    assert calls[0]["grammar"] is grammars[0]
    assert calls[1]["grammar"] is grammars[1]
    assert grammars[0] is not grammars[1]


def test_compact_v4_gateway_compiles_parent_scope_into_integer_enum(
    tmp_path: Path,
) -> None:
    """Constrain s to exact shown-section boundaries before model sampling."""
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    calls: list[dict[str, object]] = []
    grammar_schemas: list[dict[str, object]] = []

    class SpanModel:
        def __call__(self, prompt: str, **kwargs: object) -> object:
            del prompt, kwargs
            raise AssertionError("raw completion must not be used")

        def create_chat_completion(self, **kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            content = (
                '{"ok":true}'
                if len(calls) == 1
                else '{"e":[{"s":3,"n":1,"z":"changed\\n"}]}'
            )
            return {
                "choices": [
                    {"finish_reason": "stop", "message": {"content": content}}
                ]
            }

    def grammar_factory(schema: dict[str, object]) -> object:
        grammar_schemas.append(schema)
        return object()

    prompt = comparison_module.bind_compact_focus_path(
        "Use only the numbered source lines.",
        "src/general_ludd/example.py",
        editable_ranges=((3, 6), (10, 12)),
    )
    proposal = LocalProposalGateway(
        model_path,
        model_factory=lambda **_kwargs: SpanModel(),
        grammar_factory=grammar_factory,
    ).propose(
        prompt,
        contract=replace(
            _contract(),
            proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
        ),
    )

    assert isinstance(proposal, comparison_module.CompactSpanProposal)
    root_properties = cast(dict[str, object], grammar_schemas[1]["properties"])
    edits = cast(dict[str, object], root_properties["e"])
    item = cast(dict[str, object], edits["items"])
    item_properties = cast(dict[str, object], item["properties"])
    assert item_properties["s"] == {
        "type": "integer",
        "enum": [3, 4, 5, 6, 10, 11, 12],
    }
    assert item_properties["n"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 3,
    }
    assert calls[1]["response_format"] == {
        "type": "json_object",
        "schema": grammar_schemas[1],
    }


def test_compact_v4_gateway_hides_parent_bindings_from_model_visible_prompt(
    tmp_path: Path,
) -> None:
    """Keep trusted decoder metadata out of model-authored replacement text."""
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    calls: list[dict[str, object]] = []

    class SpanModel:
        def __call__(
            self,
            prompt: str,
            *,
            max_tokens: int,
            temperature: float,
            echo: bool,
        ) -> object:
            del prompt, max_tokens, temperature, echo
            raise AssertionError("compact v4 must use chat completion")

        def create_chat_completion(self, **kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            content = (
                '{"ok":true}'
                if len(calls) == 1
                else '{"e":[{"s":3,"n":1,"z":"value = 2\\n"}]}'
            )
            return {
                "choices": [
                    {"finish_reason": "stop", "message": {"content": content}}
                ]
            }

    def model_factory(
        *,
        model_path: str,
        n_ctx: int,
        verbose: bool,
        n_gpu_layers: int = 0,
    ) -> SpanModel:
        del model_path, n_ctx, verbose, n_gpu_layers
        return SpanModel()

    visible = (
        "EDIT_TASK_BEGIN\nRepair the catalog mapping.\nEDIT_TASK_END\n"
        "FOCUS_BASELINE_BEGIN\n"
        "FILE src/general_ludd/example.py state=present\n"
        "LINES 3-3\nL3|value = 1\n"
        "FOCUS_BASELINE_END"
    )
    bound = comparison_module.bind_compact_focus_path(
        visible,
        "src/general_ludd/example.py",
        editable_ranges=((3, 4),),
    )

    LocalProposalGateway(
        model_path,
        model_factory=model_factory,
        grammar_factory=lambda _schema: object(),
    ).propose(
        bound,
        contract=replace(
            _contract(),
            proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
        ),
    )

    messages = cast(list[dict[str, str]], calls[1]["messages"])
    assert messages[-1] == {"role": "user", "content": visible}
    assert "Repair the catalog mapping." in messages[-1]["content"]
    assert "FILE src/general_ludd/example.py" in messages[-1]["content"]
    assert "GLUDD_SELF_IMPROVE_" not in messages[-1]["content"]


def test_compact_v4_scope_marker_collision_and_enum_overflow_fail_closed() -> None:
    """Trust only one parent-prepended scope and bound grammar construction."""
    marker = "GLUDD_SELF_IMPROVE_EDITABLE_RANGES="
    with pytest.raises(ValueError, match="already contains an editable-range marker"):
        comparison_module.bind_compact_focus_path(
            f"task text\n{marker}[[1,2]]",
            "src/general_ludd/example.py",
            editable_ranges=((1, 2),),
        )

    with pytest.raises(ValueError, match="scope coordinate enum exceeds 2048"):
        comparison_module.bind_compact_focus_path(
            "bounded task",
            "src/general_ludd/example.py",
            editable_ranges=((1, 2049),),
        )


@pytest.mark.parametrize(
    ("encoded", "match"),
    [
        ("[[1,3],[1,3]]", "ordered half-open ranges"),
        ("[[1,4],[3,5]]", "ordered half-open ranges"),
        ("[[3,5],[1,3]]", "ordered half-open ranges"),
        ("[[1, 3]]", "not canonical JSON"),
        ("[[1,3]]\N{NO-BREAK SPACE}", "must be ASCII"),
        (f"[[{'9' * 5000},1]]", "not canonical JSON"),
    ],
)
def test_compact_v4_scope_marker_rejects_noncanonical_sections(
    encoded: str,
    match: str,
) -> None:
    """Reject duplicate, overlapping, unordered, or noncanonical scope markers."""
    marker = "GLUDD_SELF_IMPROVE_EDITABLE_RANGES="
    prompt = (
        f"{marker}{encoded}\n"
        "GLUDD_SELF_IMPROVE_FOCUS_PATH=src/general_ludd/example.py\n"
        "bounded task"
    )

    with pytest.raises(ValueError, match=match):
        comparison_module._trusted_compact_editable_ranges(prompt)


def test_compact_v4_scope_marker_has_an_independent_byte_bound() -> None:
    """Reject an oversized leading marker before JSON parsing or grammar work."""
    marker = "GLUDD_SELF_IMPROVE_EDITABLE_RANGES="
    oversized = marker + ("0" * (16_384 - len(marker) + 1))

    with pytest.raises(ValueError, match="editable-range marker exceeds 16384 bytes"):
        comparison_module._trusted_compact_editable_ranges(f"{oversized}\nbounded task")


def test_compact_v4_scope_coordinates_cannot_exceed_baseline_byte_space() -> None:
    """Reject sparse huge coordinates even when their enum cardinality is small."""
    with pytest.raises(ValueError, match="outside bounded baseline coordinates"):
        comparison_module._compact_proposal_schema_for_ranges(
            ((1_048_577, 1_048_578),)
        )


@pytest.mark.parametrize(
    ("prompt", "match"),
    [
        (
            "bounded task\nGLUDD_SELF_IMPROVE_EDITABLE_RANGES=[[1,2]]",
            "must be the first prompt line",
        ),
        (
            "GLUDD_SELF_IMPROVE_EDITABLE_RANGES=[[1,2]]\n"
            "GLUDD_SELF_IMPROVE_EDITABLE_RANGES=[[3,4]]",
            "exactly one editable-range marker",
        ),
        (
            "GLUDD_SELF_IMPROVE_EDITABLE_RANGES={\nbounded task",
            "not canonical JSON",
        ),
        (
            "GLUDD_SELF_IMPROVE_EDITABLE_RANGES=[1,2]\nbounded task",
            "must contain integer pairs",
        ),
        (
            "GLUDD_SELF_IMPROVE_EDITABLE_RANGES=[[true,2]]\nbounded task",
            "must contain integer pairs",
        ),
    ],
)
def test_compact_v4_scope_marker_fail_closed_parser_paths(
    prompt: str,
    match: str,
) -> None:
    """Reject every ambiguous leading-marker shape without reading source labels."""
    with pytest.raises(ValueError, match=match):
        comparison_module._trusted_compact_editable_ranges(prompt)


def test_compact_v4_scope_enum_is_sorted_and_deduplicates_adjacent_boundaries() -> None:
    """Compile adjacent half-open sections to one ordered unique integer enum."""
    schema = comparison_module._compact_proposal_schema_for_ranges(
        ((8, 10), (10, 12), (15, 16))
    )
    root_properties = cast(dict[str, object], schema["properties"])
    edits = cast(dict[str, object], root_properties["e"])
    item = cast(dict[str, object], edits["items"])
    item_properties = cast(dict[str, object], item["properties"])

    assert item_properties["s"] == {
        "type": "integer",
        "enum": [8, 9, 10, 11, 12, 15, 16],
    }
    assert item_properties["n"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 2,
    }
    assert edits["maxItems"] == 4
    assert item_properties["z"] == {"type": "string", "maxLength": 768}


def test_compact_v4_schema_bounds_runaway_replacement_text() -> None:
    """Bound each generated replacement while retaining the parent byte ceiling."""
    schema = comparison_module._compact_proposal_schema_for_ranges(((3, 6),))
    root_properties = cast(dict[str, object], schema["properties"])
    edits = cast(dict[str, object], root_properties["e"])
    item = cast(dict[str, object], edits["items"])
    item_properties = cast(dict[str, object], item["properties"])

    assert edits["minItems"] == 1
    assert edits["maxItems"] == 4
    assert item_properties["z"] == {
        "type": "string",
        "maxLength": 768,
    }
    assert comparison_module._COMPACT_MAX_CONTENT_BYTES == 3072


def test_compact_v4_schema_preserves_multiple_edits_in_one_shown_section() -> None:
    """Do not trade the line-span protocol's same-section multi-edit support for closure."""
    schema = comparison_module._compact_proposal_schema_for_ranges(((3, 20),))
    root_properties = cast(dict[str, object], schema["properties"])
    edits = cast(dict[str, object], root_properties["e"])
    item = cast(dict[str, object], edits["items"])
    item_properties = cast(dict[str, object], item["properties"])

    assert edits["maxItems"] == 4
    assert item_properties["z"] == {"type": "string", "maxLength": 768}


def test_compact_v4_multisection_schema_conservatively_bounds_unicode_bytes() -> None:
    """Allocate the shared byte ceiling across sections at four bytes per codepoint."""
    schema = comparison_module._compact_proposal_schema_for_ranges(
        ((1, 3), (8, 10), (20, 21))
    )
    root_properties = cast(dict[str, object], schema["properties"])
    edits = cast(dict[str, object], root_properties["e"])
    item = cast(dict[str, object], edits["items"])
    item_properties = cast(dict[str, object], item["properties"])
    z_schema = cast(dict[str, object], item_properties["z"])

    assert edits["maxItems"] == 4
    assert z_schema["maxLength"] == 768
    assert 768 * len("😀".encode()) == 3072


def test_compact_v4_maximum_sections_have_a_finite_per_item_content_budget() -> None:
    """Keep every grammar dimension finite at the four-edit shard limit."""
    ranges = tuple((line, line + 1) for line in range(1, 48, 3))
    schema = comparison_module._compact_proposal_schema_for_ranges(ranges)
    root_properties = cast(dict[str, object], schema["properties"])
    edits = cast(dict[str, object], root_properties["e"])
    item = cast(dict[str, object], edits["items"])
    item_properties = cast(dict[str, object], item["properties"])
    start_schema = cast(dict[str, object], item_properties["s"])
    length_schema = cast(dict[str, object], item_properties["n"])
    content_schema = cast(dict[str, object], item_properties["z"])

    assert edits["maxItems"] == 4
    assert len(cast(list[int], start_schema["enum"])) == 32
    assert length_schema["maximum"] == 1
    assert content_schema["maxLength"] == 768
    assert (
        768 * comparison_module._COMPACT_MAX_UTF8_BYTES_PER_CODEPOINT
        == comparison_module._COMPACT_MAX_CONTENT_BYTES
    )
    assert comparison_module._COMPACT_SPAN_PROPOSAL_TOKENS == 4096
    assert comparison_module._STRUCTURED_OUTPUT_REQUIRE_STOP is True


def test_compact_v3_retains_its_historical_sixteen_edit_limit() -> None:
    """Keep stored v3 schema and decoder semantics unchanged by the v4 cap."""
    root = cast(
        dict[str, object],
        comparison_module._LEGACY_COMPACT_PROPOSAL_JSON_SCHEMA["properties"],
    )
    edits_schema = cast(dict[str, object], root["e"])
    raw = json.dumps(
        {
            "e": [
                {"a": f"old-{index}", "z": f"new-{index}"}
                for index in range(16)
            ]
        }
    )

    assert edits_schema["maxItems"] == 16
    manifest = comparison_module._decode_compact_proposal(
        raw,
        _contract(),
        focus_path="src/general_ludd/example.py",
    )
    assert len(manifest.edits) == 16


def test_compact_v4_parent_counts_decoded_utf8_not_json_escape_bytes() -> None:
    """Keep the parent byte cap authoritative after JSON escape decoding."""
    raw = json.dumps(
        {
            "e": [
                {"s": 1, "n": 1, "z": "😀" * 257},
                {"s": 8, "n": 1, "z": "😀" * 257},
                {"s": 20, "n": 1, "z": "😀" * 257},
            ]
        }
    )

    assert len(raw.encode("utf-8")) > 3072
    with pytest.raises(ValueError, match="new text exceeds 3072 bytes"):
        comparison_module._decode_compact_span_proposal(
            raw,
            focus_path="src/general_ludd/example.py",
        )


def test_compact_v4_empty_scope_schema_allows_only_create_coordinate() -> None:
    """Constrain an absent-file shard to the sole valid create coordinate."""
    schema = comparison_module._compact_proposal_schema_for_ranges(())
    root_properties = cast(dict[str, object], schema["properties"])
    edits = cast(dict[str, object], root_properties["e"])
    item = cast(dict[str, object], edits["items"])
    item_properties = cast(dict[str, object], item["properties"])

    assert edits["maxItems"] == 1
    assert item_properties["s"] == {"type": "integer", "enum": [1]}
    assert item_properties["n"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 0,
    }
    assert item_properties["z"] == {"type": "string", "maxLength": 768}


def test_locked_llama_grammar_compiles_multirange_integer_enum() -> None:
    """Exercise the locked 0.3.24 converter without fragile anyOf/oneOf."""
    if importlib.util.find_spec("llama_cpp") is None:
        pytest.skip("locked optional local-inference extra is not materialized")
    schema = comparison_module._compact_proposal_schema_for_ranges(
        ((3, 6), (10, 12))
    )
    encoded_schema = json.dumps(schema, ensure_ascii=True, separators=(",", ":"))
    script = (
        "from llama_cpp import LlamaGrammar, _utils\n"
        f"grammar = LlamaGrammar.from_json_schema({encoded_schema!r}, verbose=False)\n"
        "assert grammar is not None\n"
        "_utils.outnull_file.close()\n"
        "_utils.errnull_file.close()\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert "ResourceWarning" not in completed.stderr


def test_locked_llama_grammar_honors_small_string_and_array_bounds() -> None:
    """Prove locked 0.3.24 turns maxLength/maxItems into bounded GBNF."""
    if importlib.util.find_spec("llama_cpp") is None:
        pytest.skip("locked optional local-inference extra is not materialized")
    schema = comparison_module._compact_proposal_schema_for_ranges(((3, 6), (10, 12)))
    properties = cast(dict[str, object], schema["properties"])
    edits = cast(dict[str, object], properties["e"])
    item = cast(dict[str, object], edits["items"])
    item_properties = cast(dict[str, object], item["properties"])
    item_properties["z"] = {"type": "string", "maxLength": 8}
    encoded_schema = json.dumps(schema, ensure_ascii=True, separators=(",", ":"))
    script = (
        "from llama_cpp import LlamaGrammar, _utils\n"
        f"grammar = LlamaGrammar.from_json_schema({encoded_schema!r}, verbose=False)\n"
        "rendered = grammar._grammar\n"
        "array_rule = next(line for line in rendered.splitlines() if line.startswith('e ::='))\n"
        "string_rule = next(line for line in rendered.splitlines() if line.startswith('e-item-z ::='))\n"
        "assert array_rule.count('e-item') == 4, rendered\n"
        "assert string_rule.count('char') == 8, rendered\n"
        "assert '*' not in rendered and '+' not in rendered, rendered\n"
        "_utils.outnull_file.close()\n"
        "_utils.errnull_file.close()\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert "ResourceWarning" not in completed.stderr


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ({"e": []}, "1..4"),
        ({"e": [{"s": True, "n": 1, "z": "x"}]}, "integers, not booleans"),
        ({"e": [{"s": 1, "n": False, "z": "x"}]}, "integers, not booleans"),
        ({"e": [{"s": 0, "n": 1, "z": "x"}]}, "positive"),
        ({"e": [{"s": 1, "n": -1, "z": "x"}]}, "non-negative"),
        ({"e": [{"s": 1, "n": 0, "z": ""}]}, "must change content"),
        ({"e": [{"s": 1, "n": 1, "z": 7}]}, "new text must be a string"),
        ({"e": [{"s": 1, "n": 1, "z": "x", "a": "old"}]}, "exactly n, s, and z"),
        (
            {"e": [{"s": 3, "n": 2, "z": "x"}, {"s": 4, "n": 1, "z": "y"}]},
            "must not overlap",
        ),
        (
            {"e": [{"s": 2, "n": 0, "z": "x"}, {"s": 2, "n": 0, "z": "y"}]},
            "distinct start coordinates",
        ),
    ],
)
def test_compact_v4_decoder_rejects_ambiguous_spans(raw: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _span_proposal(raw)


def test_compact_v4_decoder_enforces_new_text_byte_budget() -> None:
    with pytest.raises(ValueError, match="exceeds 3072 bytes"):
        _span_proposal({"e": [{"s": 1, "n": 1, "z": "x" * 3073}]})


def test_compact_v4_decoder_canonicalizes_unordered_snapshot_spans() -> None:
    """Sort valid model spans by immutable baseline coordinate before expansion."""
    proposal = _span_proposal(
        {
            "e": [
                {"s": 4, "n": 1, "z": "delta = 2\n"},
                {"s": 1, "n": 1, "z": "alpha = 2\n"},
            ]
        }
    )

    assert tuple(edit.start_line for edit in proposal.edits) == (1, 4)


@pytest.mark.parametrize(
    ("edits", "match"),
    (
        (
            (
                {"s": 4, "n": 2, "z": "right\n"},
                {"s": 3, "n": 2, "z": "left\n"},
            ),
            "must not overlap",
        ),
        (
            (
                {"s": 4, "n": 0, "z": "first\n"},
                {"s": 4, "n": 0, "z": "second\n"},
            ),
            "distinct start coordinates",
        ),
    ),
)
def test_compact_v4_decoder_rejects_overlap_after_canonical_sort(
    edits: tuple[dict[str, object], ...],
    match: str,
) -> None:
    """Canonical sorting must not turn duplicate or overlapping spans into authority."""
    with pytest.raises(ValueError, match=match):
        _span_proposal({"e": list(edits)})


def test_compact_v4_decoder_checks_aggregate_budget_before_input_order() -> None:
    """Classify the Qwen overgeneration class before any harmless input ordering."""
    secret = "PRIVATE_SOURCE=" + ("😀" * 768)
    raw = {
        "e": [
            {"s": 7, "n": 1, "z": secret},
            {"s": 1, "n": 1, "z": "😀" * 768},
        ]
    }

    with pytest.raises(ValueError, match="new text exceeds 3072 bytes") as captured:
        _span_proposal(raw)

    detail = str(captured.value)
    assert "received_edits=2" in detail
    assert "received_content_bytes=>3072" in detail
    assert "PRIVATE_SOURCE" not in detail
    assert secret not in detail
    assert len(detail.encode("utf-8")) <= 192


def test_compact_v4_parent_rechecks_content_budget_across_shards() -> None:
    """Frozen and regenerated shards must share the original aggregate byte cap."""
    secret = "PRIVATE_SOURCE=" + ("x" * 1_590)
    proposals = (
        _span_proposal(
            {"e": [{"s": 1, "n": 1, "z": secret}]},
            path="src/general_ludd/one.py",
        ),
        _span_proposal(
            {"e": [{"s": 1, "n": 1, "z": "y" * 1_600}]},
            path="src/general_ludd/two.py",
        ),
    )

    with pytest.raises(ValueError, match="new text exceeds 3072 bytes") as captured:
        _expand_span_proposals(
            proposals,
            paths=("src/general_ludd/one.py", "src/general_ludd/two.py"),
            baselines={
                "src/general_ludd/one.py": "old\n",
                "src/general_ludd/two.py": "old\n",
            },
            editable_ranges=(((1, 2),), ((1, 2),)),
        )

    detail = str(captured.value)
    assert "received_shards=2" in detail
    assert "received_content_bytes=>3072" in detail
    assert "PRIVATE_SOURCE" not in detail
    assert secret not in detail
    assert len(detail.encode("utf-8")) <= 192


def test_compact_v4_decoder_rejects_fifth_edit_with_bounded_telemetry() -> None:
    """Keep one shard useful while preventing the observed sixteen-edit runaway."""
    edits = [
        {"s": index * 2 + 1, "n": 1, "z": f"value_{index} = True\n"}
        for index in range(5)
    ]

    with pytest.raises(ValueError, match=r"1\.\.4 entries") as captured:
        _span_proposal({"e": edits})

    detail = str(captured.value)
    assert "received_edits=>4 max_edits=4" in detail
    assert "value_" not in detail
    assert len(detail.encode("utf-8")) <= 192


def test_compact_v4_parent_derives_unique_preimage_for_duplicate_source_text() -> None:
    baseline = "header\nsame\nsame\ntail\n"
    proposal = _span_proposal({"e": [{"s": 3, "n": 1, "z": "changed\n"}]})

    manifest = _expand_span_proposals(
        (proposal,),
        baselines={"src/general_ludd/example.py": baseline},
        editable_ranges=(((1, 5),),),
    )

    edit = manifest.edits[0]
    assert edit.operation == "replace"
    assert baseline.count(edit.old_text) == 1
    assert baseline.replace(edit.old_text, edit.new_text, 1) == (
        "header\nsame\nchanged\ntail\n"
    )


@pytest.mark.parametrize(
    ("raw", "ranges", "match"),
    [
        ({"e": [{"s": 2, "n": 1, "z": "changed\n"}]}, ((1, 2),), "explicitly shown"),
        (
            {"e": [{"s": 4, "n": 0, "z": "inserted\n"}]},
            ((1, 3),),
            "first shown line through one past the last shown line",
        ),
        ({"e": [{"s": 6, "n": 1, "z": "changed\n"}]}, ((1, 5),), "outside trusted baseline"),
        ({"e": [{"s": 2, "n": 1, "z": "same\n"}]}, ((1, 5),), "must change content"),
    ],
)
def test_compact_v4_parent_rejects_hidden_out_of_range_and_noop_spans(
    raw: object,
    ranges: tuple[tuple[int, int], ...],
    match: str,
) -> None:
    proposal = _span_proposal(raw)

    with pytest.raises(ValueError, match=match):
        _expand_span_proposals((proposal,), editable_ranges=(ranges,))


def test_compact_v4_insertion_uses_closed_boundaries_of_each_shown_section() -> None:
    """Admit shard-edge coordinates while rejecting a boundary in a hidden gap."""
    path = "src/general_ludd/example.py"
    baseline = "hidden-a\nshown-b\nshown-c\nhidden-d\nhidden-e\nshown-f\nshown-g\n"
    ranges = (((2, 4), (6, 8)),)
    expected_by_start = {
        2: "hidden-a\ninserted\nshown-b\nshown-c\nhidden-d\nhidden-e\nshown-f\nshown-g\n",
        4: "hidden-a\nshown-b\nshown-c\ninserted\nhidden-d\nhidden-e\nshown-f\nshown-g\n",
    }

    for start_line, expected in expected_by_start.items():
        manifest = _expand_span_proposals(
            (_span_proposal({"e": [{"s": start_line, "n": 0, "z": "inserted\n"}]}),),
            baselines={path: baseline},
            editable_ranges=ranges,
        )
        edit = manifest.edits[0]
        assert baseline.replace(edit.old_text, edit.new_text, 1) == expected

    hidden_gap = _span_proposal(
        {"e": [{"s": 5, "n": 0, "z": "MODEL_SECRET=do-not-copy\n"}]}
    )
    with pytest.raises(
        ValueError,
        match="first shown line through one past the last shown line",
    ) as error:
        _expand_span_proposals(
            (hidden_gap,),
            baselines={path: baseline},
            editable_ranges=ranges,
        )
    assert "MODEL_SECRET" not in str(error.value)


def test_compact_v4_scope_error_exposes_only_typed_bounded_parent_telemetry() -> None:
    """Carry useful coordinates without model text, source, or the raw path."""
    path = "src/private/TOKEN=path-secret.py"
    baseline = "shown-a\nshown-b\nhidden-c\nhidden-d\nshown-e\nshown-f\n"
    ranges = (((1, 3), (5, 7)),)
    proposal = comparison_module._decode_compact_span_proposal(
        '{"e":[{"s":4,"n":0,"z":"PASSWORD=hunter2\\n"}]}',
        focus_path=path,
    )

    with pytest.raises(ValueError) as captured:
        _expand_span_proposals(
            (proposal,),
            paths=(path,),
            baselines={path: baseline},
            editable_ranges=ranges,
        )

    feedback = comparison_module._safe_compact_scope_telemetry(captured.value)
    assert feedback == (
        f"path_sha256={hashlib.sha256(path.encode()).hexdigest()} "
        "received_s=4 received_n=0 "
        "sections=[1,3),[5,7) boundaries=[1,3],[5,7]"
    )
    assert all(
        secret not in feedback
        for secret in (path, "TOKEN", "path-secret", "PASSWORD", "hunter2", "shown-a")
    )
    assert len(feedback.encode("utf-8")) <= 256


def test_compact_v4_scope_telemetry_bounds_many_sections() -> None:
    """Truncate only trusted ranges while keeping received coordinates actionable."""
    path = "src/private/TOKEN=path-secret.py"
    ranges = ((1, 2), (4, 5), (7, 8), (10, 11), (13, 14))
    baseline = "".join(f"line-{index}\n" for index in range(1, 14))
    proposal = comparison_module._decode_compact_span_proposal(
        '{"e":[{"s":3,"n":0,"z":"PASSWORD=hunter2\\n"}]}',
        focus_path=path,
    )

    with pytest.raises(ValueError) as captured:
        _expand_span_proposals(
            (proposal,),
            paths=(path,),
            baselines={path: baseline},
            editable_ranges=(ranges,),
        )

    feedback = comparison_module._safe_compact_scope_telemetry(captured.value)
    assert "sections=[1,2),[4,5),[7,8),[10,11),+1" in feedback
    assert "boundaries=[1,2],[4,5],[7,8],[10,11],+1" in feedback
    assert all(secret not in feedback for secret in (path, "TOKEN", "PASSWORD", "hunter2"))
    assert len(feedback.encode("utf-8")) <= 256


def test_compact_v4_strict_decoder_redacts_live_deepseek_framing_failure() -> None:
    """Reject the exact 2,308-byte live failure without echoing model output."""
    sensitive = (
        "Reasoning TOKEN=do-not-publish before object\n"
        '{"e":[{"s":1,"n":1,"z":"PASSWORD=hunter2"}]}\n'
    )
    raw = sensitive + ("x" * (2308 - len(sensitive.encode("utf-8"))))
    assert len(raw.encode("utf-8")) == 2308

    with pytest.raises(
        ValueError,
        match=r"compact-v4 proposal is not one complete JSON object; output_bytes=2308",
    ) as error:
        comparison_module._decode_compact_span_proposal(
            raw,
            focus_path="src/general_ludd/example.py",
        )

    diagnostic = str(error.value)
    assert all(secret not in diagnostic for secret in ("TOKEN", "PASSWORD", "hunter2"))
    assert len(diagnostic.encode("utf-8")) < 160


def test_compact_v4_parent_compiles_insert_partial_delete_and_whole_delete() -> None:
    path = "src/general_ludd/example.py"
    baseline = "first\nsecond\nthird\n"
    cases = (
        ({"e": [{"s": 2, "n": 0, "z": "inserted\n"}]}, "first\ninserted\nsecond\nthird\n", "replace"),
        ({"e": [{"s": 2, "n": 1, "z": ""}]}, "first\nthird\n", "replace"),
        ({"e": [{"s": 1, "n": 3, "z": ""}]}, "", "delete"),
    )

    for raw, expected, operation in cases:
        manifest = _expand_span_proposals(
            (_span_proposal(raw),),
            baselines={path: baseline},
            editable_ranges=(((1, 4),),),
        )
        edit = manifest.edits[0]
        assert edit.operation == operation
        actual = "" if operation == "delete" else baseline.replace(
            edit.old_text, edit.new_text, 1
        )
        assert actual == expected


@pytest.mark.parametrize(
    ("baseline", "start_line", "old_line_count", "new_text", "expected"),
    [
        ("first\nsecond\nthird\n", 2, 1, "changed", "first\nchanged\nthird\n"),
        ("first\r\nsecond\r\nthird\r\n", 2, 1, "changed", "first\r\nchanged\r\nthird\r\n"),
        ("first\nsecond\n", 2, 0, "inserted", "first\ninserted\nsecond\n"),
        ("first\r\nsecond\r\n", 2, 0, "inserted", "first\r\ninserted\r\nsecond\r\n"),
        ("first\nsecond\nthird\n", 2, 1, "", "first\nthird\n"),
        ("first\nsecond\n", 2, 1, "changed", "first\nchanged\n"),
        ("first\nsecond", 2, 1, "changed\n", "first\nchanged"),
        ("first\n", 2, 0, "last", "first\nlast\n"),
        ("first", 2, 0, "last\n", "first\nlast"),
    ],
)
def test_compact_v4_parent_owns_whole_line_boundaries(
    baseline: str,
    start_line: int,
    old_line_count: int,
    new_text: str,
    expected: str,
) -> None:
    """Materialize logical line spans without delegating separator bytes to a model."""
    path = "src/general_ludd/example.py"
    line_count = len(baseline.splitlines())

    manifest = _expand_span_proposals(
        (
            _span_proposal(
                {"e": [{"s": start_line, "n": old_line_count, "z": new_text}]}
            ),
        ),
        baselines={path: baseline},
        editable_ranges=(((1, line_count + 1),),),
    )

    assert manifest.schema_version == 2
    assert manifest.edits == (
        comparison_module.ProposalEdit(
            operation="replace",
            path=path,
            old_text=baseline,
            new_text=expected,
        ),
    )


def test_compact_v4_parent_accepts_only_canonical_absent_file_create() -> None:
    path = "src/general_ludd/example.py"
    manifest = _expand_span_proposals(
        (_span_proposal({"e": [{"s": 1, "n": 0, "z": "created = True\n"}]}),),
        baselines={path: None},
        editable_ranges=((),),
    )
    assert manifest.edits[0].operation == "create"

    for raw in (
        {"e": [{"s": 2, "n": 0, "z": "created = True\n"}]},
        {"e": [{"s": 1, "n": 1, "z": "created = True\n"}]},
    ):
        with pytest.raises(ValueError, match="absent file create"):
            _expand_span_proposals(
                (_span_proposal(raw),),
                baselines={path: None},
                editable_ranges=((),),
            )

    with pytest.raises(ValueError, match=r"absent file.*editable baseline ranges"):
        _expand_span_proposals(
            (_span_proposal({"e": [{"s": 1, "n": 0, "z": "created = True\n"}]}),),
            baselines={path: None},
            editable_ranges=(((1, 2),),),
        )


def test_compact_v4_parent_materializes_empty_existing_snapshot_without_anchor() -> None:
    path = "src/general_ludd/example.py"
    manifest = _expand_span_proposals(
        (_span_proposal({"e": [{"s": 1, "n": 0, "z": "created = True\n"}]}),),
        baselines={path: ""},
        editable_ranges=((),),
    )

    assert manifest.schema_version == 2
    assert manifest.edits == (
        comparison_module.ProposalEdit(
            operation="replace",
            path=path,
            old_text="",
            new_text="created = True\n",
        ),
    )


def test_compact_v4_parent_preserves_two_ordered_edits_in_one_file() -> None:
    path = "src/general_ludd/example.py"
    baseline = "alpha = 1\nbetween = 0\nomega = 1\n"
    proposal = _span_proposal(
        {
            "e": [
                {"s": 1, "n": 1, "z": "alpha = 2\n"},
                {"s": 3, "n": 1, "z": "omega = 2\n"},
            ]
        }
    )

    manifest = _expand_span_proposals(
        (proposal,),
        baselines={path: baseline},
        editable_ranges=(((1, 4),),),
    )

    current = baseline
    for edit in manifest.edits:
        assert current.count(edit.old_text) == 1
        current = current.replace(edit.old_text, edit.new_text, 1)
    assert current == "alpha = 2\nbetween = 0\nomega = 2\n"


def test_compact_v4_multi_edit_coordinates_stay_bound_to_immutable_snapshot() -> None:
    path = "src/general_ludd/example.py"
    baseline = "alpha = 1\nbetween = 0\nomega = 1\n"
    proposal = _span_proposal(
        {
            "e": [
                {"s": 1, "n": 1, "z": "alpha = 2\ninserted = True\n"},
                {"s": 3, "n": 1, "z": "omega = 2\n"},
            ]
        }
    )

    manifest = _expand_span_proposals(
        (proposal,),
        baselines={path: baseline},
        editable_ranges=(((1, 4),),),
    )

    current = baseline
    for edit in manifest.edits:
        assert current.count(edit.old_text) == 1
        current = current.replace(edit.old_text, edit.new_text, 1)
    assert current == "alpha = 2\ninserted = True\nbetween = 0\nomega = 2\n"


@pytest.mark.parametrize(
    ("baseline", "replacement", "expected"),
    [
        (
            "first\r\nsecond\r\nthird",
            "changed\r\n",
            "first\r\nchanged\r\nthird",
        ),
        (
            "first\r\nsecond\r\n",
            "changed\r\n",
            "first\r\nchanged\r\n",
        ),
    ],
)
def test_compact_v4_parent_preserves_crlf_and_final_newline_state(
    baseline: str,
    replacement: str,
    expected: str,
) -> None:
    path = "src/general_ludd/example.py"
    manifest = _expand_span_proposals(
        (_span_proposal({"e": [{"s": 2, "n": 1, "z": replacement}]}),),
        baselines={path: baseline},
        editable_ranges=(((1, len(baseline.splitlines()) + 1),),),
    )

    edit = manifest.edits[0]
    assert baseline.replace(edit.old_text, edit.new_text, 1) == expected


@pytest.mark.parametrize(
    ("start_line", "new_text", "expected"),
    [
        (1, "before\n", "before\nfirst\nsecond\nthird\n"),
        (4, "after\n", "first\nsecond\nthird\nafter\n"),
    ],
)
def test_compact_v4_parent_accepts_shown_zero_width_edge_boundaries(
    start_line: int,
    new_text: str,
    expected: str,
) -> None:
    path = "src/general_ludd/example.py"
    baseline = "first\nsecond\nthird\n"
    manifest = _expand_span_proposals(
        (_span_proposal({"e": [{"s": start_line, "n": 0, "z": new_text}]}),),
        baselines={path: baseline},
        editable_ranges=(((1, 4),),),
    )

    edit = manifest.edits[0]
    assert baseline.replace(edit.old_text, edit.new_text, 1) == expected


def test_compact_v4_parent_materializes_repeated_snapshot_without_unique_anchor() -> None:
    path = "src/general_ludd/example.py"
    baseline = "same\n" * 30_000
    proposal = _span_proposal(
        {"e": [{"s": 15_000, "n": 1, "z": "changed\n"}]}
    )

    manifest = _expand_span_proposals(
        (proposal,),
        baselines={path: baseline},
        editable_ranges=(((1, 30_001),),),
    )

    assert manifest.schema_version == 2
    assert manifest.edits[0].old_text == baseline
    expected_lines = baseline.splitlines(keepends=True)
    expected_lines[14_999] = "changed\n"
    assert manifest.edits[0].new_text == "".join(expected_lines)


def test_compact_v4_parent_materializes_repeated_closers_and_blank_line_insertion() -> None:
    """Immutable coordinates disambiguate content that has no unique local anchor."""
    path = "src/general_ludd/example.py"
    baseline = "def first():\n    pass\n\n}\n\n}\n"
    proposal = _span_proposal(
        {
            "e": [
                {"s": 3, "n": 0, "z": "    return 1"},
                {"s": 6, "n": 1, "z": "# final closer"},
            ]
        }
    )

    manifest = _expand_span_proposals(
        (proposal,),
        baselines={path: baseline},
        editable_ranges=(((1, 7),),),
    )

    assert manifest.edits[0].old_text == baseline
    assert manifest.edits[0].new_text == (
        "def first():\n    pass\n    return 1\n\n}\n\n# final closer\n"
    )


def test_compact_gateway_rejects_failed_canary_without_task_decode_or_secret_leak(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    calls = 0

    class FailedCanaryModel:
        def create_chat_completion(self, **_kwargs: object) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": '{"token=do-not-log":"secret"'},
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 32,
                    "total_tokens": 52,
                },
            }

        def __call__(self, prompt: str, **kwargs: object) -> object:
            raise AssertionError("raw completion must not be used")

    gateway = LocalProposalGateway(
        model_path,
        model_factory=lambda **_kwargs: FailedCanaryModel(),
    )
    contract = ProposalContract(
        baseline_sha="a" * 40,
        task_id="S83.133",
        tests=("tests/unit/test_example.py",),
        make_commands=("make test-files TESTFILES=tests/unit/test_example.py",),
    )

    with pytest.raises(ValueError, match="structured-output canary") as error:
        gateway.propose("Repair the example.", contract=contract)

    assert calls == 1
    assert "finish=length" in str(error.value)
    assert "completion_tokens=32" in str(error.value)
    assert "do-not-log" not in str(error.value)


def test_compact_gateway_rejects_non_stop_even_when_json_looks_complete(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    outputs: list[dict[str, object]] = [
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"ok":true}'},
                }
            ]
        },
        {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {
                        "content": json.dumps(
                            {
                                "e": [
                                    {
                                        "a": "x = 0",
                                        "z": "x = 1",
                                    }
                                ]
                            }
                        )
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 500,
                "completion_tokens": 1024,
                "total_tokens": 1524,
            },
        },
    ]

    class LengthModel:
        def create_chat_completion(self, **_kwargs: object) -> dict[str, object]:
            return outputs.pop(0)

        def __call__(self, prompt: str, **kwargs: object) -> object:
            raise AssertionError("raw completion must not be used")

    gateway = LocalProposalGateway(
        model_path,
        model_factory=lambda **_kwargs: LengthModel(),
    )
    contract = ProposalContract(
        baseline_sha="a" * 40,
        task_id="S83.133",
        tests=("tests/unit/test_example.py",),
        make_commands=("make test-files TESTFILES=tests/unit/test_example.py",),
    )

    with pytest.raises(ValueError, match="token budget") as error:
        gateway.propose(
            comparison_module.bind_compact_focus_path(
                "Repair the example.",
                "src/general_ludd/example.py",
            ),
            contract=contract,
        )

    assert "budget=1024" in str(error.value)
    assert "completion_tokens=1024" in str(error.value)


def test_local_gateway_rejects_token_budget_truncation_even_with_valid_json(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")

    class TruncatedChatModel:
        def create_chat_completion(self, **_kwargs: object) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": json.dumps({
                            "schema_version": 1,
                            "baseline_sha": "a" * 40,
                            "task_id": "S83.133",
                            "edits": [{
                                "operation": "replace",
                                "path": "src/general_ludd/example.py",
                                "old_text": "x = 0",
                                "new_text": "x = 1",
                            }],
                            "tests": ["tests/unit/test_example.py"],
                            "make_commands": [
                                "make test-files TESTFILES=tests/unit/test_example.py"
                            ],
                            "commit_message": "fix: apparently complete",
                        })},
                    }
                ]
            }

        def __call__(self, prompt: str, **kwargs: object) -> object:
            raise AssertionError("raw completion must not be used")

    gateway = LocalProposalGateway(
        model_path,
        model_factory=lambda **_kwargs: TruncatedChatModel(),
    )

    with pytest.raises(ValueError, match="token budget"):
        gateway.propose("Repair the example.")


def test_local_gateway_length_stop_reports_secret_safe_finish_and_usage(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")

    class ExhaustedChatModel:
        def create_chat_completion(self, **_kwargs: object) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "content": '{"token=do-not-log-this":"still truncated"'
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 640,
                    "completion_tokens": 4096,
                    "total_tokens": 4736,
                },
            }

        def __call__(self, prompt: str, **kwargs: object) -> object:
            raise AssertionError("raw completion must not be used")

    gateway = LocalProposalGateway(
        model_path,
        model_factory=lambda **_kwargs: ExhaustedChatModel(),
    )

    with pytest.raises(ValueError, match="token budget") as error:
        gateway.propose("Repair the example.")

    diagnostic = str(error.value)
    assert "finish=length" in diagnostic
    assert "prompt_tokens=640" in diagnostic
    assert "completion_tokens=4096" in diagnostic
    assert "total_tokens=4736" in diagnostic
    assert "do-not-log-this" not in diagnostic
    assert len(diagnostic.encode("utf-8")) <= 300


def test_completion_telemetry_hashes_output_before_strict_decode(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Make invalid-but-complete model output auditable without revealing content."""
    secret_output = '{"e":[{"s":1,"n":0,"z":"SECRET_TOKEN"}]}'
    expected_sha256 = hashlib.sha256(secret_output.encode("utf-8")).hexdigest()

    decoded = comparison_module._completion_text(
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": secret_output},
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 12,
                "total_tokens": 22,
            },
        },
        phase="proposal",
        budget=4096,
        require_stop=True,
    )

    telemetry = capsys.readouterr().out
    assert decoded == secret_output
    assert f"output_bytes={len(secret_output.encode('utf-8'))}" in telemetry
    assert f"output_sha256={expected_sha256}" in telemetry
    assert "SECRET_TOKEN" not in telemetry


def test_local_gateway_reports_bounded_incomplete_json_output(tmp_path: Path) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    incomplete = '{"schema_version":1,"edits":[' + ("x" * 5000)

    class FakeChatModel:
        def create_chat_completion(self, **_kwargs: object) -> dict[str, object]:
            return {"choices": [{"message": {"content": incomplete}}]}

        def __call__(self, prompt: str, **kwargs: object) -> object:
            raise AssertionError("raw completion must not be used")

    gateway = LocalProposalGateway(
        model_path,
        model_factory=lambda **_kwargs: FakeChatModel(),
    )

    with pytest.raises(ValueError, match="incomplete JSON") as error:
        gateway.propose("Repair exactly.")

    assert "output_bytes=" in str(error.value)
    assert "schema_version" not in str(error.value)
    assert len(str(error.value).encode("utf-8")) <= 300


def test_self_improve_runner_uses_local_model_and_make_only_git_workflow() -> None:
    source = Path("scripts/run_self_improve_e2e.py").read_text(encoding="utf-8")
    assert "--local-model-path" in source
    assert "--baseline-ref" in source
    assert "--reference-ref" in source
    assert '["git"' not in source
    assert "agent-worktree-base" in source
    assert "patch-equivalence" in source


def test_make_contract_forwards_local_comparison_inputs() -> None:
    makefile = compose_makefile(Path("Makefile"))
    contract = Path("config/make_target_contract.json").read_text(encoding="utf-8")
    for token in (
        "SELF_IMPROVE_CONTRACT_FILE",
        "SELF_IMPROVE_ENVELOPE_FILE",
        "SELF_IMPROVE_MODEL_PATH",
        "SELF_IMPROVE_BASELINE_REF",
        "SELF_IMPROVE_REFERENCE_REF",
        "SELF_IMPROVE_TASK_FILE",
        "SELF_IMPROVE_CONFIG_FILE",
    ):
        assert token in makefile
        assert token in contract
    assert '--contract-file "$(SELF_IMPROVE_CONTRACT_FILE)"' in makefile
    assert '--envelope-file "$(SELF_IMPROVE_ENVELOPE_FILE)"' in makefile
    assert '--self-improve-config-file "$(SELF_IMPROVE_CONFIG_FILE)"' in makefile


def test_gateway_fails_closed_for_each_malformed_model_response(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")

    class FakeModel:
        def __init__(self, output: object) -> None:
            self.output = output

        def __call__(
            self,
            prompt: str,
            *,
            max_tokens: int,
            temperature: float,
            echo: bool,
        ) -> object:
            del prompt, max_tokens, temperature, echo
            return self.output

    class FakeFactory:
        def __init__(self, output: object) -> None:
            self.output = output

        def __call__(
            self,
            *,
            model_path: str,
            n_ctx: int,
            verbose: bool,
            n_gpu_layers: int = 0,
        ) -> FakeModel:
            del model_path, n_ctx, verbose, n_gpu_layers
            return FakeModel(self.output)

    malformed = [
        [],
        {},
        {"choices": []},
        {"choices": ["not-a-mapping"]},
        {"choices": [{}]},
        {"choices": [{"message": {}}]},
    ]
    expected = [
        "non-object",
        "no choices",
        "no choices",
        "no choices",
        "no proposal text",
        "no proposal text",
    ]
    for output, match in zip(malformed, expected, strict=True):
        gateway = LocalProposalGateway(
            model,
            model_factory=FakeFactory(output),
        )
        with pytest.raises(ValueError, match=match):
            gateway.propose("repair")


def test_json_extractor_accepts_fenced_json_and_rejects_incomplete_tail() -> None:
    raw = comparison_module._extract_json_object('''```json\n{"ok":true}\n```''')
    assert raw == '{"ok":true}'
    with pytest.raises(ValueError, match="incomplete JSON"):
        comparison_module._extract_json_object('prefix {"ok": true')



@pytest.mark.parametrize(
    ("offload_probe_result", "expected_gpu_layers"),
    [(True, -1), (False, 0), (OSError("probe failed"), 0)],
)
def test_optional_llama_runtime_gates_offload_through_native_support_probe(
    monkeypatch: pytest.MonkeyPatch,
    offload_probe_result: bool | OSError,
    expected_gpu_layers: int,
) -> None:
    fake_module = ModuleType("llama_cpp")
    imports: list[str] = []

    class FakeModel:
        def __init__(
            self,
            *,
            model_path: str,
            n_ctx: int,
            verbose: bool,
            n_gpu_layers: int,
        ) -> None:
            self.settings = model_path, n_ctx, verbose, n_gpu_layers

    vars(fake_module)["Llama"] = FakeModel

    def probe_gpu_offload() -> bool:
        if isinstance(offload_probe_result, OSError):
            raise offload_probe_result
        return offload_probe_result

    vars(fake_module)["llama_supports_gpu_offload"] = probe_gpu_offload

    def import_runtime(name: str) -> ModuleType:
        imports.append(name)
        return fake_module

    monkeypatch.setattr(importlib, "import_module", import_runtime)
    model = comparison_module._default_model_factory(
        model_path="/tmp/gludd-model.gguf",
        n_ctx=0,
        verbose=False,
    )
    assert isinstance(model, FakeModel)
    assert model.settings == (
        "/tmp/gludd-model.gguf",
        0,
        False,
        expected_gpu_layers,
    )
    assert imports == ["llama_cpp"]


def test_locked_llama_runtime_compiles_canonical_schema_with_public_grammar_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Call the 0.3.24 public LlamaGrammar seam without a custom converter."""
    calls: list[tuple[str, bool]] = []
    grammar = object()
    runtime = cast(Any, ModuleType("llama_cpp"))

    class GrammarType:
        @staticmethod
        def from_json_schema(schema: str, *, verbose: bool = True) -> object:
            calls.append((schema, verbose))
            return grammar

    runtime.LlamaGrammar = GrammarType
    monkeypatch.setattr(comparison_module, "_load_llama_cpp_runtime", lambda: runtime)
    schema: dict[str, object] = {"required": ["e"], "type": "object"}

    assert comparison_module._default_json_schema_grammar(schema) is grammar
    assert calls == [('{"required":["e"],"type":"object"}', False)]


def test_locked_llama_grammar_construction_failure_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed without copying a native converter diagnostic."""
    runtime = cast(Any, ModuleType("llama_cpp"))

    class FailedGrammarType:
        @staticmethod
        def from_json_schema(_schema: str, *, verbose: bool = True) -> object:
            del verbose
            raise ValueError("TOKEN=do-not-publish native schema detail")

    runtime.LlamaGrammar = FailedGrammarType
    monkeypatch.setattr(comparison_module, "_load_llama_cpp_runtime", lambda: runtime)

    with pytest.raises(
        RuntimeError,
        match="JSON-schema grammar construction failed",
    ) as error:
        comparison_module._default_json_schema_grammar({"type": "object"})

    assert "TOKEN" not in str(error.value)


def test_gateway_rejects_missing_injected_grammar_before_decode(tmp_path: Path) -> None:
    """Treat an injected grammar factory returning no object as a hard failure."""
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    decode_calls = 0

    class Model:
        def __call__(self, prompt: str, **kwargs: object) -> object:
            del prompt, kwargs
            raise AssertionError("raw completion must not be used")

        def create_chat_completion(self, **_kwargs: object) -> dict[str, object]:
            nonlocal decode_calls
            decode_calls += 1
            return {"choices": []}

    def missing_grammar(_schema: dict[str, object]) -> object:
        return cast(object, None)

    gateway = LocalProposalGateway(
        model_path,
        model_factory=lambda **_kwargs: Model(),
        grammar_factory=missing_grammar,
    )
    contract = replace(
        _contract(),
        proposal_protocol=comparison_module.COMPACT_PROPOSAL_PROTOCOL_V4,
    )

    with pytest.raises(RuntimeError, match="returned no grammar"):
        gateway.propose(
            comparison_module.bind_compact_focus_path(
                "bounded",
                "src/general_ludd/example.py",
            ),
            contract=contract,
        )

    assert decode_calls == 0
