"""MessageFormatter: syntax-highlights code blocks in AI responses.

P2: pygments auto-detection + rich Syntax rendering with edge-case handling.
"""

from __future__ import annotations

import re
from typing import cast

_FENCE_RE = re.compile(r"```(\S*)\n(.*?)```", re.DOTALL)

MAX_CODE_BLOCK_LENGTH = 32_000


class MessageFormatter:
    """Detects and syntax-highlights fenced code blocks in text."""

    @staticmethod
    def detect_code_blocks(text: str) -> list[tuple[str, str]]:
        """Return ``(language, code)`` tuples for every fenced code block.

        Blocks without a language tag default to ``""`` (caller should
        auto-detect).  Leading and trailing whitespace is stripped from
        code bodies.
        """
        blocks: list[tuple[str, str]] = []
        for match in _FENCE_RE.finditer(text):
            lang = match.group(1) or ""
            code = match.group(2).strip()
            blocks.append((lang, code))
        return blocks

    @staticmethod
    def _resolve_lexer_name(lang: str, code: str) -> str:
        if not code.strip():
            return "text"

        if lang:
            try:
                from pygments.lexers import get_lexer_by_name

                lexer = get_lexer_by_name(lang)
                aliases = cast(list[str], lexer.aliases)
                if aliases:
                    return aliases[0]
            except Exception:
                pass

        try:
            from pygments.lexers import guess_lexer

            lexer = guess_lexer(code)
            aliases = cast(list[str], lexer.aliases)
            if aliases:
                return aliases[0]
        except Exception:
            pass

        return "text"

    @staticmethod
    def highlight(text: str) -> str:
        """Return *text* with every fenced code block replaced by its
        ``rich.Syntax``-highlighted rendering using pygments auto-detection.

        Non-code regions pass through unchanged.
        """
        if "```" not in text:
            return text

        try:
            from rich.console import Console
            from rich.syntax import Syntax
        except ImportError:
            return text

        console = Console(force_terminal=True)

        def _replace(match: re.Match[str]) -> str:
            lang_tag = match.group(1) or ""
            code = match.group(2)
            if not code.strip():
                return match.group(0)

            truncated = False
            if len(code) > MAX_CODE_BLOCK_LENGTH:
                code = code[:MAX_CODE_BLOCK_LENGTH] + "\n... [truncated]"
                truncated = True

            lexer_name = MessageFormatter._resolve_lexer_name(lang_tag, code)
            syntax = Syntax(
                code,
                lexer_name,
                theme="monokai",
                line_numbers=False,
            )
            with console.capture() as capture:
                console.print(syntax)
            result = capture.get()
            if truncated:
                result += "\n... [code block truncated]"
            return result

        return _FENCE_RE.sub(_replace, text)
