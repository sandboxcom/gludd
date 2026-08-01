"""Generated-code response normalization shared by both game pipelines."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from general_ludd.cloud.game_generation import normalize_generated_python

PYGAME_PROGRAM = """import pygame
pygame.init()
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
pygame.quit()
"""


def test_normalizes_langchain_structured_text_blocks_from_raw_response() -> None:
    """Do not parse the lossy string representation stored by ModelResponse."""
    blocks = [
        {"type": "reasoning", "reasoning": "I should return one Python file."},
        {
            "type": "text",
            "text": f"Here is the game.\n```Python\n{PYGAME_PROGRAM}```\n",
        },
    ]
    response = SimpleNamespace(
        content=str(blocks),
        raw_response=AIMessage(content=blocks),
    )

    assert normalize_generated_python(response) == PYGAME_PROGRAM.strip()


@pytest.mark.parametrize("language", ["python", "Python", "py", ""])
def test_normalizes_supported_markdown_fence_variants(language: str) -> None:
    response = SimpleNamespace(content=f"answer:\n```{language}\n{PYGAME_PROGRAM}```")

    assert normalize_generated_python(response) == PYGAME_PROGRAM.strip()


def test_normalizes_unclosed_python_fence_from_length_stopped_completion() -> None:
    response = SimpleNamespace(content=f"Here is the game:\n```python\n{PYGAME_PROGRAM}")

    assert normalize_generated_python(response) == PYGAME_PROGRAM.strip()


def test_rejects_response_without_text_instead_of_stringifying_objects() -> None:
    response = SimpleNamespace(
        content=[],
        raw_response=SimpleNamespace(content=[{"type": "image", "image_url": "redacted"}]),
    )

    with pytest.raises(RuntimeError, match="text content"):
        normalize_generated_python(response)
