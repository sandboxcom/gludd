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


class StreamingChatFormatter:
    """Incrementally formats streamed tokens with code-block buffering.

    Plain text passes through immediately for real-time display. Fenced
    code blocks are buffered until the closing fence arrives, then rendered
    with syntax highlighting via :class:`MessageFormatter`.
    """

    _FENCE = "```"

    def __init__(self) -> None:
        self._plain_buffer: str = ""
        self._in_code: bool = False
        self._code_buffer: str = ""
        self._formatter = MessageFormatter()

    def feed(self, token: str) -> str:
        """Process a token and return display-ready text.

        Returns ``""`` while buffering inside an unclosed code block.
        """
        if self._in_code:
            return self._feed_code(token)
        return self._feed_plain(token)

    def flush(self) -> str:
        """Emit any remaining buffered content when the stream ends."""
        if self._in_code:
            result = f"{self._FENCE}{self._code_buffer}"
            self._in_code = False
            self._code_buffer = ""
            return result
        result = self._plain_buffer
        self._plain_buffer = ""
        return result

    def _feed_plain(self, token: str) -> str:
        self._plain_buffer += token

        idx = self._plain_buffer.find(self._FENCE)
        if idx != -1:
            before = self._plain_buffer[:idx]
            after = self._plain_buffer[idx + len(self._FENCE):]
            self._plain_buffer = ""
            self._in_code = True
            self._code_buffer = after
            if self._FENCE in self._code_buffer:
                return before + self._feed_code("")
            return before

        trailing = len(self._plain_buffer) - len(self._plain_buffer.rstrip("`"))
        if 0 < trailing < len(self._FENCE):
            emit = self._plain_buffer[:-trailing]
            self._plain_buffer = self._plain_buffer[-trailing:]
            return emit

        emit = self._plain_buffer
        self._plain_buffer = ""
        return emit

    def _feed_code(self, token: str) -> str:
        self._code_buffer += token

        idx = self._code_buffer.find(self._FENCE)
        if idx == -1:
            return ""

        code_body = self._code_buffer[:idx]
        after = self._code_buffer[idx + len(self._FENCE):]
        self._in_code = False
        self._code_buffer = ""

        highlighted = self._formatter.highlight(
            f"{self._FENCE}{code_body}{self._FENCE}"
        )
        if after:
            return highlighted + self._feed_plain(after)
        return highlighted
