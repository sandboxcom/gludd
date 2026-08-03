"""DaemonChatRunner: interactive chat session connected to the gludd daemon.

Routes all model calls through ``POST /api/chat/completions`` on the daemon
so billing, rate-limiting, and security controls are enforced at the gateway.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from prompt_toolkit.key_binding.key_processor import KeyPressEvent

from general_ludd.chat.formatter import MessageFormatter, StreamingChatFormatter

DEFAULT_DAEMON_URL = "http://localhost:8000"
DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful AI assistant with access to ansible and terraform "
    "for advanced system administration and infrastructure tasks."
)
MAX_INPUT_LENGTH = 32_000


class DaemonChatRunner:
    """Interactive chat session that delegates model calls to the daemon."""

    def __init__(
        self,
        daemon_url: str = DEFAULT_DAEMON_URL,
        model_profile_id: str = "default",
        system_prompt: str | None = None,
        eval_mode: bool = False,
    ) -> None:
        self._daemon_url = daemon_url.rstrip("/")
        self._model_profile_id = model_profile_id
        self._formatter = MessageFormatter()
        self._eval_mode = eval_mode
        self.history: list[dict[str, str]] = [{"role": "system", "content": system_prompt or DEFAULT_SYSTEM_PROMPT}]

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    async def send_message(self, prompt: str) -> str:
        """Send a single user message, receive the full (non-streamed) response."""
        prompt = self._truncate_input(prompt)
        self.history.append({"role": "user", "content": prompt})

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
                resp = await client.post(
                    f"{self._daemon_url}/api/chat/completions/sync",
                    json={
                        "messages": self.history,
                        "model_profile_id": self._model_profile_id,
                        "stream": False,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError:
            self.history.pop()
            return "[Error: Could not connect to the daemon. Is gludd daemon running?]"
        except httpx.HTTPStatusError as exc:
            self.history.pop()
            return f"[Error: Daemon returned {exc.response.status_code}]"
        except Exception as exc:
            self.history.pop()
            return f"[Error: {exc}]"

        content = str(data.get("response", "") or "")
        if not content.strip():
            content = "[The model returned an empty response.]"
        self.history.append({"role": "assistant", "content": content})
        return self._formatter.highlight(content)

    async def stream_message(self, prompt: str) -> str:
        """Stream a user message through the daemon, writing tokens as they arrive."""
        prompt = self._truncate_input(prompt)
        self.history.append({"role": "user", "content": prompt})

        stream_fmt = StreamingChatFormatter()
        full_response = ""

        try:
            async with (
                httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client,
                client.stream(
                    "POST",
                    f"{self._daemon_url}/api/chat/completions",
                    json={
                        "messages": self.history,
                        "model_profile_id": self._model_profile_id,
                        "stream": True,
                    },
                ) as response,
            ):
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[len("data: ") :]
                    if data_str == "[DONE]":
                        break
                    try:
                        payload = json.loads(data_str)
                        if "error" in payload:
                            msg = f"\n[Daemon error: {payload['error']}]"
                            print(msg, file=sys.stderr)
                            self.history.pop()
                            return ""
                    except json.JSONDecodeError:
                        pass
                    chunk = data_str
                    if isinstance(chunk, str):
                        full_response += chunk
                        formatted = stream_fmt.feed(chunk)
                        if formatted:
                            sys.stdout.write(formatted)
                            sys.stdout.flush()
        except httpx.ConnectError:
            print("\n[Error: Could not connect to the daemon. Is gludd daemon running?]", file=sys.stderr)
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
        sys.stdout.write("\n")
        return full_response

    async def run_eval(self, prompt: str, stream: bool = False) -> str:
        """Non-interactive single-turn evaluation."""
        if stream:
            return await self.stream_message(prompt)
        return await self.send_message(prompt)

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

        print(f"Chat session connected to daemon at {self._daemon_url}")
        print(f"Model profile: {self._model_profile_id}")
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
                    break
                print("\n(Cancelled — Ctrl-D to exit, press Ctrl-C twice to quit)")
                continue
            except EOFError:
                print("\nGoodbye.")
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit", "/quit", "/exit"):
                print("Goodbye.")
                break

            if len(user_input) > MAX_INPUT_LENGTH:
                print(
                    f"Input truncated from {len(user_input)} to {MAX_INPUT_LENGTH} characters.",
                    file=sys.stderr,
                )
                user_input = user_input[:MAX_INPUT_LENGTH]

            try:
                await self.stream_message(user_input)
            except Exception as exc:
                print(f"\nError: {exc}", file=sys.stderr)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def clear_history(self) -> None:
        self.history = [
            {"role": "system", "content": self.history[0]["content"]}
            if self.history and self.history[0].get("role") == "system"
            else {"role": "system", "content": DEFAULT_SYSTEM_PROMPT}
        ]

    def get_messages(self) -> list[dict[str, str]]:
        return list(self.history)

    @staticmethod
    def _truncate_input(text: str) -> str:
        if len(text) > MAX_INPUT_LENGTH:
            return text[:MAX_INPUT_LENGTH]
        return text
