"""Pure chat-session export helpers shipped inside the chat collection."""

from __future__ import annotations

import html
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Literal

ExportFormat = Literal["md", "json", "html"]
_CODE_FENCE_RE = re.compile(r"```(\S*)\n(.*?)```", re.DOTALL)


def load_messages(session_file: Path) -> list[dict[str, Any]]:
    """Load newline-delimited chat records and reject malformed entries."""
    if not session_file.is_file():
        raise FileNotFoundError(f"Session file not found: {session_file}")
    messages: list[dict[str, Any]] = []
    try:
        for line in session_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError("session entries must be JSON objects")
            messages.append(item)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Corrupt session file: {session_file}") from exc
    return messages


def _markdown(messages: list[dict[str, Any]]) -> str:
    lines = ["# Chat Session Export\n"]
    for message in messages:
        lines.append(f"## {str(message.get('role', 'unknown')).capitalize()}")
        timestamp = str(message.get("timestamp", ""))
        if timestamp:
            lines.append(f"*{timestamp}*\n")
        lines.extend((str(message.get("content", "")), ""))
    return "\n".join(lines)


def _render_html_content(content: str) -> str:
    rendered: list[str] = []
    cursor = 0
    for match in _CODE_FENCE_RE.finditer(content):
        rendered.append(html.escape(content[cursor : match.start()]))
        language = html.escape(match.group(1))
        code = html.escape(match.group(2).strip())
        language_class = f' class="language-{language}"' if language else ""
        rendered.append(f"<pre><code{language_class}>{code}</code></pre>")
        cursor = match.end()
    rendered.append(html.escape(content[cursor:]))
    return "".join(rendered)


def _html(messages: list[dict[str, Any]]) -> str:
    body: list[str] = []
    for message in messages:
        role = html.escape(str(message.get("role", "unknown")))
        timestamp = html.escape(str(message.get("timestamp", "")))
        timestamp_html = (
            f'<span class="timestamp">{timestamp}</span>' if timestamp else ""
        )
        body.append(
            f'<div class="message message-{role}">'
            f'<div class="role">{role.capitalize()}</div>'
            f"{timestamp_html}"
            f'<div class="content">'
            f'{_render_html_content(str(message.get("content", "")))}'
            f"</div></div>"
        )
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>Chat Session Export</title>\n"
        "<style>body{font-family:sans-serif;max-width:900px;margin:2rem auto;}"
        ".message{border:1px solid #ddd;border-radius:8px;padding:1rem;margin:1rem 0;}"
        ".role{font-weight:bold}.content{white-space:pre-wrap}"
        "pre{background:#272822;color:#f8f8f2;padding:.75rem;overflow:auto}</style>\n"
        "</head>\n<body>\n<h1>Chat Session Export</h1>\n"
        + "\n".join(body)
        + "\n</body>\n</html>\n"
    )


def render_session(session_file: Path, export_format: ExportFormat = "md") -> str:
    """Render one session to a deterministic text representation."""
    messages = load_messages(session_file)
    if export_format == "md":
        return _markdown(messages)
    if export_format == "json":
        return json.dumps({"messages": messages}, indent=2)
    if export_format == "html":
        return _html(messages)
    raise ValueError(f"Unsupported export format: {export_format!r}")


def publish_export(output_file: Path, rendered: str) -> Path:
    """Atomically publish rendered chat data and return its destination."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=output_file.parent,
            prefix=f".{output_file.name}.",
            delete=False,
        ) as handle:
            temporary = handle.name
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output_file)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return output_file


def export_session(
    session_file: Path,
    export_format: ExportFormat = "md",
    output_file: Path | None = None,
) -> str | Path:
    """Render a session and optionally write it to ``output_file``."""
    rendered = render_session(session_file, export_format)
    if output_file is None:
        return rendered
    return publish_export(output_file, rendered)


__all__ = [
    "ExportFormat",
    "export_session",
    "load_messages",
    "publish_export",
    "render_session",
]
