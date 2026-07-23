"""ChatSession: local AI chat session with REPL and --eval modes.

P1: single-turn --eval mode via OpenAI-compatible HTTP API.
P2: MessageFormatter integration for syntax-highlighted output.
P3: Interactive REPL via prompt_toolkit with streaming output.
P4: ansible/terraform context injection from --project-dir.
"""

from __future__ import annotations

import asyncio
import datetime
import html
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import httpx

if TYPE_CHECKING:
    from prompt_toolkit.key_binding.key_processor import KeyPressEvent

from general_ludd.chat.context_window import DEFAULT_MAX_TOKENS, ContextWindow
from general_ludd.chat.formatter import MessageFormatter, StreamingChatFormatter
from general_ludd.models.provider_presets import (
    PROVIDER_FLAGSHIP_MODELS,
    PROVIDER_PRESETS,
)

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful AI assistant with access to ansible and terraform "
    "for advanced system administration and infrastructure tasks."
)

DEFAULT_PROVIDER = "openai"
DEFAULT_MODEL = "gpt-4o"

MAX_INPUT_LENGTH = 32_000
MAX_CODE_BLOCK_LENGTH = 32_000

DEFAULT_SAVE_INTERVAL = 5
DEFAULT_HISTORY_DIR = Path.home() / ".gludd" / "chat_history"
SESSION_INDEX_FILE = "index.json"


def _read_file_safe(path: Path, max_bytes: int = 8192) -> str | None:
    try:
        content = path.read_text(encoding="utf-8")
        if len(content) > max_bytes:
            return content[:max_bytes] + "\n... [truncated]"
        return content
    except Exception:
        return None


def _collect_ansible_context(project_dir: Path) -> str | None:
    inventory_paths = [
        project_dir / "inventory",
        project_dir / "inventory.yml",
        project_dir / "inventory.yaml",
        project_dir / "ansible" / "inventory",
        project_dir / "ansible" / "inventory.yml",
        project_dir / "ansible" / "hosts",
    ]
    for inv_path in inventory_paths:
        content = _read_file_safe(inv_path)
        if content:
            return f"[Ansible Inventory ({inv_path.relative_to(project_dir)})]\n{content}"
    return None


def _collect_terraform_context(project_dir: Path) -> str | None:
    state_paths = [
        project_dir / "terraform.tfstate",
        project_dir / "terraform" / "terraform.tfstate",
    ]
    for tf_path in state_paths:
        content = _read_file_safe(tf_path)
        if content:
            return f"[Terraform State ({tf_path.relative_to(project_dir)})]\n{content}"
    return None


def _build_context_system_prompt(project_dir: str | None, base_prompt: str) -> str:
    if not project_dir:
        return base_prompt

    dir_path = Path(project_dir).expanduser().resolve()
    if not dir_path.is_dir():
        return base_prompt

    parts: list[str] = [base_prompt, "", f"Project directory: {dir_path}"]

    ansible_ctx = _collect_ansible_context(dir_path)
    if ansible_ctx:
        parts.append("")
        parts.append(ansible_ctx)

    tf_ctx = _collect_terraform_context(dir_path)
    if tf_ctx:
        parts.append("")
        parts.append(tf_ctx)

    return "\n".join(parts)


