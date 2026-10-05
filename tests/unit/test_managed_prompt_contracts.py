"""Regression tests for extracted managed prompt value objects."""

from __future__ import annotations

import pytest

from general_ludd.self_improve import managed_prompt_contracts
from general_ludd.self_improve.managed_runner import PromptShard


def test_managed_runner_reexports_the_canonical_prompt_shard() -> None:
    """Prompt shard identity remains stable for downstream callers."""
    assert PromptShard is managed_prompt_contracts.PromptShard


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"prompt": "x" * 16_385}, "exceeds"),
        ({"editable_ranges": []}, "immutable tuple"),
        ({"editable_ranges": ((1,),)}, "integer pairs"),
        ({"editable_ranges": ((0, 1),)}, "ordered half-open ranges"),
    ],
)
def test_prompt_shard_rejects_every_extracted_bounded_shape(
    kwargs: dict[str, object],
    message: str,
) -> None:
    """The extracted object retains every fail-closed validation branch."""
    values: dict[str, object] = {
        "focus_paths": ("src/example.py",),
        "prompt": "bounded",
        "editable_ranges": (),
        **kwargs,
    }
    with pytest.raises(ValueError, match=message):
        PromptShard(**values)