class ChatSession:
    """Manages an interactive or one-shot chat session with an AI model."""

    def __init__(
        self,
        model: str = "default",
        system_prompt: str | None = None,
        eval_mode: bool = False,
        api_base_url: str | None = None,
        api_key: str | None = None,
        project_dir: str | None = None,
        history_file: str | None = None,
        save_interval: int = DEFAULT_SAVE_INTERVAL,
        resume: bool = False,
        max_context: int | None = None,
    ) -> None:
        self._model_arg = model
        base_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self._system_prompt = _build_context_system_prompt(project_dir, base_prompt)
        self.eval_mode = eval_mode
        self._provider, self._model_id = self._resolve_model(model)
        self._formatter = MessageFormatter()
        self._api_base_override = api_base_url
        self._api_key_override = api_key
        self._project_dir = project_dir
        self._save_interval = save_interval
        self._turn_count = 0
        self._history_dir = DEFAULT_HISTORY_DIR
        self._history_file_path: Path | None = None
        self._context_window = ContextWindow(
            max_tokens=max_context if max_context and max_context > 0 else DEFAULT_MAX_TOKENS
        )

        if resume:
            self._history_file_path = self._find_latest_session()
            if self._history_file_path is not None:
                self.history = self._load_history(self._history_file_path)
                if not self.history or self.history[0].get("role") != "system":
                    self.history.insert(0, {
                        "role": "system",
                        "content": self._system_prompt,
                    })
            else:
                self.history = [
                    {"role": "system", "content": self._system_prompt}
                ]
        elif history_file is not None:
            self._history_file_path = Path(history_file).expanduser()
            self.history = self._load_history(self._history_file_path)
            if not self.history or self.history[0].get("role") != "system":
                self.history.insert(0, {
                    "role": "system",
                    "content": self._system_prompt,
                })
        else:
            self.history = [
                {"role": "system", "content": self._system_prompt}
            ]

    @property
    def history_path(self) -> Path:
        return self._history_file_path or DEFAULT_HISTORY_DIR

    def _resolve_api_config(self) -> tuple[str, str]:
        base_override = self._api_base_override
        key_override = self._api_key_override
        if base_override is not None and key_override is not None:
            return base_override, key_override

        preset = PROVIDER_PRESETS.get(self._provider)
        if preset is None:
            raise ValueError(f"Unknown provider: {self._provider!r}")

        base_url = base_override if base_override is not None else cast(str, preset["api_base_url"])
        credential_env_var = cast(str, preset["credential_env_var"])

        api_key = key_override if key_override is not None else os.environ.get(credential_env_var)
        if not api_key:
            raise RuntimeError(
                f"No API key found. Set the {credential_env_var} "
                f"environment variable for provider {self._provider!r}, "
                f"or pass --api-key."
            )
        return base_url, api_key

    async def _post_with_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
    ) -> httpx.Response:
        max_retries = 2
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError:
                raise
            except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
                last_exc = exc
                if attempt < max_retries:
                    delay = 2 ** attempt
                    logger.warning(
                        "Connection error on attempt %d/%d (retrying in %ds): %s",
                        attempt + 1, max_retries + 1, delay, exc,
                    )
                    await asyncio.sleep(delay)
        if last_exc is not None:
            raise last_exc
        raise httpx.TimeoutException("request failed without a captured exception")

    async def run_once(self, prompt: str) -> str:
        prompt = self._truncate_input(prompt)
        self.history.append({"role": "user", "content": prompt})

        base_url, api_key = self._resolve_api_config()
        url = self._build_endpoint(base_url)

        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            try:
                response = await self._post_with_retry(
                    client, url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    payload={
                        "model": self._model_id,
                        "messages": self._messages_for_api(),
                    },
                )
            except httpx.ConnectError:
                return "[Error: Could not connect to the API server. Check your network and API base URL.]"
            except httpx.TimeoutException:
                return "[Error: Request timed out. The API server may be overloaded. Try again.]"
            except Exception as exc:
                return f"[Error: {exc}]"

        data = response.json()
        choices = data.get("choices") or [{"message": {"content": ""}}]
        content = str(choices[0].get("message", {}).get("content", "") or "")

        if not content.strip():
            content = "[The model returned an empty response.]"

        self.history.append({"role": "assistant", "content": content})
        self._record_turn_tokens(prompt, content)
        self._maybe_auto_save()
        return self._formatter.highlight(content)

    async def stream_response(self, prompt: str) -> str:
        prompt = self._truncate_input(prompt)
        self.history.append({"role": "user", "content": prompt})

        base_url, api_key = self._resolve_api_config()
        url = self._build_endpoint(base_url)

        stream_fmt = StreamingChatFormatter()
        full_response = ""

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client, client.stream(
                "POST",
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model_id,
                    "messages": self._messages_for_api(),
                    "stream": True,
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[len("data: "):]
                    if data_str == "[DONE]":
                        break
                    try:
                        payload = json.loads(data_str)
                        delta = payload["choices"][0].get("delta", {})
                        chunk = delta.get("content", "")
                        if chunk:
                            full_response += chunk
                            formatted = stream_fmt.feed(chunk)
                            if formatted:
                                sys.stdout.write(formatted)
                                sys.stdout.flush()
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
        except httpx.ConnectError:
            msg = "[Error: Could not connect to the API server. "
            msg += "Check your network and API base URL.]"
            print(f"\n{msg}", file=sys.stderr)
            self.history.pop()
            return ""
        except httpx.TimeoutException:
            print("\n[Error: Request timed out. The API server may be overloaded. Try again.]", file=sys.stderr)
            self.history.pop()
            return ""
        except Exception as exc:
            print(f"\n[Error: {exc}]", file=sys.stderr)
            self.history.pop()
            return ""

        remaining = stream_fmt.flush()
        if remaining:
            sys.stdout.write(remaining)
            sys.stdout.flush()

        if not full_response.strip():
            full_response = "[The model returned an empty response.]"

        self.history.append({"role": "assistant", "content": full_response})
        self._record_turn_tokens(prompt, full_response)
        self._maybe_auto_save()
        sys.stdout.write("\n")
        return full_response

    async def start_repl(self) -> None:
        """Interactive REPL loop using prompt_toolkit."""
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.history import InMemoryHistory
            from prompt_toolkit.key_binding import KeyBindings
        except ImportError:
            print(
                "prompt_toolkit is not installed. Install it with: pip install prompt_toolkit>=3.0.0",
                file=sys.stderr,
            )
            sys.exit(1)

        bindings = KeyBindings()

        @bindings.add("c-d")
        def _(event: KeyPressEvent) -> None:
            event.app.exit()

        session_obj = PromptSession[str](
            history=InMemoryHistory(),
            key_bindings=bindings,
        )

        print(f"Chat session started. Model: {self._provider}/{self._model_id}")
        if self._project_dir:
            print(f"Project context: {self._project_dir}")
        print("Type your message and press Enter. Ctrl-D to exit, Ctrl-C to cancel input.\n")

        consecutive_ctrl_c = 0

        while True:
            try:
                user_input = await session_obj.prompt_async("> ")
                consecutive_ctrl_c = 0
            except KeyboardInterrupt:
                consecutive_ctrl_c += 1
                if consecutive_ctrl_c >= 2:
                    print("\nGoodbye.")
                    self.save_history()
                    break
                print("\n(Cancelled — Ctrl-D to exit, press Ctrl-C twice to quit)")
                continue
            except EOFError:
                print("\nGoodbye.")
                self.save_history()
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit", "/quit", "/exit"):
                print("Goodbye.")
                self.save_history()
                break

            if len(user_input) > MAX_INPUT_LENGTH:
                print(
                    f"Input truncated from {len(user_input)} to {MAX_INPUT_LENGTH} characters.",
                    file=sys.stderr,
                )
                user_input = user_input[:MAX_INPUT_LENGTH]

            try:
                await self.stream_response(user_input)
            except Exception as exc:
                print(f"\nError: {exc}", file=sys.stderr)

    @staticmethod
    def _truncate_input(text: str) -> str:
        if len(text) > MAX_INPUT_LENGTH:
            return text[:MAX_INPUT_LENGTH]
        return text

    def _messages_for_api(self) -> list[dict[str, str]]:
        """Return the message list to send to the API. If the context window
        signals that summarization is warranted, fold older turns into a
        summary placeholder; otherwise return history verbatim. The full
        history is preserved in ``self.history`` for saving/export."""
        if self._context_window.needs_summarization():
            compacted = self._context_window.summarize_if_needed(self.history)
            if compacted is not None:
                return compacted
        return self.history

    def _record_turn_tokens(self, user_text: str, assistant_text: str) -> None:
        """Estimate and record the token cost of a completed turn."""
        user_tokens = ContextWindow.estimate_tokens(user_text)
        assistant_tokens = ContextWindow.estimate_tokens(assistant_text)
        self._context_window.record_turn(user_tokens + assistant_tokens)

    @staticmethod
    def _resolve_model(model: str) -> tuple[str, str]:
        if model == "default":
            return DEFAULT_PROVIDER, DEFAULT_MODEL
        if "/" in model:
            provider, _, model_id = model.partition("/")
            if not model_id:
                model_id = PROVIDER_FLAGSHIP_MODELS.get(provider, DEFAULT_MODEL)
            return provider, model_id
        if model in PROVIDER_FLAGSHIP_MODELS:
            return model, PROVIDER_FLAGSHIP_MODELS[model]
        return DEFAULT_PROVIDER, model

    @staticmethod
    def _build_endpoint(base_url: str) -> str:
        if "/chat/completions" in base_url:
            return base_url
        return f"{base_url.rstrip('/')}/chat/completions"

    def _ensure_history_dir(self) -> None:
        self._history_dir.mkdir(parents=True, exist_ok=True)

    def _find_latest_session(self) -> Path | None:
        self._ensure_history_dir()
        index = self._read_session_index()
        entries: list[dict[str, Any]] = cast("list[dict[str, Any]]", index.get("sessions", []))
        if not entries:
            return None
        latest = entries[-1]
        return Path(cast(str, latest["file"]))

    def _read_session_index(self) -> dict[str, object]:
        index_path = self._history_dir / SESSION_INDEX_FILE
        if index_path.exists():
            try:
                return cast("dict[str, object]", json.loads(index_path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
        return {"sessions": []}

    def _write_session_index(self, index: dict[str, object]) -> None:
        self._ensure_history_dir()
        index_path = self._history_dir / SESSION_INDEX_FILE
        index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")

    def _make_session_filename(self) -> Path:
        self._ensure_history_dir()
        timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
        return self._history_dir / f"session_{timestamp}.jsonl"

    def _load_history(self, file_path: Path) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        try:
            with file_path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        messages.append({
                            "role": record["role"],
                            "content": record["content"],
                        })
                    except json.JSONDecodeError:
                        continue
        except OSError as exc:
            logger.warning("Failed to load history from %s: %s", file_path, exc)
            return [
                {"role": "system", "content": self._system_prompt}
            ]
        if not messages:
            return [
                {"role": "system", "content": self._system_prompt}
            ]
        return messages

    def load_history(self) -> None:
        if self._history_file_path is not None:
            self.history = self._load_history(self._history_file_path)
            if not self.history or self.history[0].get("role") != "system":
                self.history.insert(0, {
                    "role": "system",
                    "content": self._system_prompt,
                })
        else:
            self.history = [
                {"role": "system", "content": self._system_prompt}
            ]

    def clear_history(self) -> None:
        if self._history_file_path is not None:
            self._history_file_path.unlink(missing_ok=True)
        self.history = [
            {"role": "system", "content": self._system_prompt}
        ]

    @classmethod
    def resume(
        cls,
        *,
        history_file: str | None = None,
        system_prompt: str | None = None,
        resume: bool = False,
        **kwargs: Any,
    ) -> ChatSession | None:
        if resume:
            dummy = cls(**kwargs)
            latest = dummy._find_latest_session()
            if latest is None:
                return None
            history_file = str(latest)
        elif history_file:
            file_path = Path(history_file).expanduser()
            if not file_path.exists():
                return None
        session = cls(system_prompt=system_prompt, **kwargs)
        if history_file:
            session._history_file_path = Path(history_file).expanduser()
        session.load_history()
        if system_prompt and session.history:
            session.history[0]["content"] = system_prompt
        return session

    def save_history(self) -> None:
        if len(self.history) == 1 and self.history[0].get("role") == "system":
            return
        self._ensure_history_dir()
        if self._history_file_path is None:
            self._history_file_path = self._make_session_filename()
        self._history_file_path.parent.mkdir(parents=True, exist_ok=True)
        write_messages = self._messages_for_save()
        self._history_file_path.write_text(
            "\n".join(json.dumps(m) for m in write_messages) + "\n",
            encoding="utf-8",
        )
        self._update_index(self._history_file_path, write_messages)

    def _messages_for_save(self) -> list[dict[str, object]]:
        messages: list[dict[str, object]] = []
        for msg in self.history:
            messages.append({
                "role": msg["role"],
                "content": msg["content"],
            })
        base_ts = datetime.datetime.now(datetime.UTC)
        for i, msg_dict in enumerate(messages):
            msg_dict["timestamp"] = (base_ts - datetime.timedelta(seconds=len(messages) - i)).isoformat()
        return messages

    def _update_index(
        self,
        file_path: Path,
        messages: list[dict[str, object]],
    ) -> None:
        index = self._read_session_index()
        sessions = cast("list[dict[str, object]]", index.get("sessions", []))
        first_user = ""
        for msg in messages:
            if msg["role"] == "user":
                first_user = cast(str, msg["content"])[:80]
                break
        entry: dict[str, object] = {
            "file": str(file_path),
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "model": f"{self._provider}/{self._model_id}",
            "message_count": len(messages),
            "preview": first_user,
        }
        existing = [s for s in sessions if s.get("file") == str(file_path)]
        if existing:
            existing[0].update(entry)
        else:
            sessions.append(entry)
        index["sessions"] = sessions
        self._write_session_index(index)

    @staticmethod
    def list_sessions(history_dir: Path | None = None) -> list[dict[str, object]]:
        if history_dir is None:
            history_dir = DEFAULT_HISTORY_DIR
        index_path = history_dir / SESSION_INDEX_FILE
        if not index_path.exists():
            return []
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            return cast("list[dict[str, object]]", index.get("sessions", []))
        except (json.JSONDecodeError, OSError):
            return []

    def _maybe_auto_save(self) -> None:
        self._turn_count += 1
        if self._turn_count % self._save_interval == 0:
            self.save_history()

    def export_markdown(self, output_file: str | Path | None = None) -> str | Path:
        messages: list[dict[str, object]] = [
            {"role": m["role"], "content": m["content"]} for m in self.history
        ]
        result = _export_to_markdown(messages)
        if output_file is not None:
            out = Path(output_file) if isinstance(output_file, str) else output_file
            out.write_text(result, encoding="utf-8")
            return out
        return result

    def export_json(self, output_file: str | Path | None = None) -> str | Path:
        messages: list[dict[str, object]] = [
            {"role": m["role"], "content": m["content"]} for m in self.history
        ]
        result = _export_to_json(messages)
        if output_file is not None:
            out = Path(output_file) if isinstance(output_file, str) else output_file
            out.write_text(result, encoding="utf-8")
            return out
        return result

    def export_html(self, output_file: str | Path | None = None) -> str | Path:
        messages: list[dict[str, object]] = [
            {"role": m["role"], "content": m["content"]} for m in self.history
        ]
        result = _export_to_html(messages)
        if output_file is not None:
            out = Path(output_file) if isinstance(output_file, str) else output_file
            out.write_text(result, encoding="utf-8")
            return out
        return result


def export_session(
    session_file: Path,
    format: str | None = "md",
    output_file: Path | None = None,
) -> str | Path:
    if format is None or format == "":
        format = "md"
    if format not in ("md", "json", "html"):
        raise ValueError(f"Unsupported export format: {format!r}. Use 'md', 'json', or 'html'.")
    if not isinstance(session_file, Path):
        session_file = Path(session_file)
    if not session_file.exists():
        raise FileNotFoundError(f"Session file not found: {session_file}")
    try:
        messages: list[dict[str, object]] = []
        with session_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    raise
    except json.JSONDecodeError as exc:
        raise ValueError(f"Corrupt session file: {session_file}") from exc

    if format == "md":
        result = _export_to_markdown(messages)
    elif format == "html":
        result = _export_to_html(messages)
    else:
        result = _export_to_json(messages)

    if output_file is not None:
        if isinstance(output_file, str):
            output_file = Path(output_file)
        output_file.write_text(result, encoding="utf-8")
        return output_file
    return result


def _export_to_markdown(messages: list[dict[str, object]]) -> str:
    lines: list[str] = []
    lines.append("# Chat Session Export\n")
    for msg in messages:
        role = str(msg.get("role", "unknown")).capitalize()
        content = str(msg.get("content", ""))
        ts = str(msg.get("timestamp", ""))
        lines.append(f"## {role}")
        if ts:
            lines.append(f"*{ts}*\n")
        lines.append(content)
        lines.append("")
    return "\n".join(lines)


def _export_to_json(messages: list[dict[str, object]]) -> str:
    return json.dumps({"messages": messages}, indent=2)


_CODE_FENCE_RE = re.compile(r"```(\S*)\n(.*?)```", re.DOTALL)


def _export_to_html(messages: list[dict[str, object]]) -> str:
    body_parts: list[str] = []
    for msg in messages:
        role = html.escape(str(msg.get("role", "unknown")))
        role_label = role.capitalize()
        content = str(msg.get("content", ""))
        ts = html.escape(str(msg.get("timestamp", "")))
        rendered = _render_html_content(content)
        ts_html = f'<span class="timestamp">{ts}</span>' if ts else ""
        body_parts.append(
            f'<div class="message message-{role}">'
            f'<div class="role">{role_label}</div>'
            f'{ts_html}'
            f'<div class="content">{rendered}</div>'
            f'</div>'
        )
    body = "\n".join(body_parts)
    return (
        "<!DOCTYPE html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>Chat Session Export</title>\n"
        "<style>\n"
        "body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; "
        "max-width: 900px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }\n"
        ".message { border: 1px solid #e0e0e0; border-radius: 8px; "
        "padding: 1rem; margin-bottom: 1rem; }\n"
        ".message-user { background: #f0f7ff; }\n"
        ".message-assistant { background: #f5f5f5; }\n"
        ".message-system { background: #fff8e1; font-size: 0.9em; }\n"
        ".role { font-weight: bold; margin-bottom: 0.5rem; text-transform: capitalize; }\n"
        ".timestamp { color: #888; font-size: 0.85em; margin-left: 0.5rem; }\n"
        ".content { white-space: pre-wrap; word-wrap: break-word; }\n"
        "pre { background: #272822; color: #f8f8f2; padding: 0.75rem; "
        "border-radius: 4px; overflow-x: auto; }\n"
        "code { font-family: 'SF Mono', Consolas, monospace; }\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        f"<h1>Chat Session Export</h1>\n"
        f"{body}\n"
        "</body>\n"
        "</html>\n"
    )


def _render_html_content(content: str) -> str:
    """Render message content as HTML: fenced code blocks become <pre><code>,
    other text is escaped and inline code preserved."""
    out: list[str] = []
    last_end = 0
    for match in _CODE_FENCE_RE.finditer(content):
        if match.start() > last_end:
            out.append(html.escape(content[last_end:match.start()]))
        lang = html.escape(match.group(1))
        code = html.escape(match.group(2).strip())
        lang_attr = f' class="language-{lang}"' if lang else ""
        out.append(f'<pre><code{lang_attr}>{code}</code></pre>')
        last_end = match.end()
    if last_end < len(content):
        out.append(html.escape(content[last_end:]))
    return "".join(out)
